"""字段与损伤提取用例（R-2 / R-3 / R-15 / R-16 / R-17 / R-21 / R-22 / R-36 / R-44、N-2 / N-3 / N-4 / N-7）。

纯函数级用例：`infer_status` / `extract_fields` / `extract_injuries` /
`parse_blocks` / `detect_conflicts`。
"""

from __future__ import annotations

from backend.app.constants import FIELD_INDEX, OCR_LOW_CONFIDENCE
from backend.app.extractor import (
    detect_conflicts,
    extract_fields,
    extract_injuries,
    infer_status,
    parse_blocks,
)


def row(text: str, *, material_id: int = 1, page: int = 1, index: int = 0,
        confidence: float = 0.95, bbox=None, handwritten: bool = False,
        column_hint: str = ""):
    return {
        "id": index + 1,
        "material_id": material_id,
        "page_number": page,
        "block_index": index,
        "text": text,
        "confidence": confidence,
        "bbox": list(bbox or [0.1, 0.1 + 0.05 * index, 0.6, 0.14 + 0.05 * index]),
        "engine": "sidecar",
        "is_handwritten": handwritten,
        "column_hint": column_hint,
    }


# --------------------------------------------------------------------------
# R-17：五档状态（`infer_status` 优先级）
# --------------------------------------------------------------------------


def test_r17_blank_or_placeholder_marks_are_not_recorded():
    """R-17 / N-5：空白与整格标记 → `not_recorded`。"""

    for text in ("", "   ", "/", "—", "-", "无记录"):
        assert infer_status(text) == "not_recorded", text


def test_r17_not_examined_before_negative_and_recorded():
    """R-17：`not_examined` 优先于 `negative`；两者都优先于 `recorded`。"""

    assert infer_status("未检") == "not_examined"
    assert infer_status("未进行该项检查") == "not_examined"
    assert infer_status("未进行，未见异常") == "not_examined"
    assert infer_status("未见异常") == "negative"
    assert infer_status("未发现皮下出血") == "negative"
    assert infer_status("背侧，紫红，指压褪色") == "recorded"


def test_r17_infer_status_never_returns_unclear():
    """R-17 / §5.5：`unclear` 不由 `infer_status` 产生（由低置信度或冲突改写）。"""

    samples = ["", "/", "未检", "未见异常", "尸长172cm", "颈部压退"]
    assert {infer_status(text) for text in samples} <= {
        "recorded", "not_recorded", "not_examined", "negative",
    }


def test_r17_status_set_matches_contract_enumeration():
    """R-17：五档状态取值与 `FIELD_STATUSES` 一致。"""

    from backend.app.constants import FIELD_STATUSES

    assert tuple(FIELD_STATUSES) == (
        "recorded", "not_recorded", "not_examined", "negative", "unclear",
    )


# --------------------------------------------------------------------------
# R-16 / R-36：损伤逐条
# --------------------------------------------------------------------------


def test_r16_one_ocr_line_yields_at_most_one_injury_and_two_lines_stay_two():
    """R-16 / N-4：一行 OCR → 至多一条损伤；两行不得合并。"""

    rows = [
        row("左前臂擦伤3×2cm", index=0),
        row("右小腿挫伤4×3cm", index=1),
    ]
    injuries = extract_injuries(rows)
    assert len(injuries) == 2
    assert [item.seq for item in injuries] == [1, 2]
    assert injuries[0].body_part == "前臂" and injuries[0].side == "左"
    assert injuries[1].body_part == "小腿" and injuries[1].side == "右"
    assert injuries[0].raw_text == "左前臂擦伤3×2cm"
    assert injuries[1].raw_text == "右小腿挫伤4×3cm"


def test_r36_single_line_holding_two_measurements_is_not_split_or_mixed():
    """R-36：同一行的两处尺寸不得被拆成两条，也不得与其它行混搭。"""

    rows = [row("左前臂擦伤3×2cm，右小腿挫伤4×3cm", index=0)]
    injuries = extract_injuries(rows)
    assert len(injuries) == 1
    assert injuries[0].raw_text == "左前臂擦伤3×2cm，右小腿挫伤4×3cm"


def test_r16_negated_or_blank_lines_do_not_become_injuries():
    """R-16 / R-17 / N-2：阴性、未检、空白行不得被记成损伤事实。"""

    rows = [
        row("未见骨折", index=0),
        row("未检", index=1),
        row("/", index=2),
    ]
    assert extract_injuries(rows) == []


def test_n3_n4_injury_keeps_original_numbers_side_and_unit():
    """N-2 / N-3 / N-4：尺寸照抄原文（不补单位、不改数值精度、不改左右侧）。"""

    injuries = extract_injuries([row("左前臂擦伤3×2", index=0)])
    assert len(injuries) == 1
    injury = injuries[0]
    assert (injury.length, injury.width) == ("3", "2")
    assert injury.size_unit == ""
    assert injury.side == "左"
    assert injury.morphology == ""


def test_r3_injury_seq_follows_page_and_block_order():
    """R-3：`seq` 按 `(page_number, block_index)` 递增，保持原有顺序。"""

    rows = [
        row("右小腿挫伤4×3cm", index=3),
        row("左前臂擦伤3×2cm", index=1),
        row("头部挫伤2×2cm", page=1, index=0),
    ]
    injuries = extract_injuries(rows)
    assert [item.seq for item in injuries] == [1, 2, 3]
    assert [item.body_part for item in injuries] == ["头部", "前臂", "小腿"]


def test_r21_low_confidence_injury_line_keeps_a_review_note():
    """R-21 / R-45：低置信度损伤行必须留下“需人工核对”的说明（分数只用于排序）。"""

    injuries = extract_injuries([row("左前臂擦伤3×2cm", confidence=0.4, index=0)])
    assert len(injuries) == 1
    assert injuries[0].confidence == 0.4
    assert "人工核对" in injuries[0].rule_note
    assert str(OCR_LOW_CONFIDENCE) in injuries[0].rule_note


# --------------------------------------------------------------------------
# R-2 / R-22：字段提取
# --------------------------------------------------------------------------


def test_r2_r22_only_fields_with_sources_are_written():
    """R-22 / §5.5：只填有来源的字段，绝不产生“空字段行”。"""

    fields = extract_fields([row("尸长172cm", index=0, column_hint="尸长")])
    assert [field.field_key for field in fields] == ["body_length"]
    assert fields[0].raw_text == "尸长172cm"
    assert fields[0].confirmed_value == ""
    assert fields[0].status == "recorded"


def test_r2_commission_document_fields_extracted_from_printed_blocks():
    """R-2：委托书实际存在的信息都能提取，未出现的字段不写入。"""

    rows = [
        row("委托单位：某市公安局某分局", index=0, column_hint="委托单位"),
        row("委托事项：对李某某进行尸表检验", index=1, column_hint="委托事项"),
        row("委托日期：2026-10-11", index=2, column_hint="委托日期"),
    ]
    keys = [field.field_key for field in extract_fields(rows)]
    assert keys == ["commission_unit", "commission_matter", "commission_date"]
    assert "subject_name" not in keys


def test_n7_name_is_not_written_into_commission_unit_and_dates_stay_separate():
    """N-7：姓名不得填进委托单位栏；委托日期与检验日期分别处理。"""

    rows = [
        row("姓名：李某某", index=0, column_hint="姓名"),
        row("委托单位：某市公安局某分局", index=1, column_hint="委托单位"),
        row("委托日期：2026-10-11", index=2, column_hint="委托日期"),
        row("检验时间：2026-10-12 09:30", index=3, column_hint="检验时间"),
    ]
    by_key = {field.field_key: field for field in extract_fields(rows)}
    assert by_key["subject_name"].raw_text == "姓名：李某某"
    assert by_key["commission_unit"].raw_text == "委托单位：某市公安局某分局"
    assert "姓名" not in by_key["commission_unit"].raw_text
    assert by_key["commission_date"].raw_text != by_key["exam_time"].raw_text
    assert by_key["exam_time"].raw_text == "检验时间：2026-10-12 09:30"


def test_n6_case_summary_keeps_commission_group_not_general_findings():
    """N-6：委托书中的案情描述保留其来源属性（案件与委托组），不作为检验所见。"""

    fields = extract_fields([row("案情简介：李某某于2026年10月10日被发现死亡",
                                 index=0, column_hint="案情简介")])
    assert [field.field_key for field in fields] == ["case_summary"]
    assert fields[0].field_group == "case_commission"
    assert fields[0].field_group != "general_findings"


def test_inv1_field_group_and_label_come_from_the_field_index():
    """INV-1：`field_group` / `field_label` / `display_order` 由字段目录派生。"""

    fields = extract_fields([row("尸长172cm", index=0, column_hint="尸长")])
    definition = FIELD_INDEX["body_length"]
    assert fields[0].field_group == definition.group
    assert fields[0].field_label == definition.label
    assert fields[0].display_order == definition.order


def test_r15_every_field_keeps_full_source_trace():
    """R-15：每个事实字段都保留原文、来源材料、页码、bbox、识别值、规则与复核状态。"""

    rows = [row("尸长172cm", material_id=7, page=2, index=4, confidence=0.88,
                bbox=[0.1, 0.2, 0.5, 0.3], column_hint="尸长")]
    field = extract_fields(rows)[0]
    assert field.raw_text == "尸长172cm"
    assert field.source_material_id == 7
    assert field.source_page == 2
    assert field.source_block_id == 5
    assert len(field.source_bbox) == 4
    assert field.confidence == 0.88
    assert field.review_state == "pending"
    assert field.applied_rule_codes == []
    assert field.conflict == []


def test_r33_unit_is_not_invented_at_extraction_time():
    """R-33 / N-3：提取阶段不补单位，单位只在原文出现时由规则处理。"""

    field = extract_fields([row("尸长172", index=0, column_hint="尸长")])[0]
    assert field.unit == ""


def test_r21_low_confidence_field_becomes_unclear():
    """R-21 / §5.5：来源块置信度低于阈值 → `unclear`，并说明只用于排序。"""

    field = extract_fields([row("尸长172cm", confidence=0.4, index=0,
                                column_hint="尸长")])[0]
    assert field.status == "unclear"
    assert field.confidence == 0.4
    assert "人工" in field.rule_note or "核对" in field.rule_note


# --------------------------------------------------------------------------
# R-44：跨材料冲突
# --------------------------------------------------------------------------


def test_r44_same_field_with_two_different_sources_is_explicit_conflict():
    """R-44：同一字段多来源取值不一致 → `conflict` 非空、`status == "unclear"`。"""

    rows = [
        row("姓名：李某某", material_id=1, index=0, confidence=0.95),
        row("姓名：王某某", material_id=2, index=0, confidence=0.95),
    ]
    fields = extract_fields(rows)
    assert len(fields) == 1
    field = fields[0]
    assert field.field_key == "subject_name"
    assert field.status == "unclear"
    assert len(field.conflict) == 2
    assert {entry["value"] for entry in field.conflict} == {"李某某", "王某某"}
    assert {entry["material_id"] for entry in field.conflict} == {1, 2}

    conflicts = detect_conflicts(fields)
    assert len(conflicts) == 1
    assert conflicts[0]["field_key"] == "subject_name"
    assert sorted(conflicts[0]["values"]) == sorted(["李某某", "王某某"])


def test_r44_same_value_from_two_sources_is_not_a_conflict():
    """R-44：取值一致的两份来源只是重复记载，不算冲突（不得静默择一也不得虚报）。"""

    rows = [
        row("姓名：李某某", material_id=1, index=0),
        row("姓名：李某某", material_id=2, index=0),
    ]
    field = extract_fields(rows)[0]
    assert field.status == "recorded"
    assert field.conflict == []
    assert detect_conflicts([field]) == []
    assert field.source_material_id == 1


def test_r44_conflict_keeps_minimum_confidence_of_all_sources():
    """R-15：字段 `confidence` 取来源块的最小置信度（不得只取第一条）。"""

    rows = [
        row("姓名：李某某", material_id=1, index=0, confidence=0.99),
        row("姓名：李某某", material_id=2, index=0, confidence=0.90),
    ]
    assert extract_fields(rows)[0].confidence == 0.90


# --------------------------------------------------------------------------
# 行内多栏目切分 / 解析
# --------------------------------------------------------------------------


def test_multi_column_single_line_is_split_per_column():
    """R-2 / R-3：一行中出现多个栏目时按栏目切成多个原文片段（不串值）。"""

    fields = extract_fields([row("尸长172cm，体重60kg", index=0)])
    by_key = {field.field_key: field for field in fields}
    assert set(by_key) == {"body_length", "body_weight"}
    assert by_key["body_length"].confirmed_value == ""
    assert "体重" not in by_key["body_length"].raw_text


def test_parse_blocks_normalizes_and_sorts_rows():
    """§5.5：块解析排序固定为 `(material_id, page_number, block_index, block_id)`。"""

    blocks = parse_blocks([
        row("B", material_id=2, index=0),
        row("A", material_id=1, index=1),
        row("", material_id=1, index=0),
    ])
    assert [block["text"] for block in blocks] == ["A", "B"]


def test_empty_column_value_is_not_recorded_not_negative():
    """N-5：栏目有来源但内容为空 → `not_recorded`，绝不写成“未见异常”。"""

    field = extract_fields([row("角膜：", index=0, column_hint="角膜")])[0]
    assert field.field_key == "cornea"
    assert field.status == "not_recorded"
    assert "未见异常" not in field.raw_text
