"""文书生成（docgen）：模板构建、上下文组装、模板渲染与 PDF 导出。

对应 `docs/architecture.md` 第 5.8 节。公开接口逐字冻结，见文末 `__all__`。

设计要点
--------
1. **原件不可覆盖（N-8）**：`render_document` 先把模板复制到 `output_path`，
   再在副本上渲染，模板文件本身只读不写。
2. **N-1 硬约束**：`build_context` 的 `opinion` 只取自 `case_data["opinion_text"]`，
   任何情况下都不从 OCR 原文、字段值或术语规则推导鉴定意见。
3. **N-5**：`status == "not_recorded"` 的字段值恒为“未记录”，绝不出“未见异常”。
4. **builtin 渲染器**：模板占位符常被 Word 拆到多个 run，因此按“段落合并文本 → 渲染 →
   写回首个模板 run、清空其余模板 run”的方式处理；跨段落的 `{% for %}` / `{% if %}`
   按段落块用 `copy.deepcopy(p._p)` 复制后逐项渲染。
5. 渲染引擎：`docxtpl` 未安装（TODO-DL-1）时回退 `builtin`；`builtin` 是当前唯一可用路径。
6. **部位照片（N-11 / R-56 / N-12）**：模板中以 `{{ photo_<position_key> }}` 独立成段的
   锚点段落，被替换为“居中图片段落 + 小字号说明行”。渲染器**只读文件路径**（以及图片头部
   记录的像素尺寸/DPI 用于排版算宽），**不检查、不分析、不推断图片像素内容**；栏位没有
   可用照片时保留栏位并写入 `PHOTO_PLACEHOLDER_TEXT`（“（未提供照片）”），绝不挪用其它
   栏位的照片、绝不写“未见异常”。
7. **照片栏位兜底（默认行为，R-56 / N-8 / N-12）**：用户自建模板常常没有任何
   `{{ photo_* }}` 锚点，此时照片过去会被**静默丢弃**（`inline_shapes = 0`、`notes` 里也
   没有说明）。现在 `render_document` 先复制模板到**工作副本**，若副本里没有任何
   `{{ photo_` 锚点、而 `context["photo_slots"]` / `context["photos"]` 里确实有“已指派
   位置”的照片，就向**工作副本**追加标准「部位照片」区（18 个栏位，见
   `append_photo_section`）再渲染；模板原件一个字节都不改（N-8）。追加发生、无法追加
   （模板与输出同一路径）或追加失败时，都在 `RenderResult.notes` 里写明中文说明，
   绝不静默。相应地，`checker.PHOTO_SLOT_MISSING` 只对“照片确实无处安放”的栏位产出。

已知限制（本文件顶部注释即声明，勿在别处重复声明）
--------------------------------------------------
- **TODO-DL-1**：未安装 `docxtpl`，`engine="docxtpl"` 会抛 `DocgenUnavailable`。
- **表格行级循环不支持**：`{% for %}` 只支持“段落块”与“单元格内段落”两种粒度。
  整行重复（`copy.deepcopy(row._tr)`）未实现；如模板需要整行重复，请改用
  “单元格内段落循环 + 行内 `{% if %}`”表达，或安装 `docxtpl` 后走 `docxtpl` 引擎。
- **单段落内联块**（同一段落里同时出现 `{% for %}` 与 `{% endfor %}`）只展开为
  一个段落，不拆分成多段；需要逐条成段时请把标签分行书写（演示模板即如此）。
- `{% if %}` 的 `elif` 不单独支持（按普通内容处理）；请用嵌套 `{% if %}` 表达。
"""

from __future__ import annotations

import copy
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from docx import Document
from docx.document import Document as DocxDocument
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt
from docx.table import Table
from docx.text.paragraph import Paragraph
from docx.text.run import Run

try:  # 仅用于读取图片头部的像素尺寸 / DPI 做排版算宽，绝不读取像素内容（N-11）
    from PIL import Image as _PILImage
except Exception:  # pragma: no cover - Pillow 缺失时退化为 python-docx 自带尺寸
    _PILImage = None  # type: ignore[assignment]

try:  # Jinja2 是硬依赖，但仍给出可诊断的失败信息
    import jinja2
    from jinja2 import ChainableUndefined
    from jinja2 import Environment
    from jinja2 import Template
except Exception as exc:  # pragma: no cover - 环境缺库时才会触发
    jinja2 = None  # type: ignore[assignment]
    ChainableUndefined = None  # type: ignore[assignment]
    Environment = None  # type: ignore[assignment]
    Template = None  # type: ignore[assignment]
    _JINJA_IMPORT_ERROR: Optional[BaseException] = exc
else:
    _JINJA_IMPORT_ERROR = None

from . import constants as C

__all__ = [
    "DocgenUnavailable",
    "RenderResult",
    "docx_engine_status",
    "append_photo_section",
    "photo_slot_title",
    "template_photo_keys",
    "build_demo_template",
    "build_context",
    "resolve_template",
    "render_document",
    "detect_libreoffice",
    "export_pdf",
    "extract_document_text",
    "document_filename",
]


# --------------------------------------------------------------------------
# 一、异常与结果类型
# --------------------------------------------------------------------------


class DocgenUnavailable(RuntimeError):
    """渲染引擎不可用或渲染失败。调用方据此降级并记录校核项。"""


@dataclass
class RenderResult:
    output_path: str
    engine: str
    unresolved: List[str] = field(default_factory=list)
    paragraph_count: int = 0
    table_count: int = 0
    #: 渲染过程中的**中文说明**（如「模板未声明照片栏位，已按标准栏位追加照片区」）。
    #: 默认空列表，向后兼容；调用方（`quick.run_quick_pipeline`）应原样透出给用户，
    #: 让“系统做了什么”可见，而不是静默处理（R-59～R-63 主流程透明化）。
    notes: List[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# 二、常量
# --------------------------------------------------------------------------

#: 引擎标识
ENGINE_DOCXTPL = "docxtpl"
ENGINE_BUILTIN = "builtin"

#: 依赖下载待办编号（见 docs/TODO_DOWNLOADS.md）
TODO_DOCXTPL = "TODO-DL-1"

#: 模板渲染使用的 Jinja2 分隔符（与 PLACEHOLDER_PATTERNS 一致）
_VAR_OPEN = "{{"
_VAR_CLOSE = "}}"
_BLOCK_OPEN = "{%"
_BLOCK_CLOSE = "%}"
_COMMENT_OPEN = "{#"
_COMMENT_CLOSE = "#}"

_TEMPLATE_MARKERS: Tuple[str, ...] = (_VAR_OPEN, _BLOCK_OPEN, _COMMENT_OPEN)
#: 未解析锚点：只统计变量锚点，`{% %}` / `{# #}` 不算。
_UNRESOLVED_RE = re.compile(r"\{\{(.*?)\}\}", re.DOTALL)

#: `{% for ... %}` / `{% endfor %}` / `{% if ... %}` / `{% else %}` / `{% endif %}`
_FOR_RE = re.compile(r"^for\s+(?P<target>[A-Za-z_][A-Za-z0-9_]*)\s+in\s+(?P<expr>.+)$", re.DOTALL)
_IF_RE = re.compile(r"^if\s+(?P<expr>.+)$", re.DOTALL)

#: 单个容器内块处理的最大轮次，防止异常模板导致死循环。
_MAX_BLOCK_ROUNDS = 4000

#: 导出 PDF 时 soffice 转换出的临时文件名前缀（避免污染同名交付文件）。
_PDF_TMP_PREFIX = "._docgen_tmp_"

#: 照片栏位锚点：段落整段（去掉前后空白）恰为 `{{ photo_<position_key> }}`。
_PHOTO_ANCHOR_RE = re.compile(r"^\s*\{\{\s*photo_(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*\}\}\s*$")

#: 段落内任意位置出现的照片栏位锚点（用于发现“未按约定独立成段”的锚点）。
_PHOTO_ANCHOR_ANY_RE = re.compile(r"\{\{\s*photo_(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*\}\}")

#: 标准「部位照片」区的标题行（`append_photo_section` 写入）。
#: checker 用它判断“栏位是否真的进入了文稿”——「照片栏位」这四个字是本模块与
#: `checker.PHOTO_SLOT_MISSING` 之间的**唯一**文本约定，改动时必须同步。
PHOTO_SECTION_HEADING = "部位照片"

#: 标准区的栏位标题行前缀：`照片栏位：<中文栏位名>`（宽幅栏位追加“（宽幅）”）。
PHOTO_SLOT_TITLE_PREFIX = "照片栏位："

#: 标准区的说明行（N-11：明确系统不识别照片内容）。
PHOTO_SECTION_NOTE = (
    "（以下照片按送检时逐张指定的部位栏位插入；系统不识别、不描述照片内容，"
    "也不对其运行文字识别。）"
)

#: 图片头部没有 DPI 记录时的缺省值（px → cm 换算用）。
_DEFAULT_IMAGE_DPI = 96.0

#: 照片说明行字号（磅）。说明行使用小字号，避免喧宾夺主。
_PHOTO_CAPTION_PT = 9.0

#: N-5 / R-35：栏目空白（模板声明了该栏目但本次没有字段行，或字段值本身为空）
#: 在正文中的**唯一**写法；绝不写成“未见异常”，也绝不编造数值。
_NOT_RECORDED_TEXT = "未记录"

#: N-5（明确未检）：`status == "not_examined"` 且三处取值皆空时的兜底写法。
#: 正常路径下 `terminology.py` 已把 `normalized_text` 写成“未进行该项检查：原文”。
_NOT_EXAMINED_TEXT = "未进行该项检查"

#: D15：判断“字段值本身已含栏目标签”时允许出现的分隔符（全角/半角冒号与空白）。
_LABEL_SEPARATORS: Tuple[str, ...] = ("：", ":", "　", " ")

#: D15：结构明确的标量行——`<栏目名>：{{ <字段键> }}`（栏目名不含冒号/花括号/空白）。
_SCALAR_LABEL_ANCHOR_RE = re.compile(
    r"^\s*(?P<label>[^：:{}\s]{1,12})\s*[：:]\s*\{\{\s*(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*\}\}"
)


# --------------------------------------------------------------------------
# 三、docx 遍历工具
# --------------------------------------------------------------------------


def _container_element(container: Any) -> Any:
    """取容器的 lxml 元素：文档取 `w:body`，表格单元格取 `w:tc`。

    `Document._element` 是 `w:document`（其子节点是 `w:body`），直接对 `_element`
    取子节点会漏掉正文段落与表格，因此这里必须区分文档与单元格。
    """
    try:
        return container.element.body
    except AttributeError:
        return container._element


def _container_of(element: Any, document: DocxDocument) -> Any:
    """把 lxml 元素还原为 python-docx 段落对象（找不到时返回 None）。"""
    try:
        return Paragraph(element, document)
    except Exception:
        return None


def _iter_paragraphs(container: Any) -> Iterable[Paragraph]:
    """按文档顺序遍历容器内的全部段落（递归进入表格单元格与嵌套表格）。"""
    body = _container_element(container)
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            paragraph = _container_of(child, container)
            if paragraph is not None:
                yield paragraph
        elif child.tag == qn("w:tbl"):
            try:
                table = Table(child, container)
            except Exception:
                continue
            for row in table.rows:
                for cell in row.cells:
                    for nested in _iter_paragraphs(cell):
                        yield nested


def _iter_tables(container: Any) -> Iterable[Table]:
    """按文档顺序遍历容器内的全部表格（含嵌套表格）。"""
    body = _container_element(container)
    for child in body.iterchildren():
        if child.tag != qn("w:tbl"):
            continue
        try:
            table = Table(child, container)
        except Exception:
            continue
        yield table
        for row in table.rows:
            for cell in row.cells:
                for nested in _iter_tables(cell):
                    yield nested


def _iter_all_paragraphs(document: DocxDocument) -> Iterable[Paragraph]:
    """按文档顺序遍历正文、表格单元格与嵌套表格内的全部段落。"""
    return _iter_paragraphs(document)


def _iter_table_containers(container: Any) -> Iterable[Any]:
    """遍历容器内所有“含段落序列”的容器：正文自身 + 每个表格单元格。"""
    yield container
    for child in _container_element(container).iterchildren():
        if child.tag == qn("w:tbl"):
            try:
                table = Table(child, container)
            except Exception:
                continue
            for row in table.rows:
                for cell in row.cells:
                    for nested in _iter_table_containers(cell):
                        yield nested


def _container_paragraphs(container: Any) -> List[Paragraph]:
    """只取容器**直接**持有的段落（不下钻嵌套表格），顺序即文档顺序。"""
    result: List[Paragraph] = []
    for child in _container_element(container).iterchildren():
        if child.tag == qn("w:p"):
            paragraph = _container_of(child, container)
            if paragraph is not None:
                result.append(paragraph)
    return result


def _count_paragraphs(document: DocxDocument) -> int:
    return sum(1 for _ in _iter_all_paragraphs(document))


def _count_tables(document: DocxDocument) -> int:
    return sum(1 for _ in _iter_tables(document))


# --------------------------------------------------------------------------
# 四、run 级文本处理
# --------------------------------------------------------------------------


def _runs(paragraph: Paragraph) -> List[Run]:
    try:
        return list(paragraph.runs)
    except Exception:
        return []


def _paragraph_text(paragraph: Paragraph) -> str:
    """段落全文：所有 run 的 text 拼接（占位符常被 Word 拆到多个 run）。"""
    try:
        return "".join(run.text or "" for run in paragraph.runs)
    except Exception:
        return ""


def _contains_marker(text: str) -> bool:
    return any(marker in text for marker in _TEMPLATE_MARKERS)


def _is_pure_block(text: str) -> bool:
    """段落是否只由 `{% %}` / `{# #}` 构成（这类段落不参与段落级渲染）。"""
    stripped = text.strip()
    if not stripped:
        return False
    stripped = re.sub(r"\{[%#].*?[%#]\}", "", stripped, flags=re.DOTALL).strip()
    return stripped == ""


def _photo_anchor_key(text: str) -> str:
    """若段落（去掉前后空白后）恰为已知照片栏位锚点，返回该 `position_key`。

    只认 `constants.PHOTO_POSITION_KEYS` 目录内的 key；其它情形（key 不在目录，
    或锚点没有独立成段）由 `_render_single_paragraph` 保留原文并作为未解析锚点上报，
    绝不静默清空。
    """
    match = _PHOTO_ANCHOR_RE.match(text or "")
    if not match:
        return ""
    key = match.group("key")
    return key if key in C.PHOTO_POSITION_KEYS else ""


def _photo_anchor_keys(text: str) -> List[str]:
    """段落内出现的全部 `{{ photo_<key> }}`（不论位置与 key 是否在目录中）。"""
    return [match.group("key") for match in _PHOTO_ANCHOR_ANY_RE.finditer(text or "")]


def template_photo_keys(text: str) -> List[str]:
    """模板文本里**声明**的照片栏位 key：只认位置目录内的 key，按出现顺序去重。

    供 `quick.run_quick_pipeline` 判断“模板是否自己声明了照片栏位”（从而决定要不要
    说明系统追加了标准照片区）。只读文本，不打开任何图片（N-11）。
    """
    keys: List[str] = []
    for key in _photo_anchor_keys(text):
        if key in C.PHOTO_POSITION_INDEX and key not in keys:
            keys.append(key)
    return keys


def _working_copy_photo_anchors(document: DocxDocument) -> List[str]:
    """扫描**工作副本**里出现的全部 `{{ photo_* }}` 锚点（去重、保持顺序）。

    刻意比 `_photo_anchor_key()` 宽松：只要副本里出现过任何 `{{ photo_` 锚点（哪怕
    没有独立成段、或 key 不在位置目录中），就认为模板**自己**声明了照片区，不再追加
    标准区——否则模板里已有的锚点会与追加的 18 个标准栏位重复。
    """
    found: List[str] = []
    for paragraph in _iter_all_paragraphs(document):
        for key in _photo_anchor_keys(_paragraph_text(paragraph)):
            if key not in found:
                found.append(key)
    return found


def _clear_run(run: Run) -> None:
    """清空 run 的文本，但保留 run 本身（保留格式与可能的图形对象）。"""
    if run.text:
        run.text = ""


def _assign_text(paragraph: Paragraph, text: str) -> None:
    """把渲染结果写回段落：保留段落格式，把全部文本放入首个 run。"""
    runs = _runs(paragraph)
    if not runs:
        try:
            paragraph.add_run(text)
        except Exception:
            pass
        return
    runs[0].text = text
    for run in runs[1:]:
        _clear_run(run)


def _write_rendered(paragraph: Paragraph, text: str, start_run: int, end_run: int) -> None:
    """把模板区间 `[start_run, end_run)` 的渲染结果写回该区间首个 run。"""
    runs = _runs(paragraph)
    if not runs:
        try:
            paragraph.add_run(text)
        except Exception:
            pass
        return
    start = max(0, min(start_run, len(runs) - 1))
    end = max(start + 1, min(end_run, len(runs)))
    runs[start].text = text
    for run in runs[start + 1:end]:
        _clear_run(run)


def _template_run_span(paragraph: Paragraph) -> Optional[Tuple[int, int]]:
    """返回段落中包含模板标记的 run 区间 `[start, end)`；无标记时返回 None。"""
    runs = _runs(paragraph)
    if not runs:
        return None
    starts: List[int] = []
    for index, run in enumerate(runs):
        if _contains_marker(run.text or ""):
            starts.append(index)
    if not starts:
        return None
    return (starts[0], starts[-1] + 1)


# --------------------------------------------------------------------------
# 五、Jinja2 环境
# --------------------------------------------------------------------------


def _require_jinja() -> Any:
    if jinja2 is None or Environment is None:
        raise DocgenUnavailable(
            f"Jinja2 不可用（builtin 渲染器依赖 Jinja2）：{_JINJA_IMPORT_ERROR!r}"
        )
    return jinja2


def _build_env() -> Any:
    """构造渲染环境；`undefined=ChainableUndefined` 保证缺 key 不报 UndefinedError。"""
    _require_jinja()
    try:
        return Environment(
            autoescape=False,
            undefined=ChainableUndefined,
            trim_blocks=False,
            lstrip_blocks=False,
            keep_trailing_newline=True,
        )
    except Exception as exc:  # pragma: no cover - 仅在 Jinja2 异常损坏时触发
        raise DocgenUnavailable(f"Jinja2 环境初始化失败：{exc!r}") from exc


def _excerpt(text: str, limit: int = 120) -> str:
    flat = " ".join((text or "").split())
    if len(flat) <= limit:
        return flat
    return flat[:limit] + "…"


def _render_text(env: Any, text: str, context: Mapping[str, Any], where: str) -> str:
    """渲染一段文本；任何 Jinja2 异常都转成带段落片段的 DocgenUnavailable。"""
    if not text:
        return ""
    try:
        template = env.from_string(text)
        return template.render(dict(context))
    except DocgenUnavailable:
        raise
    except Exception as exc:
        raise DocgenUnavailable(
            f"Jinja2 渲染失败（{where}）：{type(exc).__name__}: {exc}；段落文本片段："
            f"{_excerpt(text)!r}"
        ) from exc


def _resolve_names(env: Any, expr: str, context: Mapping[str, Any], where: str) -> Dict[str, Any]:
    """把 `{% for %}` / `{% if %}` 表达式中的顶层变量名解析为实际对象。"""
    try:
        ast = env.parse("{{ %s }}" % expr)
    except Exception as exc:
        raise DocgenUnavailable(
            f"模板表达式无法解析（{where}）：{expr!r}；{type(exc).__name__}: {exc}"
        ) from exc
    data = dict(context)
    names: Dict[str, Any] = {}
    try:
        from jinja2 import nodes
        from jinja2.visitor import NodeVisitor

        class _NameCollector(NodeVisitor):
            def __init__(self) -> None:
                self.found: List[str] = []

            def visit_Name(self, node: Any, *args: Any, **kwargs: Any) -> None:
                if node.name in data and node.name not in self.found:
                    self.found.append(node.name)

        collector = _NameCollector()
        collector.visit(ast)
        for name in collector.found:
            names[name] = data[name]
    except DocgenUnavailable:
        raise
    except Exception:
        # 静态解析失败时退化为“按顶层变量名逐个尝试”，保证仍能渲染。
        for name in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", expr):
            if name in data:
                names[name] = data[name]
    return names


def _eval_condition(env: Any, expr: str, context: Mapping[str, Any], where: str) -> bool:
    names = _resolve_names(env, expr, context, where)
    try:
        ast = env.compile_expression(expr, undefined_to_none=False)
        value = ast(**names)
    except DocgenUnavailable:
        raise
    except Exception as exc:
        raise DocgenUnavailable(
            f"模板条件表达式求值失败（{where}）：{expr!r}；{type(exc).__name__}: {exc}"
        ) from exc
    if value is ChainableUndefined or isinstance(value, getattr(jinja2, "Undefined", ())):
        return False
    return bool(value)


def _eval_iterable(
    env: Any, expr: str, context: Mapping[str, Any], where: str
) -> Tuple[bool, List[Any]]:
    """返回 (表达式是否求值成功, 元素列表)。失败时调用方删除整块。"""
    names = _resolve_names(env, expr, context, where)
    try:
        ast = env.compile_expression(expr, undefined_to_none=False)
        value = ast(**names)
    except Exception:
        return (False, [])
    if value is None or value is ChainableUndefined:
        return (True, [])
    if isinstance(value, getattr(jinja2, "Undefined", ())):
        return (True, [])
    if isinstance(value, (str, bytes, Mapping)):
        return (False, [])
    try:
        return (True, list(value))
    except TypeError:
        return (False, [])


# --------------------------------------------------------------------------
# 六、块扫描与块处理（跨段落 `{% for %}` / `{% if %}`）
# --------------------------------------------------------------------------


@dataclass
class _BlockTag:
    kind: str          # "for" | "endfor" | "if" | "else" | "endif"
    expr: str          # for/if 的表达式，其余为空
    target: str        # for 的循环变量名
    index: int         # 该标签在所属段落内的序号
    start: int         # 标签在段落文本中的起始偏移
    end: int           # 标签在段落文本中的结束偏移


@dataclass
class _Block:
    kind: str          # "for" | "if"
    expr: str
    target: str
    start_para: int
    start_tag_index: int
    start_text: str
    start_span: Tuple[int, int]
    end_para: int
    end_tag_index: int
    end_text: str
    end_span: Tuple[int, int]
    else_para: Optional[int] = None
    else_tag_index: Optional[int] = None
    else_text: str = ""
    else_span: Optional[Tuple[int, int]] = None

    @property
    def inline(self) -> bool:
        """标签是否落在同一段落（同一段落内联块）。"""
        return self.start_para == self.end_para


def _parse_tags(text: str) -> Tuple[List[_BlockTag], bool]:
    """扫描一段文本里的 `{% %}` 标签；返回 (标签列表, 是否含未配对标记)。"""
    tags: List[_BlockTag] = []
    in_raw = False
    index = 0
    position = 0
    length = len(text)
    while position < length:
        var_at = text.find(_VAR_OPEN, position)
        block_at = text.find(_BLOCK_OPEN, position)
        comment_at = text.find(_COMMENT_OPEN, position)
        candidates = [item for item in (var_at, block_at, comment_at) if item != -1]
        if not candidates:
            break
        found = min(candidates)
        if found == comment_at and comment_at != block_at:
            close = text.find(_COMMENT_CLOSE, comment_at + 2)
            if close == -1:
                break
            position = close + 2
            continue
        if found == block_at:
            close = text.find(_BLOCK_CLOSE, block_at + 2)
            if close == -1:
                return (tags, True)
            raw = text[block_at + 2:close].strip()
            position = close + 2
            lowered = raw.lower()
            if lowered.startswith("raw"):
                in_raw = True
                continue
            if lowered.startswith("endraw"):
                in_raw = False
                continue
            if in_raw:
                continue
            kind = ""
            expr = ""
            target = ""
            if lowered.startswith("endfor"):
                kind = "endfor"
            elif lowered.startswith("endif"):
                kind = "endif"
            elif lowered.startswith("else"):
                kind = "else"
            else:
                match_for = _FOR_RE.match(raw)
                match_if = _IF_RE.match(raw)
                if match_for:
                    kind = "for"
                    target = match_for.group("target")
                    expr = match_for.group("expr").strip()
                elif match_if:
                    kind = "if"
                    expr = match_if.group("expr").strip()
            if kind:
                tags.append(
                    _BlockTag(
                        kind=kind,
                        expr=expr,
                        target=target,
                        index=index,
                        start=block_at,
                        end=close + 2,
                    )
                )
                index += 1
            continue
        # 变量锚点：跳过
        close = text.find(_VAR_CLOSE, var_at + 2)
        if close == -1:
            break
        position = close + 2
    return (tags, False)


def _scan_blocks(paragraphs: Sequence[Paragraph]) -> List[_Block]:
    """在段落序列上扫描块结构（不处理，只描述位置）。"""
    blocks: List[_Block] = []
    stack: List[_Block] = []
    for para_index, paragraph in enumerate(paragraphs):
        text = _paragraph_text(paragraph)
        if not text or _BLOCK_OPEN not in text:
            continue
        tags, malformed = _parse_tags(text)
        if malformed:
            raise DocgenUnavailable(
                f"模板块标记不完整（`{{%` 缺少 `%}}`），段落文本片段：{_excerpt(text)!r}"
            )
        for tag in tags:
            if tag.kind in ("for", "if"):
                stack.append(
                    _Block(
                        kind=tag.kind,
                        expr=tag.expr,
                        target=tag.target,
                        start_para=para_index,
                        start_tag_index=tag.index,
                        start_text=text,
                        start_span=(tag.start, tag.end),
                        end_para=-1,
                        end_tag_index=-1,
                        end_text="",
                        end_span=(0, 0),
                    )
                )
                continue
            if not stack:
                raise DocgenUnavailable(
                    f"模板出现未配对的 `{{% {tag.kind} %}}`，段落文本片段：{_excerpt(text)!r}"
                )
            current = stack[-1]
            if tag.kind == "else":
                if current.kind != "if" or current.else_para is not None:
                    raise DocgenUnavailable(
                        f"模板 `{{% else %}}` 位置非法，段落文本片段：{_excerpt(text)!r}"
                    )
                current.else_para = para_index
                current.else_tag_index = tag.index
                current.else_text = text
                current.else_span = (tag.start, tag.end)
                continue
            if tag.kind == "endfor" and current.kind != "for":
                raise DocgenUnavailable(
                    f"模板 `{{% endfor %}}` 与 `{{% {current.kind} %}}` 不匹配，"
                    f"段落文本片段：{_excerpt(text)!r}"
                )
            if tag.kind == "endif" and current.kind != "if":
                raise DocgenUnavailable(
                    f"模板 `{{% endif %}}` 与 `{{% {current.kind} %}}` 不匹配，"
                    f"段落文本片段：{_excerpt(text)!r}"
                )
            current.end_para = para_index
            current.end_tag_index = tag.index
            current.end_text = text
            current.end_span = (tag.start, tag.end)
            stack.pop()
            blocks.append(current)
    if stack:
        pending = stack[-1]
        raise DocgenUnavailable(
            f"模板 `{{% {pending.kind} {pending.expr} %}}` 缺少 `{{% end{pending.kind} %}}`，"
            f"段落文本片段：{_excerpt(pending.start_text)!r}"
        )
    return blocks


def _deepest(blocks: Sequence[_Block]) -> Optional[_Block]:
    """找出最内层块（其后没有在它之后开始的块）。"""
    if not blocks:
        return None
    candidate = max(blocks, key=lambda item: (item.start_para, item.start_tag_index))
    for other in blocks:
        if other is candidate:
            continue
        if (other.start_para, other.start_tag_index) > (candidate.start_para, candidate.start_tag_index):
            return None
    return candidate


def _emit_branch(
    env: Any,
    paragraphs: List[Paragraph],
    block: _Block,
    context: Mapping[str, Any],
    start_para: int,
    end_para: int,
    extra: Mapping[str, Any],
    errors: List[str],
    anchor: Any = None,
) -> Any:
    """在锚点之后插入 `[start_para, end_para)` 区间的逐项渲染副本，并返回新的锚点。

    D3（R-3）：锚点必须由调用方在**同一次循环内贯穿**。此前每次调用都在函数内部
    重新取 `paragraphs[end_para]._p`，而 `_process_for()` 对每个循环项各调一次，
    于是每一项的副本都被插到 `{% endfor %}` 段紧后面，把前一项挤到更后面 →
    最终呈现**整体倒序**。现在由调用方传入并用返回值串联：

    * `anchor is None`（单次调用，如 `{% if %}` 分支）→ 仍以 `end_para` 段为锚；
    * 多次调用（`{% for %}` 逐项）→ 传入上一次返回的元素，保证插入顺序与
      `items` 顺序一致，且块内多段的相对顺序不变（同一分支内仍用
      `anchor = new_element` 逐个串联）。
    """
    if anchor is None:
        anchor = paragraphs[end_para]._p
    for para_index in range(start_para, end_para):
        source = paragraphs[para_index]._p
        new_element = copy.deepcopy(source)
        anchor.addnext(new_element)
        anchor = new_element
        paragraph = _container_of(new_element, paragraphs[para_index]._parent)
        if paragraph is None:
            continue
        _render_single_paragraph(env, paragraph, context, extra, errors)
    return anchor


def _process_for(
    env: Any,
    paragraphs: List[Paragraph],
    block: _Block,
    context: Mapping[str, Any],
    errors: List[str],
) -> None:
    where = f"for {block.target} in {block.expr}"
    ok, items = _eval_iterable(env, block.expr, context, where)
    if not ok:
        # 表达式无法求值：整块删除（保持“缺数据即无内容”，绝不编造条目）。
        return
    # D3：锚点在整次循环内贯穿——第一项以 `{% endfor %}` 段为锚，之后每项以
    # 上一项插入的最后一个元素为锚，确保多段循环的渲染顺序与 `items` 一致。
    anchor = paragraphs[block.end_para]._p
    for position, item in enumerate(items):
        extra: Dict[str, Any] = {block.target: item}
        extra["loop"] = _LoopContext(position, len(items))
        if block.inline:
            source = paragraphs[block.start_para]._p
            new_element = copy.deepcopy(source)
            anchor.addnext(new_element)
            anchor = new_element
            paragraph = _container_of(new_element, paragraphs[block.start_para]._parent)
            if paragraph is None:
                continue
            rendered = _render_text(env, block.start_text[block.start_span[1]:block.end_span[0]],
                                    _merged(context, extra), where)
            _assign_text(paragraph, rendered)
            continue
        start = block.start_para + 1
        end = block.end_para
        if block.else_para is not None and block.else_para < block.end_para:
            end = block.else_para
        if start >= end:
            continue
        anchor = _emit_branch(
            env, paragraphs, block, context, start, end, extra, errors,
            anchor=anchor,
        )


class _LoopContext:
    """最小 loop 对象：支持 `loop.index` / `loop.index0` / `loop.first` / `loop.last` / `loop.length`。"""

    def __init__(self, index0: int, length: int) -> None:
        self.index0 = index0
        self.index = index0 + 1
        self.revindex = length - index0
        self.revindex0 = length - index0 - 1
        self.first = index0 == 0
        self.last = index0 == length - 1
        self.length = length
        self.cycle = self._cycle

    def _cycle(self, *args: Any) -> Any:
        if not args:
            return ""
        return args[self.index0 % len(args)]


def _merged(context: Mapping[str, Any], extra: Mapping[str, Any]) -> Dict[str, Any]:
    merged = dict(context)
    merged.update(extra)
    return merged


def _process_if(
    env: Any,
    paragraphs: List[Paragraph],
    block: _Block,
    context: Mapping[str, Any],
    errors: List[str],
) -> None:
    where = f"if {block.expr}"
    truthy = _eval_condition(env, block.expr, context, where)
    if block.inline:
        if truthy:
            body = block.start_text[block.start_span[1]:block.end_span[0]]
        elif block.else_span is not None:
            body = block.start_text[block.else_span[1]:block.end_span[0]]
        else:
            body = ""
        rendered = _render_text(env, body, context, where)
        _assign_text(paragraphs[block.start_para], rendered)
        return
    if truthy:
        start, end = block.start_para + 1, block.end_para
        if block.else_para is not None and block.else_para < block.end_para:
            end = block.else_para
        if start >= end:
            return
        _emit_branch(env, paragraphs, block, context, start, end, {}, errors)
        return
    if block.else_para is None:
        return
    start, end = block.else_para + 1, block.end_para
    if start >= end:
        return
    _emit_branch(env, paragraphs, block, context, start, end, {}, errors)


def _delete_region(paragraphs: List[Paragraph], block: _Block) -> None:
    """删除原始模板块区间段落（标签段落一并删除）。"""
    for para_index in range(block.start_para, block.end_para + 1):
        element = paragraphs[para_index]._p
        parent = element.getparent()
        if parent is not None:
            parent.remove(element)


def _process_blocks(
    env: Any, container: Any, context: Mapping[str, Any], errors: List[str]
) -> None:
    """在容器内逐轮处理块结构：块先展开，段落渲染随后统一进行。"""
    rounds = 0
    while True:
        rounds += 1
        if rounds > _MAX_BLOCK_ROUNDS:
            raise DocgenUnavailable(
                f"模板块结构处理超过 {_MAX_BLOCK_ROUNDS} 轮，疑似存在异常的嵌套循环"
            )
        paragraphs = _container_paragraphs(container)
        blocks = _scan_blocks(paragraphs)
        block = _deepest(blocks)
        if block is None:
            return
        if block.kind == "for":
            _process_for(env, paragraphs, block, context, errors)
        else:
            _process_if(env, paragraphs, block, context, errors)
        _delete_region(paragraphs, block)


# --------------------------------------------------------------------------
# 六之二、部位照片栏位渲染（N-11 / R-56 / N-12）
#
# 渲染器**只按文件路径插入照片**：不检查、不分析、不推断图片的像素内容，
# 也不生成任何来自照片的图像结论。唯一读取的图片信息是文件头部记录的像素
# 尺寸与 DPI（用于把宽度换算成厘米并保持纵横比），以及文件是否存在。
# --------------------------------------------------------------------------


def _slot_of(context: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    """取出某一栏位的照片信息；结构异常时返回空字典（安全降级）。"""
    slots = context.get("photo_slots") if isinstance(context, Mapping) else None
    if isinstance(slots, Mapping):
        slot = slots.get(key)
        if isinstance(slot, Mapping):
            return slot
    return {}


def _slot_label(key: str, slot: Mapping[str, Any]) -> str:
    return _clean(slot.get("label")) or C.PHOTO_POSITION_LABELS.get(key, key)


def _slot_max_width_cm(key: str, slot: Mapping[str, Any]) -> float:
    """栏位最大宽度（厘米）：宽幅栏位用 `PHOTO_WIDE_MAX_WIDTH_CM`。"""
    definition = C.PHOTO_POSITION_INDEX.get(key)
    wide = bool(slot.get("wide")) or bool(getattr(definition, "wide", False))
    value = C.PHOTO_WIDE_MAX_WIDTH_CM if wide else C.PHOTO_MAX_WIDTH_CM
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = C.PHOTO_MAX_WIDTH_CM
    return value if value > 0 else C.PHOTO_MAX_WIDTH_CM


def _photo_entries(slot: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """把栏位的照片行归一化为 `[{path, caption, material_id, filename}]`（保持输入顺序）。"""
    entries: List[Dict[str, Any]] = []
    for raw in _sequence(slot.get("photos")):
        row = _as_mapping(raw)
        path = _clean(row.get("path")) or _clean(row.get("original_path")) or _clean(
            row.get("file_path")
        )
        if not path:
            continue
        entries.append(
            {
                "path": path,
                "caption": _clean(row.get("caption")) or _clean(row.get("photo_caption")),
                "material_id": row.get("material_id"),
                "filename": _clean(row.get("filename")) or Path(path).name,
                "sort_order": row.get("sort_order"),
            }
        )
    return entries


def _photo_exists(path: str) -> bool:
    """只判断文件是否存在（不打开、不解码，更不看像素内容）。"""
    if not path:
        return False
    try:
        return Path(path).is_file()
    except OSError:
        return False


def _photo_slots_with_photos(context: Mapping[str, Any]) -> List[str]:
    """上下文里**确实带照片行**、且 key 在位置目录内的栏位（保持目录/输入顺序）。

    只看“有没有照片行”，不判断文件是否存在、更不读取图片内容（N-11）；`photo_slots`
    缺失时退回 `context["photos"]` 的 `position_key`。目录外的 key 不计入：标准照片区
    只有 18 个目录栏位，给目录外的 key 追加标准区解决不了任何问题（那种情形由
    `PHOTO_POSITION_MISSING` 提示用户重新指派位置，见 checker）。
    """
    if not isinstance(context, Mapping):
        return []
    keys: List[str] = []
    slots = context.get("photo_slots")
    if isinstance(slots, Mapping):
        for raw_key, raw_slot in slots.items():
            key = _clean(raw_key)
            if key and _photo_entries(_as_mapping(raw_slot)):
                keys.append(key)
    if not keys:
        for raw in _sequence(context.get("photos")):
            key = _clean(_as_mapping(raw).get("position_key"))
            if key:
                keys.append(key)
    picked: List[str] = []
    for key in keys:
        if key in C.PHOTO_POSITION_INDEX and key not in picked:
            picked.append(key)
    return picked


def _natural_width_cm(path: str) -> Optional[float]:
    """图片原始宽度（厘米）：只读图片头部记录的像素尺寸与 DPI。

    绝不读取像素内容。无法判定时返回 None，由调用方退化为 python-docx 自带尺寸。
    """
    if _PILImage is None:
        return None
    try:
        with _PILImage.open(path) as image:
            width_px = float(image.size[0])
            dpi = image.info.get("dpi")
    except Exception:
        return None
    dpi_value = _DEFAULT_IMAGE_DPI
    if isinstance(dpi, (tuple, list)) and dpi:
        try:
            candidate = float(dpi[0])
        except (TypeError, ValueError):
            candidate = 0.0
        if candidate > 1.0:
            dpi_value = candidate
    elif isinstance(dpi, (int, float)) and float(dpi) > 1.0:
        dpi_value = float(dpi)
    if width_px <= 0:
        return None
    return width_px / dpi_value * 2.54


def _new_paragraph_like(paragraph: Paragraph) -> Paragraph:
    """新建一个与锚点段落同格式（复制 `pPr`）的段落，尚未插入文档树。"""
    element = OxmlElement("w:p")
    try:
        source_properties = paragraph._p.pPr
    except Exception:
        source_properties = None
    if source_properties is not None:
        element.append(copy.deepcopy(source_properties))
    return Paragraph(element, paragraph._parent)


def _copy_run_properties(source: Optional[Run], target: Run) -> None:
    """把模板锚点 run 的字体属性复制到新 run（保证中文与字号与模板一致）。"""
    if source is None:
        return
    try:
        properties = source._element.rPr
        if properties is None:
            return
        holder = target._element.get_or_add_rPr()
        for child in list(holder):
            holder.remove(child)
        for child in properties:
            holder.append(copy.deepcopy(child))
    except Exception:
        return


def _fill_picture_paragraph(
    paragraph: Paragraph, path: str, max_width_cm: float, source_run: Optional[Run]
) -> bool:
    """把一张照片放进居中段落；保留纵横比、不裁切。插入失败返回 False。"""
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = paragraph.add_run()
    _copy_run_properties(source_run, run)
    natural_cm = _natural_width_cm(path)
    try:
        if natural_cm is not None and natural_cm > 0:
            # 宽度 = min(栏位最大宽度, 图片原始宽度)；只给宽度，高度按纵横比自动缩放。
            run.add_picture(str(path), width=Cm(min(max_width_cm, natural_cm)))
        else:
            # 无法读取原始尺寸时先按 python-docx 自带尺寸插入，再按上限等比缩小。
            shape = run.add_picture(str(path))
            try:
                limit = Cm(max_width_cm)
                if shape.width and shape.width > limit:
                    scaled_height = int(round(float(shape.height) * float(limit) / float(shape.width)))
                    shape.width = limit
                    if scaled_height > 0:
                        shape.height = scaled_height
            except Exception:
                pass
    except Exception:
        return False
    return True


def _fill_caption_paragraph(
    paragraph: Paragraph, caption: str, source_run: Optional[Run]
) -> None:
    """照片说明行：居中、小字号。用户 caption 优先，其次栏位 label。"""
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = paragraph.add_run(caption)
    _copy_run_properties(source_run, run)
    try:
        run.font.size = Pt(_PHOTO_CAPTION_PT)
    except Exception:
        pass


def _render_photo_paragraph(
    paragraph: Paragraph, key: str, context: Mapping[str, Any]
) -> None:
    """把 `{{ photo_<key> }}` 锚点段落替换为图片段落（N-11 / R-56 / N-12）。"""
    slot = _slot_of(context, key)
    label = _slot_label(key, slot)
    max_width_cm = _slot_max_width_cm(key, slot)
    usable = [entry for entry in _photo_entries(slot) if _photo_exists(entry["path"])]

    if not usable:
        # N-12：保留该栏位，写入占位文字；不得挪用其它栏位照片，不得写“未见异常”。
        _assign_text(paragraph, C.PHOTO_PLACEHOLDER_TEXT)
        return

    anchor_element = paragraph._p
    parent = anchor_element.getparent()
    if parent is None:
        _assign_text(paragraph, C.PHOTO_PLACEHOLDER_TEXT)
        return

    runs = _runs(paragraph)
    source_run = runs[0] if runs else None
    inserted = 0
    for entry in usable:
        picture_paragraph = _new_paragraph_like(paragraph)
        if not _fill_picture_paragraph(picture_paragraph, entry["path"], max_width_cm, source_run):
            continue
        anchor_element.addprevious(picture_paragraph._p)
        inserted += 1
        caption = _clean(entry.get("caption")) or label
        if caption:
            caption_paragraph = _new_paragraph_like(paragraph)
            _fill_caption_paragraph(caption_paragraph, caption, source_run)
            anchor_element.addprevious(caption_paragraph._p)

    if not inserted:
        _assign_text(paragraph, C.PHOTO_PLACEHOLDER_TEXT)
        return
    parent.remove(anchor_element)


# --------------------------------------------------------------------------
# 七、段落渲染
# --------------------------------------------------------------------------


def _render_single_paragraph(
    env: Any,
    paragraph: Paragraph,
    context: Mapping[str, Any],
    extra: Mapping[str, Any],
    errors: List[str],
) -> None:
    """渲染单个段落：照片锚点 → 插图；其余合并 run 文本 → Jinja2 渲染 → 写回。"""
    text = _paragraph_text(paragraph)
    if not text:
        return
    photo_key = _photo_anchor_key(text)
    if photo_key:
        # 照片栏位：只按用户指定位置插图，不做任何像素内容分析（N-11）。
        _render_photo_paragraph(paragraph, photo_key, context)
        return
    if _photo_anchor_keys(text):
        # 段落里出现 `{{ photo_* }}` 却没有按约定独立成段（或 key 不在位置目录中）：
        # 不猜栏位、不静默清空，保留原锚点，由 `unresolved` 与
        # `DOCUMENT_UNRESOLVED_PLACEHOLDER` 上报（N-12/R-33）。
        return
    if _is_pure_block(text):
        # 块标签段落由块处理器负责；单独出现时只清空标签，不参与渲染。
        span = _template_run_span(paragraph)
        if span:
            _write_rendered(paragraph, "", span[0], len(_runs(paragraph)) or 1)
        return
    data = _merged(context, extra)
    try:
        rendered = _render_text(env, text, data, "段落")
    except DocgenUnavailable as exc:
        errors.append(str(exc))
        raise
    # D15：去掉“栏目标签 + 取值自带标签”的叠字（只针对结构明确的标量行）。
    rendered = _dedup_scalar_label(text, rendered)
    span = _template_run_span(paragraph)
    if span is None:
        return
    if rendered == text:
        return
    _write_rendered(paragraph, rendered, span[0], span[1])


def _render_all_paragraphs(
    env: Any,
    document: DocxDocument,
    context: Mapping[str, Any],
    errors: List[str],
) -> None:
    """渲染后统一处理所有段落（正文、表格单元格、嵌套表格）。

    先物化为列表：照片栏位渲染会在文档树中插入新段落，不能边遍历边改树。
    """
    for paragraph in list(_iter_all_paragraphs(document)):
        _render_single_paragraph(env, paragraph, context, {}, errors)


def _process_all_blocks(
    env: Any, document: DocxDocument, context: Mapping[str, Any], errors: List[str]
) -> None:
    """处理文档内所有容器的块结构（正文 + 表格单元格 + 嵌套表格）。"""
    for container in _iter_table_containers(document):
        _process_blocks(env, container, context, errors)


def _collect_unresolved(document: DocxDocument) -> List[str]:
    """收集渲染后仍存在的 `{{ ... }}` 锚点：去重且保持出现顺序。"""
    seen: Dict[str, None] = {}
    for paragraph in _iter_all_paragraphs(document):
        for match in _UNRESOLVED_RE.finditer(_paragraph_text(paragraph)):
            anchor = match.group(0)
            if anchor not in seen:
                seen[anchor] = None
    return list(seen.keys())


# --------------------------------------------------------------------------
# 八、公开接口：引擎状态
# --------------------------------------------------------------------------


def docx_engine_status() -> dict:
    """返回渲染引擎可用性：`docxtpl` 未安装时首选 `builtin`（TODO-DL-1）。"""
    available = False
    try:
        import docxtpl  # noqa: F401
    except Exception:
        available = False
    else:
        available = True
    return {
        "preferred": ENGINE_DOCXTPL if available else ENGINE_BUILTIN,
        "docxtpl_available": available,
        "todo": TODO_DOCXTPL,
    }


# --------------------------------------------------------------------------
# 九、公开接口：演示模板
# --------------------------------------------------------------------------

#: 演示模板的基础信息表：(标签, 占位符)
_DEMO_INFO_ROWS: Tuple[Tuple[str, str], ...] = (
    ("案件编号", "{{ case_no }}"),
    ("委托单位", "{{ commission_unit }}"),
    ("委托事项", "{{ commission_matter }}"),
    ("委托日期", "{{ commission_date }}"),
    ("姓名", "{{ subject_name }}"),
    ("性别", "{{ subject_gender }}"),
    ("年龄", "{{ subject_age }}"),
    ("身份信息", "{{ subject_identity }}"),
    ("检验时间", "{{ exam_time }}"),
    ("检验地点", "{{ exam_place }}"),
    ("检验人员", "{{ examiners }}"),
    ("检验方法", "{{ exam_method }}"),
    ("模板版本", "{{ template_version }}"),
    ("术语规则版本", "{{ term_rule_version }}"),
    ("文稿版本", "{{ doc_version }}"),
)

#: 演示模板的“尸表一般所见”栏目：(标签, 占位符)
_DEMO_GENERAL_ROWS: Tuple[Tuple[str, str], ...] = (
    ("尸长", "{{ body_length }}"),
    ("体重", "{{ body_weight }}"),
    ("衣着", "{{ clothing }}"),
    ("尸斑", "{{ livor_mortis }}"),
    ("尸僵", "{{ rigor_mortis }}"),
    ("尸温", "{{ body_temperature }}"),
    ("角膜", "{{ cornea }}"),
    ("其他一般所见", "{{ general_other }}"),
)

#: 演示模板中声明的照片栏位：**必须与 `constants.PHOTO_POSITION_DEFINITIONS` 逐一对应**。
#: D7：此前只硬编码 14 个，导致 `GET /photo-slots` 按目录上报 18 个栏位、而交付文稿只有
#: 14 个栏位，`missing`/`PHOTO_SLOT_EMPTY` 口径与文稿占位文字不闭合。这里直接取自
#: 目录的唯一来源，新增位置时自动跟随，不再有第二份清单。
_DEMO_PHOTO_KEYS: Tuple[str, ...] = tuple(C.PHOTO_POSITION_KEYS)

#: 演示模板用的正文字体（保证在无中文字体的环境下仍可读）
_DEMO_FONT = "宋体"
_DEMO_EAST_ASIA = "宋体"

#: 常规公文排版：正文宋体小四（12 磅）、1.5 倍行距、A4 页面。
_DEMO_BODY_PT = 12.0
_DEMO_TABLE_PT = 10.5
_DEMO_LINE_SPACING = 1.5


def _set_east_asia_font(holder: Any, name: str = _DEMO_EAST_ASIA) -> None:
    """把 run / style 的中文字体设为 `name`（`w:rFonts/@w:eastAsia`）。"""
    try:
        fonts = holder.find(qn("w:rFonts"))
        if fonts is None:
            fonts = holder.makeelement(qn("w:rFonts"), {})
            holder.append(fonts)
        fonts.set(qn("w:eastAsia"), name)
    except Exception:
        pass


def _setup_demo_document(document: DocxDocument) -> None:
    """A4 页面 + 常规页边距 + 正文宋体小四 + 1.5 倍行距（仅用 python-docx，无新依赖）。"""
    try:
        section = document.sections[0]
        section.page_width = Cm(21.0)
        section.page_height = Cm(29.7)
        section.top_margin = Cm(2.54)
        section.bottom_margin = Cm(2.54)
        section.left_margin = Cm(3.17)
        section.right_margin = Cm(3.17)
    except Exception:
        pass
    try:
        style = document.styles["Normal"]
        style.font.name = _DEMO_FONT
        style.font.size = Pt(_DEMO_BODY_PT)
        style.paragraph_format.line_spacing = _DEMO_LINE_SPACING
        _set_east_asia_font(style.element.get_or_add_rPr())
    except Exception:
        pass


def _apply_demo_font(
    run: Run, size: float = _DEMO_BODY_PT, bold: bool = False
) -> None:
    try:
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.name = _DEMO_FONT
        _set_east_asia_font(run._element.get_or_add_rPr())
    except Exception:
        pass


def _add_paragraph(
    document: DocxDocument,
    text: str,
    size: float = _DEMO_BODY_PT,
    bold: bool = False,
    align: Any = None,
) -> Paragraph:
    paragraph = document.add_paragraph()
    if align is not None:
        try:
            paragraph.alignment = align
        except Exception:
            pass
    run = paragraph.add_run(text)
    _apply_demo_font(run, size=size, bold=bold)
    return paragraph


def _set_cell_text(cell: Any, text: str, bold: bool = False) -> None:
    cell.text = ""
    paragraph = cell.paragraphs[0]
    run = paragraph.add_run(text)
    _apply_demo_font(run, size=_DEMO_TABLE_PT, bold=bold)


# --------------------------------------------------------------------------
# 九之一、标准「部位照片」区（演示模板与**兜底追加**共用同一段构造逻辑）
#
# 用户自建模板常常**没有任何** `{{ photo_<key> }}` 锚点：此时照片不会被渲染进文稿
# （修复前是静默丢弃）。`render_document` 因此在**工作副本**上调用
# `append_photo_section()` 追加标准照片区，再照常渲染；模板原件一个字节都不改（N-8）。
# --------------------------------------------------------------------------


def _photo_section_keys(keys: Any = None) -> List[str]:
    """标准照片区要写入的栏位 key：缺省为位置目录的全部栏位；显式传入时按目录顺序取。

    只接受 `constants.PHOTO_POSITION_INDEX` 内的 key：目录外的 key 写成的锚点渲染不了，
    只会留下 `{{ photo_* }}` 残锚（DOCUMENT_UNRESOLVED_PLACEHOLDER，blocking），
    因此这里直接过滤掉，绝不写入。
    """
    if keys is None:
        return [key for key in C.PHOTO_POSITION_KEYS if key in C.PHOTO_POSITION_INDEX]
    picked: List[str] = []
    for item in _sequence(keys):
        key = _clean(item)
        if key in C.PHOTO_POSITION_INDEX and key not in picked:
            picked.append(key)
    ordered = [key for key in C.PHOTO_POSITION_KEYS if key in picked]
    ordered.extend(key for key in picked if key not in ordered)
    return ordered


def photo_slot_title(key: str) -> str:
    """照片栏位的中文标题行：`照片栏位：<栏位名>`（宽幅栏位追加“（宽幅）”）。

    这是本模块与 `checker.PHOTO_SLOT_MISSING` 之间的**唯一**文本约定：checker 依据
    渲染产物里是否存在该标题行，判断“栏位是否真的进入了文稿”。
    """
    label = C.PHOTO_POSITION_LABELS.get(key, key)
    if getattr(C.PHOTO_POSITION_INDEX.get(key), "wide", False):
        label = f"{label}（宽幅）"
    return f"{PHOTO_SLOT_TITLE_PREFIX}{label}"


def append_photo_section(document: DocxDocument, keys: Any = None) -> int:
    """向 `document`（**工作副本**，绝非模板原件）追加标准「部位照片」区，返回栏位数。

    写入内容与排版（沿用演示模板的样式，中文栏位标题 + 居中锚点段落）：

    * 标题行 `部位照片`（14 磅加粗）；
    * 说明行 `PHOTO_SECTION_NOTE`（说明系统不识别照片内容，N-11）；
    * 每个栏位两段：`照片栏位：<中文栏位名>`（加粗）+ **居中**的 `{{ photo_<key> }}`
      锚点段落；栏位没有可用照片时渲染器会把锚点段替换为
      `PHOTO_PLACEHOLDER_TEXT`（“（未提供照片）”）并保留栏位（N-12）。

    :param document: 已打开的 `python-docx` 文档对象（调用方负责加载与保存）。
    :param keys: 要写入的栏位 key；缺省为该文档对象所对应的**全部**目录栏位（18 个）。
                 目录外的 key 一律忽略（见 `_photo_section_keys`）。
    :returns: 实际追加的栏位数（0 表示 `keys` 为空或全部不在目录内）。

    调用方约定：只有在工作副本里**完全没有** `{{ photo_` 锚点时才能调用，否则会与模板
    自带的栏位重复（`render_document` 用 `_working_copy_photo_anchors()` 保证幂等）。
    """
    picked = _photo_section_keys(keys)
    if not picked:
        return 0
    _add_paragraph(document, PHOTO_SECTION_HEADING, size=14, bold=True)
    _add_paragraph(document, PHOTO_SECTION_NOTE, size=_DEMO_TABLE_PT)
    for key in picked:
        # 栏位标题（中文）+ 居中锚点段落：本栏位没有照片时正文写“（未提供照片）”，
        # 居中的占位文字与插图段落（渲染时同样居中）排版一致。
        _add_paragraph(document, photo_slot_title(key), size=_DEMO_BODY_PT, bold=True)
        _add_paragraph(
            document, "{{ photo_%s }}" % key, align=WD_ALIGN_PARAGRAPH.CENTER
        )
    return len(picked)


def build_demo_template(path: str | Path, template_version: str = C.DEFAULT_TEMPLATE_VERSION) -> str:
    """程序化生成演示模板并保存到 `path`，返回路径字符串。

    占位符 key 与 `build_context` 输出一致；`{% for %}` / `{% for %}` 与 `{% if %}`
    的标签**分行书写**，以便 builtin 渲染器按段落块展开（跨段落块）。
    照片栏位对 `_DEMO_PHOTO_KEYS` 中每个 `position_key` 各写一个显式锚点段落
    `{{ photo_<key> }}`，并在其上方写中文栏位标题行；没有照片时正文写
    `PHOTO_PLACEHOLDER_TEXT`（N-12），系统不读取照片内容（N-11）。
    """
    target = Path(path)
    if target.parent and str(target.parent):
        target.parent.mkdir(parents=True, exist_ok=True)

    document = Document()
    _setup_demo_document(document)

    # ---- 标题（正文标题，非占位符） -------------------------------------
    _add_paragraph(
        document, "尸表检验记录", size=18, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER
    )
    _add_paragraph(
        document,
        f"（模板版本：{template_version}；鉴定意见只能由专业人员填写）",
        size=_DEMO_TABLE_PT,
        align=WD_ALIGN_PARAGRAPH.CENTER,
    )

    # ---- 案件与委托基础信息表格（2 列多行） -----------------------------
    table = document.add_table(rows=len(_DEMO_INFO_ROWS), cols=2)
    try:
        table.style = "Table Grid"
    except Exception:
        pass
    for row_index, (label, anchor) in enumerate(_DEMO_INFO_ROWS):
        _set_cell_text(table.rows[row_index].cells[0], label, bold=True)
        _set_cell_text(table.rows[row_index].cells[1], anchor)

    # ---- 案情简介 -------------------------------------------------------
    _add_paragraph(document, "案情简介", size=14, bold=True)
    _add_paragraph(document, "{{ case_summary }}")

    # ---- 尸表一般所见 ---------------------------------------------------
    _add_paragraph(document, "尸表一般所见", size=14, bold=True)
    for label, anchor in _DEMO_GENERAL_ROWS:
        _add_paragraph(document, f"{label}：{anchor}")

    # ---- 损伤检验记录（跨段落 {% for %}） ------------------------------
    _add_paragraph(document, "损伤检验记录", size=14, bold=True)
    _add_paragraph(document, "{% for injury in injuries %}")
    _add_paragraph(document, "{{ injury.seq }}. {{ injury.text }}（{{ injury.status_label }}）")
    _add_paragraph(document, "{% endfor %}")

    # ---- 部位照片（N-11/N-12/R-56：只按位置插入，不读取照片内容） ------
    # D7：`_DEMO_PHOTO_KEYS` 取自 `constants.PHOTO_POSITION_KEYS`（全部 18 个栏位），
    # 与 `GET /photo-slots` 的目录口径一致；本函数每次都用 Document() 新建文档并覆盖
    # 目标文件，因此可重复调用且不会重复插入（幂等）。
    # 段落构造与“用户模板没有锚点时向工作副本兜底追加”共用同一个函数
    # （`append_photo_section`），保证两处的栏位标题、排版与占位口径完全一致。
    append_photo_section(document, _DEMO_PHOTO_KEYS)

    # ---- 草稿提示（跨段落 {% if %}；R-46：校核信息与文书正文分开） --------
    # 校核清单只在「校核表 / 工作台」中查看，**不得**把识别候选与处理说明写进文书正文；
    # 正文只保留一行草稿提示，且只在内部草稿（有 draft_marker）时出现。
    _add_paragraph(document, "{% if draft_marker %}")
    _add_paragraph(document, "本稿为内部草稿：识别候选、疑点与处理说明见校核清单，本稿不得作为待签发稿。")
    _add_paragraph(document, "{% endif %}")

    # ---- 鉴定意见 -------------------------------------------------------
    _add_paragraph(document, "鉴定意见（由专业人员提供）", size=14, bold=True)
    _add_paragraph(document, "{{ opinion }}")

    # ---- 签名与日期 -----------------------------------------------------
    _add_paragraph(document, "签名与日期", size=14, bold=True)
    _add_paragraph(document, "检验人员签名：{{ examiners }}")
    _add_paragraph(document, "复核人员签名：＿＿＿＿＿＿＿＿")
    _add_paragraph(document, "鉴定机构（盖章）：")
    _add_paragraph(document, "日期：{{ exam_time }}")
    _add_paragraph(document, "{{ signature_block }}")

    # ---- 草稿标记位（末段） ---------------------------------------------
    _add_paragraph(document, "{{ draft_marker }}")

    document.save(str(target))
    return str(target)


# --------------------------------------------------------------------------
# 十、公开接口：上下文组装
# --------------------------------------------------------------------------


def _as_mapping(row: Any) -> Dict[str, Any]:
    """把任意“行”统一成 dict（sqlite3.Row / dataclass / Mapping / 对象均可）。"""
    if row is None:
        return {}
    if isinstance(row, Mapping):
        return dict(row)
    if hasattr(row, "keys"):
        try:
            return {key: row[key] for key in row.keys()}  # type: ignore[index]
        except Exception:
            pass
    if hasattr(row, "__dict__"):
        return {key: value for key, value in vars(row).items() if not key.startswith("_")}
    return {}


def _clean(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)):
        return str(value)
    return str(value).strip()


def _sequence(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, (str, bytes, Mapping)):
        return []
    try:
        return list(value)
    except TypeError:
        return []


def _status_label(status: str) -> str:
    return C.FIELD_STATUS_LABELS.get(status, status or "")


def _field_value(row: Mapping[str, Any]) -> str:
    """字段值：confirmed_value → normalized_text → raw_text。

    N-5 / R-35：`status == "not_recorded"` 时恒为“未记录”，且绝不输出“未见异常”；
    `status == "not_examined"` 时保留原文（`terminology.py` 已前置“未进行该项检查：”），
    原文缺失时只写该状态提示语，绝不改写成“未见异常”；
    `status == "negative"` 时原文照抄，否定词原样保留（N-4）。
    """
    status = _clean(row.get("status"))
    if status == "not_recorded":
        return _NOT_RECORDED_TEXT
    for key in ("confirmed_value", "normalized_text", "raw_text"):
        value = _clean(row.get(key))
        if value:
            return value
    # 以下是“状态明确但三处取值皆空”的兜底：按状态给出不编造内容的写法。
    if status == "not_examined":
        return _NOT_EXAMINED_TEXT
    if status == "negative":
        # 明确阴性所见却没有原文：只声明“未记录”，绝不写成“未见异常”，
        # 也绝不用任何推断代替缺失的原文（N-2/N-5）。
        return _NOT_RECORDED_TEXT
    return ""


def _dedup_scalar_label(template_text: str, rendered: str) -> str:
    """D15：模板栏目已写标签、而字段取值本身又以同一标签开头时，去掉重复的那一处。

    只处理**结构明确**的标量行——模板段落形如「<栏目名>：{{ <字段键> }}」，且
    `<栏目名>` 与 `constants.FIELD_INDEX[<字段键>].label` **逐字相同**时，才在渲染结果
    以「标签 + 分隔符 + 标签」或「标签 + 标签」开头的情况下剥掉模板那一份标签：

    * `尸长：{{ body_length }}` + 取值 `尸长172 cm。` → `尸长172 cm。`（不再叠字）
    * `其他一般所见：{{ general_other }}` + 取值 `其他一般所见：未见皮下出血`
      → `其他一般所见：未见皮下出血`
    * `体重：{{ body_weight }}` + 取值 `未记录` → 原样（保留栏目名，N-5）

    取值里没有标签时**不动**；自由文本（案情简介、鉴定意见等）与其它锚点一律不参与，
    避免篡改用户原文（N-2/N-3）。
    """
    match = _SCALAR_LABEL_ANCHOR_RE.match(template_text or "")
    if not match:
        return rendered
    key = match.group("key")
    definition = C.FIELD_INDEX.get(key)
    if definition is None:
        return rendered
    label = _clean(match.group("label"))
    if not label or label != _clean(definition.label):
        return rendered
    if not rendered:
        return rendered
    for separator in _LABEL_SEPARATORS:
        if rendered.startswith(label + separator + label):
            return rendered[len(label) + len(separator):]
    if rendered.startswith(label + label):
        return rendered[len(label):]
    return rendered


def _field_entry(row: Mapping[str, Any], definition: Any) -> Dict[str, Any]:
    key = _clean(row.get("field_key"))
    label = _clean(row.get("field_label")) or (getattr(definition, "label", "") or key)
    status = _clean(row.get("status"))
    return {
        "label": label,
        "value": _field_value(row),
        "status_label": _status_label(status),
        "source": _clean(row.get("source_note")) or _clean(row.get("source_material_id")),
    }


def _field_sort_key(row: Mapping[str, Any], definition: Any, fallback: int) -> Tuple[int, str]:
    order = row.get("display_order")
    if order is None:
        order = getattr(definition, "order", None)
    try:
        numeric = int(order)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        numeric = fallback
    return (numeric, _clean(row.get("field_key")))


def _field_rows(case_data: Mapping[str, Any]) -> List[Dict[str, Any]]:
    return [_as_mapping(row) for row in _sequence(case_data.get("fields"))]


def _row_index(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Mapping[str, Any]]:
    index: Dict[str, Mapping[str, Any]] = {}
    for row in rows:
        key = _clean(row.get("field_key"))
        if key and key not in index:
            index[key] = row
    return index


def _scalar(
    key: str,
    index: Mapping[str, Mapping[str, Any]],
    case_data: Mapping[str, Any],
    default: str = "",
) -> str:
    """标量取值：字段行（confirmed→normalized→raw）优先，其次 case_data 顶层同名 key。

    D6 / N-5 / R-35：模板声明了该栏目（`key in constants.FIELD_INDEX`）却既没有字段行、
    `case_data` 顶层也没有值时，**必须**返回“未记录”，绝不渲染成空白——同一份文书里
    不能出现“未记录”与空白两种“栏目空白”写法。非字段锚点（`opinion`/`draft_marker`/
    `signature_block` 等）仍返回 `default`，保持原样。
    D15 的标签叠字在段落渲染阶段由 `_dedup_scalar_label()` 处理（只针对结构明确的
    标量行，不改动自由文本，也不改动本函数返回值）。
    """
    row = index.get(key)
    if row is not None:
        value = _field_value(row)
        if value:
            return value
    value = _clean(case_data.get(key))
    if value:
        return value
    if key in C.FIELD_INDEX:
        return _NOT_RECORDED_TEXT
    return default


def _injury_entry(row: Mapping[str, Any], position: int) -> Dict[str, Any]:
    status = _clean(row.get("status"))
    try:
        seq = int(row.get("seq"))
    except (TypeError, ValueError):
        seq = 0
    if seq <= 0:
        seq = position
    text = _clean(row.get("normalized_text")) or _clean(row.get("raw_text"))
    if not text:
        # 兜底：按目录顺序拼出已记录的部分，绝不补造未记载的特征（N-2）。
        parts: List[str] = []
        for key in C.INJURY_FIELDS:
            value = _clean(row.get(key))
            if value:
                label = C.INJURY_FIELD_LABELS.get(key, key)
                parts.append(f"{label}{value}")
        text = "；".join(parts)
    return {"seq": seq, "text": text, "status_label": _status_label(status)}


def _material_entry(row: Mapping[str, Any]) -> Dict[str, Any]:
    kind = _clean(row.get("kind"))
    return {
        "id": row.get("id"),
        "kind": kind,
        "kind_label": C.MATERIAL_KIND_LABELS.get(kind, kind),
        "filename": _clean(row.get("original_filename")) or _clean(row.get("stored_path")),
        "media_type": _clean(row.get("media_type")),
        "page_count": row.get("page_count") or 0,
    }


def _checklist_entry(row: Mapping[str, Any]) -> Dict[str, Any]:
    severity = _clean(row.get("severity"))
    return {
        "id": row.get("id"),
        "code": _clean(row.get("code")),
        "category": _clean(row.get("category")),
        "severity": severity,
        "severity_label": C.CHECK_SEVERITY_LABELS.get(severity, severity),
        "message": _clean(row.get("message")),
        "target": _clean(row.get("target_type")),
    }


def _signature_block(case_data: Mapping[str, Any]) -> str:
    lines = ["检验人签名：", "复核人签名：", "机构盖章："]
    return "\n".join(lines)


def _number(value: Any) -> Optional[float]:
    """安全转 float（用于 `sort_order` 排序）；无法转换返回 None。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _photo_slot_rows(case_data: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """取出 `case_data["photo_slots"]` 的行列表。

    兼容两种输入形态（契约未写明，见交付说明「契约问题」）：
    * `list[dict]`：`database.list_photo_slots()` 的返回结构；
    * `dict[position_key] -> dict`：已按栏位建好索引的形态。
    """
    raw = case_data.get("photo_slots") if isinstance(case_data, Mapping) else None
    rows: List[Dict[str, Any]] = []
    if isinstance(raw, Mapping):
        for key, value in raw.items():
            row = _as_mapping(value)
            if not row:
                continue
            if not _clean(row.get("position_key")):
                row["position_key"] = _clean(key)
            rows.append(row)
        return rows
    for item in _sequence(raw):
        row = _as_mapping(item)
        if row:
            rows.append(row)
    return rows


def _slot_photo_rows(slot: Mapping[str, Any], key: str, label: str) -> List[Dict[str, Any]]:
    """把栏位下的照片行归一化为上下文中的 `photos` 条目（保持/按 sort_order 排序）。

    **路径只取输入里已经解析好的绝对路径**（`path` 优先，兼容 `original_path`），
    绝不在 `docgen` 里拼接存储根目录（N-11 / R-56）。
    """
    entries: List[Tuple[float, int, Dict[str, Any]]] = []
    for position, raw_value in enumerate(_sequence(slot.get("photos")), start=1):
        row = _as_mapping(raw_value)
        if not row:
            continue
        path = (
            _clean(row.get("path"))
            or _clean(row.get("original_path"))
            or _clean(row.get("file_path"))
        )
        if not path:
            continue
        order = _number(row.get("sort_order"))
        entries.append(
            (
                order if order is not None else float(position),
                position,
                {
                    "position_key": key,
                    "label": _clean(row.get("label")) or label,
                    "slot_label": label,
                    "path": path,
                    "caption": _clean(row.get("caption")) or _clean(row.get("photo_caption")),
                    "filename": _clean(row.get("filename")) or Path(path).name,
                    "material_id": row.get("material_id"),
                    "sort_order": order,
                    "exists": _photo_exists(path),
                },
            )
        )
    entries.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in entries]


def _build_photo_slots(
    case_data: Mapping[str, Any]
) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]:
    """构造 `photo_slots`（dict，含全部目录栏位）与展开后的 `photos` 列表。

    * 目录内的 18 个位置键**始终存在**，没有照片时为 `photos: []`——这样模板里
      显式声明的栏位才能被识别并写入 `PHOTO_PLACEHOLDER_TEXT`（N-12）；
    * 目录外的 position_key 也保留（排在最后），不静默丢弃；
    * `wide` / `label` / `section` 缺省值来自 `constants.PHOTO_POSITION_DEFINITIONS`。
    """
    ordered: List[str] = list(C.PHOTO_POSITION_KEYS)
    known: Dict[str, Dict[str, Any]] = {}
    extra: Dict[str, Dict[str, Any]] = {}
    for row in _photo_slot_rows(case_data):
        key = _clean(row.get("position_key"))
        if not key:
            continue
        if key in C.PHOTO_POSITION_INDEX:
            known.setdefault(key, row)
        else:
            extra.setdefault(key, row)

    slots: Dict[str, Dict[str, Any]] = {}
    photos: List[Dict[str, Any]] = []
    for key in ordered + [item for item in extra if item not in ordered]:
        definition = C.PHOTO_POSITION_INDEX.get(key)
        row = known.get(key) or extra.get(key) or {}
        label = _clean(row.get("label")) or C.PHOTO_POSITION_LABELS.get(key, key)
        section = _clean(row.get("section")) or _clean(getattr(definition, "section", "")) or "other"
        if "wide" in row:
            wide = bool(row.get("wide"))
        else:
            wide = bool(getattr(definition, "wide", False))
        anchor = _clean(row.get("anchor")) or C.PHOTO_POSITION_ANCHORS.get(key, f"photo_{key}")
        slot_photos = _slot_photo_rows(row, key, label)
        slots[key] = {
            "position_key": key,
            "label": label,
            "section": section,
            "section_label": C.PHOTO_SECTION_LABELS.get(section, section),
            "wide": wide,
            "anchor": anchor,
            "max_width_cm": (
                C.PHOTO_WIDE_MAX_WIDTH_CM if wide else C.PHOTO_MAX_WIDTH_CM
            ),
            "photos": slot_photos,
        }
        photos.extend(slot_photos)
    return slots, photos


def build_context(case_data: Mapping[str, Any]) -> dict:
    """把案件数据组装成模板上下文。

    - 标量字段：字段行（confirmed_value → normalized_text → raw_text）优先，
      其次 `case_data` 顶层同名 key，再其次目录默认值。
    - `opinion`（N-1）：只取 `case_data["opinion_text"]`，绝不从其他来源推导。
    - `draft_marker`：**只有** `doc_status == "internal_draft"`（或缺省）时为
      `constants.DRAFT_MARKER`；`pending_issue` / `approved` / `superseded` 一律为空串
      （D4/R-46/架构 §3.2：待签发稿不得再带【待核对】）。
    - 标量栏目（D6/N-5/R-35）：模板声明了栏目但没有字段行时写“未记录”，不写空白。
    - `photo_slots` / `photos`（N-11/R-56/N-12）：照片只按用户指定的 `position_key`
      分栏；路径只取 `case_data["photo_slots"][i]["photos"][j]["path"]`（workflow 已解析为
      绝对路径），`docgen` 不自行拼接存储路径，也绝不读取照片内容。
    - 缺失 key 一律给空串/空列表，配合 `ChainableUndefined` 不会报 UndefinedError。
    """
    data: Mapping[str, Any] = case_data if isinstance(case_data, Mapping) else {}
    rows = _field_rows(data)
    index = _row_index(rows)

    # 栏目顺序
    section_order: List[str] = []
    for item in _sequence(data.get("section_order")):
        value = _clean(item)
        if value:
            section_order.append(value)
    if not section_order:
        section_order = list(C.DEFAULT_SECTION_ORDER)

    # 文书配置
    template_version = _scalar(
        "template_version", index, data, default=C.DEFAULT_TEMPLATE_VERSION
    )
    term_rule_version = _scalar(
        "term_rule_version", index, data, default=C.TERM_RULE_DEFAULT_VERSION
    )
    doc_version = _scalar("doc_version", index, data)
    if not doc_version or doc_version == _NOT_RECORDED_TEXT:
        # 兼容旧调用：没有 `doc_version` 时退回案件自身的 `version`；
        # 仍取不到才保留 `_scalar` 给出的“未记录”（N-5，不写空白）。
        doc_version = _clean(data.get("version")) or doc_version or _NOT_RECORDED_TEXT

    # 字段字典
    fields: Dict[str, Dict[str, Any]] = {}
    for position, row in enumerate(rows, start=1):
        key = _clean(row.get("field_key"))
        if not key or key in fields:
            continue
        definition = C.FIELD_INDEX.get(key)
        fields[key] = _field_entry(row, definition)
        if definition is not None:
            fields[key].setdefault("group", definition.group)
            fields[key].setdefault("required", definition.required)

    # 尸表一般所见（只含 general_findings 组，按 display_order 排序）
    general_rows: List[Tuple[Tuple[int, str], Dict[str, Any]]] = []
    for position, row in enumerate(rows, start=1):
        key = _clean(row.get("field_key"))
        definition = C.FIELD_INDEX.get(key)
        group = _clean(row.get("field_group")) or (getattr(definition, "group", "") or "")
        if group != "general_findings":
            continue
        entry = _field_entry(row, definition)
        general_rows.append((_field_sort_key(row, definition, position), entry))
    general_rows.sort(key=lambda item: item[0])
    general_findings = [entry for _, entry in general_rows]

    # 部位与损伤（按 seq 排序）
    injuries: List[Dict[str, Any]] = []
    for position, raw in enumerate(_sequence(data.get("injuries")), start=1):
        row = _as_mapping(raw)
        if not row:
            continue
        injuries.append(_injury_entry(row, position))
    injuries.sort(key=lambda item: (item["seq"], item["text"]))

    # 材料与待核对清单
    materials = [
        _material_entry(_as_mapping(raw))
        for raw in _sequence(data.get("materials"))
        if _as_mapping(raw)
    ]
    checklist_open: List[Dict[str, Any]] = []
    for raw in _sequence(data.get("checklist")):
        row = _as_mapping(raw)
        if not row:
            continue
        status = _clean(row.get("status")) or "open"
        if status not in ("open", "pending"):
            continue
        checklist_open.append(_checklist_entry(row))

    # N-1：鉴定意见只能来自用户提供的内容
    opinion = _clean(data.get("opinion_text"))

    # 草稿标记（D4 / R-46 / 架构 §3.2）：**只有** `internal_draft`（草稿）正文写
    # `DRAFT_MARKER`；`pending_issue`（待签发稿）与 `approved`（已审核）一律不写。
    # 否则 `pending_issue` 必然携带【待核对】，被自家 checker 的
    # `DOCUMENT_DRAFT_MARKER`（blocking）立即判为阻断——状态机自相矛盾。
    # `doc_status` 缺失（历史调用）时按草稿处理：宁严勿松，草稿标记多写不会漏掉
    # 待核对事项，少写则可能让未核对内容静默进入签发稿。
    doc_status = _clean(data.get("doc_status"))
    draft_marker = C.DRAFT_MARKER if doc_status in ("", "internal_draft") else ""

    # 部位照片（N-11/R-56/N-12）：只按用户指定位置插入，不读取照片内容。
    # 路径只取输入里已解析好的绝对路径，docgen 不自行拼接存储根目录。
    photo_slots, photos = _build_photo_slots(data)

    context: Dict[str, Any] = {
        "case_no": _scalar("case_no", index, data),
        "commission_unit": _scalar("commission_unit", index, data),
        "commission_matter": _scalar("commission_matter", index, data),
        "commission_date": _scalar("commission_date", index, data),
        "case_summary": _scalar("case_summary", index, data),
        "subject_name": _scalar("subject_name", index, data),
        "subject_gender": _scalar("subject_gender", index, data),
        "subject_age": _scalar("subject_age", index, data),
        "subject_identity": _scalar("subject_identity", index, data),
        "exam_time": _scalar("exam_time", index, data),
        "exam_place": _scalar("exam_place", index, data),
        "examiners": _scalar("examiners", index, data),
        "exam_method": _scalar("exam_method", index, data),
        "body_length": _scalar("body_length", index, data),
        "body_weight": _scalar("body_weight", index, data),
        "clothing": _scalar("clothing", index, data),
        "livor_mortis": _scalar("livor_mortis", index, data),
        "rigor_mortis": _scalar("rigor_mortis", index, data),
        "body_temperature": _scalar("body_temperature", index, data),
        "cornea": _scalar("cornea", index, data),
        "general_other": _scalar("general_other", index, data),
        "material_list": _scalar("material_list", index, data),
        "template_version": template_version,
        "term_rule_version": term_rule_version,
        "doc_version": doc_version,
        "section_order": section_order,
        "section_order_labels": [
            C.SECTION_ORDER_LABELS.get(item, item) for item in section_order
        ],
        "fields": fields,
        "general_findings": general_findings,
        "injuries": injuries,
        "materials": materials,
        "checklist_open": checklist_open,
        "opinion": opinion,
        "draft_marker": draft_marker,
        "signature_block": _signature_block(data),
        "photo_slots": photo_slots,
        "photos": photos,
        "photo_placeholder": C.PHOTO_PLACEHOLDER_TEXT,
        "photo_max_width_cm": C.PHOTO_MAX_WIDTH_CM,
        "photo_wide_max_width_cm": C.PHOTO_WIDE_MAX_WIDTH_CM,
    }
    return context


# --------------------------------------------------------------------------
# 十一、公开接口：模板解析
# --------------------------------------------------------------------------


def resolve_template(case_data: Mapping[str, Any], templates_dir: str | Path) -> str:
    """按 `template_version` 在 `templates_dir` 下找 `<version>.docx`。

    找不到 → `report_template.docx`；再找不到 → 调用 `build_demo_template` 生成后返回。
    """
    directory = Path(templates_dir)
    data: Mapping[str, Any] = case_data if isinstance(case_data, Mapping) else {}
    version = _clean(data.get("template_version")) or C.DEFAULT_TEMPLATE_VERSION
    candidates = [directory / f"{version}.docx", directory / "report_template.docx"]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return build_demo_template(candidates[-1], template_version=version)


# --------------------------------------------------------------------------
# 十二、公开接口：渲染
# --------------------------------------------------------------------------


def _load_document(path: Path) -> DocxDocument:
    try:
        return Document(str(path))
    except Exception as exc:
        raise DocgenUnavailable(f"无法打开 Word 文档 {str(path)!r}：{exc!r}") from exc


def _render_with_docxtpl(template_path: Path, context: Mapping[str, Any], output_path: Path) -> None:
    try:
        from docxtpl import DocxTemplate  # type: ignore
    except Exception as exc:
        raise DocgenUnavailable(
            f"未安装 docxtpl（{TODO_DOCXTPL}），无法使用 docxtpl 引擎：{exc!r}"
        ) from exc
    try:
        template = DocxTemplate(str(template_path))
        template.render(dict(context))
        template.save(str(output_path))
    except DocgenUnavailable:
        raise
    except Exception as exc:
        raise DocgenUnavailable(f"docxtpl 渲染失败：{type(exc).__name__}: {exc}") from exc


def _render_with_builtin(template_path: Path, context: Mapping[str, Any], output_path: Path) -> None:
    env = _build_env()
    document = _load_document(template_path)
    errors: List[str] = []
    _process_all_blocks(env, document, context, errors)
    _render_all_paragraphs(env, document, context, errors)
    try:
        document.save(str(output_path))
    except Exception as exc:
        raise DocgenUnavailable(f"渲染结果保存失败 {str(output_path)!r}：{exc!r}") from exc


def _same_file(left: Path, right: Path) -> bool:
    """两个路径是否指向同一个文件（`resolve()` 失败时退回字符串比较）。"""
    try:
        return left.resolve() == right.resolve()
    except Exception:
        return str(left) == str(right)


def _prepare_photo_section(
    source: Path, target: Path, context: Mapping[str, Any], notes: List[str]
) -> int:
    """兜底：模板没有声明任何照片栏位而本次确实有照片时，向**工作副本**追加标准照片区。

    行为与边界（语义必须与 `checker.PHOTO_SLOT_MISSING` 一致）：

    * **默认行为**：只要工作副本里没有任何 `{{ photo_` 锚点，而上下文里确实有
      “已指派位置（key 在位置目录内）”的照片，就追加标准「部位照片」区（18 个栏位）
      并返回栏位数——照片因此**不会**被静默丢弃；
    * 追加只作用于 `target`（模板的**工作副本**），`source` 指向的模板原件绝不写入（N-8）；
    * 模板与输出是同一个文件时（调用方要求就地渲染）不追加：此时没有“副本”可写，
      写入就等于改写模板原件；改为在 `notes` 里如实说明，并由校核项
      `PHOTO_SLOT_MISSING` 提醒用户（绝不静默）；
    * 追加本身失败（异常）时**不中断渲染**：恢复工作副本为模板原样，并在 `notes` 里
      写明“照片未插入文稿”。
    """
    photo_keys = _photo_slots_with_photos(context)
    if not photo_keys:
        # 没有“已指派位置的照片”→ 没有必须插入的照片，不追加空栏位（避免无谓地改排版）。
        return 0
    if _same_file(source, target):
        notes.append(
            "模板与输出为同一路径，无法在不改动模板原件的前提下追加照片栏位（N-8）："
            "已指派位置的照片未插入文稿；请把模板与输出分开，或在模板中加入 "
            "{{ photo_<位置> }} 栏位"
        )
        return 0
    try:
        working = _load_document(target)
        if _working_copy_photo_anchors(working):
            # 模板自己声明了照片栏位：绝不重复追加（幂等）。
            return 0
        appended = append_photo_section(working)
        if not appended:
            return 0
        working.save(str(target))
    except Exception as exc:  # noqa: BLE001 - 追加失败不得让整个生成失败
        notes.append(
            "模板未声明照片栏位，自动追加标准「部位照片」区失败（{0}: {1}）："
            "已指派位置的照片未插入文稿，请在模板中加入 {{{{ photo_<位置> }}}} 栏位".format(
                type(exc).__name__, exc
            )
        )
        try:
            # 恢复工作副本为模板原样，保证后续渲染仍从完整副本开始（N-8：原件未被触碰）。
            shutil.copyfile(str(source), str(target))
        except Exception:  # noqa: BLE001 - 恢复失败时后续渲染会给出明确失败信息
            pass
        return 0
    notes.append(
        "模板未声明照片栏位（{{{{ photo_<位置> }}}}），已按标准栏位追加「{0}」区"
        "（{1} 个栏位）后再渲染；模板原件未改动（N-8）".format(
            PHOTO_SECTION_HEADING, appended
        )
    )
    return appended


def render_document(
    template_path: str | Path,
    context: Mapping[str, Any],
    output_path: str | Path,
    engine: str = "auto",
) -> RenderResult:
    """渲染文书。

    - `auto`：能 import `docxtpl` 就用 `docxtpl`，否则 `builtin`。
    - `docxtpl`：未安装 → `DocgenUnavailable`（消息含 `TODO-DL-1`）。
    - `builtin`：python-docx + Jinja2 自实现渲染。
    - **绝不修改模板原件（N-8）**：先复制模板到 `output_path`，之后的一切读写（含照片区
      兜底追加）都发生在**工作副本**上；模板原件只被读取。
    - **照片栏位兜底（R-56 / N-12）**：工作副本里没有任何 `{{ photo_` 锚点、而
      `context["photo_slots"]` / `context["photos"]` 里确实有“已指派位置”的照片时，
      先向工作副本追加标准「部位照片」区（18 个栏位、中文栏位标题行、居中锚点段落）
      再渲染，并把说明写进 `RenderResult.notes`；追加失败或无法追加（模板与输出同一
      路径）时同样写进 `notes` 如实说明“照片未插入文稿”，绝不静默丢弃。
    """
    source = Path(template_path)
    target = Path(output_path)
    if not source.is_file():
        raise DocgenUnavailable(f"模板文件不存在：{str(source)!r}")
    if target.parent and str(target.parent):
        target.parent.mkdir(parents=True, exist_ok=True)

    requested = (engine or "auto").strip().lower()
    if requested not in ("auto", ENGINE_DOCXTPL, ENGINE_BUILTIN):
        raise DocgenUnavailable(
            f"未知渲染引擎 {engine!r}；可选 auto / docxtpl / builtin"
        )

    selected = requested
    if requested == "auto":
        selected = ENGINE_DOCXTPL if docx_engine_status()["docxtpl_available"] else ENGINE_BUILTIN

    notes: List[str] = []

    # N-8：先复制模板副本，之后所有写入都发生在副本上。
    try:
        if not _same_file(source, target):
            shutil.copyfile(str(source), str(target))
    except Exception as exc:
        raise DocgenUnavailable(
            f"复制模板到输出路径失败（{str(source)!r} → {str(target)!r}）：{exc!r}"
        ) from exc

    # 照片栏位兜底：只改工作副本（target），模板原件（source）绝不写入。
    _prepare_photo_section(source, target, context, notes)

    if selected == ENGINE_DOCXTPL:
        _render_with_docxtpl(target, context, target)
    else:
        _render_with_builtin(target, context, target)

    document = _load_document(target)
    unresolved = _collect_unresolved(document)
    return RenderResult(
        output_path=str(target),
        engine=selected,
        unresolved=unresolved,
        paragraph_count=_count_paragraphs(document),
        table_count=_count_tables(document),
        notes=notes,
    )


# --------------------------------------------------------------------------
# 十三、公开接口：LibreOffice / PDF
# --------------------------------------------------------------------------


def _libreoffice_candidates() -> List[str]:
    candidates: List[str] = []
    env_bin = os.environ.get("LIBREOFFICE_BIN", "").strip()
    if env_bin:
        candidates.append(env_bin)
    for name in ("soffice", "soffice.exe", "libreoffice"):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    if sys.platform.startswith("win"):
        candidates.extend(
            [
                r"C:\Program Files\LibreOffice\program\soffice.exe",
                r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
            ]
        )
    candidates.extend(
        [
            "/usr/bin/soffice",
            "/Applications/LibreOffice.app/Contents/MacOS/soffice",
        ]
    )
    return candidates


def detect_libreoffice() -> str | None:
    """依次检查环境变量、PATH、常见安装路径，返回第一个存在的 soffice 路径。"""
    for candidate in _libreoffice_candidates():
        if not candidate:
            continue
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
        if os.path.isfile(candidate):
            return candidate
    return None


def export_pdf(
    docx_path: str | Path, output_dir: str | Path | None = None, timeout: int = 120
) -> str | None:
    """用 LibreOffice headless 把 docx 转成 pdf。

    失败（找不到 soffice / 超时 / 非 0 退出码 / 产物不存在）一律返回 `None`，**不抛异常**。
    """
    try:
        source = Path(docx_path)
        if not source.is_file():
            return None
        binary = detect_libreoffice()
        if not binary:
            return None
        target_dir = Path(output_dir) if output_dir else source.parent
        target_dir.mkdir(parents=True, exist_ok=True)
        try:
            timeout_value = float(timeout)
        except (TypeError, ValueError):
            timeout_value = 120.0
        if timeout_value <= 0:
            timeout_value = 120.0

        work_dir = target_dir
        work_name = f"{_PDF_TMP_PREFIX}{source.stem}.docx"
        work_path = work_dir / work_name
        shutil.copyfile(str(source), str(work_path))
        expected = work_dir / f"{work_path.stem}.pdf"

        command = [
            binary,
            "--headless",
            "--norestore",
            "--convert-to",
            "pdf",
            "--outdir",
            str(work_dir),
            str(work_path),
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                timeout=timeout_value,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return None
        except Exception:
            return None

        if getattr(completed, "returncode", 1) != 0:
            return None
        if not expected.is_file():
            return None

        final = target_dir / f"{source.stem}.pdf"
        if final.exists() and final.resolve() != expected.resolve():
            try:
                final.unlink()
            except OSError:
                return None
        try:
            if expected.resolve() != final.resolve():
                shutil.move(str(expected), str(final))
        except Exception:
            return None
        return str(final) if final.is_file() else None
    except Exception:
        return None
    finally:
        try:
            for leftover in Path(output_dir or Path(docx_path).parent).glob(
                f"{_PDF_TMP_PREFIX}*"
            ):
                try:
                    leftover.unlink()
                except OSError:
                    pass
        except Exception:
            pass


# --------------------------------------------------------------------------
# 十四、公开接口：文本提取与文件命名
# --------------------------------------------------------------------------


def _table_lines(table: Table) -> List[str]:
    lines: List[str] = []
    for row in table.rows:
        for cell in row.cells:
            for child in cell._element.iterchildren():
                if child.tag == qn("w:p"):
                    paragraph = _container_of(child, cell)
                    if paragraph is not None:
                        lines.append(paragraph.text)
                elif child.tag == qn("w:tbl"):
                    try:
                        nested = Table(child, cell)
                    except Exception:
                        continue
                    lines.extend(_table_lines(nested))
    return lines


def extract_document_text(path: str | Path) -> str:
    """按文档顺序提取所有段落文本与表格单元格文本，用 `\\n` 连接。"""
    source = Path(path)
    if not source.is_file():
        raise DocgenUnavailable(f"文档不存在：{str(source)!r}")
    document = _load_document(source)
    lines: List[str] = []
    for child in document.element.body.iterchildren():
        if child.tag == qn("w:p"):
            paragraph = _container_of(child, document)
            if paragraph is not None:
                lines.append(paragraph.text)
        elif child.tag == qn("w:tbl"):
            try:
                table = Table(child, document)
            except Exception:
                continue
            lines.extend(_table_lines(table))
    return "\n".join(lines)


def document_filename(case_no: str, doc_type: str, version_no: int, fmt: str) -> str:
    """交付文件名：`{case_no}-{doc_type}-v{version_no}.{fmt}`（R-51）。"""
    case = _clean(case_no)
    kind = _clean(doc_type) or "report"
    try:
        version = int(version_no)
    except (TypeError, ValueError):
        version = 1
    if version < 1:
        version = 1
    suffix = _clean(fmt).lstrip(".").lower() or "docx"
    return f"{case}-{kind}-v{version}.{suffix}"
