"""离线 sidecar OCR 引擎（默认引擎，无需任何下载）。

约定（与 W1 的 seed 对齐，见架构 5.4）：

* 识别用副本位于 ``derived/<kind>/<stem>_p<N>.png``；
* sidecar OCR JSON 与副本**同目录同主名**：``derived/<kind>/<stem>_p<N>.ocr.json``；
* ``sidecar_dir`` 为空时在 ``image_path`` 同目录查找 ``<image_stem>.ocr.json``；
  ``sidecar_dir`` 非空时改在 ``sidecar_dir/<image_stem>.ocr.json`` 查找；
* JSON 结构 ``{"engine": "sidecar", "blocks": [{"text", "confidence", "bbox",
  "is_handwritten", "column_hint"}]}``；``block_index`` 未提供时按数组下标从 0 递增，
  ``page_number`` 一律取 ``recognize()`` 传入的参数；
* **文件不存在时返回空列表**（不抛异常），由 workflow 记入校核清单。
* 部位照片（``kind="photo"``）**永不进入识别**（N-11/INV-11）：`recognize()` 第一行就调用
  :func:`assert_ocr_kind`，在任何文件读取之前抛 :class:`OcrEngineUnavailable`。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping

from .base import OcrBlock, OcrEngineUnavailable, assert_ocr_kind

__all__ = ["SidecarOcrEngine"]

_TRUE_WORDS = ("1", "true", "yes", "y", "是", "手写", "handwritten")
_FALSE_WORDS = ("0", "false", "no", "n", "否", "印刷", "printed")


def _get(row: Mapping[str, Any], key: str, default: Any = None) -> Any:
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if value is None else value


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUE_WORDS:
            return True
        if text in _FALSE_WORDS:
            return False
    return bool(value)


def _as_confidence(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, number))


def _as_bbox(value: Any) -> List[float]:
    """归一化 ``[x0, y0, x1, y1]``；结构无效时返回全 0（表示位置未知，不丢文本）。"""

    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return [0.0, 0.0, 0.0, 0.0]
    numbers: List[float] = []
    for item in value:
        try:
            numbers.append(float(item))
        except (TypeError, ValueError):
            return [0.0, 0.0, 0.0, 0.0]
    return [max(0.0, min(1.0, number)) for number in numbers]


class SidecarOcrEngine:
    """从离线 JSON 读取识别结果。默认引擎，纯离线可用。"""

    name = "sidecar"

    def __init__(self, sidecar_dir: str | None = None) -> None:
        self.sidecar_dir = str(sidecar_dir) if sidecar_dir else ""

    # -- 契约 ---------------------------------------------------------------
    def available(self) -> bool:
        return True

    def describe(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "available": True,
            "detail": "离线 JSON 引擎（默认）；未找到同名 .ocr.json 时返回空结果",
            "sidecar_dir": self.sidecar_dir,
            "todo": "",
        }

    def recognize(
        self,
        image_path: str,
        page_number: int,
        *,
        kind: str = "",
        column_hint: str = "",
    ) -> List[OcrBlock]:
        # N-11 内核兜底（与 paddle 引擎一致）：部位照片在任何文件读取之前就被拒绝，
        # 即使同目录存在同名 <stem>.ocr.json 也不读取（不生成任何 ocr_blocks）。
        assert_ocr_kind(kind)

        path = self.sidecar_path(image_path)
        if path is None or not path.is_file():
            # 文件不存在：返回空列表，由 workflow 记入校核清单（不抛异常）。
            return []

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise OcrEngineUnavailable(
                f"sidecar OCR 文件无法解析：{path}（{type(error).__name__}）"
            ) from error

        if not isinstance(payload, dict):
            raise OcrEngineUnavailable(f"sidecar OCR 文件结构无效：{path}（顶层必须是对象）")

        engine = str(payload.get("engine") or self.name)
        raw_blocks = payload.get("blocks")
        if not isinstance(raw_blocks, list):
            return []

        blocks: List[OcrBlock] = []
        for index, item in enumerate(raw_blocks):
            if not isinstance(item, Mapping):
                continue
            text = str(_get(item, "text", "") or "").strip()
            if not text:
                continue
            block_index = _get(item, "block_index", None)
            try:
                block_index = int(block_index)
            except (TypeError, ValueError):
                block_index = index
            blocks.append(
                OcrBlock(
                    page_number=int(page_number),
                    block_index=block_index,
                    text=text,
                    confidence=_as_confidence(_get(item, "confidence", None)),
                    bbox=_as_bbox(_get(item, "bbox", None)),
                    engine=str(_get(item, "engine", "") or engine),
                    is_handwritten=_as_bool(_get(item, "is_handwritten", False)),
                    column_hint=str(_get(item, "column_hint", "") or column_hint).strip(),
                )
            )
        return blocks

    # -- 辅助 ---------------------------------------------------------------
    def sidecar_path(self, image_path: str | Path) -> Path | None:
        """返回该页对应的 sidecar JSON 路径（不判断是否存在）。"""

        image = Path(str(image_path))
        if not image.name:
            return None
        filename = f"{image.stem}.ocr.json"
        if self.sidecar_dir:
            return Path(self.sidecar_dir) / filename
        return image.parent / filename
