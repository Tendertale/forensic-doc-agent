"""可选模型抽取接口（默认关闭，架构 5.9；对应 TODO-DL-5 的接入位）。

职责边界（必须严格遵守）：

* 模型**只能挑选并标注来源块**：输入是本案 `ocr_blocks`，输出只能是
  ``{"fields": [{"field_key", "source_block_id", "value"}]}``；
* 不在 `FIELD_INDEX` 中的 ``field_key`` **一律丢弃**，来源块不在本次输入中的也丢弃；
* 不生成鉴定意见（N-1）：提示词明确禁止推断死亡原因、死亡时间、致伤工具与损伤机制；
* **密钥只从参数来**：只放在 ``Authorization`` 请求头里，不写数据库、不写日志、
  不写源码或构建产物，也不进入任何返回值；出错时只返回可读中文信息，
  **不透传上游正文**（架构 4.2）；
* 错误映射：401 密钥无效 / 429 限流 / 502 上游不可用 / 504 超时；
* 本模块不接触数据库，因此模型请求期间**不可能**持有 SQLite 写事务（AGENTS.md 约束 10）；
* 测试必须用 ``httpx.MockTransport``，禁止访问真实服务；本模块也不在顶层 import
  ``httpx``，未安装时其余功能照常可用。
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any, Dict, List, Mapping, Optional, Sequence

from .constants import FIELD_INDEX

if TYPE_CHECKING:  # pragma: no cover - 仅用于类型标注
    import httpx

__all__ = [
    "LlmAPIError",
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "verify_key",
    "extract_with_llm",
]

#: 环境变量默认值（与参考项目一致；密钥永远不来自环境变量）。
DEFAULT_BASE_URL = (os.getenv("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").rstrip("/")
DEFAULT_MODEL = os.getenv("DEEPSEEK_MODEL") or "deepseek-v4-flash"

#: 连通性探测 / 抽取请求的超时（秒）。
VERIFY_TIMEOUT = 8.0
EXTRACT_TIMEOUT = 30.0

#: 允许模型挑选的字段目录（键 → 中文名），用于提示词与结果过滤。
_FIELD_MENU: List[Dict[str, str]] = [
    {"field_key": item.key, "field_label": item.label}
    for item in FIELD_INDEX.values()
]

_SYSTEM_PROMPT = (
    "你是法医尸表检验记录的结构化助手。你只能从用户给出的文本块中"
    "“挑选并标注来源”：把已经写在某个文本块里的字段值原样摘出，并给出该文本块的 block_id。\n"
    "硬性要求：\n"
    "1. 只输出 JSON，不要输出 Markdown 或解释文字；\n"
    "2. 不得推断、补全或改写任何事实：不补数量、深度、形态、颜色、单位，"
    "不改左右侧，不改否定词；原文没有的内容一律不输出；\n"
    "3. 不得生成死亡原因、死亡时间、致伤工具、损伤形成机制等鉴定意见；\n"
    "4. field_key 只能取给定字段目录中的键，不得自造；\n"
    "5. 每个字段最多给出一处最能代表原文的来源块；找不到就省略该字段。"
)

_USER_TEMPLATE = (
    "字段目录（只能使用这些 field_key）：{menu}\n\n"
    "本案识别文本块（block_id 必须原样引用）：\n{blocks}\n\n"
    '请只输出形如 {{"fields":[{{"field_key":"subject_name","source_block_id":"12","value":"张三"}}]}} 的 JSON。'
)


class LlmAPIError(Exception):
    """模型服务错误（状态码 + 可读中文信息，不含上游正文）。"""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = int(status_code)
        self.message = str(message)

    def as_dict(self) -> Dict[str, Any]:
        return {"status_code": self.status_code, "message": self.message}


# --------------------------------------------------------------------------
# 内部工具
# --------------------------------------------------------------------------


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _mapping(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "__dataclass_fields__"):
        return {name: getattr(value, name) for name in value.__dataclass_fields__}
    data = getattr(value, "__dict__", None)
    return dict(data) if isinstance(data, dict) else {}


def _base_url(base_url: str) -> str:
    return (base_url or DEFAULT_BASE_URL).rstrip("/")


def _public_error(response: Any) -> LlmAPIError:
    """把上游状态码映射成对外错误；**绝不**回传上游正文。"""

    status = int(getattr(response, "status_code", 0) or 0)
    if status == 401:
        return LlmAPIError(401, "模型服务密钥无效或已失效（401）")
    if status == 429:
        return LlmAPIError(429, "模型服务当前限流（429），请稍后重试")
    if status == 504:
        return LlmAPIError(504, "模型服务响应超时（504）")
    return LlmAPIError(502, f"模型服务暂时不可用（{status or 502}）")


def _headers(api_key: str) -> Dict[str, str]:
    """请求头：密钥只在这里出现，不写日志、不进返回值。"""

    return {"Authorization": "Bearer " + api_key, "Content-Type": "application/json"}


def _request(client: Any, method: str, url: str, api_key: str, **kwargs: Any) -> Any:
    """发起一次同步请求并做统一的错误映射（超时 504 / 连接失败 502）。"""

    import httpx  # 延迟 import：顶层不依赖可选网络库

    try:
        response = client.request(method, url, headers=_headers(api_key), **kwargs)
    except httpx.TimeoutException as error:
        raise LlmAPIError(504, "模型服务响应超时，请稍后重试") from error
    except httpx.HTTPError as error:
        raise LlmAPIError(502, "模型服务暂时不可用，请检查网络或稍后重试") from error
    if int(getattr(response, "status_code", 0) or 0) >= 400:
        raise _public_error(response)
    return response


async def _arequest(client: Any, method: str, url: str, api_key: str, **kwargs: Any) -> Any:
    """发起一次异步请求（`httpx.AsyncClient` 的 ``request`` 必须 await）。"""

    import httpx

    try:
        response = await client.request(method, url, headers=_headers(api_key), **kwargs)
    except httpx.TimeoutException as error:
        raise LlmAPIError(504, "模型服务响应超时，请稍后重试") from error
    except httpx.HTTPError as error:
        raise LlmAPIError(502, "模型服务暂时不可用，请检查网络或稍后重试") from error
    if int(getattr(response, "status_code", 0) or 0) >= 400:
        raise _public_error(response)
    return response


def _require_key(api_key: str) -> str:
    key = _text(api_key).strip()
    if not key:
        raise LlmAPIError(
            401, "未提供模型密钥：请在请求头 X-DeepSeek-API-Key 中填写（密钥只保存在内存中）"
        )
    return key


def _blocks_payload(blocks: Sequence[Mapping[str, Any]]) -> tuple[List[Dict[str, Any]], Dict[str, str]]:
    """构造给模型的文本块列表；返回 ``(载荷, token → 真实块 id)``。

    ``token`` 优先使用数据库块 id；没有 id 时使用 ``#<block_index>``，保证模型引用的
    来源可以还原成 ``ocr_blocks.id``（模型只挑来源，不生产来源）。
    """

    payload: List[Dict[str, Any]] = []
    token_map: Dict[str, str] = {}
    for index, raw in enumerate(blocks or ()):  # type: ignore[arg-type]
        data = _mapping(raw)
        text = _text(data.get("text")).strip()
        if not text:
            continue
        block_id = data.get("id") if data.get("id") is not None else data.get("block_id")
        token = _text(block_id).strip() if _text(block_id).strip() else f"#{index}"
        token_map[token] = _text(block_id).strip()
        payload.append(
            {
                "block_id": token,
                "page_number": int(data.get("page_number") or 0),
                "material_id": int(data.get("material_id") or 0),
                "column_hint": _text(data.get("column_hint")).strip(),
                "is_handwritten": bool(data.get("is_handwritten")),
                "text": text,
            }
        )
    return payload, token_map


# --------------------------------------------------------------------------
# 连通性探测
# --------------------------------------------------------------------------


def verify_key(
    api_key: str,
    base_url: str = "",
    model: str = "",
    client: Optional["httpx.Client"] = None,
) -> dict:
    """探测模型密钥是否可用（``GET /models``）。

    密钥为空时**不发起网络请求**，直接抛 :class:`LlmAPIError(401)`。
    ``client`` 用于测试注入 ``httpx.MockTransport``；未提供时临时创建并关闭。
    """

    import httpx  # 延迟 import

    key = _require_key(api_key)
    root = _base_url(base_url)
    owns_client = client is None
    active = client or httpx.Client(timeout=VERIFY_TIMEOUT, trust_env=False)
    try:
        _request(active, "GET", f"{root}/models", key)
    finally:
        if owns_client:
            try:
                active.close()
            except Exception:  # pragma: no cover - 关闭失败不影响结果
                pass
    return {
        "provider": "deepseek",
        "base_url": root,
        "model": _text(model).strip() or DEFAULT_MODEL,
        "status": "ok",
    }


# --------------------------------------------------------------------------
# 抽取
# --------------------------------------------------------------------------


def _parse_fields(content: str, token_map: Mapping[str, str]) -> tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    """解析并**过滤**模型输出；返回 ``(可用字段, 被丢弃项)``。"""

    try:
        payload = json.loads(content)
    except (TypeError, ValueError) as error:
        raise LlmAPIError(502, "模型返回的抽取结果不是合法 JSON") from error
    if not isinstance(payload, Mapping):
        raise LlmAPIError(502, "模型返回的抽取结果结构无效（顶层必须是对象）")

    raw_fields = payload.get("fields")
    if not isinstance(raw_fields, list):
        raise LlmAPIError(502, "模型返回的抽取结果缺少 fields 列表")

    accepted: List[Dict[str, Any]] = []
    dropped: List[Dict[str, str]] = []
    seen: set[str] = set()
    for item in raw_fields:
        data = _mapping(item)
        field_key = _text(data.get("field_key")).strip()
        if field_key not in FIELD_INDEX:
            dropped.append({"field_key": field_key or "（空）", "reason": "不在 FIELD_INDEX 中，已丢弃"})
            continue
        if field_key in seen:
            dropped.append({"field_key": field_key, "reason": "重复字段，已丢弃后出现的值"})
            continue
        value = _text(data.get("value")).strip()
        if not value:
            dropped.append({"field_key": field_key, "reason": "取值为空，已丢弃"})
            continue
        token = _text(data.get("source_block_id")).strip()
        if token not in token_map:
            dropped.append({"field_key": field_key, "reason": "来源块不在本次输入中，已丢弃"})
            continue
        raw_id = token_map.get(token, "")
        accepted.append(
            {
                "field_key": field_key,
                "field_label": FIELD_INDEX[field_key].label,
                "value": value,
                "source_block_id": int(raw_id) if raw_id.isdigit() else None,
                "source_block_ref": token,
            }
        )
        seen.add(field_key)
    return accepted, dropped


async def extract_with_llm(
    blocks: Sequence[Mapping[str, Any]],
    api_key: str,
    *,
    base_url: str = "",
    model: str = "",
    client: Optional["httpx.AsyncClient"] = None,
) -> dict:
    """让模型从本案文本块中**挑选**字段值并标注来源块（默认关闭的可选路径）。

    返回 ``{"fields": [{"field_key","field_label","value","source_block_id",
    "source_block_ref"}], "dropped": [...], "provider","model","base_url"}``。
    模型无权新增事实：取值必须能在来源块文本中找到，否则同样丢弃（N-2 兜底）。
    """

    import httpx  # 延迟 import

    key = _require_key(api_key)
    root = _base_url(base_url)
    payload, token_map = _blocks_payload(blocks)
    if not payload:
        # 没有文本块就没有可挑选的来源，直接返回空结果（不占用额度、不猜内容）。
        return {
            "fields": [],
            "dropped": [],
            "provider": "deepseek",
            "model": _text(model).strip() or DEFAULT_MODEL,
            "base_url": root,
        }

    menu = json.dumps(_FIELD_MENU, ensure_ascii=False)
    body = _USER_TEMPLATE.format(menu=menu, blocks=json.dumps(payload, ensure_ascii=False))
    request_payload = {
        "model": _text(model).strip() or DEFAULT_MODEL,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": body},
        ],
    }

    owns_client = client is None
    active = client or httpx.AsyncClient(timeout=EXTRACT_TIMEOUT, trust_env=False)
    try:
        response = await _arequest(
            active, "POST", f"{root}/chat/completions", key, json=request_payload
        )
        try:
            payload_body = response.json()
        except Exception as error:
            raise LlmAPIError(502, "模型返回的内容不是合法 JSON") from error
        try:
            content = payload_body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise LlmAPIError(502, "模型返回的抽取结果结构无效") from error
        fields, dropped = _parse_fields(_text(content), token_map)
    finally:
        if owns_client:
            try:
                await active.aclose()
            except Exception:  # pragma: no cover
                pass

    # 兜底：取值必须能在其来源块文本里找到，否则视为模型自行编造，丢弃（N-2）。
    texts = {_text(item["block_id"]): _text(item["text"]) for item in payload}
    verified: List[Dict[str, Any]] = []
    for item in fields:
        source_text = texts.get(_text(item["source_block_ref"]), "")
        if item["value"] and item["value"] in source_text:
            verified.append(item)
        else:
            dropped.append(
                {"field_key": item["field_key"], "reason": "取值未能在来源块原文中找到，已丢弃（N-2）"}
            )

    return {
        "fields": verified,
        "dropped": dropped,
        "provider": "deepseek",
        "model": _text(model).strip() or DEFAULT_MODEL,
        "base_url": root,
    }
