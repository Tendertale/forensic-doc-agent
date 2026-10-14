"""快速通道：一次调用跑完主流程（`docs/architecture.md` 第 4 节 `POST /api/quick-run`）。

需求依据：R-59～R-63（主流程透明化：上传 + 结果）、R-54～R-57（部位照片）、
R-55/N-11（照片永不进入识别或模型调用）、N-5（空白写「未记录」，不得写「未见异常」）、
R-51（文件命名 案件编号＋文档类型＋版本）、R-63（一次调用跑完整条链路，单步失败不丢结果）。

本模块只做两件事：

1. **纯函数推断**：:func:`infer_material_kind` 与 :func:`infer_position_key`。
   只使用**文件名与扩展名**这类用户提供的文字线索（R-60）；**绝不读取图片字节、
   绝不做内容识别、描述或推断**（N-11）。推断结果一律带 ``inferred=True`` 与中文
   ``reason``，由前端在结果页显式标注「按文件名推断，可修改」。
2. **流水线编排**：:func:`run_quick_pipeline` = 建案 → 登记材料 → 预处理 → 识别 →
   提取 → 整理 → 生成 → 校核。步骤函数全部来自 `workflow.py` 的冻结签名。

乐观锁（INV-9）与 `expected_version`：
    本流程是**新建案件**，语义等价于 `POST /api/cases` + 后续全部阶段，
    因此请求体（`schemas.QuickRunRequest`）**不携带 `expected_version`**
    —— 案件在这一步才刚刚建立，不存在可供校验的既有版本。
    管道内部所有写入统一传 ``expected_version=0``（系统内部写：不重复校验，
    但同样占位并递增 `cases.version`，与 `workflow.py` 内部写口径一致，见其 D1 修正）。
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from .constants import (
    CASE_STAGES,
    DEFAULT_TEMPLATE_VERSION,
    MATERIAL_KIND_LABELS,
    PHOTO_MATERIAL_KIND,
    PHOTO_POSITION_KEYS,
    PHOTO_POSITION_LABELS,
)
from .workflow import (
    run_check,
    run_extraction,
    run_normalization,
    run_ocr,
    run_preprocess,
    run_render,
)

__all__ = ["infer_material_kind", "infer_position_key", "run_quick_pipeline"]


#: 执行者名称（写入 `case_events` 审计事件；R-48）。
_ACTOR = "快速通道"

#: 需要预处理（生成识别用副本）的材料类型；照片**永不**进入（N-11/R-55）。
_PREPROCESS_KINDS: Tuple[str, ...] = ("commission", "record_book")

#: 预处理操作：与 `schemas.PreprocessRequest` 的默认值一致。
_PREPROCESS_OPERATIONS: Tuple[str, ...] = ("autocontrast",)


# --------------------------------------------------------------------------
# 一、文件名线索表（只用用户提供的文字，N-11 / R-60）
# --------------------------------------------------------------------------

#: Word 文档后缀。`.doc` 只按扩展名归类，系统不解析旧二进制格式。
_WORD_SUFFIXES: Tuple[str, ...] = (".docx", ".doc")
#: 图片后缀（与 `imaging.IMAGE_SUFFIXES` 同口径）。
_IMAGE_SUFFIXES: Tuple[str, ...] = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff")
#: 委托书文件名关键词（含英文 commission）。
_COMMISSION_KEYWORDS: Tuple[str, ...] = ("委托书", "委托", "受理", "commission")
#: 记录本文件名关键词（含英文 record）。
_RECORD_KEYWORDS: Tuple[str, ...] = ("记录本", "记录", "尸表", "检验", "record")

#: 部位栏目关键词表：``(栏位族, 关键词…)``。
#:
#: 匹配规则是「**由具体到宽泛**」：先按表的先后顺序找栏位族，族内关键词也按
#: 由具体到宽泛排列（如「小腿」先于「腿」）。两处刻意的顺序安排：
#:
#: * ``back`` 族排在 ``face`` 族之前 —— 否则「背面.jpg」会被单字「面」抢走而
#:   误判为面部；同理「腰背部」由双字「腰背」命中 ``back``，不会落到 ``waist_hip``。
#: * ``body_overall`` 族放最后（最宽泛），与 `constants.PHOTO_POSITION_DEFINITIONS`
#:   的目录顺序一致。
_POSITION_RULES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("hand", ("手指", "手掌", "手", "掌")),
    ("foot", ("脚踝", "踝", "足", "脚")),
    ("leg", ("小腿", "大腿", "下肢", "膝", "腿")),
    ("arm", ("上臂", "前臂", "胳膊", "上肢")),
    ("back", ("腰背部", "背部", "背面", "腰背", "背")),
    ("head", ("头部", "头颅", "颅", "头", "顶")),
    ("face", ("面部", "颜面", "脸", "面")),
    ("neck", ("颈部", "颈项", "颈", "项")),
    ("chest", ("胸部", "胸")),
    ("abdomen", ("腹部", "腹")),
    ("waist_hip", ("腰臀部", "臀部", "骶部", "腰", "臀", "骶")),
    ("perineum", ("会阴部", "会阴", "阴部", "生殖")),
    ("body_overall", ("全身", "body")),
)

#: 有左右侧之分的栏位族：同族按 ``<族>_left`` / ``<族>_right`` 取值。
_BILATERAL_FAMILIES: Tuple[str, ...] = ("hand", "foot", "leg", "arm")

#: 左右侧文字线索。
_LEFT_WORDS: Tuple[str, ...] = ("左",)
_RIGHT_WORDS: Tuple[str, ...] = ("右",)

#: 材料类型说明文案（结果页「自动归类说明表」，R-62）。
_KIND_REASONS: Dict[str, str] = {
    "template": "第一份 Word 文档按报告模板处理（模板原件只读使用，N-8）",
    "approved_report": "不是第一份 Word 文档（或已有模板），按已审核报告处理，只用于学习格式（N-10）",
    "commission": "文件名含委托/受理关键词，按委托书处理",
    "record_book_record": "文件名含记录/尸表/检验关键词，按记录本处理",
    "record_book_default": "文件名未出现委托关键词，按扩展名默认按记录本处理",
    "photo": "文件名命中部位关键词，按部位照片处理（不读取照片内容，N-11）",
    "record_book_no_keyword": "未匹配部位关键词，先按扫描页识别；若是部位照片请在案件记录里改类型",
    "supplement_json": "JSON 文件按补充资料登记",
    "supplement_other": "无法从文件名判断类型，先按补充资料登记，请在案件记录中修改",
}


def _field(source: Any, name: str, default: Any = None) -> Any:
    """从 `QuickRunRequest` / `QuickRunFile` 实例或普通 dict 中取一个字段。"""

    if isinstance(source, Mapping):
        value = source.get(name, default)
    else:
        value = getattr(source, name, default)
    return default if value is None else value


def _suffix(filename: str) -> str:
    return Path(str(filename or "")).suffix.lower()


def _match_position(filename: str) -> Tuple[str, str]:
    """按 `_POSITION_RULES` 返回 ``(栏位族, 命中的关键词)``；无命中返回 ``("", "")``。

    只做字符串包含判断，不打开任何文件（N-11）。
    """

    text = str(filename or "")
    for family, keywords in _POSITION_RULES:
        for keyword in keywords:
            if keyword and keyword in text:
                return family, keyword
    return "", ""


def infer_position_key(filename: str) -> Dict[str, Any]:
    """按文件名推断部位照片的目标位置（R-60；**只看文件名，不看图片内容** N-11）。

    规则（由具体到宽泛）：

    * 手/掌 → ``hand_left`` / ``hand_right``；足/脚/踝 → ``foot_*``；
      小腿/大腿/膝/腿/下肢 → ``leg_*``；上臂/前臂/胳膊/上肢 → ``arm_*``；
      头/颅/顶 → ``head``；面/脸 → ``face``；颈/项 → ``neck``；胸 → ``chest``；
      腹 → ``abdomen``；背/腰背 → ``back``；腰/臀/骶 → ``waist_hip``；
      会阴/阴部/生殖 → ``perineum``；全身/body → ``body_overall``；
    * 文件名中的「左」/「右」决定 ``_left`` / ``_right``；四肢类**未注明左右侧**时
      **位置留空**（不推测左右，N-4），由 `PHOTO_POSITION_MISSING` 提醒用户指定（R-57）；
    * 无命中 → ``position_key="other"``、``matched=False``；调用方应**留空位置**
      交给校核提示（R-57），不要用其它栏位顶替（N-12）。

    返回 ``{"position_key", "position_label", "reason", "inferred": True, "matched"}``。
    """

    name = str(filename or "")
    family, keyword = _match_position(name)
    if not family:
        return {
            "position_key": "other",
            "position_label": PHOTO_POSITION_LABELS.get("other", "其他部位"),
            "reason": "文件名未出现部位关键词，无法推断目标位置；请上传时指定，"
                      "或在案件记录的「照片位置」里指定（R-57）",
            "inferred": True,
            "matched": False,
        }

    if family in _BILATERAL_FAMILIES:
        if any(word in name for word in _LEFT_WORDS):
            key = "{0}_left".format(family)
            side_note = "文件名含「左」，匹配左侧栏位"
        elif any(word in name for word in _RIGHT_WORDS):
            key = "{0}_right".format(family)
            side_note = "文件名含「右」，匹配右侧栏位"
        else:
            # 不臆断左右（N-4）也绝不占位到某一侧：位置留空 → 让 R-57 的
            # PHOTO_POSITION_MISSING 校核项提醒用户指定，绝不把照片塞进错误的左右栏位。
            return {
                "position_key": "",
                "position_label": "",
                "reason": "文件名含「{0}」但未注明左右侧：不推测左右，位置留空，"
                          "请在案件记录的「照片位置」中指定后重新生成（N-4/R-57）".format(keyword),
                "inferred": True,
                "matched": False,
            }
        label = PHOTO_POSITION_LABELS.get(key, key)
        return {
            "position_key": key,
            "position_label": label,
            "reason": "文件名含「{0}」→ {1}；{2}".format(keyword, label, side_note),
            "inferred": True,
            "matched": True,
        }

    label = PHOTO_POSITION_LABELS.get(family, family)
    return {
        "position_key": family,
        "position_label": label,
        "reason": "文件名含「{0}」→ {1}".format(keyword, label),
        "inferred": True,
        "matched": True,
    }


def infer_material_kind(filename: str, media_type: str, *, index: int = 0,
                        has_template: bool = False) -> Dict[str, Any]:
    """按文件名与扩展名推断材料类型（R-60；**不读取图片字节** N-11）。

    :param filename: 用户提供的文件名（唯一可用的文字线索）。
    :param media_type: `schemas.QuickRunFile.media_type`；为 ``other`` 时只看扩展名。
    :param index: 该文件在**本批 Word 文档**中的序号（``0`` = 第一份 Word 文档）。
    :param has_template: 本批材料里是否**已经**出现报告模板。

    规则：

    * ``.docx`` / ``.doc`` → 第一份且尚无模板时 ``template``，其余 ``approved_report``；
    * ``.pdf`` → 文件名含 ``委托|受理|委托书|commission`` → ``commission``；
      含 ``记录|尸表|检验|记录本|record`` → ``record_book``；
      否则 ``record_book``（reason 注明「按扩展名默认按记录本处理」）；
    * 图片（jpg/jpeg/png/webp/bmp/tif/tiff）→ 文件名命中部位关键词 → ``photo``；
      否则 ``record_book``（reason 注明「未匹配部位关键词……」）；
    * ``.json`` → ``supplement``；其他 → ``supplement``。

    返回 ``{"kind", "kind_label", "reason", "inferred": True}``（``kind_label`` 取自
    `constants.MATERIAL_KIND_LABELS`）。**推断只是默认值**：用户在前端点选时以用户为准。
    """

    name = str(filename or "")
    suffix = _suffix(name)
    media = str(media_type or "").strip().lower()

    if suffix in _WORD_SUFFIXES or media == "docx":
        if index == 0 and not has_template:
            return _kind_result("template", _KIND_REASONS["template"])
        return _kind_result("approved_report", _KIND_REASONS["approved_report"])

    if suffix == ".pdf" or media == "pdf":
        if any(keyword in name for keyword in _COMMISSION_KEYWORDS):
            return _kind_result("commission", _KIND_REASONS["commission"])
        if any(keyword in name for keyword in _RECORD_KEYWORDS):
            return _kind_result("record_book", _KIND_REASONS["record_book_record"])
        return _kind_result("record_book", _KIND_REASONS["record_book_default"])

    if suffix in _IMAGE_SUFFIXES or media in ("jpeg", "png"):
        family, _keyword = _match_position(name)
        if family:
            return _kind_result("photo", _KIND_REASONS["photo"])
        return _kind_result("record_book", _KIND_REASONS["record_book_no_keyword"])

    if suffix == ".json" or media == "json":
        return _kind_result("supplement", _KIND_REASONS["supplement_json"])

    return _kind_result("supplement", _KIND_REASONS["supplement_other"])


def _kind_result(kind: str, reason: str) -> Dict[str, Any]:
    return {
        "kind": kind,
        "kind_label": MATERIAL_KIND_LABELS.get(kind, kind),
        "reason": reason,
        "inferred": True,
    }


def _derive_media_type(filename: str, media_type: Any) -> str:
    """把 ``media_type`` 归一化：用户给了具体值就用它，否则按扩展名（同类文字线索）。"""

    value = str(media_type or "").strip().lower()
    if value and value != "other":
        return value
    return {
        ".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg", ".webp": "other",
        ".bmp": "other", ".tif": "other", ".tiff": "other",
        ".pdf": "pdf", ".docx": "docx", ".doc": "docx", ".json": "json",
    }.get(_suffix(filename), "other")


def _normalize_formats(value: Any) -> List[str]:
    """输出格式：只接受 docx / pdf，去重且保持用户顺序；缺省 docx+pdf。"""

    items = value if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) else []
    picked: List[str] = []
    for item in items or []:
        text = str(item or "").strip().lower()
        if text in ("docx", "pdf") and text not in picked:
            picked.append(text)
    return picked or ["docx", "pdf"]


# --------------------------------------------------------------------------
# 二、流水线
# --------------------------------------------------------------------------


def run_quick_pipeline(database, storage, payload) -> Dict[str, Any]:
    """一次调用跑完「建案 → 登记材料 → 预处理 → 识别 → 提取 → 整理 → 生成 → 校核」。

    * **新建案件**：`payload` 里的 ``case_no`` / ``commission_unit`` / ``subject_name`` /
      ``note`` 直接透传；``case_no`` 留空时由 `database.create_case` 自动生成
      ``FY-<年份>-<序号>``。因此本流程**不携带 `expected_version`**（`QuickRunRequest`
      已冻结这一约定），内部写统一传 ``0``。
    * **材料登记**：``content_base64`` 非空时落盘到 ``originals/<kind>/``；``kind`` /
      ``position_key`` 留空时用 :func:`infer_material_kind` / :func:`infer_position_key`
      的推断结果；照片推断不出位置时**留空**，交给校核提示（R-57），绝不用别的栏位顶替。
    * **照片**：只登记元数据与文件，永不进入预处理、识别、提取或任何模型调用（N-11）。
    * **单步失败不丢结果**（R-63）：任何阶段异常都写进 ``steps[].ok=false`` + ``detail``
      后继续；PDF 导出失败只记入 ``notes``，不抛错、不返回 500。
    * ``files`` 为空由 Pydantic 拦下（422）；**所有** ``content_base64`` 都为空时抛
      ``ValueError``（→ HTTP 400），因为没有字节就无法建立可处理的材料。

    返回结构见 `docs/architecture.md` 第 4 节 `POST /api/quick-run`：
    ``{case_id, case_no, stage, steps[], assignments[], summary{}, documents[], notes[], case_url}``。
    """

    files = [item for item in (_field(payload, "files", []) or [])]
    if not files:
        raise ValueError("至少需要上传一份材料才能建立案件")
    if not any(str(_field(item, "content_base64", "") or "").strip() for item in files):
        raise ValueError(
            "未收到任何文件内容（content_base64 全部为空）：请上传材料文件本身，"
            "只登记文件名无法形成可处理的材料"
        )

    formats = _normalize_formats(_field(payload, "formats", None))
    case_no_input = str(_field(payload, "case_no", "") or "").strip()
    commission_unit = str(_field(payload, "commission_unit", "") or "").strip()
    subject_name = str(_field(payload, "subject_name", "") or "").strip()
    note_input = str(_field(payload, "note", "") or "").strip()
    filenames = [str(_field(item, "filename", "") or "").strip() or "material" for item in files]

    steps: List[Dict[str, Any]] = []
    notes: List[str] = []
    assignments: List[Dict[str, Any]] = []
    registered: List[Dict[str, Any]] = []
    photo_ids: List[int] = []

    # ---- 1. 建案（等价于 POST /api/cases；无 expected_version）----------------
    case = database.create_case({
        "case_no": case_no_input,
        "commission_unit": commission_unit,
        "subject_name": subject_name,
        "note": note_input or "快速通道：上传即出结果（R-59～R-63）",
        "template_version": DEFAULT_TEMPLATE_VERSION,
        "intake": {
            "input_materials": "、".join(filenames)[:2000],
            "output_documents": "、".join(formats),
            "template_requirement": "未指定时使用内置演示模板",
            "content_requirement": "上传材料 → 自动登记、识别、提取、整理、生成、校核（R-63）",
            "format_requirement": "本次输出：" + "、".join(formats),
            "final_artifacts": ",".join(formats),
            "open_questions": "材料类型与照片位置按文件名推断，请到「案件记录」核对（R-60）",
        },
    }, actor=_ACTOR)
    case_id = int(case.get("id") or 0)
    case_no = str(case.get("case_no") or "")

    # ---- 2. 逐份登记材料 ------------------------------------------------------
    has_template = False
    word_index = 0
    inferred_count = 0
    inferred_anything = False
    for index, item in enumerate(files):
        filename = filenames[index]
        media_type = _derive_media_type(filename, _field(item, "media_type", "other"))
        suffix = _suffix(filename)
        is_word = suffix in _WORD_SUFFIXES or media_type == "docx"
        guess = infer_material_kind(filename, media_type,
                                    index=word_index if is_word else index,
                                    has_template=has_template)
        if is_word:
            word_index += 1

        chosen_kind = str(_field(item, "kind", "") or "").strip()
        kind = chosen_kind or guess["kind"]
        kind_inferred = not chosen_kind
        kind_reason = guess["reason"] if kind_inferred else "用户指定类型"
        if kind_inferred:
            inferred_count += 1
            inferred_anything = True

        position_key = ""
        position_label = ""
        position_inferred = False
        position_reason = ""
        if kind == PHOTO_MATERIAL_KIND:
            given_position = str(_field(item, "position_key", "") or "").strip()
            if given_position:
                position_key = given_position
                position_label = PHOTO_POSITION_LABELS.get(given_position, given_position)
                position_reason = "用户指定位置"
            else:
                position = infer_position_key(filename)
                position_inferred = True
                position_reason = position["reason"]
                inferred_anything = True
                if position["matched"]:
                    position_key = position["position_key"]
                    position_label = position["position_label"]
                else:
                    notes.append(
                        "照片「{0}」未推断出位置，已留空以便校核提示（R-57）："
                        "请在案件记录的「照片位置」里指定".format(filename))

        material_payload = {
            "kind": kind,
            "filename": filename,
            "media_type": media_type,
            "page_count": 1 if (kind == PHOTO_MATERIAL_KIND or suffix in _IMAGE_SUFFIXES
                                 or media_type in ("png", "jpeg")) else 0,
            "content_base64": str(_field(item, "content_base64", "") or ""),
            "clarity_note": str(_field(item, "clarity_note", "") or ""),
            "sort_order": index,
            "position_key": position_key,
            "photo_caption": "",
        }
        try:
            # 新建案件：系统内部写用 expected_version=0（不重复校验，但同样递增版本）
            material = database.add_material(case_id, material_payload, 0, storage, _ACTOR)
        except Exception as error:  # noqa: BLE001 - 单份材料失败不中断整批（R-63）
            notes.append("材料「{0}」登记失败：{1}".format(filename, error))
            continue

        registered.append(material)
        if str(material.get("kind") or "") == "template":
            has_template = True
        material_id = int(material.get("id") or 0)
        if str(material.get("kind") or "") == PHOTO_MATERIAL_KIND:
            photo_ids.append(material_id)
        else:
            # 页级探测只针对扫描件（PDF/图片）。照片绝不打开（N-11）。
            _register_page_probe(database, storage, material)

        assignments.append({
            "filename": filename,
            "kind": str(material.get("kind") or kind),
            "kind_label": MATERIAL_KIND_LABELS.get(str(material.get("kind") or kind), kind),
            "kind_inferred": bool(kind_inferred),
            "kind_reason": kind_reason,
            "position_key": str(material.get("position_key") or ""),
            "position_label": PHOTO_POSITION_LABELS.get(
                str(material.get("position_key") or ""), ""),
            "position_inferred": bool(position_inferred),
            "position_reason": position_reason,
        })

    steps.append({
        "step": "materials",
        "ok": bool(registered),
        "detail": "登记 {0} 份材料（推断 {1}）".format(len(registered), inferred_count),
    })

    # ---- 3. 预处理：只处理委托书与记录本（照片按 N-11 排除）--------------------
    preprocess_targets = [item for item in registered
                          if str(item.get("kind") or "") in _PREPROCESS_KINDS]
    preprocess_skipped = [str(item.get("filename") or "")
                          for item in preprocess_targets if not (item.get("pages") or [])]
    preprocess_targets = [item for item in preprocess_targets if item.get("pages")]
    if not preprocess_targets:
        steps.append({
            "step": "preprocess",
            "ok": True,
            "detail": "没有需要预处理的材料：委托书/记录本之外的类型不进入预处理，"
                      "部位照片按 N-11 排除"
                      + ("；{0} 没有可识别的页".format("、".join(preprocess_skipped))
                         if preprocess_skipped else ""),
        })
    else:
        pages_done = 0
        failures: List[str] = []
        for material in preprocess_targets:
            try:
                result = run_preprocess(database, storage, case_id, int(material["id"]),
                                        list(_PREPROCESS_OPERATIONS), 0, 0, _ACTOR)
                pages_done += len(result.payload.get("pages") or [])
            except Exception as error:  # noqa: BLE001 - 单份失败继续（R-63）
                failures.append("{0}：{1}".format(material.get("filename"), error))
        detail = "生成识别用副本 {0} 页（委托书/记录本 {1} 份）".format(
            pages_done, len(preprocess_targets))
        if preprocess_skipped:
            detail += "；跳过 {0} 份（没有可识别的页）".format(len(preprocess_skipped))
        if failures:
            detail += "；失败 {0} 份：{1}".format(len(failures), "；".join(failures))
        steps.append({"step": "preprocess", "ok": not failures, "detail": detail})

    # ---- 4. 识别（显式 sidecar 引擎；照片必须出现在跳过清单里）-----------------
    ocr_payload: Dict[str, Any] = {}
    try:
        ocr_result = run_ocr(database, storage, case_id, engine="sidecar", force=False,
                             expected_version=0, actor=_ACTOR)
        ocr_payload = dict(ocr_result.payload or {})
        # `run_ocr` 的 detail 里已经列出跳过的照片张数（N-11/R-55），这里不重复叙述。
        steps.append({"step": "ocr", "ok": bool(ocr_result.ok), "detail": ocr_result.detail})
    except Exception as error:  # noqa: BLE001 - 识别失败继续后续步骤（R-63）
        steps.append({"step": "ocr", "ok": False, "detail": "识别失败：{0}".format(error)})

    skipped_photo_ids = {int(item) for item in (ocr_payload.get("skipped_photo_ids") or [])}
    leaked = [item for item in photo_ids if item not in skipped_photo_ids]
    if leaked:
        # 安全网（预期永不触发）：照片绝不允许进入识别流程（N-11/R-55）。
        notes.append("N-11 告警：照片材料 {0} 未出现在识别跳过清单中，请检查流程".format(leaked))
    if not (ocr_payload.get("blocks") or 0):
        notes.append(
            "本次没有取到识别原文：离线 sidecar 引擎在 derived/<材料类型>/ 下没有找到同名 "
            ".ocr.json（演示引擎）；照片不参与识别（N-11）"
        )

    # ---- 5. 提取 / 6. 整理 / 7. 生成 / 8. 校核 --------------------------------
    render_payload: Dict[str, Any] = {}
    document_id = None

    try:
        result = run_extraction(database, storage, case_id, replace_existing=True,
                                use_llm=False, api_key="", expected_version=0, actor=_ACTOR)
        steps.append({"step": "extract", "ok": bool(result.ok), "detail": result.detail})
    except Exception as error:  # noqa: BLE001
        steps.append({"step": "extract", "ok": False, "detail": "提取失败：{0}".format(error)})

    try:
        result = run_normalization(database, storage, case_id, target="all",
                                   confirm_safe=False, expected_version=0, actor=_ACTOR)
        steps.append({"step": "normalize", "ok": bool(result.ok), "detail": result.detail})
    except Exception as error:  # noqa: BLE001
        steps.append({"step": "normalize", "ok": False, "detail": "术语整理失败：{0}".format(error)})

    try:
        result = run_render(database, storage, case_id, doc_type="report",
                            doc_status="internal_draft", formats=formats,
                            expected_version=0, actor=_ACTOR)
        render_payload = dict(result.payload or {})
        document_id = render_payload.get("document_id")
        steps.append({"step": "render", "ok": bool(result.ok), "detail": result.detail})
    except Exception as error:  # noqa: BLE001 - 生成失败仍继续校核（R-63）
        steps.append({"step": "render", "ok": False, "detail": "生成文稿失败：{0}".format(error)})

    if "pdf" in formats and not render_payload.get("pdf_path"):
        # PDF 导出失败只作为说明，不中断流程、不返回 500（R-63）
        notes.append(
            "PDF 未能生成：LibreOffice 不可用或转换超时；Word 文稿已生成，"
            "可安装 LibreOffice 后重新导出，或在本机用 Word 另存为 PDF"
        )

    # 生成阶段的说明（`docgen.RenderResult.notes`）：系统做了什么必须可见（R-59～R-63）。
    # 上游尚未把 notes 放进 payload 时，按模板与产物自行核对，仍然如实说明照片的去向，
    # 绝不因为“没拿到说明字段”就让照片静默消失。
    render_notes = _render_notes(render_payload)
    notes.extend(render_notes)
    if not render_notes:
        notes.extend(_photo_placement_notes(database, storage, render_payload, registered))

    try:
        result = run_check(database, storage, case_id,
                           document_id=int(document_id) if document_id else None,
                           include_layout=True, expected_version=0, actor=_ACTOR)
        steps.append({"step": "check", "ok": bool(result.ok), "detail": result.detail})
    except Exception as error:  # noqa: BLE001
        steps.append({"step": "check", "ok": False, "detail": "校核失败：{0}".format(error)})

    # ---- 9. 阶段标记推进到「校核文档」（只补标记，不代替人工复核）-------------
    advance_note = _advance_to_check(database, case_id)
    if advance_note:
        notes.append(advance_note)

    # ---- 10. 说明与结果 ------------------------------------------------------
    if inferred_anything:
        notes.append("自动归类仅依据文件名；部位照片的位置可在案件记录中修改（N-11）")
    if not has_template:
        notes.append("未提供模板，已使用内置演示模板 {0}".format(DEFAULT_TEMPLATE_VERSION))
    if any(item["kind"] == PHOTO_MATERIAL_KIND and not item["position_key"]
           for item in assignments):
        notes.append("有照片尚未指定位置：不会进入文稿，并会作为待处理项列出（R-57/N-12）")

    case_data = database.get_case(case_id) or {}
    summary_source = dict(case_data.get("summary") or {})
    checklist = database.list_checklist(case_id) or []
    summary = {
        "material_count": int(summary_source.get("material_count") or 0),
        "field_count": int(summary_source.get("field_count") or 0),
        "injury_count": int(summary_source.get("injury_count") or 0),
        "document_count": int(summary_source.get("document_count") or 0),
        "blocking_count": int(summary_source.get("blocking_count") or 0),
        "warning_count": sum(1 for item in checklist if item.get("severity") == "warning"),
        "info_count": sum(1 for item in checklist if item.get("severity") == "info"),
        "unresolved_count": int(summary_source.get("unresolved_count") or 0),
    }
    if summary["unresolved_count"]:
        notes.append(
            "仍有 {0} 项待核对（身份、关键所见、占位符等）：本版本是内部草稿，"
            "未进入待签发稿（R-27/R-28）".format(summary["unresolved_count"])
        )

    documents = [_document_entry(storage, item)
                 for item in database.list_documents(case_id) or []]

    try:
        database.add_event(case_id, "quick_run", "快速通道一次完成",
                           "；".join("{0}={1}".format(step["step"], "成功" if step["ok"] else "失败")
                                     for step in steps), _ACTOR)
    except Exception:  # noqa: BLE001 - 审计事件失败不影响结果返回
        pass

    return {
        "case_id": case_id,
        "case_no": case_no,
        "stage": str(case_data.get("stage") or ""),
        "steps": steps,
        "assignments": assignments,
        "summary": summary,
        "documents": documents,
        "notes": _dedupe(notes),
        "case_url": "/api/cases/{0}".format(case_id),
    }


# --------------------------------------------------------------------------
# 三、内部工具
# --------------------------------------------------------------------------


def _render_notes(render_payload: Mapping[str, Any]) -> List[str]:
    """透出 `docgen.RenderResult.notes`（渲染过程中的中文说明）。

    对 payload 里的 ``notes`` 做**前向兼容**读取：`workflow.run_render` 目前只放部分
    字段，一旦上游补上 ``notes`` 就自动生效，本模块不需要再改；为空则返回空列表，
    由 :func:`_photo_placement_notes` 按产物核对兜底说明。
    """

    value = render_payload.get("notes") if isinstance(render_payload, Mapping) else None
    if not isinstance(value, (list, tuple)):
        return []
    result: List[str] = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _document_docx_path(storage, render_payload: Mapping[str, Any]):
    """本次生成文稿的绝对路径（payload 优先，其次按存储根拼接）；没有则返回 None。"""

    absolute = str(render_payload.get("docx_absolute") or "")
    if absolute:
        return Path(absolute)
    relative = str(render_payload.get("docx_path") or "")
    if relative:
        try:
            return Path(storage.absolute(relative))
        except Exception:  # noqa: BLE001 - 路径解析失败按“没有文稿”处理
            return None
    return None


def _template_text_used(database, storage, render_payload: Mapping[str, Any]) -> str:
    """本次生成所用模板的正文文本（只读文件；读不出来返回空串）。

    模板路径取自文稿行的 ``template_path``（`workflow.run_render` 实际使用的那一份）。
    只读文档文本，不打开、不解码任何图片（N-11）。
    """

    try:
        document_id = int(render_payload.get("document_id") or 0)
    except (TypeError, ValueError):
        document_id = 0
    if not document_id:
        return ""
    try:
        document = database.get_document(document_id) or {}
    except Exception:  # noqa: BLE001 - 读不到文稿行时按“模板文本未知”处理
        return ""
    relative = str(document.get("template_path") or "")
    if not relative:
        return ""
    try:
        path = Path(storage.absolute(relative))
    except Exception:  # noqa: BLE001
        return ""
    if not path.is_file():
        return ""
    try:
        docgen = importlib.import_module(".docgen", __package__)
        return docgen.extract_document_text(str(path)) or ""
    except Exception:  # noqa: BLE001 - 模板文本读不出来时不猜栏位
        return ""


def _inline_shape_count(path: Path) -> int:
    """文稿里的图片个数（只数个数，不解码、不查看像素内容，N-11）；读不出来返回 -1。"""

    try:
        from docx import Document as _DocxDocument

        return len(_DocxDocument(str(path)).inline_shapes)
    except Exception:  # noqa: BLE001
        return -1


def _photo_placement_notes(database, storage, render_payload: Mapping[str, Any],
                           registered: Sequence[Mapping[str, Any]]) -> List[str]:
    """按模板与产物核对“照片到底进没进文稿”，并把结论写进说明（绝不静默）。

    只在**确有已指派位置的照片**时产出说明。判据（全部只看文件与文本，N-11）：

    * 模板自己声明了照片栏位 → 不在这里说明：逐栏位情形由校核清单负责
      （``PHOTO_FILE_MISSING`` / ``PHOTO_SLOT_EMPTY`` / ``PHOTO_SLOT_MISSING``）；
    * 模板没有任何 ``{{ photo_* }}`` 锚点 → 生成时由 `docgen.render_document` 向
      **工作副本**自动追加标准「部位照片」区（18 个栏位，模板原件不改动，N-8）：
      文稿里确实有图片 → 说明“已自动追加并插入”；文稿里没有图片或文稿根本没生成
      → 如实写明“照片未插入文稿”，并给出下一步（在模板里加栏位后重新生成）。
    """

    assigned = [
        item
        for item in registered or []
        if str(item.get("kind") or "") == PHOTO_MATERIAL_KIND
        and str(item.get("position_key") or "").strip()
    ]
    if not assigned:
        return []

    docgen = None
    try:
        docgen = importlib.import_module(".docgen", __package__)
    except Exception:  # noqa: BLE001 - docgen 不可用时退化为“只按目录栏位数说明”
        docgen = None

    template_text = _template_text_used(database, storage, render_payload)
    declared: List[str] = []
    if docgen is not None:
        try:
            declared = list(docgen.template_photo_keys(template_text) or [])
        except Exception:  # noqa: BLE001
            declared = []
    if declared:
        # 模板声明了照片栏位（含演示模板的 18 个）：栏位级的说明交给校核清单。
        return []

    docx_path = _document_docx_path(storage, render_payload)
    if docx_path is None or not docx_path.is_file():
        return [
            "本次未生成 Word 文稿：照片未插入文稿（照片不会被静默省略，"
            "请查看「生成」步骤的说明后重试）"
        ]

    inserted = _inline_shape_count(docx_path)
    if inserted < 0:
        return [
            "模板未声明照片栏位：无法核对文稿内的图片数量，"
            "请打开文稿确认照片是否已插入（系统只按位置插入照片，不读取照片内容，N-11）"
        ]
    if inserted > 0:
        return [
            "模板未声明照片栏位：已按标准「部位照片」区（{0} 个栏位）自动追加照片栏位"
            "后再生成，模板原件未改动（N-8）；本次文稿内共有图片 {1} 张".format(
                len(PHOTO_POSITION_KEYS), inserted)
        ]
    return [
        "模板未声明照片栏位且未能追加照片区：照片未插入文稿"
        "（已指派位置的照片 {0} 张均未进入文稿，请在模板中加入 {{{{ photo_<位置> }}}} "
        "栏位后重新生成）".format(len(assigned))
    ]


def _register_page_probe(database, storage, material: Mapping[str, Any]) -> int:
    """用 `imaging.probe` 读取**非照片**材料的页级信息并写入 `material_pages`。

    * 只对扫描件（PDF / 图片）调用；照片调用方已排除，绝不打开照片文件（N-11）；
    * PDF 在缺少 pypdfium2（TODO-DL-4）时按字节估计页数并标记为扫描件，
      这样后续预处理与识别至少能给出「无法渲染」的明确说明，而不是静默跳过；
    * 探测失败不影响登记结果（返回 0）。
    """

    relative = str(material.get("original_path") or "")
    if not relative:
        return 0
    path = storage.absolute(relative)
    if not Path(path).is_file():
        return 0
    try:
        imaging = importlib.import_module(".imaging", __package__)
        pages = [page.as_dict() for page in (imaging.probe(str(path)) or [])]
    except Exception:  # noqa: BLE001 - 探测失败按“没有页信息”处理
        return 0
    if not pages:
        return 0
    try:
        database.register_pages(int(material["id"]), pages, 0)
    except Exception:  # noqa: BLE001
        return 0
    return len(pages)


def _advance_to_check(database, case_id: int) -> str:
    """把案件阶段一格一格推进到 ``check_document``（阶段只允许相邻一格，第 3.1 节）。

    快速通道一次调用就跑完了生成与校核，因此这里按 `CASE_STAGES` 的顺序补齐中间标记；
    事件与说明中都写明：这只是**系统已完成到校核**的记录，不代替「核对关键事实」与
    「专业复核」这两项必须由人员完成的工作（R-28/R-29）。
    """

    order = list(CASE_STAGES)
    moved: List[str] = []
    for _ in range(len(order) + 2):
        current = str((database.get_case(case_id) or {}).get("stage") or "")
        if current == "check_document":
            break
        if current not in order:
            break
        index = order.index(current)
        if index + 1 >= len(order):
            break
        target = order[index + 1]
        try:
            # 系统内部推进：expected_version=0（不重复校验，但同样占位递增）
            database.update_stage(case_id, target, 0,
                                  note="快速通道一次完成（R-63）", actor=_ACTOR)
        except Exception as error:  # noqa: BLE001 - 阶段标记失败不影响已完成的产物
            return "阶段标记未推进到「校核文档」：{0}".format(error)
        moved.append(target)
    if not moved:
        return ""
    return ("阶段标记已推进到「校核文档」（{0}）：关键事实核对与专业复核仍需人工完成；"
            "本版本为内部草稿".format(" → ".join(moved)))


def _document_entry(storage, document: Mapping[str, Any]) -> Dict[str, Any]:
    """文稿条目：文件名、是否存在、可直接使用的下载地址（第 4 节）。"""

    document_id = int(document.get("id") or 0)
    entry: Dict[str, Any] = {
        "id": document_id,
        "version_no": int(document.get("version_no") or 0),
        "doc_type": str(document.get("doc_type") or ""),
        "doc_status": str(document.get("doc_status") or ""),
        "filename_docx": "",
        "filename_pdf": "",
        "has_docx": False,
        "has_pdf": False,
        "download_urls": {"docx": "", "pdf": ""},
    }
    for fmt in ("docx", "pdf"):
        relative = str(document.get("{0}_path".format(fmt)) or "")
        if not relative:
            continue
        path = storage.absolute(relative)
        if not Path(path).is_file():
            continue
        entry["filename_{0}".format(fmt)] = Path(path).name
        entry["has_{0}".format(fmt)] = True
        entry["download_urls"][fmt] = "/api/documents/{0}/download?fmt={1}".format(document_id, fmt)
    return entry


def _dedupe(items: Sequence[str]) -> List[str]:
    """按出现顺序去重（说明文案可能来自多个分支）。"""

    result: List[str] = []
    for item in items or []:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result
