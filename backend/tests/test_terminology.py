"""术语整理引擎用例（R-4 / R-23 / R-30～R-37、N-2～N-5）。

全部为纯函数级用例：`terminology.normalize_text` / `normalize_field` /
`normalize_injury` / `normalize_case` / `compile_rules`，外加
`/api/terminology-rules` 的规则 CRUD 与规则级乐观锁。
"""

from __future__ import annotations

import pytest

from backend.app.terminology import (
    NOT_EXAMINED_PREFIX,
    NOT_RECORDED_TEXT,
    compile_rules,
    default_rules,
    normalize_case,
    normalize_field,
    normalize_injury,
    normalize_text,
)


def _rule(code: str, pattern: str, template: str, **kwargs):
    base = {"code": code, "pattern": pattern, "match_type": "literal", "template": template}
    base.update(kwargs)
    return base


# --------------------------------------------------------------------------
# R-30 / R-33 / N-3：数值与单位
# --------------------------------------------------------------------------


def test_r30_body_length_with_unit_expands_without_changing_value():
    """R-30：`尸长172cm` → `尸长172 cm。`，保留原数值与测量精度。"""

    result = normalize_text("尸长172cm", field_key="body_length", column="尸长")
    assert result.normalized_text == "尸长172 cm。"
    assert result.applied_rule_codes == ["body_length_unit"]
    assert result.requires_confirmation is False


def test_r33_body_length_missing_unit_keeps_value_and_asks_confirmation():
    """R-33：`尸长172` 未注明单位 → 保留数值并提示确认，不臆造单位。"""

    result = normalize_text("尸长172", field_key="body_length", column="尸长")
    assert result.normalized_text == "尸长172"
    assert "cm" not in result.normalized_text
    assert result.requires_confirmation is True
    assert result.rule_note  # missing_group_note
    assert [issue["code"] for issue in result.issues] == ["RULE_GROUP_MISSING"]


def test_n3_number_precision_is_never_manufactured():
    """N-3：`3` 不得变成 `3.0`（无认可规则时保留原文）。"""

    result = normalize_text("3", field_key="body_length")
    assert result.normalized_text == "3"
    assert "3.0" not in result.normalized_text
    assert result.requires_confirmation is True


def test_n3_measure_keeps_original_numbers_verbatim():
    """N-3：尺寸数值按原文搬运，`3×2` 不写成 `3.0×2.0`。"""

    result = normalize_text("左前臂擦伤3×2cm", field_key="injuries", column="损伤")
    assert "3 cm × 2 cm" in result.normalized_text
    assert "3.0" not in result.normalized_text


# --------------------------------------------------------------------------
# R-35 / N-5：空白
# --------------------------------------------------------------------------


def test_r35_n5_blank_is_recorded_as_not_recorded_only():
    """R-35 / N-5：空白栏目恰好写“未记录”，且绝不含“未见异常”。"""

    for raw in ("", "/", "—", "无记录"):
        result = normalize_text(raw, field_key="cornea", status="not_recorded")
        assert result.normalized_text == NOT_RECORDED_TEXT
        assert "未见异常" not in result.normalized_text
        assert result.requires_confirmation is False


def test_n5_not_recorded_wins_even_when_source_says_otherwise():
    """N-5 / INV-3：`not_recorded` 一律写“未记录”，规则不得把它改写成正常。"""

    rules = compile_rules([_rule("mislabel", "未见异常", "已见异常", scope_field_key="cornea")])
    result = normalize_text("未见异常", field_key="cornea", status="not_recorded", rules=rules)
    assert result.normalized_text == NOT_RECORDED_TEXT
    assert result.applied_rule_codes == []


def test_r35_blank_clothing_column_from_record_book_is_not_recorded():
    """R-35：记录本 `/`（衣着栏空白）→ “未记录”，不是“未见异常”。"""

    result = normalize_text("/", field_key="clothing", column="衣着", status="not_recorded")
    assert result.normalized_text == "未记录"
    assert "未见异常" not in result.normalized_text


# --------------------------------------------------------------------------
# R-31 / R-32 / R-34 / R-37：展开与多义
# --------------------------------------------------------------------------


def test_r31_injury_measure_expands_without_adding_facts():
    """R-31：`左前臂擦伤3×2cm` → `左前臂见擦伤，范围为3 cm × 2 cm。`，不补颜色深度形态。"""

    result = normalize_text("左前臂擦伤3×2cm", field_key="injuries", column="损伤")
    assert result.normalized_text == "左前臂见擦伤，范围为3 cm × 2 cm。"
    assert result.applied_rule_codes == ["injury_measure"]
    for forbidden in ("颜色", "深度", "形态", "约"):
        assert forbidden not in result.normalized_text


def test_r32_livor_column_expands_without_inferring_death_time():
    """R-32：尸斑栏 `背侧，紫红，指压褪色` 按认可规则展开，不推导死亡时间。"""

    result = normalize_text("背侧，紫红，指压褪色", field_key="livor_mortis", column="尸斑")
    assert result.normalized_text == "尸斑位于背侧，呈紫红色，指压褪色。"
    assert result.applied_rule_codes == ["livor_dorsal_purple"]
    assert "死亡时间" not in result.normalized_text
    assert "死亡时间" not in result.rule_note


def test_r34_ambiguous_shorthand_only_asks_for_confirmation():
    """R-34：`压退` 含义不明 → 保留原文并要求确认，不直接展开为完整尸斑描述。"""

    result = normalize_text("压退", field_key="livor_mortis", column="尸斑")
    assert result.normalized_text == "压退"
    assert result.requires_confirmation is True
    assert "尸斑" not in result.normalized_text
    assert result.rule_note
    assert [issue["code"] for issue in result.issues] == ["RULE_NO_TEMPLATE"]


def test_r37_same_shorthand_outside_its_column_is_not_expanded():
    """R-37：同一简写在其它栏目下不得套用该栏目的规则，保留原文并要求确认。"""

    result = normalize_text("背侧，紫红，指压褪色", field_key="general_other",
                            column="其他一般所见")
    assert result.normalized_text == "背侧，紫红，指压褪色"
    assert result.applied_rule_codes == []
    assert result.requires_confirmation is True
    assert [issue["code"] for issue in result.issues] == ["NO_ACCEPTED_RULE"]


def test_r23_unclear_status_keeps_raw_text_and_asks_confirmation():
    """R-23 / R-34：`unclear` 原文照抄 + 必须人工确认。"""

    result = normalize_text("颈部压退", field_key="livor_mortis", status="unclear")
    assert result.normalized_text == "颈部压退"
    assert result.status == "unclear"
    assert result.requires_confirmation is True
    assert [issue["code"] for issue in result.issues] == ["TEXT_UNCLEAR"]


# --------------------------------------------------------------------------
# 未检 / 否定词（N-4）
# --------------------------------------------------------------------------


def test_not_examined_prefix_is_added_and_raw_text_kept():
    """R-17：`not_examined` 保留原文并前置“未进行该项检查：”。"""

    result = normalize_text("未检", field_key="cornea", status="not_examined")
    assert result.normalized_text == NOT_EXAMINED_PREFIX + "未检"
    assert result.requires_confirmation is False


def test_n4_negative_keeps_negation_words_in_output():
    """N-4：`negative` 输出必须保留原文否定词。"""

    result = normalize_text("未见皮下出血", field_key="general_other", status="negative")
    assert result.normalized_text == "未见皮下出血"
    assert "未见" in result.normalized_text


def test_n4_guard_reverts_when_rule_would_drop_negation():
    """N-4：若规则展开会丢掉否定词，必须退回原文并标记待确认。"""

    rules = compile_rules([_rule("drops_negation", "未见异常", "已见异常",
                                scope_field_key="cornea", priority=1)])
    result = normalize_text("未见异常", field_key="cornea", status="negative", rules=rules)
    assert result.normalized_text == "未见异常"
    assert result.requires_confirmation is True
    assert "NEGATION_LOST" in [issue["code"] for issue in result.issues]


# --------------------------------------------------------------------------
# 规则选择：优先级、首个命中、无命中
# --------------------------------------------------------------------------


def test_rules_are_ordered_by_priority_then_code_and_first_match_wins():
    """§5.6：规则按 `(priority, code)` 升序取第一条命中者。"""

    rules = compile_rules([
        _rule("zzz_late", "同一简写", "后命中", scope_field_key="cornea", priority=90),
        _rule("aaa_early", "同一简写", "先命中", scope_field_key="cornea", priority=10),
    ])
    assert [rule.code for rule in rules] == ["aaa_early", "zzz_late"]
    result = normalize_text("同一简写", field_key="cornea", rules=rules)
    assert result.normalized_text == "先命中"
    assert result.applied_rule_codes == ["aaa_early"]


def test_rules_with_equal_priority_are_ordered_by_code():
    """§5.6：优先级相同时按 `code` 排序（确定性，可重复）。"""

    rules = compile_rules([
        _rule("b_rule", "同优先级", "b", scope_field_key="cornea", priority=5),
        _rule("a_rule", "同优先级", "a", scope_field_key="cornea", priority=5),
    ])
    assert [rule.code for rule in rules] == ["a_rule", "b_rule"]
    assert normalize_text("同优先级", field_key="cornea", rules=rules).normalized_text == "a"


def test_no_rule_hit_keeps_raw_text_and_requires_confirmation():
    """R-23：无认可规则 → 原文 + 要求确认（不得自由发挥）。"""

    result = normalize_text("某种没人见过的简写", field_key="cornea")
    assert result.normalized_text == "某种没人见过的简写"
    assert result.requires_confirmation is True
    assert [issue["code"] for issue in result.issues] == ["NO_ACCEPTED_RULE"]


def test_invalid_regex_rule_is_skipped_without_breaking_normalization():
    """R-4：一条坏规则不得让整批整理失败（非法正则跳过）。"""

    rules = compile_rules([
        {"code": "broken", "pattern": "([unclosed", "match_type": "regex", "template": "x"},
        _rule("healthy", "可用简写", "已展开", scope_field_key="cornea", priority=50),
    ])
    assert {rule.code for rule in rules} == {"broken", "healthy"}
    result = normalize_text("可用简写", field_key="cornea", rules=rules)
    assert result.normalized_text == "已展开"


def test_compile_rules_skips_rows_without_code_or_pattern():
    """R-4：缺 `code` 或 `pattern` 的规则行直接跳过。"""

    rules = compile_rules([{"code": "", "pattern": "x"}, {"code": "y", "pattern": ""}])
    assert rules == []


# --------------------------------------------------------------------------
# 字段 / 损伤 / 整案
# --------------------------------------------------------------------------


def test_r15_normalize_field_prefers_confirmed_value_over_raw_text():
    """R-15：整理取“确认后的值”优先，其次是识别原文。"""

    field = {
        "id": 3, "field_key": "body_length", "raw_text": "尸长172",
        "confirmed_value": "尸长170cm", "status": "recorded",
    }
    result = normalize_field(field, default_rules())
    assert result.normalized_text == "尸长170 cm。"
    assert result.target_type == "field"
    assert result.target_id == 3


def test_r31_normalize_injury_assembles_sentence_from_structured_fields():
    """R-31 / §5.6：无规则命中时按结构化字段拼装规范句式。"""

    injury = {
        "id": 7, "field_key": "injuries", "column": "损伤",
        "raw_text": "左前臂擦伤3×2cm", "side": "左", "body_part": "前臂",
        "injury_type": "擦伤", "length": "3", "width": "2", "size_unit": "cm",
        "status": "recorded",
    }
    result = normalize_injury(injury, [])
    assert result.normalized_text == "左前臂见擦伤，范围为3 cm × 2 cm。"
    assert result.target_type == "injury"
    assert result.target_id == 7
    assert result.applied_rule_codes == []


def test_n2_normalize_injury_never_adds_missing_unit_or_size():
    """N-2 / N-3 / R-33：尺寸单位缺失时不补单位，只保留数值并提示确认。"""

    injury = {
        "id": 8, "field_key": "injuries", "column": "损伤",
        "raw_text": "左前臂擦伤3×2", "side": "左", "body_part": "前臂",
        "injury_type": "擦伤", "length": "3", "width": "2", "size_unit": "",
        "status": "recorded",
    }
    result = normalize_injury(injury, [])
    assert result.normalized_text == "左前臂见擦伤，范围为3 × 2。"
    assert "cm" not in result.normalized_text
    assert result.requires_confirmation is True
    assert "INJURY_UNIT_MISSING" in [issue["code"] for issue in result.issues]


def test_n2_injury_without_type_keeps_raw_text():
    """N-2：部位或类型未记录时保留原文，不拼装、不补事实。"""

    injury = {"id": 9, "field_key": "injuries", "raw_text": "左前臂3×2cm",
              "side": "左", "body_part": "前臂", "injury_type": "",
              "length": "3", "width": "2", "size_unit": "cm", "status": "recorded"}
    result = normalize_injury(injury, [])
    assert result.normalized_text == "左前臂3×2cm"
    assert result.requires_confirmation is True
    assert "INJURY_FIELDS_MISSING" in [issue["code"] for issue in result.issues]


def test_r3_normalize_case_orders_fields_then_injuries_by_seq():
    """R-3：整案整理顺序 = 字段（目录顺序）→ 损伤（按 `seq`）。"""

    fields = [{"id": 1, "field_key": "body_length", "raw_text": "尸长172cm",
               "status": "recorded", "field_group": "general_findings"}]
    injuries = [
        {"id": 2, "seq": 2, "field_key": "injuries", "raw_text": "右小腿挫伤4×3cm",
         "body_part": "小腿", "side": "右", "injury_type": "挫伤",
         "length": "4", "width": "3", "size_unit": "cm", "status": "recorded"},
        {"id": 3, "seq": 1, "field_key": "injuries", "raw_text": "左前臂擦伤3×2cm",
         "body_part": "前臂", "side": "左", "injury_type": "擦伤",
         "length": "3", "width": "2", "size_unit": "cm", "status": "recorded"},
    ]
    results = normalize_case(fields, injuries, compile_rules([]))
    assert [item.target_type for item in results] == ["field", "injury", "injury"]
    assert [item.target_id for item in results] == [1, 3, 2]


# --------------------------------------------------------------------------
# 规则管理 API（R-4 / R-39 的规则版本乐观锁）
# --------------------------------------------------------------------------


def test_terminology_rules_seed_import_and_crud(client, db):
    """R-4：认可规则可由接口维护；PATCH/DELETE 使用规则自己的版本号。"""

    db.import_seed_rules()
    listed = client.get("/api/terminology-rules?active_only=true").json()
    assert listed, "种子规则应已导入"
    codes = {row["code"] for row in listed}
    assert {"body_length_unit", "livor_dorsal_purple", "injury_measure"} <= codes

    created = client.post("/api/terminology-rules", json={
        "code": "w5_test_rule", "shorthand": "<测试>", "pattern": "W5简写",
        "match_type": "literal", "template": "W5 展开。", "scope_field_key": "cornea",
        "priority": 200,
    })
    assert created.status_code == 200, created.text
    rule = created.json()
    assert rule["code"] == "w5_test_rule"

    patched = client.patch("/api/terminology-rules/{0}".format(rule["id"]), json={
        "expected_version": rule["version"], "template": "W5 展开（修订）。",
    })
    assert patched.status_code == 200, patched.text
    assert patched.json()["template"] == "W5 展开（修订）。"

    conflict = client.patch("/api/terminology-rules/{0}".format(rule["id"]), json={
        "expected_version": rule["version"], "template": "过期版本不应写入。",
    })
    assert conflict.status_code == 409, conflict.text

    removed = client.delete("/api/terminology-rules/{0}".format(rule["id"]),
                            params={"expected_version": patched.json()["version"]})
    assert removed.status_code == 200, removed.text
    assert removed.json()["deleted"] is True


def test_named_rule_saved_to_db_is_applied_by_normalize_endpoint(client, db, storage):
    """R-4 / R-23：数据库中的认可规则参与整理，无命中条目要求确认。"""

    from backend.tests.helpers import (
        case_version, prepare_full_case, run_normalize, run_ocr, workflow_extract,
    )

    case = prepare_full_case(client, db, storage)
    case_id = int(case["id"])
    db.import_seed_rules()
    run_ocr(client, case_id)
    # `POST /extract` 恒 409（实现缺陷，见 test_api_pipeline.py 的 xfail 用例），
    # 这里走冻结的 workflow 接口以继续验证"规则命中"这一目标行为。
    workflow_extract(db, storage, case_id)
    result = run_normalize(client, case_id)
    entries = {item["field_key"]: item for item in result["payload"]["results"]}
    assert entries["body_length"]["applied_rule_codes"] == ["body_length_unit"]
    assert entries["body_length"]["requires_confirmation"] is False
    assert case_version(client, case_id) >= 1
