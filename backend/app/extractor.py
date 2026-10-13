"""字段与损伤提取、状态推断与跨材料冲突（架构 5.5）。

对应需求：R-2（委托书信息）、R-3（记录本内容保持顺序与对应关系）、R-14～R-17、
R-22（每个字段必须关联来源）、R-36（多处损伤分别整理）、R-44（冲突显式处理）、
N-2（不增加原文没有的事实）、N-3（不改数值精度）、N-5（空白≠正常）。

四条不可违反的行为：

1. **只填有来源的字段**：没有在 OCR 文本里找到来源的字段**不写入记录**，绝不产生
   “空字段行”去冒充“已记录”；缺失由 `checker.py` 报告（R-22 / 架构 5.5）。
2. **一行 OCR → 至多一条损伤**：`extract_injuries` 逐行处理，绝不把多行合并成一条，
   也不把一条拆成多条（R-16 / INV-4 / R-36）。
3. **不改变原文**：数值一律按原文截取的**字符串**搬运（`3` 不会变成 `3.0`），
   不补数量、深度、形态、颜色，也不改左右侧与否定词（N-2/N-3/N-4）。
4. **同字段多来源不一致 → 显式冲突**：全部来源按 `seq` 保留进 `conflict`，并把
   `status` 置为 `unclear`，交由专业人员确认，不做“静默择一”（R-44）。

本模块只依赖标准库与 `constants.py`，不含任何联网或模型调用（N-11：部位照片不会
出现在来源集合中，`kind` 过滤由 `workflow.run_ocr` 与 `database` 保证）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as dataclass_field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .constants import (
    FIELD_DEFINITIONS,
    FIELD_INDEX,
    FieldDefinition,
    OCR_LOW_CONFIDENCE,
    STATUS_KEYWORDS,
)

__all__ = [
    "ExtractedField",
    "ExtractedInjury",
    "infer_status",
    "parse_blocks",
    "extract_fields",
    "extract_injuries",
    "detect_conflicts",
    "FIELD_ALIASES",
    "INJURY_TYPE_WORDS",
]

# --------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------

#: 视为“未记录”的整格标记（架构 5.5 规则 1）。
NOT_RECORDED_MARKS: Tuple[str, ...] = ("/", "—", "-", "－", "／", "无记录", "未记录", "空白")

#: 全角/破折号归一：只改写法，不改数值精度。
_MARK_TRANSLATION = str.maketrans({"／": "/", "－": "-", "—": "-", "–": "-"})

#: 提取时会自动剥离的“标签/取值”分隔符。
_SEPARATORS = "：:：=＝ 　\t"

#: 行首编号（“1.”、“2、”、“（3）”、“三、”等），提取前剥离，避免混进部位。
_LEADING_NUMBER = re.compile(r"^\s*(?:[（(]?\s*\d+\s*[）)]?\s*[、.．,，)]|[一二三四五六七八九十]+\s*[、.．)])\s*")

#: 尺寸：“3×2cm”“3 x 2 CM”“3*2 厘米”。
_MEASURE = re.compile(
    r"(?P<length>\d+(?:\.\d+)?)\s*[×xX*]\s*(?P<width>\d+(?:\.\d+)?)\s*(?P<unit>cm|CM|厘米|mm|MM|毫米)?"
)

#: 数量：“共3处”“2条”“1个”。
_COUNT = re.compile(r"(?:共\s*)?(?P<count>\d+)\s*(?:处|条|个|块)")

#: 损伤类型词：按长度倒序匹配，避免“挫裂创”被“创”抢先。
INJURY_TYPE_WORDS: Tuple[str, ...] = (
    "挫裂创", "表皮剥脱", "挫裂伤", "挫擦伤", "擦伤", "挫伤", "裂创", "创口",
    "淤血", "瘀斑", "瘀血", "划伤", "咬痕", "压痕", "骨折", "青紫", "肿胀",
    "出血", "瘢痕", "烧伤", "冻伤", "抓痕", "齿痕", "针孔", "创", "痕",
)

#: 字段别名表（在 `FIELD_DEFINITIONS.label` 之外补充记录本常见写法）。
#: 只用于**定位**字段在原文中的位置，取值一律照抄原文（N-2）。
FIELD_ALIASES: Dict[str, Tuple[str, ...]] = {
    "case_no": ("案件编号", "案件号", "编号"),
    "commission_unit": ("委托单位", "委托机关", "送检单位"),
    "commission_matter": ("委托事项", "委托内容", "鉴定事项", "检验事项"),
    "commission_date": ("委托日期", "委托时间", "受理日期", "委托书日期"),
    "case_summary": ("案情简介", "简要案情", "案情摘要", "案情"),
    "material_list": ("材料清单", "送检材料", "送检材料清单"),
    "subject_name": ("姓名", "被检验人", "被鉴定人", "死者姓名"),
    "subject_gender": ("性别",),
    "subject_age": ("年龄",),
    "subject_identity": ("身份证号", "身份证件号", "证件号", "身份信息"),
    "exam_time": ("检验时间", "检查时间", "检验日期"),
    "exam_place": ("检验地点", "检查地点", "检验场所"),
    "examiners": ("检验人员", "检验人", "检查人", "鉴定人"),
    "exam_method": ("检验方法", "检查方法"),
    "body_length": ("尸长",),
    "body_weight": ("体重",),
    "clothing": ("衣着", "着装", "衣服"),
    "livor_mortis": ("尸斑",),
    "rigor_mortis": ("尸僵",),
    "body_temperature": ("尸温", "肛温"),
    "cornea": ("角膜",),
    "general_other": ("其他一般所见", "其他所见"),
}

#: 不在 OCR 中提取的字段组：文书配置来自案件配置而不是扫描件，从原文“提取”会等于造假。
_NON_EXTRACTABLE_GROUPS: Tuple[str, ...] = ("document_config",)


# --------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------


@dataclass
class ExtractedField:
    """一个事实字段的提取结果（列同 `case_fields`，不含 `id` / `case_id`）。

    ``raw_text`` 是**原文片段**（含栏目名，便于术语规则做锚定匹配）；
    ``confirmed_value`` 留给人工核对，提取阶段一律为空，避免把识别结果冒充“已确认”。
    """

    field_key: str
    raw_text: str = ""
    confirmed_value: str = ""
    normalized_text: str = ""
    unit: str = ""
    status: str = "recorded"
    review_state: str = "pending"
    confidence: Optional[float] = None
    source_material_id: Optional[int] = None
    source_page: int = 0
    source_bbox: List[float] = dataclass_field(default_factory=list)
    source_block_id: Optional[int] = None
    applied_rule_codes: List[str] = dataclass_field(default_factory=list)
    rule_note: str = ""
    conflict: List[Dict[str, Any]] = dataclass_field(default_factory=list)
    # 便于 terminology 选择栏目的辅助信息（数据库只取已知列，忽略多余键）。
    field_group: str = ""
    field_label: str = ""
    display_order: int = 0
    column_hint: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "field_key": self.field_key,
            "field_group": self.field_group,
            "field_label": self.field_label,
            "display_order": self.display_order,
            "raw_text": self.raw_text,
            "confirmed_value": self.confirmed_value,
            "normalized_text": self.normalized_text,
            "unit": self.unit,
            "status": self.status,
            "review_state": self.review_state,
            "confidence": self.confidence,
            "source_material_id": self.source_material_id,
            "source_page": self.source_page,
            "source_bbox": list(self.source_bbox),
            "source_block_id": self.source_block_id,
            "applied_rule_codes": list(self.applied_rule_codes),
            "rule_note": self.rule_note,
            "conflict": [dict(item) for item in self.conflict],
            "column_hint": self.column_hint,
        }


@dataclass
class ExtractedInjury:
    """一条损伤（列同 `case_injuries`，不含 `id` / `case_id`）。

    **一条 = 一行 OCR**（R-16 / INV-4）；``seq`` 按 `(page_number, block_index)` 递增。
    """

    seq: int = 0
    body_part: str = ""
    side: str = ""
    injury_type: str = ""
    count: str = ""
    length: str = ""
    width: str = ""
    size_unit: str = ""
    morphology: str = ""
    other_traits: str = ""
    raw_text: str = ""
    normalized_text: str = ""
    status: str = "recorded"
    review_state: str = "pending"
    confidence: Optional[float] = None
    source_material_id: Optional[int] = None
    source_page: int = 0
    source_bbox: List[float] = dataclass_field(default_factory=list)
    source_block_id: Optional[int] = None
    applied_rule_codes: List[str] = dataclass_field(default_factory=list)
    rule_note: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "seq": self.seq,
            "body_part": self.body_part,
            "side": self.side,
            "injury_type": self.injury_type,
            "count": self.count,
            "length": self.length,
            "width": self.width,
            "size_unit": self.size_unit,
            "morphology": self.morphology,
            "other_traits": self.other_traits,
            "raw_text": self.raw_text,
            "normalized_text": self.normalized_text,
            "status": self.status,
            "review_state": self.review_state,
            "confidence": self.confidence,
            "source_material_id": self.source_material_id,
            "source_page": self.source_page,
            "source_bbox": list(self.source_bbox),
            "source_block_id": self.source_block_id,
            "applied_rule_codes": list(self.applied_rule_codes),
            "rule_note": self.rule_note,
        }


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------


def _mapping(value: Any) -> Dict[str, Any]:
    """dataclass / Mapping / 普通对象 → dict（与 `database._as_mapping` 同义）。"""

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


def _clean(value: Any) -> str:
    """统一空白与全角符号写法；**不动数值本身**。"""

    text = _text(value).replace("\r\n", "\n").replace("\r", "\n")
    text = text.translate(_MARK_TRANSLATION)
    text = re.sub(r"[ \t\u3000]+", " ", text)
    return text.strip()


def _compact(value: Any) -> str:
    """去掉所有空白的版本，仅用于与整格标记比较。"""

    return re.sub(r"\s+", "", _clean(value))


def _int_or_none(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _bbox(value: Any) -> List[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return []
    numbers: List[float] = []
    for item in value:
        try:
            numbers.append(float(item))
        except (TypeError, ValueError):
            return []
    return numbers


def _hits(text: str, words: Iterable[str]) -> bool:
    return any(word and word in text for word in words)


def _infer_status_compact(compact: str) -> str:
    """`infer_status` 的紧凑版实现（输入已去除空白）。"""

    if not compact or compact in NOT_RECORDED_MARKS:
        return "not_recorded"
    if _hits(compact, STATUS_KEYWORDS["not_recorded"]):
        return "not_recorded"
    if _hits(compact, STATUS_KEYWORDS["not_examined"]):
        return "not_examined"
    if _hits(compact, STATUS_KEYWORDS["negative"]):
        return "negative"
    return "recorded"


# --------------------------------------------------------------------------
# 状态推断
# --------------------------------------------------------------------------


def infer_status(text: str) -> str:
    """按优先级推断信息状态（架构 5.5 规则 1—4）。

    优先级从高到低：

    1. 空串，或整格只有 ``/`` ``—`` ``-`` ``无记录``（以及 ``空白``/``未记录``）
       → ``not_recorded``；
    2. 命中 ``STATUS_KEYWORDS["not_examined"]`` → ``not_examined``；
    3. 命中 ``STATUS_KEYWORDS["negative"]`` → ``negative``；
    4. 其余非空文本 → ``recorded``。

    第 5 档 ``unclear`` **不由本函数产生**：它表示“字迹不清/材料互相冲突”，
    由低置信度（< ``OCR_LOW_CONFIDENCE``）或同字段多来源取值不一致触发，
    见 :func:`extract_fields` 与 `workflow.run_extraction`。
    """

    return _infer_status_compact(_compact(text))


# --------------------------------------------------------------------------
# OCR 行解析
# --------------------------------------------------------------------------


def parse_blocks(rows: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """把 `ocr_blocks` 行归一化为提取用的块列表。

    输出块字段：``block_id``、``material_id``、``page_number``、``block_index``、
    ``text``、``confidence``、``bbox``、``engine``、``is_handwritten``、``column_hint``。

    * 空文本块被丢弃（数据库本来也不会写入）；
    * 排序固定为 ``(material_id, page_number, block_index, block_id)``，
      从而“保留原有顺序和对应关系”（R-3），并让 ``seq`` 稳定可复现。
    """

    blocks: List[Dict[str, Any]] = []
    for index, row in enumerate(rows or ()):  # type: ignore[arg-type]
        data = _mapping(row)
        text = _clean(data.get("text"))
        if not text:
            continue
        blocks.append(
            {
                "block_id": _int_or_none(data.get("id") if data.get("id") is not None
                                         else data.get("block_id")),
                "material_id": _int_or_none(data.get("material_id")),
                "page_number": _int_or_none(data.get("page_number")) or 1,
                "block_index": _int_or_none(data.get("block_index"))
                if data.get("block_index") is not None
                else index,
                "text": text,
                "confidence": _float(data.get("confidence"), 0.0),
                "bbox": _bbox(data.get("bbox")),
                "engine": _text(data.get("engine")),
                "is_handwritten": bool(data.get("is_handwritten")),
                "column_hint": _clean(data.get("column_hint")),
            }
        )
    blocks.sort(
        key=lambda item: (
            item["material_id"] or 0,
            item["page_number"],
            item["block_index"],
            item["block_id"] or 0,
        )
    )
    return blocks


# --------------------------------------------------------------------------
# 字段提取
# --------------------------------------------------------------------------


def _definitions(definitions: Optional[Sequence[FieldDefinition]]) -> Tuple[FieldDefinition, ...]:
    if definitions is None:
        return tuple(FIELD_DEFINITIONS)
    return tuple(definitions)


def _aliases(definition: FieldDefinition) -> Tuple[str, ...]:
    """标签别名：目录 label 优先，随后是补充别名（长别名优先，避免“编号”压过“案件编号”）。"""

    names: List[str] = [definition.label] if definition.label else []
    for alias in FIELD_ALIASES.get(definition.key, ()):
        if alias not in names:
            names.append(alias)
    names.sort(key=len, reverse=True)
    return tuple(names)


def _find_mentions(
    text: str, definition: FieldDefinition
) -> Optional[Tuple[int, int, str]]:
    """返回 ``(标签起点, 标签长度, 标签)``；同一最左位置取最长别名。"""

    best: Optional[Tuple[int, int, str]] = None
    for alias in _aliases(definition):
        index = text.find(alias)
        if index < 0:
            continue
        if best is None or index < best[0] or (index == best[0] and len(alias) > best[1]):
            best = (index, len(alias), alias)
    return best


def _by_column_hint(
    hint: str, definitions: Sequence[FieldDefinition]
) -> Optional[FieldDefinition]:
    """把 ``column_hint``（如“尸长”“姓名”）解析成字段定义；解析不出时返回 ``None``。"""

    wanted = _strip_service(hint)
    if not wanted:
        return None
    for definition in definitions:
        if definition.label and wanted == definition.label:
            return definition
    for definition in definitions:
        names = list(FIELD_ALIASES.get(definition.key, ()))
        if definition.label:
            names.append(definition.label)
        for alias in names:
            if alias and (wanted == alias or alias in wanted):
                return definition
    return None


def _strip_service(value: str) -> str:
    """去掉取值前后的分隔符与标点（只改分隔符，不动数值与文字）。"""

    return _clean(value).strip(_SEPARATORS + "，,。；;、")


def _low_confidence(confidence: Optional[float]) -> bool:
    return confidence is not None and confidence < OCR_LOW_CONFIDENCE


def extract_fields(
    rows: Sequence[Mapping[str, Any]],
    case_context: Mapping[str, Any] | None = None,
    definitions: Sequence[FieldDefinition] = FIELD_DEFINITIONS,
) -> List[ExtractedField]:
    """从 OCR 块中提取事实字段（架构 5.5）。

    * 只输出**在原文里找到栏目来源**的字段；没有来源的字段不写入（不产生空字段行）；
    * 一行里出现多个栏目（如“尸长172cm，体重60kg”）时按栏目切成多个原文片段；
    * 同一字段命中多个来源：取值一致 → 取最早来源、``confidence`` 取**最小**置信度；
      取值不一致 → 全部来源写入 ``conflict`` 且 ``status="unclear"``（R-44）；
    * 最小置信度低于 ``OCR_LOW_CONFIDENCE`` 的 ``recorded`` 字段 → ``unclear``，
      并在 ``rule_note`` 说明“识别分数偏低，仅用于安排核对优先级”（R-21/R-45）。

    ``case_context`` 仅作为保留参数（例如后续补充案件上下文），当前不影响提取结果，
    提取永远只依赖原文，避免用背景知识补事实（N-2）。
    """

    _ = case_context  # 显式忽略：不允许用上下文补原文没有的事实
    wanted = [
        item for item in _definitions(definitions)
        if item.group not in _NON_EXTRACTABLE_GROUPS
    ]

    collected: Dict[str, List[Dict[str, Any]]] = {}
    for block in parse_blocks(rows):
        text = block["text"]
        mentions: List[Tuple[int, int, str, FieldDefinition]] = []
        for definition in wanted:
            found = _find_mentions(text, definition)
            if found is not None:
                mentions.append((found[0], found[1], found[2], definition))
        if not mentions:
            # 文本块本身没有栏目名，但 sidecar/引擎给出了 column_hint（架构 5.4 示例）：
            # 只在“栏目名可识别”时用整块原文归属该字段，绝不猜测栏目（R-37）。
            hinted = _by_column_hint(block["column_hint"], wanted)
            if hinted is None:
                continue
            collected.setdefault(hinted.key, []).append(
                {
                    "value": text,
                    "fragment": text,
                    "alias": block["column_hint"],
                    "status": infer_status(text),
                    "confidence": block["confidence"],
                    "material_id": block["material_id"],
                    "page_number": block["page_number"],
                    "block_index": block["block_index"],
                    "block_id": block["block_id"],
                    "bbox": list(block["bbox"]),
                    "column_hint": block["column_hint"],
                    "is_handwritten": block["is_handwritten"],
                }
            )
            continue
        mentions.sort(key=lambda item: (item[0], -item[1]))

        resolved: List[Tuple[int, int, str, FieldDefinition]] = []
        for mention in mentions:
            start, length, _alias, _definition = mention
            if resolved and start < resolved[-1][0] + resolved[-1][1]:
                # 落在上一个标签内部（例如“案件编号”里的“编号”），跳过。
                continue
            resolved.append(mention)

        for position, (start, length, alias, definition) in enumerate(resolved):
            value_start = start + length
            value_end = resolved[position + 1][0] if position + 1 < len(resolved) else len(text)
            fragment = _strip_service(text[start:value_end]) or _clean(text)
            value = _strip_service(text[value_start:value_end])
            collected.setdefault(definition.key, []).append(
                {
                    "value": value,
                    "fragment": fragment or _clean(text),
                    "alias": alias,
                    # 栏目有来源但内容为空（如“角膜：”）→ not_recorded，绝不写成“未见异常”（N-5）。
                    "status": infer_status(value),
                    "confidence": block["confidence"],
                    "material_id": block["material_id"],
                    "page_number": block["page_number"],
                    "block_index": block["block_index"],
                    "block_id": block["block_id"],
                    "bbox": list(block["bbox"]),
                    "column_hint": block["column_hint"] or alias,
                    "is_handwritten": block["is_handwritten"],
                }
            )

    fields: List[ExtractedField] = []
    for definition in _definitions(definitions):
        sources = collected.get(definition.key)
        if not sources:
            continue  # 只填有来源的字段
        sources.sort(key=lambda item: (item["page_number"], item["block_index"]))
        primary = sources[0]
        confidence = min(
            (item["confidence"] for item in sources if item["confidence"] is not None),
            default=None,
        )

        distinct: List[str] = []
        for item in sources:
            if item["value"] not in distinct:
                distinct.append(item["value"])

        conflict: List[Dict[str, Any]] = []
        status = primary["status"]
        rule_note = ""
        if len(distinct) > 1:
            status = "unclear"
            conflict = [
                {
                    "value": item["value"],
                    "raw_text": item["fragment"],
                    "material_id": item["material_id"],
                    "page_number": item["page_number"],
                    "block_index": item["block_index"],
                    "block_id": item["block_id"],
                    "confidence": item["confidence"],
                }
                for item in sources
            ]
            rule_note = "同一字段在不同来源取值不一致，已全部保留待人工确认（R-44）"
        elif _low_confidence(confidence) and status == "recorded":
            status = "unclear"
            rule_note = (
                f"识别置信度 {confidence} 低于 {OCR_LOW_CONFIDENCE}，"
                "仅用于安排核对优先级，需人工核对原件（R-21/R-45）"
            )

        field = ExtractedField(
            field_key=definition.key,
            raw_text=primary["fragment"],
            confirmed_value="",
            normalized_text="",
            unit="",  # 单位只在原文出现时由术语规则处理，不在此处补（R-33/N-3）
            status=status,
            review_state="pending",
            confidence=confidence,
            source_material_id=primary["material_id"],
            source_page=int(primary["page_number"] or 0),
            source_bbox=list(primary["bbox"]),
            source_block_id=primary["block_id"],
            applied_rule_codes=[],
            rule_note=rule_note,
            conflict=conflict,
            field_group=definition.group,
            field_label=definition.label,
            display_order=definition.order,
            column_hint=primary["column_hint"],
        )
        fields.append(field)

    fields.sort(key=lambda item: (item.display_order, item.field_key))
    return fields


# --------------------------------------------------------------------------
# 损伤提取
# --------------------------------------------------------------------------


def _strip_leading_number(text: str) -> str:
    previous = ""
    current = text
    while current and current != previous:
        previous = current
        current = _LEADING_NUMBER.sub("", current, count=1).strip()
    return current


def _longest_type(text: str) -> Tuple[str, int]:
    """返回文本中出现的**最长**损伤类型词及其位置；没有则返回 ``("", -1)``。"""

    best = ("", -1)
    for word in sorted(INJURY_TYPE_WORDS, key=len, reverse=True):
        index = text.find(word)
        if index >= 0 and len(word) > len(best[0]):
            best = (word, index)
    return best


def _split_injury(text: str) -> Optional[Dict[str, str]]:
    """解析一行损伤记录；不像损伤行时返回 ``None``。

    只做“切分与照抄”，不推断形态、颜色、深度，也不改左右侧（N-2/N-4）。
    """

    body = _strip_leading_number(text)
    if not body:
        return None

    measure = _MEASURE.search(body)
    count = _COUNT.search(body)
    injury_type, type_index = _longest_type(body)
    if not injury_type and not measure:
        return None  # 既无类型词也无尺寸：不当作损伤记录，避免凭空造条

    working = body
    length = width = unit = ""
    if measure:
        length = measure.group("length")
        width = measure.group("width")
        unit = measure.group("unit") or ""
        working = (working[:measure.start()] + " " + working[measure.end():]).strip()
    count_value = ""
    if count:
        count_value = count.group("count")
        working = (working[:count.start()] + " " + working[count.end():]).strip()

    side = ""
    for candidate in ("双侧", "左", "右"):
        if working.startswith(candidate):
            side = candidate
            working = working[len(candidate):].strip()
            break

    type_index = working.find(injury_type) if injury_type else -1
    if injury_type and type_index >= 0:
        body_part = working[:type_index]
        tail = working[type_index + len(injury_type):]
    else:
        body_part = ""
        tail = working
    body_part = _strip_service(body_part)
    other_traits = _strip_service(tail)

    return {
        "body_part": body_part,
        "side": side,
        "injury_type": injury_type,
        "count": count_value,
        "length": length,
        "width": width,
        "size_unit": unit,
        "other_traits": other_traits,
    }


def extract_injuries(rows: Sequence[Mapping[str, Any]]) -> List[ExtractedInjury]:
    """逐行提取损伤：**一行 OCR → 至多一条损伤**（R-16 / INV-4 / R-36）。

    * ``seq`` 按 ``(page_number, block_index)`` 递增，与原文顺序一致（R-3/R-7）；
    * 否定/未记录/未检的行（如“未见骨折”“未检”）**不产生**损伤记录（N-2/R-17）；
    * 尺寸、数量、左右侧一律照抄原文，缺失就留空，绝不补单位与形态（N-3/N-4）。
    """

    injuries: List[ExtractedInjury] = []
    for block in parse_blocks(rows):
        text = block["text"]
        status = infer_status(text)
        if status != "recorded":
            # 明确阴性/未记录/未检的行不是“一条损伤”，不能被记成损伤事实。
            continue
        parsed = _split_injury(text)
        if parsed is None:
            continue
        injuries.append(
            ExtractedInjury(
                seq=len(injuries) + 1,
                body_part=parsed["body_part"],
                side=parsed["side"],
                injury_type=parsed["injury_type"],
                count=parsed["count"],
                length=parsed["length"],
                width=parsed["width"],
                size_unit=parsed["size_unit"],
                morphology="",  # 原文未单独记载形态时不推断（N-2）
                other_traits=parsed["other_traits"],
                raw_text=text,
                normalized_text="",
                status="recorded",
                review_state="pending",
                confidence=block["confidence"],
                source_material_id=block["material_id"],
                source_page=int(block["page_number"] or 0),
                source_bbox=list(block["bbox"]),
                source_block_id=block["block_id"],
                applied_rule_codes=[],
                rule_note=(
                    f"识别置信度 {block['confidence']} 低于 {OCR_LOW_CONFIDENCE}，"
                    "需人工核对原件（R-21/R-45）"
                    if _low_confidence(block["confidence"])
                    else ""
                ),
            )
        )
    return injuries


# --------------------------------------------------------------------------
# 冲突汇总
# --------------------------------------------------------------------------


def detect_conflicts(fields: Sequence[ExtractedField]) -> List[Dict[str, Any]]:
    """汇总字段级冲突，供 `checker.py` 产出 `FIELD_CONFLICT`（R-44）。

    只报告**同一字段出现不同取值**的情况；来源不同但取值一致不算冲突
    （那是正常的重复记载，两份材料都要在界面上展示来源）。
    """

    conflicts: List[Dict[str, Any]] = []
    for item in fields or ():
        data = _mapping(item)
        entries = data.get("conflict") or []
        if not entries:
            continue
        field_key = _text(data.get("field_key"))
        definition = FIELD_INDEX.get(field_key)
        values: List[str] = []
        sources: List[Dict[str, Any]] = []
        for entry in entries:
            row = _mapping(entry)
            value = _text(row.get("value"))
            if value not in values:
                values.append(value)
            sources.append(
                {
                    "material_id": _int_or_none(row.get("material_id")),
                    "page_number": _int_or_none(row.get("page_number")),
                    "block_index": _int_or_none(row.get("block_index")),
                    "block_id": _int_or_none(row.get("block_id")),
                    "value": value,
                    "confidence": row.get("confidence"),
                }
            )
        conflicts.append(
            {
                "field_key": field_key,
                "field_label": definition.label if definition else field_key,
                "status": _text(data.get("status")) or "unclear",
                "values": values,
                "sources": sources,
                "message": (
                    f"{definition.label if definition else field_key}在不同材料/页码的记载不一致："
                    + "、".join(value if value else "（空）" for value in values)
                    + "；需展示双方来源并人工确认（R-44）"
                ),
            }
        )
    return conflicts
