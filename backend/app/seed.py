"""演示案件与演示材料（TODO-DL-6 / TODO-DL-7）。

本模块生成**完全程序化的合成演示数据**，用于打通流程与自动化测试：

* **不代表任何真实案件**：`GET /api/config` 以 `demo_data: true` 标注；
* **不含任何真实证据**：扫描页是程序化绘制的文字图，部位照片是**纯色占位图 + 一行
  “演示占位图：<label>”**，绝无真实人体内容；
* **系统不读取照片内容**（N-11）：占位照片只按用户指定的 `position_key` 放进对应栏位，
  4 张照片里 3 张分别指派到不同位置并设置 `sort_order`，1 张**不指派**以触发
  `PHOTO_POSITION_MISSING`（R-57）；
* **sidecar 命名约定**（架构 5.4）：识别用副本 `derived/<kind>/<stem>_p<N>.png` 与
  OCR JSON `derived/<kind>/<stem>_p<N>.ocr.json` 同目录同主名，`SidecarOcrEngine()`
  默认参数即可找到；
* 演示记录本含**印刷块与手写块、一处低置信度、一条否定词、两处损伤、一处跨材料冲突、
  一处空白（未记录）**，以及 `intake` 事件（`create_case` 写入 `event_type='intake'`）。

TODO-DL-6（真实样本）与 TODO-DL-7（真实部位照片）**不解除**：接入真实数据时替换本模块
的演示生成逻辑即可，接口保持不变。
"""

from __future__ import annotations

import base64
import importlib
import io
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .constants import (
    DEFAULT_TEMPLATE_VERSION,
    PHOTO_POSITION_DEFINITIONS,
    PHOTO_SECTION_LABELS,
)

__all__ = [
    "DEMO_PHOTO_PLAN",
    "demo_sidecar_blocks",
    "demo_commission_blocks",
    "demo_photo_labels",
    "ensure_demo_template",
    "seed_demo_case",
]

#: 演示数据标注（前端与 `GET /api/config` 共用）。
DEMO_NOTE = "演示数据：由 backend/app/seed.py 程序化生成，不含真实案件与真实人体内容"

#: 占位照片计划：`(position_key, 说明, sort_order)`；`position_key` 为空表示**故意不指派**，
#: 用于触发 `PHOTO_POSITION_MISSING`（R-57）。
DEMO_PHOTO_PLAN: Tuple[Tuple[str, str, int], ...] = (
    ("body_overall", "演示占位图：全身", 10),
    ("head", "演示占位图：头部", 10),
    ("chest", "演示占位图：胸部", 20),
    ("", "演示占位图：未指派位置", 0),
)

#: 中文字体候选（仅用于把说明文字画进占位图；找不到时退回 Pillow 内置位图字体）。
_FONT_CANDIDATES: Tuple[str, ...] = (
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/PingFang.ttc",
)
_HAND_FONT_CANDIDATES: Tuple[str, ...] = (
    "C:/Windows/Fonts/simkai.ttf",
    "C:/Windows/Fonts/simhei.ttf",
) + _FONT_CANDIDATES

#: 占位图的纯色背景（按序循环；不构成任何解剖学含义）。
_PLACEHOLDER_COLORS: Tuple[Tuple[int, int, int], ...] = (
    (214, 232, 248),
    (226, 240, 222),
    (250, 236, 214),
    (238, 226, 244),
)


# --------------------------------------------------------------------------
# 程序化图片生成（不含任何真实证据内容）
# --------------------------------------------------------------------------


def _font(size: int, handwritten: bool = False):
    """按候选路径加载中文字体；全部缺失时退回 Pillow 内置字体。"""

    from PIL import ImageFont

    for candidate in (_HAND_FONT_CANDIDATES if handwritten else _FONT_CANDIDATES):
        try:
            if Path(candidate).is_file():
                return ImageFont.truetype(candidate, size)
        except Exception:  # noqa: BLE001 - 字体问题不阻断演示数据生成
            continue
    return ImageFont.load_default()


def _png_bytes(image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def placeholder_photo_bytes(label: str, index: int = 0,
                            size: Tuple[int, int] = (960, 720)) -> bytes:
    """纯色占位图 + 一行说明文字（TODO-DL-7：绝非真实证据）。"""

    from PIL import Image, ImageDraw

    background = _PLACEHOLDER_COLORS[index % len(_PLACEHOLDER_COLORS)]
    image = Image.new("RGB", size, background)
    draw = ImageDraw.Draw(image)
    width, height = size
    # 对角交叉线 + 边框：明确标注这是占位图，不含任何人体内容
    draw.line((0, 0, width, height), fill=(198, 198, 198), width=3)
    draw.line((0, height, width, 0), fill=(198, 198, 198), width=3)
    draw.rectangle((6, 6, width - 7, height - 7), outline=(120, 120, 120), width=4)
    draw.text((48, int(height * 0.45)), label, fill=(44, 44, 66), font=_font(40))
    return _png_bytes(image)


def demo_scan_bytes(title: str, lines: Sequence[Tuple[str, str]],
                    size: Tuple[int, int] = (1240, 1754)) -> bytes:
    """程序化“扫描页”：白底 + 页框 + 若干行文字。

    `lines` 每项为 `(文字, 字型)`，字型取 `"printed"` 或 `"handwritten"`。
    """

    from PIL import Image, ImageDraw

    image = Image.new("RGB", size, (252, 250, 245))
    draw = ImageDraw.Draw(image)
    width, height = size
    draw.rectangle((60, 60, width - 60, height - 60), outline=(150, 150, 150), width=2)
    draw.text((96, 110), title, fill=(20, 20, 20), font=_font(38))
    draw.line((96, 168, width - 96, 168), fill=(90, 90, 90), width=2)
    y = 220
    for text, style in lines:
        handwritten = style == "handwritten"
        draw.text((112, y), text, fill=(26, 34, 96) if handwritten else (18, 18, 18),
                  font=_font(32, handwritten=handwritten))
        y += 66
    return _png_bytes(image)


# --------------------------------------------------------------------------
# 演示识别结果（sidecar JSON）
# --------------------------------------------------------------------------


def _block(text: str, confidence: float, bbox: Sequence[float], handwritten: bool,
           column_hint: str = "") -> Dict[str, Any]:
    return {
        "text": text,
        "confidence": round(float(confidence), 2),
        "bbox": [round(float(item), 4) for item in bbox],
        "is_handwritten": bool(handwritten),
        "column_hint": column_hint,
    }


def demo_commission_blocks(case_no: str = "") -> Dict[str, Any]:
    """委托书识别结果（印刷块；姓名与记录本冲突，构成 R-44 的一处材料冲突）。"""

    blocks = [
        _block("委托书", 0.98, [0.08, 0.06, 0.30, 0.11], False),
        _block("委托单位：某市公安局某分局", 0.96, [0.10, 0.16, 0.62, 0.21], False, "委托单位"),
        _block("姓名：李某某", 0.95, [0.10, 0.24, 0.36, 0.29], False, "姓名"),
        _block("委托事项：对李某某进行尸表检验", 0.94, [0.10, 0.32, 0.72, 0.37], False, "委托事项"),
        _block("委托日期：2026-10-11", 0.93, [0.10, 0.40, 0.46, 0.45], False, "委托日期"),
        _block("案情简介：李某某于2026年10月10日被发现死亡", 0.92,
               [0.10, 0.48, 0.82, 0.53], False, "案情简介"),
    ]
    return {"engine": "sidecar", "case_no": case_no, "material": "commission", "blocks": blocks}


def demo_sidecar_blocks(case_no: str = "") -> Dict[str, Any]:
    """记录本识别结果（架构 5.11 要求的演示块集合）。

    包含：印刷块（表头、姓名、单位、检验时间）、手写块（尸长、尸斑、尸僵、损伤）、
    **一处低置信度**（`"颈部压退"` 0.48 < `OCR_LOW_CONFIDENCE` 0.55）、
    **一条否定词**（`其他一般所见：未见皮下出血`）、**两处损伤**（左前臂擦伤 3×2cm、
    右小腿挫伤 4×3cm）、**一处空白**（`衣着` 栏为 `/` → `not_recorded`）。
    """

    blocks = [
        _block("尸表检验记录", 0.98, [0.08, 0.05, 0.34, 0.10], False),
        _block("姓名：李某某", 0.97, [0.10, 0.14, 0.36, 0.19], False, "姓名"),
        _block("委托单位：某市公安局某分局", 0.96, [0.10, 0.21, 0.62, 0.26], False, "委托单位"),
        _block("检验时间：2026-10-12 09:30", 0.95, [0.10, 0.28, 0.52, 0.33], False, "检验时间"),
        _block("尸长172cm", 0.93, [0.10, 0.36, 0.36, 0.42], True, "尸长"),
        _block("背侧，紫红，指压褪色", 0.90, [0.10, 0.44, 0.52, 0.50], True, "尸斑"),
        _block("尸僵全身大关节", 0.62, [0.10, 0.52, 0.44, 0.58], True, "尸僵"),
        _block("左前臂擦伤3×2cm", 0.88, [0.10, 0.60, 0.52, 0.66], True, "损伤"),
        _block("右小腿挫伤4×3cm", 0.86, [0.10, 0.68, 0.52, 0.74], True, "损伤"),
        _block("颈部压退", 0.48, [0.10, 0.76, 0.32, 0.82], True, "尸斑"),
        _block("其他一般所见：未见皮下出血", 0.90, [0.10, 0.84, 0.62, 0.90], True, "其他一般所见"),
        _block("/", 0.70, [0.10, 0.91, 0.20, 0.95], True, "衣着"),
    ]
    return {"engine": "sidecar", "case_no": case_no, "material": "record_book", "blocks": blocks}


# --------------------------------------------------------------------------
# 演示模板
# --------------------------------------------------------------------------


def _insert_paragraph_after(paragraph, text: str):
    """在指定段落**之后**插入一个同层级的新段落，并返回它（python-docx 无直接 API）。"""

    from docx.oxml import OxmlElement
    from docx.text.paragraph import Paragraph

    new_element = OxmlElement("w:p")
    paragraph._p.addnext(new_element)
    new_paragraph = Paragraph(new_element, paragraph._parent)
    new_paragraph.add_run(text)
    return new_paragraph


def _augment_template_with_photo_slots(path: Path) -> bool:
    """把演示模板补齐到 `PHOTO_POSITION_DEFINITIONS` 的**全部**栏位（幂等）。

    D7：旧实现只要模板里出现任意 `{{ photo_* }}` 就整体跳过，于是「模板 14 个锚点 /
    位置目录 18 项」长期不闭合，`GET /photo-slots` 的 `missing` 口径与文稿占位文字口径
    对不上。现在改为**按栏位逐个判定**：缺哪个补哪个，已有的绝不重复插入。

    * 模板里已有照片锚点时，缺失的栏位插在**最后一个照片锚点段落之后**，保持同一栏目；
    * 模板里完全没有照片锚点时，先补栏目说明，再按目录顺序追加；
    * `PHOTO_POSITION_DEFINITIONS` 与 W3 的 `docgen.build_demo_template` 以
      “最终模板含 18 个锚点、且每个锚点只出现一次”为共同验收口径。
    """

    from docx import Document

    document = Document(str(path))
    body_text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    missing = [definition for definition in PHOTO_POSITION_DEFINITIONS
               if definition.anchor not in body_text]
    if not missing:
        return False

    existing_anchors = [paragraph for paragraph in document.paragraphs
                        if any(definition.anchor in paragraph.text
                               for definition in PHOTO_POSITION_DEFINITIONS)]
    cursor = existing_anchors[-1] if existing_anchors else None
    if cursor is None:
        document.add_paragraph("部位照片")
        document.add_paragraph(
            "（系统不读取照片内容，只按用户指定的位置插入；没有照片的栏位予以保留并写入"
            "“（未提供照片）”）"
        )
    for definition in missing:
        section_label = PHOTO_SECTION_LABELS.get(definition.section, definition.section)
        label_text = "{0}（{1}）".format(definition.label, section_label)
        anchor_text = "{{ " + definition.anchor + " }}"
        if cursor is None:
            document.add_paragraph(label_text)
            document.add_paragraph(anchor_text)
        else:
            cursor = _insert_paragraph_after(cursor, label_text)
            cursor = _insert_paragraph_after(cursor, anchor_text)
    document.save(str(path))
    return True


def ensure_demo_template(storage, template_version: str = DEFAULT_TEMPLATE_VERSION) -> Path:
    """生成（或复用）演示模板文件，返回其路径。模板原件写入 `templates/`（只读使用）。"""

    docgen = importlib.import_module(".docgen", __package__)
    target = Path(storage.templates_dir()) / "{0}.docx".format(template_version)
    docgen.build_demo_template(target, template_version)
    _augment_template_with_photo_slots(target)
    return target


# --------------------------------------------------------------------------
# 演示案件
# --------------------------------------------------------------------------


def _current_version(database, case_id: int) -> int:
    case_data = database.get_case(case_id) or {}
    return int(case_data.get("version") or 1)


def _register_scan(database, storage, case_id: int, case_no: str, *, kind: str,
                   filename: str, title: str, lines: Sequence[Tuple[str, str]],
                   blocks: Mapping[str, Any]) -> int:
    """登记一份“扫描件”材料：原件 PNG + 识别用副本 + sidecar OCR JSON。"""

    image = demo_scan_bytes(title, lines)
    material = database.add_material(case_id, {
        "kind": kind,
        "filename": filename,
        "media_type": "png",
        "page_count": 1,
        "content_base64": base64.b64encode(image).decode("ascii"),
        "clarity_note": "演示用程序化生成页（非真实扫描件）",
    }, _current_version(database, case_id), storage, actor="系统")
    material_id = int(material["id"])

    stem = Path(filename).stem
    derived_name = "{0}_p1.png".format(stem)
    derived_relative = storage.write_derived(case_no, kind, derived_name, image)
    # 演示副本与原件内容一致（不做真实预处理）；记录 image_path 以便 OCR 找到 sidecar
    database.save_preprocessing(material_id, 1, [], derived_relative)

    sidecar = storage.derived_dir(case_no, kind) / "{0}_p1.ocr.json".format(stem)
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(json.dumps(dict(blocks), ensure_ascii=False, indent=2),
                       encoding="utf-8")
    database.add_event(case_id, "material_recognized_source", "识别用副本与 sidecar 已生成",
                       "{0} → {1} / {2}".format(filename, derived_relative, sidecar.name),
                       "系统")
    return material_id


def _register_photos(database, storage, case_id: int, case_no: str) -> List[Dict[str, Any]]:
    """登记 4 张程序化占位照片（3 张指派位置，1 张不指派）。"""

    created: List[Dict[str, Any]] = []
    for index, (position_key, label, sort_order) in enumerate(DEMO_PHOTO_PLAN, start=1):
        tag = position_key or "unassigned"
        filename = "photo_{0:02d}_{1}.png".format(index, tag)
        payload = placeholder_photo_bytes(label, index - 1)
        material = database.add_material(case_id, {
            "kind": "photo",
            "filename": filename,
            "media_type": "png",
            "content_base64": base64.b64encode(payload).decode("ascii"),
            "position_key": position_key,
            "photo_caption": label,
            "sort_order": sort_order,
            "clarity_note": "程序化生成的纯色占位图（TODO-DL-7：非真实照片）",
        }, _current_version(database, case_id), storage, actor="系统")
        # 只记录几何元数据（宽高像素），不读取、不描述任何像素内容（N-11）
        geom = _image_size(storage.absolute(material.get("original_path") or ""))
        if geom:
            database.update_material_page(
                int(material["id"]), 1,
                {"note": "占位图尺寸 {0}×{1} 像素（几何元数据，系统不读取照片内容）".format(*geom)},
                0, actor="系统")
        created.append(material)
    return created


def _image_size(path) -> Optional[Tuple[int, int]]:
    """只读图片头部记录的像素宽高（不解码像素内容）。"""

    try:
        from PIL import Image

        with Image.open(str(path)) as image:
            return int(image.width), int(image.height)
    except Exception:  # noqa: BLE001 - 尺寸不可得时不影响登记
        return None


def seed_demo_case(database, storage, *, index: int = 1) -> int:
    """建立一个完整的演示案件，返回 `case_id`。

    组成：委托书 + 记录本（含 sidecar OCR）+ 程序化演示模板（含照片栏位）
    + 4 张程序化占位照片 + 术语种子规则 + `intake` 事件。
    """

    storage.ensure()
    surnames = ("李某某", "王某某", "赵某某", "陈某某")
    subject = surnames[(max(1, int(index)) - 1) % len(surnames)]
    commission_unit = "某市公安局某分局"
    template_version = DEFAULT_TEMPLATE_VERSION

    # 1) 演示模板（含 {{ photo_<key> }} 栏位）+ 术语种子规则
    template_path = ensure_demo_template(storage, template_version)
    try:
        database.import_seed_rules()
    except Exception:  # noqa: BLE001 - 规则导入失败不阻断演示案件
        pass

    # 2) 建案（create_case 会写入 case_created 与 intake 两条事件）
    case_data = database.create_case({
        "commission_unit": commission_unit,
        "commission_matter": "对{0}进行尸表检验并整理检验记录".format(subject),
        "commission_date": "2026-10-11",
        "case_summary": "{0}于2026年10月10日被发现死亡，委托进行尸表检验。".format(subject),
        "subject_name": subject,
        "template_version": template_version,
        "note": DEMO_NOTE,
        "intake": {
            "input_materials": "委托书扫描件 1 页、记录本扫描件 1 页、部位照片 4 张（程序化占位图）",
            "output_documents": "尸表检验记录 Word 草稿与 PDF 预览",
            "template_requirement": "使用 {0} 模板，保持标题、表格与照片栏位".format(template_version),
            "content_requirement": "只整理材料已记载的内容；空白栏目写“未记录”；部位照片只按指定位置插入",
            "format_requirement": "字体、字号、行距沿用模板；照片保持纵横比、不裁切",
            "final_artifacts": "docx,pdf",
            "open_questions": "姓名在委托书与记录本中不一致，需人工确认（演示用冲突）",
        },
    }, actor="系统")
    case_id = int(case_data["id"])
    case_no = str(case_data["case_no"])

    # 3) 模板材料（原件写入 originals/template/，模板文件本身只读使用）
    template_bytes = Path(template_path).read_bytes()
    database.add_material(case_id, {
        "kind": "template",
        "filename": "{0}.docx".format(template_version),
        "media_type": "docx",
        "page_count": 0,
        "content_base64": base64.b64encode(template_bytes).decode("ascii"),
        "clarity_note": "程序化生成的演示模板（TODO-DL-6：非正式模板）",
    }, _current_version(database, case_id), storage, actor="系统")

    # 4) 委托书与记录本（含 sidecar OCR JSON，同目录同主名）
    _register_scan(
        database, storage, case_id, case_no,
        kind="commission", filename="commission_scan.png", title="委托书",
        lines=[
            ("委托单位：某市公安局某分局", "printed"),
            ("姓名：李某某", "printed"),
            ("委托事项：对{0}进行尸表检验".format(subject), "printed"),
            ("委托日期：2026-10-11", "printed"),
            ("案情简介：{0}于2026年10月10日被发现死亡".format(subject), "handwritten"),
        ],
        blocks=demo_commission_blocks(case_no),
    )
    _register_scan(
        database, storage, case_id, case_no,
        kind="record_book", filename="record_book_scan.png", title="尸表检验记录",
        lines=[
            ("姓名：李某某", "handwritten"),
            ("检验时间：2026-10-12 09:30", "printed"),
            ("尸长172cm", "handwritten"),
            ("背侧，紫红，指压褪色", "handwritten"),
            ("尸僵全身大关节", "handwritten"),
            ("左前臂擦伤3×2cm", "handwritten"),
            ("右小腿挫伤4×3cm", "handwritten"),
        ],
        blocks=demo_sidecar_blocks(case_no),
    )

    # 5) 部位照片（N-11：只登记文件与位置元数据）
    _register_photos(database, storage, case_id, case_no)

    # 6) 说明性审计事件（intake 事件由 create_case 写入）
    database.add_event(case_id, "demo_data", "演示数据已生成",
                       DEMO_NOTE + "；案件编号 {0}".format(case_no), "系统")
    return case_id


def demo_photo_labels() -> List[str]:
    """演示照片的说明文字（前端可用于展示“演示占位图”标记）。"""

    return [label for _, label, _ in DEMO_PHOTO_PLAN]
