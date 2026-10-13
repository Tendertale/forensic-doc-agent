"""PaddleOCR 引擎（预留接口，**需要下载**：TODO-DL-2）。

本模块遵守两条硬性约束：

1. **顶层不 import `paddleocr`**。`paddleocr` 只在 :meth:`PaddleOcrEngine.available`
   与 :meth:`PaddleOcrEngine._load_engine` 内部延迟探测，因此未安装时本项目仍可正常
   import 与运行（降级到 ``sidecar`` 引擎）。
2. **不可用时明确报错，不伪装成成功**。`available()` 返回 ``False``，
   `recognize()` 抛 :class:`OcrEngineUnavailable`，消息中含 ``TODO-DL-2``，
   由 `routes.py` 映射为 HTTP 409（架构 4.2）。

部位照片（``kind="photo"``）**永远不进入识别**（N-11/INV-11）：即使被误传，
`recognize()` 也会立刻抛 :class:`OcrEngineUnavailable`，绝不读取照片像素内容。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import OcrBlock, OcrEngineUnavailable, assert_ocr_kind

__all__ = ["PaddleOcrEngine", "PADDLE_TODO", "PADDLE_MISSING_DETAIL"]

#: 该引擎对应的待办编号。
PADDLE_TODO = "TODO-DL-2"

#: 未安装时的可读说明（含 TODO-DL-2，`/api/config` 与 409 详情共用）。
PADDLE_MISSING_DETAIL = (
    "未安装 PaddleOCR（TODO-DL-2）：请在有网络的环境执行 "
    "python -m pip install paddleocr paddlepaddle 后重启服务；"
    "当前请改用 sidecar 引擎（离线 JSON 识别结果）"
)


class PaddleOcrEngine:
    """真实扫描件的印刷/手写识别引擎（TODO-DL-2 解除后启用）。

    构造 **不** 触碰 paddleocr，也不加载模型；模型在首次 `recognize()` 时惰性加载，
    避免拖慢 `GET /api/config` 之类的探测请求。
    """

    name = "paddle"

    def __init__(self, lang: str = "ch", use_angle_cls: bool = True) -> None:
        self.lang = str(lang or "ch")
        self.use_angle_cls = bool(use_angle_cls)
        self._engine: Any = None

    # -- 契约 ---------------------------------------------------------------
    def available(self) -> bool:
        """能否 import `paddleocr`（纯离线探测，不联网、不下载）。"""

        try:
            import paddleocr  # noqa: F401  （仅探测可导入性）
        except Exception:
            return False
        return True

    def describe(self) -> Dict[str, Any]:
        available = self.available()
        return {
            "name": self.name,
            "available": available,
            "detail": (
                "PaddleOCR 已安装，可用于印刷与手写文字识别（候选仍需人工核对）"
                if available
                else PADDLE_MISSING_DETAIL
            ),
            "lang": self.lang,
            "todo": "" if available else PADDLE_TODO,
        }

    def recognize(
        self,
        image_path: str,
        page_number: int,
        *,
        kind: str = "",
        column_hint: str = "",
    ) -> List[OcrBlock]:
        """识别一页图像；未安装时抛 :class:`OcrEngineUnavailable`。"""

        # N-11：部位照片不识别（在任何 import 之前就要拦住）。
        assert_ocr_kind(kind)

        if not self.available():
            raise OcrEngineUnavailable(PADDLE_MISSING_DETAIL)

        engine = self._load_engine()
        try:
            raw = engine.ocr(str(image_path), cls=self.use_angle_cls)
        except OcrEngineUnavailable:
            raise
        except Exception as error:
            raise OcrEngineUnavailable(
                f"PaddleOCR 识别失败（TODO-DL-2）：{type(error).__name__}；"
                "该页已跳过，请人工核对原件"
            ) from error
        return self._to_blocks(raw, image_path, page_number, column_hint)

    # -- 内部 ---------------------------------------------------------------
    def _load_engine(self) -> Any:
        """惰性构造 PaddleOCR 实例（只调用一次）。"""

        if self._engine is not None:
            return self._engine
        try:
            from paddleocr import PaddleOCR  # 延迟 import：TODO-DL-2
        except Exception as error:  # pragma: no cover - available() 已拦截
            raise OcrEngineUnavailable(PADDLE_MISSING_DETAIL) from error
        try:
            self._engine = PaddleOCR(use_angle_cls=self.use_angle_cls, lang=self.lang)
        except Exception as error:
            self._engine = None
            raise OcrEngineUnavailable(
                f"PaddleOCR 初始化失败（TODO-DL-2）：{type(error).__name__}；模型文件可能缺失"
            ) from error
        return self._engine

    def _to_blocks(
        self,
        raw: Any,
        image_path: str,
        page_number: int,
        column_hint: str,
    ) -> List[OcrBlock]:
        """把 PaddleOCR 的原始输出转成 :class:`OcrBlock`（归一化 bbox）。"""

        items = self._flatten(raw)
        if not items:
            return []
        size = self._image_size(image_path)
        blocks: List[OcrBlock] = []
        for index, item in enumerate(items):
            try:
                points, text, score = item
            except (TypeError, ValueError):
                continue
            content = str(text or "").strip()
            if not content:
                continue
            blocks.append(
                OcrBlock(
                    page_number=int(page_number),
                    block_index=index,
                    text=content,
                    confidence=_clamp(float(score or 0.0)),
                    bbox=_normalize_bbox(points, size),
                    engine=self.name,
                    # PaddleOCR 不区分印刷/手写，一律按“未标注”处理，避免虚假声明。
                    is_handwritten=False,
                    column_hint=str(column_hint or "").strip(),
                )
            )
        return blocks

    @staticmethod
    def _flatten(raw: Any) -> List[tuple]:
        """把 ``[[ [poly, (text, score)], ...]]`` 之类的多层结构压平成三元组列表。"""

        items: List[tuple] = []
        if not isinstance(raw, (list, tuple)):
            return items
        for entry in raw:
            if not isinstance(entry, (list, tuple)):
                continue
            for line in entry:
                if not isinstance(line, (list, tuple)) or len(line) < 2:
                    continue
                points, payload = line[0], line[1]
                if isinstance(payload, (list, tuple)) and len(payload) >= 2:
                    text, score = payload[0], payload[1]
                else:
                    text, score = payload, 0.0
                items.append((points, text, score))
        return items

    @staticmethod
    def _image_size(image_path: str) -> Optional[tuple[int, int]]:
        """读取图像宽高（**仅几何元数据**，不做任何内容识别）。"""

        try:
            from PIL import Image  # 延迟 import

            with Image.open(image_path) as image:
                width, height = image.size
            return int(width), int(height)
        except Exception:
            return None


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _normalize_bbox(points: Any, size: Optional[tuple[int, int]]) -> List[float]:
    """四点/多点包围盒 → 归一化 ``[x0, y0, x1, y1]``（0..1，原点左上角）。"""

    xs: List[float] = []
    ys: List[float] = []
    if isinstance(points, (list, tuple)):
        for point in points:
            if isinstance(point, (list, tuple)) and len(point) >= 2:
                try:
                    xs.append(float(point[0]))
                    ys.append(float(point[1]))
                except (TypeError, ValueError):
                    continue
    if not xs or not ys:
        return [0.0, 0.0, 0.0, 0.0]

    width = float(size[0]) if size else 0.0
    height = float(size[1]) if size else 0.0
    if width <= 0:
        width = max(xs) or 1.0
    if height <= 0:
        height = max(ys) or 1.0
    return [
        _clamp(min(xs) / width),
        _clamp(min(ys) / height),
        _clamp(max(xs) / width),
        _clamp(max(ys) / height),
    ]
