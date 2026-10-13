"""OCR 引擎注册表与选择（架构 5.4）。

本模块是**并行开发的解冻点**：其他子智能体（`workflow.py`、`routes.py`）只依赖
下面四个契约函数，不直接 import 具体引擎类：

* :func:`register_engine` —— 登记一个引擎工厂（TODO-DL-5 的云端 OCR 扩展点）；
* :func:`get_engine` —— 按名字取引擎实例（不判断可用性）；
* :func:`resolve_engine` —— 取“首选引擎”，不可用时**回退 sidecar**；
* :func:`engine_status` —— 逐引擎报告可用性与说明（供 `GET /api/config` 使用）。

设计约束：

* 本模块**顶层不 import 任何需要下载的包**（`paddleocr` 只在 `PaddleOcrEngine.available()`
  内部延迟探测），因此默认安装即可 import。
* 取不到结果与“引擎不可用”是两件事：引擎不可用时由 `recognize()` 抛
  :class:`OcrEngineUnavailable`（→ HTTP 409），而不是静默返回空列表。
* `OCR_ENGINES` 中的 ``none`` 也必须能取到：它表示**用户明确选择不做识别**，
  ``available()`` 恒为真、``recognize()`` 返回空列表（不产生任何识别结果）。
* N-11 兜底**不可绕过**：`get_engine()`（以及经由它实现的 `resolve_engine()`）返回的
  一律是 :class:`~.base.GuardedOcrEngine` 包装实例，``recognize()`` 的最外圈统一调用
  :func:`~.base.assert_ocr_kind`。因此无论是内置引擎还是通过 :func:`register_engine`
  新增的第三方引擎，``kind == "photo"`` 都会在**读取任何文件之前**抛
  :class:`OcrEngineUnavailable`——“新引擎忘记兜底”在结构上不可能发生。
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional

from ..constants import DEFAULT_OCR_ENGINE, OCR_ENGINES
from .base import OcrBlock, OcrEngine, OcrEngineUnavailable, assert_ocr_kind, guard_engine

__all__ = [
    "register_engine",
    "get_engine",
    "resolve_engine",
    "engine_status",
    "registered_engines",
    "NoneOcrEngine",
]

#: 引擎注册表：名字 → 工厂（工厂必须能在引擎不可用时也构造出实例）。
_REGISTRY: Dict[str, Callable[[], OcrEngine]] = {}


# --------------------------------------------------------------------------
# 内置引擎：none
# --------------------------------------------------------------------------


class NoneOcrEngine:
    """不做识别的占位引擎（`OCR_ENGINES` 中的 ``none``）。

    它的存在是为了让“用户明确选择不识别”成为一条**显式**路径：既不伪装成识别成功，
    也不抛异常中断流程；`recognize()` 返回空列表，由 workflow 把该页记为“未识别”。
    """

    name = "none"

    def available(self) -> bool:
        return True

    def describe(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "available": True,
            "detail": "未选择 OCR 引擎（none）：不产生任何识别结果，需人工录入原文",
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
        # N-11 兜底：即使用户明确选择“不识别”，photo 也必须立刻失败而不是静默返回空列表，
        # 否则调用方无法区分“没有识别结果”和“照片被误传进来”。
        assert_ocr_kind(kind)
        return []


# --------------------------------------------------------------------------
# 注册与取用
# --------------------------------------------------------------------------


def register_engine(name: str, factory: Callable[[], OcrEngine]) -> None:
    """登记（或覆盖）一个引擎工厂。

    ``factory`` 必须是无参可调用对象，返回实现 :class:`OcrEngine` 协议的实例；
    登记阶段**不探测**可用性，可用性由实例的 ``available()`` 回答。
    """

    key = str(name or "").strip()
    if not key:
        raise ValueError("引擎名不能为空")
    if not callable(factory):
        raise TypeError("引擎工厂必须是可调用对象")
    _REGISTRY[key] = factory


def registered_engines() -> List[str]:
    """返回已登记引擎名（顺序与 `OCR_ENGINES` 一致，未在常数表内的排在后面）。"""

    known = [name for name in OCR_ENGINES if name in _REGISTRY]
    extra = sorted(name for name in _REGISTRY if name not in OCR_ENGINES)
    return known + extra


def get_engine(name: str) -> OcrEngine:
    """按名字取引擎实例。

    只做构造，**不**判断 ``available()``；未知名字抛 :class:`OcrEngineUnavailable`
    （而不是 `KeyError`），调用方与 `routes.py` 只需处理一种异常。

    返回值一律经 :func:`~.base.guard_engine` 包装（N-11 兜底），但仍满足
    :class:`~.base.OcrEngine` 协议，`name` / `available()` / `describe()` 与其他属性透明转发。
    """

    key = str(name or "").strip() or DEFAULT_OCR_ENGINE
    factory: Optional[Callable[[], OcrEngine]] = _REGISTRY.get(key)
    if factory is None:
        raise OcrEngineUnavailable(
            f"未知 OCR 引擎：{key}（可用引擎：{'、'.join(registered_engines())}）"
        )
    engine = factory()
    if engine is None:  # pragma: no cover - 防御性分支
        raise OcrEngineUnavailable(f"OCR 引擎工厂未返回实例：{key}")
    # N-11：所有引擎（含 TODO-DL-5 的第三方注册引擎）统一在此包上兜底校验，
    # 保证“新引擎忘记 assert_ocr_kind”不可能发生。
    return guard_engine(engine)


def resolve_engine(preferred: str = DEFAULT_OCR_ENGINE) -> OcrEngine:
    """取首选引擎；首选不可用或名字未知时**回退 sidecar**。

    回退是显式行为：调用方可以在 `payload` 中对比 `resolve_engine(...).name` 与请求的
    `preferred`，从而在校核清单里报告“引擎已回退”（不静默伪装成成功）。
    """

    candidates: List[str] = []
    wanted = str(preferred or "").strip()
    if wanted:
        candidates.append(wanted)
    if DEFAULT_OCR_ENGINE not in candidates:
        candidates.append(DEFAULT_OCR_ENGINE)

    for name in candidates:
        try:
            engine = get_engine(name)
        except OcrEngineUnavailable:
            continue
        if engine.available():
            return engine

    # sidecar 恒可用；走到这里说明注册表被外部改坏（TODO-DL-5 的插件路径）。
    raise OcrEngineUnavailable(
        "没有可用的 OCR 引擎："
        f"首选 {wanted or DEFAULT_OCR_ENGINE} 与回退 {DEFAULT_OCR_ENGINE} 均不可用"
        "（sidecar 引擎应恒可用，请检查 ocr/__init__.py 的注册表）"
    )


def engine_status() -> List[Dict[str, object]]:
    """逐引擎报告 ``{"name","available","detail"}``（顺序同 `OCR_ENGINES`）。

    ``describe()`` 抛异常时按不可用处理并把异常类型写进 detail，绝不因单个引擎
    探测失败而让 `GET /api/config` 整体 500。
    """

    status: List[Dict[str, object]] = []
    for name in registered_engines():
        try:
            engine = get_engine(name)
            info = dict(engine.describe() or {})
            available = bool(info.get("available", engine.available()))
            detail = str(info.get("detail") or "")
        except Exception as error:  # pragma: no cover - 单个引擎探测失败
            available = False
            detail = f"引擎探测失败：{type(error).__name__}"
        status.append({"name": name, "available": available, "detail": detail})
    return status


# --------------------------------------------------------------------------
# 内置引擎登记（放在文件末尾，确保上面四个函数已定义）
# --------------------------------------------------------------------------


def _sidecar_factory() -> OcrEngine:
    from .sidecar import SidecarOcrEngine  # 延迟 import：避免循环依赖

    return SidecarOcrEngine()


def _paddle_factory() -> OcrEngine:
    from .paddle import PaddleOcrEngine  # 延迟 import：paddleocr 只在探测时才 import

    return PaddleOcrEngine()


register_engine("sidecar", _sidecar_factory)
register_engine("paddle", _paddle_factory)
register_engine("none", NoneOcrEngine)
