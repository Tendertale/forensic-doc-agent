"""用户修正用例（R-7 / R-38～R-41 / R-47、N-10、INV-6 / INV-7 / INV-8）。

四类修正（识别纠正 / 术语纠正 / 格式纠正 / 工作顺序调整）+ 修正记录、复核失效、
适用范围、已审核版本保留与交付前置条件。
"""

from __future__ import annotations

from backend.app.database import InvalidTransition
from backend.tests.helpers import (
    case_version,
    create_case,
    create_document,
    get_case,
    pipeline_case,
    run_check,
    workflow_extract,
)


def _fields(client, case_id):
    return {row["field_key"]: row for row in
            client.get("/api/cases/{0}/fields".format(case_id)).json()}


def _correction(client, case_id, **payload):
    body = {
        "expected_version": case_version(client, case_id),
        "kind": "recognition",
        "target_type": "field",
        "target_id": 0,
        "before_text": "旧值",
        "after_text": "新值",
        "reason": "测试修正原因",
    }
    body.update(payload)
    return client.post("/api/cases/{0}/corrections".format(case_id), json=body)


# --------------------------------------------------------------------------
# R-38：修正记录
# --------------------------------------------------------------------------


def test_r38_correction_record_keeps_before_after_reason_confirmer_and_scope(client, db, storage):
    """R-38：每次修正保存修改前后内容、原因、确认人、适用范围与受影响文稿。"""

    case = pipeline_case(client, db, storage)
    case_id = int(case["id"])
    workflow_extract(db, storage, case_id)
    field = _fields(client, case_id)["body_length"]

    response = _correction(client, case_id, kind="recognition", target_type="field",
                           target_id=field["id"], before_text="尸长172cm",
                           after_text="尸长173cm", reason="识别纠正：原页为 173",
                           confirmed_by="复核人甲")
    assert response.status_code == 200, response.text
    record = response.json()
    assert record["before_text"] == "尸长172cm"
    assert record["after_text"] == "尸长173cm"
    assert record["reason"] == "识别纠正：原页为 173"
    assert record["confirmed_by"] == "复核人甲"
    assert record["scope"] == "case_only"
    assert record["scope_value"] == ""
    assert record["applied"] == 1
    assert isinstance(record["affected"], list)
    assert isinstance(record["invalidated"], list)

    listed = client.get("/api/cases/{0}/corrections".format(case_id)).json()
    assert len(listed) == 1
    assert listed[0]["id"] == record["id"]
    events = [item for item in get_case(client, case_id)["events"]
              if item["event_type"] == "correction_recorded"]
    assert events and "识别纠正" in events[0]["detail"]


def test_correction_requires_a_real_change_and_a_reason(client, db, storage):
    """R-38：修正前后内容不得相同，且必须给出原因（`CorrectionCreate` 冻结约束）。"""

    case = pipeline_case(client, db, storage)
    case_id = int(case["id"])
    workflow_extract(db, storage, case_id)
    field = _fields(client, case_id)["body_length"]

    same = _correction(client, case_id, target_id=field["id"], before_text="同",
                       after_text="同", reason="无变化")
    assert same.status_code == 422, same.text
    assert "不能相同" in same.json()["detail"]

    no_reason = client.post("/api/cases/{0}/corrections".format(case_id), json={
        "expected_version": case_version(client, case_id), "kind": "recognition",
        "target_type": "field", "target_id": field["id"], "before_text": "A",
        "after_text": "B",
    })
    assert no_reason.status_code == 422, no_reason.text


def test_r7_recognition_correction_updates_confirmed_value_and_keeps_raw_text(client, db, storage):
    """R-7 / R-38：识别纠正更新确认值，原识别原文保留（来源不被覆盖）。"""

    case = pipeline_case(client, db, storage)
    case_id = int(case["id"])
    workflow_extract(db, storage, case_id)
    before = _fields(client, case_id)["body_length"]

    response = _correction(client, case_id, kind="recognition", target_id=before["id"],
                           before_text=before["raw_text"], after_text="尸长173cm",
                           reason="字迹确认为 173")
    assert response.status_code == 200, response.text
    after = _fields(client, case_id)["body_length"]
    assert after["confirmed_value"] == "尸长173cm"
    assert after["raw_text"] == before["raw_text"]


def test_r41_format_correction_keeps_fact_numbers(client, db, storage):
    """R-40 / R-41：格式纠正只改表达，事实数值必须原样保留。"""

    case = pipeline_case(client, db, storage)
    case_id = int(case["id"])
    workflow_extract(db, storage, case_id)
    field = _fields(client, case_id)["body_length"]

    response = _correction(client, case_id, kind="format", target_id=field["id"],
                           before_text="尸长172 cm。", after_text="尸长 172 cm。",
                           reason="按样例调整空格")
    assert response.status_code == 200, response.text
    after = _fields(client, case_id)["body_length"]
    assert after["confirmed_value"] == "尸长 172 cm。"
    assert "173" not in after["confirmed_value"]


# --------------------------------------------------------------------------
# R-40 / INV-6：事实修正使旧复核状态失效
# --------------------------------------------------------------------------


def test_r40_fact_correction_supersedes_documents_and_reopens_field_review(client, db, storage):
    """R-40 / INV-6：事实修正后相关文稿 `superseded`，字段复核状态打回 `pending`。"""

    case = pipeline_case(client, db, storage)
    case_id = int(case["id"])
    workflow_extract(db, storage, case_id)
    field = _fields(client, case_id)["body_length"]

    confirmed = client.patch("/api/cases/{0}/fields/{1}".format(case_id, "body_length"), json={
        "expected_version": case_version(client, case_id),
        "review_state": "confirmed", "confirmed_value": "尸长172cm",
    })
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["review_state"] == "confirmed"

    created = create_document(client, case_id)
    document_id = created["payload"]["document_id"]
    approved = client.post("/api/documents/{0}/approve".format(document_id), json={
        "expected_version": case_version(client, case_id)})
    assert approved.status_code == 200, approved.text
    assert approved.json()["doc_status"] == "approved"

    response = _correction(client, case_id, kind="recognition", target_id=field["id"],
                           before_text="尸长172cm", after_text="尸长173cm",
                           reason="事实变化：重新核对原件")
    assert response.status_code == 200, response.text
    record = response.json()
    assert record["invalidated"], "修正记录必须列出被打回复核的字段"
    assert record["affected"], "修正记录必须列出受影响文稿"

    after = _fields(client, case_id)["body_length"]
    assert after["review_state"] == "pending"
    documents = client.get("/api/cases/{0}/documents".format(case_id)).json()
    assert [item["doc_status"] for item in documents] == ["superseded"]
    assert get_case(client, case_id)["doc_status"] == "superseded"


def test_r41_approved_version_is_kept_and_new_version_supersedes_it(client, db, storage):
    """R-41 / INV-6：已审核版本继续保留（含文件），新版本生成后旧版本标记为 `superseded`。"""

    case = pipeline_case(client, db, storage)
    case_id = int(case["id"])
    first = create_document(client, case_id)
    first_docx = first["payload"]["docx_absolute"]
    approved = client.post("/api/documents/{0}/approve".format(first["payload"]["document_id"]),
                           json={"expected_version": case_version(client, case_id)})
    assert approved.status_code == 200, approved.text

    second = create_document(client, case_id)
    assert second["payload"]["version_no"] == 2
    documents = client.get("/api/cases/{0}/documents".format(case_id)).json()
    statuses = {item["version_no"]: item["doc_status"] for item in documents}
    assert statuses == {1: "superseded", 2: "internal_draft"}
    from pathlib import Path

    assert Path(first_docx).is_file(), "已审核版本的产物文件必须保留"
    assert get_case(client, case_id)["doc_status"] == "internal_draft"


def test_inv8_deliver_requires_an_approved_document(client, db, storage):
    """INV-8 / R-41：只有 `approved` 文稿可交付；草稿交付 → 409。"""

    case = pipeline_case(client, db, storage)
    case_id = int(case["id"])
    created = create_document(client, case_id)
    document_id = created["payload"]["document_id"]

    blocked = client.post("/api/cases/{0}/deliver".format(case_id), json={
        "expected_version": case_version(client, case_id), "document_id": document_id,
        "formats": ["docx"]})
    assert blocked.status_code == 409, blocked.text
    assert "已审核" in blocked.json()["detail"]

    approved = client.post("/api/documents/{0}/approve".format(document_id), json={
        "expected_version": case_version(client, case_id)})
    assert approved.status_code == 200, approved.text
    delivered = client.post("/api/cases/{0}/deliver".format(case_id), json={
        "expected_version": case_version(client, case_id), "document_id": document_id,
        "formats": ["docx"]})
    assert delivered.status_code == 200, delivered.text
    payload = delivered.json()["payload"]
    assert payload["doc_status"] == "approved"
    assert payload["docx_path"]


# --------------------------------------------------------------------------
# R-39 / INV-7：适用范围
# --------------------------------------------------------------------------


def test_inv7_scope_outside_case_requires_scope_value(client, db, storage):
    """R-39 / INV-7：`scope != case_only` 必须写明适用范围（接口 422 + 数据层守卫）。"""

    case = pipeline_case(client, db, storage)
    case_id = int(case["id"])
    workflow_extract(db, storage, case_id)
    field = _fields(client, case_id)["body_length"]

    rejected = _correction(client, case_id, kind="terminology", target_id=field["id"],
                           before_text="压退", after_text="指压褪色",
                           reason="术语确认", scope="column", scope_value="")
    assert rejected.status_code == 422, rejected.text
    assert "适用范围" in rejected.json()["detail"]

    try:
        db.create_correction(case_id, {
            "kind": "terminology", "target_type": "field", "target_id": field["id"],
            "before_text": "压退", "after_text": "指压褪色", "reason": "术语确认",
            "scope": "column", "scope_value": "",
        }, int((db.get_case(case_id) or {}).get("version") or 1))
    except InvalidTransition as error:
        assert "适用范围" in str(error)
    else:  # pragma: no cover - 数据层守卫缺失时必须显式失败
        raise AssertionError("数据层必须拒绝缺少 scope_value 的非 case_only 修正")


def test_r39_non_case_only_scope_is_recorded_and_does_not_leak_to_other_cases(client, db, storage):
    """R-39 / N-10：拟用于以后案件的规则必须记录范围，且不得自动改写其他案件。"""

    first = pipeline_case(client, db, storage)
    first_id = int(first["id"])
    second = create_case(client, subject_name="乙案对象")
    second_id = int(second["id"])
    workflow_extract(db, storage, first_id)
    field = _fields(client, first_id)["body_length"]

    response = _correction(client, first_id, kind="terminology", target_id=field["id"],
                           before_text="压退", after_text="指压褪色",
                           reason="术语确认：退 = 指压褪色", scope="column",
                           scope_value="尸斑")
    assert response.status_code == 200, response.text
    record = response.json()
    assert record["scope"] == "column"
    assert record["scope_value"] == "尸斑"

    assert client.get("/api/cases/{0}/corrections".format(second_id)).json() == []
    assert _fields(client, second_id).get("body_length") is None


def test_r38_workflow_order_correction_is_recorded(client, db, storage):
    """R-38 / R-7：工作顺序调整也要留痕（`workflow_order` 类型允许空 `after_text`）。"""

    case = pipeline_case(client, db, storage)
    case_id = int(case["id"])
    response = _correction(client, case_id, kind="workflow_order", target_type="case",
                           target_id=0, before_text="先整理一般尸表所见",
                           after_text="先整理损伤", reason="按用户要求调整输出顺序")
    assert response.status_code == 200, response.text
    assert response.json()["kind"] == "workflow_order"


def test_r7_injuries_can_be_reordered_and_order_is_persisted(client, db, storage):
    """R-7 / R-16：损伤条目的顺序可按用户要求调整，且保持逐条对应。"""

    case = create_case(client)
    case_id = int(case["id"])
    created = []
    for index, (part, side) in enumerate((("前臂", "左"), ("小腿", "右"), ("头部", "")), start=1):
        response = client.post("/api/cases/{0}/injuries".format(case_id), json={
            "expected_version": case_version(client, case_id), "seq": index,
            "body_part": part, "side": side, "injury_type": "擦伤",
            "length": "3", "width": "2", "size_unit": "cm",
        })
        assert response.status_code == 200, response.text
        created.append(response.json()["id"])

    reversed_ids = list(reversed(created))
    response = client.post(
        "/api/cases/{0}/injuries/reorder".format(case_id),
        params={"expected_version": case_version(client, case_id)},
        json={"ids": reversed_ids})
    assert response.status_code == 200, response.text
    listed = client.get("/api/cases/{0}/injuries".format(case_id)).json()
    assert [item["id"] for item in listed] == reversed_ids
    assert [item["seq"] for item in listed] == [1, 2, 3]
    assert {item["body_part"] for item in listed} == {"前臂", "小腿", "头部"}


# --------------------------------------------------------------------------
# 校核项处置（R-46 / INV-5）
# --------------------------------------------------------------------------


def test_waived_checklist_item_requires_a_reason(client, db, storage):
    """R-46：豁免校核项必须填写理由（空理由 422；填写后落库并记录处置人）。"""

    case = pipeline_case(client, db, storage)
    case_id = int(case["id"])
    workflow_extract(db, storage, case_id)
    run_check(client, case_id)
    item = [row for row in client.get("/api/cases/{0}/checklist".format(case_id)).json()
            if row["severity"] == "blocking"][0]

    rejected = client.patch("/api/checklist/{0}".format(item["id"]), json={
        "expected_version": case_version(client, case_id), "status": "waived", "note": "  "})
    assert rejected.status_code == 422, rejected.text
    assert "豁免校核项必须填写理由" in rejected.json()["detail"]

    accepted = client.patch("/api/checklist/{0}".format(item["id"]), json={
        "expected_version": case_version(client, case_id), "status": "waived",
        "note": "材料确实缺失，已在交付说明中记录", "resolved_by": "复核人乙"})
    assert accepted.status_code == 200, accepted.text
    body = accepted.json()
    assert body["status"] == "waived"
    assert body["note"] == "材料确实缺失，已在交付说明中记录"
    assert body["resolved_by"] == "复核人乙"
    assert body["resolved_at"]


def test_corrected_target_must_exist(client, db, storage):
    """第 4.2 节：修正指向不存在的目标 → 404（不得静默成功）。"""

    case = pipeline_case(client, db, storage)
    case_id = int(case["id"])
    response = _correction(client, case_id, target_type="field", target_id=999999,
                           before_text="A", after_text="B")
    assert response.status_code == 404, response.text
    assert "不存在" in response.json()["detail"]


def test_correction_scope_must_be_from_the_frozen_enum(client, db, storage):
    """§5：`scope` 取值受 `CORRECTION_SCOPES` 约束（未知范围 422）。"""

    case = pipeline_case(client, db, storage)
    case_id = int(case["id"])
    response = _correction(client, case_id, scope="global", scope_value="全部")
    assert response.status_code == 422, response.text
