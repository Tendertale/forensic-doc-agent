"""校核器（需求 5.7 / 第八章；架构文档 5.7）。

本模块只读地检查 `database.get_case()` 的返回结构，产出结构化的校核项
（`CheckItem`），供 API、前端清单与 `docgen.py` 的签发门禁使用。

契约约束
--------
* 类别取自 ``constants.CHECK_CATEGORIES``，级别取自 ``constants.CHECK_SEVERITIES``；
* 24 个 ``code`` 是契约的一部分（测试与前端按 code 断言），不得增删改名
  （``PHOTO_SLOT_MISSING`` 为「照片栏位兜底」新增的第 24 个，见 §5.7 交付说明）；
* 本模块**只报告，不修改**任何输入数据，也不写数据库。

``code`` ↔ 需求编号对照
-----------------------
recognition（识别质量）
  * ``OCR_LOW_CONFIDENCE``            R-21/R-22：低置信度块进入待辨认，只用于排序与提示。
  * ``OCR_HANDWRITTEN_UNVERIFIED``    R-21/R-45：手写块必须被字段引用，否则需人工核对。
  * ``PHOTO_CONTENT_READ``            N-11/R-55 / INV-11：部位照片不得有任何识别结果。

content（内容质量）
  * ``FIELD_UNRESOLVED_CRITICAL``     R-28/R-45 / INV-2：关键字段 unclear 或 review_state=pending 即阻断。
  * ``FIELD_REQUIRED_EMPTY``          R-14/R-28：必填字段缺失或 confirmed_value 为空。
  * ``FIELD_CONFLICT``                R-44：材料冲突必须显式暴露，不得静默取值。
  * ``INJURY_UNRESOLVED``             R-16/R-45：损伤条目待核对。
  * ``INJURY_MISSING_SIZE``           R-16/R-36：长宽缺一，不得补全、不得合并条目。
  * ``NEGATION_UNPRESERVED``          N-3/N-4/R-35：否定词丢失（尤其“未”）。
  * ``NOT_RECORDED_MISLABELED``       N-5/R-35 / INV-3：空白被写成“未见异常”。
  * ``OPINION_FROM_SOURCE``           N-1/R-30 / INV-10：鉴定意见不得从 OCR 原文推导。
  * ``PHOTO_POSITION_MISSING``        R-57：部位照片未指定目标位置，不得进入文稿。

source（来源完整性）
  * ``MATERIAL_MISSING``              R-12/R-13：必需材料类型缺失。
  * ``FIELD_SOURCE_MISSING``          R-15/R-21：已记载字段缺少来源定位。

format（格式质量）
  * ``DOCUMENT_UNRESOLVED_PLACEHOLDER`` R-33/R-46：模板占位符残留。
  * ``DOCUMENT_DRAFT_MARKER``         R-46：待签发稿正文仍含 ``DRAFT_MARKER``。
  * ``DOCUMENT_PDF_MISSING``          R-50/R-51：PDF 未生成（LibreOffice 不可用或导出失败）。
  * ``DOCUMENT_TEXT_DIVERGED``        R-46/R-50：渲染文本与存储文本不一致。
  * ``PHOTO_FILE_MISSING``            R-56/N-12：已指派位置的照片原件文件不存在，渲染时被跳过。
  * ``PHOTO_SLOT_EMPTY``              N-12：模板声明了该照片栏位但本次没有照片（正文写占位文字）。
  * ``PHOTO_SLOT_MISSING``            R-56/N-12：案件有已指派位置的照片，但模板没声明该栏位的
    锚点、渲染产物里也没有该栏位 → 照片进不了文稿（提醒加 `{{ photo_<位置> }}` 或让系统自动追加）。

correction（修改可靠性）
  * ``CORRECTION_UNAPPLIED``          R-38/R-40：修正未应用。
  * ``CORRECTION_SCOPE_UNCLEAR``      R-39/N-10 / INV-7：非案件级修正缺少 scope_value。
  * ``REVIEW_INVALIDATED``            R-40/R-41 / INV-6：已确认字段被修正打回待核对。

输入容错
--------
`case_data` 允许缺任意键、允许子项为 ``None`` 或非预期类型；所有取值都走
``.get()`` + 类型归一化，**不得因缺 key 抛 KeyError**。
OCR 块支持 ``case_data["ocr_blocks"]`` 与 ``case_data["materials"][i]["ocr_blocks"]``
两种形态；两者都没有时退化为仅依据字段的 ``confidence`` 判断。
部位照片支持 ``case_data["photo_slots"]``（`database.list_photo_slots()` /
`docgen.build_context()` 的结构，路径已解析）与 ``case_data["materials"]`` 中
``kind == "photo"`` 的行两种来源；两者都没有时照片类校核项一律不产出，绝不抛异常。
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from . import constants

__all__ = [
    "CheckItem",
    "check_recognition",
    "check_content",
    "check_source",
    "check_format",
    "check_corrections",
    "check_case",
    "has_blocking",
]


# --------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------


@dataclass
class CheckItem:
    """一条校核项。字段与 ``checklist_items`` 表列一一对应。"""

    category: str
    severity: str
    code: str
    title: str
    detail: str
    target_type: str = ""
    target_id: int = 0
    source: dict = field(default_factory=dict)
    suggestion: str = ""


# --------------------------------------------------------------------------
# 通用容错工具
# --------------------------------------------------------------------------

#: 归一化比较时去掉的标点与符号（中文全角 + 英文半角 + 常见符号）。
_PUNCT_RE = re.compile(
    r"[\s\u3000"
    r"!-/:-@\[-`{-~"
    r"\u3001\u3002\u300a\u300b\u300c\u300d\u300e\u300f\u3010\u3011\u3014\u3015"
    r"\uff01\uff02\uff03\uff04\uff05\uff06\uff07\uff08\uff09\uff0a\uff0b\uff0c"
    r"\uff0d\uff0e\uff0f\uff1a\uff1b\uff1c\uff1d\uff1e\uff1f\uff20\uff3b\uff3c"
    r"\uff3d\uff3e\uff3f\uff5b\uff5c\uff5d\uff5e\u2018\u2019\u201c\u201d\u2026"
    r"\u2014\u2013\u00b7\u3007]+"
)

_WS_RE = re.compile(r"\s+")

#: 违反 N-5 的标签。
_MISLABEL_TEXT = "未见异常"

#: “高度重合”阈值（架构 5.7 / 任务书）。
_OPINION_SIMILARITY = 0.8


def _text(value: Any) -> str:
    """把任意值安全转成字符串（None → ""）。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    return str(value)


def _has_text(value: Any) -> bool:
    """是否存在非空白文本。"""
    return bool(_text(value).strip())


def _seq(value: Any) -> List[Any]:
    """把任意值安全转成 list。"""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, (str, bytes, bytearray)):
        return []
    if isinstance(value, Mapping):
        return [value]
    return []


def _rows(case_data: Any, key: str) -> List[Dict[str, Any]]:
    """取出 ``case_data[key]`` 中的字典行列表，过滤掉非字典项。"""
    if not isinstance(case_data, Mapping):
        return []
    return [row for row in _seq(case_data.get(key)) if isinstance(row, Mapping)]


def _as_int(value: Any) -> int:
    """安全转 int；失败或为 None 返回 0。"""
    if value is None or isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def _as_float(value: Any) -> Optional[float]:
    """安全转 float；失败或为 None 返回 None。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _norm_text(value: Any) -> str:
    """归一化空白（用于文稿文本一致性比较）。"""
    return _WS_RE.sub(" ", _text(value)).strip()


def _norm_strict(value: Any) -> str:
    """去掉所有空白与标点（用于“高度重合”判定）。"""
    return _PUNCT_RE.sub("", _text(value))


def _field_label(field_row: Mapping[str, Any]) -> str:
    """字段标签：优先行内 field_label，其次 FIELD_INDEX，最后 field_key。"""
    label = _text(field_row.get("field_label")).strip()
    if label:
        return label
    key = _text(field_row.get("field_key")).strip()
    definition = constants.FIELD_INDEX.get(key)
    if definition is not None:
        return definition.label
    return key or "（未知字段）"


def _field_key(field_row: Mapping[str, Any]) -> str:
    return _text(field_row.get("field_key")).strip()


def _status_label(status: str) -> str:
    return constants.FIELD_STATUS_LABELS.get(status, status or "未知状态")


def _review_label(review_state: str) -> str:
    return constants.REVIEW_STATE_LABELS.get(review_state, review_state or "未知复核状态")


def _severity_label(severity: str) -> str:
    return constants.CHECK_SEVERITY_LABELS.get(severity, severity)


def _field_value(field_row: Mapping[str, Any]) -> str:
    """字段的“已确认取值”：优先 confirmed_value，其次 normalized_text。"""
    confirmed = _text(field_row.get("confirmed_value")).strip()
    if confirmed:
        return confirmed
    return _text(field_row.get("normalized_text")).strip()


def _doc_label(document: Mapping[str, Any]) -> str:
    """文稿定位标签：类型 + 版本号。"""
    doc_type = _text(document.get("doc_type")).strip() or "report"
    version_no = _as_int(document.get("version_no"))
    if version_no:
        return f"文稿 {doc_type} v{version_no}"
    return f"文稿 {doc_type}"


# --------------------------------------------------------------------------
# OCR 块收集
# --------------------------------------------------------------------------


@dataclass
class _OcrBlock:
    """归一化后的 OCR 块视图。"""

    block_id: int
    text: str
    confidence: Optional[float]
    is_handwritten: bool
    material_id: Optional[int]
    page_number: int
    origin: str  # "case" | "material" | "field"

    def label(self) -> str:
        parts: List[str] = []
        if self.material_id is not None:
            parts.append(f"材料 {self.material_id}")
        if self.page_number:
            parts.append(f"第 {self.page_number} 页")
        if self.block_id:
            parts.append(f"块 {self.block_id}")
        return " / ".join(parts) if parts else "未定位文本块"


def _normalize_block(raw: Mapping[str, Any], *, origin: str, index: int) -> _OcrBlock:
    block_id = _as_int(raw.get("id"))
    if not block_id:
        block_id = _as_int(raw.get("block_id"))
    if not block_id:
        # 没有稳定 id 时用序号兜底，保证 target_id 可区分。
        block_id = index + 1
    material_id = raw.get("material_id")
    material_id = _as_int(material_id) if material_id is not None else None
    return _OcrBlock(
        block_id=block_id,
        text=_text(raw.get("text")),
        confidence=_as_float(raw.get("confidence")),
        is_handwritten=bool(raw.get("is_handwritten")),
        material_id=material_id,
        page_number=_as_int(raw.get("page_number")),
        origin=origin,
    )


def _collect_ocr_blocks(case_data: Any) -> Tuple[List[_OcrBlock], List[Dict[str, Any]]]:
    """收集 OCR 块。

    返回 ``(blocks, field_rows)``；``field_rows`` 是 ``case_data["fields"]``
    的原始行，供字段置信度退化路径使用。无 OCR 块时不报错，返回空列表。
    """
    blocks: List[_OcrBlock] = []
    seen: set = set()

    def _add(raw: Mapping[str, Any], origin: str, index: int) -> None:
        block = _normalize_block(raw, origin=origin, index=index)
        key = (block.material_id, block.page_number, block.block_id)
        if key in seen:
            return
        seen.add(key)
        blocks.append(block)

    if isinstance(case_data, Mapping):
        for index, raw in enumerate(_seq(case_data.get("ocr_blocks"))):
            if isinstance(raw, Mapping):
                _add(raw, "case", index)

        for material in _rows(case_data, "materials"):
            nested = _seq(material.get("ocr_blocks"))
            material_id = _as_int(material.get("id"))
            for index, raw in enumerate(nested):
                if not isinstance(raw, Mapping):
                    continue
                if raw.get("material_id") is None and material_id:
                    raw = {**raw, "material_id": material_id}
                _add(raw, "material", index)

    return blocks, _rows(case_data, "fields")


# --------------------------------------------------------------------------
# 部位照片视图（N-11 / R-55 / R-56 / R-57 / N-12）
#
# 只读取“文件是否存在”与“用户指定的 position_key”；**绝不读取、识别或推断照片
# 的像素内容**，也不把任何来自照片的图像结论写进校核项。
# --------------------------------------------------------------------------

#: 模板中的照片栏位锚点：`{{ photo_<position_key> }}`（允许前后空白）。
_PHOTO_ANCHOR_RE = re.compile(r"\{\{\s*photo_(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def _photo_material_rows(case_data: Any) -> List[Dict[str, Any]]:
    """`kind == "photo"` 的材料行（缺失或类型异常时返回空列表）。"""
    return [
        row
        for row in _rows(case_data, "materials")
        if _text(row.get("kind")).strip() == constants.PHOTO_MATERIAL_KIND
    ]


def _photo_slot_rows(case_data: Any) -> List[Dict[str, Any]]:
    """取出 `case_data["photo_slots"]` 的行：兼容 list 与 dict[key] -> slot 两种形态。"""
    raw = case_data.get("photo_slots") if isinstance(case_data, Mapping) else None
    rows: List[Dict[str, Any]] = []
    if isinstance(raw, Mapping):
        for key, value in raw.items():
            if not isinstance(value, Mapping):
                continue
            row = dict(value)
            if not _text(row.get("position_key")).strip():
                row["position_key"] = _text(key).strip()
            rows.append(row)
        return rows
    return [dict(row) for row in _seq(raw) if isinstance(row, Mapping)]


def _photo_slot_photos(case_data: Any) -> Dict[str, List[Dict[str, Any]]]:
    """`position_key` → 该栏位的照片行。

    优先 `case_data["photo_slots"]`（路径已由 workflow 解析）；缺失时按
    `case_data["materials"]` 中 `kind == "photo"` 的 `position_key` 兜底，
    此时 `original_path` 是存储根下的相对路径，能否落盘由 `_file_state` 判定为
    “无法判定”而不报项。
    """
    result: Dict[str, List[Dict[str, Any]]] = {}
    for row in _photo_slot_rows(case_data):
        key = _text(row.get("position_key")).strip()
        if not key:
            continue
        slot_label = _text(row.get("label")).strip()
        photos: List[Dict[str, Any]] = []
        for index, raw_photo in enumerate(_seq(row.get("photos")), start=1):
            photo = raw_photo if isinstance(raw_photo, Mapping) else {}
            path = (
                _text(photo.get("path")).strip()
                or _text(photo.get("original_path")).strip()
                or _text(photo.get("file_path")).strip()
            )
            photos.append(
                {
                    "material_id": _as_int(photo.get("material_id")),
                    "path": path,
                    "filename": _text(photo.get("filename")).strip()
                    or (Path(path).name if path else ""),
                    "caption": _text(photo.get("caption")).strip()
                    or _text(photo.get("photo_caption")).strip(),
                    "sort_order": _as_int(photo.get("sort_order")),
                    "index": index,
                    "slot_label": _text(photo.get("label")).strip() or slot_label,
                }
            )
        if photos:
            result.setdefault(key, []).extend(photos)

    if result:
        return result

    for row in _photo_material_rows(case_data):
        key = _text(row.get("position_key")).strip()
        if not key:
            continue
        result.setdefault(key, []).append(
            {
                "material_id": _as_int(row.get("id")),
                "path": _text(row.get("original_path")).strip(),
                "filename": _text(row.get("filename")).strip(),
                "caption": _text(row.get("photo_caption")).strip(),
                "sort_order": _as_int(row.get("sort_order")),
                "index": 0,
            }
        )
    return result


def _file_state(case_data: Any, value: Any) -> Optional[bool]:
    """照片原件是否存在：``True`` 存在／``False`` 明确不存在／``None`` 无法判定。

    相对路径只在本机给出存储根（`storage_root`/`storage_dir`/`root`）时才判定；
    否则返回 ``None``，避免把“路径本来就无法从此处解析”误报成文件缺失。
    """
    text = _text(value).strip()
    if not text:
        return None
    try:
        candidate = Path(text)
        if candidate.is_file():
            return True
        if candidate.is_absolute():
            return False
    except OSError:
        return None
    root = ""
    if isinstance(case_data, Mapping):
        for key in ("storage_root", "storage_dir", "root"):
            root = _text(case_data.get(key)).strip()
            if root:
                break
    if not root:
        return None
    try:
        return (Path(root) / text).is_file()
    except OSError:
        return None


def _declared_photo_keys(case_data: Any, template_text: str) -> List[str]:
    """模板中显式声明的照片栏位 key（`{{ photo_<key> }}`），保持出现顺序。

    先在 `template_text`（其次 `case_data["template_text"]`）中找锚点，再用显式的
    `photo_slot_keys` / `photo_anchors` 字段兜底。无法确定模板声明了哪些栏位时返回
    空列表——宁可不报 ``PHOTO_SLOT_EMPTY``，也不凭空猜测栏位（不得编造事实）。
    """
    sources: List[str] = [_text(template_text)]
    if isinstance(case_data, Mapping):
        sources.append(_text(case_data.get("template_text")))
    keys: List[str] = []
    for source in sources:
        if not source.strip():
            continue
        for match in _PHOTO_ANCHOR_RE.finditer(source):
            key = match.group("key")
            if key in constants.PHOTO_POSITION_KEYS and key not in keys:
                keys.append(key)
    if keys:
        return keys
    if isinstance(case_data, Mapping):
        for field_name in ("photo_slot_keys", "photo_anchors"):
            for item in _seq(case_data.get(field_name)):
                key = _text(item).strip()
                if key.startswith("photo_"):
                    key = key[len("photo_") :]
                if key in constants.PHOTO_POSITION_KEYS and key not in keys:
                    keys.append(key)
    return keys


def _photo_slot_label(key: str, photos: Sequence[Mapping[str, Any]]) -> str:
    for photo in photos:
        label = _text(photo.get("slot_label")).strip() or _text(photo.get("label")).strip()
        if label:
            return label
    return constants.PHOTO_POSITION_LABELS.get(key, key)


def _photo_slot_target_id(key: str) -> int:
    """栏位的稳定 target_id：取自目录 `order`；目录外的 key 用 0。

    去重规则是 `(code, target_type, target_id)`，因此同一 `code` 下的不同栏位必须
    有不同的 `target_id`，否则会被合并成一条。
    """
    definition = constants.PHOTO_POSITION_INDEX.get(key)
    order = getattr(definition, "order", 0)
    try:
        value = int(order)
    except (TypeError, ValueError):
        value = 0
    return value if value > 0 else 0


#: 标准照片区的栏位标题行前缀（与 `docgen.PHOTO_SLOT_TITLE_PREFIX` 同一约定：
#: 「照片栏位：<中文栏位名>」）。这是 `PHOTO_SLOT_MISSING` 判断“栏位是否真的进入了
#: 文稿”的唯一文本依据，两个模块必须同步改动。
_PHOTO_SLOT_TITLE_PREFIX = "照片栏位："


def _photo_slot_title_texts(key: str, photos: Sequence[Mapping[str, Any]]) -> List[str]:
    """该栏位在文稿正文里应当出现的标题行文本（可能有多种写法）。

    优先取 `docgen.photo_slot_title()`（标准照片区与演示模板写入的唯一格式），
    `docgen` 不可用时退回本模块的前缀常量；此外也接受栏位行自带的自定义 `label`
    （用户改过栏位名时不算“没有栏位”）。只读文本，不看任何图片（N-11）。
    """
    titles: List[str] = []
    try:
        from . import docgen  # 局部导入：复用“栏位标题行”这一文本约定的唯一来源

        titles.append(docgen.photo_slot_title(key))
    except Exception:  # pragma: no cover - docgen 不可用时退回常量
        label = constants.PHOTO_POSITION_LABELS.get(key, key)
        if getattr(constants.PHOTO_POSITION_INDEX.get(key), "wide", False):
            label = f"{label}（宽幅）"
        titles.append(_PHOTO_SLOT_TITLE_PREFIX + label)
    label = _photo_slot_label(key, photos)
    if label:
        titles.append(_PHOTO_SLOT_TITLE_PREFIX + label)
    return titles


def _rendered_texts(case_data: Any, stored_text: str, rendered_snapshot: str) -> List[str]:
    """所有可用的“文稿正文文本”：逐文稿文本 + `stored_text` + 生成时快照。

    用于判断某个照片栏位是否**真的**出现在文稿里（`PHOTO_SLOT_MISSING`）。
    宁缺勿猜：一条文本都没有时返回空列表，调用方据此按“没有栏位”报告。
    """
    texts: List[str] = []
    for document in _rows(case_data, "documents"):
        value = _text(document.get("rendered_text")) or _text(document.get("text"))
        if value.strip():
            texts.append(value)
    for value in (stored_text, rendered_snapshot):
        text = _text(value)
        if text.strip():
            texts.append(text)
    return texts


# --------------------------------------------------------------------------
# recognition —— 识别质量
# --------------------------------------------------------------------------


def check_recognition(case_data: Mapping[str, Any]) -> List[CheckItem]:
    """识别质量检查：低置信度块、手写块未被引用。"""
    items: List[CheckItem] = []
    blocks, field_rows = _collect_ocr_blocks(case_data)

    referenced: set = set()
    for row in field_rows:
        block_id = _as_int(row.get("source_block_id"))
        if block_id:
            referenced.add(block_id)

    # ---- OCR_LOW_CONFIDENCE ------------------------------------------------
    low_blocks: List[_OcrBlock] = []
    for block in blocks:
        if block.confidence is not None and block.confidence < constants.OCR_LOW_CONFIDENCE:
            low_blocks.append(block)

    if blocks:
        for block in low_blocks:
            snippet = block.text.strip()
            snippet = snippet[:40] + ("…" if len(snippet) > 40 else "")
            items.append(
                CheckItem(
                    category="recognition",
                    severity="warning",
                    code="OCR_LOW_CONFIDENCE",
                    title="识别置信度偏低，需人工辨认",
                    detail=(
                        f"{block.label()} 的识别置信度仅 {block.confidence:.2f}，"
                        f"低于阈值 {constants.OCR_LOW_CONFIDENCE:.2f}"
                        + (f"，文本片段“{snippet}”" if snippet else "")
                        + "；该分数只用于排序，不能作为结论依据。"
                    ),
                    target_type="document",
                    target_id=block.block_id,
                    source={
                        "block_id": block.block_id,
                        "material_id": block.material_id,
                        "page_number": block.page_number,
                        "confidence": block.confidence,
                        "threshold": constants.OCR_LOW_CONFIDENCE,
                        "text": block.text,
                        "origin": block.origin,
                    },
                    suggestion="对照原件逐字核对，确认无误后在字段上点“已确认”。",
                )
            )
    else:
        # 退化路径：无 OCR 块时用字段 confidence + source_block_id 推断。
        for row in field_rows:
            confidence = _as_float(row.get("confidence"))
            if confidence is None or confidence >= constants.OCR_LOW_CONFIDENCE:
                continue
            label = _field_label(row)
            block_id = _as_int(row.get("source_block_id"))
            items.append(
                CheckItem(
                    category="recognition",
                    severity="warning",
                    code="OCR_LOW_CONFIDENCE",
                    title="字段来源识别置信度偏低，需人工辨认",
                    detail=(
                        f"字段“{label}”的识别置信度为 {confidence:.2f}，"
                        f"低于阈值 {constants.OCR_LOW_CONFIDENCE:.2f}"
                        + (f"，来源块 {block_id}" if block_id else "")
                        + "；案件未提供 OCR 块明细，仅按字段置信度提示。"
                    ),
                    target_type="field",
                    target_id=block_id,
                    source={
                        "field_key": _field_key(row),
                        "field_label": label,
                        "confidence": confidence,
                        "threshold": constants.OCR_LOW_CONFIDENCE,
                        "block_id": block_id,
                        "material_id": _as_int(row.get("source_material_id")) or None,
                        "page_number": _as_int(row.get("source_page")),
                    },
                    suggestion="对照原件逐字核对，确认后更新该字段的复核状态。",
                )
            )

    # ---- OCR_HANDWRITTEN_UNVERIFIED ---------------------------------------
    for block in blocks:
        if not block.is_handwritten:
            continue
        if block.block_id in referenced:
            continue
        snippet = block.text.strip()
        snippet = snippet[:40] + ("…" if len(snippet) > 40 else "")
        items.append(
            CheckItem(
                category="recognition",
                severity="warning",
                code="OCR_HANDWRITTEN_UNVERIFIED",
                title="手写内容尚未被任何字段引用",
                detail=(
                    f"{block.label()} 为手写识别结果"
                    + (f"，文本片段“{snippet}”" if snippet else "")
                    + "，但没有任何字段把 source_block_id 指向它，说明该手写内容可能漏提取。"
                ),
                target_type="document",
                target_id=block.block_id,
                source={
                    "block_id": block.block_id,
                    "material_id": block.material_id,
                    "page_number": block.page_number,
                    "is_handwritten": True,
                    "text": block.text,
                    "origin": block.origin,
                },
                suggestion="确认该手写内容是否属于应记录事实；属于则补建字段，不属于则在清单中注明豁免理由。",
            )
        )

    # ---- PHOTO_CONTENT_READ ----------------------------------------------
    # N-11/R-55：部位照片只贡献图像文件与 position_key，永远不得有识别结果。
    # 这里只看“存在多少条 ocr_blocks 记录”，**不读取照片内容**，也不把任何
    # 归一化后的块文本当作照片内容引用。
    photo_materials = _photo_material_rows(case_data)
    if photo_materials:
        photo_ids = {
            _as_int(row.get("id")) for row in photo_materials if _as_int(row.get("id"))
        }
        by_material: Dict[int, List[_OcrBlock]] = {}
        for block in blocks:
            if block.material_id is None or block.material_id not in photo_ids:
                continue
            by_material.setdefault(block.material_id, []).append(block)
        for material_id, material_blocks in by_material.items():
            material = next(
                (row for row in photo_materials if _as_int(row.get("id")) == material_id), {}
            )
            filename = _text(material.get("filename")).strip() or "（未记录文件名）"
            block_ids = sorted({block.block_id for block in material_blocks})
            items.append(
                CheckItem(
                    category="recognition",
                    severity="blocking",
                    code="PHOTO_CONTENT_READ",
                    title="部位照片被送入了文字识别",
                    detail=(
                        f"部位照片材料 {material_id}（{filename}）下存在 "
                        f"{len(material_blocks)} 条 ocr_blocks 记录"
                        f"（块 {', '.join(str(value) for value in block_ids[:8])}）；"
                        "系统不得对照片内容做识别、描述或推断，照片一律跳过 OCR"
                        "（N-11/R-55/INV-11）。"
                    ),
                    target_type="material",
                    target_id=material_id,
                    source={
                        "material_id": material_id,
                        "filename": filename,
                        "kind": constants.PHOTO_MATERIAL_KIND,
                        "position_key": _text(material.get("position_key")).strip(),
                        "block_count": len(material_blocks),
                        "block_ids": block_ids,
                        "page_numbers": sorted(
                            {block.page_number for block in material_blocks if block.page_number}
                        ),
                        # 刻意不携带照片的任何 R-15 版式信息：系统不读取照片内容（N-11）。
                    },
                    suggestion=(
                        "删除这些照片的识别记录，并在流程中确认照片材料被 OCR 阶段跳过"
                        "（workflow.run_ocr 只处理 OCR_MATERIAL_KINDS）。"
                    ),
                )
            )

    return items


# --------------------------------------------------------------------------
# content —— 内容质量
# --------------------------------------------------------------------------


def check_content(case_data: Mapping[str, Any]) -> List[CheckItem]:
    """内容质量检查：关键字段、必填、冲突、否定词、损伤、鉴定意见来源。"""
    items: List[CheckItem] = []
    field_rows = _rows(case_data, "fields")
    by_key: Dict[str, Dict[str, Any]] = {}
    for row in field_rows:
        key = _field_key(row)
        if key and key not in by_key:
            by_key[key] = row

    # ---- FIELD_REQUIRED_EMPTY --------------------------------------------
    for key in constants.REQUIRED_FIELD_KEYS:
        definition = constants.FIELD_INDEX.get(key)
        label = definition.label if definition is not None else key
        row = by_key.get(key)
        if row is None:
            items.append(
                CheckItem(
                    category="content",
                    severity="blocking",
                    code="FIELD_REQUIRED_EMPTY",
                    title="必填字段缺失",
                    detail=f"必填字段“{label}”（{key}）在案件字段中完全不存在，无法进入待签发稿。",
                    target_type="field",
                    target_id=0,
                    source={"field_key": key, "field_label": label, "reason": "missing_row"},
                    suggestion="回到提取/核对阶段补齐该字段；若材料确实没有记载，请按“未记录”建立该字段行。",
                )
            )
            continue
        if not _field_value(row):
            items.append(
                CheckItem(
                    category="content",
                    severity="blocking",
                    code="FIELD_REQUIRED_EMPTY",
                    title="必填字段为空",
                    detail=(
                        f"必填字段“{label}”（{key}）当前状态为"
                        f"{_status_label(_text(row.get('status')).strip())}，但 confirmed_value 为空。"
                    ),
                    target_type="field",
                    target_id=_as_int(row.get("id")),
                    source={
                        "field_key": key,
                        "field_label": label,
                        "status": _text(row.get("status")),
                        "review_state": _text(row.get("review_state")),
                        "raw_text": _text(row.get("raw_text")),
                    },
                    suggestion="填写确认值；确无记载时改为“未记录”，不得留空。",
                )
            )

    # ---- FIELD_UNRESOLVED_CRITICAL / FIELD_CONFLICT / 否定词 / 标签 -------
    for row in field_rows:
        key = _field_key(row)
        label = _field_label(row)
        status = _text(row.get("status")).strip()
        review_state = _text(row.get("review_state")).strip()
        normalized = _text(row.get("normalized_text"))
        confirmed = _text(row.get("confirmed_value"))
        row_id = _as_int(row.get("id"))

        if key in constants.CRITICAL_FIELD_KEYS and (
            status == "unclear" or review_state == "pending"
        ):
            reasons: List[str] = []
            if status == "unclear":
                reasons.append(f"状态为“{_status_label(status)}”")
            if review_state == "pending":
                reasons.append(f"复核状态为“{_review_label(review_state)}”")
            items.append(
                CheckItem(
                    category="content",
                    severity="blocking",
                    code="FIELD_UNRESOLVED_CRITICAL",
                    title="关键事实尚未定论",
                    detail=(
                        f"关键字段“{label}”（{key}）"
                        + "、".join(reasons)
                        + "；关键事实必须逐项人工核对后才能进入待签发稿（R-28/R-45）。"
                    ),
                    target_type="field",
                    target_id=row_id,
                    source={
                        "field_key": key,
                        "field_label": label,
                        "status": status,
                        "review_state": review_state,
                        "confirmed_value": confirmed,
                        "raw_text": _text(row.get("raw_text")),
                        "source_material_id": row.get("source_material_id"),
                        "source_page": row.get("source_page"),
                        "source_block_id": row.get("source_block_id"),
                    },
                    suggestion="对照原件核对并给出明确取值；确认后把复核状态改为“已确认”。",
                )
            )

        conflict = _seq(row.get("conflict"))
        conflict = [entry for entry in conflict if entry not in (None, "", [], {})]
        if conflict:
            descriptions: List[str] = []
            for entry in conflict:
                if isinstance(entry, Mapping):
                    piece = (
                        _text(entry.get("detail"))
                        or _text(entry.get("note"))
                        or _text(entry.get("text"))
                        or _text(entry.get("value"))
                        or _text(entry.get("source"))
                    )
                    material_id = entry.get("material_id")
                    if material_id is not None:
                        piece = (f"材料 {_as_int(material_id)}：" + piece) if piece else f"材料 {_as_int(material_id)}"
                    descriptions.append(piece or str(dict(entry)))
                else:
                    descriptions.append(_text(entry))
            joined = "；".join(part for part in descriptions if part) or "（冲突明细为空）"
            items.append(
                CheckItem(
                    category="content",
                    severity="blocking",
                    code="FIELD_CONFLICT",
                    title="同一字段存在材料冲突",
                    detail=(
                        f"字段“{label}”（{key}）记录了 {len(conflict)} 条互相冲突的来源取值：{joined}；"
                        "冲突必须由人工裁决，不得静默取值（R-44）。"
                    ),
                    target_type="field",
                    target_id=row_id,
                    source={
                        "field_key": key,
                        "field_label": label,
                        "conflict": conflict,
                        "confirmed_value": confirmed,
                        "raw_text": _text(row.get("raw_text")),
                    },
                    suggestion="逐条比对来源材料，选定唯一取值并记录裁决理由；无法裁决时保持“待辨认”。",
                )
            )

        if status in ("negative", "not_examined"):
            haystack = normalized or confirmed
            if haystack and not any(word and word in haystack for word in constants.NEGATION_WORDS):
                items.append(
                    CheckItem(
                        category="content",
                        severity="blocking",
                        code="NEGATION_UNPRESERVED",
                        title="否定词在整理后丢失",
                        detail=(
                            f"字段“{label}”（{key}）状态为“{_status_label(status)}”，"
                            f"但整理后文本“{haystack}”不含任何否定词"
                            f"（{'、'.join(w for w in constants.NEGATION_WORDS if w)}），"
                            "含义已被改变（N-3/N-4）。"
                        ),
                        target_type="field",
                        target_id=row_id,
                        source={
                            "field_key": key,
                            "field_label": label,
                            "status": status,
                            "normalized_text": normalized,
                            "confirmed_value": confirmed,
                            "raw_text": _text(row.get("raw_text")),
                            "negation_words": list(constants.NEGATION_WORDS),
                        },
                        suggestion="按原文恢复否定词（如“未见”“未检”“无”），不得改写为肯定或空白结论。",
                    )
                )

        if status == "not_recorded":
            for candidate in (normalized, confirmed):
                if _MISLABEL_TEXT in candidate:
                    items.append(
                        CheckItem(
                            category="content",
                            severity="blocking",
                            code="NOT_RECORDED_MISLABELED",
                            title="空白栏目被写成“未见异常”",
                            detail=(
                                f"字段“{label}”（{key}）状态为“未记录”，"
                                f"但文本“{candidate}”含“{_MISLABEL_TEXT}”；"
                                "空白不等于正常，未记录只能写“未记录”（N-5/INV-3）。"
                            ),
                            target_type="field",
                            target_id=row_id,
                            source={
                                "field_key": key,
                                "field_label": label,
                                "status": status,
                                "normalized_text": normalized,
                                "confirmed_value": confirmed,
                                "raw_text": _text(row.get("raw_text")),
                                "mislabeled_text": candidate,
                            },
                            suggestion="把该字段文本改为“未记录”，或回到材料确认是否真有“未见异常”的记载。",
                        )
                    )
                    break

    # ---- INJURY_UNRESOLVED / INJURY_MISSING_SIZE -------------------------
    for injury in _rows(case_data, "injuries"):
        seq_no = _as_int(injury.get("seq"))
        injury_id = _as_int(injury.get("id"))
        body_part = _text(injury.get("body_part")).strip() or "（部位未记录）"
        side = _text(injury.get("side")).strip()
        injury_type = _text(injury.get("injury_type")).strip()
        position = f"第 {seq_no} 条损伤" if seq_no else "损伤条目"
        descriptor = f"{side}{body_part}" + (f"{injury_type}" if injury_type else "")

        if _text(injury.get("review_state")).strip() == "pending":
            items.append(
                CheckItem(
                    category="content",
                    severity="warning",
                    code="INJURY_UNRESOLVED",
                    title="损伤条目待核对",
                    detail=(
                        f"{position}（{descriptor}，id={injury_id}）的复核状态仍为“待核对”，"
                        "尚未经人工确认。"
                    ),
                    target_type="injury",
                    target_id=injury_id,
                    source={
                        "seq": seq_no,
                        "body_part": body_part,
                        "side": side,
                        "injury_type": injury_type,
                        "raw_text": _text(injury.get("raw_text")),
                        "normalized_text": _text(injury.get("normalized_text")),
                        "review_state": _text(injury.get("review_state")),
                        "source_material_id": injury.get("source_material_id"),
                        "source_page": injury.get("source_page"),
                    },
                    suggestion="对照原件逐条核对部位、侧别、类型与尺寸，确认后改为“已确认”。",
                )
            )

        length = _text(injury.get("length")).strip()
        width = _text(injury.get("width")).strip()
        if bool(length) != bool(width):
            missing = "宽度" if length else "长度"
            present = "长度" if length else "宽度"
            present_value = length or width
            unit = _text(injury.get("size_unit")).strip()
            items.append(
                CheckItem(
                    category="content",
                    severity="warning",
                    code="INJURY_MISSING_SIZE",
                    title="损伤尺寸不完整",
                    detail=(
                        f"{position}（{descriptor}，id={injury_id}）只记录了{present} {present_value}"
                        f"{unit}，缺少{missing}；不得补全尺寸，也不得把多条记录合并（R-16/R-36）。"
                    ),
                    target_type="injury",
                    target_id=injury_id,
                    source={
                        "seq": seq_no,
                        "body_part": body_part,
                        "side": side,
                        "injury_type": injury_type,
                        "length": length,
                        "width": width,
                        "size_unit": unit,
                        "missing": "width" if length else "length",
                        "raw_text": _text(injury.get("raw_text")),
                    },
                    suggestion="回查原件确认尺寸；原件确实只有一维时保留原样并在清单中注明“原文仅记录一维”。",
                )
            )

    # ---- OPINION_FROM_SOURCE ---------------------------------------------
    opinion = _text((case_data or {}).get("opinion_text")).strip() if isinstance(case_data, Mapping) else ""
    if opinion:
        opinion_norm = _norm_strict(opinion)
        blocks, _ = _collect_ocr_blocks(case_data)
        for block in blocks:
            block_norm = _norm_strict(block.text)
            if not block_norm or not opinion_norm:
                continue
            matched = False
            if opinion_norm in block_norm or block_norm in opinion_norm:
                matched = True
            elif difflib.SequenceMatcher(None, opinion_norm, block_norm).ratio() >= _OPINION_SIMILARITY:
                matched = True
            if not matched:
                continue
            ratio = difflib.SequenceMatcher(None, opinion_norm, block_norm).ratio()
            snippet = block.text.strip()
            snippet = snippet[:60] + ("…" if len(snippet) > 60 else "")
            items.append(
                CheckItem(
                    category="content",
                    severity="blocking",
                    code="OPINION_FROM_SOURCE",
                    title="鉴定意见疑似来自识别原文",
                    detail=(
                        f"opinion_text 与 {block.label()} 的文本高度重合"
                        f"（相似度 {ratio:.2f}），片段“{snippet}”；"
                        "鉴定意见只能由专业人员输入，不得从 OCR 原文推导（N-1/INV-10）。"
                    ),
                    target_type="case",
                    target_id=_as_int((case_data or {}).get("id")) if isinstance(case_data, Mapping) else 0,
                    source={
                        "opinion_text": opinion,
                        "block_id": block.block_id,
                        "material_id": block.material_id,
                        "page_number": block.page_number,
                        "block_text": block.text,
                        "snippet": snippet,
                        "similarity": round(ratio, 4),
                        "threshold": _OPINION_SIMILARITY,
                    },
                    suggestion="删除该意见文本，改由鉴定人依据检验所见独立撰写；不得直接粘贴材料原文。",
                )
            )

    # ---- PHOTO_POSITION_MISSING ------------------------------------------
    # R-57：未指定目标位置的部位照片不得进入文稿。这里只读材料行的 position_key，
    # 不打开、不识别照片内容（N-11）。
    for index, material in enumerate(_photo_material_rows(case_data), start=1):
        position_key = _text(material.get("position_key")).strip()
        known = position_key in constants.PHOTO_POSITION_INDEX
        material_id = _as_int(material.get("id"))
        filename = _text(material.get("filename")).strip() or "（未记录文件名）"
        if position_key and not known:
            reason = f"position_key=“{position_key}”不在部位位置目录中"
            suggestion = (
                "重新指派目标位置：从“部位照片位置目录”中选择全身/头/面/颈/胸/腹/背/"
                "腰臀/会阴/左右上肢/左右手/左右下肢/左右足/其他之一；未指派的照片不会进入文稿。"
            )
        else:
            reason = "position_key 为空（尚未指派目标位置）"
            suggestion = (
                "为该照片指派目标位置（R-54/R-57）；未指派位置的照片不得进入文稿，"
                "也不得用其他栏位的照片顶替。"
            )
        if known and position_key:
            continue
        items.append(
            CheckItem(
                category="content",
                severity="warning",
                code="PHOTO_POSITION_MISSING",
                title="部位照片未指定目标位置",
                detail=(
                    f"部位照片材料 {material_id or index}（{filename}）{reason}，"
                    "无法确定应插入哪个照片栏位；未指定位置的照片一律不进入文稿（R-57）。"
                ),
                target_type="material",
                target_id=material_id or index,
                source={
                    "material_id": material_id,
                    "filename": filename,
                    "kind": constants.PHOTO_MATERIAL_KIND,
                    "position_key": position_key,
                    "known_position_keys": list(constants.PHOTO_POSITION_KEYS),
                },
                suggestion=suggestion,
            )
        )

    return items


# --------------------------------------------------------------------------
# source —— 来源完整性
# --------------------------------------------------------------------------


def check_source(case_data: Mapping[str, Any]) -> List[CheckItem]:
    """来源完整性检查：必需材料缺失、已记载字段缺来源。"""
    items: List[CheckItem] = []

    materials = _rows(case_data, "materials")
    present_kinds = {_text(row.get("kind")).strip() for row in materials}

    # ---- MATERIAL_MISSING -------------------------------------------------
    for kind in constants.REQUIRED_MATERIAL_KINDS:
        if kind in present_kinds:
            continue
        label = constants.MATERIAL_KIND_LABELS.get(kind, kind)
        items.append(
            CheckItem(
                category="source",
                severity="blocking",
                code="MATERIAL_MISSING",
                title="必需材料缺失",
                detail=(
                    f"必需材料类型“{label}”（{kind}）在本案材料清单中不存在；"
                    f"当前已有材料类型：{'、'.join(sorted(k for k in present_kinds if k)) or '（无）'}。"
                ),
                target_type="material",
                target_id=0,
                source={
                    "kind": kind,
                    "kind_label": label,
                    "required_kinds": list(constants.REQUIRED_MATERIAL_KINDS),
                    "present_kinds": sorted(k for k in present_kinds if k),
                },
                suggestion=f"补充上传{label}扫描件；确实无法取得时，在清单中记录缺失原因并由专业人员确认。",
            )
        )

    # ---- FIELD_SOURCE_MISSING --------------------------------------------
    for row in _rows(case_data, "fields"):
        if _text(row.get("status")).strip() != "recorded":
            continue
        material_id = row.get("source_material_id")
        if material_id is not None and _as_int(material_id) != 0:
            continue
        label = _field_label(row)
        key = _field_key(row)
        items.append(
            CheckItem(
                category="source",
                severity="blocking",
                code="FIELD_SOURCE_MISSING",
                title="已记载字段缺少来源定位",
                detail=(
                    f"字段“{label}”（{key}）状态为“明确记载”，"
                    "但 source_material_id 为空，无法回溯到具体材料页（R-15/R-21）。"
                ),
                target_type="field",
                target_id=_as_int(row.get("id")),
                source={
                    "field_key": key,
                    "field_label": label,
                    "status": _text(row.get("status")),
                    "confirmed_value": _text(row.get("confirmed_value")),
                    "raw_text": _text(row.get("raw_text")),
                    "source_material_id": material_id,
                    "source_page": row.get("source_page"),
                    "source_block_id": row.get("source_block_id"),
                },
                suggestion="回查该取值的来源材料与页码并补记 source_material_id；无法定位时不得标为“明确记载”。",
            )
        )

    return items


# --------------------------------------------------------------------------
# format —— 格式质量
# --------------------------------------------------------------------------


def check_format(
    case_data: Mapping[str, Any],
    *,
    template_text: str = "",
    stored_text: str = "",
    rendered_snapshot: str = "",
) -> List[CheckItem]:
    """格式质量检查：占位符残留、草稿标记、PDF 缺失、文本分歧、照片栏位去向。

    ``template_text``：模板正文文本，仅用于判断“模板声明了哪些栏位”（照片栏位占位提示）；
    ``stored_text``：**当前**已渲染文稿的正文文本；
    ``rendered_snapshot``：**生成该文稿时**保存的正文文本快照（``<docx>.render.txt``）。
    ``DOCUMENT_TEXT_DIVERGED`` 只在同时提供快照与当前文本时比较——即检测文稿在生成之后
    被系统之外改动的情形；没有快照时**不报**（宁缺勿猜，避免把“模板≠产物”误报成缺陷）。

    照片栏位（**设计选择，语义冻结在此**）：`render_document` 把“模板没有声明任何
    ``{{ photo_* }}`` 锚点、但案件确有已指派位置的照片”做成**默认兜底**——自动向渲染用的
    **工作副本**追加标准「部位照片」区（18 个栏位，模板原件不改动，N-8），因此**自动追加
    成功后不再产出** ``PHOTO_SLOT_MISSING``；本 code 只在“照片确实无处安放”时产出：模板
    没声明该栏位、且渲染产物正文里也没有该栏位的标题行（生成失败 / 追加异常 / 模板与输出
    同一路径 / 模板只声明了别的栏位）。判定只看文本，不打开任何图片（N-11）。
    """
    items: List[CheckItem] = []
    documents = _rows(case_data, "documents")
    case_id = _as_int((case_data or {}).get("id")) if isinstance(case_data, Mapping) else 0
    single_document = len(documents) <= 1

    for document in documents:
        doc_id = _as_int(document.get("id"))
        doc_label = _doc_label(document)
        doc_status = _text(document.get("doc_status")).strip()
        if not doc_status and isinstance(case_data, Mapping):
            doc_status = _text(case_data.get("doc_status")).strip()

        per_doc_text = _text(document.get("rendered_text")) or _text(document.get("text"))
        if single_document and not per_doc_text.strip():
            doc_text = stored_text
        else:
            doc_text = per_doc_text
        doc_text = _text(doc_text)

        # ---- DOCUMENT_UNRESOLVED_PLACEHOLDER ------------------------------
        if doc_text:
            hits = [pattern for pattern in constants.PLACEHOLDER_PATTERNS if pattern and pattern in doc_text]
            for pattern in hits:
                position = doc_text.find(pattern)
                context = doc_text[max(0, position - 20) : position + len(pattern) + 20]
                context = _WS_RE.sub(" ", context).strip()
                items.append(
                    CheckItem(
                        category="format",
                        severity="blocking",
                        code="DOCUMENT_UNRESOLVED_PLACEHOLDER",
                        title="文稿仍含未替换的模板占位符",
                        detail=(
                            f"{doc_label}（id={doc_id}）正文含占位符“{pattern}”，"
                            f"上下文“…{context}…”；占位符残留说明模板字段未填满（R-33/R-46）。"
                        ),
                        target_type="document",
                        target_id=doc_id,
                        source={
                            "document_id": doc_id,
                            "doc_type": _text(document.get("doc_type")),
                            "version_no": _as_int(document.get("version_no")),
                            "pattern": pattern,
                            "context": context,
                        },
                        suggestion="补齐对应字段后重新生成文稿；确认该占位符应删除时修改模板，不得手工覆盖已审核文稿。",
                    )
                )

        # ---- DOCUMENT_DRAFT_MARKER ---------------------------------------
        if doc_status == "pending_issue" and doc_text and constants.DRAFT_MARKER in doc_text:
            position = doc_text.find(constants.DRAFT_MARKER)
            context = doc_text[max(0, position - 20) : position + len(constants.DRAFT_MARKER) + 20]
            context = _WS_RE.sub(" ", context).strip()
            count = doc_text.count(constants.DRAFT_MARKER)
            items.append(
                CheckItem(
                    category="format",
                    severity="blocking",
                    code="DOCUMENT_DRAFT_MARKER",
                    title="待签发稿仍保留草稿待核对标记",
                    detail=(
                        f"{doc_label}（id={doc_id}）状态为“待签发稿”，"
                        f"正文仍出现 {count} 处草稿标记“{constants.DRAFT_MARKER}”，"
                        f"例如“…{context}…”；进入待签发稿前必须全部处理（R-46）。"
                    ),
                    target_type="document",
                    target_id=doc_id,
                    source={
                        "document_id": doc_id,
                        "doc_type": _text(document.get("doc_type")),
                        "version_no": _as_int(document.get("version_no")),
                        "doc_status": doc_status,
                        "draft_marker": constants.DRAFT_MARKER,
                        "count": count,
                        "context": context,
                    },
                    suggestion="处理或豁免对应疑点后重新生成文稿，确认正文不再出现草稿标记。",
                )
            )

        # ---- DOCUMENT_PDF_MISSING ----------------------------------------
        if not _has_text(document.get("pdf_path")):
            items.append(
                CheckItem(
                    category="format",
                    severity="warning",
                    code="DOCUMENT_PDF_MISSING",
                    title="文稿缺少 PDF 文件",
                    detail=(
                        f"{doc_label}（id={doc_id}）的 pdf_path 为空，"
                        f"渲染引擎为“{_text(document.get('render_engine')) or '未记录'}”，"
                        "可能是 LibreOffice 不可用或导出失败（R-50/R-51）。"
                    ),
                    target_type="document",
                    target_id=doc_id,
                    source={
                        "document_id": doc_id,
                        "doc_type": _text(document.get("doc_type")),
                        "version_no": _as_int(document.get("version_no")),
                        "docx_path": _text(document.get("docx_path")),
                        "render_engine": _text(document.get("render_engine")),
                    },
                    suggestion="确认本机 LibreOffice 可用后重新导出 PDF；仍失败时在清单中记录原因并告知交付限制。",
                )
            )

        # ---- DOCUMENT_TEXT_DIVERGED --------------------------------------
        # 只在“生成时快照”与“当前文稿文本”都可用时比较：检测文稿被系统外改动。
        if _has_text(rendered_snapshot) and _has_text(doc_text):
            left = _norm_text(rendered_snapshot)
            right = _norm_text(doc_text)
            if left != right:
                matcher = difflib.SequenceMatcher(None, left, right)
                ratio = matcher.ratio()
                sample: List[str] = []
                for tag, i1, i2, j1, j2 in matcher.get_opcodes():
                    if tag == "equal":
                        continue
                    sample.append(f"生成时“{left[i1:i2][:30]}”→当前“{right[j1:j2][:30]}”")
                    if len(sample) >= 3:
                        break
                items.append(
                    CheckItem(
                        category="format",
                        severity="warning",
                        code="DOCUMENT_TEXT_DIVERGED",
                        title="文稿文本与生成时不一致",
                        detail=(
                            f"{doc_label}（id={doc_id}）的当前文本与生成时保存的快照在归一化空白后仍不同"
                            f"（相似度 {ratio:.2f}）；差异示例：{'；'.join(sample) or '（无法定位片段）'}。"
                        ),
                        target_type="document",
                        target_id=doc_id,
                        source={
                            "document_id": doc_id,
                            "doc_type": _text(document.get("doc_type")),
                            "version_no": _as_int(document.get("version_no")),
                            "similarity": round(ratio, 4),
                            "snapshot_length": len(left),
                            "stored_length": len(right),
                            "diff_sample": sample,
                        },
                        suggestion="确认文稿是否被系统外改动过；如属正常填充请重新生成文稿以刷新快照。",
                    )
                )

    # documents 为空时，退化为对传入文本做占位符/草稿标记检查。
    # 注意：只要传入了 stored_text 就执行，否则「单文档 + pdf_path 为空」的
    # 早退会连占位符/草稿标记一起漏掉。
    if not documents:
        for pattern in constants.PLACEHOLDER_PATTERNS:
            if pattern and pattern in stored_text:
                position = stored_text.find(pattern)
                context = _WS_RE.sub(" ", stored_text[max(0, position - 20) : position + len(pattern) + 20]).strip()
                items.append(
                    CheckItem(
                        category="format",
                        severity="blocking",
                        code="DOCUMENT_UNRESOLVED_PLACEHOLDER",
                        title="文稿仍含未替换的模板占位符",
                        detail=f"传入的文稿文本含占位符“{pattern}”，上下文“…{context}…”（R-33/R-46）。",
                        target_type="document",
                        target_id=0,
                        source={"pattern": pattern, "context": context, "document_id": 0},
                        suggestion="补齐对应字段后重新生成文稿。",
                    )
                )
        case_status = _text((case_data or {}).get("doc_status")).strip() if isinstance(case_data, Mapping) else ""
        if case_status == "pending_issue" and constants.DRAFT_MARKER in stored_text:
            items.append(
                CheckItem(
                    category="format",
                    severity="blocking",
                    code="DOCUMENT_DRAFT_MARKER",
                    title="待签发稿仍保留草稿待核对标记",
                    detail=(
                        f"案件状态为“待签发稿”，传入文稿文本仍含草稿标记“{constants.DRAFT_MARKER}”，"
                        f"共 {stored_text.count(constants.DRAFT_MARKER)} 处（R-46）。"
                    ),
                    target_type="document",
                    target_id=0,
                    source={"draft_marker": constants.DRAFT_MARKER, "document_id": 0, "case_id": case_id},
                    suggestion="处理或豁免对应疑点后重新生成文稿。",
                )
            )

    # ---- PHOTO_FILE_MISSING ----------------------------------------------
    # R-56/N-12：已指派位置但原件文件不存在的照片，渲染时会被跳过，栏位回落到占位文字。
    slot_photos = _photo_slot_photos(case_data)
    extra_keys = [key for key in slot_photos if key not in constants.PHOTO_POSITION_INDEX]
    reported: set = set()
    missing_index = 0
    for key in list(constants.PHOTO_POSITION_KEYS) + extra_keys:
        photos = slot_photos.get(key) or []
        label = _photo_slot_label(key, photos)
        for photo in photos:
            if _file_state(case_data, photo.get("path")) is not False:
                continue
            path = _text(photo.get("path")).strip()
            material_id = _as_int(photo.get("material_id"))
            missing_index += 1
            target_id = material_id or (100000 + missing_index)
            if (target_id, path) in reported:
                continue
            reported.add((target_id, path))
            items.append(
                CheckItem(
                    category="format",
                    severity="warning",
                    code="PHOTO_FILE_MISSING",
                    title="部位照片原件文件不存在",
                    detail=(
                        f"栏位“{label}”（{key}）的照片"
                        + (f"材料 {material_id}（{photo.get('filename') or path}）" if material_id else f"“{path}”")
                        + "在原位不存在，生成文稿时会被跳过，该栏位正文写"
                        f"“{constants.PHOTO_PLACEHOLDER_TEXT}”（R-56/N-12）。"
                    ),
                    target_type="material",
                    target_id=target_id,
                    source={
                        "position_key": key,
                        "position_label": label,
                        "material_id": material_id,
                        "filename": _text(photo.get("filename")).strip(),
                        "path": path,
                        "exists": False,
                        "placeholder_text": constants.PHOTO_PLACEHOLDER_TEXT,
                    },
                    suggestion=(
                        "确认该照片原件是否已被移动或删除；补回原件后重新生成文稿，"
                        "或在照片位置指派界面重新指派可用照片。"
                    ),
                )
            )

    # ---- PHOTO_SLOT_EMPTY -------------------------------------------------
    # N-12：模板显式声明了该照片栏位而本次没有照片 → 正文必须写占位文字，
    # 保留栏位、不得用其他栏位的照片顶替、不得写“未见异常”。
    for key in _declared_photo_keys(case_data, template_text):
        photos = slot_photos.get(key) or []
        usable = [
            photo for photo in photos if _file_state(case_data, photo.get("path")) is not False
        ]
        if usable:
            continue
        label = constants.PHOTO_POSITION_LABELS.get(key, key)
        items.append(
            CheckItem(
                category="format",
                severity="info",
                code="PHOTO_SLOT_EMPTY",
                title="照片栏位本次没有照片",
                detail=(
                    f"模板声明了照片栏位“{label}”（{key}），但本次没有可用照片"
                    f"（指派数量 {len(photos)}）；正文保留该栏位并写"
                    f"“{constants.PHOTO_PLACEHOLDER_TEXT}”，不得删除栏位、不得借用其他栏位照片、"
                    "不得写成“未见异常”（N-12）。"
                ),
                target_type="photo_slot",
                target_id=_photo_slot_target_id(key),
                source={
                    "position_key": key,
                    "position_label": label,
                    "assigned_count": len(photos),
                    "wide": bool(getattr(constants.PHOTO_POSITION_INDEX.get(key), "wide", False)),
                    "placeholder_text": constants.PHOTO_PLACEHOLDER_TEXT,
                },
                suggestion=(
                    "如本案确有该部位照片，请上传并指派到该栏位；确实没有照片时保留占位文字，"
                    "并在清单中注明“本次未提供该部位照片”。"
                ),
            )
        )

    # ---- PHOTO_SLOT_MISSING -----------------------------------------------
    # 与 `PHOTO_SLOT_EMPTY` 的分界（两者绝不可混用）：
    #   * `PHOTO_SLOT_EMPTY`（info）：**模板声明了**该栏位，本次没有照片 → 正文写占位文字；
    #   * `PHOTO_SLOT_MISSING`（warning）：**案件里有已指派位置的照片，但文稿里根本没有
    #     这个栏位**（模板没声明该栏位的 `{{ photo_<key> }}` 锚点，渲染产物里也找不到
    #     该栏位的标题行）→ 照片进不了文稿。
    # 系统默认会在渲染时向**工作副本**追加标准「部位照片」区（`docgen.append_photo_section`，
    # 模板原件不改动，N-8），此时栏位标题行会出现在文稿正文里，本 code **不再产出**；
    # 只有当追加没有发生（生成失败、追加异常、模板与输出同一路径）或模板只声明了**别的**
    # 栏位时才会命中——也就是照片确实无处安放的情形。
    layout_texts = _rendered_texts(case_data, stored_text, rendered_snapshot)
    declared_keys = _declared_photo_keys(case_data, template_text)
    for key in constants.PHOTO_POSITION_KEYS:
        if key in declared_keys:
            continue
        photos = [
            photo
            for photo in (slot_photos.get(key) or [])
            if _file_state(case_data, photo.get("path")) is not False
        ]
        if not photos:
            continue
        titles = _photo_slot_title_texts(key, photos)
        if any(title in text for title in titles for text in layout_texts):
            # 该栏位确实出现在文稿正文里（例如系统自动追加标准照片区后渲染）→ 不报。
            continue
        label = _photo_slot_label(key, photos)
        material_ids = sorted({photo["material_id"] for photo in photos if photo.get("material_id")})
        filenames = [photo["filename"] for photo in photos if photo.get("filename")]
        items.append(
            CheckItem(
                category="format",
                severity="warning",
                code="PHOTO_SLOT_MISSING",
                title="文稿里没有该照片栏位，照片无法插入",
                detail=(
                    f"本案有 {len(photos)} 张已指派到“{label}”（{key}）的照片"
                    + (f"（材料 {', '.join(str(value) for value in material_ids)}）"
                       if material_ids else "")
                    + (f"（{('、'.join(filenames))[:60]}）" if filenames else "")
                    + "，但模板没有声明任何照片栏位锚点、渲染产物里也没有该栏位："
                    "这些照片不会被插入文稿，请在模板中加入 "
                    "{{ photo_" + key + " }} 栏位或让系统自动追加标准「部位照片」区"
                    "（自动追加只改渲染用的工作副本，模板原件不改动，N-8）。"
                ),
                target_type="photo_slot",
                target_id=_photo_slot_target_id(key),
                source={
                    "position_key": key,
                    "position_label": label,
                    "photo_count": len(photos),
                    "material_ids": material_ids,
                    "filenames": filenames,
                    "declared_slot_keys": list(declared_keys),
                    "anchor": constants.PHOTO_POSITION_ANCHORS.get(key, f"photo_{key}"),
                    "checked_texts": len(layout_texts),
                    "reason": "no_photo_anchor_in_template_and_no_slot_in_document",
                },
                suggestion=(
                    "在模板里加入 {{ photo_" + key + " }} 独立段落锚点"
                    "（或任意 {{ photo_* }} 锚点，让系统按标准 18 个栏位自动追加照片区），"
                    "然后重新生成文稿；不要手工改动已生成的文稿。"
                ),
            )
        )

    return items


# --------------------------------------------------------------------------
# correction —— 修改可靠性
# --------------------------------------------------------------------------


def check_corrections(case_data: Mapping[str, Any]) -> List[CheckItem]:
    """修改可靠性检查：未应用修正、作用范围不明、复核被打回。"""
    items: List[CheckItem] = []

    # ---- CORRECTION_UNAPPLIED / CORRECTION_SCOPE_UNCLEAR -----------------
    for correction in _rows(case_data, "corrections"):
        correction_id = _as_int(correction.get("id"))
        kind = _text(correction.get("kind")).strip()
        kind_label = constants.CORRECTION_KIND_LABELS.get(kind, kind or "未分类")
        target_type = _text(correction.get("target_type")).strip()
        target_id = _as_int(correction.get("target_id"))
        scope = _text(correction.get("scope")).strip()
        scope_value = _text(correction.get("scope_value")).strip()

        if _as_int(correction.get("applied")) == 0:
            items.append(
                CheckItem(
                    category="correction",
                    severity="warning",
                    code="CORRECTION_UNAPPLIED",
                    title="修正记录尚未应用",
                    detail=(
                        f"修正 #{correction_id}（{kind_label}，目标 {target_type or '未记录'}"
                        f"#{target_id}）的 applied=0，"
                        f"“{_text(correction.get('before_text'))[:30]}”→“{_text(correction.get('after_text'))[:30]}”"
                        "尚未落到案件数据上（R-38/R-40）。"
                    ),
                    target_type="correction",
                    target_id=correction_id,
                    source={
                        "correction_id": correction_id,
                        "kind": kind,
                        "target_type": target_type,
                        "target_id": target_id,
                        "before_text": _text(correction.get("before_text")),
                        "after_text": _text(correction.get("after_text")),
                        "reason": _text(correction.get("reason")),
                        "applied": _as_int(correction.get("applied")),
                    },
                    suggestion="应用该修正并失效依赖它的旧复核状态；确不需要时删除或注明放弃理由。",
                )
            )

        if scope and scope != "case_only" and not scope_value:
            scope_label = constants.CORRECTION_SCOPE_LABELS.get(scope, scope)
            items.append(
                CheckItem(
                    category="correction",
                    severity="warning",
                    code="CORRECTION_SCOPE_UNCLEAR",
                    title="修正作用范围未写明具体对象",
                    detail=(
                        f"修正 #{correction_id}（{kind_label}）的 scope 为“{scope_label}”（{scope}），"
                        "但 scope_value 为空，无法判断该规则适用于哪些栏目、书写者或模板（R-39/INV-7）。"
                    ),
                    target_type="correction",
                    target_id=correction_id,
                    source={
                        "correction_id": correction_id,
                        "kind": kind,
                        "scope": scope,
                        "scope_value": scope_value,
                        "target_type": target_type,
                        "target_id": target_id,
                    },
                    suggestion="填写具体的栏目名、记录栏目、书写者或模板版本；不确定时改为“仅当前案件”。",
                )
            )

    # ---- REVIEW_INVALIDATED ---------------------------------------------
    for row in _rows(case_data, "fields"):
        if _text(row.get("review_state")).strip() != "pending":
            continue
        rule_codes = [code for code in _seq(row.get("applied_rule_codes")) if _has_text(code)]
        rule_note = _text(row.get("rule_note")).strip()
        if not rule_codes and not rule_note:
            continue
        label = _field_label(row)
        key = _field_key(row)
        evidence: List[str] = []
        if rule_codes:
            evidence.append("曾应用术语规则 " + "、".join(_text(code) for code in rule_codes))
        if rule_note:
            evidence.append(f"规则说明“{rule_note}”")
        items.append(
            CheckItem(
                category="correction",
                severity="info",
                code="REVIEW_INVALIDATED",
                title="已确认字段被修正打回待核对",
                detail=(
                    f"字段“{label}”（{key}）的复核状态已回到“待核对”，且"
                    + "，".join(evidence)
                    + "；说明该字段的整理结果或依赖文稿已被修正失效（R-40/R-41）。"
                ),
                target_type="field",
                target_id=_as_int(row.get("id")),
                source={
                    "field_key": key,
                    "field_label": label,
                    "review_state": _text(row.get("review_state")),
                    "status": _text(row.get("status")),
                    "applied_rule_codes": [_text(code) for code in rule_codes],
                    "rule_note": rule_note,
                    "confirmed_value": _text(row.get("confirmed_value")),
                    "normalized_text": _text(row.get("normalized_text")),
                },
                suggestion="按新的修正结果重新核对并确认该字段，确认后重新生成文稿版本。",
            )
        )

    return items


# --------------------------------------------------------------------------
# 聚合
# --------------------------------------------------------------------------


def check_case(
    case_data: Mapping[str, Any],
    *,
    include_layout: bool = True,
    template_text: str = "",
    stored_text: str = "",
    rendered_snapshot: str = "",
) -> List[CheckItem]:
    """执行全部校核并聚合。

    顺序固定为 ``recognition → content → source → format → correction``；
    相同 ``(code, target_type, target_id)`` 只保留第一条。
    ``include_layout=False`` 时跳过 ``check_format``。
    ``template_text`` / ``stored_text`` / ``rendered_snapshot`` 语义见 :func:`check_format`。
    """
    items: List[CheckItem] = []
    items.extend(check_recognition(case_data))
    items.extend(check_content(case_data))
    items.extend(check_source(case_data))
    if include_layout:
        items.extend(check_format(case_data, template_text=template_text, stored_text=stored_text,
                                 rendered_snapshot=rendered_snapshot))
    items.extend(check_corrections(case_data))

    deduped: List[CheckItem] = []
    seen: set = set()
    for item in items:
        key = (item.code, item.target_type, item.target_id)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def has_blocking(items: Sequence[CheckItem]) -> bool:
    """是否存在阻断项（``severity == "blocking"``）。"""
    return any(getattr(item, "severity", None) == "blocking" for item in items or [])
