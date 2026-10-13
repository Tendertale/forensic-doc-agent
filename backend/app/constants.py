"""领域常量与字段目录。

本模块是整个后端与前端共享的**冻结契约**：枚举取值、字段目录、阶段顺序、
状态流转和种子术语规则都只在这里定义一次，其他模块只允许引用，不允许各自
重新声明一份副本。

对应需求文档：第三章（结构化数据要求）、第五章（执行顺序）、第六章（简写规则）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

# --------------------------------------------------------------------------
# 一、枚举字典
# --------------------------------------------------------------------------

#: 信息状态。需求 3.4：这些状态不能相互替代，尤其不能把空白栏目自动写成“未见异常”。
FIELD_STATUSES: Tuple[str, ...] = (
    "recorded",       # 明确记载：原文存在且含义明确
    "not_recorded",   # 未记录：材料没有记载
    "not_examined",   # 明确未检：原文说明未进行该项检查
    "negative",       # 明确阴性所见：原文说明“未见……”
    "unclear",        # 待辨认或有冲突：字迹不清、含义不明或材料不一致
)

FIELD_STATUS_LABELS: Dict[str, str] = {
    "recorded": "明确记载",
    "not_recorded": "未记录",
    "not_examined": "明确未检",
    "negative": "明确阴性所见",
    "unclear": "待辨认或有冲突",
}

#: 只有这些状态才算“已定论”，可以进入待签发稿。
RESOLVED_FIELD_STATUSES: Tuple[str, ...] = (
    "recorded",
    "not_recorded",
    "not_examined",
    "negative",
)

#: 复核状态。
REVIEW_STATES: Tuple[str, ...] = ("pending", "confirmed")
REVIEW_STATE_LABELS: Dict[str, str] = {"pending": "待核对", "confirmed": "已确认"}

#: 案件阶段，顺序即需求 5.1—5.10 的执行顺序。
CASE_STAGES: Tuple[str, ...] = (
    "intake",               # 1 接收材料
    "image_check",          # 2 检查图像
    "ocr",                  # 3 识别原文
    "extract",              # 4 提取字段
    "verify_facts",         # 5 核对关键事实
    "normalize",            # 6 整理术语
    "render",               # 7 填充模板
    "check_document",       # 8 校核文档
    "professional_review",  # 9 专业复核
    "delivered",            # 10 保存交付
)

CASE_STAGE_LABELS: Dict[str, str] = {
    "intake": "接收材料",
    "image_check": "检查图像",
    "ocr": "识别原文",
    "extract": "提取字段",
    "verify_facts": "核对关键事实",
    "normalize": "整理术语",
    "render": "填充模板",
    "check_document": "校核文档",
    "professional_review": "专业复核",
    "delivered": "保存交付",
}

#: 阶段只能前进一格或回退一格，回退用于用户修正后重新处理。
def _stage_transitions() -> Dict[str, Tuple[str, ...]]:
    transitions: Dict[str, Tuple[str, ...]] = {}
    for index, stage in enumerate(CASE_STAGES):
        allowed: List[str] = []
        if index + 1 < len(CASE_STAGES):
            allowed.append(CASE_STAGES[index + 1])
        if index > 0:
            allowed.append(CASE_STAGES[index - 1])
        transitions[stage] = tuple(allowed)
    return transitions


STAGE_TRANSITIONS: Dict[str, Tuple[str, ...]] = _stage_transitions()

#: 文稿状态。内部草稿可以带疑点；待签发稿必须没有阻断项。
DOC_STATUSES: Tuple[str, ...] = ("internal_draft", "pending_issue", "approved", "superseded")
DOC_STATUS_LABELS: Dict[str, str] = {
    "internal_draft": "内部草稿",
    "pending_issue": "待签发稿",
    "approved": "已审核",
    "superseded": "已被替代",
}

#: 材料类型。
MATERIAL_KINDS: Tuple[str, ...] = (
    "commission",              # 委托书扫描件
    "record_book",             # 记录本扫描件
    "photo",                   # 部位照片（只按用户指定位置插入，不识别内容：N-11）
    "template",                # 报告模板
    "approved_report",         # 已审核报告（仅用于学习格式）
    "terminology_reference",   # 简写对照表
    "supplement",              # 本次整理要求等补充资料
)

MATERIAL_KIND_LABELS: Dict[str, str] = {
    "commission": "委托书",
    "record_book": "记录本",
    "photo": "部位照片",
    "template": "报告模板",
    "approved_report": "已审核报告",
    "terminology_reference": "简写对照表",
    "supplement": "补充资料",
}

#: 必需材料类型。缺失时进入待核对清单。
REQUIRED_MATERIAL_KINDS: Tuple[str, ...] = ("commission", "record_book", "template")

MATERIAL_MEDIA_TYPES: Tuple[str, ...] = ("pdf", "jpeg", "png", "docx", "json", "other")

#: 允许进入文字识别流程的材料类型。``photo``（部位照片）**永远不在**此列：
#: 系统不得对照片内容做任何识别、描述或推断（N-11）。
OCR_MATERIAL_KINDS: Tuple[str, ...] = ("commission", "record_book", "supplement")

PHOTO_MATERIAL_KIND = "photo"


#: 图像质量标记：模糊、裁切、方向、遮挡、涂改。
IMAGE_QUALITY_FLAGS: Tuple[str, ...] = ("blurred", "cropped", "rotated", "occluded", "altered")
IMAGE_QUALITY_LABELS: Dict[str, str] = {
    "blurred": "模糊",
    "cropped": "裁切",
    "rotated": "方向异常",
    "occluded": "遮挡",
    "altered": "涂改",
}

#: 图像预处理操作。原件不改变，处理结果单独保存。
IMAGE_OPERATIONS: Tuple[str, ...] = ("rotate", "deskew", "crop", "contrast", "grayscale", "autocontrast")

# --------------------------------------------------------------------------
# 一之二、部位照片的目标位置（N-11：只按用户指定位置插入，不识别照片内容）
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PhotoPosition:
    """报告模板中的一个照片栏位。

    用户上传部位照片时**必须**为每张照片选择一个 ``key``；系统只按该 key
    把照片放进对应栏位，绝不识别、描述或推断照片内容（N-11）。
    """

    key: str
    label: str
    section: str
    order: int
    anchor: str          # Word 模板中的锚点，形如 ``{{ photo_<key> }}``
    wide: bool = False   # 宽幅栏位（全身照等）使用更大的最大宽度


def _p(key: str, label: str, section: str, order: int, wide: bool = False) -> PhotoPosition:
    return PhotoPosition(key=key, label=label, section=section, order=order,
                         anchor=f"photo_{key}", wide=wide)


#: 部位照片目标位置目录。顺序即模板中照片栏的排列顺序。
PHOTO_POSITION_DEFINITIONS: Tuple[PhotoPosition, ...] = (
    _p("body_overall", "全身", "full", 10, wide=True),
    _p("head", "头部", "head", 20),
    _p("face", "面部", "head", 30),
    _p("neck", "颈部", "head", 40),
    _p("chest", "胸部", "body", 50),
    _p("abdomen", "腹部", "body", 60),
    _p("back", "背部", "body", 70),
    _p("waist_hip", "腰臀部", "body", 80),
    _p("perineum", "会阴部", "body", 90),
    _p("arm_left", "左上肢", "limb", 100),
    _p("arm_right", "右上肢", "limb", 110),
    _p("hand_left", "左手", "limb", 120),
    _p("hand_right", "右手", "limb", 130),
    _p("leg_left", "左下肢", "limb", 140),
    _p("leg_right", "右下肢", "limb", 150),
    _p("foot_left", "左足", "limb", 160),
    _p("foot_right", "右足", "limb", 170),
    _p("other", "其他部位", "other", 900),
)

PHOTO_POSITION_INDEX: Dict[str, PhotoPosition] = {
    item.key: item for item in PHOTO_POSITION_DEFINITIONS
}

PHOTO_POSITION_KEYS: Tuple[str, ...] = tuple(item.key for item in PHOTO_POSITION_DEFINITIONS)

PHOTO_POSITION_LABELS: Dict[str, str] = {
    item.key: item.label for item in PHOTO_POSITION_DEFINITIONS
}

PHOTO_POSITION_ANCHORS: Dict[str, str] = {
    item.key: item.anchor for item in PHOTO_POSITION_DEFINITIONS
}

#: 照片分组与显示顺序。
PHOTO_SECTIONS: Tuple[str, ...] = ("full", "head", "body", "limb", "other")
PHOTO_SECTION_LABELS: Dict[str, str] = {
    "full": "全身",
    "head": "头颈部",
    "body": "躯干",
    "limb": "四肢",
    "other": "其他",
}

#: 单张照片最大宽度（厘米）；宽幅栏位使用更大的上限。只按栏位调整宽度，不裁切内容。
PHOTO_MAX_WIDTH_CM = 7.0
PHOTO_WIDE_MAX_WIDTH_CM = 11.0

#: 同一栏位最多插入的照片数（多张时按 sort_order 依次排列）。
PHOTO_MAX_PER_POSITION = 8

#: 栏位没有照片时写入文档的提示文字。**不得**写成“未见异常”，也不得用别的栏位顶替。
PHOTO_PLACEHOLDER_TEXT = "（未提供照片）"


#: 文字识别引擎。
OCR_ENGINES: Tuple[str, ...] = ("sidecar", "paddle", "none")
DEFAULT_OCR_ENGINE = "sidecar"

#: 校核类别。
CHECK_CATEGORIES: Tuple[str, ...] = ("recognition", "content", "source", "format", "correction")
CHECK_CATEGORY_LABELS: Dict[str, str] = {
    "recognition": "识别质量",
    "content": "内容质量",
    "source": "来源完整性",
    "format": "格式质量",
    "correction": "修改可靠性",
}

#: 校核项严重级别。blocking 会阻止生成待签发稿。
CHECK_SEVERITIES: Tuple[str, ...] = ("blocking", "warning", "info")
CHECK_SEVERITY_LABELS: Dict[str, str] = {"blocking": "阻断", "warning": "警告", "info": "提示"}

CHECK_ITEM_STATUSES: Tuple[str, ...] = ("open", "resolved", "waived")
CHECK_ITEM_STATUS_LABELS: Dict[str, str] = {"open": "待处理", "resolved": "已解决", "waived": "已豁免"}

#: 用户修正类型（需求第七章）。
CORRECTION_KINDS: Tuple[str, ...] = ("recognition", "terminology", "format", "workflow_order")
CORRECTION_KIND_LABELS: Dict[str, str] = {
    "recognition": "识别纠正",
    "terminology": "术语纠正",
    "format": "格式纠正",
    "workflow_order": "工作顺序调整",
}

CORRECTION_TARGET_TYPES: Tuple[str, ...] = ("field", "injury", "terminology_rule", "document", "case")

#: 修正作用范围：默认只影响当前案件，明确记录后才用于以后案件。
CORRECTION_SCOPES: Tuple[str, ...] = ("case_only", "field_key", "column", "writer", "template")
CORRECTION_SCOPE_LABELS: Dict[str, str] = {
    "case_only": "仅当前案件",
    "field_key": "适用栏目",
    "column": "适用记录栏目",
    "writer": "适用书写者",
    "template": "适用模板",
}

OUTPUT_FORMATS: Tuple[str, ...] = ("docx", "pdf")

# --------------------------------------------------------------------------
# 二、字段目录
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldDefinition:
    """一个可提取/可填充的事实字段。"""

    key: str
    group: str
    label: str
    order: int
    required: bool = False
    critical: bool = False
    unit_hint: str = ""
    template_anchor: str = ""
    description: str = ""
    repeatable: bool = False


def _f(key: str, group: str, label: str, order: int, **kwargs: Any) -> FieldDefinition:
    return FieldDefinition(key=key, group=group, label=label, order=order, **kwargs)


#: 字段目录。key 全局唯一；template_anchor 是 Word 模板中的占位符名称。
FIELD_DEFINITIONS: Tuple[FieldDefinition, ...] = (
    # 案件与委托
    _f("case_no", "case_commission", "案件编号", 10, required=True, critical=True,
       template_anchor="case_no", description="本机构案件编号，命名与追溯的依据"),
    _f("commission_unit", "case_commission", "委托单位", 20, required=True,
       template_anchor="commission_unit"),
    _f("commission_matter", "case_commission", "委托事项", 30, required=True, critical=True,
       template_anchor="commission_matter", description="不得被自动改写成鉴定意见"),
    _f("commission_date", "case_commission", "委托日期", 40, required=True,
       template_anchor="commission_date", description="与受理日期、检验日期分别处理"),
    _f("case_summary", "case_commission", "案情简介", 50,
       template_anchor="case_summary", description="保留委托书来源属性，不作为检验人员直接观察所见"),
    _f("material_list", "case_commission", "材料清单", 60, template_anchor="material_list"),
    # 被检验对象
    _f("subject_name", "subject", "姓名", 110, required=True, critical=True, template_anchor="subject_name"),
    _f("subject_gender", "subject", "性别", 120, critical=True, template_anchor="subject_gender"),
    _f("subject_age", "subject", "年龄", 130, template_anchor="subject_age"),
    _f("subject_identity", "subject", "身份信息", 140, critical=True, template_anchor="subject_identity",
       description="身份证件号或其他材料实际记载的身份内容"),
    # 检验基本信息
    _f("exam_time", "examination", "检验时间", 210, required=True, critical=True, template_anchor="exam_time"),
    _f("exam_place", "examination", "检验地点", 220, template_anchor="exam_place"),
    _f("examiners", "examination", "检验人员", 230, template_anchor="examiners"),
    _f("exam_method", "examination", "检验方法", 240, template_anchor="exam_method"),
    # 尸表一般所见
    _f("body_length", "general_findings", "尸长", 310, required=True, critical=True, unit_hint="cm",
       template_anchor="body_length", description="保留原数值及测量精度，不把 3 写成 3.0"),
    _f("body_weight", "general_findings", "体重", 320, unit_hint="kg", template_anchor="body_weight"),
    _f("clothing", "general_findings", "衣着", 330, template_anchor="clothing"),
    _f("livor_mortis", "general_findings", "尸斑", 340, template_anchor="livor_mortis",
       description="不推导死亡时间"),
    _f("rigor_mortis", "general_findings", "尸僵", 350, template_anchor="rigor_mortis"),
    _f("body_temperature", "general_findings", "尸温", 360, unit_hint="℃", template_anchor="body_temperature"),
    _f("cornea", "general_findings", "角膜", 370, template_anchor="cornea"),
    _f("general_other", "general_findings", "其他一般所见", 380, template_anchor="general_other"),
    # 文书配置
    _f("template_version", "document_config", "模板版本", 410, required=True, template_anchor="template_version"),
    _f("section_order", "document_config", "栏目顺序", 420, template_anchor="section_order"),
    _f("term_rule_version", "document_config", "术语规则版本", 430, template_anchor="term_rule_version"),
    _f("doc_version", "document_config", "文稿版本", 440, template_anchor="doc_version"),
)

FIELD_INDEX: Dict[str, FieldDefinition] = {item.key: item for item in FIELD_DEFINITIONS}

FIELD_GROUP_LABELS: Dict[str, str] = {
    "case_commission": "案件与委托",
    "subject": "被检验对象",
    "examination": "检验基本信息",
    "general_findings": "尸表一般所见",
    "injuries": "部位与损伤",
    "document_config": "文书配置",
}

FIELD_GROUP_ORDER: Tuple[str, ...] = (
    "case_commission",
    "subject",
    "examination",
    "general_findings",
    "injuries",
    "document_config",
)

#: 必填字段。
REQUIRED_FIELD_KEYS: Tuple[str, ...] = tuple(item.key for item in FIELD_DEFINITIONS if item.required)

#: 关键事实：身份、关键检验所见。需求 5.5 要求逐项人工核对。
CRITICAL_FIELD_KEYS: Tuple[str, ...] = tuple(item.key for item in FIELD_DEFINITIONS if item.critical)


# --------------------------------------------------------------------------
# 三、损伤条目字段
# --------------------------------------------------------------------------

INJURY_FIELDS: Tuple[str, ...] = (
    "body_part",    # 部位
    "side",         # 左右侧
    "injury_type",  # 类型
    "count",        # 数量
    "length",       # 尺寸长
    "width",        # 尺寸宽
    "size_unit",    # 尺寸单位
    "morphology",   # 形态
    "other_traits",  # 其他已记录特征
)

INJURY_FIELD_LABELS: Dict[str, str] = {
    "body_part": "部位",
    "side": "左右侧",
    "injury_type": "类型",
    "count": "数量",
    "length": "长度",
    "width": "宽度",
    "size_unit": "单位",
    "morphology": "形态",
    "other_traits": "其他特征",
}

#: 左右侧取值。规范化时不允许改变方向。
SIDE_VALUES: Tuple[str, ...] = ("左", "右", "双侧", "正中", "")

#: 否定/限定词。规范化时不允许漏掉。
NEGATION_WORDS: Tuple[str, ...] = ("不", "无", "未", "未见", "可疑", "否认")

# --------------------------------------------------------------------------
# 四、识别与来源
# --------------------------------------------------------------------------

DEFAULT_OCR_CONFIDENCE_THRESHOLD = 0.75

#: 低于该分数进入“待辨认”状态并安排优先核对。OCR 分数只用于排序，不作为结论。
OCR_LOW_CONFIDENCE = 0.55

#: 字段状态推断用的关键词。
STATUS_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "not_examined": ("未检", "未作检查", "未进行", "放弃检查", "拒绝检查", "不予检验"),
    "negative": ("未见", "未发现", "无异常", "未见异常", "未触及", "未扪及", "阴性"),
    "not_recorded": ("未记录", "空白", "无记录", "/", "—", "-"),
}

# --------------------------------------------------------------------------
# 五、种子术语规则（需求第六章示例）
# --------------------------------------------------------------------------


def _rule(**kwargs: Any) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "code": "",
        "shorthand": "",
        "pattern": "",
        "match_type": "literal",
        "template": "",
        "scope_field_key": "",
        "scope_column": "",
        "scope_writer": "",
        "boundary_note": "",
        "require_groups": (),
        "missing_group_note": "",
        "requires_confirmation": False,
        "priority": 100,
        "active": True,
    }
    base.update(kwargs)
    return base


TERMINOLOGY_SEED: Tuple[Dict[str, Any], ...] = (
    _rule(
        code="livor_dorsal_purple",
        shorthand="背侧，紫红，指压褪色",
        pattern="背侧，紫红，指压褪色",
        match_type="literal",
        template="尸斑位于背侧，呈紫红色，指压褪色。",
        scope_field_key="livor_mortis",
        scope_column="尸斑",
        boundary_note="不推导死亡时间，不补充未见记载的分布范围。",
        priority=10,
    ),
    _rule(
        code="livor_site",
        shorthand="<部位>，<颜色>，指压<褪色与否>",
        pattern=r"(?P<site>背侧|胸腹部|腰背部|四肢|颈项部)?，?\s*(?P<color>紫红|暗红|淡红|樱红)",
        match_type="regex",
        template="尸斑位于{site}，呈{color}色。",
        scope_field_key="livor_mortis",
        scope_column="尸斑",
        boundary_note="不推导死亡时间。",
        priority=20,
    ),
    _rule(
        code="injury_measure",
        shorthand="<侧别><部位><类型><长>×<宽>cm",
        pattern=r"^(?P<side>左|右|双侧)?(?P<part>[\u4e00-\u9fa5]{2,8}?)(?P<type>擦伤|挫伤|挫裂创|裂创|创口|淤血|瘀斑|瘀血|表皮剥脱|划伤|咬痕|压痕|骨折)?\s*(?P<length>\d+(?:\.\d+)?)\s*[×xX*]\s*(?P<width>\d+(?:\.\d+)?)\s*(?P<unit>cm|CM|厘米|mm|MM|毫米)?$",
        match_type="regex",
        template="{side}{part}见{type}，范围为{length} {unit} × {width} {unit}。",
        scope_field_key="injuries",
        scope_column="损伤",
        boundary_note="不补充颜色、深度、形态或数量；单位缺失时提示确认。",
        require_groups=("type", "unit"),
        missing_group_note="损伤类型或单位未记录，需确认后再展开。",
        requires_confirmation=True,
        priority=30,
    ),
    _rule(
        code="body_length_unit",
        shorthand="尸长<数值>cm",
        pattern=r"^尸长\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>cm|CM|厘米)?$",
        match_type="regex",
        template="尸长{value} cm。",
        scope_field_key="body_length",
        scope_column="尸长",
        boundary_note="保留原数值及测量精度，只有表头或已确认记录规则明确单位时才可补入单位。",
        require_groups=("unit",),
        missing_group_note="尸长未注明单位，保留数值并提示确认单位。",
        requires_confirmation=False,
        priority=40,
    ),
    _rule(
        code="rigor_mortis_full",
        shorthand="尸僵<分布>",
        pattern=r"^(?:尸僵)?(?P<distribution>全身|颈部|下颌|上肢|下肢|大关节|全身大关节)[\u4e00-\u9fa5]*$",
        match_type="regex",
        template="尸僵{raw}",
        scope_field_key="rigor_mortis",
        scope_column="尸僵",
        boundary_note="保留原分布描述，不推导死亡时间。",
        priority=50,
    ),
    _rule(
        code="pressure_fading_ambiguous",
        shorthand="压退",
        pattern="压退",
        match_type="literal",
        template="",
        scope_field_key="",
        scope_column="",
        boundary_note="不直接展开为完整尸斑描述，需确认其指代与完整含义。",
        requires_confirmation=True,
        priority=5,
    ),
)

TERM_RULE_DEFAULT_VERSION = "term-rules-v1"

# --------------------------------------------------------------------------
# 六、模板与渲染
# --------------------------------------------------------------------------

DEFAULT_TEMPLATE_VERSION = "report-template-v1"

#: 呈现给专业人员的文书栏目顺序（可在文书配置中调整）。
DEFAULT_SECTION_ORDER: Tuple[str, ...] = (
    "title",
    "case_commission",
    "subject",
    "examination",
    "general_findings",
    "injuries",
    "photos",
    "opinion",
    "signature",
)

SECTION_ORDER_LABELS: Dict[str, str] = {
    "title": "标题",
    "case_commission": "案件与委托",
    "subject": "被检验对象",
    "examination": "检验基本信息",
    "general_findings": "尸表一般所见",
    "injuries": "部位与损伤",
    "photos": "部位照片",
    "opinion": "鉴定意见（由专业人员提供）",
    "signature": "签名与盖章区域",
}

#: 必须由鉴定人提供、系统不得自行生成的栏目。
PROFESSIONAL_ONLY_SECTIONS: Tuple[str, ...] = ("opinion",)

#: 尸体检验技术参考标准（R-49）。**只作参考提示**：具体采用的标准与版本必须由业务方确认后
#: 写入规则配置；未确认前不得据标准推导任何事实、所见或鉴定意见（N-1/N-2）。
REFERENCE_STANDARDS: Tuple[Dict[str, str], ...] = (
    {
        "code": "GA/T 147-2019",
        "title": "法医学 尸体检验技术总则",
        "status": "candidate",
        "note": "官方条目列明其代替 GA/T 147—1996、GA/T 149—1996；是否采用由业务方确认后写入规则配置。",
    },
    {
        "code": "GA/T 147-1996",
        "title": "法医学 尸体检验技术总则",
        "status": "superseded",
        "note": "已被 GA/T 147—2019 代替，仅供历史比对。",
    },
    {
        "code": "GA/T 149-1996",
        "title": "尸体检验技术总则相关标准",
        "status": "superseded",
        "note": "已被 GA/T 147—2019 代替，仅供历史比对。",
    },
)

#: 标准配置是否已被业务方确认。未确认时 `/api/config` 必须如实报告，且不得据标准生成内容。
STANDARD_CONFIG_CONFIRMED = False


#: 排版校核要检查的占位符残留特征。
PLACEHOLDER_PATTERNS: Tuple[str, ...] = ("{{", "}}", "{%", "%}", "TODO", "待填", "XXXX")

#: 草稿待核对标记。进入待签发稿前必须处理。
DRAFT_MARKER = "【待核对】"

# --------------------------------------------------------------------------
# 七、统计口径
# --------------------------------------------------------------------------

STAGE_PROGRESS_WEIGHTS: Dict[str, float] = {
    stage: round((index + 1) / len(CASE_STAGES), 4) for index, stage in enumerate(CASE_STAGES)
}

SEED_CASE_COUNT = 2

__all__ = [
    "FIELD_STATUSES",
    "FIELD_STATUS_LABELS",
    "RESOLVED_FIELD_STATUSES",
    "REVIEW_STATES",
    "REVIEW_STATE_LABELS",
    "CASE_STAGES",
    "CASE_STAGE_LABELS",
    "STAGE_TRANSITIONS",
    "DOC_STATUSES",
    "DOC_STATUS_LABELS",
    "MATERIAL_KINDS",
    "MATERIAL_KIND_LABELS",
    "REQUIRED_MATERIAL_KINDS",
    "MATERIAL_MEDIA_TYPES",
    "OCR_MATERIAL_KINDS",
    "PHOTO_MATERIAL_KIND",
    "PhotoPosition",
    "PHOTO_POSITION_DEFINITIONS",
    "PHOTO_POSITION_INDEX",
    "PHOTO_POSITION_KEYS",
    "PHOTO_POSITION_LABELS",
    "PHOTO_POSITION_ANCHORS",
    "PHOTO_SECTIONS",
    "PHOTO_SECTION_LABELS",
    "PHOTO_MAX_WIDTH_CM",
    "PHOTO_WIDE_MAX_WIDTH_CM",
    "PHOTO_MAX_PER_POSITION",
    "PHOTO_PLACEHOLDER_TEXT",
    "IMAGE_QUALITY_FLAGS",
    "IMAGE_QUALITY_LABELS",
    "IMAGE_OPERATIONS",
    "OCR_ENGINES",
    "DEFAULT_OCR_ENGINE",
    "CHECK_CATEGORIES",
    "CHECK_CATEGORY_LABELS",
    "CHECK_SEVERITIES",
    "CHECK_SEVERITY_LABELS",
    "CHECK_ITEM_STATUSES",
    "CHECK_ITEM_STATUS_LABELS",
    "CORRECTION_KINDS",
    "CORRECTION_KIND_LABELS",
    "CORRECTION_TARGET_TYPES",
    "CORRECTION_SCOPES",
    "CORRECTION_SCOPE_LABELS",
    "OUTPUT_FORMATS",
    "FieldDefinition",
    "FIELD_DEFINITIONS",
    "FIELD_INDEX",
    "FIELD_GROUP_LABELS",
    "FIELD_GROUP_ORDER",
    "REQUIRED_FIELD_KEYS",
    "CRITICAL_FIELD_KEYS",
    "INJURY_FIELDS",
    "INJURY_FIELD_LABELS",
    "SIDE_VALUES",
    "NEGATION_WORDS",
    "DEFAULT_OCR_CONFIDENCE_THRESHOLD",
    "OCR_LOW_CONFIDENCE",
    "STATUS_KEYWORDS",
    "TERMINOLOGY_SEED",
    "TERM_RULE_DEFAULT_VERSION",
    "DEFAULT_TEMPLATE_VERSION",
    "DEFAULT_SECTION_ORDER",
    "SECTION_ORDER_LABELS",
    "PROFESSIONAL_ONLY_SECTIONS",
    "REFERENCE_STANDARDS",
    "STANDARD_CONFIG_CONFIRMED",
    "PLACEHOLDER_PATTERNS",
    "DRAFT_MARKER",
    "STAGE_PROGRESS_WEIGHTS",
    "SEED_CASE_COUNT",
]
