"""OCR 引擎的公共数据结构与协议（架构 5.4）。

本模块只定义契约，不依赖任何具体引擎，也不 import 任何需要下载的第三方包。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Protocol, runtime_checkable

from ..constants import PHOTO_MATERIAL_KIND

__all__ = [
    "OcrBlock",
    "OcrEngineUnavailable",
    "OcrEngine",
    "assert_ocr_kind",
    "GuardedOcrEngine",
    "guard_engine",
]


@dataclass
class OcrBlock:
    """一段识别结果，保留原页定位。

    ``bbox`` 为归一化坐标 ``[x0, y0, x1, y1]``，取值 ``0..1``，原点在页面左上角。
    ``confidence`` 只用于安排核对优先级，**不能**作为结论（R-21/R-45）。
    """

    page_number: int
    block_index: int
    text: str
    confidence: float
    bbox: List[float]
    engine: str
    is_handwritten: bool = False
    column_hint: str = ""

    def as_row(self) -> Dict[str, Any]:
        """转为可直接写入 ``ocr_blocks`` 的字典（不含 id / case_id / material_id / page_id）。"""

        return {
            "page_number": int(self.page_number),
            "block_index": int(self.block_index),
            "text": self.text,
            "confidence": float(self.confidence),
            "bbox": [float(value) for value in self.bbox],
            "engine": self.engine,
            "is_handwritten": bool(self.is_handwritten),
            "column_hint": self.column_hint,
        }


class OcrEngineUnavailable(RuntimeError):
    """OCR 引擎不可用（未安装、缺少离线数据或引擎配置无效）。"""


def assert_ocr_kind(kind: str) -> None:
    """拒绝让部位照片进入任何识别路径（N-11 / INV-11）。

    ``kind == "photo"`` 的材料**永远不进入 OCR**：系统不读取照片像素、不做内容识别、
    描述或推断，也不调用任何视觉模型。正常流程由 ``workflow.run_ocr`` 过滤，
    这里是内核层的兜底——一旦被误传就立刻失败，而不是偷偷写出 ``ocr_blocks``。
    """

    if str(kind or "").strip() == PHOTO_MATERIAL_KIND:
        raise OcrEngineUnavailable(
            "部位照片不进行任何识别（N-11）：系统只按用户指定的位置插入照片，"
            "不读取照片内容；请检查调用方是否遗漏了 OCR_MATERIAL_KINDS 过滤"
        )


@runtime_checkable
class OcrEngine(Protocol):
    """OCR 引擎协议。所有引擎都必须能在不可用时明确报告，而不是静默返回空结果。"""

    name: str

    def available(self) -> bool:
        """引擎当前是否可用（离线探测，不联网）。"""

    def describe(self) -> Dict[str, Any]:
        """引擎自述：至少包含 ``name`` / ``available`` / ``detail``。"""

    def recognize(
        self,
        image_path: str,
        page_number: int,
        *,
        kind: str = "",
        column_hint: str = "",
    ) -> List[OcrBlock]:
        """识别一页图像；不可用时抛 :class:`OcrEngineUnavailable`。"""


class GuardedOcrEngine:
    """在**任何**引擎的最外圈统一执行 N-11 兜底校验的包装层。

    引擎注册表（``ocr.get_engine`` / ``ocr.resolve_engine``）返回的一律是本类的实例，
    因此“新增引擎忘记调用 :func:`assert_ocr_kind`”在结构上不可能发生：`recognize()`
    先做兜底校验，再转发给被包装引擎，``kind == "photo"`` 时**在任何文件读取之前**
    就抛 :class:`OcrEngineUnavailable`（不读照片、不读同名 ``.ocr.json``、不 import
    任何视觉模型）。

    ``name`` / ``available()`` / ``describe()`` 显式转发；其余属性（如 sidecar 引擎的
    ``sidecar_dir``、paddle 引擎的 ``lang``）由 ``__getattr__`` 透明转发，调用方无需知道
    自己拿到的是被包装的引擎。
    """

    #: 被包装引擎的名字（类属性占位，实例化时覆盖；便于 `hasattr` 与协议检查）。
    name: str = ""

    def __init__(self, engine: OcrEngine) -> None:
        object.__setattr__(self, "_engine", engine)
        object.__setattr__(self, "name", str(getattr(engine, "name", "") or ""))

    def __getattr__(self, item: str) -> Any:
        # 只在本类未定义该属性时触发；直接用 object.__getattribute__ 取 _engine，避免递归。
        return getattr(object.__getattribute__(self, "_engine"), item)

    def available(self) -> bool:
        return bool(self._engine.available())

    def describe(self) -> Dict[str, Any]:
        return dict(self._engine.describe() or {})

    def recognize(
        self,
        image_path: str,
        page_number: int,
        *,
        kind: str = "",
        column_hint: str = "",
    ) -> List[OcrBlock]:
        # N-11 兜底：所有引擎（含第三方注册的引擎）都必须经过这里。
        assert_ocr_kind(kind)
        return self._engine.recognize(
            image_path, page_number, kind=kind, column_hint=column_hint
        )


def guard_engine(engine: OcrEngine) -> OcrEngine:
    """把引擎包进 :class:`GuardedOcrEngine`（幂等：已包装的原样返回）。"""

    if isinstance(engine, GuardedOcrEngine):
        return engine
    return GuardedOcrEngine(engine)
