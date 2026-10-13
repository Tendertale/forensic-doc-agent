"""案件流程编排（`docs/architecture.md` 第 5.10 节）。

本模块把 `storage` / `database` / `imaging` / `ocr` / `extractor` / `terminology`
/ `docgen` / `checker` 串成**一条可复现的流水线**：预处理 → 识别 → 提取 → 整理
→ 生成 → 校核 → 交付。它不自己打开数据库、不自己解析图片，只调用第 5 节冻结签名。

硬性约束（本模块逐条落实）：

* **INV-9 乐观锁**：每个阶段入口先 `database.claim(case_id, expected_version, actor)`；
  下游写方法不带 `expected_version` 的（如 `replace_ocr_blocks`）由这一次占位统一保护，
  后续内部写传 `0`（系统写，不重复校验但同样递增版本）。
  **D1 修正**：入口已经用真实版本号校验过一次，所以同一调用内后续所有带版本参数的写
  （`upsert_fields` / `upsert_injuries` / `create_document` / `update_document_assets` /
  `update_stage`）必须传 `0`——传同一个 `expected_version` 必然第二次 `rowcount=0` → 409。
* **N-11 / R-55**：`kind == "photo"` 的部位照片**永不**进入预处理、识别、提取或任何
  视觉/模型调用；`run_ocr` 把它们列入 `payload["skipped_photo_ids"]`。本模块只把
  照片当**文件路径 + 位置元数据**处理。
* **INV-5 / R-28**：`run_render` 生成 `pending_issue` 前查 `open_blocking_items`，
  非空抛 `BlockedByChecklist` → HTTP 409。
* **N-1 / INV-10**：`opinion` 只来自 `cases.opinion_text`，本模块从不从识别文本推导。
* **模型请求期间不持有 SQLite 写事务**：`run_extraction(use_llm=True)` 先读块、再发起
  网络请求、最后才写库。
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .constants import (
    CASE_STAGE_LABELS,
    DEFAULT_OCR_ENGINE,
    DEFAULT_TEMPLATE_VERSION,
    OCR_LOW_CONFIDENCE,
    OCR_MATERIAL_KINDS,
    PHOTO_MAX_WIDTH_CM,
    PHOTO_MATERIAL_KIND,
    PHOTO_PLACEHOLDER_TEXT,
    PHOTO_POSITION_KEYS,
    PHOTO_WIDE_MAX_WIDTH_CM,
    STAGE_TRANSITIONS,
    TERM_RULE_DEFAULT_VERSION,
)
from .database import BlockedByChecklist, InvalidTransition, NotFound

__all__ = [
    "StageUnavailable",
    "StageResult",
    "photo_context",
    "run_preprocess",
    "run_ocr",
    "run_extraction",
    "run_normalization",
    "run_render",
    "run_check",
    "run_delivery",
]


class StageUnavailable(RuntimeError):
    """阶段所需的模块 / 能力尚未交付或不可用（`routes.py` 统一映射为 409）。"""


@dataclass
class StageResult:
    """一个阶段的执行结果。`payload` 只放可 JSON 序列化的数据。"""

    stage: str
    ok: bool
    detail: str
    payload: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {"stage": self.stage, "ok": self.ok, "detail": self.detail,
                "payload": self.payload}


# --------------------------------------------------------------------------
# 内部工具
# --------------------------------------------------------------------------


def _module(name: str):
    """按需导入同包模块；未交付时给出可读的中文错误（不静默、不用桩顶替）。"""

    import importlib

    try:
        return importlib.import_module("." + name, __package__)
    except Exception as error:  # noqa: BLE001 - 统一转成可读前置条件错误
        raise StageUnavailable(
            "依赖模块 backend.app.{0} 尚不可用：{1}".format(name, error)
        ) from error


def _case(database, case_id: int) -> Dict[str, Any]:
    case_data = database.get_case(case_id)
    if not case_data:
        raise NotFound("案件不存在：{0}".format(case_id))
    return case_data


def _advance(database, case_id: int, target: str, actor: str) -> str:
    """只允许前进一格：当前阶段恰好是 `target` 的前一格时自动推进。

    其它情况（还在更早的阶段、已经在该阶段、已越过）一律不动作，由用户显式
    `PATCH /api/cases/{id}/stage` 决定；回退同样只能一格（`STAGE_TRANSITIONS`）。
    """

    current = (_case(database, case_id) or {}).get("stage", "")
    if current == target:
        return ""
    if target not in STAGE_TRANSITIONS.get(current, ()):
        return ""
    try:
        # 系统内部推进：expected_version=0（不重复校验，但同样占位递增）
        database.update_stage(case_id, target, 0,
                              note="流程执行自动推进", actor=actor)
        return "{0} → {1}".format(
            CASE_STAGE_LABELS.get(current, current), CASE_STAGE_LABELS.get(target, target))
    except Exception as error:  # noqa: BLE001 - 阶段推进失败不影响阶段本身的产出
        return "阶段未自动推进：{0}".format(error)


def _material_case_id(database, material_id: int, case_id: int) -> Dict[str, Any]:
    material = database.get_material(material_id)
    if not material or int(material.get("case_id") or 0) != int(case_id):
        raise NotFound("材料不存在或不属于该案件：{0}".format(material_id))
    return material


def _is_photo(material: Mapping[str, Any]) -> bool:
    return str(material.get("kind") or "") == PHOTO_MATERIAL_KIND


def photo_context(database, storage, case_id: int) -> List[Dict[str, Any]]:
    """照片栏位视图，并为每张照片补 `path`（绝对路径）。

    `docgen` 只认 `path`（`docs/architecture.md` 第 5.8 节），而数据库里存的是
    **相对存储根的 `original_path`**；这里统一换算，绝不在 `docgen` 里拼存储根。
    本函数只处理路径字符串与位置元数据，不打开、不解码任何图片（N-11）。
    """

    slots = database.list_photo_slots(case_id)
    for slot in slots or []:
        for photo in slot.get("photos") or []:
            relative = str(photo.get("original_path") or "")
            photo["path"] = str(storage.absolute(relative)) if relative else ""
    return slots or []


def _slot_mapping(slots: Sequence[Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {str(slot.get("position_key")): dict(slot) for slot in slots or []}


# --------------------------------------------------------------------------
# 1. 预处理（image_check）
# --------------------------------------------------------------------------


def run_preprocess(database, storage, case_id: int, material_id: int,
                   operations: Sequence[str], page_number: int = 0,
                   expected_version: int = 0, actor: str = "系统") -> StageResult:
    """生成识别用副本（原件不动，R-19）；照片一律跳过（N-11）。"""

    database.claim(case_id, expected_version, actor)
    material = _material_case_id(database, material_id, case_id)
    if _is_photo(material):
        return StageResult(
            "image_check", True,
            "部位照片不做任何预处理：不生成识别用副本、不读取像素内容（N-11/R-55）",
            {"skipped_photo_id": int(material_id), "operations": [], "pages": [],
             "advance": _advance(database, case_id, "image_check", actor)},
        )

    imaging = _module("imaging")
    kind = str(material.get("kind") or "other")
    case_no = str(material.get("case_no") or (_case(database, case_id) or {}).get("case_no") or "")
    stem = Path(str(material.get("filename") or "material")).stem or "material"
    derived_dir = storage.derived_dir(case_no, kind)
    derived_dir.mkdir(parents=True, exist_ok=True)
    operations = [str(item) for item in operations or []]

    pages = [page for page in material.get("pages") or []
             if not page_number or int(page.get("page_number") or 0) == int(page_number)]
    if not pages:
        raise NotFound("页码不存在：材料 {0} 第 {1} 页".format(material_id, page_number))

    applied: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for page in pages:
        number = int(page.get("page_number") or 0)
        source = str(page.get("original_image_path") or material.get("original_path") or "")
        src = storage.absolute(source) if source else None
        if src is None or not Path(src).is_file():
            skipped.append({"page": number, "reason": "原件不存在：{0}".format(source)})
            continue
        suffix = Path(src).suffix.lower()
        target = derived_dir / "{0}_p{1}.png".format(stem, number)
        try:
            if suffix == ".pdf":
                # PDF：先把页面渲染成图像副本，再做图像预处理（原件不动）
                rendered = imaging.render_pdf_pages(src, derived_dir, prefix=stem) or []
                picked = rendered[number - 1] if 0 < number <= len(rendered) else None
                if not picked:
                    skipped.append({"page": number, "reason": "PDF 页面渲染不可用（TODO-DL-4）"})
                    continue
                result = imaging.preprocess_image(picked, target, operations)
            else:
                result = imaging.preprocess_image(src, target, operations)
        except Exception as error:  # noqa: BLE001 - 单页失败不中断整份材料
            skipped.append({"page": number, "reason": str(error)})
            continue
        stored = database.save_preprocessing(material_id, number, operations,
                                             storage.relative(target))
        applied.append({"page": number, "path": str(target),
                        "operations": (result or {}).get("applied", list(operations)),
                        "skipped_operations": (result or {}).get("skipped", []),
                        "size": (result or {}).get("size"),
                        "ocr_status": stored.get("ocr_status")})

    ok = bool(applied)
    detail = "生成识别用副本 {0} 页".format(len(applied))
    if skipped:
        detail += "；跳过 {0} 页（{1}）".format(
            len(skipped), "、".join(sorted({str(item["reason"]) for item in skipped})))
    return StageResult("image_check", ok, detail, {
        "material_id": int(material_id),
        "operations": operations,
        "pages": applied,
        "skipped": skipped,
        "advance": _advance(database, case_id, "image_check", actor),
    })


# --------------------------------------------------------------------------
# 2. 识别（ocr）
# --------------------------------------------------------------------------


def run_ocr(database, storage, case_id: int, *, engine: str = "sidecar",
            material_id: Optional[int] = None, force: bool = False,
            expected_version: int = 0, actor: str = "系统") -> StageResult:
    """只识别 `OCR_MATERIAL_KINDS`；部位照片一律跳过并计入 `skipped_photo_ids`（R-55）。

    引擎选择按 `docs/architecture.md` §5.4 的裁决执行（**显式选择不得静默回退**）：

    * 显式指定引擎（`sidecar` / `paddle` / `none` / 第三方注册引擎）：不可用或名字未知时
      抛 `OcrEngineUnavailable` → HTTP 409，`detail` 明确提示“改用 sidecar 引擎”（TODO-DL-2）；
    * `engine` 留空 / `"auto"`：`resolve_engine()` 回退默认引擎 sidecar，实际使用的引擎与
      回退原因如实写进 `payload["engine"]` / `payload["fallback_from"]` 与 `detail`；
    * `"none"` 是显式选择的“不识别”：返回空结果（200），不是错误。
    """

    database.claim(case_id, expected_version, actor)
    ocr = _module("ocr")

    # ① 先定引擎、再谈材料：显式选了不可用引擎的请求，即使本案暂时没有可识别材料，
    #    也必须如实返回 409，而不是「没有材料要识别」的成功回执（§5.4）。
    requested = str(engine or "").strip().lower() or "auto"
    fallback_from = ""
    if requested == "auto":
        # 未指定 / auto：按裁决回退默认引擎 sidecar；回退来源记为 auto（实际引擎见 payload.engine）
        try:
            ocr_engine = ocr.resolve_engine(DEFAULT_OCR_ENGINE)
        except Exception as error:  # noqa: BLE001 - 引擎不可用 → 409（第 4.2 节）
            raise StageUnavailable("OCR 引擎不可用：{0}".format(error)) from error
        actual_engine = str(getattr(ocr_engine, "name", "") or DEFAULT_OCR_ENGINE)
        fallback_from = "auto"
    else:
        # 显式选择：只认这个名字，不可用就报错，绝不改用它人
        try:
            ocr_engine = ocr.get_engine(requested)
        except Exception as error:  # noqa: BLE001 - 未知 / 无法构造的引擎 → 409
            raise ocr.OcrEngineUnavailable(
                "显式选择的 OCR 引擎「{0}」不可用，系统不做静默回退：{1}；"
                "请改用 sidecar 引擎（TODO-DL-2）".format(requested, error)) from error
        if not getattr(ocr_engine, "available", lambda: True)():
            reason = ""
            try:
                reason = str((ocr_engine.describe() or {}).get("detail") or "")
            except Exception:  # noqa: BLE001 - 引擎自述失败不影响「不可用」这一结论
                reason = ""
            raise ocr.OcrEngineUnavailable(
                "显式选择的 OCR 引擎「{0}」当前不可用，系统不做静默回退{1}；"
                "请改用 sidecar 引擎（TODO-DL-2）".format(
                    requested, "：" + reason if reason else ""))
        actual_engine = str(getattr(ocr_engine, "name", "") or requested)

    engine_note = ""
    if fallback_from:
        engine_note = "；请求为自动选择（{0}），实际使用 {1} 引擎（§5.4）".format(
            fallback_from, actual_engine)

    materials = database.list_materials(case_id)
    if material_id:
        materials = [item for item in materials
                     if int(item.get("id") or 0) == int(material_id)]
        if not materials:
            raise NotFound("材料不存在：{0}".format(material_id))

    skipped_photo_ids: List[int] = []
    skipped_kinds: List[str] = []
    targets: List[Dict[str, Any]] = []
    for material in materials:
        if _is_photo(material):
            # N-11 / R-55：照片永不进入识别流程
            skipped_photo_ids.append(int(material.get("id") or 0))
            continue
        if str(material.get("kind") or "") not in OCR_MATERIAL_KINDS:
            skipped_kinds.append(str(material.get("kind") or ""))
            continue
        targets.append(material)

    if not targets:
        return StageResult(
            "ocr", True,
            "没有需要识别的材料：照片 {0} 张已跳过（N-11），其它类型 {1} 个（引擎 {2}）{3}".format(
                len(skipped_photo_ids), len(skipped_kinds), actual_engine, engine_note),
            {"engine": actual_engine, "engine_requested": requested,
             "fallback_from": fallback_from,
             "material_ids": [], "pages": 0, "blocks": 0,
             "skipped_photo_ids": skipped_photo_ids, "skipped_kinds": skipped_kinds,
             "skipped_pages": [],
             "advance": _advance(database, case_id, "ocr", actor)},
        )

    pages_done = 0
    blocks_total = 0
    skipped_pages: List[Dict[str, Any]] = []
    for material in targets:
        kind = str(material.get("kind") or "")
        for page in material.get("pages") or []:
            number = int(page.get("page_number") or 0)
            if not force and str(page.get("ocr_status") or "") == "done":
                skipped_pages.append({"material_id": material.get("id"), "page": number,
                                      "reason": "已识别（force=false）"})
                continue
            source = str(page.get("image_path") or page.get("original_image_path")
                         or material.get("original_path") or "")
            image = storage.absolute(source) if source else None
            if image is None or not Path(image).is_file():
                database.set_ocr_status(material["id"], number, "missing")
                skipped_pages.append({"material_id": material.get("id"), "page": number,
                                      "reason": "识别用副本不存在：{0}".format(source)})
                continue
            try:
                blocks = ocr_engine.recognize(
                    str(image), number, kind=kind,
                    column_hint=str(page.get("column_hint") or ""))
            except Exception as error:  # noqa: BLE001 - 单页失败不中断整案
                database.set_ocr_status(material["id"], number, "failed")
                skipped_pages.append({"material_id": material.get("id"), "page": number,
                                      "reason": str(error)})
                continue
            count = database.replace_ocr_blocks(case_id, int(material["id"]), page,
                                               actual_engine, blocks)
            database.set_ocr_status(material["id"], number, "done")
            pages_done += 1
            blocks_total += int(count or 0)

    detail = "识别 {0} 页，写入 {1} 个文本块（引擎 {2}）{3}".format(
        pages_done, blocks_total, actual_engine, engine_note)
    if skipped_photo_ids:
        detail += "；部位照片跳过 {0} 张（N-11/R-55）".format(len(skipped_photo_ids))
    if skipped_pages:
        detail += "；跳过 {0} 页".format(len(skipped_pages))
    return StageResult("ocr", True, detail, {
        "engine": actual_engine,
        "engine_requested": requested,
        "fallback_from": fallback_from,
        "material_ids": [int(item["id"]) for item in targets],
        "pages": pages_done,
        "blocks": blocks_total,
        "skipped_photo_ids": skipped_photo_ids,
        "skipped_kinds": skipped_kinds,
        "skipped_pages": skipped_pages,
        "advance": _advance(database, case_id, "ocr", actor),
    })


# --------------------------------------------------------------------------
# 3. 提取（extract）
# --------------------------------------------------------------------------


def run_extraction(database, storage, case_id: int, *, material_ids: Sequence[int] = (),
                   replace_existing: bool = True, use_llm: bool = False,
                   api_key: str = "", expected_version: int = 0,
                   actor: str = "系统") -> StageResult:
    """字段与损伤提取（R-14～R-17、R-22）。

    * 来源集合**只含** `OCR_MATERIAL_KINDS`：`kind == "photo"` 的材料绝不进入（INV-11）；
    * `confidence < OCR_LOW_CONFIDENCE` 的字段按第 5.5 节第 5 条改写为 `unclear`；
    * `use_llm=True` 时先取块、再发起网络请求、最后写库（模型请求期间不持有写事务）。
    """

    database.claim(case_id, expected_version, actor)
    extractor = _module("extractor")

    allowed_ids: List[int] = []
    blocks: List[Dict[str, Any]] = []
    for material in database.list_materials(case_id):
        kind = str(material.get("kind") or "")
        if kind not in OCR_MATERIAL_KINDS:
            continue  # INV-11：照片及其它类型不作为字段来源
        if material_ids and int(material.get("id") or 0) not in set(int(x) for x in material_ids):
            continue
        allowed_ids.append(int(material["id"]))
        page = database.list_ocr_blocks(case_id, int(material["id"]), page=1, page_size=2000)
        for block in page.get("items") or []:
            row = dict(block)
            row["kind"] = kind
            row["filename"] = material.get("filename") or ""
            blocks.append(row)

    if not blocks and not use_llm:
        return StageResult("extract", True, "没有可用于提取的识别结果（照片材料按 N-11 排除）", {
            "material_ids": allowed_ids, "blocks": 0, "fields": 0, "injuries": 0,
            "conflicts": 0,
            "advance": _advance(database, case_id, "extract", actor)})

    case_data = _case(database, case_id)
    context = {"case_no": case_data.get("case_no"), "commission_unit": case_data.get("commission_unit"),
               "subject_name": case_data.get("subject_name"),
               "field_index": _field_keys()}

    fields: List[Dict[str, Any]] = [_as_dict(item) for item in
                                    extractor.extract_fields(blocks, context)]
    injuries: List[Dict[str, Any]] = [_as_dict(item) for item in
                                      extractor.extract_injuries(blocks)]

    # 第 5.5 节第 5 条：低置信度 → unclear（由调用方改写）
    for item in fields:
        confidence = item.get("confidence")
        if confidence is not None and float(confidence) < OCR_LOW_CONFIDENCE \
                and item.get("status") == "recorded":
            item["status"] = "unclear"
            item.setdefault("rule_note", "")
            item["rule_note"] = (item["rule_note"] + "；" if item["rule_note"] else "") + \
                "识别置信度低于 {0}，需人工辨认".format(OCR_LOW_CONFIDENCE)

    llm_picked: List[Dict[str, Any]] = []
    if use_llm and api_key:
        llm = _module("llm")
        try:
            # 网络请求发生在任何写库之前：不持有 SQLite 写事务
            picked = asyncio.run(llm.extract_with_llm(blocks, api_key))
        except Exception as error:  # noqa: BLE001 - 模型失败不阻断本地流程
            llm_picked = []
            llm_note = "模型提取跳过：{0}".format(error)
        else:
            by_id = {int(item["id"]): item for item in
                     [dict(block) for block in blocks] if item.get("id") is not None}
            fields = [item for item in fields
                      if str(item.get("field_key")) not in
                      {str(pick.get("field_key")) for pick in (picked or {}).get("fields") or []}]
            for pick in (picked or {}).get("fields") or []:
                source = by_id.get(int(pick.get("source_block_id") or 0))
                if source is None:
                    continue
                llm_picked.append({
                    "field_key": pick.get("field_key"),
                    "raw_text": source.get("text", ""),
                    "confirmed_value": str(pick.get("value") or ""),
                    "normalized_text": str(pick.get("value") or ""),
                    "status": "recorded",
                    "review_state": "pending",
                    "confidence": source.get("confidence"),
                    "source_material_id": source.get("material_id"),
                    "source_page": source.get("page_number"),
                    "source_bbox": source.get("bbox") or [],
                    "source_block_id": source.get("id"),
                    "rule_note": "由模型挑选来源块，需人工复核",
                })
            fields.extend(llm_picked)
            llm_note = "模型挑选 {0} 项".format(len(llm_picked))
    else:
        llm_note = "未启用模型提取（默认关闭）"

    conflicts: List[Dict[str, Any]] = []
    try:
        conflicts = [_as_dict(item) for item in extractor.detect_conflicts(fields)]
    except Exception:  # noqa: BLE001 - 冲突检测失败不阻断写入
        conflicts = []

    # 入口已 `claim`（INV-9）：本次调用内的内部写一律传 0（D1）。
    # D11：先取“用户已确认值”快照，再写库；被用户确认过的字段识别结果不覆盖。
    confirmed_before = {
        str(row.get("field_key")): str(row.get("confirmed_value") or "").strip()
        for row in database.list_fields(case_id)
    }
    written_fields = database.upsert_fields(case_id, fields, replace_existing, 0, actor)
    written_injuries = database.upsert_injuries(case_id, injuries, replace_existing, 0, actor)

    kept_fields: List[str] = []
    for item in fields:
        key = str(item.get("field_key") or "")
        confirmed_value = confirmed_before.get(key) or ""
        recognized = str(item.get("raw_text") or item.get("normalized_text") or "").strip()
        if confirmed_value and recognized and recognized != confirmed_value:
            kept_fields.append(key)

    detail = "提取字段 {0} 项、损伤 {1} 条（来源材料 {2} 个）".format(
        len(written_fields), len(written_injuries), len(allowed_ids))
    if conflicts:
        detail += "；跨材料冲突 {0} 处".format(len(conflicts))
    if kept_fields:
        detail += "；识别值未覆盖用户已确认值 {0} 项（D11/R-45，见案件事件 field_value_priority）".format(
            len(kept_fields))
    detail += "；{0}".format(llm_note)
    return StageResult("extract", True, detail, {
        "material_ids": allowed_ids,
        "blocks": len(blocks),
        "fields": len(written_fields),
        "injuries": len(written_injuries),
        "conflicts": conflicts,
        "kept_user_values": kept_fields,
        "llm": llm_note,
        "advance": _advance(database, case_id, "extract", actor),
    })


def _field_keys() -> List[str]:
    """字段目录（供 `extractor.extract_fields` 的 `case_context` 使用）。"""

    from .constants import FIELD_INDEX

    return sorted(FIELD_INDEX)


# --------------------------------------------------------------------------
# 4. 术语整理（normalize）
# --------------------------------------------------------------------------


def run_normalization(database, storage, case_id: int, *, target: str = "all",
                      rule_version: str = TERM_RULE_DEFAULT_VERSION,
                      confirm_safe: bool = False, expected_version: int = 0,
                      actor: str = "系统") -> StageResult:
    """按认可规则整理检验描述（R-4 / R-23 / R-30～R-35）。"""

    terminology = _module("terminology")
    rules = database.list_terminology_rules(active_only=True)
    compiled = terminology.compile_rules(rules)

    fields = database.list_fields(case_id) if target in ("all", "fields") else []
    injuries = database.list_injuries(case_id) if target in ("all", "injuries") else []
    results = [_as_dict(item) for item in terminology.normalize_case(fields, injuries, compiled)]

    updates: List[Dict[str, Any]] = []
    needs_confirmation = 0
    for item in results:
        requires = bool(item.get("requires_confirmation"))
        if requires:
            needs_confirmation += 1
        updates.append({
            "target_type": item.get("target_type") or "field",
            "target_id": item.get("target_id") or 0,
            "field_key": item.get("field_key") or "",
            "normalized_text": item.get("normalized_text") or "",
            "status": item.get("status") or "recorded",
            "applied_rule_codes": item.get("applied_rule_codes") or [],
            "rule_note": item.get("rule_note") or "",
            "requires_confirmation": requires,
            "review_state": ("confirmed" if confirm_safe and not requires else "pending"),
            "issues": item.get("issues") or [],
        })

    written = database.apply_normalization(case_id, updates, expected_version, actor)
    detail = "整理字段 {0} 项、损伤 {1} 条；待确认 {2} 项（规则版本 {3}）".format(
        written.get("fields", 0), written.get("injuries", 0), needs_confirmation, rule_version)
    return StageResult("normalize", True, detail, {
        "rule_version": rule_version,
        "rules": len(compiled),
        "fields": written.get("fields", 0),
        "injuries": written.get("injuries", 0),
        "requires_confirmation": needs_confirmation,
        "results": [{"target_type": item["target_type"], "field_key": item["field_key"],
                     "raw_text": item.get("raw_text") or "",
                     "normalized_text": item["normalized_text"],
                     "applied_rule_codes": item["applied_rule_codes"],
                     "requires_confirmation": item["requires_confirmation"]}
                    for item in results],
        "advance": _advance(database, case_id, "normalize", actor),
    })


# --------------------------------------------------------------------------
# 5. 生成文稿（render）
# --------------------------------------------------------------------------


#: 模板里的照片栏位锚点：`{{ photo_<key> }}`（允许锚点内有空格，与 docgen 的判定一致）。
_PHOTO_ANCHOR_RE = re.compile(r"\{\{\s*(photo_[A-Za-z0-9_]+)\s*\}\}")


def _template_photo_slots(docgen, path: str) -> int:
    """统计模板正文里出现的**照片栏位**个数。

    只读文档文本（`docgen.extract_document_text`），不打开、不解码任何图片（N-11）；
    读不出文本的模板按 0 个栏位处理，绝不因此中断生成。
    """

    try:
        text = docgen.extract_document_text(path) or ""
    except Exception:  # noqa: BLE001 - 模板文本读不出来时按“没有栏位”处理
        return 0
    found = {match.group(1) for match in _PHOTO_ANCHOR_RE.finditer(text)}
    return len(found & set(PHOTO_POSITION_KEYS))


def _template_for(database, storage, case_data: Mapping[str, Any],
                  template_version: str) -> Tuple[str, str]:
    """选定模板：优先本案登记的 `template` 材料原件，其次 `templates/` 目录。

    D13：一个案件可能登记了**多份**模板材料（早期副本 + 之后补登的完整副本）。这里取
    “照片栏位最完整”的一份（栏位数相同时取最新登记的），否则补登的新模板永远不会生效
    —— 旧实现固定取第一份（`sort_order,id` 顺序），实测导致演示案件长期只用 14 个照片
    栏位的旧副本渲染，与位置目录的 18 项对不上。登记的新模板是**另存的新材料**，
    原模板材料一个字节都不改（N-8）。
    """

    docgen = _module("docgen")
    best: Optional[Tuple[int, int, str, str]] = None
    for material in database.list_materials(int(case_data["id"])):
        if str(material.get("kind")) != "template":
            continue
        relative = str(material.get("original_path") or "")
        if not relative:
            continue
        path = storage.absolute(relative)
        if not Path(path).is_file():
            continue
        candidate = (_template_photo_slots(docgen, str(path)),
                     int(material.get("id") or 0), str(path), relative)
        if best is None or candidate[:2] > best[:2]:
            best = candidate
    if best is not None:
        return best[2], best[3]
    version = template_version or str(case_data.get("template_version") or "") \
        or DEFAULT_TEMPLATE_VERSION
    path = docgen.resolve_template(case_data, storage.templates_dir())
    return str(path), storage.relative(path)


def run_render(database, storage, case_id: int, *, doc_type: str = "report",
               doc_status: str = "internal_draft",
               formats: Sequence[str] = ("docx", "pdf"), template_version: str = "",
               expected_version: int = 0, actor: str = "系统") -> StageResult:
    """填写模板生成 Word（+ PDF）。照片只按 `position_key` 放进对应栏位（R-56/N-11）。

    D12 / INV-9：**先 `_claim`（真实版本号）再检查 `pending_issue` 的阻断项**，
    因此「版本过期」返回版本冲突语义、「阻断项未解决」返回阻断语义，两者都是 409
    但不会互相掩盖；同时 `_claim` 先于任何落盘，过期版本不会留下孤儿文件（B7）。
    本次调用内 `create_document` 传 `0`（已校验过，不重复校验：D1 同一规则）。
    """

    # INV-9：任何写操作先占位；先占位也让版本冲突优先于阻断检查暴露
    database.claim(case_id, expected_version, actor)
    docgen = _module("docgen")
    case_data = _case(database, case_id)
    if doc_status == "pending_issue":
        blocking = database.open_blocking_items(case_id)
        if blocking:
            raise BlockedByChecklist("、".join(
                "{0}（{1}）".format(item.get("title") or item.get("code"), item.get("code"))
                for item in blocking[:5]))

    formats = [str(item) for item in formats or ("docx",)]
    version_no = database.next_document_version(case_id, doc_type)
    case_no = str(case_data.get("case_no") or "case")

    # 照片槽位接进上下文：docgen 只认 `path`
    slots = photo_context(database, storage, case_id)
    case_data["photo_slots"] = slots
    case_data["doc_version"] = str(version_no)
    case_data["doc_status"] = doc_status

    template_path, template_relative = _template_for(
        database, storage, case_data,
        template_version or str(case_data.get("template_version") or ""))
    context = docgen.build_context(case_data)
    # 照片槽位：§5.8 允许 dict（key → 栏位）与 list 两种形态，这里统一成 dict 再取值（B10）
    slots_value = context.get("photo_slots")
    if isinstance(slots_value, Mapping):
        slots_map = dict(slots_value)
    elif slots_value:
        slots_map = _slot_mapping(slots_value)
    else:
        # 契约守卫：docgen 若未输出照片键，由本模块补齐（补全后为幂等空操作）
        slots_map = _slot_mapping(slots)
    context["photo_slots"] = slots_map
    if not context.get("photos"):
        context["photos"] = [photo for slot in slots_map.values()
                             for photo in (slot.get("photos") or [])]
    context.setdefault("photo_placeholder", PHOTO_PLACEHOLDER_TEXT)
    context.setdefault("photo_max_width_cm", PHOTO_MAX_WIDTH_CM)
    context.setdefault("photo_wide_max_width_cm", PHOTO_WIDE_MAX_WIDTH_CM)

    documents_dir = storage.documents_dir(case_no)
    documents_dir.mkdir(parents=True, exist_ok=True)
    docx_name = docgen.document_filename(case_no, doc_type, version_no, "docx")
    docx_path = documents_dir / docx_name
    result = docgen.render_document(template_path, context, docx_path, engine="auto")
    docx_relative = storage.relative(getattr(result, "output_path", docx_path))

    # 生成时正文快照：供 run_check 判定“文稿在生成之后被系统外改动”（DOCUMENT_TEXT_DIVERGED）。
    # 快照只是文本，不参与渲染；写失败不影响生成结果。
    snapshot_relative = ""
    try:
        rendered_text = docgen.extract_document_text(docx_path) or ""
        if rendered_text:
            snapshot_path = Path(docx_path).with_suffix(".render.txt")
            snapshot_path.write_text(rendered_text, encoding="utf-8")
            snapshot_relative = storage.relative(snapshot_path)
    except Exception:  # noqa: BLE001 - 快照失败不得影响文稿生成
        snapshot_relative = ""

    pdf_relative = ""
    pdf_error = ""
    if "pdf" in formats:
        exported = docgen.export_pdf(docx_path, documents_dir)
        if exported:
            pdf_relative = storage.relative(exported)
        else:
            pdf_error = "PDF 导出失败：LibreOffice 不可用或转换超时（TODO-DL-3 之外的本机依赖）"

    document = database.create_document(case_id, {
        "doc_type": doc_type,
        "doc_status": doc_status,
        "template_version": str(case_data.get("template_version") or DEFAULT_TEMPLATE_VERSION),
        "term_rule_version": str(case_data.get("term_rule_version") or TERM_RULE_DEFAULT_VERSION),
        "template_path": template_relative,
        "docx_path": docx_relative,
        "pdf_path": pdf_relative,
        "render_engine": getattr(result, "engine", ""),
        "unresolved": list(getattr(result, "unresolved", []) or []),
        "note": pdf_error,
    }, 0, actor)

    unresolved = list(getattr(result, "unresolved", []) or [])
    detail = "生成 {0} v{1}（{2}），引擎 {3}，段落 {4}、表格 {5}".format(
        doc_type, version_no, doc_status, getattr(result, "engine", ""),
        getattr(result, "paragraph_count", 0), getattr(result, "table_count", 0))
    if unresolved:
        detail += "；未解析占位符 {0} 个".format(len(unresolved))
    if pdf_error:
        detail += "；{0}".format(pdf_error)
    return StageResult("render", True, detail, {
        "document_id": document.get("id"),
        "version_no": version_no,
        "doc_status": doc_status,
        "docx_path": docx_relative,
        "docx_absolute": str(storage.absolute(docx_relative)),
        "pdf_path": pdf_relative,
        "pdf_absolute": str(storage.absolute(pdf_relative)) if pdf_relative else "",
        "engine": getattr(result, "engine", ""),
        "unresolved": unresolved,
        "paragraph_count": getattr(result, "paragraph_count", 0),
        "table_count": getattr(result, "table_count", 0),
        "photos_assigned": sum(len(slot.get("photos") or []) for slot in slots),
        "photo_slots_filled": [slot["position_key"] for slot in slots if slot.get("photos")],
        "advance": _advance(database, case_id, "render", actor),
    })


# --------------------------------------------------------------------------
# 6. 校核（check_document）
# --------------------------------------------------------------------------


def run_check(database, storage, case_id: int, *, document_id: Optional[int] = None,
              include_layout: bool = True, expected_version: int = 0,
              actor: str = "系统") -> StageResult:
    """运行校核并写入清单（R-6 / R-26 / R-46 / R-52）。"""

    database.claim(case_id, expected_version, actor)
    checker = _module("checker")
    docgen = _module("docgen")
    case_data = _case(database, case_id)
    case_data["photo_slots"] = photo_context(database, storage, case_id)
    # N-11 / N-1 安全网：`database.get_case()` 不聚合识别结果，若这里不补，
    # checker 的 PHOTO_CONTENT_READ（照片不得有识别结果）与 OPINION_FROM_SOURCE
    # （鉴定意见不得来自识别原文）在真实链路中永远不可达。只传文本与来源元数据，
    # 不读取任何照片内容（N-11）。
    case_data["ocr_blocks"] = list(
        (database.list_ocr_blocks(case_id, page=1, page_size=2000) or {}).get("items") or [])

    document: Optional[Dict[str, Any]] = None
    if document_id:
        document = database.get_document(document_id)
        if not document or int(document.get("case_id") or 0) != int(case_id):
            raise NotFound("文稿不存在：{0}".format(document_id))
    else:
        documents = database.list_documents(case_id)
        document = documents[0] if documents else None

    template_text = ""
    stored_text = ""
    rendered_snapshot = ""
    if include_layout and document:
        template_relative = str(document.get("template_path") or "")
        if template_relative:
            path = storage.absolute(template_relative)
            if Path(path).is_file():
                template_text = docgen.extract_document_text(path) or ""
        docx_relative = str(document.get("docx_path") or "")
        if docx_relative:
            path = storage.absolute(docx_relative)
            if Path(path).is_file():
                stored_text = docgen.extract_document_text(path) or ""
                snapshot_path = Path(path).with_suffix(".render.txt")
                if snapshot_path.is_file():
                    try:
                        rendered_snapshot = snapshot_path.read_text(encoding="utf-8")
                    except OSError:
                        rendered_snapshot = ""

    items = checker.check_case(case_data, include_layout=include_layout,
                               template_text=template_text, stored_text=stored_text,
                               rendered_snapshot=rendered_snapshot)
    stored = database.replace_checklist(case_id, (document or {}).get("id"), items, actor)

    blocking = [item for item in stored
                if item.get("severity") == "blocking" and item.get("status") == "open"]
    codes = sorted({str(item.get("code")) for item in stored})
    detail = "校核 {0} 项，其中阻断 {1} 项".format(len(stored), len(blocking))
    if codes:
        detail += "；类别：" + "、".join(codes[:8]) + ("…" if len(codes) > 8 else "")
    return StageResult("check_document", True, detail, {
        "document_id": (document or {}).get("id"),
        "item_count": len(stored),
        "blocking_count": len(blocking),
        "codes": codes,
        "items": [{"code": item.get("code"), "severity": item.get("severity"),
                   "category": item.get("category"), "title": item.get("title"),
                   "status": item.get("status")} for item in stored],
        "advance": _advance(database, case_id, "check_document", actor),
    })


# --------------------------------------------------------------------------
# 7. 交付（delivered）
# --------------------------------------------------------------------------


def run_delivery(database, storage, case_id: int, *,
                 document_id: Optional[int] = None,
                 formats: Sequence[str] = ("docx", "pdf"), expected_version: int = 0,
                 actor: str = "复核人") -> StageResult:
    """保存交付：只允许引用 `doc_status="approved"` 的文稿（INV-8 / R-41）。"""

    database.claim(case_id, expected_version, actor)
    docgen = _module("docgen")
    formats = [str(item) for item in formats or ("docx",)]

    document: Optional[Dict[str, Any]] = None
    if document_id:
        document = database.get_document(document_id)
        if not document or int(document.get("case_id") or 0) != int(case_id):
            raise NotFound("文稿不存在：{0}".format(document_id))
    else:
        for item in database.list_documents(case_id):
            if item.get("doc_status") == "approved":
                document = item
                break
    if not document:
        raise InvalidTransition("没有可交付的已审核文稿：请先生成并审核文稿（INV-8）")
    if document.get("doc_status") != "approved":
        raise InvalidTransition(
            "仅已审核文稿可交付：当前状态为 {0}（INV-8）".format(document.get("doc_status")))

    case_no = str((_case(database, case_id) or {}).get("case_no") or "case")
    updates: Dict[str, Any] = {}
    missing: List[str] = []

    docx_relative = str(document.get("docx_path") or "")
    docx_path = storage.absolute(docx_relative) if docx_relative else None
    if "docx" in formats and (docx_path is None or not Path(docx_path).is_file()):
        missing.append("docx")

    pdf_relative = str(document.get("pdf_path") or "")
    pdf_path = storage.absolute(pdf_relative) if pdf_relative else None
    if "pdf" in formats and (pdf_path is None or not Path(pdf_path).is_file()):
        if docx_path is not None and Path(docx_path).is_file():
            exported = docgen.export_pdf(docx_path, storage.documents_dir(case_no))
            if exported:
                pdf_relative = storage.relative(exported)
                updates["pdf_path"] = pdf_relative
                pdf_path = storage.absolute(pdf_relative)
            else:
                missing.append("pdf")
        else:
            missing.append("pdf")

    if updates:
        document = database.update_document_assets(document["id"], updates, 0, actor) or document

    database.add_event(case_id, "delivered", "交付已保存",
                       "文稿 v{0}；格式：{1}".format(document.get("version_no"),
                                                  "、".join(formats))
                       + ("；缺失：" + "、".join(missing) if missing else ""),
                       actor)
    advance = _advance(database, case_id, "delivered", actor)
    ok = not missing
    detail = "交付已保存：文稿 v{0}，格式 {1}".format(document.get("version_no"),
                                                    "、".join(formats))
    if missing:
        detail += "；以下格式未能生成：{0}".format("、".join(missing))
    return StageResult("delivered", ok, detail, {
        "document_id": document.get("id"),
        "version_no": document.get("version_no"),
        "doc_status": document.get("doc_status"),
        "docx_path": str(document.get("docx_path") or ""),
        "pdf_path": str(document.get("pdf_path") or ""),
        "missing": missing,
        "advance": advance,
    })


# --------------------------------------------------------------------------
# 工具
# --------------------------------------------------------------------------


def _as_dict(item: Any) -> Dict[str, Any]:
    """把 dataclass / sqlite3.Row / Mapping 统一成 dict（不改变字段名）。"""

    if item is None:
        return {}
    if isinstance(item, Mapping):
        return dict(item)
    if hasattr(item, "__dict__") and not isinstance(item, type):
        return {key: value for key, value in vars(item).items() if not key.startswith("_")}
    try:
        return {key: item[key] for key in item.keys()}  # type: ignore[index]
    except Exception:  # noqa: BLE001
        return {}
