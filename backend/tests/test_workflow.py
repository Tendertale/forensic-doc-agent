"""`workflow` 阶段编排与模型接口用例（R-10 / R-19 / R-21 / R-24 / R-28 / R-40 / R-48 /
R-52、N-1 / N-2 / N-5 / N-8 / N-11、INV-3 / INV-5 / INV-9）。

阶段编排按 `docs/architecture.md` 第 5.10 节的冻结签名直接调用；
`llm` 一律用 `httpx.MockTransport`，**不访问真实网络**。
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from backend.app import llm
from backend.app.database import BlockedByChecklist, InvalidTransition
from backend.app.workflow import run_check, run_ocr, run_preprocess, run_render
from backend.tests.helpers import (
    case_version,
    create_case,
    create_document,
    db_add_material,
    db_add_photo,
    get_case,
    pipeline_case,
    run_check as api_run_check,
    run_normalize,
    run_ocr as api_run_ocr,
    solid_png_bytes,
    upload_scan,
    workflow_extract,
)


# --------------------------------------------------------------------------
# 阶段推进（R-48 / §3.1）
# --------------------------------------------------------------------------


def test_stage_advances_one_step_per_executed_stage(client, db, storage):
    """§5 / R-48：预处理 → 检查图像、识别 → 识别原文、提取 → 提取字段，自动前进一格。"""

    case = create_case(client)
    case_id = int(case["id"])
    assert get_case(client, case_id)["stage"] == "intake"

    upload_scan(client, db, storage, case_id, case["case_no"], kind="record_book",
                filename="rb.png", blocks=[{"text": "尸长172cm", "confidence": 0.93,
                                            "column_hint": "尸长"}])
    assert get_case(client, case_id)["stage"] == "image_check"

    api_run_ocr(client, case_id)
    assert get_case(client, case_id)["stage"] == "ocr"

    result = workflow_extract(db, storage, case_id)
    assert result["payload"]["fields"] == 1
    assert get_case(client, case_id)["stage"] == "extract"


def test_normalize_does_not_skip_the_manual_verification_stage(client, db, storage):
    """§5：第 5 步“核对关键事实”必须由用户显式推进，整理阶段不得跨越该步。"""

    case = pipeline_case(client, db, storage)
    case_id = int(case["id"])
    workflow_extract(db, storage, case_id)
    assert get_case(client, case_id)["stage"] == "extract"

    run_normalize(client, case_id)
    assert get_case(client, case_id)["stage"] == "extract"

    advanced = client.patch("/api/cases/{0}/stage".format(case_id), json={
        "expected_version": case_version(client, case_id), "stage": "verify_facts"})
    assert advanced.status_code == 200, advanced.text
    assert advanced.json()["stage"] == "verify_facts"


# --------------------------------------------------------------------------
# 阻断（INV-5 / R-28）
# --------------------------------------------------------------------------


def test_run_render_pending_issue_raises_blocked_by_checklist(client, db, storage):
    """INV-5：存在 open 阻断项时 `run_render(doc_status="pending_issue")` 抛 `BlockedByChecklist`。"""

    case = pipeline_case(client, db, storage)
    case_id = int(case["id"])
    workflow_extract(db, storage, case_id)
    api_run_check(client, case_id)
    assert db.open_blocking_items(case_id)

    with pytest.raises(BlockedByChecklist):
        run_render(db, storage, case_id, doc_status="pending_issue",
                   formats=["docx"], expected_version=0)

    # 内部草稿不受阻断限制（R-27）
    draft = run_render(db, storage, case_id, doc_status="internal_draft",
                       formats=["docx"], expected_version=0)
    assert draft.ok is True
    assert draft.payload["doc_status"] == "internal_draft"


def test_run_check_is_idempotent_and_keeps_handled_items(client, db, storage):
    """R-6 / R-46：重复校核不重复写入清单，且保留人工已处置的结论。"""

    case = pipeline_case(client, db, storage)
    case_id = int(case["id"])
    workflow_extract(db, storage, case_id)

    first = run_check(db, storage, case_id, expected_version=0)
    second = run_check(db, storage, case_id, expected_version=0)
    assert first.payload["item_count"] == second.payload["item_count"]

    items = client.get("/api/cases/{0}/checklist".format(case_id)).json()
    target = [item for item in items if item["severity"] == "blocking"][0]
    db.update_checklist_item(target["id"], {"status": "resolved", "note": "已人工核对"}, 0)

    third = run_check(db, storage, case_id, expected_version=0)
    after = client.get("/api/cases/{0}/checklist".format(case_id)).json()
    assert third.payload["item_count"] == second.payload["item_count"]
    handled = [item for item in after if item["id"] == target["id"]]
    assert len(handled) == 1
    assert handled[0]["status"] == "resolved"
    assert handled[0]["note"] == "已人工核对"


# --------------------------------------------------------------------------
# OCR（R-19 / N-11）
# --------------------------------------------------------------------------


def test_run_ocr_without_force_skips_pages_already_done(client, db, storage):
    """R-19 / R-52：`force=False` 时不重复识别已完成的页（避免重复写入识别结果）。"""

    case = pipeline_case(client, db, storage)
    case_id = int(case["id"])
    before = client.get("/api/cases/{0}/ocr-blocks".format(case_id)).json()["total"]

    again = run_ocr(db, storage, case_id, expected_version=0)
    assert again.payload["pages"] == 0
    assert again.payload["skipped_pages"]
    after = client.get("/api/cases/{0}/ocr-blocks".format(case_id)).json()["total"]
    assert after == before

    forced = run_ocr(db, storage, case_id, expected_version=0, force=True)
    assert forced.payload["pages"] >= 1


def test_run_preprocess_skips_photo_materials_entirely(client, db, storage):
    """N-11 / R-55：部位照片不做预处理、不生成识别用副本（`skipped_photo_id`）。"""

    case = create_case(client)
    case_id = int(case["id"])
    photo = db_add_photo(db, storage, case_id, "head.png", position_key="head")

    result = run_preprocess(db, storage, case_id, photo["id"], ["autocontrast"], 0,
                            expected_version=0)
    assert result.ok is True
    assert result.payload["skipped_photo_id"] == photo["id"]
    assert result.payload["pages"] == []
    assert not storage.derived_dir(case["case_no"], "photo").exists()


# --------------------------------------------------------------------------
# 字段写回（INV-3 / R-40）
# --------------------------------------------------------------------------


def test_apply_normalization_keeps_fact_columns_and_forces_not_recorded(client, db, storage):
    """INV-3 / N-2 / N-5：整理只改规范化列；`not_recorded` 恒为“未记录”。"""

    case = create_case(client)
    case_id = int(case["id"])
    db.upsert_fields(case_id, [
        {"field_key": "body_length", "raw_text": "尸长172", "confirmed_value": "尸长172",
         "status": "recorded"},
        {"field_key": "clothing", "raw_text": "/", "status": "not_recorded"},
    ], False, 0)

    db.apply_normalization(case_id, [
        {"target_type": "field", "field_key": "body_length",
         "normalized_text": "尸长172 cm。", "status": "recorded",
         "applied_rule_codes": ["body_length_unit"], "requires_confirmation": False},
        {"target_type": "field", "field_key": "clothing",
         "normalized_text": "未见异常", "status": "not_recorded"},
    ], 0)

    body = db.get_field(case_id, "body_length")
    assert body["raw_text"] == "尸长172"
    assert body["confirmed_value"] == "尸长172"
    assert body["normalized_text"] == "尸长172 cm。"
    assert body["applied_rule_codes"] == ["body_length_unit"]

    clothing = db.get_field(case_id, "clothing")
    assert clothing["normalized_text"] == "未记录"
    assert "未见异常" not in clothing["normalized_text"]


def test_upsert_fields_keeps_confirmed_rows_on_reextract(client, db, storage):
    """R-40 / §5.2：重复提取时保留已确认字段，不静默清空用户的确认结果。"""

    case = create_case(client)
    case_id = int(case["id"])
    db.upsert_fields(case_id, [{"field_key": "body_length", "raw_text": "尸长172cm",
                                "status": "recorded", "review_state": "confirmed",
                                "confirmed_value": "尸长172cm"}], True, 0)
    db.upsert_fields(case_id, [{"field_key": "livor_mortis", "raw_text": "背侧",
                                "status": "recorded"}], True, 0)

    keys = {row["field_key"] for row in db.list_fields(case_id)}
    assert "body_length" in keys
    assert "livor_mortis" in keys
    assert db.get_field(case_id, "body_length")["review_state"] == "confirmed"


def test_inv9_internal_writes_with_zero_version_do_not_check_the_lock(client, db, storage):
    """INV-9：`expected_version=0` 表示系统内部写（不校验但同样递增案件版本）。"""

    case = create_case(client)
    case_id = int(case["id"])
    before = case_version(client, case_id)
    db.claim(case_id, 0)
    assert case_version(client, case_id) == before + 1
    with pytest.raises(Exception):
        db.claim(case_id, before + 99)  # 过期版本 → VersionConflict


# --------------------------------------------------------------------------
# 交付（INV-8）
# --------------------------------------------------------------------------


def test_deliver_without_any_document_raises_invalid_transition(client, db, storage):
    """INV-8：没有任何已审核文稿时交付 → `InvalidTransition`（→ HTTP 409）。"""

    from backend.app.workflow import run_delivery

    case = create_case(client)
    with pytest.raises(InvalidTransition):
        run_delivery(db, storage, case["id"], formats=["docx"], expected_version=0)

    created = create_document(client, case["id"])
    with pytest.raises(InvalidTransition):
        run_delivery(db, storage, int(case["id"]),
                     document_id=created["payload"]["document_id"],
                     formats=["docx"], expected_version=0)


# --------------------------------------------------------------------------
# 模型接口（N-1 / N-2 / R-10）：只用 MockTransport
# --------------------------------------------------------------------------


def test_llm_extract_only_accepts_values_backed_by_a_source_block():
    """N-1 / N-2：模型只能挑选来源块；不在目录中、来源不明或凭空编造的取值一律丢弃。"""

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = {key.lower(): value for key, value in request.headers.items()}
        seen["url"] = str(request.url)
        content = json.dumps({"fields": [
            {"field_key": "body_length", "value": "尸长172cm", "source_block_id": "11"},
            {"field_key": "not_a_field", "value": "x", "source_block_id": "11"},
            {"field_key": "subject_name", "value": "凭空编造的名字", "source_block_id": "11"},
            {"field_key": "body_weight", "value": "60kg", "source_block_id": "99"},
        ]}, ensure_ascii=False)
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                     trust_env=False) as client:
            return await llm.extract_with_llm(
                [{"id": 11, "page_number": 1, "material_id": 2, "text": "尸长172cm"}],
                "sk-w5-test", base_url="https://llm.invalid/v1", client=client)

    result = asyncio.run(run())
    assert [item["field_key"] for item in result["fields"]] == ["body_length"]
    assert result["fields"][0]["source_block_id"] == 11
    reasons = {item["field_key"]: item["reason"] for item in result["dropped"]}
    assert "not_a_field" in reasons
    assert "subject_name" in reasons and "未能" in reasons["subject_name"]
    assert "body_weight" in reasons
    assert seen["url"].endswith("/chat/completions")
    assert "sk-w5-test" in json.dumps(seen["headers"], ensure_ascii=False)


def test_llm_verify_key_maps_status_codes_and_requires_a_key():
    """§5.9 / R-10：密钥缺失或无效时抛 `LlmAPIError`（401），且不透传上游正文。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "Invalid key sk-upstream-secret"}})

    client = httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)
    with pytest.raises(llm.LlmAPIError) as info:
        llm.verify_key("sk-w5-bad", base_url="https://llm.invalid/v1", client=client)
    assert info.value.status_code == 401
    assert "sk-upstream-secret" not in str(info.value)

    with pytest.raises(llm.LlmAPIError) as missing:
        llm.verify_key("")
    assert missing.value.status_code == 401

    ok_client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"data": []})),
        trust_env=False)
    healthy = llm.verify_key("sk-w5-ok", base_url="https://llm.invalid/v1", client=ok_client)
    assert healthy["status"] == "ok"
    assert healthy["provider"] == "deepseek"


def test_llm_extract_returns_empty_result_without_blocks():
    """R-10 / N-2：没有文本块时不发起任何请求（不猜内容、不占用额度）。"""

    def handler(request: httpx.Request):  # pragma: no cover - 不应被调用
        raise AssertionError("没有文本块时不得发起网络请求")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                     trust_env=False) as client:
            return await llm.extract_with_llm([], "sk-w5-test", client=client)

    result = asyncio.run(run())
    assert result["fields"] == [] and result["dropped"] == []
