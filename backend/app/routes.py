"""HTTP 路由层（`docs/architecture.md` 第 4 节）。

统一约定（逐条落实）：

* 前缀 `/api`，JSON 请求与响应，UTF-8；错误体 `{"detail": "<中文可读信息>"}`；
* **乐观锁归属（三档规则）**：`/api/cases/**`（含 fields / injuries / materials /
  documents / check / corrections / deliver / stage）用**案件** `cases.version`；
  `/api/terminology-rules/**` 用**规则** `terminology_rules.version`；
  `/api/documents/{id}/approve` 与 `/api/checklist/{item_id}` 用**所属案件**的版本；
* `DELETE` 的 `expected_version` 放在**查询参数**（`?expected_version=N`）；
* 状态映射集中在 :func:`_handle_error`（第 4.2 节）。

密钥只从请求头 `X-DeepSeek-API-Key` 读取，**不写数据库、不写日志**。
"""

from __future__ import annotations

import importlib
import shutil
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from fastapi import APIRouter, Body, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse

from .constants import (
    CASE_STAGES,
    CASE_STAGE_LABELS,
    CHECK_CATEGORY_LABELS,
    CHECK_CATEGORIES,
    CHECK_ITEM_STATUSES,
    CHECK_ITEM_STATUS_LABELS,
    CHECK_SEVERITIES,
    CHECK_SEVERITY_LABELS,
    CORRECTION_KINDS,
    CORRECTION_KIND_LABELS,
    CORRECTION_SCOPES,
    CORRECTION_SCOPE_LABELS,
    CORRECTION_TARGET_TYPES,
    DEFAULT_OCR_ENGINE,
    DEFAULT_SECTION_ORDER,
    DEFAULT_TEMPLATE_VERSION,
    DRAFT_MARKER,
    DOC_STATUSES,
    DOC_STATUS_LABELS,
    FIELD_DEFINITIONS,
    FIELD_GROUP_LABELS,
    FIELD_GROUP_ORDER,
    FIELD_STATUSES,
    FIELD_STATUS_LABELS,
    IMAGE_OPERATIONS,
    IMAGE_QUALITY_FLAGS,
    IMAGE_QUALITY_LABELS,
    MATERIAL_KINDS,
    MATERIAL_KIND_LABELS,
    OCR_ENGINES,
    OCR_LOW_CONFIDENCE,
    OCR_MATERIAL_KINDS,
    OUTPUT_FORMATS,
    PHOTO_MAX_PER_POSITION,
    PHOTO_MAX_WIDTH_CM,
    PHOTO_PLACEHOLDER_TEXT,
    PHOTO_POSITION_DEFINITIONS,
    PHOTO_SECTION_LABELS,
    PHOTO_SECTIONS,
    PHOTO_WIDE_MAX_WIDTH_CM,
    PLACEHOLDER_PATTERNS,
    REFERENCE_STANDARDS,
    REQUIRED_FIELD_KEYS,
    REQUIRED_MATERIAL_KINDS,
    REVIEW_STATES,
    REVIEW_STATE_LABELS,
    SECTION_ORDER_LABELS,
    STAGE_PROGRESS_WEIGHTS,
    STAGE_TRANSITIONS,
    STANDARD_CONFIG_CONFIRMED,
    TERM_RULE_DEFAULT_VERSION,
)
from .database import (
    BlockedByChecklist,
    DuplicateCase,
    InvalidTransition,
    NotFound,
    VersionConflict,
)
from .schemas import (
    AiVerifyRequest,
    CaseCreate,
    CaseStageUpdate,
    CaseUpdate,
    CheckRequest,
    ChecklistItemUpdate,
    CorrectionCreate,
    DeliverRequest,
    DocumentCreate,
    ExtractRequest,
    FieldUpdate,
    IdListRequest,
    InjuryCreate,
    InjuryUpdate,
    MaterialCreate,
    MaterialPageUpdate,
    NormalizeRequest,
    OcrRunRequest,
    PhotoPlacementUpdate,
    PreprocessRequest,
    TerminologyRuleCreate,
    TerminologyRuleUpdate,
)
from .workflow import (
    StageUnavailable,
    photo_context,
    run_check,
    run_delivery,
    run_extraction,
    run_normalization,
    run_ocr,
    run_preprocess,
    run_render,
)

__all__ = ["router", "install_error_handlers", "build_config", "_handle_error"]

router = APIRouter(prefix="/api")

_MEDIA_TYPES: Dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".json": "application/json",
    ".txt": "text/plain; charset=utf-8",
}


# --------------------------------------------------------------------------
# 统一错误映射（第 4.2 节）
# --------------------------------------------------------------------------


def _handle_error(error: Exception) -> HTTPException:
    """把领域异常与库异常统一映射为 `{"detail": 中文}` + 状态码（第 4.2 节）。"""

    if isinstance(error, HTTPException):
        return error
    if isinstance(error, VersionConflict):
        return HTTPException(status_code=409, detail="案件已被其他操作更新，请刷新后重试")
    if isinstance(error, DuplicateCase):
        return HTTPException(status_code=409, detail="该案件编号已存在，不能重复建立")
    if isinstance(error, BlockedByChecklist):
        detail = str(error) or "存在未解决项"
        return HTTPException(
            status_code=409, detail="存在未解决的阻断项，请先处理或豁免：" + detail)
    if isinstance(error, InvalidTransition):
        return HTTPException(status_code=409, detail=str(error))
    if isinstance(error, ValueError):
        # 参数 / 前置条件不满足（例如给非照片材料指派位置）
        return HTTPException(status_code=400, detail=str(error))
    if isinstance(error, NotFound):
        return HTTPException(status_code=404, detail=str(error))
    if isinstance(error, KeyError):
        return HTTPException(status_code=404, detail="资源不存在：{0}".format(error))
    if isinstance(error, LookupError):
        return HTTPException(status_code=404, detail=str(error) or "资源不存在")

    # 可选依赖 / 引擎不可用（含 OCR、docgen、模型、尚未交付的模块）
    try:
        from .docgen import DocgenUnavailable
    except Exception:  # noqa: BLE001 - docgen 不可导入时忽略该分支
        DocgenUnavailable = ()  # type: ignore[assignment]
    if DocgenUnavailable and isinstance(error, DocgenUnavailable):
        return HTTPException(status_code=409, detail=str(error))
    try:
        from .ocr.base import OcrEngineUnavailable
    except Exception:  # noqa: BLE001
        OcrEngineUnavailable = ()  # type: ignore[assignment]
    if OcrEngineUnavailable and isinstance(error, OcrEngineUnavailable):
        return HTTPException(status_code=409, detail=str(error))
    if isinstance(error, StageUnavailable):
        return HTTPException(status_code=409, detail=str(error))

    try:
        from .llm import LlmAPIError
    except Exception:  # noqa: BLE001 - llm 是可选模块
        LlmAPIError = ()  # type: ignore[assignment]
    if LlmAPIError and isinstance(error, LlmAPIError):
        status = int(getattr(error, "status_code", 502) or 502)
        return HTTPException(status_code=status, detail=str(getattr(error, "message", error)))

    # B8：不把内部异常正文（可能含 SQL、文件路径等）拼进对外响应；
    # 排查请用服务端控制台输出（本仓库当前没有独立日志通道）。
    return HTTPException(status_code=500, detail="服务内部错误")


def install_error_handlers(app) -> None:
    """把 :func:`_handle_error` 注册为全局异常处理器（由 `main.create_app` 调用）。"""

    from fastapi.exceptions import RequestValidationError

    @app.exception_handler(HTTPException)
    async def _http_error(_request: Request, error: HTTPException):  # pragma: no cover - 转发
        return _json_error(error.status_code, error.detail)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, error: RequestValidationError):
        return _json_error(422, _format_validation(error))

    @app.exception_handler(Exception)
    async def _unexpected(_request: Request, error: Exception):  # pragma: no cover - 兜底
        mapped = _handle_error(error)
        return _json_error(mapped.status_code, mapped.detail)


def _json_error(status_code: int, detail: Any):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=status_code, content={"detail": detail})


def _format_validation(error: Any) -> str:
    """把 Pydantic 校验错误压成一句中文提示（前端只展示 `detail`）。

    `error` 可以是 `ValidationError`，也可以是 `.errors()` 返回的列表。
    """

    items: Any = error
    if hasattr(error, "errors"):
        try:
            items = error.errors()
        except Exception:  # noqa: BLE001 - 非标准异常对象时不中断
            items = []
    parts: List[str] = []
    for item in items or []:
        if not isinstance(item, Mapping):
            continue
        location = ".".join(str(part) for part in item.get("loc") or () if part != "body")
        parts.append("{0} {1}".format(location or "请求体", item.get("msg") or "校验失败"))
    return "；".join(parts) or "请求参数校验失败"


def _call(func, *args, **kwargs):
    """执行并把领域异常翻译成 `HTTPException`（端点内统一入口）。"""

    try:
        return func(*args, **kwargs)
    except Exception as error:  # noqa: BLE001 - 统一映射
        raise _handle_error(error) from error


def _lazy(name: str):
    """按需导入可选模块；未交付时给出可读的 409 提示。"""

    try:
        return importlib.import_module("." + name, __package__)
    except Exception as error:  # noqa: BLE001
        raise _handle_error(StageUnavailable(
            "依赖模块 backend.app.{0} 尚不可用：{1}".format(name, error))) from error


# --------------------------------------------------------------------------
# 通用取用
# --------------------------------------------------------------------------


def _db(request: Request):
    return request.app.state.db


def _storage(request: Request):
    return request.app.state.storage


def _case_or_404(database, case_id: int) -> Dict[str, Any]:
    case_data = database.get_case(case_id)
    if not case_data:
        raise HTTPException(status_code=404, detail="案件不存在：{0}".format(case_id))
    return case_data


def _material_or_404(database, case_id: int, material_id: int) -> Dict[str, Any]:
    material = database.get_material(material_id)
    if not material or int(material.get("case_id") or 0) != int(case_id):
        raise HTTPException(status_code=404, detail="材料不存在：{0}".format(material_id))
    return material


def _document_or_404(database, document_id: int) -> Dict[str, Any]:
    document = database.get_document(document_id)
    if not document:
        raise HTTPException(status_code=404, detail="文稿不存在：{0}".format(document_id))
    return document


def _payload(model, data: Any) -> Dict[str, Any]:
    """用冻结的 Pydantic 模型校验请求体，返回 `model_dump()`（422 → 中文提示）。"""

    try:
        return model.model_validate(data).model_dump()
    except Exception as error:  # noqa: BLE001 - Pydantic 校验失败 → 422
        raise HTTPException(status_code=422, detail=_format_validation(
            getattr(error, "errors", lambda: [])() )) from error


# --------------------------------------------------------------------------
# 基础
# --------------------------------------------------------------------------


@router.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok", "service": "forensic-doc-agent"}


def build_config() -> Dict[str, Any]:
    """`GET /api/config`：字段目录、枚举字典、阶段顺序、引擎状态、照片位置目录。"""

    docgen = None
    try:
        docgen = importlib.import_module(".docgen", __package__)
    except Exception:  # noqa: BLE001 - docgen 不可用时如实报告
        docgen = None
    ocr = None
    try:
        ocr = importlib.import_module(".ocr", __package__)
    except Exception:  # noqa: BLE001
        ocr = None

    docx_engine = docgen.docx_engine_status() if docgen else {
        "preferred": "unknown", "docxtpl_available": False, "todo": "TODO-DL-1"}
    libreoffice = docgen.detect_libreoffice() if docgen else None
    engines = {"docx": docx_engine, "libreoffice": libreoffice or "",
               "libreoffice_available": bool(libreoffice)}
    try:
        engines["ocr"] = ocr.engine_status() if ocr else []
    except Exception as error:  # noqa: BLE001
        engines["ocr"] = [{"name": DEFAULT_OCR_ENGINE, "available": False, "detail": str(error)}]

    return {
        "service": "forensic-doc-agent",
        "contract_version": "1.0",
        "demo_data": True,
        "demo_note": "演示数据由 backend/app/seed.py 程序化生成，不代表任何真实案件",
        "photo_policy": "系统不读取照片内容，只按用户指定的位置插入（N-11）",
        "field_definitions": [
            {
                "key": item.key, "group": item.group,
                "group_label": FIELD_GROUP_LABELS.get(item.group, item.group),
                "label": item.label, "order": item.order,
                "required": bool(item.required), "critical": bool(item.critical),
                "unit_hint": item.unit_hint, "template_anchor": item.template_anchor,
                "description": item.description, "repeatable": bool(item.repeatable),
            }
            for item in FIELD_DEFINITIONS
        ],
        "field_groups": [
            {"key": key, "label": FIELD_GROUP_LABELS.get(key, key)}
            for key in FIELD_GROUP_ORDER
        ],
        "required_field_keys": list(REQUIRED_FIELD_KEYS),
        "field_statuses": [{"key": key, "label": FIELD_STATUS_LABELS.get(key, key)}
                           for key in FIELD_STATUSES],
        "review_states": [{"key": key, "label": REVIEW_STATE_LABELS.get(key, key)}
                          for key in REVIEW_STATES],
        "material_kinds": [
            {"key": key, "label": MATERIAL_KIND_LABELS.get(key, key),
             "ocr": key in OCR_MATERIAL_KINDS, "photo": key == "photo"}
            for key in MATERIAL_KINDS
        ],
        "required_material_kinds": list(REQUIRED_MATERIAL_KINDS),
        "ocr_material_kinds": list(OCR_MATERIAL_KINDS),
        "doc_statuses": [{"key": key, "label": DOC_STATUS_LABELS.get(key, key)}
                         for key in DOC_STATUSES],
        "check_categories": [{"key": key, "label": CHECK_CATEGORY_LABELS.get(key, key)}
                             for key in CHECK_CATEGORIES],
        "check_severities": [{"key": key, "label": CHECK_SEVERITY_LABELS.get(key, key)}
                             for key in CHECK_SEVERITIES],
        "check_item_statuses": [{"key": key, "label": CHECK_ITEM_STATUS_LABELS.get(key, key)}
                                for key in CHECK_ITEM_STATUSES],
        "correction_kinds": [{"key": key, "label": CORRECTION_KIND_LABELS.get(key, key)}
                             for key in CORRECTION_KINDS],
        "correction_target_types": list(CORRECTION_TARGET_TYPES),
        "correction_scopes": [{"key": key, "label": CORRECTION_SCOPE_LABELS.get(key, key)}
                              for key in CORRECTION_SCOPES],
        "image_quality_flags": [{"key": key, "label": IMAGE_QUALITY_LABELS.get(key, key)}
                                for key in IMAGE_QUALITY_FLAGS],
        "image_operations": list(IMAGE_OPERATIONS),
        "output_formats": list(OUTPUT_FORMATS),
        "ocr_engines": list(OCR_ENGINES),
        "default_ocr_engine": DEFAULT_OCR_ENGINE,
        "ocr_low_confidence": OCR_LOW_CONFIDENCE,
        "stages": [
            {"key": key, "label": CASE_STAGE_LABELS.get(key, key), "order": index,
             "progress": STAGE_PROGRESS_WEIGHTS.get(key, 0.0)}
            for index, key in enumerate(CASE_STAGES, start=1)
        ],
        "stage_order": list(CASE_STAGES),
        # 阶段流转（R-? / 前端需求）：{stage: [允许的下一阶段, 上一阶段]}。
        # 只作前端提示与按钮可用性判断，真正的约束在 database.update_stage 内（只能一格）。
        "stage_transitions": {
            stage: list(targets) for stage, targets in STAGE_TRANSITIONS.items()
        },
        # R-49：技术参考标准只作展示与提示。**不得**据标准生成任何事实、所见或鉴定意见
        # （N-1/N-2）；采用与否由业务方确认后写入规则配置（standard_config_confirmed）。
        "reference_standards": [dict(item) for item in REFERENCE_STANDARDS],
        "standard_config_confirmed": bool(STANDARD_CONFIG_CONFIRMED),
        "standard_note": (
            "参考标准仅作展示与提示（R-49）：系统不据标准生成任何事实、所见或鉴定意见"
            "（N-1/N-2）；采用哪一版标准由业务方确认后写入规则配置，"
            "确认前 standard_config_confirmed 恒为 false。"
        ),
        "section_order": list(DEFAULT_SECTION_ORDER),
        "section_order_labels": dict(SECTION_ORDER_LABELS),
        "template_version": DEFAULT_TEMPLATE_VERSION,
        "term_rule_version": TERM_RULE_DEFAULT_VERSION,
        "draft_marker": DRAFT_MARKER,
        "placeholder_patterns": list(PLACEHOLDER_PATTERNS),
        "photo_positions": [
            {
                "key": item.key, "label": item.label, "section": item.section,
                "section_label": PHOTO_SECTION_LABELS.get(item.section, item.section),
                "order": item.order, "anchor": item.anchor, "wide": bool(item.wide),
                "max_width_cm": (PHOTO_WIDE_MAX_WIDTH_CM if item.wide else PHOTO_MAX_WIDTH_CM),
            }
            for item in PHOTO_POSITION_DEFINITIONS
        ],
        "photo_sections": [{"key": key, "label": PHOTO_SECTION_LABELS.get(key, key)}
                           for key in PHOTO_SECTIONS],
        "photo_placeholder_text": PHOTO_PLACEHOLDER_TEXT,
        "photo_max_width_cm": PHOTO_MAX_WIDTH_CM,
        "photo_wide_max_width_cm": PHOTO_WIDE_MAX_WIDTH_CM,
        "photo_max_per_position": PHOTO_MAX_PER_POSITION,
        "engines": engines,
        "todo_downloads": [
            {"code": "TODO-DL-1", "item": "docxtpl", "available": bool(
                docx_engine.get("docxtpl_available"))},
            {"code": "TODO-DL-2", "item": "paddleocr", "available": any(
                entry.get("name") == "paddle" and entry.get("available")
                for entry in engines.get("ocr") or [])},
            {"code": "TODO-DL-3", "item": "frontend npm 依赖（Vue 3 构建）", "available": False},
            {"code": "TODO-DL-4", "item": "pypdfium2", "available": _module_available("pypdfium2")},
            {"code": "TODO-DL-6", "item": "真实样本与已审核文稿", "available": False},
            {"code": "TODO-DL-7", "item": "真实部位照片样本", "available": False},
        ],
    }


def _module_available(name: str) -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec(name) is not None
    except Exception:  # noqa: BLE001
        return False


@router.get("/config")
def get_config() -> Dict[str, Any]:
    return build_config()


@router.get("/stats")
def get_stats(request: Request) -> Dict[str, Any]:
    return _call(_db(request).stats)


@router.post("/ai/verify")
def verify_ai(request: Request, payload: AiVerifyRequest = Body(...),
              x_deepseek_api_key: str = Header(default="", alias="X-DeepSeek-API-Key")
              ) -> Dict[str, Any]:
    """测试模型连通性；密钥只从请求头读取，不落库、不落日志。"""

    llm = _lazy("llm")
    if not x_deepseek_api_key:
        raise HTTPException(status_code=400, detail="请在请求头 X-DeepSeek-API-Key 中提供密钥")
    data = payload.model_dump()
    return _call(llm.verify_key, x_deepseek_api_key, data.get("base_url", ""),
                 data.get("model", ""))


# --------------------------------------------------------------------------
# 案件
# --------------------------------------------------------------------------


@router.get("/cases")
def list_cases(request: Request,
               search: str = Query(""), stage: str = Query(""), doc_status: str = Query(""),
               material_kind: str = Query(""), field_status: str = Query(""),
               has_blocking: str = Query(""), page: int = Query(1, ge=1),
               page_size: int = Query(20, ge=1, le=100)) -> Dict[str, Any]:
    return _call(_db(request).list_cases, search, stage, doc_status, material_kind,
                 field_status, has_blocking, page, page_size)


@router.post("/cases")
def create_case(request: Request, payload: CaseCreate = Body(...)) -> Dict[str, Any]:
    return _call(_db(request).create_case, payload.model_dump(), "复核人")


@router.get("/cases/{case_id}")
def get_case(case_id: int, request: Request) -> Dict[str, Any]:
    database = _db(request)
    case_data = _case_or_404(database, case_id)
    case_data["photo_slots"] = photo_context(database, _storage(request), case_id)
    return case_data


@router.patch("/cases/{case_id}")
def update_case(case_id: int, request: Request, payload: CaseUpdate = Body(...)
                ) -> Dict[str, Any]:
    data = payload.model_dump()
    expected = int(data.pop("expected_version"))
    return _call(_db(request).update_case, case_id, data, expected, "复核人")


@router.patch("/cases/{case_id}/stage")
def update_stage(case_id: int, request: Request, payload: CaseStageUpdate = Body(...)
                 ) -> Dict[str, Any]:
    data = payload.model_dump()
    return _call(_db(request).update_stage, case_id, data["stage"],
                 int(data["expected_version"]), data.get("note", ""), "复核人")


@router.delete("/cases/{case_id}")
def delete_case(case_id: int, request: Request,
                expected_version: int = Query(..., ge=1)) -> Dict[str, Any]:
    database = _db(request)
    storage = _storage(request)
    case_data = _case_or_404(database, case_id)
    case_no = str(case_data.get("case_no") or "")
    _call(database.delete_case, case_id, expected_version)
    removed = ""
    if case_no:
        target = storage.case_dir(case_no)
        try:
            if target.is_dir() and str(target).startswith(str(Path(storage.root))):
                shutil.rmtree(target)
                removed = str(target)
        except OSError:
            removed = ""
    return {"deleted": True, "case_id": case_id, "case_no": case_no, "directory_removed": removed}


# --------------------------------------------------------------------------
# 材料与页
# --------------------------------------------------------------------------


@router.post("/cases/{case_id}/materials")
def add_material(case_id: int, request: Request, payload: Mapping[str, Any] = Body(...),
                 expected_version: int = Query(0, ge=0)) -> Dict[str, Any]:
    """登记材料。

    契约：`schemas.MaterialCreate` **已声明** `expected_version: int = Field(ge=1)`（必填）。
    因此这里必须**带着版本号一起**交给模型校验，再用校验后的值做 `_claim`；
    同时兼容「版本号放查询参数」的旧调用方式（请求体未携带时用它补进去）。
    """

    raw = dict(payload or {})
    if "expected_version" not in raw and expected_version:
        # 兼容路径：版本号放在查询参数（前端契约走请求体，见 §4）
        raw["expected_version"] = int(expected_version)
    data = _payload(MaterialCreate, raw)
    version = int(data.pop("expected_version"))
    material = _call(_db(request).add_material, case_id, data, version,
                     _storage(request), "复核人")
    return material


@router.get("/cases/{case_id}/materials")
def list_materials(case_id: int, request: Request) -> List[Dict[str, Any]]:
    database = _db(request)
    _case_or_404(database, case_id)
    return database.list_materials(case_id)


@router.get("/cases/{case_id}/materials/{material_id}/file")
def material_file(case_id: int, material_id: int, request: Request,
                  page: int = Query(1, ge=1),
                  variant: str = Query("original")):
    """原件只读预览（字段核对原图、照片缩略图）。`variant` 取 `original` / `derived`。"""

    database = _db(request)
    storage = _storage(request)
    material = _material_or_404(database, case_id, material_id)
    if variant not in ("original", "derived"):
        raise HTTPException(status_code=400, detail="variant 只能是 original 或 derived")
    if variant == "derived":
        page_row = database.get_page(material_id, page)
        relative = str((page_row or {}).get("image_path") or "")
        if not relative:
            raise HTTPException(
                status_code=404,
                detail="该页没有识别用副本：材料 {0} 第 {1} 页".format(material_id, page))
    else:
        relative = str(material.get("original_path") or "")
    if not relative:
        raise HTTPException(status_code=404, detail="材料没有可预览的文件")
    path = storage.absolute(relative)
    if not Path(path).is_file():
        raise HTTPException(status_code=404, detail="文件不存在：{0}".format(relative))
    return FileResponse(str(path), media_type=_MEDIA_TYPES.get(path.suffix.lower(),
                                                               "application/octet-stream"),
                        filename=path.name)


@router.patch("/cases/{case_id}/materials/{material_id}/placement")
def set_photo_placement(case_id: int, material_id: int, request: Request,
                        payload: PhotoPlacementUpdate = Body(...)) -> Dict[str, Any]:
    """为部位照片指定/调整目标位置与顺序（只写元数据，不读照片内容：N-11）。"""

    data = payload.model_dump()
    expected = int(data.pop("expected_version"))
    return _call(_db(request).set_photo_placement, case_id, material_id, data,
                 expected, "复核人")


@router.get("/cases/{case_id}/photo-slots")
def list_photo_slots(case_id: int, request: Request) -> Dict[str, Any]:
    database = _db(request)
    storage = _storage(request)
    _case_or_404(database, case_id)
    slots = photo_context(database, storage, case_id)
    return {
        "photo_policy": "系统不读取照片内容，只按用户指定的位置插入（N-11）",
        "placeholder_text": PHOTO_PLACEHOLDER_TEXT,
        "slots": slots,
        "missing_count": sum(1 for slot in slots if slot.get("missing")),
        "assigned_count": sum(len(slot.get("photos") or []) for slot in slots),
    }


@router.patch("/cases/{case_id}/materials/{material_id}/pages/{page_number}")
def update_material_page(case_id: int, material_id: int, page_number: int, request: Request,
                         payload: MaterialPageUpdate = Body(...)) -> Dict[str, Any]:
    database = _db(request)
    _material_or_404(database, case_id, material_id)
    data = payload.model_dump()
    expected = int(data.pop("expected_version"))
    return _call(database.update_material_page, material_id, page_number, data,
                 expected, "复核人")


@router.post("/cases/{case_id}/materials/{material_id}/preprocess")
def preprocess(case_id: int, material_id: int, request: Request,
               payload: PreprocessRequest = Body(...)) -> Dict[str, Any]:
    data = payload.model_dump()
    result = _call(run_preprocess, _db(request), _storage(request), case_id, material_id,
                   data["operations"], int(data["page_number"]),
                   int(data["expected_version"]), "复核人")
    return result.as_dict()


# --------------------------------------------------------------------------
# 识别与提取
# --------------------------------------------------------------------------


@router.post("/cases/{case_id}/ocr")
def run_ocr_route(case_id: int, request: Request, payload: OcrRunRequest = Body(...)
                  ) -> Dict[str, Any]:
    data = payload.model_dump()
    result = _call(run_ocr, _db(request), _storage(request), case_id,
                   engine=data["engine"], material_id=data.get("material_id"),
                   force=bool(data.get("force")), expected_version=int(data["expected_version"]),
                   actor="复核人")
    return result.as_dict()


@router.get("/cases/{case_id}/ocr-blocks")
def list_ocr_blocks(case_id: int, request: Request,
                    material_id: Optional[int] = Query(None, ge=1),
                    page: int = Query(1, ge=1), page_size: int = Query(200, ge=1, le=2000)
                    ) -> Dict[str, Any]:
    database = _db(request)
    _case_or_404(database, case_id)
    return database.list_ocr_blocks(case_id, material_id, page, page_size)


@router.post("/cases/{case_id}/extract")
def extract(case_id: int, request: Request, payload: ExtractRequest = Body(...),
            x_deepseek_api_key: str = Header(default="", alias="X-DeepSeek-API-Key")
            ) -> Dict[str, Any]:
    data = payload.model_dump()
    if data.get("use_llm") and not x_deepseek_api_key:
        raise HTTPException(status_code=400, detail="启用模型提取时需要在请求头 X-DeepSeek-API-Key 中提供密钥")
    result = _call(run_extraction, _db(request), _storage(request), case_id,
                   material_ids=data.get("material_ids") or (),
                   replace_existing=bool(data.get("replace_existing", True)),
                   use_llm=bool(data.get("use_llm")), api_key=x_deepseek_api_key,
                   expected_version=int(data["expected_version"]), actor="复核人")
    return result.as_dict()


@router.get("/cases/{case_id}/fields")
def list_fields(case_id: int, request: Request) -> List[Dict[str, Any]]:
    database = _db(request)
    _case_or_404(database, case_id)
    return database.list_fields(case_id)


@router.patch("/cases/{case_id}/fields/{field_key}")
def update_field(case_id: int, field_key: str, request: Request,
                 payload: FieldUpdate = Body(...)) -> Dict[str, Any]:
    data = payload.model_dump()
    expected = int(data.pop("expected_version"))
    return _call(_db(request).update_field, case_id, field_key, data, expected, "复核人")


# --------------------------------------------------------------------------
# 损伤
# --------------------------------------------------------------------------


@router.get("/cases/{case_id}/injuries")
def list_injuries(case_id: int, request: Request) -> List[Dict[str, Any]]:
    database = _db(request)
    _case_or_404(database, case_id)
    return database.list_injuries(case_id)


@router.post("/cases/{case_id}/injuries")
def create_injury(case_id: int, request: Request, payload: InjuryCreate = Body(...)
                  ) -> Dict[str, Any]:
    data = payload.model_dump()
    expected = int(data.pop("expected_version"))
    return _call(_db(request).create_injury, case_id, data, expected, "复核人")


@router.post("/cases/{case_id}/injuries/reorder")
def reorder_injuries(case_id: int, request: Request, payload: IdListRequest = Body(...),
                     expected_version: int = Query(..., ge=1)) -> List[Dict[str, Any]]:
    return _call(_db(request).reorder_injuries, case_id, payload.model_dump()["ids"],
                 expected_version, "复核人")


@router.patch("/cases/{case_id}/injuries/{injury_id}")
def update_injury(case_id: int, injury_id: int, request: Request,
                  payload: InjuryUpdate = Body(...)) -> Dict[str, Any]:
    data = payload.model_dump()
    expected = int(data.pop("expected_version"))
    return _call(_db(request).update_injury, case_id, injury_id, data, expected, "复核人")


@router.delete("/cases/{case_id}/injuries/{injury_id}")
def delete_injury(case_id: int, injury_id: int, request: Request,
                  expected_version: int = Query(..., ge=1)) -> Dict[str, Any]:
    _call(_db(request).delete_injury, case_id, injury_id, expected_version)
    return {"deleted": True, "injury_id": injury_id}


# --------------------------------------------------------------------------
# 术语整理与规则
# --------------------------------------------------------------------------


@router.post("/cases/{case_id}/normalize")
def normalize(case_id: int, request: Request, payload: NormalizeRequest = Body(...)
              ) -> Dict[str, Any]:
    data = payload.model_dump()
    result = _call(run_normalization, _db(request), _storage(request), case_id,
                   target=data.get("target", "all"),
                   rule_version=data.get("rule_version") or TERM_RULE_DEFAULT_VERSION,
                   confirm_safe=bool(data.get("confirm_safe")),
                   expected_version=int(data["expected_version"]), actor="复核人")
    return result.as_dict()


@router.get("/terminology-rules")
def list_rules(request: Request, active_only: bool = Query(False),
               search: str = Query("")) -> List[Dict[str, Any]]:
    return _call(_db(request).list_terminology_rules, active_only, search)


@router.post("/terminology-rules")
def create_rule(request: Request, payload: TerminologyRuleCreate = Body(...)
                ) -> Dict[str, Any]:
    return _call(_db(request).create_terminology_rule, payload.model_dump(), "复核人")


@router.patch("/terminology-rules/{rule_id}")
def update_rule(rule_id: int, request: Request, payload: TerminologyRuleUpdate = Body(...)
                ) -> Dict[str, Any]:
    data = payload.model_dump()
    expected = int(data.pop("expected_version"))
    return _call(_db(request).update_terminology_rule, rule_id, data, expected, "复核人")


@router.delete("/terminology-rules/{rule_id}")
def delete_rule(rule_id: int, request: Request,
                expected_version: int = Query(..., ge=1)) -> Dict[str, Any]:
    _call(_db(request).delete_terminology_rule, rule_id, expected_version)
    return {"deleted": True, "rule_id": rule_id}


# --------------------------------------------------------------------------
# 文稿
# --------------------------------------------------------------------------


@router.post("/cases/{case_id}/documents")
def create_document(case_id: int, request: Request, payload: DocumentCreate = Body(...)
                    ) -> Dict[str, Any]:
    data = payload.model_dump()
    result = _call(run_render, _db(request), _storage(request), case_id,
                   doc_type=data.get("doc_type", "report"),
                   doc_status=data.get("doc_status", "internal_draft"),
                   formats=data.get("formats") or ("docx", "pdf"),
                   template_version=data.get("template_version") or "",
                   expected_version=int(data["expected_version"]), actor="复核人")
    return result.as_dict()


@router.get("/cases/{case_id}/documents")
def list_documents(case_id: int, request: Request) -> List[Dict[str, Any]]:
    database = _db(request)
    _case_or_404(database, case_id)
    return database.list_documents(case_id)


@router.get("/documents/{document_id}/download")
def download_document(document_id: int, request: Request,
                      fmt: str = Query("docx")):
    database = _db(request)
    storage = _storage(request)
    document = _document_or_404(database, document_id)
    if fmt not in OUTPUT_FORMATS:
        raise HTTPException(status_code=400, detail="fmt 只能是 docx 或 pdf")
    relative = str(document.get("docx_path" if fmt == "docx" else "pdf_path") or "")
    if not relative:
        raise HTTPException(
            status_code=404,
            detail="{0} 格式尚未生成（PDF 需本机 LibreOffice）".format(fmt))
    path = storage.absolute(relative)
    if not Path(path).is_file():
        raise HTTPException(status_code=404, detail="文件不存在：{0}".format(relative))
    case_data = database.get_case(int(document["case_id"])) or {}
    docgen = _lazy("docgen")
    filename = docgen.document_filename(str(case_data.get("case_no") or "case"),
                                        str(document.get("doc_type") or "report"),
                                        int(document.get("version_no") or 1), fmt)
    return FileResponse(str(path), media_type=_MEDIA_TYPES.get(path.suffix.lower(),
                                                               "application/octet-stream"),
                        filename=filename)


@router.post("/documents/{document_id}/approve")
def approve_document(document_id: int, request: Request, payload: CheckRequest = Body(...)
                     ) -> Dict[str, Any]:
    data = payload.model_dump()
    return _call(_db(request).approve_document, document_id, int(data["expected_version"]),
                 "", "鉴定人")


# --------------------------------------------------------------------------
# 校核
# --------------------------------------------------------------------------


@router.post("/cases/{case_id}/check")
def run_check_route(case_id: int, request: Request, payload: CheckRequest = Body(...)
                    ) -> Dict[str, Any]:
    data = payload.model_dump()
    result = _call(run_check, _db(request), _storage(request), case_id,
                   document_id=data.get("document_id"),
                   include_layout=bool(data.get("include_layout", True)),
                   expected_version=int(data["expected_version"]), actor="复核人")
    return result.as_dict()


@router.get("/cases/{case_id}/checklist")
def list_checklist(case_id: int, request: Request, status: str = Query(""),
                   category: str = Query("")) -> List[Dict[str, Any]]:
    database = _db(request)
    _case_or_404(database, case_id)
    return database.list_checklist(case_id, status, category)


@router.patch("/checklist/{item_id}")
def update_checklist_item(item_id: int, request: Request,
                          payload: ChecklistItemUpdate = Body(...)) -> Dict[str, Any]:
    data = payload.model_dump()
    expected = int(data.pop("expected_version"))
    return _call(_db(request).update_checklist_item, item_id, data, expected, "复核人")


# --------------------------------------------------------------------------
# 修正与交付
# --------------------------------------------------------------------------


@router.post("/cases/{case_id}/corrections")
def create_correction(case_id: int, request: Request, payload: CorrectionCreate = Body(...)
                      ) -> Dict[str, Any]:
    data = payload.model_dump()
    expected = int(data.pop("expected_version"))
    return _call(_db(request).create_correction, case_id, data, expected, "复核人")


@router.get("/cases/{case_id}/corrections")
def list_corrections(case_id: int, request: Request) -> List[Dict[str, Any]]:
    database = _db(request)
    _case_or_404(database, case_id)
    return database.list_corrections(case_id)


@router.post("/cases/{case_id}/deliver")
def deliver(case_id: int, request: Request, payload: DeliverRequest = Body(...)
            ) -> Dict[str, Any]:
    data = payload.model_dump()
    result = _call(run_delivery, _db(request), _storage(request), case_id,
                   document_id=data.get("document_id"),
                   formats=data.get("formats") or ("docx", "pdf"),
                   expected_version=int(data["expected_version"]), actor="复核人")
    return result.as_dict()
