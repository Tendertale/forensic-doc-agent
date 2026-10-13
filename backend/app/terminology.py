"""简写 → 规范表述的规则引擎（架构 5.6）。

对应需求：R-4（简写规范化）、R-23（多义表达交由用户确认）、R-30～R-37、R-44、
以及四条红线：N-3（不改数值精度）、N-4（不漏否定词/左右侧）、N-5（空白≠正常）、
N-2（不增加原文没有的事实）。

硬性规则（逐条可测）：

* ``not_recorded`` → 规范化文本**恰好**是 ``"未记录"``，且任何路径都不会出现“未见异常”（N-5/R-35）；
* ``not_examined`` → 保留原文并前置 ``"未进行该项检查："``；
* ``negative`` → 保留原文中的否定词（N-4）；
* ``unclear`` → 原文照抄 + ``requires_confirmation=True`` + 写入 ``issues``（R-34）；
* 命中规则但 ``require_groups`` 中有空分组 → **保留数值原文**（不补单位/形态）+
  ``requires_confirmation=True`` + ``rule_note = missing_group_note``（R-33）；
* 未命中任何规则且 ``status == "recorded"`` → 原文 + ``requires_confirmation=True`` +
  ``issues`` 记“无认可规则”（R-23）；
* 只做大小写/空白归一，**绝不改数值精度**：``3`` 不会变成 ``3.0``（N-3）；
* 规则按 ``(priority, code)`` 升序，取**第一条命中**者；
* ``normalize_injury`` 无规则命中时按结构化字段拼装（架构 5.6）。

本模块纯标准库实现，不联网、不调用模型（N-1：不生成鉴定意见）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as dataclass_field, replace
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .constants import NEGATION_WORDS, TERMINOLOGY_SEED

__all__ = [
    "NormalizationResult",
    "CompiledRule",
    "compile_rules",
    "default_rules",
    "normalize_text",
    "normalize_field",
    "normalize_injury",
    "normalize_case",
    "NOT_RECORDED_TEXT",
    "NOT_EXAMINED_PREFIX",
]

#: `not_recorded` 的唯一合法写法（N-5 / INV-3）。
NOT_RECORDED_TEXT = "未记录"

#: `not_examined` 的前置提示语。
NOT_EXAMINED_PREFIX = "未进行该项检查："

#: 绝对不允许出现在 `not_recorded` 结果里的字样（N-5）。
_FORBIDDEN_NORMAL_TEXT = "未见异常"

#: 模板占位符：``{value}``、``{site}``、``{raw}`` …
_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")

#: 需要被“结构化字段拼装”视为损伤范畴的作用域取值。
_INJURY_SCOPES = ("injuries", "injury", "损伤")


# --------------------------------------------------------------------------
# 数据结构（架构 5.6）
# --------------------------------------------------------------------------


@dataclass
class NormalizationResult:
    """一条规范化结果。

    ``target_type`` 取 ``"field"`` 或 ``"injury"``；``target_id`` 为对应记录的 id
    （未落库时可能是 0）。``issues`` 是给人看的疑点列表，每项至少含 ``code`` 与
    ``message``，便于 `checker.py` 与前端直接展示。
    """

    target_type: str
    target_id: int
    field_key: str
    raw_text: str
    normalized_text: str
    status: str
    applied_rule_codes: List[str] = dataclass_field(default_factory=list)
    rule_note: str = ""
    requires_confirmation: bool = False
    issues: List[Dict[str, Any]] = dataclass_field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "target_type": self.target_type,
            "target_id": self.target_id,
            "field_key": self.field_key,
            "raw_text": self.raw_text,
            "normalized_text": self.normalized_text,
            "status": self.status,
            "applied_rule_codes": list(self.applied_rule_codes),
            "rule_note": self.rule_note,
            "requires_confirmation": self.requires_confirmation,
            "issues": [dict(item) for item in self.issues],
        }


@dataclass
class CompiledRule:
    """编译后的术语规则（`terminology_rules` 行的内存形态）。"""

    code: str
    pattern: str
    match_type: str
    template: str
    scope_field_key: str
    scope_column: str
    scope_writer: str
    boundary_note: str
    require_groups: Tuple[str, ...]
    missing_group_note: str
    requires_confirmation: bool
    priority: int
    regex: Optional[re.Pattern[str]] = None


# --------------------------------------------------------------------------
# 编译
# --------------------------------------------------------------------------


def _mapping(value: Any) -> Dict[str, Any]:
    """dataclass / Mapping / 普通对象 → dict。"""

    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "__dataclass_fields__"):
        return {name: getattr(value, name) for name in value.__dataclass_fields__}
    data = getattr(value, "__dict__", None)
    return dict(data) if isinstance(data, dict) else {}


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _group_list(value: Any) -> Tuple[str, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, str):
        parts = [item.strip() for item in re.split(r"[,，;；\s]+", value) if item.strip()]
        return tuple(parts)
    if isinstance(value, Iterable):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def compile_rules(rules: Sequence[Mapping[str, Any]]) -> List[CompiledRule]:
    """把规则字典编译为 :class:`CompiledRule`，并按 ``(priority, code)`` 升序排列。

    * ``literal`` 规则编译为 ``re.escape`` 后的正则（语义是“原文包含该简写”）；
    * ``regex`` 规则编译失败（正则非法）时不抛异常，只把 ``regex`` 置为 ``None``，
      该条规则在匹配阶段被跳过——一条坏规则不应让整批整理失败；
    * 缺少 ``code`` 或 ``pattern`` 的行直接跳过。
    """

    compiled: List[CompiledRule] = []
    for raw in rules or ():  # type: ignore[arg-type]
        data = _mapping(raw)
        code = _text(data.get("code")).strip()
        pattern = _text(data.get("pattern"))
        if not code or not pattern:
            continue
        match_type = (_text(data.get("match_type")) or "literal").strip().lower()
        regex: Optional[re.Pattern[str]] = None
        if match_type == "regex":
            try:
                regex = re.compile(pattern)
            except re.error:
                regex = None
        elif match_type == "literal":
            regex = re.compile(re.escape(pattern))
        else:
            # 未知匹配方式：同样按不可用处理，不猜语义。
            regex = None
        compiled.append(
            CompiledRule(
                code=code,
                pattern=pattern,
                match_type=match_type,
                template=_text(data.get("template")),
                scope_field_key=_text(data.get("scope_field_key")).strip(),
                scope_column=_text(data.get("scope_column")).strip(),
                scope_writer=_text(data.get("scope_writer")).strip(),
                boundary_note=_text(data.get("boundary_note")).strip(),
                require_groups=_group_list(
                    data.get("require_groups")
                    if data.get("require_groups") not in (None, "")
                    else data.get("require_groups_json")
                ),
                missing_group_note=_text(data.get("missing_group_note")).strip(),
                requires_confirmation=bool(data.get("requires_confirmation")),
                priority=_int(data.get("priority"), 100),
                regex=regex,
            )
        )
    compiled.sort(key=lambda item: (item.priority, item.code))
    return compiled


_DEFAULT_RULES: Optional[List[CompiledRule]] = None


def default_rules() -> List[CompiledRule]:
    """内置种子规则（`constants.TERMINOLOGY_SEED`）编译结果，进程内只编译一次。

    ``TERMINOLOGY_SEED`` 是“可离线运行的认可规则”；数据库中管理员可维护的规则
    由调用方通过 ``rules`` 参数传入，两者不混用，避免隐式改变整理结果。
    """

    global _DEFAULT_RULES
    if _DEFAULT_RULES is None:
        _DEFAULT_RULES = compile_rules(TERMINOLOGY_SEED)
    return _DEFAULT_RULES


# --------------------------------------------------------------------------
# 匹配
# --------------------------------------------------------------------------


def _first_text(data: Mapping[str, Any], names: Sequence[str]) -> str:
    for name in names:
        value = _text(data.get(name)).strip()
        if value:
            return value
    return ""


def _in_scope(
    rule: CompiledRule,
    field_key: str,
    column: str,
    writer: str,
    target_type: str,
) -> Tuple[bool, bool]:
    """返回 ``(是否适用, 作用域是否无法判定)``。

    作用域信息缺失（调用方既没给字段也没给栏目）时不拒用规则，而是要求人工确认：
    宁可多一次确认，也不因为“猜栏目”而对多义简写做错误展开（R-37）。
    """

    uncertain = False
    scope_key = rule.scope_field_key
    if scope_key:
        if scope_key in _INJURY_SCOPES:
            # 损伤域规则：目标类型是 injury，或调用方直接以 "injuries" 作为 field_key。
            if target_type != "injury" and field_key not in _INJURY_SCOPES:
                return False, False
        elif field_key:
            if scope_key != field_key:
                return False, False
        else:
            uncertain = True

    if rule.scope_column:
        if column:
            if rule.scope_column != column:
                return False, False
        else:
            uncertain = True

    if rule.scope_writer and writer and rule.scope_writer != writer:
        return False, False
    return True, uncertain


def _overlap(produced: str, raw: str) -> int:
    """返回 ``produced`` 末尾与 ``raw`` 开头的最长重合长度。"""

    limit = min(len(produced), len(raw))
    for size in range(limit, 0, -1):
        if produced.endswith(raw[:size]):
            return size
    return 0


def _render_template(template: str, groups: Mapping[str, str], raw: str) -> Tuple[str, List[str]]:
    """渲染规则模板；返回 ``(文本, 未解析的占位符名)``。

    * ``{分组名}`` 用正则分组值替换；
    * ``{raw}`` 表示“命中的原文片段”。若模板在 ``{raw}`` 之前已经写出了该片段开头的文字
      （例如种子规则 `rigor_mortis_full` 的模板 ``尸僵{raw}`` 遇到原文 ``尸僵全身大关节``），
      只补写**尚未写出**的剩余部分，避免出现“尸僵尸僵全身大关节”这类重复（R-52）；
    * 取值缺失的占位符原样保留并登记为 ``unresolved``，由调用方退回原文并要求人工确认。
    """

    parts: List[str] = []
    unresolved: List[str] = []
    position = 0
    for match in _PLACEHOLDER.finditer(template):
        parts.append(template[position:match.start()])
        position = match.end()
        name = match.group(1)
        if name == "raw":
            produced = "".join(parts)
            skip = _overlap(produced, raw)
            parts.append(raw[skip:] if skip < len(raw) else raw)
            continue
        value = groups.get(name, "")
        if value == "":
            unresolved.append(name)
            parts.append(match.group(0))
        else:
            parts.append(value)
    parts.append(template[position:])
    return "".join(parts), unresolved


def _group_present(groups: Mapping[str, str], name: str, raw: str) -> bool:
    if name == "raw":
        return bool(raw)
    return bool(groups.get(name, ""))


def _issue(code: str, message: str, **extra: Any) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"code": code, "message": message}
    payload.update(extra)
    return payload


# --------------------------------------------------------------------------
# 规范化
# --------------------------------------------------------------------------


def _normalize(
    raw_text: str,
    *,
    field_key: str,
    column: str,
    writer: str,
    status: str,
    rules: Optional[Sequence[CompiledRule]],
    target_type: str,
    target_id: int,
) -> NormalizationResult:
    raw = _text(raw_text).strip()
    active_status = status if status else "recorded"

    def build(normalized: str, **kwargs: Any) -> NormalizationResult:
        payload: Dict[str, Any] = {
            "target_type": target_type,
            "target_id": int(target_id or 0),
            "field_key": field_key,
            "raw_text": raw,
            "normalized_text": normalized,
            "status": active_status,
            "applied_rule_codes": [],
            "rule_note": "",
            "requires_confirmation": False,
            "issues": [],
        }
        payload.update(kwargs)
        return NormalizationResult(**payload)

    # 1）未记录：唯一合法写法是“未记录”，且绝不含“未见异常”（N-5/R-35/INV-3）。
    if active_status == "not_recorded":
        normalized = NOT_RECORDED_TEXT
        if _FORBIDDEN_NORMAL_TEXT in normalized:  # pragma: no cover - 防御性断言
            normalized = NOT_RECORDED_TEXT
        return build(normalized, requires_confirmation=False)

    # 2）明确未检：保留原文并前置说明。
    if active_status == "not_examined":
        normalized = f"{NOT_EXAMINED_PREFIX}{raw}" if raw else NOT_EXAMINED_PREFIX.rstrip("：")
        return build(normalized, requires_confirmation=False)

    # 3）待辨认或有冲突：原文照抄，必须人工确认（R-34/R-44）。
    if active_status == "unclear":
        return build(
            raw,
            requires_confirmation=True,
            issues=[
                _issue(
                    "TEXT_UNCLEAR",
                    "该栏目的字迹或来源存在疑点（含材料间冲突），须由专业人员核对原件后再使用（R-34/R-44）",
                )
            ],
        )

    # 4）其余（recorded / negative）：按规则整理。
    candidates = list(rules) if rules is not None else default_rules()
    candidates = sorted(candidates, key=lambda item: (item.priority, item.code))

    matched: Optional[CompiledRule] = None
    groups: Dict[str, str] = {}
    scope_uncertain = False
    for rule in candidates:
        if rule.regex is None:
            continue
        applicable, uncertain = _in_scope(rule, field_key, column, writer, target_type)
        if not applicable:
            continue
        match = rule.regex.search(raw)
        if match is None:
            continue
        matched = rule
        groups = {name: (value or "") for name, value in match.groupdict().items()}
        scope_uncertain = uncertain
        break

    if matched is None:
        if active_status == "recorded":
            return build(
                raw,
                requires_confirmation=True,
                issues=[
                    _issue(
                        "NO_ACCEPTED_RULE",
                        "无认可规则：该简写没有可依据的展开规则，已保留原文，需专业人员确认（R-23）",
                    )
                ],
            )
        # negative：保留原文即可，否定词原样带出（N-4）。
        return build(raw, requires_confirmation=False)

    applied = [matched.code]
    requires_confirmation = matched.requires_confirmation
    rule_note = ""
    issues: List[Dict[str, Any]] = []

    if not matched.template:
        # 例：“压退”只提示确认，不做展开（R-34）。
        normalized = raw
        requires_confirmation = True
        rule_note = matched.boundary_note or "该简写含义不唯一，需确认指代与完整含义后再展开（R-34）"
        issues.append(
            _issue("RULE_NO_TEMPLATE", rule_note, rule=matched.code)
        )
    else:
        missing = [name for name in matched.require_groups if not _group_present(groups, name, raw)]
        if missing:
            # 空分组：保留数值原文，不补单位/形态（R-33/N-2/N-3）。
            normalized = raw
            requires_confirmation = True
            rule_note = matched.missing_group_note or (
                "以下分组在原文中缺失，已保留原文，需确认后再展开：" + "、".join(missing)
            )
            issues.append(
                _issue("RULE_GROUP_MISSING", rule_note, rule=matched.code, missing_groups=missing)
            )
        else:
            rendered, unresolved = _render_template(matched.template, groups, raw)
            if unresolved:
                normalized = raw
                requires_confirmation = True
                rule_note = "规则模板中的以下取值在原文中缺失，已保留原文，需人工确认：" + "、".join(unresolved)
                issues.append(
                    _issue(
                        "RULE_PLACEHOLDER_MISSING",
                        rule_note,
                        rule=matched.code,
                        placeholders=unresolved,
                    )
                )
            elif rendered.strip():
                normalized = rendered.strip()
            else:  # pragma: no cover - 空模板已在上面处理
                normalized = raw
                requires_confirmation = True

    # N-4 守卫：规范化不得丢否定词/限定词；丢了就退回原文。
    lost = [word for word in NEGATION_WORDS if word and word in raw and word not in normalized]
    if lost:
        normalized = raw
        requires_confirmation = True
        note = "规范化结果会丢失原文的否定/限定词（" + "、".join(lost) + "），已保留原文（N-4）"
        rule_note = f"{rule_note}；{note}" if rule_note else note
        issues.append(_issue("NEGATION_LOST", note, rule=matched.code, words=lost))

    if scope_uncertain:
        requires_confirmation = True
        note = "未提供栏目/字段上下文，规则作用域无法判定，需人工确认适用范围（R-37）"
        rule_note = f"{rule_note}；{note}" if rule_note else note
        issues.append(_issue("RULE_SCOPE_UNKNOWN", note, rule=matched.code))

    return build(
        normalized,
        applied_rule_codes=applied,
        rule_note=rule_note,
        requires_confirmation=requires_confirmation,
        issues=issues,
    )


def normalize_text(
    raw_text: str,
    *,
    field_key: str = "",
    column: str = "",
    writer: str = "",
    status: str = "recorded",
    rules: Sequence[CompiledRule] | None = None,
) -> NormalizationResult:
    """把一条原文整理为规范表述（架构 5.6）。

    ``rules`` 为空时使用内置种子规则 `constants.TERMINOLOGY_SEED`（可离线运行）；
    传入数据库规则时按 ``(priority, code)`` 升序取第一条命中者。
    """

    return _normalize(
        raw_text,
        field_key=field_key,
        column=column,
        writer=writer,
        status=status,
        rules=rules,
        target_type="field",
        target_id=0,
    )


def normalize_field(
    field: Mapping[str, Any],
    rules: Sequence[CompiledRule],
) -> NormalizationResult:
    """整理一个事实字段（`case_fields` 行）。

    取值优先级：``confirmed_value``（人工确认后的事实）→ ``raw_text``（识别原文）。
    栏目名按 ``column_hint`` → ``field_label`` 的顺序取，保证“同一简写在不同栏目
    可以有不同含义”（R-37）。
    """

    data = _mapping(field)
    raw = _first_text(data, ("confirmed_value", "raw_text", "value"))
    field_key = _text(data.get("field_key")).strip()
    column = _first_text(data, ("column_hint", "field_label", "column", "label"))
    status = _text(data.get("status")).strip() or "recorded"
    result = _normalize(
        raw,
        field_key=field_key,
        column=column,
        writer=_text(data.get("writer")).strip(),
        status=status,
        rules=rules,
        target_type="field",
        target_id=_int(data.get("id"), 0),
    )
    return result


def _assemble_injury(data: Mapping[str, Any], raw: str) -> Tuple[str, List[Dict[str, Any]], bool]:
    """按结构化字段拼装损伤表述；返回 ``(文本, issues, 是否需确认)``。

    句式见架构 5.6：``f"{side}{body_part}见{injury_type}" + （",共N处"）+
    （"，范围为L 单位 × W 单位。"）``；``side``/``body_part``/``injury_type``
    缺失时用原 ``raw_text`` 兜底。**尺寸单位缺失时不补单位**（R-33/N-3），
    而是保留数值并提示确认。
    """

    side = _text(data.get("side")).strip()
    body_part = _text(data.get("body_part")).strip()
    injury_type = _text(data.get("injury_type")).strip()
    count = _text(data.get("count")).strip()
    length = _text(data.get("length")).strip()
    width = _text(data.get("width")).strip()
    unit = _text(data.get("size_unit")).strip()

    if not body_part or not injury_type:
        return (
            raw,
            [
                _issue(
                    "INJURY_FIELDS_MISSING",
                    "部位或损伤类型未记录，无法确定规范句式，已保留原文，需专业人员核对（N-2）",
                )
            ],
            True,
        )

    text = f"{side}{body_part}见{injury_type}"
    if count:
        text += f"，共{count}处"
    issues: List[Dict[str, Any]] = []
    needs_confirmation = False
    if length and width:
        if unit:
            text += f"，范围为{length} {unit} × {width} {unit}。"
        else:
            text += f"，范围为{length} × {width}。"
            issues.append(
                _issue("INJURY_UNIT_MISSING", "尺寸未注明单位，已保留数值并提示确认单位（R-33）")
            )
            needs_confirmation = True
    else:
        text += "。"
        if length or width:
            issues.append(
                _issue("INJURY_SIZE_INCOMPLETE", "尺寸只记录了长或宽之一，未补齐另一侧，需人工核对（N-2）")
            )
            needs_confirmation = True
    return text, issues, needs_confirmation


def normalize_injury(
    injury: Mapping[str, Any],
    rules: Sequence[CompiledRule],
) -> NormalizationResult:
    """整理一条损伤（`case_injuries` 行）。

    先走规则引擎（例如 `injury_measure` 展开 ``3×2cm``）；一条都不命中时按架构 5.6
    用结构化字段拼装句式，``side``/``body_part``/``injury_type`` 缺失则退回原文。
    无论哪条路径都**不补数量、深度、形态、颜色**，也不改左右侧（N-2/N-4）。
    """

    data = _mapping(injury)
    raw = _first_text(data, ("raw_text", "confirmed_value", "normalized_text", "value"))
    status = _text(data.get("status")).strip() or "recorded"
    result = _normalize(
        raw,
        field_key=_text(data.get("field_key")).strip() or "injuries",
        column=_first_text(data, ("column_hint", "column")) or "损伤",
        writer=_text(data.get("writer")).strip(),
        status=status,
        rules=rules,
        target_type="injury",
        target_id=_int(data.get("id"), 0),
    )

    if result.applied_rule_codes or status != "recorded":
        return result

    text, issues, needs_confirmation = _assemble_injury(data, raw)
    note = "无认可规则：按结构化字段拼装规范句式（架构 5.6）"
    if not issues:
        note = "按结构化字段拼装规范句式，数值与左右侧均照抄原文（R-31/N-3/N-4）"
    return replace(
        result,
        normalized_text=text,
        requires_confirmation=needs_confirmation,
        rule_note=note,
        issues=list(result.issues) + issues,
    )


def normalize_case(
    fields: Sequence[Mapping[str, Any]],
    injuries: Sequence[Mapping[str, Any]],
    rules: Sequence[CompiledRule],
) -> List[NormalizationResult]:
    """整理整案：先字段（按目录顺序），再损伤（按 ``seq``）。"""

    results: List[NormalizationResult] = []
    for field in fields or ():
        results.append(normalize_field(field, rules))
    ordered = sorted(list(injuries or ()), key=lambda item: _int(_mapping(item).get("seq"), 0))
    for injury in ordered:
        results.append(normalize_injury(injury, rules))
    return results
