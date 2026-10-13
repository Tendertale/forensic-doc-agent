"""校核清单用例（R-6 / R-21 / R-26 / R-28 / R-44 / R-45 / R-46、N-1 / N-5 / N-11 / N-12）。

覆盖 `docs/architecture.md` 第 5.7 节的全部 23 个 `code`：`category` / `severity`
与文档逐字一致；`check_case` 去重与返回顺序；照片相关 4 个 code（N-11/N-12/R-55/R-57）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.checker import (
    CheckItem,
    check_case,
    check_content,
    check_recognition,
    has_blocking,
)

#: 第 5.7 节的 code → (category, severity) 契约表（**逐字**取自文档）。
CHECK_CONTRACT = {
    "MATERIAL_MISSING": ("source", "blocking"),
    "FIELD_SOURCE_MISSING": ("source", "blocking"),
    "FIELD_UNRESOLVED_CRITICAL": ("content", "blocking"),
    "FIELD_REQUIRED_EMPTY": ("content", "blocking"),
    "FIELD_CONFLICT": ("content", "blocking"),
    "OCR_LOW_CONFIDENCE": ("recognition", "warning"),
    "OCR_HANDWRITTEN_UNVERIFIED": ("recognition", "warning"),
    "INJURY_UNRESOLVED": ("content", "warning"),
    "INJURY_MISSING_SIZE": ("content", "warning"),
    "NEGATION_UNPRESERVED": ("content", "blocking"),
    "NOT_RECORDED_MISLABELED": ("content", "blocking"),
    "OPINION_FROM_SOURCE": ("content", "blocking"),
    "DOCUMENT_UNRESOLVED_PLACEHOLDER": ("format", "blocking"),
    "DOCUMENT_DRAFT_MARKER": ("format", "blocking"),
    "DOCUMENT_PDF_MISSING": ("format", "warning"),
    "DOCUMENT_TEXT_DIVERGED": ("format", "warning"),
    "CORRECTION_UNAPPLIED": ("correction", "warning"),
    "CORRECTION_SCOPE_UNCLEAR": ("correction", "warning"),
    "REVIEW_INVALIDATED": ("correction", "info"),
    "PHOTO_CONTENT_READ": ("recognition", "blocking"),
    "PHOTO_POSITION_MISSING": ("content", "warning"),
    "PHOTO_FILE_MISSING": ("format", "warning"),
    "PHOTO_SLOT_EMPTY": ("format", "info"),
}

#: 完整的材料清单（满足 `REQUIRED_MATERIAL_KINDS`）。
FULL_MATERIALS = [
    {"id": 1, "kind": "commission", "filename": "commission.png"},
    {"id": 2, "kind": "record_book", "filename": "record_book.png"},
    {"id": 3, "kind": "template", "filename": "template.docx"},
]

#: 触发某一 code 的最小案件数据：code → (case_data, template_text, stored_text, snapshot)
#: 一个**确定不存在**的绝对路径（本文件所在目录下的不存在的子目录），
#: 用于证明“文件确实缺失”这一可判定状态。
_ABSENT_PHOTO = str(Path(__file__).resolve().parent / "__w5_absent_dir__" / "missing-photo.png")
TRIGGERS = {
    "MATERIAL_MISSING": (
        {"materials": [{"id": 3, "kind": "template", "filename": "t.docx"}]}, "", "", ""),
    "FIELD_SOURCE_MISSING": (
        {"materials": FULL_MATERIALS,
         "fields": [{"id": 11, "field_key": "body_length", "status": "recorded",
                     "confirmed_value": "172 cm", "source_material_id": None}]}, "", "", ""),
    "FIELD_UNRESOLVED_CRITICAL": (
        {"materials": FULL_MATERIALS,
         "fields": [{"id": 12, "field_key": "subject_name", "status": "unclear",
                     "review_state": "pending", "confirmed_value": "李某某"}]}, "", "", ""),
    "FIELD_REQUIRED_EMPTY": (
        {"materials": FULL_MATERIALS, "fields": []}, "", "", ""),
    "FIELD_CONFLICT": (
        {"materials": FULL_MATERIALS,
         "fields": [{"id": 13, "field_key": "subject_name", "status": "unclear",
                     "review_state": "confirmed", "confirmed_value": "李某某",
                     "conflict": [{"value": "李某某", "material_id": 1},
                                  {"value": "王某某", "material_id": 2}]}]}, "", "", ""),
    "OCR_LOW_CONFIDENCE": (
        {"materials": FULL_MATERIALS,
         "ocr_blocks": [{"id": 21, "material_id": 2, "page_number": 1, "block_index": 0,
                         "text": "颈部压退", "confidence": 0.4, "is_handwritten": False}]},
        "", "", ""),
    "OCR_HANDWRITTEN_UNVERIFIED": (
        {"materials": FULL_MATERIALS,
         "ocr_blocks": [{"id": 22, "material_id": 2, "page_number": 1, "block_index": 0,
                         "text": "尸长172cm", "confidence": 0.9, "is_handwritten": True}]},
        "", "", ""),
    "INJURY_UNRESOLVED": (
        {"materials": FULL_MATERIALS,
         "injuries": [{"id": 31, "seq": 1, "body_part": "前臂", "injury_type": "擦伤",
                       "length": "3", "width": "2", "review_state": "pending"}]}, "", "", ""),
    "INJURY_MISSING_SIZE": (
        {"materials": FULL_MATERIALS,
         "injuries": [{"id": 32, "seq": 2, "body_part": "前臂", "injury_type": "擦伤",
                       "length": "3", "width": "", "review_state": "confirmed"}]}, "", "", ""),
    "NEGATION_UNPRESERVED": (
        {"materials": FULL_MATERIALS,
         "fields": [{"id": 14, "field_key": "general_other", "status": "negative",
                     "normalized_text": "皮下出血", "review_state": "confirmed"}]}, "", "", ""),
    "NOT_RECORDED_MISLABELED": (
        {"materials": FULL_MATERIALS,
         "fields": [{"id": 15, "field_key": "clothing", "status": "not_recorded",
                     "normalized_text": "未见异常", "review_state": "confirmed"}]}, "", "", ""),
    "OPINION_FROM_SOURCE": (
        {"materials": FULL_MATERIALS, "opinion_text": "尸长172cm",
         "ocr_blocks": [{"id": 23, "material_id": 2, "page_number": 1, "block_index": 1,
                         "text": "尸长172cm", "confidence": 0.9, "is_handwritten": False}]},
        "", "", ""),
    "DOCUMENT_UNRESOLVED_PLACEHOLDER": (
        {"materials": FULL_MATERIALS,
         "documents": [{"id": 41, "doc_type": "report", "version_no": 1,
                        "doc_status": "internal_draft", "docx_path": "v1.docx"}]},
        "", "尸长：{{ body_length }}", ""),
    "DOCUMENT_DRAFT_MARKER": (
        {"materials": FULL_MATERIALS,
         "documents": [{"id": 42, "doc_type": "report", "version_no": 2,
                        "doc_status": "pending_issue", "docx_path": "v2.docx"}]},
        "", "【待核对】尸长172 cm。", ""),
    "DOCUMENT_PDF_MISSING": (
        {"materials": FULL_MATERIALS,
         "documents": [{"id": 43, "doc_type": "report", "version_no": 3,
                        "doc_status": "internal_draft", "docx_path": "v3.docx",
                        "pdf_path": ""}]}, "", "", ""),
    "DOCUMENT_TEXT_DIVERGED": (
        {"materials": FULL_MATERIALS,
         "documents": [{"id": 44, "doc_type": "report", "version_no": 4,
                        "doc_status": "internal_draft", "docx_path": "v4.docx"}]},
        "", "尸长172 cm。", "尸长170 cm。"),
    "CORRECTION_UNAPPLIED": (
        {"materials": FULL_MATERIALS,
         "corrections": [{"id": 51, "kind": "recognition", "target_type": "field",
                          "target_id": 11, "before_text": "左", "after_text": "右",
                          "reason": "字迹辨认", "applied": 0}]}, "", "", ""),
    "CORRECTION_SCOPE_UNCLEAR": (
        {"materials": FULL_MATERIALS,
         "corrections": [{"id": 52, "kind": "terminology", "target_type": "field",
                          "target_id": 11, "before_text": "压退", "after_text": "指压褪色",
                          "reason": "术语确认", "scope": "column", "scope_value": "",
                          "applied": 1}]}, "", "", ""),
    "REVIEW_INVALIDATED": (
        {"materials": FULL_MATERIALS,
         "fields": [{"id": 16, "field_key": "body_length", "status": "recorded",
                     "review_state": "pending", "confirmed_value": "172 cm",
                     "source_material_id": 2,
                     "applied_rule_codes": ["body_length_unit"],
                     "rule_note": "保留原数值及测量精度"}]}, "", "", ""),
    "PHOTO_CONTENT_READ": (
        {"materials": FULL_MATERIALS + [
            {"id": 61, "kind": "photo", "filename": "head.png", "position_key": "head"}],
         "ocr_blocks": [{"id": 24, "material_id": 61, "page_number": 1, "block_index": 0,
                         "text": "任何来自照片的文本都不应存在", "confidence": 0.9,
                         "is_handwritten": False}]}, "", "", ""),
    "PHOTO_POSITION_MISSING": (
        {"materials": FULL_MATERIALS + [
            {"id": 62, "kind": "photo", "filename": "unassigned.png", "position_key": ""}]},
        "", "", ""),
    "PHOTO_FILE_MISSING": (
        {"materials": FULL_MATERIALS,
         "photo_slots": [{"position_key": "head", "label": "头部", "photos": [
             {"material_id": 63, "path": _ABSENT_PHOTO, "filename": "w5-missing-photo.png",
              "sort_order": 1}]}]}, "", "", ""),
    "PHOTO_SLOT_EMPTY": (
        {"materials": FULL_MATERIALS, "photo_slots": []},
        "照片栏位：头部\n{{ photo_head }}", "", ""),
}


def _item_for(code: str):
    case_data, template_text, stored_text, snapshot = TRIGGERS[code]
    items = check_case(case_data, template_text=template_text, stored_text=stored_text,
                       rendered_snapshot=snapshot)
    matches = [item for item in items if item.code == code]
    assert matches, "未产出校核项：{0}；实际产出 {1}".format(
        code, sorted({item.code for item in items}))
    return matches[0]


@pytest.mark.parametrize("code", sorted(CHECK_CONTRACT))
def test_checker_code_table_matches_documentation(code):
    """R-6 / §5.7：全部 23 个校核项的 `category` / `severity` 与文档逐字一致。"""

    item = _item_for(code)
    expected_category, expected_severity = CHECK_CONTRACT[code]
    assert (item.category, item.severity) == (expected_category, expected_severity)
    assert item.title and item.detail
    assert item.target_type, "校核项必须带定位信息（target_type）"


def test_checker_code_table_covers_exactly_the_documented_codes():
    """§5.7：契约表恰为 23 个 code（19 旧 + 4 `PHOTO_*`）。"""

    doc_old = [code for code in CHECK_CONTRACT if not code.startswith("PHOTO_")]
    doc_photo = [code for code in CHECK_CONTRACT if code.startswith("PHOTO_")]
    assert len(CHECK_CONTRACT) == 23
    assert len(doc_old) == 19
    assert sorted(doc_photo) == ["PHOTO_CONTENT_READ", "PHOTO_FILE_MISSING",
                                 "PHOTO_POSITION_MISSING", "PHOTO_SLOT_EMPTY"]


def test_n11_photo_content_read_is_blocking_when_photo_material_has_ocr_blocks():
    """N-11 / R-55：照片材料出现 `ocr_blocks` → `PHOTO_CONTENT_READ`（blocking）。"""

    item = _item_for("PHOTO_CONTENT_READ")
    assert item.category == "recognition"
    assert item.severity == "blocking"
    assert item.target_type == "material"
    assert item.target_id == 61
    assert item.source["block_count"] == 1
    assert has_blocking([item]) is True


def test_n11_photo_codes_absent_when_photos_are_clean_and_assigned():
    """N-11 / N-12：照片只有文件与位置元数据时，不得凭空产出照片类校核项。"""

    case_data = {
        "materials": FULL_MATERIALS + [
            {"id": 64, "kind": "photo", "filename": "head.png", "position_key": "head"}],
        "photo_slots": [{"position_key": "head", "label": "头部", "photos": [
            {"material_id": 64, "path": __file__, "filename": "head.png", "sort_order": 1}]}],
    }
    codes = {item.code for item in check_case(case_data, template_text="{{ photo_head }}")}
    assert "PHOTO_CONTENT_READ" not in codes
    assert "PHOTO_POSITION_MISSING" not in codes
    assert "PHOTO_FILE_MISSING" not in codes
    assert "PHOTO_SLOT_EMPTY" not in codes


def test_check_case_deduplicates_by_code_target_type_and_target_id():
    """§5.7 / R-6：相同 `(code, target_type, target_id)` 只保留一条。"""

    case_data = {"materials": [], "fields": [], "documents": [], "corrections": []}
    items = check_case(case_data)
    missing = [item for item in items if item.code == "MATERIAL_MISSING"]
    # commission / record_book / template 三种必需材料都缺，但 target_id 都是 0 → 去重成一条
    assert len(missing) == 1
    keys = [(item.code, item.target_type, item.target_id) for item in items]
    assert len(keys) == len(set(keys))


def test_check_case_returns_items_in_fixed_category_order():
    """§5.7 / R-26：返回顺序固定为 recognition → content → source → format → correction。"""

    order = ["recognition", "content", "source", "format", "correction"]
    case_data, template_text, stored_text, snapshot = TRIGGERS["PHOTO_CONTENT_READ"]
    case_data = dict(case_data)
    case_data["corrections"] = TRIGGERS["CORRECTION_UNAPPLIED"][0]["corrections"]
    case_data["documents"] = [{"id": 45, "doc_type": "report", "version_no": 1,
                               "doc_status": "internal_draft", "docx_path": "v.docx"}]
    items = check_case(case_data, template_text=template_text, stored_text="{{ x }}",
                       rendered_snapshot=snapshot)
    seen = [order.index(item.category) for item in items]
    assert seen == sorted(seen)
    assert {item.category for item in items} >= {"recognition", "content", "format", "correction"}


def test_include_layout_false_skips_format_checks():
    """R-46：`include_layout=False` 时不做排版（format）校核。"""

    case_data = TRIGGERS["DOCUMENT_UNRESOLVED_PLACEHOLDER"][0]
    items = check_case(case_data, include_layout=False, stored_text="{{ body_length }}")
    assert all(item.category != "format" for item in items)


def test_r28_has_blocking_helper_只认_blocking():
    """R-28：`has_blocking` 只对 `severity == "blocking"` 返回 True。"""

    warning = CheckItem(category="content", severity="warning", code="X", title="t", detail="d")
    blocking = CheckItem(category="content", severity="blocking", code="Y", title="t", detail="d")
    assert has_blocking([warning]) is False
    assert has_blocking([warning, blocking]) is True
    assert has_blocking([]) is False


def test_r21_recognition_check_reports_low_confidence_and_unreferenced_handwriting():
    """R-21 / R-45：低置信度与未被引用的手写块都进入待核对清单（分数只用于排序）。"""

    case_data = {
        "materials": FULL_MATERIALS,
        "ocr_blocks": [
            {"id": 71, "material_id": 2, "page_number": 1, "block_index": 0,
             "text": "颈部压退", "confidence": 0.4, "is_handwritten": False},
            {"id": 72, "material_id": 2, "page_number": 1, "block_index": 1,
             "text": "尸长172cm", "confidence": 0.9, "is_handwritten": True},
        ],
    }
    codes = {item.code for item in check_recognition(case_data)}
    assert codes == {"OCR_LOW_CONFIDENCE", "OCR_HANDWRITTEN_UNVERIFIED"}
    low = [item for item in check_recognition(case_data) if item.code == "OCR_LOW_CONFIDENCE"][0]
    assert "只用于排序" in low.detail


def test_r44_field_conflict_is_blocking_and_lists_all_sources():
    """R-44：材料冲突必须是阻断项，且列出全部来源取值（不得静默择一）。"""

    item = _item_for("FIELD_CONFLICT")
    assert item.category == "content" and item.severity == "blocking"
    assert "李某某" in item.detail and "王某某" in item.detail
    assert len(item.source["conflict"]) == 2


def test_n5_not_recorded_mislabeled_is_blocking():
    """N-5 / INV-3：`not_recorded` 文本含“未见异常” → blocking。"""

    item = _item_for("NOT_RECORDED_MISLABELED")
    assert item.severity == "blocking"
    assert "未见异常" in item.detail


def test_inv2_critical_pending_or_unclear_field_is_blocking():
    """INV-2 / R-28 / R-45：关键字段 `unclear` 或 `pending` 构成阻断项。"""

    for status, review in (("unclear", "pending"), ("recorded", "pending")):
        case_data = {
            "materials": FULL_MATERIALS,
            "fields": [{"id": 81, "field_key": "subject_identity", "status": status,
                        "review_state": review, "confirmed_value": "身份证号 000"}],
        }
        codes = {item.code for item in check_content(case_data)}
        assert "FIELD_UNRESOLVED_CRITICAL" in codes, (status, review)


def test_n12_photo_slot_empty_is_info_and_never_says_normal():
    """N-12：模板声明了栏位但没有照片 → `PHOTO_SLOT_EMPTY`（info），提示写占位文字。"""

    item = _item_for("PHOTO_SLOT_EMPTY")
    assert (item.category, item.severity) == ("format", "info")
    assert item.target_type == "photo_slot"
    assert "（未提供照片）" in item.detail
    assert item.source["assigned_count"] == 0


def test_r57_unassigned_photo_produces_warning_pointing_at_material():
    """R-57：未指定目标位置的照片 → `PHOTO_POSITION_MISSING`（warning，指向材料）。"""

    item = _item_for("PHOTO_POSITION_MISSING")
    assert (item.category, item.severity) == ("content", "warning")
    assert item.target_type == "material"
    assert item.target_id == 62
    assert item.source["position_key"] == ""


def test_photo_file_missing_flag_only_for_provably_absent_files():
    """R-56 / N-12：只有能证明文件不存在时才报 `PHOTO_FILE_MISSING`（宁缺勿猜）。"""

    item = _item_for("PHOTO_FILE_MISSING")
    assert (item.category, item.severity) == ("format", "warning")
    assert item.source["exists"] is False

    # 无法判定（相对路径且没有存储根）时不得误报
    vague = {"materials": FULL_MATERIALS,
             "photo_slots": [{"position_key": "head", "photos": [
                 {"material_id": 65, "path": "cases/x/originals/photo/head.png"}]}]}
    codes = {item.code for item in check_case(vague)}
    assert "PHOTO_FILE_MISSING" not in codes


def test_document_text_diverged_only_fires_with_a_generation_snapshot():
    """R-52 / §5.7：`DOCUMENT_TEXT_DIVERGED` 仅在存在生成时快照且文本不同时触发。"""

    case_data = TRIGGERS["DOCUMENT_TEXT_DIVERGED"][0]
    # 没有生成时快照 → 不报（宁缺勿猜，绝不能拿“模板≠产物”当分歧）
    without_snapshot = check_case(case_data, stored_text="尸长170 cm。")
    assert "DOCUMENT_TEXT_DIVERGED" not in {item.code for item in without_snapshot}
    # 有快照且文本一致 → 不报
    identical = check_case(case_data, stored_text="尸长172 cm。", rendered_snapshot="尸长172 cm。")
    assert "DOCUMENT_TEXT_DIVERGED" not in {item.code for item in identical}
    # 有快照且文本不同 → 报（warning）
    diverged = check_case(case_data, stored_text="尸长170 cm。", rendered_snapshot="尸长172 cm。")
    codes = {item.code: item for item in diverged}
    assert codes["DOCUMENT_TEXT_DIVERGED"].severity == "warning"
