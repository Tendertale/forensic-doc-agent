"""图像与 PDF 探测、渲染与预处理。

对应需求：R-18（区分原生文字 PDF / 扫描 PDF / 含 OCR 文字层的 PDF）、
R-19（原件不改，处理结果单独保存）、第五章第 2 步（检查图像并生成识别用副本）。

设计约束：

* **纯离线**。``pypdfium2``（TODO-DL-4）与 ``PyMuPDF``（``fitz``）都在函数内部
  延迟 import，模块顶层不依赖任何可选包。
* PDF 处理**优先 pypdfium2**，其次 ``fitz``；两者都不可用时 ``render_pdf_pages``
  返回 ``[]``，``probe`` 仍按“扫描件、无文字层”报告页信息，绝不伪装成成功。
* ``extract_pdf_text`` 只读**原生文字层**，其结果只用于判断“这一页有没有文字层”，
  **不得**当作准确原文使用（R-18）。原文以 OCR 结果与原件图像为准。
* ``preprocess_image`` 只写 ``dst``（识别用副本），绝不改写 ``src``（原件，N-8）。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Sequence

from .constants import IMAGE_OPERATIONS

__all__ = [
    "PDF_SUFFIXES",
    "IMAGE_SUFFIXES",
    "PageProbe",
    "sha256_file",
    "probe",
    "extract_pdf_text",
    "render_pdf_pages",
    "preprocess_image",
]

PDF_SUFFIXES = (".pdf",)
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp", ".gif")

#: 页面内整页图像覆盖率超过该比例、且页面存在文字层时，判定为“扫描件 + OCR 文字层”。
FULL_PAGE_IMAGE_COVERAGE = 0.6

#: 纠偏搜索范围与步长（度）。
DESKEW_MAX_ANGLE = 6.0
DESKEW_STEP = 0.5

#: 二值化阈值：灰度值小于该值视为“有墨迹”。
INK_THRESHOLD = 160

#: 裁切时保留的边距比例。
CROP_MARGIN_RATIO = 0.01

#: 对比度增强倍率。
CONTRAST_FACTOR = 1.5


@dataclass
class PageProbe:
    """单页探测结果。

    ``is_scanned`` 与 ``has_text_layer`` 相互独立：

    * 原生文字 PDF：``is_scanned=False``、``has_text_layer=True``
    * 纯扫描 PDF：``is_scanned=True``、``has_text_layer=False``
    * 扫描件 + OCR 文字层：``is_scanned=True``、``has_text_layer=True``

    ``image_path`` 是识别用副本的路径；``probe`` 不渲染，因此 PDF 页在渲染前
    该字段为空字符串，由调用方在 ``render_pdf_pages`` 之后回填。
    """

    page_number: int
    width: int
    height: int
    is_scanned: bool
    has_text_layer: bool
    text_layer_chars: int
    original_path: str
    image_path: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "page_number": self.page_number,
            "width": self.width,
            "height": self.height,
            "is_scanned": self.is_scanned,
            "has_text_layer": self.has_text_layer,
            "text_layer_chars": self.text_layer_chars,
            "original_path": self.original_path,
            "image_path": self.image_path,
        }


# --------------------------------------------------------------------------
# 通用工具
# --------------------------------------------------------------------------


def sha256_file(path: str | Path) -> str:
    """返回文件内容的 SHA-256 十六进制摘要（分块读取，不整文件载入内存）。"""

    digest = hashlib.sha256()
    with open(Path(path), "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _clean_text(value: Any) -> str:
    text = "" if value is None else str(value)
    return re.sub(r"[ \t\u3000]+", " ", text.replace("\r\n", "\n").replace("\r", "\n")).strip()


def _is_pdf(path: Path) -> bool:
    return path.suffix.lower() in PDF_SUFFIXES


def _is_image(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_SUFFIXES


def _count_pdf_pages_from_bytes(path: Path) -> int:
    """两个 PDF 库都不可用时，用最朴素的方式估计页数（仅用于给出占位页）。"""

    try:
        raw = path.read_bytes()
    except OSError:
        return 1
    count = len(re.findall(rb"/Type\s*/Page[^s]", raw))
    return max(count, 1)


# --------------------------------------------------------------------------
# PDF 探测（pypdfium2 优先，fitz 兜底）
# --------------------------------------------------------------------------


def _classify_page(text_layer_chars: int, image_coverage: float) -> tuple[bool, bool]:
    """返回 ``(is_scanned, has_text_layer)``。"""

    if text_layer_chars <= 0:
        return True, False
    if image_coverage >= FULL_PAGE_IMAGE_COVERAGE:
        # 整页图像 + 文字层：扫描件 + OCR 文字层。
        return True, True
    return False, True


def _probe_pdf_pypdfium2(path: Path) -> List[PageProbe]:
    import pypdfium2 as pdfium  # 延迟 import（TODO-DL-4）
    import pypdfium2.raw as pdfium_raw

    probes: List[PageProbe] = []
    document = pdfium.PdfDocument(str(path))
    try:
        for index in range(len(document)):
            page = document[index]
            width_f, height_f = page.get_size()
            width = int(round(width_f))
            height = int(round(height_f))
            try:
                text_page = page.get_textpage()
                text = text_page.get_text_range() or ""
            except Exception:  # pragma: no cover - 个别损坏页
                text = ""
            chars = len(_clean_text(text))
            coverage = 0.0
            try:
                area = max(width_f * height_f, 1.0)
                for obj in page.get_objects(filter=(pdfium_raw.FPDF_PAGEOBJ_IMAGE,)):
                    left, bottom, right, top = obj.get_bounds()
                    ratio = max(0.0, (right - left)) * max(0.0, (top - bottom)) / area
                    coverage = max(coverage, ratio)
            except Exception:  # pragma: no cover - 结构异常页
                coverage = 0.0
            is_scanned, has_text_layer = _classify_page(chars, coverage)
            probes.append(
                PageProbe(
                    page_number=index + 1,
                    width=width,
                    height=height,
                    is_scanned=is_scanned,
                    has_text_layer=has_text_layer,
                    text_layer_chars=chars,
                    original_path=str(path),
                    image_path="",
                )
            )
    finally:
        document.close()
    return probes


def _probe_pdf_fitz(path: Path) -> List[PageProbe]:
    import fitz  # 延迟 import（PyMuPDF）

    probes: List[PageProbe] = []
    document = fitz.open(str(path))
    try:
        for index, page in enumerate(document):
            rect = page.rect
            width = int(round(rect.width))
            height = int(round(rect.height))
            try:
                text = page.get_text() or ""
            except Exception:  # pragma: no cover - 个别损坏页
                text = ""
            chars = len(_clean_text(text))
            coverage = 0.0
            try:
                area = max(float(rect.width) * float(rect.height), 1.0)
                for info in page.get_images(full=True):
                    for image_rect in page.get_image_rects(info[0]):
                        ratio = (
                            max(0.0, float(image_rect.width))
                            * max(0.0, float(image_rect.height))
                            / area
                        )
                        coverage = max(coverage, ratio)
            except Exception:  # pragma: no cover - 结构异常页
                coverage = 0.0
            is_scanned, has_text_layer = _classify_page(chars, coverage)
            probes.append(
                PageProbe(
                    page_number=index + 1,
                    width=width,
                    height=height,
                    is_scanned=is_scanned,
                    has_text_layer=has_text_layer,
                    text_layer_chars=chars,
                    original_path=str(path),
                    image_path="",
                )
            )
    finally:
        document.close()
    return probes


def _probe_pdf(path: Path) -> List[PageProbe]:
    try:
        probes = _probe_pdf_pypdfium2(path)
        if probes:
            return probes
    except Exception:
        pass
    try:
        probes = _probe_pdf_fitz(path)
        if probes:
            return probes
    except Exception:
        pass
    # 两个库都不可用（或都失败）：按扫描件、无文字层报告，页数用字节估计。
    count = _count_pdf_pages_from_bytes(path)
    return [
        PageProbe(
            page_number=index + 1,
            width=0,
            height=0,
            is_scanned=True,
            has_text_layer=False,
            text_layer_chars=0,
            original_path=str(path),
            image_path="",
        )
        for index in range(count)
    ]


def _probe_image(path: Path) -> List[PageProbe]:
    try:
        from PIL import Image  # 延迟 import

        with Image.open(path) as image:
            width, height = image.size
    except Exception:
        return []
    return [
        PageProbe(
            page_number=1,
            width=int(width),
            height=int(height),
            is_scanned=True,
            has_text_layer=False,
            text_layer_chars=0,
            original_path=str(path),
            image_path=str(path),
        )
    ]


def probe(path: str | Path) -> List[PageProbe]:
    """探测 PDF 或图像文件，返回逐页信息。

    * PDF：优先 ``pypdfium2``，其次 ``PyMuPDF``；两者都不可用时返回按字节估计页数的
      占位页，并标记 ``is_scanned=True`` / ``has_text_layer=False``。
    * 图像（JPG/PNG/…）：返回单页，``is_scanned=True``。
    * 其他类型或文件不存在：返回 ``[]``（由调用方记入校核清单，不抛异常）。
    """

    target = Path(path)
    if not target.is_file():
        return []
    if _is_pdf(target):
        return _probe_pdf(target)
    if _is_image(target):
        return _probe_image(target)
    return []


# --------------------------------------------------------------------------
# PDF 文字层与页面渲染
# --------------------------------------------------------------------------


def _pdf_text_pypdfium2(path: Path) -> str:
    import pypdfium2 as pdfium  # 延迟 import

    pages: List[str] = []
    document = pdfium.PdfDocument(str(path))
    try:
        for index in range(len(document)):
            try:
                text = document[index].get_textpage().get_text_range() or ""
            except Exception:  # pragma: no cover
                text = ""
            pages.append(_clean_text(text))
    finally:
        document.close()
    return "\n".join(pages)


def _pdf_text_fitz(path: Path) -> str:
    import fitz  # 延迟 import

    pages: List[str] = []
    document = fitz.open(str(path))
    try:
        for page in document:
            try:
                pages.append(_clean_text(page.get_text() or ""))
            except Exception:  # pragma: no cover
                pages.append("")
    finally:
        document.close()
    return "\n".join(pages)


def extract_pdf_text(path: str | Path) -> str:
    """读取 PDF 的**原生文字层**。

    只用于判断页面是否存在文字层与 OCR 结果比对（R-18）。文字层可能来自扫描件的
    旧 OCR，**不得**把它当作准确原文；原文以原件图像与本案 OCR 结果为准。
    非 PDF、文件不存在或两个 PDF 库都不可用时返回空字符串。
    """

    target = Path(path)
    if not target.is_file() or not _is_pdf(target):
        return ""
    for reader in (_pdf_text_pypdfium2, _pdf_text_fitz):
        try:
            return reader(target)
        except Exception:
            continue
    return ""


def _render_pypdfium2(pdf_path: Path, out_dir: Path, dpi: int, prefix: str) -> List[str]:
    import pypdfium2 as pdfium  # 延迟 import

    scale = max(int(dpi), 1) / 72.0
    written: List[str] = []
    document = pdfium.PdfDocument(str(pdf_path))
    try:
        for index in range(len(document)):
            image = document[index].render(scale=scale).to_pil()
            target = out_dir / f"{prefix}{index + 1}.png"
            image.save(str(target), format="PNG")
            written.append(str(target))
    finally:
        document.close()
    return written


def _render_fitz(pdf_path: Path, out_dir: Path, dpi: int, prefix: str) -> List[str]:
    import fitz  # 延迟 import

    written: List[str] = []
    document = fitz.open(str(pdf_path))
    try:
        for index, page in enumerate(document):
            pixmap = page.get_pixmap(dpi=max(int(dpi), 1))
            target = out_dir / f"{prefix}{index + 1}.png"
            pixmap.save(str(target))
            written.append(str(target))
    finally:
        document.close()
    return written


def render_pdf_pages(
    pdf_path: str | Path,
    out_dir: str | Path,
    dpi: int = 200,
    prefix: str = "page",
) -> List[str]:
    """把 PDF 逐页渲染为 PNG（识别用副本，不修改原件）。

    输出文件名 = ``f"{prefix}{页码}.png"``，页码从 1 开始。workflow 传
    ``prefix=f"{stem}_p"`` 即得到架构约定的 ``<stem>_p<N>.png``。

    优先 ``pypdfium2``，其次 ``PyMuPDF``；两者都不可用时返回 ``[]``
    （由调用方在校核清单中报告，不抛异常）。
    """

    source = Path(pdf_path)
    if not source.is_file() or not _is_pdf(source):
        return []
    target_dir = Path(out_dir)
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return []

    for renderer in (_render_pypdfium2, _render_fitz):
        try:
            written = renderer(source, target_dir, dpi, prefix)
            if written:
                return written
        except Exception:
            # 渲染到一半失败时清掉半成品，再尝试下一个渲染器。
            for name in sorted(target_dir.glob(f"{prefix}*.png")):
                try:
                    name.unlink()
                except OSError:  # pragma: no cover
                    pass
            continue
    return []


# --------------------------------------------------------------------------
# 图像预处理
# --------------------------------------------------------------------------


def _deskew_angle(image: Any) -> float | None:
    """用行投影方差估计倾斜角（度）。失败返回 ``None``。"""

    try:
        from PIL import Image  # 延迟 import

        gray = image.convert("L")
        if gray.width > 400:
            ratio = 400.0 / float(gray.width)
            gray = gray.resize((400, max(1, int(gray.height * ratio))), Image.Resampling.BILINEAR)
        ink = gray.point(lambda value: 255 if value < INK_THRESHOLD else 0)

        best_angle = 0.0
        best_score = -1.0
        steps = int(round((DESKEW_MAX_ANGLE * 2) / DESKEW_STEP))
        for step in range(steps + 1):
            angle = -DESKEW_MAX_ANGLE + step * DESKEW_STEP
            rotated = ink.rotate(angle, resample=Image.Resampling.BILINEAR, expand=False, fillcolor=0)
            column = rotated.resize((1, max(1, rotated.height)), Image.Resampling.BOX)
            values = [float(value) for value in column.getdata()]
            if not values:
                continue
            mean = sum(values) / len(values)
            score = sum((value - mean) ** 2 for value in values) / len(values)
            if score > best_score:
                best_score = score
                best_angle = angle
        return best_angle
    except Exception:
        return None


def _ink_bbox(image: Any) -> tuple[int, int, int, int] | None:
    """返回墨迹外接框（含边距），无墨迹时返回 ``None``。"""

    try:
        from PIL import Image, ImageOps  # 延迟 import

        gray = ImageOps.autocontrast(image.convert("L"))
        mask = gray.point(lambda value: 255 if value < INK_THRESHOLD else 0)
        box = mask.getbbox()
        if box is None:
            return None
        left, top, right, bottom = box
        margin_x = int(round(image.width * CROP_MARGIN_RATIO))
        margin_y = int(round(image.height * CROP_MARGIN_RATIO))
        return (
            max(0, left - margin_x),
            max(0, top - margin_y),
            min(image.width, right + margin_x),
            min(image.height, bottom + margin_y),
        )
    except Exception:
        return None


def _apply_operation(image: Any, operation: str) -> tuple[Any, bool]:
    """执行一个预处理操作，返回 ``(新图像, 是否实际执行)``。"""

    from PIL import Image, ImageEnhance, ImageOps  # 延迟 import

    if operation == "rotate":
        # 固定顺时针 90°：用于修正“方向异常（rotated）”的扫描件。
        return image.rotate(-90, expand=True), True
    if operation == "deskew":
        angle = _deskew_angle(image)
        if angle is None:
            return image, False
        if abs(angle) < DESKEW_STEP / 2:
            return image, True
        return (
            image.rotate(angle, resample=Image.Resampling.BICUBIC, expand=False, fillcolor=255),
            True,
        )
    if operation == "crop":
        box = _ink_bbox(image)
        if box is None:
            return image, False
        return image.crop(box), True
    if operation == "contrast":
        return ImageEnhance.Contrast(image).enhance(CONTRAST_FACTOR), True
    if operation == "grayscale":
        return image.convert("L"), True
    if operation == "autocontrast":
        return ImageOps.autocontrast(image), True
    return image, False


def preprocess_image(src: str | Path, dst: str | Path, operations: Sequence[str]) -> dict:
    """按 ``operations`` 生成识别用副本，返回处理报告。

    返回 ``{"operations": [...], "applied": [...], "skipped": [...],
    "size": [w, h], "output": path}``。未知操作、无法执行的操作一律进 ``skipped``，
    **任何情况下都不抛异常**；``src`` 不会被修改（N-8）。
    """

    requested: List[str] = []
    for item in operations or ():
        name = str(item or "").strip()
        if name and name not in requested:
            requested.append(name)

    report: Dict[str, Any] = {
        "operations": requested,
        "applied": [],
        "skipped": [],
        "size": [0, 0],
        "output": "",
    }

    try:
        from PIL import Image  # 延迟 import

        source = Path(src)
        target = Path(dst)
        if not source.is_file():
            report["skipped"] = list(requested)
            return report

        with Image.open(source) as opened:
            image = opened.copy()
        for operation in requested:
            if operation not in IMAGE_OPERATIONS:
                report["skipped"].append(operation)
                continue
            try:
                image, applied = _apply_operation(image, operation)
            except Exception:
                report["skipped"].append(operation)
                continue
            (report["applied"] if applied else report["skipped"]).append(operation)

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            report["skipped"] = list(requested)
            return report
        fmt = "JPEG" if target.suffix.lower() in (".jpg", ".jpeg") else "PNG"
        if fmt == "JPEG" and image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        image.save(str(target), format=fmt)

        report["size"] = [int(image.width), int(image.height)]
        report["output"] = str(target)
        return report
    except Exception:
        report["skipped"] = list(requested)
        report["applied"] = []
        report["size"] = [0, 0]
        report["output"] = ""
        return report
