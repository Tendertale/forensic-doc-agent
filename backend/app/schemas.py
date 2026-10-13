"""Pydantic 请求模型（冻结契约）。

约定（沿用 hotspot-workbench 的接口风格）：

* 所有会改变案件状态、字段、损伤、规则、文稿或清单的请求都必须携带
  ``expected_version``；版本过期时返回 HTTP 409。
* 响应体使用普通 ``dict``（由 ``database.py`` 组装），本模块只冻结**请求**结构，
  避免响应字段在两处重复声明后产生分歧。
* 字段取值一律引用 ``constants.py``，不在本模块重复书写字面量。
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .constants import (
    CASE_STAGES,
    CHECK_ITEM_STATUSES,
    CORRECTION_KINDS,
    CORRECTION_SCOPES,
    CORRECTION_TARGET_TYPES,
    DEFAULT_TEMPLATE_VERSION,
    FIELD_STATUSES,
    IMAGE_OPERATIONS,
    IMAGE_QUALITY_FLAGS,
    INJURY_FIELDS,
    MATERIAL_KINDS,
    MATERIAL_MEDIA_TYPES,
    OCR_ENGINES,
    OUTPUT_FORMATS,
    PHOTO_POSITION_KEYS,
    PHOTO_POSITION_LABELS,
    REVIEW_STATES,
    TERM_RULE_DEFAULT_VERSION,
)

CaseStage = Literal[
    "intake", "image_check", "ocr", "extract", "verify_facts",
    "normalize", "render", "check_document", "professional_review", "delivered",
]
FieldStatus = Literal["recorded", "not_recorded", "not_examined", "negative", "unclear"]
ReviewState = Literal["pending", "confirmed"]
MaterialKind = Literal[
    "commission", "record_book", "photo", "template", "approved_report",
    "terminology_reference", "supplement",
]
DocStatus = Literal["internal_draft", "pending_issue"]
CheckItemStatus = Literal["open", "resolved", "waived"]
CorrectionKind = Literal["recognition", "terminology", "format", "workflow_order"]
CorrectionTargetType = Literal["field", "injury", "terminology_rule", "document", "case"]
CorrectionScope = Literal["case_only", "field_key", "column", "writer", "template"]
OutputFormat = Literal["docx", "pdf"]

# 启动时自检：Literal 与 constants 中的元组必须一致，避免契约漂移。
for _literal, _tuple, _name in (
    (CaseStage, CASE_STAGES, "CASE_STAGES"),
    (FieldStatus, FIELD_STATUSES, "FIELD_STATUSES"),
    (ReviewState, REVIEW_STATES, "REVIEW_STATES"),
    (MaterialKind, MATERIAL_KINDS, "MATERIAL_KINDS"),
    (CheckItemStatus, CHECK_ITEM_STATUSES, "CHECK_ITEM_STATUSES"),
    (CorrectionKind, CORRECTION_KINDS, "CORRECTION_KINDS"),
    (CorrectionTargetType, CORRECTION_TARGET_TYPES, "CORRECTION_TARGET_TYPES"),
    (CorrectionScope, CORRECTION_SCOPES, "CORRECTION_SCOPES"),
):
    assert tuple(_literal.__args__) == tuple(_tuple), (  # type: ignore[attr-defined]
        "schemas.Literal 与 constants.%s 不一致" % _name
    )


# --------------------------------------------------------------------------
# 案件
# --------------------------------------------------------------------------


class CaseIntake(BaseModel):
    """需求第一章的“任务信息”表，建案时由 Agent 整理。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    input_materials: str = Field(default="", max_length=2000)
    output_documents: str = Field(default="", max_length=1000)
    template_requirement: str = Field(default="", max_length=1000)
    content_requirement: str = Field(default="", max_length=2000)
    format_requirement: str = Field(default="", max_length=1000)
    final_artifacts: str = Field(default="docx,pdf", max_length=80)
    open_questions: str = Field(default="", max_length=2000)

    @field_validator("final_artifacts")
    @classmethod
    def validate_artifacts(cls, value: str) -> str:
        parts = [item.strip().lower() for item in value.split(",") if item.strip()]
        invalid = [item for item in parts if item not in OUTPUT_FORMATS]
        if invalid:
            raise ValueError("最终产物只能是 docx 或 pdf，收到：" + "、".join(invalid))
        return ",".join(dict.fromkeys(parts)) if parts else "docx,pdf"


class CaseCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    case_no: str = Field(default="", max_length=64, description="留空时自动生成 FY-<年份>-<序号>")
    commission_unit: str = Field(default="", max_length=120)
    commission_matter: str = Field(default="", max_length=500)
    commission_date: str = Field(default="", max_length=40)
    case_summary: str = Field(default="", max_length=4000)
    subject_name: str = Field(default="", max_length=80)
    template_version: str = Field(default=DEFAULT_TEMPLATE_VERSION, max_length=80)
    term_rule_version: str = Field(default=TERM_RULE_DEFAULT_VERSION, max_length=80)
    intake: CaseIntake = Field(default_factory=CaseIntake)
    note: str = Field(default="", max_length=300)


class CaseUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    expected_version: int = Field(ge=1)
    case_no: Optional[str] = Field(default=None, max_length=64)
    commission_unit: Optional[str] = Field(default=None, max_length=120)
    commission_matter: Optional[str] = Field(default=None, max_length=500)
    commission_date: Optional[str] = Field(default=None, max_length=40)
    case_summary: Optional[str] = Field(default=None, max_length=4000)
    subject_name: Optional[str] = Field(default=None, max_length=80)
    template_version: Optional[str] = Field(default=None, max_length=80)
    term_rule_version: Optional[str] = Field(default=None, max_length=80)
    section_order: Optional[List[str]] = Field(default=None, max_length=32)
    opinion_text: Optional[str] = Field(
        default=None,
        max_length=4000,
        description="鉴定意见：**只能**由用户请求原样写入，任何代码路径不得从 OCR 文本推导（N-1/INV-10）",
    )
    note: str = Field(default="", max_length=300)

    @model_validator(mode="after")
    def require_change(self):
        fields = ("case_no", "commission_unit", "commission_matter", "commission_date", "case_summary",
                  "subject_name", "template_version", "term_rule_version", "section_order", "opinion_text")
        if all(getattr(self, name) is None for name in fields):
            raise ValueError("至少需要修改一个案件字段")
        return self


class CaseStageUpdate(BaseModel):
    expected_version: int = Field(ge=1)
    stage: CaseStage
    note: str = Field(default="", max_length=300)


# --------------------------------------------------------------------------
# 材料与页面
# --------------------------------------------------------------------------


class MaterialCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    expected_version: int = Field(ge=1)
    kind: MaterialKind
    filename: str = Field(min_length=1, max_length=200)
    media_type: Literal["pdf", "jpeg", "png", "docx", "json", "other"] = "other"
    page_count: int = Field(default=0, ge=0, le=2000)
    content_base64: str = Field(default="", description="可选：文件字节的 base64 编码")
    clarity_note: str = Field(default="", max_length=1000)
    quality_flags: List[str] = Field(default_factory=list, max_length=8)
    sort_order: int = Field(default=0, ge=0, le=9999)
    position_key: str = Field(
        default="",
        max_length=40,
        description="部位照片的目标位置（constants.PHOTO_POSITION_KEYS）。非照片材料必须留空。",
    )
    photo_caption: str = Field(default="", max_length=200, description="照片说明（用户填写，不识别内容）")

    @field_validator("quality_flags")
    @classmethod
    def validate_flags(cls, value: List[str]) -> List[str]:
        allowed = set(IMAGE_QUALITY_FLAGS)
        invalid = [item for item in value if item not in allowed]
        if invalid:
            raise ValueError("未知图像质量标记：" + "、".join(invalid))
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def validate_photo_position(self):
        if self.kind == "photo":
            if self.position_key and self.position_key not in PHOTO_POSITION_KEYS:
                raise ValueError("未知照片目标位置：" + self.position_key)
        elif self.position_key:
            raise ValueError("只有部位照片（kind=photo）可以指定目标位置")
        return self
        return self


class PhotoPlacementUpdate(BaseModel):
    """为一张部位照片指定/调整目标位置。系统只按位置放置，不读取照片内容（N-11）。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    expected_version: int = Field(ge=1)
    position_key: str = Field(default="", max_length=40, description="留空表示尚未指定位置")
    photo_caption: Optional[str] = Field(default=None, max_length=200)
    sort_order: Optional[int] = Field(default=None, ge=0, le=9999)
    note: str = Field(default="", max_length=300)

    @field_validator("position_key")
    @classmethod
    def validate_position(cls, value: str) -> str:
        if value and value not in PHOTO_POSITION_KEYS:
            raise ValueError("未知照片目标位置：" + value + "；可选：" + "、".join(PHOTO_POSITION_LABELS.values()))
        return value


class MaterialPageUpdate(BaseModel):
    expected_version: int = Field(ge=1)
    is_scanned: Optional[bool] = None
    has_text_layer: Optional[bool] = None
    quality_flags: Optional[List[str]] = Field(default=None, max_length=8)
    note: str = Field(default="", max_length=300)


class PreprocessRequest(BaseModel):
    expected_version: int = Field(ge=1)
    operations: List[Literal["rotate", "deskew", "crop", "contrast", "grayscale", "autocontrast"]] = Field(
        default_factory=lambda: ["autocontrast"], min_length=1, max_length=8
    )
    page_number: int = Field(default=0, ge=0, le=2000, description="0 表示整份材料")

    @field_validator("operations")
    @classmethod
    def validate_operations(cls, value: List[str]) -> List[str]:
        invalid = [item for item in value if item not in IMAGE_OPERATIONS]
        if invalid:
            raise ValueError("未知预处理操作：" + "、".join(invalid))
        return list(dict.fromkeys(value))


class OcrRunRequest(BaseModel):
    expected_version: int = Field(ge=1)
    engine: Literal["sidecar", "paddle", "none"] = "sidecar"
    material_id: Optional[int] = Field(default=None, ge=1, description="留空表示识别本案件全部材料")
    force: bool = False


class ExtractRequest(BaseModel):
    expected_version: int = Field(ge=1)
    material_ids: List[int] = Field(default_factory=list, max_length=32)
    replace_existing: bool = Field(default=True, description="False 时只补充缺失字段")
    use_llm: bool = Field(default=False, description="默认关闭；开启时需要 X-DeepSeek-API-Key")


# --------------------------------------------------------------------------
# 字段与损伤
# --------------------------------------------------------------------------


class FieldUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    expected_version: int = Field(ge=1)
    status: Optional[FieldStatus] = None
    confirmed_value: Optional[str] = Field(default=None, max_length=4000)
    normalized_text: Optional[str] = Field(default=None, max_length=4000)
    unit: Optional[str] = Field(default=None, max_length=20)
    review_state: Optional[ReviewState] = None
    note: str = Field(default="", max_length=300)

    @model_validator(mode="after")
    def require_change(self):
        fields = ("status", "confirmed_value", "normalized_text", "unit", "review_state")
        if all(getattr(self, name) is None for name in fields):
            raise ValueError("至少需要修改一个字段属性")
        return self


class InjuryCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    expected_version: int = Field(ge=1)
    seq: int = Field(default=0, ge=0, le=999)
    body_part: str = Field(default="", max_length=120)
    side: str = Field(default="", max_length=20)
    injury_type: str = Field(default="", max_length=60)
    count: str = Field(default="", max_length=20)
    length: str = Field(default="", max_length=20)
    width: str = Field(default="", max_length=20)
    size_unit: str = Field(default="cm", max_length=20)
    morphology: str = Field(default="", max_length=200)
    other_traits: str = Field(default="", max_length=400)
    raw_text: str = Field(default="", max_length=2000)
    status: FieldStatus = "recorded"
    source_material_id: Optional[int] = Field(default=None, ge=1)
    source_page: int = Field(default=0, ge=0, le=2000)
    source_bbox: Optional[List[float]] = Field(default=None, min_length=4, max_length=4)
    source_block_id: Optional[int] = Field(default=None, ge=1)
    confidence: Optional[float] = Field(default=None, ge=0, le=1)
    review_state: ReviewState = "pending"
    note: str = Field(default="", max_length=300)

    @model_validator(mode="after")
    def require_content(self):
        if not any(getattr(self, name) for name in INJURY_FIELDS):
            raise ValueError("至少需要填写一个损伤字段")
        return self


class InjuryUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    expected_version: int = Field(ge=1)
    seq: Optional[int] = Field(default=None, ge=0, le=999)
    body_part: Optional[str] = Field(default=None, max_length=120)
    side: Optional[str] = Field(default=None, max_length=20)
    injury_type: Optional[str] = Field(default=None, max_length=60)
    count: Optional[str] = Field(default=None, max_length=20)
    length: Optional[str] = Field(default=None, max_length=20)
    width: Optional[str] = Field(default=None, max_length=20)
    size_unit: Optional[str] = Field(default=None, max_length=20)
    morphology: Optional[str] = Field(default=None, max_length=200)
    other_traits: Optional[str] = Field(default=None, max_length=400)
    raw_text: Optional[str] = Field(default=None, max_length=2000)
    status: Optional[FieldStatus] = None
    source_material_id: Optional[int] = Field(default=None, ge=1)
    source_page: Optional[int] = Field(default=None, ge=0, le=2000)
    source_bbox: Optional[List[float]] = Field(default=None, min_length=4, max_length=4)
    confidence: Optional[float] = Field(default=None, ge=0, le=1)
    review_state: Optional[ReviewState] = None
    note: str = Field(default="", max_length=300)

    @model_validator(mode="after")
    def require_change(self):
        fields = ("seq", "body_part", "side", "injury_type", "count", "length", "width", "size_unit",
                  "morphology", "other_traits", "raw_text", "status", "source_material_id",
                  "source_page", "source_bbox", "confidence", "review_state")
        if all(getattr(self, name) is None for name in fields):
            raise ValueError("至少需要修改一个损伤属性")
        return self


class NormalizeRequest(BaseModel):
    expected_version: int = Field(ge=1)
    target: Literal["all", "fields", "injuries"] = "all"
    rule_version: str = Field(default=TERM_RULE_DEFAULT_VERSION, max_length=80)
    confirm_safe: bool = Field(
        default=False,
        description="True 时把无需确认的规范化结果直接标记为已确认；默认只生成草稿",
    )


# --------------------------------------------------------------------------
# 术语规则
# --------------------------------------------------------------------------


class TerminologyRuleCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    code: str = Field(min_length=1, max_length=80)
    shorthand: str = Field(default="", max_length=200)
    pattern: str = Field(min_length=1, max_length=500)
    match_type: Literal["literal", "regex"] = "literal"
    template: str = Field(default="", max_length=1000)
    scope_field_key: str = Field(default="", max_length=80)
    scope_column: str = Field(default="", max_length=80)
    scope_writer: str = Field(default="", max_length=80)
    boundary_note: str = Field(default="", max_length=500)
    require_groups: List[str] = Field(default_factory=list, max_length=8)
    missing_group_note: str = Field(default="", max_length=300)
    requires_confirmation: bool = False
    priority: int = Field(default=100, ge=0, le=9999)
    active: bool = True


class TerminologyRuleUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    expected_version: int = Field(ge=1)
    shorthand: Optional[str] = Field(default=None, max_length=200)
    pattern: Optional[str] = Field(default=None, max_length=500)
    match_type: Optional[Literal["literal", "regex"]] = None
    template: Optional[str] = Field(default=None, max_length=1000)
    scope_field_key: Optional[str] = Field(default=None, max_length=80)
    scope_column: Optional[str] = Field(default=None, max_length=80)
    scope_writer: Optional[str] = Field(default=None, max_length=80)
    boundary_note: Optional[str] = Field(default=None, max_length=500)
    require_groups: Optional[List[str]] = Field(default=None, max_length=8)
    missing_group_note: Optional[str] = Field(default=None, max_length=300)
    requires_confirmation: Optional[bool] = None
    priority: Optional[int] = Field(default=None, ge=0, le=9999)
    active: Optional[bool] = None
    note: str = Field(default="", max_length=300)

    @model_validator(mode="after")
    def require_change(self):
        fields = ("shorthand", "pattern", "match_type", "template", "scope_field_key", "scope_column",
                  "scope_writer", "boundary_note", "require_groups", "missing_group_note",
                  "requires_confirmation", "priority", "active")
        if all(getattr(self, name) is None for name in fields):
            raise ValueError("至少需要修改一个规则属性")
        return self


# --------------------------------------------------------------------------
# 文稿与校核
# --------------------------------------------------------------------------


class DocumentCreate(BaseModel):
    expected_version: int = Field(ge=1)
    doc_type: str = Field(default="report", max_length=40)
    doc_status: DocStatus = "internal_draft"
    template_version: str = Field(default=DEFAULT_TEMPLATE_VERSION, max_length=80)
    formats: List[OutputFormat] = Field(default_factory=lambda: ["docx", "pdf"], min_length=1, max_length=2)
    note: str = Field(default="", max_length=300)

    @field_validator("formats")
    @classmethod
    def dedupe_formats(cls, value: List[str]) -> List[str]:
        return list(dict.fromkeys(value))


class CheckRequest(BaseModel):
    expected_version: int = Field(ge=1)
    document_id: Optional[int] = Field(default=None, ge=1, description="留空时校核当前案件全部文稿")
    include_layout: bool = True


class ChecklistItemUpdate(BaseModel):
    expected_version: int = Field(ge=1)
    status: CheckItemStatus
    note: str = Field(default="", max_length=500)
    resolved_by: str = Field(default="复核人", max_length=80)

    @model_validator(mode="after")
    def require_reason(self):
        if self.status == "waived" and not self.note.strip():
            raise ValueError("豁免校核项必须填写理由")
        return self


# --------------------------------------------------------------------------
# 修正与交付
# --------------------------------------------------------------------------


class CorrectionCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    expected_version: int = Field(ge=1)
    kind: CorrectionKind
    target_type: CorrectionTargetType
    target_id: int = Field(ge=0)
    before_text: str = Field(default="", max_length=4000)
    after_text: str = Field(default="", max_length=4000)
    reason: str = Field(min_length=1, max_length=500)
    confirmed_by: str = Field(default="复核人", max_length=80)
    scope: CorrectionScope = "case_only"
    scope_value: str = Field(default="", max_length=120)
    apply_to_case: bool = Field(default=True, description="是否把修正应用到当前案件并失效旧复核状态")

    @model_validator(mode="after")
    def require_change(self):
        if self.before_text == self.after_text:
            raise ValueError("修正前后的内容不能相同")
        if self.kind in ("recognition", "terminology", "format") and not self.after_text.strip():
            raise ValueError("该修正类型必须给出修正后的内容")
        if self.scope != "case_only" and not self.scope_value.strip():
            raise ValueError("非 case_only 范围必须填写适用范围")
        return self


class DeliverRequest(BaseModel):
    expected_version: int = Field(ge=1)
    document_id: Optional[int] = Field(default=None, ge=1)
    formats: List[OutputFormat] = Field(default_factory=lambda: ["docx", "pdf"], min_length=1, max_length=2)
    note: str = Field(default="", max_length=300)


class AiVerifyRequest(BaseModel):
    base_url: str = Field(default="", max_length=200)
    model: str = Field(default="", max_length=80)


class IdListRequest(BaseModel):
    ids: List[int] = Field(min_length=1, max_length=200)


__all__ = [
    "CaseIntake",
    "CaseCreate",
    "CaseUpdate",
    "CaseStageUpdate",
    "MaterialCreate",
    "MaterialPageUpdate",
    "PhotoPlacementUpdate",
    "PreprocessRequest",
    "OcrRunRequest",
    "ExtractRequest",
    "FieldUpdate",
    "InjuryCreate",
    "InjuryUpdate",
    "NormalizeRequest",
    "TerminologyRuleCreate",
    "TerminologyRuleUpdate",
    "DocumentCreate",
    "CheckRequest",
    "ChecklistItemUpdate",
    "CorrectionCreate",
    "DeliverRequest",
    "AiVerifyRequest",
    "IdListRequest",
    "CaseStage",
    "FieldStatus",
    "ReviewState",
    "MaterialKind",
    "DocStatus",
    "CheckItemStatus",
    "CorrectionKind",
    "CorrectionTargetType",
    "CorrectionScope",
    "OutputFormat",
]
