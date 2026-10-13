"""案件与基础接口用例（R-10 / R-11 / R-12 / R-13 / R-14 / R-15 / R-17 / R-47 / R-48 / R-51、N-1 / N-10、INV-1 / INV-9 / INV-10）。

HTTP 层约定：`expected_version` 归属案件版本；错误体统一 `{"detail": 中文}`；
`422` Pydantic 校验失败、`409` 版本冲突 / 非法阶段流转 / 重复编号、`404` 资源不存在。
"""

from __future__ import annotations

import re
from pathlib import Path

from backend.app.constants import FIELD_INDEX, FIELD_GROUP_ORDER, PHOTO_POSITION_KEYS
from backend.tests.helpers import (
    b64,
    case_version,
    create_case,
    db_add_material,
    db_add_photo,
    get_case,
    run_ocr,
    solid_png_bytes,
    workflow_extract,
)


# --------------------------------------------------------------------------
# 建案 / 编号 / 事件
# --------------------------------------------------------------------------


def test_r48_case_no_is_auto_generated_and_stored_with_intake_event(client):
    """R-48：案件编号留空时自动生成 `FY-<年份>-<序号>`，建案即落审计事件。"""

    case = create_case(client)
    assert re.fullmatch(r"FY-\d{4}-\d{4}", case["case_no"]), case["case_no"]
    assert case["stage"] == "intake"
    assert int(case["version"]) >= 1
    assert case["intake"]["final_artifacts"] == "docx,pdf"
    events = get_case(client, case["id"])["events"]
    event_types = {item["event_type"] for item in events}
    assert {"case_created", "intake"} <= event_types


def test_r48_stage_change_is_written_to_the_audit_timeline(client):
    """R-48：阶段变更写入 `case_events`（可追溯的处理记录）。"""

    case = create_case(client)
    response = client.patch("/api/cases/{0}/stage".format(case["id"]), json={
        "expected_version": case_version(client, case["id"]),
        "stage": "image_check", "note": "测试推进",
    })
    assert response.status_code == 200, response.text
    assert response.json()["stage"] == "image_check"
    events = get_case(client, case["id"])["events"]
    stage_events = [item for item in events if item["event_type"] == "stage_changed"]
    assert stage_events and "接收材料" in stage_events[0]["detail"]


def test_duplicate_case_no_returns_409():
    """第 4.2 节：重复案件编号 → 409 + 中文原因。"""

    from fastapi.testclient import TestClient

    from backend.app.main import create_app

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        app = create_app(db_path=str(Path(tmp) / "c.db"),
                         storage_root=str(Path(tmp) / "storage"), seed_data=False)
        with TestClient(app) as client:
            first = client.post("/api/cases", json={"case_no": "FY-TEST-0001"})
            assert first.status_code == 200, first.text
            again = client.post("/api/cases", json={"case_no": "FY-TEST-0001"})
            assert again.status_code == 409, again.text
            assert "已存在" in again.json()["detail"]


# --------------------------------------------------------------------------
# 乐观锁 / 校验（INV-9）
# --------------------------------------------------------------------------


def test_inv9_case_update_without_expected_version_is_422(client):
    """INV-9：所有写操作必须携带 `expected_version`，缺失即 422。"""

    case = create_case(client)
    response = client.patch("/api/cases/{0}".format(case["id"]),
                            json={"subject_name": "缺版本号"})
    assert response.status_code == 422, response.text
    assert "expected_version" in response.json()["detail"]


def test_inv9_stale_expected_version_returns_409(client):
    """INV-9：版本过期 → 409 “案件已被其他操作更新，请刷新后重试”。"""

    case = create_case(client)
    stale = int(case["version"]) - 1 if int(case["version"]) > 1 else 999
    response = client.patch("/api/cases/{0}".format(case["id"]), json={
        "expected_version": stale, "subject_name": "过期写入",
    })
    assert response.status_code == 409, response.text
    assert "已被其他操作更新" in response.json()["detail"]
    assert get_case(client, case["id"])["subject_name"] != "过期写入"


def test_inv9_case_update_with_current_version_succeeds_and_bumps_version(client):
    """INV-9：携带当前版本写入成功，且案件版本自增一格。"""

    case = create_case(client)
    before = case_version(client, case["id"])
    response = client.patch("/api/cases/{0}".format(case["id"]), json={
        "expected_version": before, "subject_name": "王某",
    })
    assert response.status_code == 200, response.text
    assert response.json()["subject_name"] == "王某"
    assert case_version(client, case["id"]) == before + 1


def test_case_update_requires_at_least_one_field():
    """§4：`CaseUpdate` 至少需要一个可修改字段（纯 `expected_version` 无效）。"""

    from pydantic import ValidationError

    from backend.app.schemas import CaseUpdate

    try:
        CaseUpdate(expected_version=1)
    except ValidationError as error:
        assert "至少需要修改一个案件字段" in str(error)
    else:  # pragma: no cover - 契约被放宽时必须显式失败
        raise AssertionError("空修改应被拒绝")


# --------------------------------------------------------------------------
# 阶段状态机
# --------------------------------------------------------------------------


def test_stage_transition_allows_one_step_forward_and_one_step_back(client):
    """R-48 / §3.1：阶段只能前进一格或回退一格。"""

    case = create_case(client)
    case_id = case["id"]
    forward = client.patch("/api/cases/{0}/stage".format(case_id), json={
        "expected_version": case_version(client, case_id), "stage": "image_check"})
    assert forward.status_code == 200, forward.text
    back = client.patch("/api/cases/{0}/stage".format(case_id), json={
        "expected_version": case_version(client, case_id), "stage": "intake"})
    assert back.status_code == 200, back.text
    assert back.json()["stage"] == "intake"


def test_invalid_stage_jump_returns_409(client):
    """§3.1 / 第 4.2 节：跨两格跳转 → 409（`InvalidTransition`）。"""

    case = create_case(client)
    response = client.patch("/api/cases/{0}/stage".format(case["id"]), json={
        "expected_version": case_version(client, case["id"]), "stage": "extract"})
    assert response.status_code == 409, response.text
    assert "阶段只能前进或回退一格" in response.json()["detail"]


def test_unknown_stage_value_is_rejected_by_schema(client):
    """§3.1：`cases.stage` 取值受 `CASE_STAGES` 约束（未知值 422）。"""

    case = create_case(client)
    response = client.patch("/api/cases/{0}/stage".format(case["id"]), json={
        "expected_version": case_version(client, case["id"]), "stage": "no_such_stage"})
    assert response.status_code == 422, response.text


# --------------------------------------------------------------------------
# 详情结构
# --------------------------------------------------------------------------


def test_r11_case_detail_exposes_every_aggregate_section(client):
    """R-11 / §5.2：案件详情结构完整（材料/字段/损伤/文稿/清单/事件/修正/摘要）。"""

    case = create_case(client)
    detail = get_case(client, case["id"])
    for key in ("materials", "fields", "injuries", "documents", "checklist",
                "events", "corrections", "summary", "intake", "section_order",
                "photo_slots"):
        assert key in detail, key
    assert isinstance(detail["materials"], list)
    assert isinstance(detail["section_order"], list)
    assert detail["summary"]["material_count"] == len(detail["materials"])
    for key in ("material_count", "field_count", "injury_count",
                "unresolved_count", "blocking_count", "document_count"):
        assert key in detail["summary"]


def test_json_columns_are_parsed_into_objects_not_strings(client, db, storage):
    """§5.2：所有 `*_json` 列必须已解析为 Python 对象，不得把 JSON 字符串透传。"""

    case = create_case(client)
    case_id = case["id"]
    material = db_add_material(db, storage, case_id, "photo", "p.png",
                              content=solid_png_bytes((100, 80)), position_key="head",
                              quality_flags=["blurred"])
    db.upsert_fields(case_id, [{
        "field_key": "body_length", "raw_text": "尸长172", "status": "recorded",
        "source_material_id": material["id"], "source_page": 1,
        "source_bbox": [0.1, 0.2, 0.5, 0.3], "conflict": [{"value": "172", "material_id": 1}],
        "applied_rule_codes": ["body_length_unit"],
    }], False, 0)
    db.replace_checklist(case_id, None, [{
        "category": "source", "severity": "blocking", "code": "MATERIAL_MISSING",
        "title": "必需材料缺失", "detail": "测试", "target_type": "material", "target_id": 0,
        "source": {"kind": "commission"},
    }], "测试")

    detail = get_case(client, case_id)
    photo = [item for item in detail["materials"] if item["kind"] == "photo"][0]
    assert photo["quality_flags"] == ["blurred"]
    assert isinstance(photo["pages"], list)
    field = [item for item in detail["fields"] if item["field_key"] == "body_length"][0]
    assert field["source_bbox"] == [0.1, 0.2, 0.5, 0.3]
    assert isinstance(field["conflict"], list) and field["conflict"][0]["value"] == "172"
    assert field["applied_rule_codes"] == ["body_length_unit"]
    assert isinstance(detail["checklist"][0]["source"], dict)
    assert detail["checklist"][0]["source"]["kind"] == "commission"

    # 递归检查：不得出现任何以 `_json` 结尾的键（那说明字符串没被解析）
    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                assert not str(key).endswith("_json"), key
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(detail)


def test_r14_r15_extracted_fields_carry_group_and_full_source_trace(client, db, storage):
    """R-14 / R-15 / INV-1：字段组由目录派生，且八项来源信息可从接口取回。"""

    from backend.tests.helpers import pipeline_case

    case = pipeline_case(client, db, storage)
    case_id = int(case["id"])
    workflow_extract(db, storage, case_id)
    fields = {row["field_key"]: row for row in client.get(
        "/api/cases/{0}/fields".format(case_id)).json()}
    assert "body_length" in fields
    for row in fields.values():
        assert row["field_group"] == FIELD_INDEX[row["field_key"]].group
        assert row["field_group"] in FIELD_GROUP_ORDER
    body = fields["body_length"]
    assert body["raw_text"] == "尸长172cm"
    assert body["source_material_id"] and body["source_page"] == 1
    assert len(body["source_bbox"]) == 4
    assert body["status"] == "recorded"
    assert body["review_state"] == "pending"
    assert body["normalized_text"] != "" or body["confirmed_value"] == ""


def test_n1_opinion_text_is_only_written_by_user_request(client, db, storage):
    """N-1 / INV-10：识别原文不得被推导成鉴定意见；`opinion_text` 只能由用户写入。"""

    from backend.tests.helpers import create_document, pipeline_case, run_extract as _unused

    case = pipeline_case(client, db, storage)
    case_id = int(case["id"])
    workflow_extract(db, storage, case_id)
    assert get_case(client, case_id)["opinion_text"] == ""

    created = create_document(client, case_id)
    docx_path = created["payload"]["docx_absolute"]
    from backend.tests.helpers import docx_text

    assert "鉴定意见" in docx_text(docx_path)
    assert case["case_summary"] != ""  # 案情来自材料，但不进入意见栏

    before = case_version(client, case_id)
    response = client.patch("/api/cases/{0}".format(case_id), json={
        "expected_version": before, "opinion_text": "由鉴定人提供的意见文本",
    })
    assert response.status_code == 200, response.text
    assert response.json()["opinion_text"] == "由鉴定人提供的意见文本"
    assert case_version(client, case_id) == before + 1


def test_r17_field_status_dictionary_is_exposed_by_config(client):
    """R-17：五档信息状态由 `/api/config` 暴露，且互不替代。"""

    config = client.get("/api/config").json()
    keys = [item["key"] for item in config["field_statuses"]]
    assert keys == ["recorded", "not_recorded", "not_examined", "negative", "unclear"]
    assert config["review_states"] == [{"key": "pending", "label": "待核对"},
                                       {"key": "confirmed", "label": "已确认"}]


def test_r12_r13_config_reports_material_and_ocr_contract(client):
    """R-12 / R-13 / R-55 / N-11：材料类型、必需的 OCR 类型与照片策略。"""

    config = client.get("/api/config").json()
    kinds = {item["key"]: item for item in config["material_kinds"]}
    assert {"commission", "record_book", "template"} <= set(kinds)
    assert kinds["photo"]["photo"] is True
    assert kinds["photo"]["ocr"] is False
    assert "photo" not in config["ocr_material_kinds"]
    assert config["required_material_kinds"] == ["commission", "record_book", "template"]
    assert config["photo_placeholder_text"] == "（未提供照片）"
    assert "不读取照片内容" in config["photo_policy"]
    assert [item["key"] for item in config["photo_positions"]] == list(PHOTO_POSITION_KEYS)


def test_config_reports_engine_status_and_todo_downloads(client):
    """§4 / §8：`/api/config` 如实报告渲染引擎、LibreOffice 与 TODO-DL 记账。"""

    config = client.get("/api/config").json()
    engines = config["engines"]
    assert engines["docx"]["todo"] == "TODO-DL-1"
    assert "docxtpl_available" in engines["docx"]
    ocr_status = {item["name"]: item for item in engines["ocr"]}
    assert ocr_status["sidecar"]["available"] is True
    todo_codes = {item["code"] for item in config["todo_downloads"]}
    assert {"TODO-DL-1", "TODO-DL-2", "TODO-DL-3", "TODO-DL-6", "TODO-DL-7"} <= todo_codes
    assert config["demo_data"] is True


def test_stats_endpoint_shape(client):
    """第 4 节：`/api/stats` 返回案件数、阶段分布、待核对与阻断计数。"""

    create_case(client)
    stats = client.get("/api/stats").json()
    assert stats["case_count"] == stats["total_cases"] >= 1
    assert set(stats["stage_counts"]) >= {"intake", "delivered"}
    assert "blocking_count" in stats and "unresolved_count" in stats
    assert {item["stage"] for item in stats["by_stage"]} >= {"intake"}
    assert {item["kind"] for item in stats["by_material_kind"]} >= {"commission", "photo"}


def test_health_endpoint_shape(client):
    """第 4 节：`GET /api/health` 返回服务标识。"""

    body = client.get("/api/health").json()
    assert body == {"status": "ok", "service": "forensic-doc-agent"}


# --------------------------------------------------------------------------
# 列表：分页 / 搜索 / 筛选
# --------------------------------------------------------------------------


def test_list_cases_pagination_search_and_filters(client, db, storage):
    """§4.1：服务端分页、`search`、`stage`、`material_kind`、`field_status`、`has_blocking`。"""

    first = create_case(client, subject_name="张三", commission_unit="甲单位")
    second = create_case(client, subject_name="李四", commission_unit="乙单位")
    third = create_case(client, subject_name="王五", commission_unit="丙单位")

    listed = client.get("/api/cases", params={"page": 1, "page_size": 2}).json()
    assert listed["total"] == 3 and listed["page"] == 1 and listed["page_size"] == 2
    assert len(listed["items"]) == 2
    page_two = client.get("/api/cases", params={"page": 2, "page_size": 2}).json()
    assert len(page_two["items"]) == 1
    assert {"material_count", "field_count", "injury_count", "unresolved_count",
            "blocking_count", "document_count"} <= set(listed["items"][0])

    found = client.get("/api/cases", params={"search": "李四"}).json()
    assert found["total"] == 1 and found["items"][0]["subject_name"] == "李四"
    unit_hit = client.get("/api/cases", params={"search": "丙单位"}).json()
    assert unit_hit["total"] == 1

    db_add_photo(db, storage, second["id"], "p.png", position_key="head")
    by_kind = client.get("/api/cases", params={"material_kind": "photo"}).json()
    assert [item["id"] for item in by_kind["items"]] == [second["id"]]

    db.upsert_fields(third["id"], [{"field_key": "cornea", "raw_text": "未检",
                                    "status": "not_examined"}], False, 0)
    by_status = client.get("/api/cases", params={"field_status": "not_examined"}).json()
    assert [item["id"] for item in by_status["items"]] == [third["id"]]

    db.replace_checklist(first["id"], None, [{
        "category": "source", "severity": "blocking", "code": "MATERIAL_MISSING",
        "title": "必需材料缺失", "detail": "测试", "target_type": "material", "target_id": 0,
    }], "测试")
    blocking = client.get("/api/cases", params={"has_blocking": "true"}).json()
    assert [item["id"] for item in blocking["items"]] == [first["id"]]
    clean = client.get("/api/cases", params={"has_blocking": "false"}).json()
    assert first["id"] not in [item["id"] for item in clean["items"]]

    staged = client.get("/api/cases", params={"stage": "intake"}).json()
    assert staged["total"] == 3
    assert client.get("/api/cases", params={"stage": "delivered"}).json()["total"] == 0


def test_list_cases_rejects_out_of_range_pagination(client):
    """§4.1：`page >= 1`、`page_size` 1–100（越界 422）。"""

    assert client.get("/api/cases", params={"page": 0}).status_code == 422
    assert client.get("/api/cases", params={"page_size": 101}).status_code == 422
    assert client.get("/api/cases", params={"page_size": 100}).status_code == 200


# --------------------------------------------------------------------------
# 404 / 删除
# --------------------------------------------------------------------------


def test_404_for_unknown_case_material_and_document(client):
    """第 4.2 节：不存在的资源统一 404 + 中文原因。"""

    case = create_case(client)
    assert client.get("/api/cases/999999").status_code == 404
    assert client.get("/api/cases/{0}/materials/999999/file".format(case["id"])).status_code == 404
    assert client.get("/api/documents/999999/download").status_code == 404
    assert client.get("/api/cases/{0}/fields".format(999999)).status_code == 404
    assert client.get("/api/cases/{0}/checklist".format(999999)).status_code == 404
    assert client.get("/api/cases/{0}/injuries".format(999999)).status_code == 404


def test_material_file_variant_validation(client, db, storage):
    """§4：`variant` 只能是 `original` / `derived`；缺文件时 404。"""

    case = create_case(client)
    case_id = case["id"]
    material = db_add_material(db, storage, case_id, "commission", "c.png",
                               content=solid_png_bytes((120, 90)), page_count=1)
    url = "/api/cases/{0}/materials/{1}/file".format(case_id, material["id"])
    assert client.get(url).status_code == 200
    assert client.get(url, params={"variant": "nope"}).status_code == 400
    assert client.get(url, params={"variant": "derived", "page": 1}).status_code == 404
    assert client.get(url, params={"variant": "derived", "page": 99}).status_code == 404


def test_delete_case_uses_query_parameter_and_removes_directory(client, db, storage):
    """§4 / R-19：`DELETE` 的 `expected_version` 走查询参数，删除后连同材料目录一起清理。"""

    case = create_case(client)
    case_id = case["id"]
    db_add_photo(db, storage, case_id, "p.png", position_key="head")
    directory = storage.case_dir(case["case_no"])
    assert directory.is_dir()

    assert client.delete("/api/cases/{0}".format(case_id)).status_code == 422
    stale = client.delete("/api/cases/{0}".format(case_id),
                          params={"expected_version": 999})
    assert stale.status_code == 409
    removed = client.delete("/api/cases/{0}".format(case_id),
                            params={"expected_version": case_version(client, case_id)})
    assert removed.status_code == 200, removed.text
    assert removed.json()["deleted"] is True
    assert not directory.exists()
    assert client.get("/api/cases/{0}".format(case_id)).status_code == 404


# --------------------------------------------------------------------------
# N-10 / R-47：案件隔离
# --------------------------------------------------------------------------


def test_n10_r47_cases_do_not_share_fields_materials_or_corrections(client, db, storage):
    """N-10 / R-47：案件之间不混用材料、字段、损伤与修正记录。"""

    alpha = create_case(client, subject_name="甲案对象")
    beta = create_case(client, subject_name="乙案对象")
    alpha_id, beta_id = alpha["id"], beta["id"]

    material = db_add_photo(db, storage, alpha_id, "alpha.png", position_key="head")
    db.upsert_fields(alpha_id, [{"field_key": "body_length", "raw_text": "尸长172cm",
                                 "status": "recorded", "source_material_id": material["id"]}],
                     False, 0)
    db.create_injury(alpha_id, {"body_part": "前臂", "side": "左", "injury_type": "擦伤"},
                     int((db.get_case(alpha_id) or {}).get("version") or 1))

    alpha_detail = get_case(client, alpha_id)
    beta_detail = get_case(client, beta_id)
    assert beta_detail["materials"] == []
    assert beta_detail["injuries"] == []
    assert beta_detail["corrections"] == []
    # 建案时会把案件基础信息同步成字段行；甲案提取出的尸长字段绝不出现在乙案
    assert "body_length" not in {row["field_key"] for row in beta_detail["fields"]}
    assert {row["field_key"] for row in alpha_detail["fields"]} == {
        "case_no", "commission_unit", "commission_matter", "commission_date",
        "case_summary", "subject_name", "template_version", "term_rule_version",
        "body_length",
    }
    assert beta_detail["summary"]["field_count"] != alpha_detail["summary"]["field_count"]
    assert beta_detail["subject_name"] == "乙案对象"

    # 在甲案记录一条修正：乙案字段与文稿不受影响
    field = alpha_detail["fields"][0]
    corrected = client.post("/api/cases/{0}/corrections".format(alpha_id), json={
        "expected_version": case_version(client, alpha_id), "kind": "recognition",
        "target_type": "field", "target_id": field["id"], "before_text": "尸长172cm",
        "after_text": "尸长173cm", "reason": "识别纠正：原页为 173（测试）",
    })
    assert corrected.status_code == 200, corrected.text
    assert get_case(client, beta_id)["corrections"] == []
    assert "body_length" not in {row["field_key"] for row in get_case(client, beta_id)["fields"]}


def test_n10_photo_slots_are_scoped_to_the_case(client, db, storage):
    """N-10 / R-57：照片栏位视图只包含本案照片。"""

    alpha = create_case(client)
    beta = create_case(client)
    db_add_photo(db, storage, alpha["id"], "alpha.png", position_key="head", caption="甲案头部")
    db_add_photo(db, storage, beta["id"], "beta.png", position_key="chest", caption="乙案胸部")

    alpha_slots = client.get("/api/cases/{0}/photo-slots".format(alpha["id"])).json()
    filled = {slot["position_key"]: slot["photos"] for slot in alpha_slots["slots"]
              if slot["photos"]}
    assert list(filled) == ["head"]
    assert filled["head"][0]["caption"] == "甲案头部"


def test_ocr_blocks_endpoint_is_paginated_and_scoped_to_case(client, db, storage):
    """R-15 / §4：识别结果分页且按案件隔离。"""

    from backend.tests.helpers import pipeline_case

    case = pipeline_case(client, db, storage)
    case_id = int(case["id"])
    other = create_case(client)
    page = client.get("/api/cases/{0}/ocr-blocks".format(case_id),
                      params={"page_size": 5}).json()
    assert page["total"] > 0 and page["page"] == 1 and page["page_size"] == 5
    assert all(item["case_id"] == case_id for item in page["items"])
    empty = client.get("/api/cases/{0}/ocr-blocks".format(other["id"])).json()
    assert empty["total"] == 0
    assert client.get("/api/cases/{0}/ocr-blocks".format(case_id),
                      params={"page_size": 5000}).status_code == 422
