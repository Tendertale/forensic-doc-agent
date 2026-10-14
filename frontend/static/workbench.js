/**
 * 法医尸表检验文书整理 Agent · 无构建原生工作台
 * 文件：frontend/static/workbench.js（W4b 独占）
 *
 * 设计要点（docs/architecture.md 第 7.3 节）：
 * - 纯原生 ES 模块 + fetch，无打包器、无 CDN、无外链字体或图标；图标为内联 SVG 或 Unicode。
 * - 主视图（默认首屏）「整理」：拖拽/选择文件 → 选输出格式 → 一次 POST /api/quick-run
 *   → 自动切到「结果」下载 Word/PDF；普通用户不需要看到四步流程的细节。
 * - 次级界面「案件记录」保留全部既有功能，覆盖四步主流程（R-1）：
 *   选择材料 → 核对疑点 → 查看文档 → 确认修改。
 * - 界面文案全中文；空白字段一律显示“未记录”，绝不显示“未见异常”（N-5）。
 * - `unclear` 字段带醒目“待辨认/冲突”标记，并同时展示冲突双方的来源与取值（R-44）。
 * - 鉴定意见（opinion）标注“由专业人员提供”，只允许手工输入（N-1）。
 * - 部位照片必须由用户自己选择目标位置，界面显著标注“系统不读取照片内容”（N-11/R-58）；
 *   本文件不包含任何图像识别、像素读取或视觉模型调用，缩略图只交给浏览器显示。
 * - 每个写操作都带 `expected_version`（POST/PATCH 在 body；DELETE 在查询参数）。
 * - 模型密钥只保存在内存变量 `state.apiKey`，只通过 `X-DeepSeek-API-Key` 请求头发送，
 *   不写入 localStorage / sessionStorage / URL / DOM 属性。
 */

import { api } from './api.js'

/* ==========================================================================
 * 0. 端点覆盖清单（静态自检与人工核对用；与 frontend/src/api.js 逐条对应）
 *    写操作的 expected_version 归属：/api/cases/** 用案件版本，
 *    /api/terminology-rules/** 用规则版本，/api/documents/{id}/approve 与
 *    /api/checklist/{item_id} 用该资源所属案件的版本。
 * ========================================================================== */
const ENDPOINT_MANIFEST = [
  ['GET', '/api/health'],                                                    // 健康检查
  ['GET', '/api/config'],                                                    // 字段目录/枚举/引擎/照片位置
  ['GET', '/api/stats'],                                                     // 统计卡
  ['POST', '/api/ai/verify'],                                                // 模型连通性（密钥仅请求头）
  ['GET', '/api/cases'],                                                      // 分页/搜索/筛选
  ['POST', '/api/cases'],                                                     // 建案（含 intake）
  ['GET', '/api/cases/{case_id}'],                                            // 案件详情
  ['PATCH', '/api/cases/{case_id}'],                                          // 案件基础信息 + opinion_text
  ['PATCH', '/api/cases/{case_id}/stage'],                                    // 推进/回退阶段
  ['DELETE', '/api/cases/{case_id}?expected_version=N'],                      // 删除案件
  ['POST', '/api/cases/{case_id}/materials'],                                 // 登记材料/上传
  ['GET', '/api/cases/{case_id}/materials'],                                  // 材料清单
  ['GET', '/api/cases/{case_id}/materials/{material_id}/file?page=N&variant=original'], // 原件预览
  ['PATCH', '/api/cases/{case_id}/materials/{material_id}/placement'],        // 照片目标位置（N-11）
  ['GET', '/api/cases/{case_id}/photo-slots'],                                // 照片栏位视图
  ['PATCH', '/api/cases/{case_id}/materials/{material_id}/pages/{page_number}'], // 页质量标记
  ['POST', '/api/cases/{case_id}/materials/{material_id}/preprocess'],        // 生成识别用副本
  ['POST', '/api/cases/{case_id}/ocr'],                                       // 运行识别
  ['GET', '/api/cases/{case_id}/ocr-blocks'],                                 // 识别结果分页
  ['POST', '/api/cases/{case_id}/extract'],                                   // 字段与损伤提取
  ['GET', '/api/cases/{case_id}/fields'],                                     // 字段列表
  ['PATCH', '/api/cases/{case_id}/fields/{field_key}'],                       // 核对/确认字段
  ['GET', '/api/cases/{case_id}/injuries'],                                   // 损伤列表
  ['POST', '/api/cases/{case_id}/injuries'],                                  // 新增损伤
  ['PATCH', '/api/cases/{case_id}/injuries/{injury_id}'],                     // 修改损伤
  ['DELETE', '/api/cases/{case_id}/injuries/{injury_id}?expected_version=N'],  // 删除损伤
  ['POST', '/api/cases/{case_id}/injuries/reorder?expected_version=N'],        // 调整顺序（IdListRequest）
  ['POST', '/api/cases/{case_id}/normalize'],                                 // 术语整理
  ['GET', '/api/terminology-rules'],                                          // 规则列表
  ['POST', '/api/terminology-rules'],                                         // 新增规则
  ['PATCH', '/api/terminology-rules/{rule_id}'],                              // 修改规则（规则版本）
  ['DELETE', '/api/terminology-rules/{rule_id}?expected_version=N'],           // 删除规则（规则版本）
  ['POST', '/api/cases/{case_id}/documents'],                                 // 生成文稿
  ['GET', '/api/cases/{case_id}/documents'],                                  // 文稿版本列表
  ['GET', '/api/documents/{document_id}/download?fmt=docx|pdf'],              // 下载（downloadUrl）
  ['POST', '/api/documents/{document_id}/approve'],                           // 标记已审核
  ['POST', '/api/cases/{case_id}/check'],                                     // 运行校核
  ['GET', '/api/cases/{case_id}/checklist'],                                  // 校核清单
  ['PATCH', '/api/checklist/{item_id}'],                                      // 处理/豁免校核项
  ['POST', '/api/cases/{case_id}/corrections'],                               // 四类修正录入
  ['GET', '/api/cases/{case_id}/corrections'],                                // 修正记录
  ['POST', '/api/cases/{case_id}/deliver'],                                  // 保存交付
  ['POST', '/api/quick-run'],                                                 // 主视图：一次请求完成识别/归类/生成
]

/* ==========================================================================
 * 1. 契约常量（镜像 backend/app/constants.py）
 *    仅作为 GET /api/config 缺项或字段名不同时的**降级显示**，取值必须与常量表一致。
 * ========================================================================== */

const NOT_RECORDED = '未记录'
const OCR_LOW_CONFIDENCE_FALLBACK = 0.55

const STAGE_ORDER = ['intake', 'image_check', 'ocr', 'extract', 'verify_facts', 'normalize',
  'render', 'check_document', 'professional_review', 'delivered']
const STAGE_LABELS = {
  intake: '接收材料', image_check: '检查图像', ocr: '识别原文', extract: '提取字段',
  verify_facts: '核对关键事实', normalize: '整理术语', render: '填充模板',
  check_document: '校核文档', professional_review: '专业复核', delivered: '保存交付',
}
const FIELD_STATUS_ORDER = ['recorded', 'not_recorded', 'not_examined', 'negative', 'unclear']
const FIELD_STATUS_LABELS = {
  recorded: '明确记载', not_recorded: '未记录', not_examined: '明确未检',
  negative: '明确阴性所见', unclear: '待辨认或有冲突',
}
const REVIEW_STATE_LABELS = { pending: '待核对', confirmed: '已确认' }
const MATERIAL_KIND_ORDER = ['commission', 'record_book', 'photo', 'template', 'approved_report',
  'terminology_reference', 'supplement']
const MATERIAL_KIND_LABELS = {
  commission: '委托书', record_book: '记录本', photo: '部位照片', template: '报告模板',
  approved_report: '已审核报告', terminology_reference: '简写对照表', supplement: '补充资料',
}
const MATERIAL_MEDIA_TYPES = ['pdf', 'jpeg', 'png', 'docx', 'json', 'other']
const DOC_STATUS_LABELS = {
  internal_draft: '内部草稿', pending_issue: '待签发稿', approved: '已审核', superseded: '已被替代',
}
const IMAGE_QUALITY_LABELS = {
  blurred: '模糊', cropped: '裁切', rotated: '方向异常', occluded: '遮挡', altered: '涂改',
}
const IMAGE_OPERATIONS = [
  ['rotate', '旋转'], ['deskew', '纠偏'], ['crop', '裁切'],
  ['contrast', '对比度'], ['grayscale', '灰度'], ['autocontrast', '自动对比度'],
]
const CHECK_CATEGORY_LABELS = {
  recognition: '识别质量', content: '内容质量', source: '来源完整性',
  format: '格式质量', correction: '修改可靠性',
}
const CHECK_CATEGORY_ORDER = ['recognition', 'content', 'source', 'format', 'correction']
const CHECK_SEVERITY_ORDER = ['blocking', 'warning', 'info']
const CHECK_SEVERITY_LABELS = { blocking: '阻断', warning: '警告', info: '提示' }
const CHECK_ITEM_STATUS_LABELS = { open: '待处理', resolved: '已解决', waived: '已豁免' }
const CORRECTION_KIND_LABELS = {
  recognition: '识别纠正', terminology: '术语纠正', format: '格式纠正', workflow_order: '工作顺序调整',
}
const CORRECTION_SCOPE_LABELS = {
  case_only: '仅当前案件', field_key: '适用栏目', column: '适用记录栏目',
  writer: '适用书写者', template: '适用模板',
}
const CORRECTION_TARGET_TYPES = ['field', 'injury', 'terminology_rule', 'document', 'case']
const CORRECTION_TARGET_LABELS = {
  field: '事实字段', injury: '损伤条目', terminology_rule: '术语规则',
  document: '文稿', case: '案件',
}
const FIELD_GROUP_LABELS = {
  case_commission: '案件与委托', subject: '被检验对象', examination: '检验基本信息',
  general_findings: '尸表一般所见', injuries: '部位与损伤', document_config: '文书配置',
}
const FIELD_GROUP_ORDER = ['case_commission', 'subject', 'examination', 'general_findings',
  'injuries', 'document_config']
const PHOTO_SECTION_LABELS = { full: '全身', head: '头颈部', body: '躯干', limb: '四肢', other: '其他' }
const PHOTO_SECTION_ORDER = ['full', 'head', 'body', 'limb', 'other']
// 降级用的照片位置目录（与 constants.PHOTO_POSITION_DEFINITIONS 一致）
const PHOTO_POSITIONS_FALLBACK = [
  { key: 'body_overall', label: '全身', section: 'full', order: 10, anchor: 'photo_body_overall', wide: true },
  { key: 'head', label: '头部', section: 'head', order: 20, anchor: 'photo_head', wide: false },
  { key: 'face', label: '面部', section: 'head', order: 30, anchor: 'photo_face', wide: false },
  { key: 'neck', label: '颈部', section: 'head', order: 40, anchor: 'photo_neck', wide: false },
  { key: 'chest', label: '胸部', section: 'body', order: 50, anchor: 'photo_chest', wide: false },
  { key: 'abdomen', label: '腹部', section: 'body', order: 60, anchor: 'photo_abdomen', wide: false },
  { key: 'back', label: '背部', section: 'body', order: 70, anchor: 'photo_back', wide: false },
  { key: 'waist_hip', label: '腰臀部', section: 'body', order: 80, anchor: 'photo_waist_hip', wide: false },
  { key: 'perineum', label: '会阴部', section: 'body', order: 90, anchor: 'photo_perineum', wide: false },
  { key: 'arm_left', label: '左上肢', section: 'limb', order: 100, anchor: 'photo_arm_left', wide: false },
  { key: 'arm_right', label: '右上肢', section: 'limb', order: 110, anchor: 'photo_arm_right', wide: false },
  { key: 'hand_left', label: '左手', section: 'limb', order: 120, anchor: 'photo_hand_left', wide: false },
  { key: 'hand_right', label: '右手', section: 'limb', order: 130, anchor: 'photo_hand_right', wide: false },
  { key: 'leg_left', label: '左下肢', section: 'limb', order: 140, anchor: 'photo_leg_left', wide: false },
  { key: 'leg_right', label: '右下肢', section: 'limb', order: 150, anchor: 'photo_leg_right', wide: false },
  { key: 'foot_left', label: '左足', section: 'limb', order: 160, anchor: 'photo_foot_left', wide: false },
  { key: 'foot_right', label: '右足', section: 'limb', order: 170, anchor: 'photo_foot_right', wide: false },
  { key: 'other', label: '其他部位', section: 'other', order: 900, anchor: 'photo_other', wide: false },
]
const OCR_ENGINE_FALLBACK = [
  { name: 'sidecar', available: true, detail: '离线 JSON 样例引擎（默认，可离线运行）' },
  { name: 'paddle', available: false, detail: 'PaddleOCR 未安装（TODO-DL-2）' },
  { name: 'none', available: true, detail: '不执行识别' },
]
const DRAFT_MARKER = '【待核对】'
const PHOTO_PLACEHOLDER_TEXT = '（未提供照片）'

/* --------------------------------------------------------------------------
 * 1.1 主视图「整理」契约（POST /api/quick-run）
 *     普通用户只需两件事：上传文件、下载结果；其余细节收在「案件记录」次级界面。
 * ------------------------------------------------------------------------ */

/** 主视图唯一入口端点（冻结契约，见 docs/architecture.md 第 4 节）。 */
const QUICK_RUN_ENDPOINT = '/api/quick-run'
/** 单文件提示阈值：超过它只提示不改行为（网关 nginx 侧上限 64 MB）。 */
const QUICK_MAX_FILE_BYTES = 30 * 1024 * 1024
/** 网关上单体请求上限（base64 后约为原文件的 4/3）。 */
const QUICK_GATEWAY_LIMIT_BYTES = 64 * 1024 * 1024

/**
 * 每个文件的类型芯片：`kind` 留空 = 交后端按文件名推断（契约要求 kind 可空）。
 * 第二个元素是本工作台的中文标注，实际显示时若后端 /api/config 给了更规范的标签
 * （如 approved_report → 已审核报告），会把两者并列显示，避免口径不一致。
 */
const QUICK_KIND_CHIPS = [
  ['', '自动判断'],
  ['commission', '委托书'],
  ['record_book', '记录本'],
  ['photo', '部位照片'],
  ['template', '模板'],
  ['approved_report', '补审报告'],
  ['supplement', '补充资料'],
]

/** 文件选择框 accept：照片、PDF、Word、JSON（契约 media_type 覆盖范围）。 */
const QUICK_ACCEPT = 'image/*,.pdf,.docx,.doc,.json'

/** N-11/R-58 必须显著标注的照片口径（原文固定，不得改写）。 */
const QUICK_PHOTO_POLICY = '系统不读取照片内容，只按你选择的位置插入（未选择时按文件名推测，可在案件记录中修改）'

const STEP_TITLES = [
  { n: 1, title: '选择材料', hint: '上传/登记材料，检查页质量，运行识别；为部位照片选择目标位置' },
  { n: 2, title: '核对疑点', hint: '字段来源与确认、损伤逐条、术语整理、校核清单' },
  { n: 3, title: '查看文档', hint: '生成文稿版本、下载 docx/pdf、标记已审核' },
  { n: 4, title: '确认修改', hint: '四类修正录入、鉴定意见手工输入、交付保存' },
]

/* ==========================================================================
 * 2. 状态（全部在内存；密钥只放 state.apiKey，绝不落盘）
 * ========================================================================== */

const state = {
  view: 'quick',
  step: 1,
  booted: false,
  health: null,
  config: null,
  cfg: normalizeConfig({}),
  stats: null,
  cases: { items: [], total: 0, page: 1, page_size: 20 },
  filters: { search: '', stage: '', doc_status: '', material_kind: '', field_status: '', has_blocking: '' },
  page: 1,
  pageSize: 20,
  showCreateForm: false,
  caseId: null,
  caseData: null,
  // 以下五个集合来自各自的列表端点（GET .../materials|fields|injuries|documents|corrections）；
  // 为 null 时回退到案件详情里的同名字段（见各 case*() 取值函数）。
  materials: null,
  fields: null,
  injuries: null,
  documents: null,
  corrections: null,
  listEndpointWarned: false,
  ocrFilterMaterial: '',
  ocrPage: 1,
  ocrPageSize: 20,
  ocrBlocks: { items: [], total: 0, page: 1, page_size: 20 },
  preview: null,
  photoSlots: [],
  photoSlotMeta: null,     // /photo-slots 的汇总（photo_policy / placeholder_text / assigned_count / missing_count）
  photoPick: {},           // material_id -> 下拉框当前选择（仅视图辅助）
  checklistFilter: { status: '', category: '' },
  checklistItems: null,     // 服务端按 status/category 过滤后的清单（null 表示用案件详情里的清单）
  rules: [],
  rulesLoaded: false,
  ruleFilter: { search: '', active_only: false },
  ruleDraftOpen: false,
  normalizeResult: null,
  stageResult: null,
  apiKey: '',              // 仅内存，绝不写 localStorage
  aiResult: null,
  banners: [],
  loading: 0,

  // —— 主视图「整理」（POST /api/quick-run）——
  // files 保存浏览器 File 对象本身：重新渲染视图不会丢文件，失败时也不清空。
  quick: {
    files: [],             // [{id, file, name, size, media_type, kind, position_key}]
    formats: ['docx', 'pdf'],
    case_no: '',
    commission_unit: '',
    subject_name: '',
    note: '',
    showOptional: false,
    running: false,
    phase: '',
    startedAt: 0,
    error: '',
    result: null,          // /api/quick-run 响应的归一化结果
  },
  quickSeq: 0,

  // —— 次级界面「复核中心」（只用既有端点，不发明新接口）——
  review: { blocking: null, top: null, loaded: false, error: '' },
}

/* ==========================================================================
 * 3. 工具函数
 * ========================================================================== */

const $ = (selector, root) => (root || document).querySelector(selector)

function esc(value) {
  if (value === null || value === undefined) return ''
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

/** 空白即“未记录”（N-5）。永远不返回“未见异常”。 */
function isEmptyValue(value) {
  if (value === null || value === undefined) return true
  const text = String(value).trim()
  return text === '' || text === '/' || text === '—' || text === '-' || text === '无记录'
}

function valHtml(value) {
  if (isEmptyValue(value)) return '<span class="value-empty">' + NOT_RECORDED + '</span>'
  return '<span>' + esc(String(value).trim()) + '</span>'
}

function valText(value) {
  return isEmptyValue(value) ? NOT_RECORDED : String(value).trim()
}

function labelOf(map, key, fallback) {
  if (key === null || key === undefined || key === '') return fallback || ''
  return (map && map[key]) || key
}

function num(value, fallback) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : (fallback === undefined ? 0 : fallback)
}

function confidenceText(value) {
  if (value === null || value === undefined || value === '') return '<span class="value-empty">无识别分</span>'
  const score = Number(value)
  if (!Number.isFinite(score)) return '<span class="value-empty">无识别分</span>'
  const cls = score < state.cfg.ocr_low_confidence ? 'badge badge-danger' : (score < 0.75 ? 'badge badge-warn' : 'badge badge-ok')
  return '<span class="' + cls + '">' + (score * 100).toFixed(0) + '%</span>'
}

function bboxText(bbox) {
  if (!Array.isArray(bbox) || bbox.length !== 4) return '<span class="value-empty">无定位</span>'
  return '<span class="mono">[' + bbox.map((item) => Number(item).toFixed(3)).join(', ') + ']</span>'
}

function badge(kind, text) {
  return '<span class="badge badge-' + kind + '">' + esc(text) + '</span>'
}

function statusBadge(status) {
  if (!status) return badge('muted', '未记录')
  const map = { recorded: 'ok', not_recorded: 'warn', not_examined: 'info', negative: 'info', unclear: 'danger' }
  return badge(map[status] || 'muted', labelOf(state.cfg.field_status_labels, status))
}

function reviewBadge(reviewState) {
  if (reviewState === 'confirmed') return badge('ok', '已确认')
  if (reviewState === 'pending') return badge('warn', '待核对')
  return badge('muted', '未记录')
}

function severityBadge(severity) {
  const map = { blocking: 'danger', warning: 'warn', info: 'info' }
  return badge(map[severity] || 'muted', labelOf(state.cfg.check_severity_labels, severity))
}

function docStatusBadge(status) {
  const map = { internal_draft: 'info', pending_issue: 'warn', approved: 'ok', superseded: 'muted' }
  return badge(map[status] || 'muted', labelOf(state.cfg.doc_status_labels, status))
}

function materialName(materialId) {
  if (!materialId) return ''
  const list = caseMaterials()
  const found = list.find((item) => Number(item.id) === Number(materialId))
  if (!found) return '材料 #' + materialId
  return found.filename || ('材料 #' + materialId)
}

function materialOptionLabel(material) {
  const kind = labelOf(state.cfg.material_kind_labels, material.kind)
  return '#' + material.id + ' ' + kind + ' · ' + (material.filename || '未命名')
}

function currentVersion() {
  return state.caseData ? num(state.caseData.version, 1) : 1
}

function jsonPretty(value) {
  try {
    return esc(JSON.stringify(value, null, 2))
  } catch (error) {
    return esc(String(value))
  }
}

function uid(prefix) {
  return prefix + '-' + Math.random().toString(36).slice(2, 9)
}

/* ==========================================================================
 * 4. 加载态 / 提示 / 错误横幅
 * ========================================================================== */

function setLoading(delta) {
  state.loading = Math.max(0, state.loading + delta)
  const slot = $('#loading-slot')
  if (!slot) return
  slot.innerHTML = state.loading > 0
    ? '<span class="spinner"></span>处理中（' + state.loading + '）'
    : ''
}

async function withLoading(task) {
  setLoading(1)
  try {
    return await task()
  } finally {
    setLoading(-1)
  }
}

function toast(kind, title, message) {
  const host = $('#toasts')
  if (!host) return
  const node = document.createElement('div')
  node.className = 'toast toast-' + kind
  node.innerHTML = '<strong>' + esc(title) + '</strong>' + esc(message || '')
  host.appendChild(node)
  window.setTimeout(() => {
    if (node.parentNode) node.parentNode.removeChild(node)
  }, kind === 'err' ? 11000 : 6500)
}

function pushBanner(kind, html) {
  state.banners = [{ kind: kind, html: html }].concat(state.banners).slice(0, 4)
  renderBanners()
}

function clearBanners() {
  state.banners = []
  renderBanners()
}

function renderBanners() {
  const host = $('#banner-slot')
  if (!host) return
  host.innerHTML = state.banners.map((item, index) =>
    '<div class="callout callout-' + esc(item.kind) + '">' + item.html +
    ' <button type="button" class="btn btn-sm" data-act="dismiss-banner" data-index="' + index + '">关闭</button>' +
    '</div>').join('')
}

/**
 * 统一错误处理：非 2xx 一律带 status/message。
 * 409 一定提示“已被其他操作更新，请刷新后重试”并自动刷新案件详情与列表（契约硬要求）；
 * 同时区分子类：版本冲突（VersionConflict）直接以该提示为主，
 * 阻断项 / 非法流转 / 引擎不可用等 409 则先展示后端原因，再附上版本冲突提示。
 */
function handleApiError(error, context) {
  const status = error && error.status ? error.status : 0
  const detail = (error && error.message) ? error.message : String(error || '未知错误')
  const where = context ? '（' + context + '）' : ''
  if (status === 409) {
    const versionHint = '已被其他操作更新，请刷新后重试'
    const isVersionConflict = detail.indexOf('其他操作更新') >= 0 || detail.indexOf('刷新后重试') >= 0
    const message = isVersionConflict
      ? versionHint + '。服务端提示：' + detail
      : detail + '（如属版本冲突：' + versionHint + '）'
    toast('warn', isVersionConflict ? '409 版本冲突' + where : '409 操作未生效' + where, message)
    pushBanner('danger', '<strong>' + esc(isVersionConflict ? versionHint : '操作未生效（409）') + '</strong>：' +
      esc(detail) + '　' + esc(versionHint) +
      '　<button type="button" class="btn btn-sm" data-act="refresh-all">立即刷新</button>')
    void refreshAfterConflict()
    return
  }
  if (status === 401) {
    toast('err', '401 未授权' + where, detail)
    return
  }
  if (status === 422) {
    toast('err', '422 参数校验未通过' + where, detail)
    return
  }
  if (status === 404) {
    toast('err', '404 资源不存在' + where, detail)
    return
  }
  if (status === 502 || status === 504) {
    toast('err', '模型服务异常（' + status + '）' + where, detail)
    return
  }
  toast('err', '请求失败' + (status ? '（' + status + '）' : '') + where, detail)
}

async function refreshAfterConflict() {
  try {
    await loadCases()
  } catch (error) { /* 冲突刷新失败时保留原错误提示 */ }
  if (state.caseId) {
    try {
      await loadCase(state.caseId, { silent: true })
    } catch (error) { /* 同上 */ }
  }
  if (state.view === 'config') {
    try { await loadConfig() } catch (error) { /* 同上 */ }
  }
  render()
}

/** 使用 withLoading 包裹的请求：统一错误处理。 */
async function guarded(task, context) {
  try {
    return await withLoading(task)
  } catch (error) {
    handleApiError(error, context)
    return null
  }
}

/* ==========================================================================
 * 5. /api/config 归一化（容忍键名差异，缺项回退到第 1 节常量）
 * ========================================================================== */

function pick(raw, keys, fallback) {
  for (let index = 0; index < keys.length; index += 1) {
    const value = raw[keys[index]]
    if (value !== undefined && value !== null) return value
  }
  return fallback
}

function toLabelMap(value, fallback) {
  const result = Object.assign({}, fallback || {})
  if (!value) return result
  if (Array.isArray(value)) {
    value.forEach((item) => {
      if (typeof item === 'string') {
        if (!result[item]) result[item] = item
      } else if (item && typeof item === 'object') {
        const key = item.key || item.name || item.value || item.code
        if (key) result[key] = item.label || item.name || result[key] || key
      }
    })
    return result
  }
  if (typeof value === 'object') Object.assign(result, value)
  return result
}

function toKeyList(value, fallback) {
  if (!value) return (fallback || []).slice()
  if (Array.isArray(value)) {
    return value.map((item) => {
      if (typeof item === 'string') return item
      if (item && typeof item === 'object') return item.key || item.name || item.code || String(item)
      return String(item)
    })
  }
  if (typeof value === 'object') return Object.keys(value)
  return (fallback || []).slice()
}

function normalizeConfig(raw) {
  const source = raw && typeof raw === 'object' ? raw : {}
  const photoRaw = pick(source, ['photo_positions', 'photo_position_definitions'], null)
  let photoPositions = PHOTO_POSITIONS_FALLBACK.slice()
  if (Array.isArray(photoRaw) && photoRaw.length) {
    photoPositions = photoRaw.map((item) => ({
      key: item.key || item.position_key || '',
      label: item.label || item.name || item.key || '',
      section: item.section || 'other',
      section_label: item.section_label || '',
      order: num(item.order, 900),
      anchor: item.anchor || ('photo_' + (item.key || '')),
      wide: Boolean(item.wide),
    })).filter((item) => item.key)
  }
  const sectionLabels = Object.assign({}, PHOTO_SECTION_LABELS,
    toLabelMap(pick(source, ['photo_section_labels', 'photo_sections'], null), {}))
  photoPositions.forEach((item) => {
    if (!item.section_label) item.section_label = sectionLabels[item.section] || item.section || '其他'
  })

  // 实测后端把引擎状态放在 engines = {docx:{preferred,docxtpl_available}, libreoffice, libreoffice_available, ocr:[...]}
  const nested = (source.engines && typeof source.engines === 'object') ? source.engines : {}
  const engineRaw = Array.isArray(nested.ocr) ? nested.ocr
    : pick(source, ['ocr_engines', 'ocr_engine_status'], null)
  let engines = OCR_ENGINE_FALLBACK.slice()
  if (Array.isArray(engineRaw) && engineRaw.length) {
    engines = engineRaw.map((item) => {
      // 实测：engines.ocr = [{name,available,detail}]；顶层 ocr_engines 只是名字数组，
      // 不含可用性，绝不能据此显示“paddle 可用”，一律按不可用处理并注明原因。
      if (typeof item === 'string') {
        return {
          name: item,
          available: false,
          detail: '后端 /api/config 未提供该引擎的可用性（engines.ocr 缺失），按不可用处理',
        }
      }
      return {
        name: item.name || item.engine || '',
        available: item.available === undefined ? false : Boolean(item.available),
        detail: item.detail || item.description || '',
      }
    }).filter((item) => item.name)
  }

  const lo = pick(source, ['libreoffice', 'libreoffice_path', 'libreoffice_bin', 'soffice'],
    nested.libreoffice || '')
  const docxNested = (nested.docx && typeof nested.docx === 'object') ? nested.docx : null
  const docxEngineRaw = pick(source, ['docx_engine', 'docx_engine_status'], '')
  const docxEngine = docxNested ? (docxNested.preferred || docxNested.engine || '')
    : (typeof docxEngineRaw === 'object' && docxEngineRaw
      ? (docxEngineRaw.preferred || docxEngineRaw.engine || '')
      : docxEngineRaw)
  const docxtpl = docxNested && 'docxtpl_available' in docxNested
    ? Boolean(docxNested.docxtpl_available)
    : pick(source, ['docxtpl_available'], null)

  return {
    raw: source,
    stage_order: toKeyList(pick(source, ['case_stages', 'stages', 'stage_order'], null), STAGE_ORDER),
    stage_labels: toLabelMap(pick(source, ['stage_labels', 'case_stage_labels', 'stages'], null), STAGE_LABELS),
    stage_transitions: pick(source, ['stage_transitions'], null),
    field_status_order: toKeyList(pick(source, ['field_statuses'], null), FIELD_STATUS_ORDER),
    field_status_labels: toLabelMap(pick(source, ['field_status_labels', 'field_statuses'], null), FIELD_STATUS_LABELS),
    review_state_labels: toLabelMap(pick(source, ['review_state_labels', 'review_states'], null), REVIEW_STATE_LABELS),
    material_kind_order: toKeyList(pick(source, ['material_kinds'], null), MATERIAL_KIND_ORDER),
    material_kind_labels: toLabelMap(pick(source, ['material_kind_labels', 'material_kinds'], null), MATERIAL_KIND_LABELS),
    media_types: toKeyList(pick(source, ['material_media_types', 'media_types'], null), MATERIAL_MEDIA_TYPES),
    doc_status_labels: toLabelMap(pick(source, ['doc_status_labels', 'doc_statuses'], null), DOC_STATUS_LABELS),
    doc_statuses: toKeyList(pick(source, ['doc_statuses'], null), Object.keys(DOC_STATUS_LABELS)),
    quality_labels: toLabelMap(pick(source, ['image_quality_labels', 'image_quality_flags'], null), IMAGE_QUALITY_LABELS),
    quality_flags: toKeyList(pick(source, ['image_quality_flags'], null), Object.keys(IMAGE_QUALITY_LABELS)),
    operations: toKeyList(pick(source, ['image_operations'], null), IMAGE_OPERATIONS.map((item) => item[0])),
    operation_labels: Object.assign({}, Object.fromEntries(IMAGE_OPERATIONS),
      toLabelMap(pick(source, ['image_operation_labels'], null), {})),
    check_category_labels: toLabelMap(pick(source, ['check_category_labels', 'check_categories'], null), CHECK_CATEGORY_LABELS),
    check_category_order: toKeyList(pick(source, ['check_categories'], null), CHECK_CATEGORY_ORDER),
    check_severity_labels: toLabelMap(pick(source, ['check_severity_labels', 'check_severities'], null), CHECK_SEVERITY_LABELS),
    check_severity_order: toKeyList(pick(source, ['check_severities'], null), CHECK_SEVERITY_ORDER),
    check_item_status_labels: toLabelMap(pick(source, ['check_item_status_labels', 'check_item_statuses'], null), CHECK_ITEM_STATUS_LABELS),
    correction_kind_labels: toLabelMap(pick(source, ['correction_kind_labels', 'correction_kinds'], null), CORRECTION_KIND_LABELS),
    correction_kinds: toKeyList(pick(source, ['correction_kinds'], null), Object.keys(CORRECTION_KIND_LABELS)),
    correction_scope_labels: toLabelMap(pick(source, ['correction_scope_labels', 'correction_scopes'], null), CORRECTION_SCOPE_LABELS),
    correction_scopes: toKeyList(pick(source, ['correction_scopes'], null), Object.keys(CORRECTION_SCOPE_LABELS)),
    correction_target_types: toKeyList(pick(source, ['correction_target_types'], null), CORRECTION_TARGET_TYPES),
    field_group_labels: toLabelMap(pick(source, ['field_group_labels', 'field_groups'], null), FIELD_GROUP_LABELS),
    field_group_order: toKeyList(pick(source, ['field_group_order', 'field_groups'], null), FIELD_GROUP_ORDER),
    photo_positions: photoPositions,
    photo_section_labels: sectionLabels,
    photo_section_order: toKeyList(pick(source, ['photo_sections'], null), PHOTO_SECTION_ORDER),
    photo_max_width_cm: num(pick(source, ['photo_max_width_cm'], 7), 7),
    photo_wide_max_width_cm: num(pick(source, ['photo_wide_max_width_cm'], 11), 11),
    photo_max_per_position: num(pick(source, ['photo_max_per_position'], 8), 8),
    photo_placeholder: pick(source, ['photo_placeholder', 'photo_placeholder_text'], PHOTO_PLACEHOLDER_TEXT),
    photo_policy: pick(source, ['photo_policy'], ''),
    engines: engines,
    docx_engine: docxEngine || '未知',
    docxtpl_available: docxtpl,
    libreoffice: lo,
    libreoffice_available: pick(source, ['libreoffice_available'], null),
    demo_data: Boolean(pick(source, ['demo_data'], false)),
    demo_note: pick(source, ['demo_note'], ''),
    contract_version: pick(source, ['contract_version'], ''),
    todo_downloads: Array.isArray(source.todo_downloads) ? source.todo_downloads : [],
    field_definitions: Array.isArray(source.field_definitions) ? source.field_definitions : [],
    required_field_keys: toKeyList(pick(source, ['required_field_keys'], null), []),
    placeholder_patterns: toKeyList(pick(source, ['placeholder_patterns'], null), []),
    template_version: pick(source, ['template_version', 'default_template_version'], 'report-template-v1'),
    term_rule_version: pick(source, ['term_rule_version', 'term_rule_default_version'], 'term-rules-v1'),
    ocr_low_confidence: num(pick(source, ['ocr_low_confidence'], OCR_LOW_CONFIDENCE_FALLBACK),
      OCR_LOW_CONFIDENCE_FALLBACK),
    ocr_default_engine: pick(source, ['default_ocr_engine', 'ocr_default_engine'], 'sidecar'),
    draft_marker: pick(source, ['draft_marker'], DRAFT_MARKER),
    required_material_kinds: toKeyList(pick(source, ['required_material_kinds'], null),
      ['commission', 'record_book', 'template']),
  }
}

/* ==========================================================================
 * 6. 数据加载
 * ========================================================================== */

async function loadConfig() {
  const data = await api.config()
  state.config = data
  state.cfg = normalizeConfig(data)
  renderConnection()
  return data
}

async function loadHealth() {
  state.health = await api.health()
  renderConnection()
  return state.health
}

function renderConnection() {
  const badgeNode = $('#conn-badge')
  if (!badgeNode) return
  if (state.health && state.health.status === 'ok') {
    badgeNode.className = 'badge badge-ok'
    badgeNode.textContent = '后端已连接'
  } else if (state.config) {
    badgeNode.className = 'badge badge-info'
    badgeNode.textContent = '配置已加载'
  } else {
    badgeNode.className = 'badge badge-muted'
    badgeNode.textContent = '未连接'
  }
  const demo = $('#demo-badge')
  if (demo) demo.hidden = !state.cfg.demo_data
}

async function loadStats() {
  state.stats = await api.stats()
  return state.stats
}

function caseQueryParams() {
  return {
    search: state.filters.search,
    stage: state.filters.stage,
    doc_status: state.filters.doc_status,
    material_kind: state.filters.material_kind,
    field_status: state.filters.field_status,
    has_blocking: state.filters.has_blocking,
    page: state.page,
    page_size: state.pageSize,
  }
}

async function loadCases() {
  const data = await api.listCases(caseQueryParams())
  state.cases = {
    items: Array.isArray(data && data.items) ? data.items : [],
    total: num(data && data.total, 0),
    page: num(data && data.page, state.page),
    page_size: num(data && data.page_size, state.pageSize),
  }
  return state.cases
}

async function loadCase(caseId) {
  const data = await api.case(caseId)
  if (!data) return null
  state.caseData = data
  state.caseId = num(data.id, caseId)
  await loadCaseCollections()
  return data
}

/** 依次调用五个列表端点；任一失败即回退到案件详情里的同名字段。 */
async function loadCaseCollections() {
  const id = state.caseId
  if (!id) return
  const tasks = [
    ['materials', '材料清单', () => api.listMaterials(id)],
    ['fields', '字段列表', () => api.fields(id)],
    ['injuries', '损伤列表', () => api.injuries(id)],
    ['documents', '文稿版本列表', () => api.documents(id)],
    ['corrections', '修正记录', () => api.corrections(id)],
  ]
  let failed = 0
  for (const [key, label, run] of tasks) {
    try {
      const data = await run()
      state[key] = Array.isArray(data) ? data : (Array.isArray(data && data.items) ? data.items : null)
    } catch (error) {
      state[key] = null
      failed += 1
      if (!state.listEndpointWarned) {
        state.listEndpointWarned = true
        pushBanner('warn', '列表端点暂不可用（' + esc(label) + '），界面已回退到 <code>GET /api/cases/{id}</code> 的聚合结果。' +
          '服务端提示：' + esc(error && error.message ? error.message : String(error)))
      }
    }
  }
  return failed
}

function caseMaterials() {
  return state.materials !== null ? state.materials : ((state.caseData && state.caseData.materials) || [])
}

function caseFields() {
  return state.fields !== null ? state.fields : ((state.caseData && state.caseData.fields) || [])
}

function caseInjuries() {
  return state.injuries !== null ? state.injuries : ((state.caseData && state.caseData.injuries) || [])
}

function caseDocuments() {
  return state.documents !== null ? state.documents : ((state.caseData && state.caseData.documents) || [])
}

function caseCorrections() {
  return state.corrections !== null ? state.corrections : ((state.caseData && state.caseData.corrections) || [])
}

/** GET 类加载：失败只提示、不抛出，避免一个可选列表端点阻断整个工作台。 */
async function softLoad(label, task) {
  try {
    return await task()
  } catch (error) {
    const status = error && error.status ? '（' + error.status + '）' : ''
    toast('warn', label + '加载失败' + status, (error && error.message) || String(error))
    return null
  }
}

async function loadPhotoSlots() {
  if (!state.caseId) { state.photoSlots = []; state.photoSlotMeta = null; return [] }
  const data = await softLoad('照片栏位', () => api.photoSlots(state.caseId))
  let list = []
  let meta = null
  if (Array.isArray(data)) {
    list = data
  } else if (data && typeof data === 'object') {
    // 实测后端返回 {photo_policy, placeholder_text, slots:[...], missing_count, assigned_count}
    if (Array.isArray(data.slots)) list = data.slots
    else if (Array.isArray(data.items)) list = data.items
    meta = {
      photo_policy: data.photo_policy || '',
      placeholder_text: data.placeholder_text || '',
      missing_count: num(data.missing_count, 0),
      assigned_count: num(data.assigned_count, 0),
    }
  }
  if (!list.length && state.caseData && state.caseData.photo_slots) {
    const fallback = state.caseData.photo_slots
    if (Array.isArray(fallback)) list = fallback
    else if (fallback && Array.isArray(fallback.slots)) list = fallback.slots
  }
  state.photoSlots = list
  state.photoSlotMeta = meta
  return list
}

async function loadOcrBlocks() {
  if (!state.caseId) { state.ocrBlocks = { items: [], total: 0, page: 1, page_size: state.ocrPageSize }; return }
  const data = await softLoad('识别结果', () => api.ocrBlocks(state.caseId, {
    material_id: state.ocrFilterMaterial,
    page: state.ocrPage,
    page_size: state.ocrPageSize,
  }))
  state.ocrBlocks = {
    items: Array.isArray(data && data.items) ? data.items : [],
    total: num(data && data.total, 0),
    page: num(data && data.page, state.ocrPage),
    page_size: num(data && data.page_size, state.ocrPageSize),
  }
}

async function loadRules() {
  const data = await api.rules({
    search: state.ruleFilter.search,
    active_only: state.ruleFilter.active_only ? 'true' : '',
  })
  state.rules = Array.isArray(data) ? data : (Array.isArray(data && data.items) ? data.items : [])
  state.rulesLoaded = true
  return state.rules
}

/* ==========================================================================
 * 7. 视图调度
 * ========================================================================== */

function setView(view) {
  state.view = view
  const map = {
    quick: '#view-quick', results: '#view-results', review: '#view-review',
    cases: '#view-cases', case: '#view-case', rules: '#view-rules', config: '#view-config',
  }
  Object.entries(map).forEach(([key, selector]) => {
    const node = $(selector)
    if (node) node.hidden = key !== view
  })
  document.querySelectorAll('.nav-item').forEach((node) => {
    node.classList.toggle('is-active', node.dataset.view === view)
  })
  render()
}

function render() {
  if (state.view === 'quick') renderQuickView()
  else if (state.view === 'results') renderResultsView()
  else if (state.view === 'review') renderReviewView()
  else if (state.view === 'cases') renderCasesView()
  else if (state.view === 'case') renderCaseView()
  else if (state.view === 'rules') renderRulesView()
  else if (state.view === 'config') renderConfigView()
  renderResultsNavBadge()
  renderBanners()
  renderConnection()
}

/** 导航「结果」上的小徽标：让用户一眼看到是否已有产物、还有多少疑点。 */
function renderResultsNavBadge() {
  const node = $('#results-nav-badge')
  if (!node) return
  const result = state.quick.result
  if (!result) {
    node.hidden = false
    node.className = 'badge badge-muted'
    node.textContent = '尚未整理'
    return
  }
  const unresolved = num(result.summary && result.summary.unresolved_count, 0)
  const blocking = num(result.summary && result.summary.blocking_count, 0)
  node.hidden = false
  node.className = 'badge ' + (blocking ? 'badge-danger' : (unresolved ? 'badge-warn' : 'badge-ok'))
  node.textContent = blocking ? ('阻断 ' + blocking) : (unresolved ? ('待处理 ' + unresolved) : '已完成')
}

/* ==========================================================================
 * 8. 案件列表视图
 * ========================================================================== */

function optionList(items, labels, selected, emptyLabel) {
  const head = emptyLabel === null ? '' : '<option value="">' + esc(emptyLabel || '全部') + '</option>'
  return head + items.map((key) =>
    '<option value="' + esc(key) + '"' + (String(selected) === String(key) ? ' selected' : '') + '>' +
    esc(labels[key] || key) + '</option>').join('')
}

function statCards() {
  const stats = state.stats || {}
  const cards = [
    { label: '案件总数', value: num(stats.case_count !== undefined ? stats.case_count : stats.total_cases, 0) },
    { label: '待核对字段/损伤', value: num(stats.unresolved_count, 0), cls: 'is-warn' },
    { label: '未解决阻断项', value: num(stats.blocking_count, 0), cls: 'is-danger' },
    { label: '文稿版本数', value: num(stats.document_count, 0) },
    { label: '已审核文稿', value: num(stats.approved_document_count, 0) },
  ]
  const body = cards.map((item) =>
    '<div class="stat-card"><div class="stat-value ' + (item.cls || '') + '">' + item.value + '</div>' +
    '<div class="stat-label">' + esc(item.label) + '</div></div>').join('')

  const byStage = Array.isArray(stats.by_stage) ? stats.by_stage : null
  const stageList = byStage
    ? byStage.map((item) => '<span class="badge badge-muted">' + esc(item.label || item.stage) + ' ' + num(item.count, 0) + '</span>').join(' ')
    : Object.entries(stats.stage_counts || {}).map(([key, count]) =>
      '<span class="badge badge-muted">' + esc(labelOf(state.cfg.stage_labels, key)) + ' ' + num(count, 0) + '</span>').join(' ')

  const byKind = Array.isArray(stats.by_material_kind) ? stats.by_material_kind : null
  const kindList = byKind
    ? byKind.map((item) => '<span class="badge badge-muted">' + esc(item.label || item.kind) + ' ' + num(item.count, 0) + '</span>').join(' ')
    : Object.entries(stats.material_counts || {}).map(([key, count]) =>
      '<span class="badge badge-muted">' + esc(labelOf(state.cfg.material_kind_labels, key)) + ' ' + num(count, 0) + '</span>').join(' ')

  return '' +
    '<div class="card"><div class="card-head"><div class="card-title">统计卡' +
    '<span class="badge badge-muted">GET /api/stats</span></div>' +
    '<button type="button" class="btn btn-sm" data-act="stats-reload">刷新统计</button></div>' +
    '<div class="card-body"><div class="stat-grid">' + body + '</div></div>' +
    '<div class="card-body"><div class="small muted">按阶段分布</div><div class="row">' + (stageList || '<span class="muted">暂无</span>') + '</div>' +
    '<div class="small muted" style="margin-top:6px">按材料类型分布</div><div class="row">' + (kindList || '<span class="muted">暂无</span>') + '</div></div></div>'
}

function casesFilterBar() {
  const cfg = state.cfg
  return '' +
    '<div class="card"><div class="card-head"><div class="card-title">筛选与分页' +
    '<span class="badge badge-muted">GET /api/cases</span></div></div><div class="card-body">' +
    '<div class="form-grid">' +
    '<div class="field"><label for="f-search">搜索（案件编号/委托单位/姓名/委托事项）</label>' +
    '<input id="f-search" class="input" data-filter="search" value="' + esc(state.filters.search) + '" placeholder="输入关键词后回车" /></div>' +
    '<div class="field"><label for="f-stage">案件阶段</label><select id="f-stage" class="select" data-filter="stage">' +
    optionList(cfg.stage_order, cfg.stage_labels, state.filters.stage) + '</select></div>' +
    '<div class="field"><label for="f-doc">文稿状态</label><select id="f-doc" class="select" data-filter="doc_status">' +
    optionList(cfg.doc_statuses, cfg.doc_status_labels, state.filters.doc_status) + '</select></div>' +
    '<div class="field"><label for="f-kind">存在材料类型</label><select id="f-kind" class="select" data-filter="material_kind">' +
    optionList(cfg.material_kind_order, cfg.material_kind_labels, state.filters.material_kind) + '</select></div>' +
    '<div class="field"><label for="f-field">存在字段状态</label><select id="f-field" class="select" data-filter="field_status">' +
    optionList(cfg.field_status_order, cfg.field_status_labels, state.filters.field_status) + '</select></div>' +
    '<div class="field"><label for="f-blocking">存在未解决阻断项</label><select id="f-blocking" class="select" data-filter="has_blocking">' +
    '<option value="">全部</option><option value="true"' + (state.filters.has_blocking === 'true' ? ' selected' : '') + '>是</option>' +
    '<option value="false"' + (state.filters.has_blocking === 'false' ? ' selected' : '') + '>否</option></select></div>' +
    '<div class="field"><label for="f-size">每页条数</label><select id="f-size" class="select" data-page-size>' +
    [10, 20, 50, 100].map((size) => '<option value="' + size + '"' + (state.pageSize === size ? ' selected' : '') + '>' + size + '</option>').join('') +
    '</select></div>' +
    '</div><div class="form-actions">' +
    '<button type="button" class="btn btn-primary" data-act="cases-search">查询</button>' +
    '<button type="button" class="btn" data-act="cases-reset">重置筛选</button>' +
    '<span class="hint">分页与筛选均为服务端执行（page / page_size ≤ 100）。</span>' +
    '</div></div></div>'
}

function casesTable() {
  const items = state.cases.items
  if (!items.length) {
    return '<div class="card"><div class="card-head"><div class="card-title">案件列表</div></div>' +
      '<div class="card-body"><div class="empty-note">没有符合条件的案件。可点击“新建案件”建立第一个案件。</div></div></div>'
  }
  const rows = items.map((item) => {
    const blocking = num(item.blocking_count, 0)
    const unresolved = num(item.unresolved_count, 0)
    return '<tr>' +
      '<td class="nowrap"><strong>' + esc(item.case_no || '未编号') + '</strong>' +
      '<div class="small muted">ID ' + num(item.id, 0) + ' · v' + num(item.version, 1) + '</div></td>' +
      '<td>' + valHtml(item.subject_name) + '</td>' +
      '<td>' + valHtml(item.commission_unit) + '</td>' +
      '<td>' + badge('info', labelOf(state.cfg.stage_labels, item.stage)) + '</td>' +
      '<td>' + docStatusBadge(item.doc_status) + '</td>' +
      '<td class="num">' + num(item.material_count, 0) + '</td>' +
      '<td class="num">' + num(item.field_count, 0) + '</td>' +
      '<td class="num">' + (unresolved ? '<span class="badge badge-warn">' + unresolved + '</span>' : '0') + '</td>' +
      '<td class="num">' + (blocking ? '<span class="badge badge-danger">' + blocking + '</span>' : '0') + '</td>' +
      '<td class="num">' + num(item.document_count, 0) + '</td>' +
      '<td class="small nowrap">' + esc(item.updated_at || '') + '</td>' +
      '<td><div class="table-actions">' +
      '<button type="button" class="btn btn-sm btn-primary" data-act="case-open" data-id="' + num(item.id, 0) + '">打开工作台</button>' +
      '<button type="button" class="btn btn-sm btn-danger" data-act="case-delete" data-id="' + num(item.id, 0) +
      '" data-version="' + num(item.version, 1) + '" data-no="' + esc(item.case_no || '') + '">删除</button>' +
      '</div></td></tr>'
  }).join('')

  const total = state.cases.total
  const pageSize = state.cases.page_size || state.pageSize
  const page = state.cases.page || state.page
  const pages = Math.max(1, Math.ceil(total / Math.max(1, pageSize)))
  return '<div class="card"><div class="card-head"><div class="card-title">案件列表' +
    '<span class="badge badge-muted">共 ' + total + ' 条</span></div></div>' +
    '<div class="table-wrap"><table class="table"><thead><tr>' +
    '<th>案件编号</th><th>姓名</th><th>委托单位</th><th>阶段</th><th>文稿状态</th>' +
    '<th class="num">材料</th><th class="num">字段</th><th class="num">待核对</th><th class="num">阻断</th>' +
    '<th class="num">文稿</th><th>更新时间</th><th>操作</th></tr></thead><tbody>' + rows + '</tbody></table></div>' +
    '<div class="card-body"><div class="pager">' +
    '<span class="pager-info">第 ' + page + ' / ' + pages + ' 页，每页 ' + pageSize + ' 条，共 ' + total + ' 条</span>' +
    '<button type="button" class="btn btn-sm" data-act="cases-page" data-page="' + (page - 1) + '"' + (page <= 1 ? ' disabled' : '') + '>上一页</button>' +
    '<button type="button" class="btn btn-sm" data-act="cases-page" data-page="' + (page + 1) + '"' + (page >= pages ? ' disabled' : '') + '>下一页</button>' +
    '</div></div></div>'
}

function createCaseForm() {
  if (!state.showCreateForm) {
    return '<div class="card"><div class="card-body row-between">' +
      '<span class="muted">建案表单默认折叠；案件编号可留空，由后端自动生成。intake 任务信息共 7 项。</span>' +
      '<button type="button" class="btn btn-primary" data-act="toggle-create">新建案件</button></div></div>'
  }
  const cfg = state.cfg
  const field = (name, label, attrs) =>
    '<div class="field"><label for="c-' + name + '">' + esc(label) + '</label>' +
    '<input id="c-' + name + '" class="input" data-create="' + name + '" ' + (attrs || '') + ' /></div>'
  const area = (name, label, rows) =>
    '<div class="field"><label for="c-' + name + '">' + esc(label) + '</label>' +
    '<textarea id="c-' + name + '" class="textarea" data-create="' + name + '" rows="' + (rows || 2) + '"></textarea></div>'

  return '<div class="card"><div class="card-head"><div class="card-title">新建案件' +
    '<span class="badge badge-muted">POST /api/cases</span></div>' +
    '<button type="button" class="btn btn-sm" data-act="toggle-create">收起</button></div>' +
    '<div class="card-body"><div class="form-grid">' +
    field('case_no', '案件编号（留空自动生成 FY-<年份>-<序号>）', 'placeholder="可留空" maxlength="64"') +
    field('commission_unit', '委托单位', 'maxlength="120"') +
    field('commission_matter', '委托事项（不会被改写成鉴定意见）', 'maxlength="500"') +
    field('commission_date', '委托日期（与受理/检验日期分别处理）', 'placeholder="如 2026-10-12" maxlength="40"') +
    field('subject_name', '姓名（不得填入委托单位栏）', 'maxlength="80"') +
    field('template_version', '模板版本', 'value="' + esc(cfg.template_version) + '" maxlength="80"') +
    field('term_rule_version', '术语规则版本', 'value="' + esc(cfg.term_rule_version) + '" maxlength="80"') +
    '</div><div class="form-grid" style="margin-top:10px">' +
    area('case_summary', '案情简介（保留委托书来源属性）', 3) +
    '</div>' +
    '<hr class="sep" /><div class="section-title">任务信息 intake（需求第一章，7 项）</div>' +
    '<div class="form-grid" style="margin-top:8px">' +
    area('input_materials', '① 输入材料', 2) +
    area('output_documents', '② 输出文书', 2) +
    area('template_requirement', '③ 模板要求', 2) +
    area('content_requirement', '④ 内容要求', 2) +
    area('format_requirement', '⑤ 格式要求', 2) +
    area('final_artifacts', '⑥ 最终产物（docx,pdf）', 1) +
    area('open_questions', '⑦ 待确认问题', 2) +
    '</div>' +
    '<div class="form-actions">' +
    '<button type="button" class="btn btn-primary" data-act="case-create">建立案件</button>' +
    '<button type="button" class="btn" data-act="toggle-create">取消</button>' +
    '<span class="hint">建案不需要 expected_version（POST /api/cases 会创建新资源）。</span>' +
    '</div></div></div>'
}

function renderCasesView() {
  const host = $('#view-cases')
  host.innerHTML = [
    statCards(),
    createCaseForm(),
    casesFilterBar(),
    casesTable(),
  ].join('')
}

/* ==========================================================================
 * 9. 案件工作台外壳：案件信息、阶段步骤条、四步主流程标签页
 * ========================================================================== */

function stageStepper() {
  const caseData = state.caseData || {}
  const order = state.cfg.stage_order
  const current = caseData.stage || 'intake'
  const index = Math.max(0, order.indexOf(current))
  const transitions = state.cfg.stage_transitions && state.cfg.stage_transitions[current]
  const allowed = Array.isArray(transitions) && transitions.length
    ? transitions
    : [order[index - 1], order[index + 1]].filter(Boolean)

  const steps = order.map((stage, position) => {
    const cls = position < index ? 'is-done' : (position === index ? 'is-active' : '')
    return '<span class="step ' + cls + '"><span class="step-num">' + (position + 1) + '</span>' +
      esc(labelOf(state.cfg.stage_labels, stage)) + '</span>'
  }).join('')

  const moves = allowed.map((stage) => {
    const isNext = order.indexOf(stage) > index
    return '<button type="button" class="btn btn-sm ' + (isNext ? 'btn-primary' : 'btn-warn') + '" ' +
      'data-act="stage-move" data-stage="' + esc(stage) + '">' +
      (isNext ? '推进到：' : '回退到：') + esc(labelOf(state.cfg.stage_labels, stage)) + '</button>'
  }).join('')

  return '<div class="card"><div class="card-body">' +
    '<div class="row-between"><div class="steps">' + steps + '</div>' +
    '<span class="badge badge-info">当前阶段：' + esc(labelOf(state.cfg.stage_labels, current)) + '</span></div>' +
    '<div class="form-actions">' + (moves || '<span class="hint">没有可前进或回退的阶段。</span>') +
    '<span class="hint">阶段只允许前进一格或回退一格（PATCH /api/cases/{id}/stage，带 expected_version）。</span>' +
    '</div></div></div>'
}

function caseHeader() {
  const caseData = state.caseData || {}
  const summary = caseData.summary || {}
  const blocking = num(summary.blocking_count, 0)
  const docStatus = caseData.doc_status || ''
  return '<div class="card"><div class="card-head">' +
    '<div class="card-title">' + esc(caseData.case_no || '未编号') +
    '<span class="badge badge-muted">案件 ID ' + num(caseData.id, 0) + ' · v' + num(caseData.version, 1) + '</span>' +
    (state.cfg.demo_data ? '<span class="badge badge-info">演示数据</span>' : '') + '</div>' +
    '<div class="btn-row">' +
    '<button type="button" class="btn btn-sm" data-act="case-reload">刷新案件</button>' +
    '<button type="button" class="btn btn-sm" data-act="nav" data-view="cases">返回案件列表</button>' +
    '</div></div>' +
    '<div class="card-body"><div class="grid-3">' +
    '<div><span class="small muted">姓名</span><div>' + valHtml(caseData.subject_name) + '</div></div>' +
    '<div><span class="small muted">委托单位</span><div>' + valHtml(caseData.commission_unit) + '</div></div>' +
    '<div><span class="small muted">委托事项</span><div>' + valHtml(caseData.commission_matter) + '</div></div>' +
    '<div><span class="small muted">委托日期</span><div>' + valHtml(caseData.commission_date) + '</div></div>' +
    '<div><span class="small muted">模板版本</span><div>' + valHtml(caseData.template_version) + '</div></div>' +
    '<div><span class="small muted">术语规则版本</span><div>' + valHtml(caseData.term_rule_version) + '</div></div>' +
    '</div></div>' +
    '<div class="card-body"><div class="row">' +
    '<span class="badge badge-muted">材料 ' + num(summary.material_count, 0) + '</span>' +
    '<span class="badge badge-muted">字段 ' + num(summary.field_count, 0) + '</span>' +
    '<span class="badge badge-muted">损伤 ' + num(summary.injury_count, 0) + '</span>' +
    '<span class="badge badge-warn">待核对 ' + num(summary.unresolved_count, 0) + '</span>' +
    '<span class="badge ' + (blocking ? 'badge-danger' : 'badge-ok') + '">未解决阻断项 ' + blocking + '</span>' +
    '<span class="badge badge-muted">文稿 ' + num(summary.document_count, 0) + '</span>' +
    '<span class="badge badge-muted">文稿状态 ' + esc(labelOf(state.cfg.doc_status_labels, docStatus, '未生成')) + '</span>' +
    '</div></div></div>'
}

function stepTabs() {
  return '<div class="card"><div class="tabs">' + STEP_TITLES.map((item) =>
    '<button type="button" class="tab' + (state.step === item.n ? ' is-active' : '') +
    '" data-act="step" data-step="' + item.n + '">' + item.n + ' · ' + esc(item.title) + '</button>').join('') +
    '</div><div class="card-body"><div class="row-between"><span class="muted">' +
    esc((STEP_TITLES.find((item) => item.n === state.step) || {}).hint || '') + '</span>' +
    '<span class="badge badge-muted">四步主流程 R-1</span></div></div></div>'
}

function renderCaseView() {
  const host = $('#view-case')
  if (!state.caseData) {
    host.innerHTML = '<div class="card"><div class="card-body"><div class="empty-note">' +
      '尚未打开案件。请到“案件列表”选择一个案件，或先新建案件。</div>' +
      '<div class="form-actions"><button type="button" class="btn btn-primary" data-act="nav" data-view="cases">前往案件列表</button></div>' +
      '</div></div>'
    return
  }
  let stepBody = ''
  if (state.step === 1) stepBody = materialsPanel() + photoPanel()
  else if (state.step === 2) stepBody = extractCard() + checklistPanel() + fieldsPanel() + injuriesPanel() + normalizePanel()
  else if (state.step === 3) stepBody = documentsPanel()
  else stepBody = caseInfoCard() + opinionCard() + correctionsPanel()
  host.innerHTML = caseHeader() + stageStepper() + stepTabs() + stepBody
}

/* ==========================================================================
 * 10. 材料面板（上传、页质量、预处理、识别、原图预览）
 * ========================================================================== */

function mediaTypeOf(file) {
  const name = (file.name || '').toLowerCase()
  const type = (file.type || '').toLowerCase()
  if (type.includes('pdf') || name.endsWith('.pdf')) return 'pdf'
  if (type.includes('jpeg') || type.includes('jpg') || name.endsWith('.jpg') || name.endsWith('.jpeg')) return 'jpeg'
  if (type.includes('png') || name.endsWith('.png')) return 'png'
  if (type.includes('word') || name.endsWith('.docx')) return 'docx'
  if (type.includes('json') || name.endsWith('.json')) return 'json'
  return 'other'
}

function readFileAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const result = String(reader.result || '')
      const comma = result.indexOf(',')
      resolve(comma >= 0 ? result.slice(comma + 1) : result)
    }
    reader.onerror = () => reject(new Error('读取文件失败：' + (file.name || '')))
    reader.readAsDataURL(file)
  })
}

function pageEditor(material, page) {
  const flags = Array.isArray(page.quality_flags) ? page.quality_flags : []
  const flagBoxes = state.cfg.quality_flags.map((flag) =>
    '<label class="field-flag"><input type="checkbox" data-page-flag="' + esc(flag) + '"' +
    (flags.includes(flag) ? ' checked' : '') + ' />' + esc(labelOf(state.cfg.quality_labels, flag)) + '</label>').join('')
  const ops = state.cfg.operations.map((op) =>
    '<label class="field-flag"><input type="checkbox" data-preprocess-op="' + esc(op) + '"' +
    (op === 'autocontrast' ? ' checked' : '') + ' />' + esc(labelOf(state.cfg.operation_labels, op)) + '</label>').join('')
  const preprocessing = page.preprocessing && typeof page.preprocessing === 'object'
    ? Object.keys(page.preprocessing).length : 0

  return '<div class="page-list" data-page-block="' + num(page.page_number, 0) + '">' +
    '<div class="row-between"><div class="row">' +
    '<strong>第 ' + num(page.page_number, 0) + ' 页</strong>' +
    badge(page.is_scanned ? 'warn' : 'ok', page.is_scanned ? '扫描页' : '原生页') +
    badge(page.has_text_layer ? 'info' : 'muted', page.has_text_layer ? ('有文字层 ' + num(page.text_layer_chars, 0) + ' 字') : '无文字层') +
    badge('muted', '识别状态：' + (page.ocr_status || 'pending')) +
    (preprocessing ? badge('info', '已预处理') : '') +
    '</div><div class="btn-row">' +
    '<button type="button" class="btn btn-sm" data-act="material-preview" data-material="' + num(material.id, 0) +
    '" data-page="' + num(page.page_number, 1) + '">原图预览</button>' +
    '<button type="button" class="btn btn-sm btn-primary" data-act="page-save" data-material="' + num(material.id, 0) +
    '" data-page="' + num(page.page_number, 1) + '">保存页质量标记</button>' +
    '</div></div>' +
    '<div class="checks">' + flagBoxes + '</div>' +
    '<div class="row-between" style="margin-top:6px"><div class="checks">' + ops + '</div>' +
    '<button type="button" class="btn btn-sm btn-warn" data-act="preprocess-run" data-material="' + num(material.id, 0) +
    '" data-page="' + num(page.page_number, 1) + '">生成识别用副本（原件不动）</button></div>' +
    '</div>'
}

function materialCard(material) {
  const pages = Array.isArray(material.pages) ? material.pages : []
  const flags = Array.isArray(material.quality_flags) ? material.quality_flags : []
  const isPhoto = material.kind === 'photo'
  const info = '<div class="row">' +
    badge('muted', '#' + num(material.id, 0)) +
    badge('info', labelOf(state.cfg.material_kind_labels, material.kind)) +
    badge('muted', 'media=' + esc(material.media_type || 'other')) +
    badge('muted', '页数 ' + num(material.page_count, 0)) +
    badge('muted', 'sort=' + num(material.sort_order, 0)) +
    (material.sha256 ? badge('muted', 'sha256 ' + String(material.sha256).slice(0, 12) + '…') : '') +
    (isPhoto ? badge('warn', '部位照片：不参与识别') : '') +
    flags.map((flag) => badge('warn', labelOf(state.cfg.quality_labels, flag))).join('') +
    '</div>'

  let body = ''
  if (isPhoto) {
    body = '<div class="callout callout-warn">部位照片不进入文字识别流程；系统不读取照片内容，' +
      '只按你为该照片选择的“目标位置”插入文稿（N-11/R-55）。</div>' +
      '<div class="grid-2" style="margin-top:8px">' +
      '<div class="src-cell"><span class="k">目标位置</span><span class="v">' +
      (material.position_key ? esc(labelOf(positionLabelMap(), material.position_key))
        : '<span class="value-empty">尚未指定（会触发 PHOTO_POSITION_MISSING）</span>') + '</span></div>' +
      '<div class="src-cell"><span class="k">照片说明（用户填写）</span><span class="v">' + valHtml(material.photo_caption) + '</span></div>' +
      '</div>'
  } else {
    body = pages.length
      ? pages.map((page) => pageEditor(material, page)).join('')
      : '<div class="empty-note">该材料尚无页级记录（后端 probe 后写入 material_pages）。</div>'
  }

  return '<div class="material-item">' +
    '<div class="material-head"><div><strong>' + esc(material.filename || '未命名文件') + '</strong>' +
    '<div class="small muted">原件路径：' + valHtml(material.original_path) + '</div></div>' + info + '</div>' +
    '<div class="material-body">' + body + '</div>' +
    (isPhoto ? '' : '<div class="material-body"><div class="row-between">' +
      '<span class="hint">预处理只生成 derived/ 下的识别用副本，原件永不修改（R-19/N-8）。</span>' +
      '<button type="button" class="btn btn-sm" data-act="preprocess-all" data-material="' + num(material.id, 0) +
      '">整份材料预处理（默认自动对比度）</button></div></div>') +
    '</div>'
}

function previewBox() {
  if (!state.preview) return ''
  const url = api.materialFileUrl(state.caseId, state.preview.materialId, state.preview.page, 'original')
  return '<div class="card"><div class="card-head"><div class="card-title">原图预览' +
    '<span class="badge badge-muted">GET /api/cases/{id}/materials/{material_id}/file?page=N</span></div>' +
    '<div class="btn-row"><a class="btn btn-sm" href="' + esc(url) + '" target="_blank" rel="noopener">在新窗口打开</a>' +
    '<button type="button" class="btn btn-sm" data-act="preview-close">关闭预览</button></div></div>' +
    '<div class="card-body"><p class="small muted">' + esc(materialName(state.preview.materialId)) +
    ' 第 ' + num(state.preview.page, 1) + ' 页（原件只读，不做任何内容识别）</p>' +
    '<img src="' + esc(url) + '" alt="材料原图预览" style="max-width:100%;border:1px solid var(--border);border-radius:6px" /></div></div>'
}

function materialsPanel() {
  const cfg = state.cfg
  const materials = caseMaterials()
  const kindOptions = cfg.material_kind_order.filter((kind) => kind !== 'photo')
    .map((kind) => '<option value="' + esc(kind) + '">' + esc(labelOf(cfg.material_kind_labels, kind)) + '</option>').join('')
  const mediaOptions = cfg.media_types.map((type) => '<option value="' + esc(type) + '">' + esc(type) + '</option>').join('')
  const materialOptions = '<option value="">全部材料</option>' + materials.map((item) =>
    '<option value="' + num(item.id, 0) + '"' + (String(state.ocrFilterMaterial) === String(item.id) ? ' selected' : '') + '>' +
    esc(materialOptionLabel(item)) + '</option>').join('')
  const engineOptions = cfg.engines.map((engine) =>
    '<option value="' + esc(engine.name) + '"' + (engine.name === cfg.ocr_default_engine ? ' selected' : '') +
    (engine.available ? '' : ' disabled') + '>' + esc(engine.name) + (engine.available ? '（可用）' : '（不可用/' + esc(engine.detail || '未安装') + '）') +
    '</option>').join('')

  const total = num(state.ocrBlocks.total, 0)
  const pages = Math.max(1, Math.ceil(total / Math.max(1, num(state.ocrBlocks.page_size, 20))))
  const blocks = (state.ocrBlocks.items || []).map((block) => {
    const low = Number(block.confidence) < cfg.ocr_low_confidence
    return '<div class="block ' + (low ? 'is-low' : '') + (block.is_handwritten ? ' is-hand' : '') + '">' +
      '<div class="block-text">' + valHtml(block.text) + '</div>' +
      '<div class="block-meta">' +
      '<span>材料：' + esc(materialName(block.material_id)) + '</span>' +
      '<span>第 ' + num(block.page_number, 0) + ' 页</span>' +
      '<span>块 ' + num(block.block_index, 0) + '</span>' +
      '<span>置信度 ' + confidenceText(block.confidence) + '</span>' +
      '<span>引擎 ' + esc(block.engine || '未记录') + '</span>' +
      (block.is_handwritten ? '<span>' + badge('info', '手写') + '</span>' : '') +
      '<span>栏目提示 ' + valHtml(block.column_hint) + '</span>' +
      '<span>bbox ' + bboxText(block.bbox) + '</span>' +
      '</div></div>'
  }).join('')

  return previewBox() +
    '<div class="card" id="panel-materials"><div class="card-head"><div class="card-title">材料清单' +
    '<span class="badge badge-muted">GET/POST /api/cases/{id}/materials</span></div>' +
    '<span class="small muted">必收材料：' + cfg.required_material_kinds.map((kind) =>
      esc(labelOf(cfg.material_kind_labels, kind))).join('、') + '</span></div>' +
    '<div class="card-body">' +
    '<div class="form-grid">' +
    '<div class="field"><label for="m-kind">材料类型</label><select id="m-kind" class="select" data-upload="kind">' + kindOptions + '</select></div>' +
    '<div class="field"><label for="m-media">媒体类型（留空按扩展名推断）</label><select id="m-media" class="select" data-upload="media_type">' +
    '<option value="">自动推断</option>' + mediaOptions + '</select></div>' +
    '<div class="field"><label for="m-sort">顺序 sort_order</label><input id="m-sort" class="input" type="number" min="0" max="9999" value="0" data-upload="sort_order" /></div>' +
    '<div class="field"><label for="m-clarity">清晰程度备注</label><input id="m-clarity" class="input" maxlength="1000" data-upload="clarity_note" /></div>' +
    '</div>' +
    '<div class="field" style="margin-top:8px"><label for="m-file">选择文件（浏览器 FileReader 读为 base64 上传，支持 PDF/JPG/PNG/DOCX/JSON）</label>' +
    '<input id="m-file" class="input" type="file" multiple data-upload="files" /></div>' +
    '<div class="form-actions"><button type="button" class="btn btn-primary" data-act="material-upload">上传并登记材料</button>' +
    '<span class="hint">上传即写入 originals/ 原件目录，永不覆盖、永不修改（N-8）。部位照片请使用下方“部位照片”区块。</span></div>' +
    '</div>' +
    '<div class="card-body">' + (materials.filter((item) => item.kind !== 'photo').length
      ? '<div class="material-list">' + materials.filter((item) => item.kind !== 'photo').map(materialCard).join('') + '</div>'
      : '<div class="empty-note">尚无扫描类材料。</div>') + '</div></div>' +

    '<div class="card"><div class="card-head"><div class="card-title">运行识别' +
    '<span class="badge badge-muted">POST /api/cases/{id}/ocr</span></div></div><div class="card-body">' +
    '<div class="form-grid">' +
    '<div class="field"><label for="ocr-engine">识别引擎</label><select id="ocr-engine" class="select" data-ocr="engine">' + engineOptions + '</select></div>' +
    '<div class="field"><label for="ocr-material">识别范围</label><select id="ocr-material" class="select" data-ocr="material_id">' + materialOptions + '</select></div>' +
    '<div class="field"><label>重复识别</label><label class="field-flag"><input type="checkbox" data-ocr="force" /> force=true（覆盖已有识别结果）</label></div>' +
    '</div><div class="form-actions"><button type="button" class="btn btn-primary" data-act="ocr-run">运行识别</button>' +
    '<span class="hint">照片材料（kind=photo）由后端跳过并计入 skipped_photo_ids；本工作台也不会对其调用任何识别接口（N-11）。</span></div>' +
    '<div class="callout callout-info" style="margin-top:8px">OCR 分数只用于安排核对优先级，不能作为结论（需求第四章）。</div>' +
    '</div></div>' +

    '<div class="card"><div class="card-head"><div class="card-title">识别结果' +
    '<span class="badge badge-muted">GET /api/cases/{id}/ocr-blocks</span></div>' +
    '<span class="small muted">共 ' + total + ' 块</span></div><div class="card-body">' +
    '<div class="row">' +
    '<label class="inline-field">材料筛选 <select class="select select-sm" data-act="ocr-filter">' + materialOptions + '</select></label>' +
    '<label class="inline-field">每页 <select class="select select-sm" data-act="ocr-page-size">' +
    [10, 20, 50, 100].map((size) => '<option value="' + size + '"' + (num(state.ocrPageSize, 20) === size ? ' selected' : '') + '>' + size + '</option>').join('') +
    '</select></label>' +
    '<button type="button" class="btn btn-sm" data-act="ocr-blocks-reload">刷新识别结果</button>' +
    '</div>' +
    '<div class="block-list" style="margin-top:8px">' + (blocks ||
      '<div class="empty-note">暂无识别结果。可先选择材料运行识别（离线环境使用 sidecar 引擎）。</div>') + '</div>' +
    '<div class="pager"><span class="pager-info">第 ' + num(state.ocrBlocks.page, 1) + ' / ' + pages + ' 页</span>' +
    '<button type="button" class="btn btn-sm" data-act="ocr-page" data-page="' + (num(state.ocrBlocks.page, 1) - 1) +
    '"' + (num(state.ocrBlocks.page, 1) <= 1 ? ' disabled' : '') + '>上一页</button>' +
    '<button type="button" class="btn btn-sm" data-act="ocr-page" data-page="' + (num(state.ocrBlocks.page, 1) + 1) +
    '"' + (num(state.ocrBlocks.page, 1) >= pages ? ' disabled' : '') + '>下一页</button></div>' +
    '</div></div>'
}

function positionLabelMap() {
  const map = {}
  state.cfg.photo_positions.forEach((item) => { map[item.key] = item.label })
  return map
}

function photoPositionOptions(selected, emptyLabel) {
  const cfg = state.cfg
  const groups = cfg.photo_section_order.map((section) => {
    const items = cfg.photo_positions.filter((item) => item.section === section)
    if (!items.length) return ''
    return '<optgroup label="' + esc(labelOf(cfg.photo_section_labels, section)) + '">' +
      items.map((item) => '<option value="' + esc(item.key) + '"' +
        (String(selected) === String(item.key) ? ' selected' : '') + '>' + esc(item.label) +
        (item.wide ? '（宽幅）' : '') + '</option>').join('') + '</optgroup>'
  }).join('')
  const head = emptyLabel === null ? '' : '<option value="">' + esc(emptyLabel || '请选择目标位置') + '</option>'
  return head + groups
}

function photoThumb(material) {
  const url = api.materialFileUrl(state.caseId, material.id, 1, 'original')
  return '<div class="thumb" data-photo="' + num(material.id, 0) + '">' +
    '<img src="' + esc(url) + '" alt="部位照片缩略图（仅浏览器显示，系统不读取内容）" loading="lazy" />' +
    '<div class="thumb-meta">' +
    '<span>#' + num(material.id, 0) + ' ' + esc(material.filename || '未命名') + '</span>' +
    '<span>' + (material.position_key
      ? badge('ok', labelOf(positionLabelMap(), material.position_key))
      : badge('danger', '未指派位置')) + '</span>' +
    '<label class="field"><span class="small">目标位置（你选择，系统只按此插入）</span>' +
    '<select class="select select-sm" data-photo-pick="' + num(material.id, 0) + '">' +
    photoPositionOptions(material.position_key, '— 尚未指定 —') + '</select></label>' +
    '<label class="field"><span class="small">照片说明</span>' +
    '<input class="input input-sm" maxlength="200" data-photo-caption="' + num(material.id, 0) +
    '" value="' + esc(material.photo_caption || '') + '" /></label>' +
    '<label class="field"><span class="small">顺序 sort_order</span>' +
    '<input class="input input-sm" type="number" min="0" max="9999" data-photo-sort="' + num(material.id, 0) +
    '" value="' + num(material.sort_order, 0) + '" /></label>' +
    '<div class="btn-row">' +
    '<button type="button" class="btn btn-sm btn-primary" data-act="photo-save" data-material="' + num(material.id, 0) + '">保存位置/说明/顺序</button>' +
    '<button type="button" class="btn btn-sm" data-act="photo-clear" data-material="' + num(material.id, 0) + '">清空位置</button>' +
    '</div></div></div>'
}

function photoPanel() {
  const cfg = state.cfg
  const materials = caseMaterials()
  const photos = materials.filter((item) => item.kind === 'photo')
  const assignedIds = new Set()
  const slots = state.photoSlots || []
  const slotMeta = state.photoSlotMeta || {}

  const slotHtml = slots.map((slot) => {
    const items = Array.isArray(slot.photos) ? slot.photos : []
    items.forEach((photo) => assignedIds.add(num(photo.material_id, 0)))
    const key = slot.position_key || slot.key || ''
    // 后端返回 missing=true 表示该栏位本次没有照片（N-12：保留栏位并写占位文字，不顶替）
    const missing = slot.missing === undefined ? items.length === 0 : Boolean(slot.missing)
    return '<div class="photo-slot' + (missing ? '' : ' is-filled') + '"><div class="photo-slot-head">' +
      '<span>' + esc(slot.section_label || labelOf(cfg.photo_section_labels, slot.section)) + ' · ' + esc(slot.label) + '</span>' +
      '<span class="row">' + badge('muted', '锚点 {{ ' + (slot.anchor || ('photo_' + key)) + ' }}') +
      (slot.wide ? badge('info', '宽幅 ≤ ' + cfg.photo_wide_max_width_cm + ' cm') : badge('muted', '≤ ' + cfg.photo_max_width_cm + ' cm')) +
      (items.length ? badge('ok', items.length + ' 张') : badge('muted', '空栏位 → ' + (slotMeta.placeholder_text || cfg.photo_placeholder))) +
      '</span></div>' +
      '<div class="photo-slot-body">' + (items.length
        ? '<div class="row">' + items.map((photo) =>
          '<span class="badge badge-ok">#' + num(photo.material_id, 0) + ' ' + esc(photo.filename || '') +
          ' · sort ' + num(photo.sort_order, 0) +
          (photo.caption ? ' · ' + esc(photo.caption) : '') + '</span>').join('') + '</div>'
        : '<span class="muted small">该栏位本次没有照片，文稿中写入“' + esc(slotMeta.placeholder_text || cfg.photo_placeholder) + '”，不会用其他栏位顶替（N-12）。</span>') +
      '</div></div>'
  }).join('')

  const unassigned = photos.filter((item) => !assignedIds.has(num(item.id, 0)) && !item.position_key)
  const unassignedHtml = unassigned.length
    ? '<div class="callout callout-danger">有 ' + unassigned.length +
      ' 张照片尚未指定目标位置，不会进入文稿，并会触发校核项 <code>PHOTO_POSITION_MISSING</code>（warning）：' +
      unassigned.map((item) => esc('#' + item.id + ' ' + (item.filename || ''))).join('、') + '</div>'
    : '<div class="callout callout-ok">本次上传的 ' + photos.length + ' 张照片都已指定目标位置。</div>'
  const slotSummary = (slotMeta.assigned_count !== undefined && state.photoSlotMeta)
    ? '<div class="row" style="margin:6px 0">' + badge('ok', '已指派 ' + num(slotMeta.assigned_count, 0) + ' 张照片') +
      // 实测语义：missing_count = 本次没有照片的**栏位数**（assigned_count 才是照片张数）。
      badge('muted', '空栏位 ' + num(slotMeta.missing_count, 0) + ' 个（写“' +
        esc(slotMeta.placeholder_text || cfg.photo_placeholder) + '”）') +
      badge('muted', '栏位 ' + slots.length + ' 个') +
      (slotMeta.photo_policy ? '<span class="small muted">服务端策略：' + esc(slotMeta.photo_policy) + '</span>' : '') +
      '</div>'
    : ''

  return '<div class="card" id="panel-photo"><div class="card-head"><div class="card-title">部位照片（N-11）' +
    '<span class="badge badge-danger">系统不读取照片内容，只按你选择的位置插入</span></div>' +
    '<span class="badge badge-muted">PATCH .../placement · GET .../photo-slots</span></div>' +
    '<div class="card-body">' +
    '<div class="callout callout-warn"><strong>重要说明：</strong>本系统不对部位照片做识别、描述、分类或推断，' +
    '不运行 OCR、不调用视觉模型，也不生成任何来自照片的图像结论（N-11/R-55）。' +
    '缩略图只由浏览器直接显示给你查看；位置完全由你从目录中选择。' +
    '报告模板中的照片栏位为 <code>{{ photo_&lt;position_key&gt; }}</code>。</div>' +
    '<div class="form-grid" style="margin-top:10px">' +
    '<div class="field"><label for="p-pos">上传时的初始目标位置（可留空，稍后逐张调整）</label>' +
    '<select id="p-pos" class="select" data-photo-upload="position_key">' + photoPositionOptions('', '— 尚未指定 —') + '</select></div>' +
    '<div class="field"><label for="p-caption">照片说明（用户填写，非识别结果）</label>' +
    '<input id="p-caption" class="input" maxlength="200" data-photo-upload="photo_caption" /></div>' +
    '<div class="field"><label for="p-sort">顺序 sort_order</label>' +
    '<input id="p-sort" class="input" type="number" min="0" max="9999" value="0" data-photo-upload="sort_order" /></div>' +
    '</div>' +
    '<div class="field" style="margin-top:8px"><label for="p-file">选择照片文件（可多选；只上传字节，不分析内容）</label>' +
    '<input id="p-file" class="input" type="file" accept="image/*" multiple data-photo-upload="files" /></div>' +
    '<div class="form-actions"><button type="button" class="btn btn-primary" data-act="photo-upload">上传部位照片</button>' +
    '<button type="button" class="btn" data-act="photo-slots-reload">刷新栏位视图</button>' +
    '<span class="hint">同一栏位最多 ' + cfg.photo_max_per_position + ' 张，多张按 sort_order 排列。</span></div>' +
    '</div>' +
    '<div class="card-body">' + (photos.length
      ? '<div class="thumb-grid">' + photos.map(photoThumb).join('') + '</div>'
      : '<div class="empty-note">尚未上传部位照片。</div>') + '</div>' +
    '<div class="card-body">' + unassignedHtml + '</div>' +
    '<div class="card-body"><div class="section-title">照片栏位视图（已指派 / 未指派）</div>' +
    '<div class="small muted" style="margin:6px 0">未指派的照片不会出现在任何栏位中（按 PHOTO_POSITION_DEFINITIONS 顺序展示）。</div>' +
    slotSummary +
    (slotHtml || '<div class="empty-note">尚无栏位数据（需要后端实现 list_photo_slots）。</div>') +
    '</div></div>'
}

/* ==========================================================================
 * 11. 字段核对面板（R-15：原文/来源文件/页码/bbox/识别结果/确认值/规范化文字/规则/复核状态）
 * ========================================================================== */

function conflictHtml(field) {
  const conflict = Array.isArray(field.conflict) ? field.conflict : []
  if (!conflict.length) return ''
  const rows = conflict.map((item) => {
    const value = item.value !== undefined ? item.value : (item.raw_text !== undefined ? item.raw_text : item.text)
    const materialId = item.material_id !== undefined ? item.material_id : item.source_material_id
    const page = item.page !== undefined ? item.page : (item.source_page !== undefined ? item.source_page : '')
    return '<tr><td>' + valHtml(value) + '</td><td>' + valHtml(materialId ? materialName(materialId) : '') + '</td>' +
      '<td>' + (page === '' || page === null ? '<span class="value-empty">未记录</span>' : esc(String(page))) + '</td>' +
      '<td>' + bboxText(item.bbox || item.source_bbox) + '</td>' +
      '<td>' + confidenceText(item.confidence) + '</td>' +
      '<td>' + valHtml(item.block_index !== undefined ? ('块 ' + item.block_index) : '') + '</td></tr>'
  }).join('')
  return '<div class="conflict-box"><h4>⚠ 冲突：不同来源取值不一致（R-44，未静默选择任何一方）</h4>' +
    '<div class="table-wrap"><table class="table"><thead><tr>' +
    '<th>取值</th><th>来源文件</th><th>页码</th><th>原页位置 bbox</th><th>识别结果</th><th>块</th>' +
    '</tr></thead><tbody>' + rows + '</tbody></table></div></div>'
}

function fieldCard(field) {
  const isUnclear = field.status === 'unclear'
  const isPending = field.review_state === 'pending'
  const rules = Array.isArray(field.applied_rule_codes) ? field.applied_rule_codes : []
  const cls = isUnclear ? 'is-unclear' : (isPending ? 'is-pending' : '')
  const flag = isUnclear
    ? '<span class="badge badge-unclear">待辨认/冲突</span>'
    : (isPending ? '<span class="badge badge-warn">待核对</span>' : '')

  return '<div class="field-card ' + cls + '" data-field-card="' + esc(field.field_key) + '">' +
    '<div class="field-card-head"><div class="field-card-title">' +
    '<span>' + esc(field.field_label || field.field_key) + '</span>' +
    '<span class="badge badge-muted mono">' + esc(field.field_key) + '</span>' +
    flag + statusBadge(field.status) + reviewBadge(field.review_state) +
    (field.confidence !== null && field.confidence !== undefined && Number(field.confidence) < state.cfg.ocr_low_confidence
      ? '<span class="badge badge-danger">低置信度</span>' : '') +
    '</div><div class="btn-row">' +
    '<button type="button" class="btn btn-sm btn-primary" data-act="field-save" data-key="' + esc(field.field_key) + '">保存核对结果</button>' +
    '</div></div>' +
    '<div class="field-card-body">' +
    '<div class="src-grid">' +
    '<div class="src-cell"><span class="k">① 原文 raw_text</span><span class="v">' + valHtml(field.raw_text) + '</span></div>' +
    '<div class="src-cell"><span class="k">② 来源文件</span><span class="v">' +
    (field.source_material_id ? esc(materialName(field.source_material_id)) : '<span class="value-empty">未记录</span>') +
    (field.source_block_id ? '<br /><span class="small muted">识别块 #' + num(field.source_block_id, 0) + '</span>' : '') + '</span></div>' +
    '<div class="src-cell"><span class="k">③ 页码</span><span class="v">' +
    (field.source_page ? esc(String(field.source_page)) : '<span class="value-empty">未记录</span>') + '</span></div>' +
    '<div class="src-cell"><span class="k">④ 原页位置 bbox（归一化 0..1）</span><span class="v">' + bboxText(field.source_bbox) + '</span></div>' +
    '<div class="src-cell"><span class="k">⑤ 识别结果 confidence</span><span class="v">' + confidenceText(field.confidence) + '</span></div>' +
    '<div class="src-cell"><span class="k">⑥ 确认后的值 confirmed_value</span><span class="v">' + valHtml(field.confirmed_value) + '</span></div>' +
    '<div class="src-cell"><span class="k">⑦ 规范化文字 normalized_text</span><span class="v">' + valHtml(field.normalized_text) + '</span></div>' +
    '<div class="src-cell"><span class="k">⑧ 使用的规则</span><span class="v">' +
    (rules.length ? rules.map((code) => '<span class="badge badge-info mono">' + esc(code) + '</span>').join(' ') : '<span class="value-empty">无</span>') +
    (field.rule_note ? '<div class="small muted">' + esc(field.rule_note) + '</div>' : '') + '</span></div>' +
    '<div class="src-cell"><span class="k">⑨ 复核状态 review_state</span><span class="v">' + reviewBadge(field.review_state) + '</span></div>' +
    '</div>' + conflictHtml(field) +
    '<div class="edit-grid">' +
    '<label class="field"><span class="small">确认后的值（人工核对后可改）</span>' +
    '<input class="input input-sm" data-field-edit="confirmed_value" value="' + esc(field.confirmed_value || '') + '" /></label>' +
    '<label class="field"><span class="small">规范化文字</span>' +
    '<input class="input input-sm" data-field-edit="normalized_text" value="' + esc(field.normalized_text || '') + '" /></label>' +
    '<label class="field"><span class="small">单位</span>' +
    '<input class="input input-sm" data-field-edit="unit" value="' + esc(field.unit || '') + '" /></label>' +
    '<label class="field"><span class="small">信息状态（五态不可互换）</span>' +
    '<select class="select select-sm" data-field-edit="status">' +
    state.cfg.field_status_order.map((key) => '<option value="' + esc(key) + '"' +
      (field.status === key ? ' selected' : '') + '>' + esc(labelOf(state.cfg.field_status_labels, key)) + '</option>').join('') +
    '</select></label>' +
    '<label class="field"><span class="small">复核状态</span>' +
    '<select class="select select-sm" data-field-edit="review_state">' +
    ['pending', 'confirmed'].map((key) => '<option value="' + key + '"' +
      (field.review_state === key ? ' selected' : '') + '>' + esc(labelOf(state.cfg.review_state_labels, key)) + '</option>').join('') +
    '</select></label>' +
    '<label class="field"><span class="small">修改备注（PATCH note）</span>' +
    '<input class="input input-sm" data-field-edit="note" placeholder="如：与记录本第 2 页核对一致" /></label>' +
    '</div></div></div>'
}

function extractCard() {
  const materials = caseMaterials()
  const selectable = materials.filter((item) => item.kind !== 'photo')
  const boxes = selectable.map((item) =>
    '<label class="field-flag"><input type="checkbox" data-extract-material="' + num(item.id, 0) + '" />' +
    esc(materialOptionLabel(item)) + '</label>').join('')
  return '<div class="card"><div class="card-head"><div class="card-title">提取字段与损伤' +
    '<span class="badge badge-muted">POST /api/cases/{id}/extract</span></div></div><div class="card-body">' +
    '<div class="checks">' + (boxes || '<span class="muted">请先上传扫描类材料。</span>') + '</div>' +
    '<div class="row" style="margin-top:8px">' +
    '<label class="field-flag"><input type="checkbox" data-extract="replace_existing" checked /> 替换已有字段（replace_existing=true）</label>' +
    '<label class="field-flag"><input type="checkbox" data-extract="use_llm"' + (state.apiKey ? '' : ' disabled') + ' /> 使用模型辅助（use_llm，需要已填写密钥）</label>' +
    '</div><div class="form-actions"><button type="button" class="btn btn-primary" data-act="extract-run">运行提取</button>' +
    '<span class="hint">未勾选材料表示提取全部材料；照片材料不参与提取（N-11）。</span></div>' +
    '</div></div>'
}

function fieldsPanel() {
  const fields = caseFields()
  if (!fields.length) {
    return '<div class="card"><div class="card-head"><div class="card-title">字段核对' +
      '<span class="badge badge-muted">GET /api/cases/{id}/fields</span></div></div>' +
      '<div class="card-body"><div class="empty-note">尚无字段记录。请在上方运行“提取字段与损伤”。<br />' +
      '提示：没有来源的字段不会被写入，缺失由校核清单报告（不会显示为“未见异常”）。</div></div></div>'
  }
  const groups = {}
  fields.forEach((field) => {
    const key = field.field_group || 'other'
    if (!groups[key]) groups[key] = []
    groups[key].push(field)
  })
  const order = state.cfg.field_group_order.concat(Object.keys(groups).filter((key) => !state.cfg.field_group_order.includes(key)))
  const body = order.filter((key) => groups[key]).map((key) =>
    '<div class="stack"><div class="section-title">' + esc(labelOf(state.cfg.field_group_labels, key)) +
    '<span class="badge badge-muted">' + groups[key].length + ' 项</span></div>' +
    groups[key].map(fieldCard).join('') + '</div>').join('')

  const unclear = fields.filter((field) => field.status === 'unclear').length
  const pending = fields.filter((field) => field.review_state === 'pending').length
  return '<div class="card"><div class="card-head"><div class="card-title">字段核对（R-15 九项来源同屏）' +
    '<span class="badge badge-muted">GET/PATCH /api/cases/{id}/fields/{field_key}</span></div>' +
    '<div class="row">' + badge('danger', '待辨认 ' + unclear) + badge('warn', '待核对 ' + pending) +
    '<button type="button" class="btn btn-sm" data-act="case-reload">重新加载字段</button></div></div>' +
    '<div class="card-body">' +
    '<div class="callout callout-warn">空白栏位一律显示“<strong>未记录</strong>”，系统<strong>不会</strong>写成“未见异常”（N-5）。' +
    '状态为 unclear 的字段带“待辨认/冲突”标记，冲突双方来源同时展示（R-44）。' +
    '信息状态五态（明确记载 / 未记录 / 明确未检 / 明确阴性所见 / 待辨认或有冲突）不可相互替代（R-17）。</div>' +
    '<div class="stack" style="margin-top:10px">' + body + '</div></div></div>'
}

/* ==========================================================================
 * 12. 损伤面板（R-16/R-36：逐条保存，绝不合并）
 * ========================================================================== */

const INJURY_EDIT_FIELDS = [
  ['seq', '顺序', 'number'],
  ['body_part', '部位', 'text'],
  ['side', '左右侧', 'text'],
  ['injury_type', '类型', 'text'],
  ['count', '数量', 'text'],
  ['length', '长度', 'text'],
  ['width', '宽度', 'text'],
  ['size_unit', '单位', 'text'],
  ['morphology', '形态', 'text'],
  ['other_traits', '其他已记录特征', 'text'],
  ['raw_text', '原文', 'text'],
]

function injuryRow(injury, index, total) {
  const inputs = INJURY_EDIT_FIELDS.map(([key, label, type]) =>
    '<label class="field"><span class="small">' + esc(label) + '</span>' +
    '<input class="input input-sm" type="' + type + '" data-injury-edit="' + key + '" value="' +
    esc(injury[key] === null || injury[key] === undefined ? '' : injury[key]) + '"' +
    (key === 'seq' ? ' min="0" max="999"' : '') + ' /></label>').join('')

  return '<div class="material-item" data-injury-row="' + num(injury.id, 0) + '">' +
    '<div class="material-head"><div class="row">' +
    '<strong>损伤 #' + num(injury.id, 0) + '</strong>' + statusBadge(injury.status) + reviewBadge(injury.review_state) +
    badge('muted', '序号 ' + num(injury.seq, 0)) +
    '</div><div class="table-actions">' +
    '<button type="button" class="btn btn-sm" data-act="injury-move" data-id="' + num(injury.id, 0) + '" data-dir="-1"' +
    (index === 0 ? ' disabled' : '') + '>上移</button>' +
    '<button type="button" class="btn btn-sm" data-act="injury-move" data-id="' + num(injury.id, 0) + '" data-dir="1"' +
    (index === total - 1 ? ' disabled' : '') + '>下移</button>' +
    '<button type="button" class="btn btn-sm btn-primary" data-act="injury-save" data-id="' + num(injury.id, 0) + '">保存</button>' +
    '<button type="button" class="btn btn-sm btn-danger" data-act="injury-delete" data-id="' + num(injury.id, 0) + '">删除</button>' +
    '</div></div>' +
    '<div class="material-body">' +
    '<div class="edit-grid">' + inputs +
    '<label class="field"><span class="small">信息状态</span><select class="select select-sm" data-injury-edit="status">' +
    state.cfg.field_status_order.map((key) => '<option value="' + esc(key) + '"' +
      (injury.status === key ? ' selected' : '') + '>' + esc(labelOf(state.cfg.field_status_labels, key)) + '</option>').join('') +
    '</select></label>' +
    '<label class="field"><span class="small">复核状态</span><select class="select select-sm" data-injury-edit="review_state">' +
    ['pending', 'confirmed'].map((key) => '<option value="' + key + '"' +
      (injury.review_state === key ? ' selected' : '') + '>' + esc(labelOf(state.cfg.review_state_labels, key)) + '</option>').join('') +
    '</select></label>' +
    '</div>' +
    '<div class="src-grid" style="margin-top:8px">' +
    '<div class="src-cell"><span class="k">来源文件</span><span class="v">' +
    (injury.source_material_id ? esc(materialName(injury.source_material_id)) : '<span class="value-empty">未记录</span>') + '</span></div>' +
    '<div class="src-cell"><span class="k">页码</span><span class="v">' +
    (injury.source_page ? esc(String(injury.source_page)) : '<span class="value-empty">未记录</span>') + '</span></div>' +
    '<div class="src-cell"><span class="k">原页位置 bbox</span><span class="v">' + bboxText(injury.source_bbox) + '</span></div>' +
    '<div class="src-cell"><span class="k">识别结果 confidence</span><span class="v">' + confidenceText(injury.confidence) + '</span></div>' +
    '<div class="src-cell"><span class="k">规范化文字</span><span class="v">' + valHtml(injury.normalized_text) + '</span></div>' +
    '<div class="src-cell"><span class="k">使用的规则</span><span class="v">' +
    ((Array.isArray(injury.applied_rule_codes) ? injury.applied_rule_codes : []).length
      ? injury.applied_rule_codes.map((code) => '<span class="badge badge-info mono">' + esc(code) + '</span>').join(' ')
      : '<span class="value-empty">无</span>') + '</span></div>' +
    '</div>' +
    '<label class="field" style="margin-top:8px"><span class="small">修改备注</span>' +
    '<input class="input input-sm" data-injury-edit="note" /></label>' +
    '</div></div>'
}

function injuriesPanel() {
  const injuries = caseInjuries()
  const createFields = [['body_part', '部位'], ['side', '左右侧'], ['injury_type', '类型'], ['count', '数量'],
    ['length', '长度'], ['width', '宽度'], ['size_unit', '单位'], ['morphology', '形态'],
    ['other_traits', '其他已记录特征'], ['raw_text', '原文']]
  const createInputs = createFields.map(([key, label]) =>
    '<label class="field"><span class="small">' + esc(label) + '</span>' +
    '<input class="input input-sm" data-injury-new="' + key + '" /></label>').join('')

  return '<div class="card"><div class="card-head"><div class="card-title">损伤逐条整理（R-16/R-36）' +
    '<span class="badge badge-muted">GET/POST/PATCH/DELETE /api/cases/{id}/injuries</span></div>' +
    '<div class="row">' + badge('muted', injuries.length + ' 条') +
    '<button type="button" class="btn btn-sm" data-act="case-reload">刷新</button></div></div>' +
    '<div class="card-body"><div class="callout callout-info">每条记录独立一行保存，系统不会把不同记录行的部位与尺寸合并；' +
    '调整顺序使用 <code>POST .../injuries/reorder</code> 并携带 <code>ids</code>（IdListRequest）。</div></div>' +
    '<div class="card-body"><div class="stack">' + (injuries.length
      ? injuries.map((injury, index) => injuryRow(injury, index, injuries.length)).join('')
      : '<div class="empty-note">尚无损伤记录。</div>') + '</div></div>' +
    '<div class="card-body"><div class="section-title">新增损伤条目</div>' +
    '<div class="edit-grid">' + createInputs + '</div>' +
    '<div class="form-actions"><button type="button" class="btn btn-primary" data-act="injury-create">新增</button>' +
    '<span class="hint">至少填写一个损伤字段；未填写的字段显示“未记录”，不会推断。</span></div></div></div>'
}

/* ==========================================================================
 * 13. 术语整理与规范文字对照
 * ========================================================================== */

function normalizeCorrespondence() {
  const rows = []
  caseFields().forEach((field) => rows.push({
    target_type: 'field', field_key: field.field_key,
    label: field.field_label || field.field_key,
    raw: field.raw_text, normalized: field.normalized_text, status: field.status,
    rules: field.applied_rule_codes || [], note: field.rule_note,
  }))
  caseInjuries().forEach((injury) => rows.push({
    target_type: 'injury', field_key: 'injury#' + num(injury.id, 0),
    label: '损伤 #' + num(injury.id, 0) + ' ' + (injury.body_part || ''),
    raw: injury.raw_text, normalized: injury.normalized_text, status: injury.status,
    rules: injury.applied_rule_codes || [], note: injury.rule_note,
  }))
  if (!rows.length) return '<div class="empty-note">暂无字段或损伤，无法展示原文 ↔ 规范文字对应关系。</div>'
  return '<div class="table-wrap"><table class="table"><thead><tr>' +
    '<th>对象</th><th>原文</th><th>规范文字（当前）</th><th>替代规则</th><th>状态</th></tr></thead><tbody>' +
    rows.map((row) => '<tr><td>' + esc(row.label) + '<div class="small muted mono">' + esc(row.field_key) + '</div></td>' +
      '<td>' + valHtml(row.raw) + '</td><td>' + valHtml(row.normalized) + '</td>' +
      '<td>' + (row.rules.length ? row.rules.map((code) => '<span class="badge badge-info mono">' + esc(code) + '</span>').join(' ') : '<span class="value-empty">无</span>') +
      (row.note ? '<div class="small muted">' + esc(row.note) + '</div>' : '') + '</td>' +
      '<td>' + statusBadge(row.status) + '</td></tr>').join('') + '</tbody></table></div>'
}

function normalizePanel() {
  const cfg = state.cfg
  const result = state.normalizeResult
  const resultBox = result
    ? '<div class="callout callout-info"><strong>上次运行结果：</strong>' + esc(result.detail || '已完成') +
      '<div class="small" style="margin-top:4px">stage=' + esc(result.stage || 'normalize') + ' · ok=' + String(result.ok) + '</div>' +
      (result.payload ? '<details style="margin-top:6px"><summary>返回载荷 payload</summary><pre class="mono" style="max-height:220px;overflow:auto">' +
        jsonPretty(result.payload) + '</pre></details>' : '') + '</div>'
    : ''
  return '<div class="card"><div class="card-head"><div class="card-title">术语整理' +
    '<span class="badge badge-muted">POST /api/cases/{id}/normalize</span></div></div>' +
    '<div class="card-body">' +
    '<div class="form-grid">' +
    '<div class="field"><label for="n-target">整理对象 target</label><select id="n-target" class="select" data-normalize="target">' +
    ['all', 'fields', 'injuries'].map((key) => '<option value="' + key + '">' + ({ all: '全部', fields: '仅字段', injuries: '仅损伤' })[key] + '</option>').join('') +
    '</select></div>' +
    '<div class="field"><label for="n-version">规则版本 rule_version</label>' +
    '<input id="n-version" class="input" data-normalize="rule_version" value="' + esc(cfg.term_rule_version) + '" /></div>' +
    '<div class="field"><label>确认策略</label><label class="field-flag"><input type="checkbox" data-normalize="confirm_safe" />' +
    ' confirm_safe=true（把无需确认的结果直接标记为已确认）</label></div>' +
    '</div><div class="form-actions"><button type="button" class="btn btn-primary" data-act="normalize-run">运行术语整理</button>' +
    '<span class="hint">状态 unclear 与未命中规则的表达一律要求人工确认（R-23/R-34）。</span></div>' +
    (resultBox ? '<div style="margin-top:10px">' + resultBox + '</div>' : '') +
    '</div>' +
    '<div class="card-body"><div class="section-title">原文 ↔ 规范文字对应关系（R-4/R-30～R-37）</div>' +
    '<div style="margin-top:8px">' + normalizeCorrespondence() + '</div></div></div>'
}

/* ==========================================================================
 * 14. 校核清单面板
 * ========================================================================== */

function checklistItemHtml(item) {
  const severity = item.severity || 'info'
  const status = item.status || 'open'
  const cls = 'check-item ' + (severity === 'blocking' ? 'is-blocking' : ('is-' + severity)) +
    (status === 'resolved' ? ' is-resolved' : '') + (status === 'waived' ? ' is-waived' : '')
  const done = status !== 'open'
  return '<div class="' + cls + '" data-check-item="' + num(item.id, 0) + '">' +
    '<div class="check-head">' + severityBadge(severity) +
    '<span class="check-code">' + esc(item.code || '') + '</span>' +
    '<span class="check-title">' + esc(item.title || '') + '</span>' +
    badge(status === 'waived' ? 'info' : (status === 'resolved' ? 'ok' : 'muted'), labelOf(state.cfg.check_item_status_labels, status)) +
    (severity === 'blocking' && status === 'open' ? '<span class="badge badge-danger">会阻止生成待签发稿</span>' : '') +
    '</div>' +
    '<div class="check-detail">' + valHtml(item.detail) + '</div>' +
    (item.suggestion ? '<div class="check-suggest">建议：' + esc(item.suggestion) + '</div>' : '') +
    '<div class="small muted" style="margin-top:4px">类别：' + esc(labelOf(state.cfg.check_category_labels, item.category)) +
    ' · 目标：' + esc(item.target_type || '未记录') + ' #' + num(item.target_id, 0) +
    (item.document_id ? ' · 文稿 #' + num(item.document_id, 0) : '') +
    (item.resolved_by ? ' · 处理人：' + esc(item.resolved_by) : '') +
    (item.note ? ' · 说明：' + esc(item.note) : '') + '</div>' +
    (item.source && Object.keys(item.source).length
      ? '<details style="margin-top:4px"><summary class="small">来源 source</summary><pre class="mono" style="max-height:180px;overflow:auto">' +
        jsonPretty(item.source) + '</pre></details>' : '') +
    '<div class="check-actions">' +
    '<label class="inline-field"><span class="small">处理说明' + (done ? '' : '（豁免必填理由）') + '</span>' +
    '<input class="input input-sm" data-check-note="' + num(item.id, 0) + '" placeholder="豁免时必须填写理由" /></label>' +
    '<button type="button" class="btn btn-sm btn-primary" data-act="check-resolve" data-id="' + num(item.id, 0) + '"' +
    (status === 'resolved' ? ' disabled' : '') + '>标记已解决</button>' +
    '<button type="button" class="btn btn-sm btn-warn" data-act="check-waive" data-id="' + num(item.id, 0) + '"' +
    (status === 'waived' ? ' disabled' : '') + '>豁免（需理由）</button>' +
    '<button type="button" class="btn btn-sm" data-act="check-reopen" data-id="' + num(item.id, 0) + '"' +
    (status === 'open' ? ' disabled' : '') + '>重新打开</button>' +
    '</div></div>'
}

function checklistPanel() {
  const caseData = state.caseData || {}
  const items = state.checklistItems !== null
    ? state.checklistItems
    : (Array.isArray(caseData.checklist) ? caseData.checklist : [])
  const documents = caseDocuments()
  const filtered = items.filter((item) =>
    (!state.checklistFilter.status || item.status === state.checklistFilter.status) &&
    (!state.checklistFilter.category || item.category === state.checklistFilter.category))

  const grouped = {}
  // blocking 置顶：未解决的阻断项单独成组并置顶，其余按 category → severity 分组（R-28/R-52）
  const blockingOpen = filtered.filter((item) => item.severity === 'blocking' && item.status === 'open')
  const rest = filtered.filter((item) => !(item.severity === 'blocking' && item.status === 'open'))
  rest.forEach((item) => {
    const key = item.category || 'other'
    if (!grouped[key]) grouped[key] = []
    grouped[key].push(item)
  })
  const categories = state.cfg.check_category_order.concat(
    Object.keys(grouped).filter((key) => !state.cfg.check_category_order.includes(key)))
  const blockingHtml = blockingOpen.length
    ? '<div class="check-group"><div class="section-title">未解决阻断项（置顶）' +
      '<span class="badge badge-danger">' + blockingOpen.length + ' 项会阻止生成待签发稿</span></div>' +
      blockingOpen.map(checklistItemHtml).join('') +
      '<div class="callout callout-danger">阻断项未解决前，生成 <code>pending_issue</code>（待签发稿）会被后端拒绝（409 / INV-5）。</div></div>'
    : ''
  const body = categories.filter((key) => grouped[key]).map((key) => {
    const list = grouped[key].slice().sort((a, b) =>
      state.cfg.check_severity_order.indexOf(a.severity) - state.cfg.check_severity_order.indexOf(b.severity))
    return '<div class="check-group"><div class="section-title">' + esc(labelOf(state.cfg.check_category_labels, key)) +
      '<span class="badge badge-muted">' + list.length + ' 项</span></div>' +
      list.map(checklistItemHtml).join('') + '</div>'
  }).join('')

  const openBlocking = items.filter((item) => item.severity === 'blocking' && item.status === 'open').length

  return '<div class="card"><div class="card-head"><div class="card-title">校核清单' +
    '<span class="badge badge-muted">POST .../check · GET .../checklist · PATCH /api/checklist/{item_id}</span></div>' +
    '<div class="row">' + badge('danger', '未解决阻断 ' + openBlocking) + badge('muted', '共 ' + items.length + ' 项') + '</div></div>' +
    '<div class="card-body">' +
    '<div class="form-grid">' +
    '<div class="field"><label for="ck-doc">校核对象</label><select id="ck-doc" class="select" data-check="document_id">' +
    '<option value="">当前案件全部文稿</option>' + documents.map((doc) =>
      '<option value="' + num(doc.id, 0) + '">#' + num(doc.id, 0) + ' ' + esc(doc.doc_type || '') + ' v' + num(doc.version_no, 1) + '</option>').join('') +
    '</select></div>' +
    '<div class="field"><label>排版校核</label><label class="field-flag"><input type="checkbox" data-check="include_layout" checked /> 包含格式与排版检查（include_layout）</label></div>' +
    '<div class="field"><label for="ck-status">状态筛选（服务端）</label><select id="ck-status" class="select" data-act="checklist-filter" data-field="status">' +
    optionList(['open', 'resolved', 'waived'], state.cfg.check_item_status_labels, state.checklistFilter.status) + '</select></div>' +
    '<div class="field"><label for="ck-cat">类别筛选（服务端）</label><select id="ck-cat" class="select" data-act="checklist-filter" data-field="category">' +
    optionList(state.cfg.check_category_order, state.cfg.check_category_labels, state.checklistFilter.category) + '</select></div>' +
    '</div>' +
    '<div class="form-actions"><button type="button" class="btn btn-primary" data-act="check-run">运行校核</button>' +
    '<button type="button" class="btn" data-act="case-reload">重新加载清单</button>' +
    '<span class="hint">阻断项（severity=blocking 且 status=open）会阻止生成待签发稿，后端返回 409。</span></div>' +
    '</div>' +
    '<div class="card-body">' + blockingHtml + (body || '<div class="empty-note">清单为空。请运行校核。</div>') + '</div></div>'
}

/* ==========================================================================
 * 15. 文稿面板（生成、版本列表、下载、审核、交付）
 * ========================================================================== */

function documentItem(doc) {
  const formats = []
  if (doc.docx_path) formats.push(['docx', 'Word 草稿'])
  if (doc.pdf_path) formats.push(['pdf', 'PDF 预览'])
  const links = formats.map(([fmt, label]) =>
    '<a class="btn btn-sm btn-primary" href="' + esc(api.downloadUrl(doc.id, fmt)) + '" download>' +
    '下载 ' + label + '（' + fmt + '）</a>').join('')
  const unresolved = Array.isArray(doc.unresolved) ? doc.unresolved : []
  const fileName = (state.caseData && state.caseData.case_no ? state.caseData.case_no : 'case') +
    '-' + (doc.doc_type || 'report') + '-v' + num(doc.version_no, 1)
  return '<div class="doc-item" data-doc="' + num(doc.id, 0) + '">' +
    '<div class="doc-head"><div>' +
    '<div class="doc-name">' + esc(fileName) + '.docx / .pdf</div>' +
    '<div class="row" style="margin-top:4px">' + docStatusBadge(doc.doc_status) +
    badge('muted', '文稿 #' + num(doc.id, 0)) +
    badge('muted', '渲染引擎 ' + (doc.render_engine || '未记录')) +
    badge('muted', '模板 ' + (doc.template_version || '未记录')) +
    badge('muted', '规则 ' + (doc.term_rule_version || '未记录')) +
    (doc.approved_at ? badge('ok', '审核于 ' + esc(doc.approved_at)) : badge('muted', '尚未审核')) +
    (!doc.pdf_path ? badge('warn', 'PDF 缺失（LibreOffice 不可用或导出失败）') : '') +
    '</div></div><div class="table-actions">' + links +
    '<button type="button" class="btn btn-sm btn-warn" data-act="doc-approve" data-id="' + num(doc.id, 0) + '"' +
    (doc.doc_status === 'approved' ? ' disabled' : '') + '>标记已审核</button>' +
    '<button type="button" class="btn btn-sm" data-act="doc-deliver" data-id="' + num(doc.id, 0) + '"' +
    (doc.doc_status === 'approved' ? '' : ' disabled title="仅已审核文稿可交付（INV-8）"') + '>保存交付</button>' +
    '</div></div>' +
    (doc.blocked_reason ? '<div class="callout callout-danger" style="margin-top:8px">生成受阻：' + esc(doc.blocked_reason) + '</div>' : '') +
    (unresolved.length ? '<div class="callout callout-warn" style="margin-top:8px">未解析占位符：' +
      unresolved.map((item) => '<code class="mono">' + esc(item) + '</code>').join('、') + '</div>' : '') +
    '<div class="small muted" style="margin-top:6px">docx：' + valHtml(doc.docx_path) + ' · pdf：' + valHtml(doc.pdf_path) + '</div>' +
    '</div>'
}

function documentsPanel() {
  const cfg = state.cfg
  const documents = caseDocuments()
  const draftMarker = cfg.draft_marker
  return '<div class="card"><div class="card-head"><div class="card-title">文稿生成与下载' +
    '<span class="badge badge-muted">POST/GET /api/cases/{id}/documents</span></div>' +
    '<span class="badge badge-info">交付物：报告草稿 .docx + 报告预览 .pdf</span></div>' +
    '<div class="card-body">' +
    '<div class="callout callout-info">新建文稿一律先落 <strong>内部草稿（internal_draft）</strong>，正文带待核对标记 <code>' +
    esc(draftMarker) + '</code>；只有在没有未解决阻断项时才可进入待签发稿（R-27/R-28/R-46）。</div>' +
    '<div class="form-grid" style="margin-top:10px">' +
    '<div class="field"><label for="d-type">文稿类型 doc_type</label>' +
    '<input id="d-type" class="input" data-doc-create="doc_type" value="report" maxlength="40" /></div>' +
    '<div class="field"><label for="d-status">文稿状态 doc_status</label><select id="d-status" class="select" data-doc-create="doc_status">' +
    '<option value="internal_draft">内部草稿</option><option value="pending_issue">待签发稿（需无阻断项）</option></select></div>' +
    '<div class="field"><label for="d-tpl">模板版本 template_version</label>' +
    '<input id="d-tpl" class="input" data-doc-create="template_version" value="' + esc(cfg.template_version) + '" maxlength="80" /></div>' +
    '<div class="field"><label>输出格式 formats</label><div class="checks">' +
    '<label class="field-flag"><input type="checkbox" data-doc-create-format="docx" checked /> docx</label>' +
    '<label class="field-flag"><input type="checkbox" data-doc-create-format="pdf" checked /> pdf</label>' +
    '</div></div>' +
    '<div class="field"><label for="d-note">备注 note</label>' +
    '<input id="d-note" class="input" data-doc-create="note" maxlength="300" /></div>' +
    '</div>' +
    '<div class="form-actions"><button type="button" class="btn btn-primary" data-act="doc-create">生成文稿版本</button>' +
    '<button type="button" class="btn" data-act="case-reload">刷新版本列表</button>' +
    '<span class="hint">生成新版本会把旧的已审核版本置为 superseded（R-41）。</span></div>' +
    '</div>' +
    '<div class="card-body">' + (documents.length
      ? documents.map(documentItem).join('')
      : '<div class="empty-note">尚无文稿版本。</div>') + '</div></div>'
}

/* ==========================================================================
 * 16. 确认修改：案件信息、鉴定意见（N-1）、四类修正
 * ========================================================================== */

function caseInfoCard() {
  const caseData = state.caseData || {}
  const sectionOrder = Array.isArray(caseData.section_order) ? caseData.section_order.join(',') : ''
  const field = (name, label, value) =>
    '<label class="field"><span class="small">' + esc(label) + '</span>' +
    '<input class="input input-sm" data-case-edit="' + name + '" value="' + esc(value || '') + '" /></label>'
  return '<div class="card"><div class="card-head"><div class="card-title">案件基础信息与栏目顺序' +
    '<span class="badge badge-muted">PATCH /api/cases/{id}</span></div>' +
    '<span class="badge badge-muted">expected_version = 案件版本 ' + currentVersion() + '</span></div>' +
    '<div class="card-body"><div class="edit-grid">' +
    field('case_no', '案件编号', caseData.case_no) +
    field('commission_unit', '委托单位', caseData.commission_unit) +
    field('commission_matter', '委托事项（不会被改写成鉴定意见）', caseData.commission_matter) +
    field('commission_date', '委托日期', caseData.commission_date) +
    field('subject_name', '姓名', caseData.subject_name) +
    field('template_version', '模板版本', caseData.template_version) +
    field('term_rule_version', '术语规则版本', caseData.term_rule_version) +
    field('section_order', '栏目顺序（逗号分隔）', sectionOrder) +
    '</div>' +
    '<label class="field" style="margin-top:8px"><span class="small">案情简介（保留委托书来源属性，不作为检验人员直接观察所见 N-6）</span>' +
    '<textarea class="textarea" data-case-edit="case_summary" rows="2">' + esc(caseData.case_summary || '') + '</textarea></label>' +
    '<div class="form-actions"><button type="button" class="btn btn-primary" data-act="case-save">保存案件信息</button>' +
    '<span class="hint">至少修改一个字段；用户修正后会失效相关复核状态（R-40）。</span></div></div></div>'
}

function opinionCard() {
  const caseData = state.caseData || {}
  return '<div class="card"><div class="card-head"><div class="card-title">鉴定意见（opinion）' +
    '<span class="badge badge-danger">由专业人员提供</span></div>' +
    '<span class="badge badge-muted">N-1：系统不得生成</span></div>' +
    '<div class="card-body">' +
    '<div class="callout callout-danger">此栏目<strong>只能由专业人员手工输入</strong>。系统不会从识别文本、' +
    '照片或任何模型输出推断死亡原因、死亡时间、致伤工具或损伤形成机制；写入的内容原样进入文稿（N-1/INV-10）。</div>' +
    '<textarea class="textarea" data-opinion-input rows="4" placeholder="由专业人员填写鉴定意见（留空表示尚未提供）">' +
    esc(caseData.opinion_text || '') + '</textarea>' +
    '<div class="form-actions"><button type="button" class="btn btn-primary" data-act="opinion-save">保存鉴定意见</button>' +
    '<span class="hint">当前值：' + valHtml(caseData.opinion_text) + '</span></div>' +
    '</div></div>'
}

function correctionsPanel() {
  const corrections = caseCorrections()
  const cfg = state.cfg
  const kinds = cfg.correction_kinds.map((kind) =>
    '<option value="' + esc(kind) + '">' + esc(labelOf(cfg.correction_kind_labels, kind)) + '</option>').join('')
  const targets = cfg.correction_target_types.map((type) =>
    '<option value="' + esc(type) + '">' + esc(labelOf(CORRECTION_TARGET_LABELS, type)) + '</option>').join('')
  const scopes = cfg.correction_scopes.map((scope) =>
    '<option value="' + esc(scope) + '">' + esc(labelOf(cfg.correction_scope_labels, scope)) + '</option>').join('')

  const history = corrections.length
    ? '<div class="table-wrap"><table class="table"><thead><tr>' +
      '<th>#</th><th>类型</th><th>对象</th><th>修改前</th><th>修改后</th><th>原因</th><th>范围</th><th>确认人</th><th>已应用</th><th>时间</th>' +
      '</tr></thead><tbody>' + corrections.map((item) =>
        '<tr><td class="num">' + num(item.id, 0) + '</td>' +
        '<td>' + badge('info', labelOf(cfg.correction_kind_labels, item.kind)) + '</td>' +
        '<td>' + esc(labelOf(CORRECTION_TARGET_LABELS, item.target_type)) + ' #' + num(item.target_id, 0) + '</td>' +
        '<td>' + valHtml(item.before_text) + '</td><td>' + valHtml(item.after_text) + '</td>' +
        '<td>' + valHtml(item.reason) + '</td>' +
        '<td>' + esc(labelOf(cfg.correction_scope_labels, item.scope)) +
        (item.scope_value ? '<div class="small muted">' + esc(item.scope_value) + '</div>' : '') + '</td>' +
        '<td>' + valHtml(item.confirmed_by) + '</td>' +
        '<td>' + (item.applied ? badge('ok', '已应用') : badge('warn', '未应用')) + '</td>' +
        '<td class="small nowrap">' + esc(item.created_at || '') + '</td></tr>').join('') +
      '</tbody></table></div>'
    : '<div class="empty-note">尚无修正记录。</div>'

  return '<div class="card"><div class="card-head"><div class="card-title">修正录入（四类）' +
    '<span class="badge badge-muted">POST /api/cases/{id}/corrections</span></div></div>' +
    '<div class="card-body">' +
    '<div class="form-grid">' +
    '<div class="field"><label for="cr-kind">修正类型 kind</label><select id="cr-kind" class="select" data-correction="kind">' + kinds + '</select></div>' +
    '<div class="field"><label for="cr-target">对象类型 target_type</label><select id="cr-target" class="select" data-correction="target_type">' + targets + '</select></div>' +
    '<div class="field"><label for="cr-id">对象 ID target_id</label><input id="cr-id" class="input" type="number" min="0" value="0" data-correction="target_id" /></div>' +
    '<div class="field"><label for="cr-scope">适用范围 scope</label><select id="cr-scope" class="select" data-correction="scope">' + scopes + '</select></div>' +
    '<div class="field"><label for="cr-scope-value">适用范围值 scope_value（非 case_only 必填）</label>' +
    '<input id="cr-scope-value" class="input" data-correction="scope_value" maxlength="120" /></div>' +
    '<div class="field"><label for="cr-by">确认人 confirmed_by</label><input id="cr-by" class="input" data-correction="confirmed_by" value="复核人" maxlength="80" /></div>' +
    '</div>' +
    '<div class="grid-2" style="margin-top:8px">' +
    '<label class="field"><span class="small">修改前内容 before_text</span>' +
    '<textarea class="textarea" data-correction="before_text" rows="2"></textarea></label>' +
    '<label class="field"><span class="small">修改后内容 after_text</span>' +
    '<textarea class="textarea" data-correction="after_text" rows="2"></textarea></label>' +
    '</div>' +
    '<label class="field" style="margin-top:8px"><span class="small">修改原因 reason（必填）</span>' +
    '<input class="input" data-correction="reason" maxlength="500" placeholder="如：记录本原字为“右”，此前识别为“左”" /></label>' +
    '<div class="row" style="margin-top:8px">' +
    '<label class="field-flag"><input type="checkbox" data-correction="apply_to_case" checked /> 应用修正到当前案件并失效旧复核状态（apply_to_case）</label>' +
    '</div>' +
    '<div class="form-actions"><button type="button" class="btn btn-primary" data-act="correction-create">提交修正</button>' +
    '<span class="hint">默认只影响当前案件；要用于以后的案件必须填写适用范围（R-39/N-10）。</span></div>' +
    '</div>' +
    '<div class="card-body"><div class="section-title">修正记录（R-38）</div><div style="margin-top:8px">' + history + '</div></div></div>'
}

/* ==========================================================================
 * 17. 术语规则视图（expected_version 是**规则版本**）
 * ========================================================================== */

function ruleEditor(rule) {
  const id = num(rule.id, 0)
  const field = (key, label, type) =>
    '<label class="field"><span class="small">' + esc(label) + '</span>' +
    '<input class="input input-sm" type="' + (type || 'text') + '" data-rule-edit="' + key + '" value="' +
    esc(rule[key] === null || rule[key] === undefined ? '' : rule[key]) + '" /></label>'
  return '<div class="material-item" data-rule-row="' + id + '">' +
    '<div class="material-head"><div class="row">' +
    '<strong class="mono">' + esc(rule.code || '') + '</strong>' +
    badge(rule.active ? 'ok' : 'muted', rule.active ? '启用' : '停用') +
    (rule.requires_confirmation ? badge('warn', '需确认') : badge('muted', '免确认')) +
    badge('muted', 'priority ' + num(rule.priority, 100)) +
    badge('info', '规则版本 v' + num(rule.version, 1)) + '</div>' +
    '<div class="btn-row">' +
    '<button type="button" class="btn btn-sm btn-primary" data-act="rule-save" data-id="' + id + '">保存（expected_version = 规则版本 ' + num(rule.version, 1) + '）</button>' +
    '<button type="button" class="btn btn-sm btn-warn" data-act="rule-toggle" data-id="' + id + '" data-active="' +
    (rule.active ? 'false' : 'true') + '">' + (rule.active ? '停用' : '启用') + '</button>' +
    '<button type="button" class="btn btn-sm btn-danger" data-act="rule-delete" data-id="' + id + '">删除</button>' +
    '</div></div>' +
    '<div class="material-body"><div class="edit-grid">' +
    field('shorthand', '简写 shorthand') + field('pattern', '匹配式 pattern') +
    '<label class="field"><span class="small">匹配方式 match_type</span><select class="select select-sm" data-rule-edit="match_type">' +
    ['literal', 'regex'].map((type) => '<option value="' + type + '"' + (rule.match_type === type ? ' selected' : '') + '>' + type + '</option>').join('') +
    '</select></label>' +
    field('template', '展开模板 template') +
    field('scope_field_key', '适用栏目 scope_field_key') +
    field('scope_column', '适用记录栏目 scope_column') +
    field('scope_writer', '适用书写者 scope_writer') +
    field('require_groups', '必需分组 require_groups（逗号分隔）') +
    field('missing_group_note', '缺组提示 missing_group_note') +
    field('boundary_note', '转换边界 boundary_note') +
    field('priority', '优先级 priority', 'number') +
    '<label class="field"><span class="small">需要人工确认</span><select class="select select-sm" data-rule-edit="requires_confirmation">' +
    '<option value="false"' + (rule.requires_confirmation ? '' : ' selected') + '>否</option>' +
    '<option value="true"' + (rule.requires_confirmation ? ' selected' : '') + '>是</option></select></label>' +
    '</div></div></div>'
}

function renderRulesView() {
  const host = $('#view-rules')
  const cfg = state.cfg
  const createFields = [['code', '规则代码 code（唯一）'], ['shorthand', '简写 shorthand'], ['pattern', '匹配式 pattern'],
    ['template', '展开模板 template'], ['scope_field_key', '适用栏目 scope_field_key'],
    ['scope_column', '适用记录栏目 scope_column'], ['scope_writer', '适用书写者 scope_writer'],
    ['require_groups', '必需分组 require_groups（逗号分隔）'], ['missing_group_note', '缺组提示 missing_group_note'],
    ['boundary_note', '转换边界 boundary_note'], ['priority', '优先级 priority']]
  const createInputs = createFields.map(([key, label]) =>
    '<label class="field"><span class="small">' + esc(label) + '</span>' +
    '<input class="input input-sm" data-rule-new="' + key + '"' + (key === 'priority' ? ' type="number" value="100"' : '') + ' /></label>').join('')

  host.innerHTML = '' +
    '<div class="card"><div class="card-head"><div class="card-title">术语规则管理' +
    '<span class="badge badge-muted">GET/POST/PATCH/DELETE /api/terminology-rules</span></div>' +
    '<span class="badge badge-danger">expected_version 是规则版本，不是案件版本</span></div>' +
    '<div class="card-body">' +
    '<div class="row">' +
    '<label class="inline-field">搜索 <input class="input input-sm" data-rule-filter="search" value="' + esc(state.ruleFilter.search) + '" placeholder="代码/简写/匹配式" /></label>' +
    '<label class="field-flag"><input type="checkbox" data-rule-filter="active_only"' + (state.ruleFilter.active_only ? ' checked' : '') + ' /> 只看启用规则</label>' +
    '<button type="button" class="btn btn-sm btn-primary" data-act="rules-search">查询</button>' +
    '<label class="field-flag"><input type="checkbox" data-rule-draft-toggle' + (state.ruleDraftOpen ? ' checked' : '') + ' /> 显示新增规则表单</label>' +
    '</div></div>' +
    (state.ruleDraftOpen
      ? '<div class="card-body"><div class="section-title">新增规则</div>' +
        '<div class="edit-grid" style="margin-top:8px">' + createInputs +
        '<label class="field"><span class="small">匹配方式 match_type</span>' +
        '<select class="select select-sm" data-rule-new="match_type"><option value="literal">literal</option><option value="regex">regex</option></select></label>' +
        '<label class="field"><span class="small">需要人工确认 requires_confirmation</span>' +
        '<select class="select select-sm" data-rule-new="requires_confirmation"><option value="false">否</option><option value="true">是</option></select></label>' +
        '<label class="field"><span class="small">启用 active</span>' +
        '<select class="select select-sm" data-rule-new="active"><option value="true">启用</option><option value="false">停用</option></select></label>' +
        '</div><div class="form-actions"><button type="button" class="btn btn-primary" data-act="rule-create">新增规则</button>' +
        '<span class="hint">新增规则不需要 expected_version（POST 创建新资源）。</span></div></div>'
      : '') +
    '<div class="card-body">' + (state.rules.length
      ? '<div class="material-list">' + state.rules.map(ruleEditor).join('') + '</div>'
      : '<div class="empty-note">没有规则。可先运行 <code>python -c "from backend.app.database import Database; Database(\'data/cases.db\').import_seed_rules()"</code> 导入种子规则，或在此新增。</div>') +
    '<div class="small muted" style="margin-top:8px">规则按 (priority, code) 升序取第一条命中者；未命中任何规则时保留原文并要求人工确认（R-23）。</div>' +
    '</div></div>'
}

/* ==========================================================================
 * 18. 系统配置视图
 * ========================================================================== */

function renderConfigView() {
  const host = $('#view-config')
  const cfg = state.cfg
  const engines = cfg.engines.map((engine) =>
    '<tr><td class="mono">' + esc(engine.name) + '</td>' +
    '<td>' + (engine.available ? badge('ok', '可用') : badge('danger', '不可用')) + '</td>' +
    '<td class="small">' + valHtml(engine.detail) + '</td></tr>').join('')
  const photos = cfg.photo_positions.map((item) =>
    '<tr><td class="mono">' + esc(item.key) + '</td><td>' + esc(item.label) + '</td>' +
    '<td>' + esc(item.section_label || labelOf(cfg.photo_section_labels, item.section)) + '</td>' +
    '<td class="num">' + num(item.order, 0) + '</td>' +
    '<td class="mono small">{{ ' + esc(item.anchor) + ' }}</td>' +
    '<td>' + (item.wide ? badge('info', '宽幅') : badge('muted', '常规')) + '</td></tr>').join('')

  host.innerHTML = '' +
    '<div class="card"><div class="card-head"><div class="card-title">系统状态' +
    '<span class="badge badge-muted">GET /api/health · GET /api/config</span></div>' +
    '<button type="button" class="btn btn-sm" data-act="config-reload">重新加载配置</button></div>' +
    '<div class="card-body"><div class="kv">' +
    '<span class="k">后端状态</span><span>' + esc(state.health ? JSON.stringify(state.health) : '未探测') + '</span>' +
    '<span class="k">演示数据 demo_data</span><span>' + (cfg.demo_data ? badge('warn', 'true（内置演示案件，不代表真实案件）') : badge('ok', 'false')) + '</span>' +
    '<span class="k">Word 渲染引擎</span><span>' + esc(String(cfg.docx_engine)) +
    (cfg.docxtpl_available === null ? '' : '（docxtpl_available=' + String(cfg.docxtpl_available) + '）') +
    ' <span class="badge badge-muted">TODO-DL-1</span></span>' +
    '<span class="k">LibreOffice</span><span>' +
    (cfg.libreoffice ? '<span class="mono">' + esc(String(cfg.libreoffice)) + '</span>' : badge('warn', '未探测到（PDF 导出将缺失并记入校核清单）')) +
    '</span>' +
    '<span class="k">识别低置信度阈值</span><span>' + esc(String(cfg.ocr_low_confidence)) + '</span>' +
    '<span class="k">契约版本</span><span>' + esc(cfg.contract_version || '未记录') + '</span>' +
    '<span class="k">模板/规则版本</span><span>' + esc(cfg.template_version) + ' · ' + esc(cfg.term_rule_version) + '</span>' +
    '<span class="k">草稿待核对标记</span><span class="mono">' + esc(cfg.draft_marker) + '</span>' +
    '<span class="k">照片策略</span><span>' + esc(cfg.photo_policy || '系统不读取照片内容，只按用户指定的位置插入（N-11）') + '</span>' +
    (cfg.demo_note ? '<span class="k">演示数据说明</span><span>' + esc(cfg.demo_note) + '</span>' : '') +
    '</div></div>' +
    '<div class="card-body"><div class="section-title">识别引擎可用性</div>' +
    '<div class="table-wrap" style="margin-top:8px"><table class="table"><thead><tr><th>引擎</th><th>可用</th><th>说明</th></tr></thead>' +
    '<tbody>' + engines + '</tbody></table></div>' +
    '<div class="small muted" style="margin-top:6px">缺少的下载项一律降级运行并在校核清单/本页显式报告，不静默失败（docs/TODO_DOWNLOADS.md）。</div>' +
    '</div>' +
    '<div class="card-body"><div class="section-title">待下载能力（TODO-DL 记账，共 ' + cfg.todo_downloads.length + ' 项）</div>' +
    (cfg.todo_downloads.length
      ? '<div class="table-wrap" style="margin-top:8px"><table class="table"><thead><tr><th>编号</th><th>内容</th><th>状态</th></tr></thead><tbody>' +
        cfg.todo_downloads.map((item) =>
          '<tr><td class="mono">' + esc(item.code || '') + '</td><td>' + esc(item.item || '') + '</td>' +
          '<td>' + (item.available ? badge('ok', '已可用') : badge('warn', '未安装（降级运行）')) + '</td></tr>').join('') +
        '</tbody></table></div>'
      : '<div class="empty-note">后端未报告待办下载项。</div>') +
    '</div>' +
    '<div class="card-body"><div class="section-title">字段目录（field_definitions，共 ' + cfg.field_definitions.length + ' 项）</div>' +
    (cfg.field_definitions.length
      ? '<div class="table-wrap" style="margin-top:8px"><table class="table"><thead><tr>' +
        '<th>key</th><th>名称</th><th>分组</th><th>必填</th><th>关键</th><th>模板锚点</th></tr></thead><tbody>' +
        cfg.field_definitions.map((item) =>
          '<tr><td class="mono">' + esc(item.key || '') + '</td><td>' + esc(item.label || '') + '</td>' +
          '<td>' + esc(item.group_label || labelOf(cfg.field_group_labels, item.group)) + '</td>' +
          '<td>' + (item.required ? badge('warn', '必填') : '') + '</td>' +
          '<td>' + (item.critical ? badge('danger', '关键事实') : '') + '</td>' +
          '<td class="mono small">{{ ' + esc(item.template_anchor || item.key || '') + ' }}</td></tr>').join('') +
        '</tbody></table></div>'
      : '<div class="empty-note">后端未提供字段目录。</div>') +
    '</div>' +
    '<div class="card-body"><div class="section-title">照片目标位置目录（photo_positions，共 ' + cfg.photo_positions.length + ' 项）</div>' +
    '<div class="table-wrap" style="margin-top:8px"><table class="table"><thead><tr>' +
    '<th>key</th><th>名称</th><th>分组</th><th class="num">order</th><th>模板锚点</th><th>宽度</th></tr></thead><tbody>' +
    photos + '</tbody></table></div>' +
    '<div class="small muted" style="margin-top:6px">照片只按用户选择的位置插入；系统不读取照片内容（N-11）。空栏位写入“' +
    esc(cfg.photo_placeholder) + '”（N-12）。</div>' +
    '</div></div>' +

    '<div class="card"><div class="card-head"><div class="card-title">模型接入（密钥仅内存）' +
    '<span class="badge badge-danger">X-DeepSeek-API-Key</span></div></div>' +
    '<div class="card-body">' +
    '<div class="callout callout-warn">密钥只保存在当前页面的内存变量中，只通过请求头发送；' +
    '不写入 localStorage / sessionStorage / URL / 页面元素属性，刷新页面即丢失（AGENTS.md 硬性约束 9）。</div>' +
    '<div class="form-grid" style="margin-top:8px">' +
    '<div class="field"><label for="ai-key">DeepSeek API Key</label>' +
    '<input id="ai-key" class="input" type="password" autocomplete="off" data-act="api-key" placeholder="仅内存，不落盘" /></div>' +
    '</div>' +
    '<div class="form-actions"><button type="button" class="btn btn-primary" data-act="ai-verify">测试模型连通性</button>' +
    '<button type="button" class="btn" data-act="api-key-clear">清除内存中的密钥</button>' +
    '<span class="hint">当前内存中' + (state.apiKey ? '已填写密钥（长度 ' + state.apiKey.length + '）' : '没有密钥') + '。</span></div>' +
    (state.aiResult ? '<div class="callout callout-info" style="margin-top:8px">连通性结果：' + jsonPretty(state.aiResult) + '</div>' : '') +
    '</div>' +
    '<div class="card-body"><div class="row">' +
    '<button type="button" class="btn btn-sm" data-act="health-check">探测 /api/health</button>' +
    '<span class="hint">模型抽取默认关闭（llm.py 可选），开关只在“运行提取”里按需打开。</span></div></div>' +
    '</div>'
}

/* ==========================================================================
 * 18b. 主视图「整理」：上传 → 一次请求 → 结果（POST /api/quick-run）
 *      普通用户只需要两件事：上传文件、下载结果。识别、归类、生成、校核的
 *      全部细节都留在次级界面「案件记录」的四步核对里，本页不暴露。
 *      注意：本页不读取照片内容、不做任何图像推断（N-11/R-55/R-58）。
 * ========================================================================== */

function quickSizeText(bytes) {
  const value = num(bytes, 0)
  if (value < 1024) return value + ' B'
  if (value < 1024 * 1024) return (value / 1024).toFixed(1) + ' KB'
  return (value / (1024 * 1024)).toFixed(2) + ' MB'
}

function quickSelectedBytes() {
  return state.quick.files.reduce((sum, item) => sum + num(item.size, 0), 0)
}

/** base64 编码后体积约为原始字节的 4/3，用来预估是否触及网关上限。 */
function quickEstimateBytes() {
  return Math.round(quickSelectedBytes() * 4 / 3)
}

function quickFormatsFromChoice(choice) {
  if (choice === 'docx') return ['docx']
  if (choice === 'pdf') return ['pdf']
  return ['docx', 'pdf']
}

function quickFormatChoice(formats) {
  const list = (Array.isArray(formats) ? formats : []).slice().sort().join(',')
  if (list === 'docx') return 'docx'
  if (list === 'pdf') return 'pdf'
  return 'both'
}

function quickFormatSummary(formats) {
  const choice = quickFormatChoice(formats)
  if (choice === 'docx') return '只要 Word（.docx）'
  if (choice === 'pdf') return '只要 PDF'
  return 'Word 与 PDF 都要'
}

/**
 * 组装 `POST /api/quick-run` 请求体（纯函数，供静态自检与人工核对）。
 * 契约：{files:[{filename,media_type,content_base64,kind,position_key}], formats:[...],
 *        case_no, commission_unit, subject_name, note}
 * `kind` 留空 = 交后端按文件名推断；`position_key` 只对部位照片发送
 * （后端对非照片指定位置会返回 400「只有部位照片才能指定目标位置」）。
 */
function buildQuickRunPayload(files, options) {
  const opts = options || {}
  const formats = Array.isArray(opts.formats) && opts.formats.length
    ? opts.formats.slice()
    : ['docx', 'pdf']
  return {
    files: (Array.isArray(files) ? files : []).map((item) => {
      const kind = (item && item.kind) || ''
      return {
        filename: (item && item.filename) || '',
        media_type: (item && item.media_type) || 'other',
        content_base64: (item && item.content_base64) || '',
        kind: kind,
        position_key: kind === 'photo' ? ((item && item.position_key) || '') : '',
      }
    }),
    formats: formats,
    case_no: ((opts.case_no || '') + '').trim(),
    commission_unit: ((opts.commission_unit || '') + '').trim(),
    subject_name: ((opts.subject_name || '') + '').trim(),
    note: ((opts.note || '') + '').trim(),
  }
}

/**
 * 下载地址白名单：只接受同源相对路径或与当前页面同源的绝对地址。
 * 本工作台不引用任何外部资源（无 CDN / 字体 / 图标库），也不允许把用户
 * 导向站外地址；不满足时返回空串，由调用方把按钮置灰并说明原因。
 */
function quickUrlAllowed(url) {
  const text = String(url || '').trim()
  if (!text) return false
  if (text.indexOf('://') < 0) return true
  if (text.charAt(0) === '/') return true
  if (typeof window === 'undefined' || !window.location) return false
  return text.indexOf(window.location.origin + '/') === 0
}

/** 文稿下载地址：优先用响应里的 `download_urls`，缺失或被判为站外地址时回退 `downloadUrl()`（同一端点）。 */
function quickDownloadUrl(doc, fmt) {
  const document_ = doc || {}
  const urls = document_.download_urls && typeof document_.download_urls === 'object'
    ? document_.download_urls : {}
  const direct = fmt === 'pdf' ? urls.pdf : urls.docx
  // 站外地址一律不用（本工作台没有任何外链），改走同源的下载端点。
  if (direct && quickUrlAllowed(direct)) return String(direct)
  if (document_.id !== undefined && document_.id !== null && document_.id !== '') {
    return api.downloadUrl(document_.id, fmt)
  }
  return ''
}

/** 该文稿在本次响应里是否真的产出了指定格式（缺失时按钮必须置灰）。 */
function quickHasFormat(doc, fmt) {
  const document_ = doc || {}
  if (fmt === 'pdf') {
    if (document_.has_pdf !== undefined && document_.has_pdf !== null) return Boolean(document_.has_pdf)
    return Boolean((document_.download_urls && document_.download_urls.pdf) ||
      document_.filename_pdf || document_.pdf_path)
  }
  if (document_.has_docx !== undefined && document_.has_docx !== null) return Boolean(document_.has_docx)
  return Boolean((document_.download_urls && document_.download_urls.docx) ||
    document_.filename_docx || document_.docx_path)
}

/** 类型芯片文案：本工作台标注 + 后端规范标签并列，避免口径不一致。 */
function quickKindLabel(kind, fallback) {
  const backend = (state.cfg.material_kind_labels && state.cfg.material_kind_labels[kind]) || ''
  if (!fallback) return backend || kind
  if (backend && backend !== fallback) return fallback + '（' + backend + '）'
  return fallback
}

function quickKindChips(item) {
  return QUICK_KIND_CHIPS.map((pair) => {
    const kind = pair[0]
    const label = pair[1]
    const active = item.kind === kind
    return '<button type="button" class="chip' + (active ? ' is-active' : '') + '" data-act="quick-kind"' +
      ' data-id="' + num(item.id, 0) + '" data-kind="' + esc(kind) + '" aria-pressed="' + (active ? 'true' : 'false') + '"' +
      ' title="' + esc(kind ? ('材料类型 kind=' + kind) : 'kind 留空：交后端按文件名推断') + '">' +
      esc(quickKindLabel(kind, label)) + '</button>'
  }).join('')
}

function quickPositionSelect(item) {
  if (item.kind !== 'photo') return ''
  return '<label class="inline-field small">目标位置' +
    '<select class="select select-sm" data-quick-position="' + num(item.id, 0) + '">' +
    photoPositionOptions(item.position_key, '自动（按文件名推测）') + '</select></label>'
}

function quickFileRow(item) {
  const oversize = num(item.size, 0) > QUICK_MAX_FILE_BYTES
  return '<div class="quick-file">' +
    '<div class="quick-file-head">' +
    '<span class="quick-file-name">' + esc(item.name) + '</span>' +
    badge('muted', quickSizeText(item.size)) +
    badge('muted', 'media=' + esc(item.media_type)) +
    (oversize ? badge('warn', '超过 30 MB') : '') +
    (item.kind === 'photo' ? badge('info', '部位照片：不参与识别') : '') +
    '<button type="button" class="btn btn-sm" data-act="quick-remove" data-id="' + num(item.id, 0) + '">移除</button>' +
    '</div>' +
    '<div class="quick-file-kind"><span class="small muted">类型</span>' +
    '<div class="chips">' + quickKindChips(item) + '</div>' + quickPositionSelect(item) + '</div>' +
    (item.kind === 'photo'
      ? '<div class="small muted">部位照片：' + esc(QUICK_PHOTO_POLICY) + '。</div>'
      : '') +
    '</div>'
}

function quickFileList() {
  const files = state.quick.files
  if (!files.length) {
    return '<div class="empty-note">还没有选择文件。把照片、Word、PDF 拖进上面的方框，或点击“选择文件”。</div>'
  }
  const estimated = quickEstimateBytes()
  const totalNote = '<div class="row" style="margin-bottom:8px">' +
    badge('muted', '已选 ' + files.length + ' 个文件') +
    badge('muted', '原始共 ' + quickSizeText(quickSelectedBytes())) +
    badge('muted', 'base64 后约 ' + quickSizeText(estimated)) +
    (estimated > QUICK_GATEWAY_LIMIT_BYTES ? badge('danger', '超过网关 64 MB 上限，可能被拒绝') : '') +
    '<button type="button" class="btn btn-sm" data-act="quick-clear">清空已选</button></div>'
  return totalNote + '<div class="quick-file-list">' + files.map(quickFileRow).join('') + '</div>'
}

function quickFormatPicker() {
  const choice = quickFormatChoice(state.quick.formats)
  const options = [['both', '两者都要（推荐）'], ['docx', '只要 Word（.docx）'], ['pdf', '只要 PDF']]
  return '<div class="field"><span class="field-label">输出格式</span><div class="chips">' +
    options.map((pair) =>
      '<label class="chip chip-radio' + (choice === pair[0] ? ' is-active' : '') + '">' +
      '<input type="radio" name="q-format" value="' + esc(pair[0]) + '"' +
      (choice === pair[0] ? ' checked' : '') + ' data-quick-format />' + esc(pair[1]) + '</label>').join('') +
    '</div><span class="hint">选择“只要 PDF”时，若服务端未安装 LibreOffice 会只得到 Word，并在结果页说明原因。</span></div>'
}

function quickOptionalFields() {
  if (!state.quick.showOptional) {
    return '<div class="quick-optional"><button type="button" class="btn btn-sm" data-act="quick-toggle-optional"' +
      ' aria-expanded="false">展开可选项（案件编号 / 委托单位 / 姓名 / 备注）</button>' +
      '<span class="hint" style="margin-left:8px">一般都是留空的：案件编号会自动生成，信息由系统从材料里读。</span></div>'
  }
  return '<div class="quick-optional"><button type="button" class="btn btn-sm" data-act="quick-toggle-optional"' +
    ' aria-expanded="true">收起可选项</button>' +
    '<div class="form-grid" style="margin-top:8px">' +
    '<div class="field"><label for="q-case-no">案件编号（留空自动生成 FY-&lt;年份&gt;-&lt;序号&gt;）</label>' +
    '<input id="q-case-no" class="input" maxlength="64" placeholder="可留空" data-quick-field="case_no" value="' +
    esc(state.quick.case_no) + '" /></div>' +
    '<div class="field"><label for="q-unit">委托单位</label>' +
    '<input id="q-unit" class="input" maxlength="120" data-quick-field="commission_unit" value="' +
    esc(state.quick.commission_unit) + '" /></div>' +
    '<div class="field"><label for="q-name">姓名</label>' +
    '<input id="q-name" class="input" maxlength="80" data-quick-field="subject_name" value="' +
    esc(state.quick.subject_name) + '" /></div>' +
    '<div class="field"><label for="q-note">备注（交给系统的一次性说明）</label>' +
    '<input id="q-note" class="input" maxlength="500" data-quick-field="note" value="' +
    esc(state.quick.note) + '" /></div>' +
    '</div></div>'
}

function quickPhaseText() {
  if (state.quick.running) {
    const seconds = state.quick.startedAt
      ? Math.max(1, Math.round((Date.now() - state.quick.startedAt) / 1000)) : 0
    return (state.quick.phase || '整理中') + '…已用 ' + seconds + ' 秒（一次请求，服务端不回报百分比）'
  }
  if (state.quick.error) return '上次整理失败：' + state.quick.error
  if (state.quick.result) return '已完成，请到「结果」页下载文稿。'
  if (!state.quick.files.length) return '还没有选择文件'
  return '准备就绪：' + quickFormatSummary(state.quick.formats) + '，点“开始整理”即可'
}

function setQuickPhaseText(text) {
  const slot = $('#quick-phase')
  if (slot) slot.textContent = text || ''
}

function updateQuickProgress() {
  setQuickPhaseText(quickPhaseText())
  const button = $('#quick-submit')
  if (button) {
    button.disabled = Boolean(state.quick.running) || !state.quick.files.length
    button.textContent = state.quick.running ? ((state.quick.phase || '整理中') + '…') : '开始整理'
  }
}

let quickTicker = null

/** 单次请求拿不到真实进度，界面按预计阶段推进，并明确说明“不回报百分比”。 */
function startQuickTicker() {
  stopQuickTicker()
  const phases = ['上传中', '识别中', '生成文稿']
  state.quick.phase = phases[0]
  let index = 0
  quickTicker = window.setInterval(() => {
    index = Math.min(index + 1, phases.length - 1)
    state.quick.phase = phases[index]
    updateQuickProgress()
  }, 7000)
}

function stopQuickTicker() {
  if (quickTicker) {
    window.clearInterval(quickTicker)
    quickTicker = null
  }
}

function quickSubmitRow() {
  const running = Boolean(state.quick.running)
  const disabled = running || !state.quick.files.length
  return '<div class="form-actions">' +
    '<button id="quick-submit" type="button" class="btn btn-primary btn-lg" data-act="quick-run"' +
    (disabled ? ' disabled' : '') + '>' + esc(running ? ((state.quick.phase || '整理中') + '…') : '开始整理') + '</button>' +
    '<span id="quick-phase" class="quick-phase" aria-live="polite">' + esc(quickPhaseText()) + '</span>' +
    '</div>' +
    '<div class="callout callout-info" style="margin-top:8px">' +
    '<strong>提交后会发生什么：</strong>文件一次性上传（浏览器 <code>FileReader</code> 读为 base64），' +
    '服务端登记材料 → 推断类型与部位 → 提取字段与损伤 → 填入模板 → 生成 Word/PDF。' +
    '一次请求完成，界面按“上传中 → 识别中 → 生成文稿”提示预计阶段，不会伪造百分比；' +
    '期间请勿重复点击（按钮已禁用）。</div>'
}

function quickIntroCard() {
  const steps = [
    ['① 上传', '照片 / Word / PDF / JSON，可多选、可拖拽'],
    ['② 选择输出', 'Word、PDF 或两者都要（默认两者）'],
    ['③ 直接拿结果', '结果页显示产物与自动归类说明，可一键下载'],
  ]
  return '<div class="card quick-hero"><div class="card-head">' +
    '<div class="card-title">整理：上传 → 结果' +
    '<span class="badge badge-muted">POST ' + esc(QUICK_RUN_ENDPOINT) + '</span></div>' +
    '<span class="card-sub">普通用户只需要这一页；核对与修改在次级界面「案件记录」</span></div>' +
    '<div class="card-body"><div class="quick-steps">' +
    steps.map((pair) => '<div class="quick-step"><strong>' + esc(pair[0]) + '</strong>' +
      '<span class="small muted">' + esc(pair[1]) + '</span></div>').join('') +
    '</div>' +
    '<div class="callout callout-warn" style="margin-top:10px"><strong>部位照片（N-11）：</strong>' +
    esc(QUICK_PHOTO_POLICY) + '。系统不对照片做识别、描述、分类或推断，既不运行 OCR 也不调用视觉模型。</div>' +
    '<div class="callout callout-info" style="margin-top:8px">' +
    '<strong>关于“自动判断”：</strong>类型与目标位置留空时由服务端按文件名推测，结果页会逐条标注' +
    '「按文件名推断」，并给出“去案件记录修改”的入口；推测结果不影响原件（原件只读、永不覆盖）。</div>' +
    '</div></div>'
}

function quickUploadCard() {
  const running = Boolean(state.quick.running)
  return '<div class="card"><div class="card-head">' +
    '<div class="card-title">上传文件<span class="badge badge-muted">支持多选</span></div>' +
    '<span class="card-sub">单个文件超过 30 MB 会提示；网关（nginx）单请求上限 64 MB</span></div>' +
    '<div class="card-body">' +
    '<div class="dropzone" data-dropzone data-act="quick-pick" tabindex="0" role="button"' +
    ' aria-label="拖拽文件到此处，或按回车选择文件">' +
    '<svg viewBox="0 0 24 24" width="34" height="34" aria-hidden="true" class="dropzone-icon">' +
    '<path d="M12 17V5" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>' +
    '<path d="M7 10l5-5 5 5" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>' +
    '<path d="M4 15v4h16v-4" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>' +
    '</svg>' +
    '<strong>把照片、Word、PDF 拖到这里</strong>' +
    '<span class="muted">或点击本区域 / 按回车选择文件</span>' +
    '<span class="hint">accept：' + esc(QUICK_ACCEPT) + '</span>' +
    '<input id="q-file" class="visually-hidden-input" type="file" multiple accept="' + esc(QUICK_ACCEPT) +
    '" data-quick-add aria-label="选择要整理的文件" />' +
    '</div>' +
    '</div>' +
    '<div class="card-body">' + quickFileList() + '</div>' +
    '<div class="card-body">' + quickFormatPicker() + quickOptionalFields() + '</div>' +
    '<div class="card-body">' + quickSubmitRow() + '</div>' +
    (running ? '' : '') +
    '</div>'
}

function renderQuickView() {
  const host = $('#view-quick')
  if (!host) return
  host.innerHTML = [
    quickIntroCard(),
    quickUploadCard(),
    state.quick.error
      ? '<div class="callout callout-danger"><strong>上次整理失败：</strong>' + esc(state.quick.error) +
        '　已选文件仍保留，可直接重试，不会重复上传已成功的部分。</div>'
      : '',
  ].join('')
}

/* ------------------------------------------------------------------ 请求 */

/** 直属 fetch 封装：`api.js` 是冻结文件，本次新增端点只在本文件内对接。 */
async function postQuickRun(payload) {
  const response = await fetch(QUICK_RUN_ENDPOINT, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!response.ok) {
    let message = '请求失败 (' + response.status + ')'
    try {
      const body = await response.json()
      if (typeof body.detail === 'string') message = body.detail
      else if (Array.isArray(body.detail)) {
        message = body.detail.map((item) => (item && item.msg ? item.msg : JSON.stringify(item))).join('；')
      }
    } catch (error) { /* 无 JSON 正文时保留 HTTP 兜底文案 */ }
    const error = new Error(message)
    error.status = response.status
    throw error
  }
  return response.json()
}

/** 响应归一化：契约字段缺失时降级为“未记录”而不是编造（N-5）。 */
function normalizeQuickResult(raw) {
  const source = raw && typeof raw === 'object' ? raw : {}
  return {
    raw: source,
    case_id: num(source.case_id, 0),
    case_no: source.case_no || '',
    stage: source.stage || '',
    steps: Array.isArray(source.steps) ? source.steps : [],
    assignments: Array.isArray(source.assignments) ? source.assignments : [],
    summary: source.summary && typeof source.summary === 'object' ? source.summary : {},
    documents: Array.isArray(source.documents) ? source.documents : [],
    notes: Array.isArray(source.notes) ? source.notes.filter((item) => typeof item === 'string') : [],
    case_url: typeof source.case_url === 'string' ? source.case_url : '',
  }
}

function handleQuickError(error) {
  const status = error && error.status ? error.status : 0
  const detail = (error && error.message) ? error.message : String(error || '未知错误')
  if (status === 404) {
    pushBanner('warn', '<strong>整理接口未就绪（404）</strong>：服务端尚未提供 <code>POST ' +
      esc(QUICK_RUN_ENDPOINT) + '</code>。已选文件仍保留；可改用「案件记录」里的四步流程完成同样的工作。' +
      '　<button type="button" class="btn btn-sm" data-act="nav" data-view="cases">去案件记录</button>')
    toast('warn', '整理接口未就绪（404）', '服务端未实现 POST ' + QUICK_RUN_ENDPOINT + '：' + detail +
      '；未上传任何文件，已选列表保持不变。')
    return
  }
  if (status === 413) {
    toast('err', '文件过大（413）', '请求体超过网关上限（64 MB）。请减少文件数量或压缩图片后重试；' +
      '已选列表保持不变。服务端提示：' + detail)
    return
  }
  handleApiError(error, '开始整理')
}

/* ------------------------------------------------------------ 文件选择 */

function addQuickFiles(fileList) {
  const incoming = Array.from(fileList || [])
  if (!incoming.length) return
  const seen = {}
  state.quick.files.forEach((item) => {
    seen[item.name + '|' + item.size + '|' + num(item.file.lastModified, 0)] = true
  })
  const skipped = []
  const big = []
  let added = 0
  incoming.forEach((file) => {
    const key = (file.name || '') + '|' + num(file.size, 0) + '|' + num(file.lastModified, 0)
    if (seen[key]) { skipped.push(file.name || '未命名文件'); return }
    seen[key] = true
    state.quickSeq += 1
    const size = num(file.size, 0)
    state.quick.files.push({
      id: state.quickSeq,
      file: file,                       // 浏览器 File 对象本身：重渲染不丢文件
      name: file.name || '未命名文件',
      size: size,
      media_type: mediaTypeOf(file),
      kind: '',                         // 默认“自动判断”：kind 留空交后端推断
      position_key: '',
      oversize: size > QUICK_MAX_FILE_BYTES,
    })
    added += 1
    if (size > QUICK_MAX_FILE_BYTES) big.push((file.name || '未命名文件') + '（' + quickSizeText(size) + '）')
  })
  state.quick.error = ''
  if (skipped.length) toast('warn', '已跳过重复文件', skipped.join('、'))
  if (big.length) {
    toast('warn', '文件较大（>30 MB）', big.join('、') +
      '：上传会明显变慢；网关（nginx）单请求上限 64 MB，超过会被拒绝。')
  }
  if (quickEstimateBytes() > QUICK_GATEWAY_LIMIT_BYTES) {
    toast('warn', '预计超过网关上限', 'base64 后约 ' + quickSizeText(quickEstimateBytes()) +
      '，超过 64 MB 网关上限，请分批整理。')
  }
  if (added) toast('ok', '已加入待整理列表', '共 ' + added + ' 个文件；类型默认“自动判断”，可在下方逐项修改。')
}

function removeQuickFile(id) {
  state.quick.files = state.quick.files.filter((item) => num(item.id, 0) !== num(id, 0))
  render()
}

function pickQuickFiles() {
  const input = $('#q-file')
  if (input) input.click()
}

function quickReset() {
  stopQuickTicker()
  state.quick.files = []
  state.quick.result = null
  state.quick.error = ''
  state.quick.phase = ''
  state.quick.startedAt = 0
  state.quick.running = false
  state.quick.case_no = ''            // 案件编号必须清空，否则会撞“编号已存在”
  setView('quick')
}

/* ------------------------------------------------------------ 提交整理 */

async function submitQuickRun() {
  if (state.quick.running) {
    toast('warn', '正在整理中', '请等待当前整理完成，不要重复提交。')
    return
  }
  const picked = state.quick.files
  if (!picked.length) {
    toast('warn', '还没有选择文件', '请先拖入或选择照片、Word、PDF 等文件。')
    return
  }
  const view = $('#view-quick')
  if (view) {
    const fields = collect(view, 'data-quick-field')
    state.quick.case_no = (fields.case_no || state.quick.case_no || '').trim()
    state.quick.commission_unit = (fields.commission_unit || state.quick.commission_unit || '').trim()
    state.quick.subject_name = (fields.subject_name || state.quick.subject_name || '').trim()
    state.quick.note = (fields.note || state.quick.note || '').trim()
  }
  state.quick.running = true
  state.quick.error = ''
  state.quick.result = null
  state.quick.startedAt = Date.now()
  state.quick.phase = '读取文件'
  render()
  try {
    const files = []
    for (let index = 0; index < picked.length; index += 1) {
      const item = picked[index]
      setQuickPhaseText('读取文件 ' + (index + 1) + '/' + picked.length + '：' + item.name)
      const content_base64 = await readFileAsBase64(item.file)
      files.push({
        filename: item.name,
        media_type: item.media_type,
        content_base64: content_base64,
        kind: item.kind,
        position_key: item.kind === 'photo' ? item.position_key : '',
      })
    }
    const payload = buildQuickRunPayload(files, {
      formats: state.quick.formats,
      case_no: state.quick.case_no,
      commission_unit: state.quick.commission_unit,
      subject_name: state.quick.subject_name,
      note: state.quick.note,
    })
    startQuickTicker()
    const response = await withLoading(() => postQuickRun(payload))
    stopQuickTicker()
    state.quick.result = normalizeQuickResult(response)
    state.quick.running = false
    state.quick.phase = ''
    state.quick.files = []              // 成功后才清空已选；失败一律保留
    const result = state.quick.result
    try { await loadCases(); await loadStats() } catch (error) { /* 列表刷新失败不影响结果页 */ }
    if (!result.documents.length) {
      toast('warn', '整理已返回，但没有文稿', '请查看结果页的说明与处理步骤；可到「案件记录」检查材料与模板。')
    } else {
      toast('ok', '整理完成', '案件 ' + (result.case_no || ('#' + result.case_id)) + '：生成 ' +
        result.documents.length + ' 份文稿；待处理疑点 ' + num(result.summary.unresolved_count, 0) + ' 条。')
    }
    setView('results')
    return
  } catch (error) {
    stopQuickTicker()
    state.quick.running = false
    state.quick.phase = ''
    state.quick.error = (error && error.message) ? error.message : String(error || '未知错误')
    handleQuickError(error)
    render()
    return
  }
}

/* ------------------------------------------------------------ 结果视图 */

function quickResultHeader(result) {
  const caseId = num(result.case_id, 0)
  const sameOriginUrl = result.case_url && quickUrlAllowed(result.case_url) ? result.case_url : ''
  return '<div class="card quick-hero"><div class="card-head">' +
    '<div class="card-title">' +
    '<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">' +
    '<circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="1.8"/>' +
    '<path d="M8 12.5l2.6 2.6L16 9.6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>' +
    '</svg>整理完成' +
    (result.case_no ? '<span class="badge badge-info">' + esc(result.case_no) + '</span>'
      : '<span class="badge badge-warn">案件编号未记录</span>') +
    (result.stage ? badge('muted', '阶段：' + labelOf(state.cfg.stage_labels, result.stage)) : '') +
    '</div>' +
    '<div class="btn-row">' +
    (caseId ? '<button type="button" class="btn btn-sm btn-primary" data-act="results-open-case" data-id="' + caseId +
      '">查看案件记录</button>' : '') +
    '<button type="button" class="btn btn-sm" data-act="quick-again">再整理一批</button>' +
    '</div></div>' +
    '<div class="card-body">' +
    '<p class="small muted">文稿已生成，可直接用下面的按钮下载。系统不生成鉴定意见（N-1）；' +
    '空白栏目一律是“未记录”，绝不写成“未见异常”（N-5）。</p>' +
    (sameOriginUrl
      ? '<p class="small">服务端案件地址：<a href="' + esc(sameOriginUrl) + '">' + esc(sameOriginUrl) + '</a>' +
        '（同源地址，仅本机/内网可访问）</p>'
      : '') +
    (result.summary && num(result.summary.blocking_count, 0) > 0
      ? '<div class="callout callout-danger"><strong>存在阻断项，不能生成待签发稿，请到案件记录处理。</strong>' +
        '阻断项 ' + num(result.summary.blocking_count, 0) + ' 条。</div>'
      : '') +
    '</div></div>'
}

function quickDocumentItem(doc) {
  const document_ = doc || {}
  const docxUrl = quickDownloadUrl(document_, 'docx')
  const pdfUrl = quickDownloadUrl(document_, 'pdf')
  const hasDocx = quickHasFormat(document_, 'docx')
  const hasPdf = quickHasFormat(document_, 'pdf')
  const docxName = document_.filename_docx || ''
  const pdfName = document_.filename_pdf || ''
  const pdfReason = '本次未导出 PDF：服务端 LibreOffice 不可用或导出失败（校核项 DOCUMENT_PDF_MISSING）。' +
    'Word 仍可下载；可在「案件记录 → 文稿」重新生成后再取 PDF。'
  const blocked = document_.blocked_reason ? String(document_.blocked_reason) : ''
  const docxButton = (hasDocx && docxUrl)
    ? '<a class="btn btn-primary" href="' + esc(docxUrl) + '" download>下载 Word（.docx）</a>'
    : '<button type="button" class="btn" disabled title="' +
      esc(hasDocx ? '服务端未返回可用的下载地址' : '本次未生成 Word 文件') + '">Word 不可用</button>'
  const pdfButton = (hasPdf && pdfUrl)
    ? '<a class="btn btn-primary" href="' + esc(pdfUrl) + '" download>下载 PDF</a>'
    : '<button type="button" class="btn" disabled title="' + esc(pdfReason) + '">PDF 不可用</button>'
  return '<div class="doc-item"><div class="doc-head">' +
    '<div><span class="doc-name">' + esc(docxName || pdfName || ('文稿 #' + num(document_.id, 0))) + '</span>' +
    '<div class="row" style="margin-top:4px">' +
    badge('info', labelOf(state.cfg.doc_status_labels, document_.doc_status, document_.doc_status || '未记录')) +
    badge('muted', '版本 v' + num(document_.version_no, 1)) +
    badge('muted', '类型 ' + esc(document_.doc_type || '未记录')) +
    (hasDocx ? badge('ok', 'Word 已生成') : badge('muted', '无 Word')) +
    (hasPdf ? badge('ok', 'PDF 已生成') : badge('warn', '无 PDF')) +
    '</div></div>' +
    '<div class="btn-row">' + docxButton + pdfButton + '</div>' +
    '</div>' +
    '<div class="kv" style="margin-top:6px">' +
    '<span class="k">Word 文件名</span><span>' + valHtml(docxName) + '</span>' +
    '<span class="k">PDF 文件名</span><span>' + valHtml(pdfName) + '</span>' +
    '</div>' +
    (hasPdf ? '' : '<div class="callout callout-warn" style="margin-top:8px">' + esc(pdfReason) + '</div>') +
    (blocked ? '<div class="callout callout-danger" style="margin-top:8px">服务端阻断原因：' + esc(blocked) + '</div>' : '') +
    (hasDocx && hasPdf && !blocked ? '' : '') +
    '</div>'
}

function quickDocumentsCard(result) {
  const documents = result.documents
  if (!documents.length) {
    return '<div class="card"><div class="card-head"><div class="card-title">产物（Word / PDF）</div>' +
      '<span class="badge badge-warn">本次 0 份</span></div><div class="card-body">' +
      '<div class="callout callout-warn">服务端本次没有返回任何文稿。常见原因：缺少报告模板（template 材料）、' +
      '所有材料都未能识别、或渲染被阻断。请到「案件记录」核对材料清单与校核清单（notes 与处理步骤见下）。</div>' +
      '</div></div>'
  }
  return '<div class="card"><div class="card-head"><div class="card-title">产物（Word / PDF）' +
    '<span class="badge badge-muted">' + documents.length + ' 份</span></div>' +
    '<span class="card-sub">下载地址来自响应 download_urls，缺失时回退 GET /api/documents/{id}/download?fmt=</span></div>' +
    '<div class="card-body">' + documents.map(quickDocumentItem).join('') + '</div></div>'
}

function quickSummaryCard(result) {
  const summary = result.summary || {}
  const blocking = num(summary.blocking_count, 0)
  const cards = [
    { label: '材料', value: num(summary.material_count, 0) },
    { label: '字段', value: num(summary.field_count, 0) },
    { label: '损伤', value: num(summary.injury_count, 0) },
    { label: '文稿', value: num(summary.document_count, 0) },
    { label: '待处理疑点', value: num(summary.unresolved_count, 0), cls: 'is-warn' },
    { label: '阻断项', value: blocking, cls: blocking ? 'is-danger' : '' },
  ]
  const body = cards.map((item) =>
    '<div class="stat-card"><div class="stat-value ' + (item.cls || '') + '">' + item.value + '</div>' +
    '<div class="stat-label">' + esc(item.label) + '</div></div>').join('')
  return '<div class="card"><div class="card-head"><div class="card-title">摘要</div>' +
    '<span class="row">' + badge('warn', '警告 ' + num(summary.warning_count, 0)) +
    badge('muted', '提示 ' + num(summary.info_count, 0)) + '</span></div>' +
    '<div class="card-body"><div class="stat-grid">' + body + '</div>' +
    (blocking
      ? '<div class="callout callout-danger" style="margin-top:8px"><strong>存在阻断项，不能生成待签发稿，请到案件记录处理。</strong></div>'
      : '<div class="callout callout-ok" style="margin-top:8px">没有阻断项；待处理疑点 ' +
        num(summary.unresolved_count, 0) + ' 条仍需专业人员核对后才能签发。</div>') +
    '</div></div>'
}

function quickAssignmentsCard(result) {
  const rows = result.assignments
  if (!rows.length) return ''
  const caseId = num(result.case_id, 0)
  const body = rows.map((item) => {
    const isPhoto = item.kind === 'photo'
    const kindLabel = item.kind_label || labelOf(state.cfg.material_kind_labels, item.kind, '未记录')
    const kindFlag = item.kind_inferred === undefined
      ? badge('muted', '来源未标注')
      : (item.kind_inferred ? badge('warn', '按文件名推断，可修改') : badge('ok', '已指定'))
    const positionCell = isPhoto
      ? (item.position_key
        ? esc(item.position_label || labelOf(positionLabelMap(), item.position_key)) +
          (item.position_inferred ? ' ' + badge('warn', '按文件名推测') : ' ' + badge('ok', '已指定'))
        : valHtml(item.position_label) + ' ' + badge('danger', '未指定位置：不会进入文稿'))
      : '<span class="muted">不适用</span>'
    const reasons = []
    if (item.kind_reason) reasons.push('类型：' + item.kind_reason)
    if (isPhoto && item.position_reason) reasons.push('位置：' + item.position_reason)
    return '<tr>' +
      '<td class="nowrap"><strong>' + esc(item.filename || '未命名文件') + '</strong>' +
      (reasons.length ? '<div class="small muted">' + esc(reasons.join('；')) + '</div>' : '') + '</td>' +
      '<td>' + esc(kindLabel) + ' ' + kindFlag + '</td>' +
      '<td>' + positionCell + '</td>' +
      '<td>' + (caseId
        ? '<button type="button" class="btn btn-sm" data-act="results-open-case" data-id="' + caseId +
          '" data-goto="' + (isPhoto ? 'photo' : 'materials') + '">去案件记录修改</button>'
        : '<span class="muted small">缺少 case_id</span>') + '</td>' +
      '</tr>'
  }).join('')
  return '<div class="card"><div class="card-head"><div class="card-title">自动归类说明' +
    '<span class="badge badge-muted">' + rows.length + ' 个文件</span></div>' +
    '<span class="card-sub">标注“按文件名推断，可修改”的条目请到案件记录确认</span></div>' +
    '<div class="table-wrap"><table class="table"><thead><tr>' +
    '<th>文件</th><th>类型</th><th>目标位置（仅部位照片）</th><th>操作</th>' +
    '</tr></thead><tbody>' + body + '</tbody></table></div></div>'
}

function quickStepsCard(result) {
  if (!result.steps.length) return ''
  return '<div class="card"><div class="card-head"><div class="card-title">服务端处理步骤' +
    '<span class="badge badge-muted">steps</span></div></div><div class="card-body">' +
    result.steps.map((step) => '<div class="quick-step-row">' +
      (step && step.ok ? badge('ok', '完成') : badge('danger', '未完成')) +
      '<span><strong>' + esc((step && step.step) || '未记录') + '</strong>' +
      ((step && step.detail) ? '<span class="small muted">：' + esc(step.detail) + '</span>' : '') + '</span>' +
      '</div>').join('') +
    '</div></div>'
}

function quickNotesCard(result) {
  if (!result.notes.length) return ''
  return '<div class="card"><div class="card-head"><div class="card-title">系统说明（notes）</div>' +
    '<span class="card-sub">未提供模板、PDF 导出失败原因、照片栏位提示等</span></div>' +
    '<div class="card-body"><ul class="note-list">' +
    result.notes.map((note) => '<li>' + esc(note) + '</li>').join('') +
    '</ul></div></div>'
}

function renderResultsView() {
  const host = $('#view-results')
  if (!host) return
  const result = state.quick.result
  if (!result) {
    host.innerHTML = '<div class="card"><div class="card-head"><div class="card-title">结果</div></div>' +
      '<div class="card-body"><div class="empty-note">还没有整理结果。到「整理」页上传文件，' +
      '完成后会自动跳到本页。</div>' +
      '<div class="form-actions"><button type="button" class="btn btn-primary" data-act="nav" data-view="quick">' +
      '去整理</button></div></div></div>'
    return
  }
  host.innerHTML = [
    quickResultHeader(result),
    quickDocumentsCard(result),
    quickSummaryCard(result),
    quickAssignmentsCard(result),
    quickStepsCard(result),
    quickNotesCard(result),
  ].join('')
}

/* ==========================================================================
 * 18c. 次级界面「复核中心」：只用既有端点，汇总“还有哪些案件需要人处理”
 * ========================================================================== */

async function loadReviewCenter() {
  const blocking = await api.listCases({ has_blocking: 'true', page: 1, page_size: 100 })
  const recent = await api.listCases({ page: 1, page_size: 100 })
  state.review.blocking = Array.isArray(blocking && blocking.items) ? blocking.items : []
  const items = Array.isArray(recent && recent.items) ? recent.items.slice() : []
  items.sort((a, b) => num(b.unresolved_count, 0) - num(a.unresolved_count, 0))
  state.review.top = items.filter((item) => num(item.unresolved_count, 0) > 0).slice(0, 12)
  state.review.loaded = true
  state.review.error = ''
}

function reviewTable(items, reason, emptyText) {
  if (!items.length) return '<div class="empty-note">' + esc(emptyText) + '</div>'
  const rows = items.map((item) => {
    const unresolved = num(item.unresolved_count, 0)
    const blocking = num(item.blocking_count, 0)
    return '<tr>' +
      '<td class="nowrap"><strong>' + esc(item.case_no || '未编号') + '</strong>' +
      '<div class="small muted">ID ' + num(item.id, 0) + ' · v' + num(item.version, 1) + '</div></td>' +
      '<td>' + valHtml(item.subject_name) + '</td>' +
      '<td>' + valHtml(item.commission_unit) + '</td>' +
      '<td>' + badge('info', labelOf(state.cfg.stage_labels, item.stage)) + '</td>' +
      '<td class="num">' + (unresolved ? badge('warn', String(unresolved)) : '0') + '</td>' +
      '<td class="num">' + (blocking ? badge('danger', String(blocking)) : '0') + '</td>' +
      '<td class="small">' + esc(reason) + '</td>' +
      '<td><button type="button" class="btn btn-sm btn-primary" data-act="case-open" data-id="' +
      num(item.id, 0) + '">打开工作台</button></td>' +
      '</tr>'
  }).join('')
  return '<div class="table-wrap"><table class="table"><thead><tr>' +
    '<th>案件编号</th><th>姓名</th><th>委托单位</th><th>阶段</th><th class="num">待核对</th>' +
    '<th class="num">阻断</th><th>排序依据</th><th>操作</th></tr></thead><tbody>' + rows + '</tbody></table></div>'
}

function renderReviewView() {
  const host = $('#view-review')
  if (!host) return
  const review = state.review
  if (!review.loaded) {
    host.innerHTML = '<div class="card"><div class="card-body"><div class="empty-note">正在加载复核概览…</div>' +
      '<div class="form-actions"><button type="button" class="btn btn-primary" data-act="review-reload">加载复核概览</button>' +
      '</div></div></div>'
    return
  }
  host.innerHTML = [
    statCards(),
    '<div class="card"><div class="card-head"><div class="card-title">存在未解决阻断项的案件' +
    '<span class="badge badge-danger">GET /api/cases?has_blocking=true</span></div>' +
    '<button type="button" class="btn btn-sm" data-act="review-reload">刷新</button></div>' +
    '<div class="card-body">' +
    reviewTable(review.blocking || [], '服务端筛选 has_blocking=true（最多 100 件）',
      '当前没有未解决阻断项的案件。') +
    '</div></div>',
    '<div class="card"><div class="card-head"><div class="card-title">待核对最多的案件' +
    '<span class="badge badge-warn">前端排序（最多 100 件内）</span></div></div>' +
    '<div class="card-body">' +
    reviewTable(review.top || [], '按 unresolved_count 降序（仅用于安排复核顺序）',
      '最近 100 件里没有待核对字段或损伤。') +
    '<div class="callout callout-info" style="margin-top:8px">排序在前端完成，不是服务端统计；' +
    '“待核对”数量可能包含待确认字段与待核对损伤，具体条目请打开案件工作台。</div>' +
    '</div></div>',
  ].join('')
}

/* ==========================================================================
 * 19. 事件处理（统一委托：click / change / input / keydown / submit）
 * ========================================================================== */

function collect(root, attr) {
  const out = {}
  root.querySelectorAll('[' + attr + ']').forEach((node) => {
    const key = node.getAttribute(attr)
    if (!key) return
    if (node.type === 'checkbox') out[key] = node.checked
    else out[key] = node.value
  })
  return out
}

function intOr(value, fallback) {
  const parsed = parseInt(value, 10)
  return Number.isFinite(parsed) ? parsed : (fallback === undefined ? 0 : fallback)
}

function valuesFromAttributes(root, selector, target) {
  const out = target || {}
  root.querySelectorAll(selector).forEach((node) => {
    const key = node.getAttribute(selector.slice(1, -1))
    if (node.type === 'checkbox') out[key] = node.checked
    else if (node.tagName === 'SELECT' && node.multiple) {
      out[key] = Array.from(node.selectedOptions).map((option) => option.value)
    } else out[key] = node.value
  })
  return out
}

async function reloadCaseAndStats(options) {
  const opts = options || {}
  try {
    await loadCase(state.caseId)
  } catch (error) {
    handleApiError(error, '刷新案件详情')
  }
  try {
    await loadStats()
  } catch (error) { /* 统计失败不阻塞主流程 */ }
  if (opts.photoSlots) {
    try { await loadPhotoSlots() } catch (error) { /* 栏位视图可选 */ }
  }
  if (opts.checklist) {
    try { await loadChecklist() } catch (error) { /* 清单可选 */ }
  }
  if (opts.ocr) {
    try { await loadOcrBlocks() } catch (error) { /* 识别结果可选 */ }
  }
}

async function loadChecklist() {
  if (!state.caseId) { state.checklistItems = null; return }
  const data = await softLoad('校核清单', () => api.checklist(state.caseId, {
    status: state.checklistFilter.status,
    category: state.checklistFilter.category,
  }))
  if (data === null) { state.checklistItems = null; return }
  state.checklistItems = Array.isArray(data) ? data : (Array.isArray(data && data.items) ? data.items : [])
}

function ruleById(ruleId) {
  return state.rules.find((item) => num(item.id, 0) === num(ruleId, 0)) || null
}

function stageResultBox(result, title) {
  if (!result) return
  const detail = result.detail || result.message || '已完成'
  toast('ok', title || '操作完成', detail)
  if (result.payload) state.stageResult = result
}

/* ------------------------------------------------------------------ click */

async function onClick(event) {
  const node = event.target.closest ? event.target.closest('[data-act]') : null
  if (!node) return
  const act = node.dataset.act
  const caseId = state.caseId

  switch (act) {
    case 'nav': {
      const view = node.dataset.view
      if (view === 'rules' && !state.rulesLoaded) {
        await guarded(() => loadRules(), '加载术语规则')
      }
      if (view === 'review' && !state.review.loaded) {
        const ok = await guarded(() => loadReviewCenter(), '加载复核概览')
        if (!ok) state.review.error = '加载失败'
      }
      setView(view)
      return
    }
    case 'quick-pick': {
      // 文件框自身的点击不再冒泡触发本动作，避免无限递归打开对话框
      if (event.target && event.target.id === 'q-file') return
      pickQuickFiles()
      return
    }
    case 'quick-clear': {
      if (!state.quick.files.length) return
      state.quick.files = []
      toast('ok', '已清空待整理列表', '可以重新选择文件。')
      render()
      return
    }
    case 'quick-remove': {
      removeQuickFile(node.dataset.id)
      return
    }
    case 'quick-kind': {
      const id = intOr(node.dataset.id, 0)
      const kind = node.dataset.kind || ''
      state.quick.files.forEach((item) => {
        if (num(item.id, 0) === id) {
          item.kind = kind
          // 部位照片之外不发送 position_key（后端会 400）
          if (kind !== 'photo') item.position_key = ''
        }
      })
      render()
      return
    }
    case 'quick-toggle-optional': {
      state.quick.showOptional = !state.quick.showOptional
      render()
      return
    }
    case 'quick-run': {
      await submitQuickRun()
      return
    }
    case 'quick-again': {
      quickReset()
      return
    }
    case 'review-reload': {
      const ok = await guarded(() => loadReviewCenter(), '加载复核概览')
      if (!ok) state.review.error = '加载失败'
      render()
      return
    }
    case 'results-open-case':
    case 'quick-open-case': {
      const id = intOr(node.dataset.id, 0)
      if (!id) {
        toast('warn', '缺少案件 ID', '服务端响应里没有 case_id，无法打开案件记录。')
        return
      }
      state.preview = null
      state.checklistItems = null
      state.ocrPage = 1
      state.ocrFilterMaterial = ''
      state.normalizeResult = null
      state.stageResult = null
      const data = await guarded(async () => {
        const result = await loadCase(id)
        await loadPhotoSlots()
        await loadOcrBlocks()
        await loadChecklist()
        return result
      }, '打开案件')
      if (!data) { render(); return }
      state.step = 1
      setView('case')
      if (node.dataset.goto === 'photo') {
        const panel = $('#panel-photo')
        if (panel && panel.scrollIntoView) panel.scrollIntoView({ block: 'start' })
      } else if (node.dataset.goto === 'materials') {
        const panel = $('#panel-materials')
        if (panel && panel.scrollIntoView) panel.scrollIntoView({ block: 'start' })
      }
      return
    }
    case 'dismiss-banner': {
      state.banners.splice(intOr(node.dataset.index, 0), 1)
      renderBanners()
      return
    }
    case 'refresh-all': {
      await guarded(async () => {
        await loadConfig(); await loadCases(); await loadStats()
        if (state.caseId) {
          await loadCase(state.caseId)
          await loadPhotoSlots()
          await loadOcrBlocks()
          await loadChecklist()
        }
      }, '整体刷新')
      render()
      return
    }
    case 'stats-reload': {
      await guarded(() => loadStats(), '加载统计')
      render()
      return
    }
    case 'cases-search': {
      const host = $('#view-cases')
      Object.assign(state.filters, collect(host, 'data-filter'))
      state.page = 1
      await guarded(() => loadCases(), '加载案件列表')
      render()
      return
    }
    case 'cases-reset': {
      state.filters = { search: '', stage: '', doc_status: '', material_kind: '', field_status: '', has_blocking: '' }
      state.page = 1
      state.pageSize = 20
      await guarded(() => loadCases(), '加载案件列表')
      render()
      return
    }
    case 'cases-page': {
      state.page = Math.max(1, intOr(node.dataset.page, 1))
      await guarded(() => loadCases(), '翻页')
      render()
      return
    }
    case 'case-open': {
      const id = intOr(node.dataset.id, 0)
      state.preview = null
      state.checklistItems = null
      state.ocrPage = 1
      state.ocrFilterMaterial = ''
      state.normalizeResult = null
      state.stageResult = null
      const data = await guarded(async () => {
        const result = await loadCase(id)
        await loadPhotoSlots()
        await loadOcrBlocks()
        await loadChecklist()
        return result
      }, '打开案件')
      if (!data) { render(); return }
      state.step = 1
      setView('case')
      return
    }
    case 'case-delete': {
      const id = intOr(node.dataset.id, 0)
      const version = intOr(node.dataset.version, 1)
      const caseNo = node.dataset.no || ('#' + id)
      if (!window.confirm('确认删除案件 ' + caseNo + '？该操作会同时删除其材料目录，且不可撤销。')) return
      const result = await guarded(() => api.deleteCase(id, version), '删除案件')
      if (result !== null) {
        toast('ok', '已删除', '案件 ' + caseNo + ' 已删除。')
        if (state.caseId === id) { state.caseId = null; state.caseData = null }
        await guarded(async () => { await loadCases(); await loadStats() }, '刷新列表')
        setView('cases')
      }
      return
    }
    case 'toggle-create': {
      state.showCreateForm = !state.showCreateForm
      render()
      return
    }
    case 'case-create': {
      const host = $('#view-cases')
      const data = collect(host, 'data-create')
      const payload = {
        case_no: (data.case_no || '').trim(),
        commission_unit: (data.commission_unit || '').trim(),
        commission_matter: (data.commission_matter || '').trim(),
        commission_date: (data.commission_date || '').trim(),
        subject_name: (data.subject_name || '').trim(),
        case_summary: (data.case_summary || '').trim(),
        template_version: (data.template_version || '').trim(),
        term_rule_version: (data.term_rule_version || '').trim(),
        intake: {
          input_materials: (data.input_materials || '').trim(),
          output_documents: (data.output_documents || '').trim(),
          template_requirement: (data.template_requirement || '').trim(),
          content_requirement: (data.content_requirement || '').trim(),
          format_requirement: (data.format_requirement || '').trim(),
          final_artifacts: (data.final_artifacts || 'docx,pdf').trim(),
          open_questions: (data.open_questions || '').trim(),
        },
        note: '由无构建工作台建立',
      }
      const created = await guarded(() => api.createCase(payload), '建立案件')
      if (created) {
        toast('ok', '已建立案件', '案件编号：' + (created.case_no || '') + '（ID ' + num(created.id, 0) + '）')
        state.showCreateForm = false
        await guarded(async () => { await loadCases(); await loadStats() }, '刷新列表')
        state.caseData = created
        state.caseId = num(created.id, 0)
        state.step = 1
        await guarded(async () => { await loadPhotoSlots(); await loadOcrBlocks(); await loadChecklist() }, '加载案件数据')
        setView('case')
      }
      return
    }
    case 'case-reload': {
      const data = await guarded(async () => {
        const result = await loadCase(caseId)
        await loadPhotoSlots()
        await loadOcrBlocks()
        await loadChecklist()
        return result
      }, '刷新案件')
      if (data) toast('ok', '已刷新', '案件详情与列表已重新加载。')
      render()
      return
    }
    case 'step': {
      state.step = intOr(node.dataset.step, 1)
      render()
      return
    }
    case 'stage-move': {
      const stage = node.dataset.stage
      const label = labelOf(state.cfg.stage_labels, stage)
      if (!window.confirm('确认把案件阶段变更为“' + label + '”？')) return
      const result = await guarded(() => api.updateStage(caseId, {
        expected_version: currentVersion(),
        stage: stage,
        note: '工作台阶段调整',
      }), '调整阶段')
      if (result) {
        toast('ok', '阶段已更新', '当前阶段：' + label)
        await reloadCaseAndStats({})
      }
      render()
      return
    }
    case 'case-save': {
      const host = $('#view-case')
      const data = collect(host, 'data-case-edit')
      const changes = { expected_version: currentVersion(), note: '工作台修改案件信息' }
      const names = ['case_no', 'commission_unit', 'commission_matter', 'commission_date',
        'subject_name', 'template_version', 'term_rule_version', 'case_summary']
      const caseData = state.caseData || {}
      let changed = 0
      names.forEach((name) => {
        const value = (data[name] || '').trim()
        if (value !== String(caseData[name] || '')) { changes[name] = value; changed += 1 }
      })
      const orderText = (data.section_order || '').trim()
      const currentOrder = Array.isArray(caseData.section_order) ? caseData.section_order.join(',') : ''
      if (orderText !== currentOrder && orderText) {
        changes.section_order = orderText.split(',').map((item) => item.trim()).filter(Boolean)
        changed += 1
      }
      if (!changed) {
        toast('warn', '没有修改', '请至少修改一个字段再保存（CaseUpdate 要求至少一个变更）。')
        return
      }
      const result = await guarded(() => api.updateCase(caseId, changes), '保存案件信息')
      if (result) {
        toast('ok', '已保存', '案件信息已更新（版本 ' + num(result.version, 1) + '）。')
        await reloadCaseAndStats({})
      }
      render()
      return
    }
    case 'opinion-save': {
      const input = $('[data-opinion-input]')
      if (!input) return
      const text = input.value
      // 落库通道：PATCH /api/cases/{id}，body 带 expected_version + opinion_text
      // （schemas.CaseUpdate 已声明 opinion_text；后端已实测 PATCH 成功）。
      const result = await guarded(() => api.updateCase(caseId, {
        expected_version: currentVersion(),
        opinion_text: text,
        note: '鉴定意见由专业人员提供',
      }), '保存鉴定意见')
      if (result) {
        const saved = result.opinion_text || ''
        if (saved === text) {
          toast('ok', '已保存', '鉴定意见已保存（由专业人员提供，系统未做任何推断，N-1）。')
        } else {
          toast('err', '未生效',
            '后端返回的鉴定意见与本次提交内容不一致，无法确认已保存；已刷新案件，请核对后重试。')
        }
        await reloadCaseAndStats({})
      }
      render()
      return
    }
    case 'material-upload': {
      const input = $('#m-file')
      const files = input && input.files ? Array.from(input.files) : []
      if (!files.length) { toast('warn', '未选择文件', '请先选择要上传的材料文件。'); return }
      const form = collect($('#view-case'), 'data-upload')
      const mediaSelect = (form.media_type || '').trim()
      const baseSort = intOr(form.sort_order, 0)
      const ok = await guarded(async () => {
        for (let index = 0; index < files.length; index += 1) {
          const file = files[index]
          const contentBase64 = await readFileAsBase64(file)
          await api.addMaterial(caseId, {
            expected_version: currentVersion(),
            kind: form.kind || 'commission',
            filename: file.name,
            media_type: mediaSelect || mediaTypeOf(file),
            sort_order: baseSort + index,
            clarity_note: (form.clarity_note || '').trim(),
            content_base64: contentBase64,
          })
        }
        return true
      }, '上传材料')
      if (ok) {
        toast('ok', '上传完成', '已登记 ' + files.length + ' 个材料文件到原件目录。')
        await reloadCaseAndStats({})
      }
      render()
      return
    }
    case 'material-preview': {
      state.preview = {
        materialId: intOr(node.dataset.material, 0),
        page: intOr(node.dataset.page, 1),
      }
      render()
      return
    }
    case 'preview-close': {
      state.preview = null
      render()
      return
    }
    case 'page-save': {
      const materialId = intOr(node.dataset.material, 0)
      const pageNumber = intOr(node.dataset.page, 1)
      const card = node.closest('.material-item')
      const block = card ? card.querySelector('[data-page-block="' + pageNumber + '"]') : null
      if (!block) return
      const flags = Array.from(block.querySelectorAll('[data-page-flag]:checked')).map((item) => item.dataset.pageFlag)
      const result = await guarded(() => api.updateMaterialPage(caseId, materialId, pageNumber, {
        expected_version: currentVersion(),
        quality_flags: flags,
        note: '工作台页质量标记',
      }), '保存页质量标记')
      if (result) {
        toast('ok', '已保存', '第 ' + pageNumber + ' 页质量标记：' + (flags.length ? flags.join('、') : '无'))
        await reloadCaseAndStats({})
      }
      render()
      return
    }
    case 'preprocess-run':
    case 'preprocess-all': {
      const materialId = intOr(node.dataset.material, 0)
      let operations = ['autocontrast']
      let pageNumber = 0
      if (act === 'preprocess-run') {
        pageNumber = intOr(node.dataset.page, 1)
        const card = node.closest('.material-item')
        const block = card ? card.querySelector('[data-page-block="' + pageNumber + '"]') : null
        const picked = block
          ? Array.from(block.querySelectorAll('[data-preprocess-op]:checked')).map((item) => item.dataset.preprocessOp)
          : []
        operations = picked.length ? picked : ['autocontrast']
      }
      const result = await guarded(() => api.preprocess(caseId, materialId, {
        expected_version: currentVersion(),
        operations: operations,
        page_number: pageNumber,
      }), '生成识别用副本')
      if (result) {
        toast('ok', '预处理完成', '操作：' + operations.join('、') +
          (pageNumber ? '（第 ' + pageNumber + ' 页）' : '（整份材料）') + '；原件未修改。')
        await reloadCaseAndStats({})
      }
      render()
      return
    }
    case 'ocr-run': {
      const form = collect($('#view-case'), 'data-ocr')
      const materialId = form.material_id ? intOr(form.material_id, 0) : null
      if (form.engine === 'paddle') {
        const engine = state.cfg.engines.find((item) => item.name === 'paddle')
        if (engine && !engine.available) {
          toast('warn', '引擎不可用', 'paddle 引擎不可用（TODO-DL-2），后端会返回 409；请改用 sidecar。')
        }
      }
      const result = await guarded(() => api.runOcr(caseId, {
        expected_version: currentVersion(),
        engine: form.engine || 'sidecar',
        material_id: materialId,
        force: Boolean(form.force),
      }), '运行识别')
      if (result) stageResultBox(result, '识别完成')
      await reloadCaseAndStats({ ocr: true })
      render()
      return
    }
    case 'ocr-blocks-reload': {
      await guarded(() => loadOcrBlocks(), '加载识别结果')
      render()
      return
    }
    case 'ocr-page': {
      state.ocrPage = Math.max(1, intOr(node.dataset.page, 1))
      await guarded(() => loadOcrBlocks(), '翻页识别结果')
      render()
      return
    }
    case 'photo-upload': {
      const input = $('#p-file')
      const files = input && input.files ? Array.from(input.files) : []
      if (!files.length) { toast('warn', '未选择照片', '请先选择部位照片文件。'); return }
      const form = collect($('#view-case'), 'data-photo-upload')
      const baseSort = intOr(form.sort_order, 0)
      const ok = await guarded(async () => {
        for (let index = 0; index < files.length; index += 1) {
          const file = files[index]
          const contentBase64 = await readFileAsBase64(file)
          await api.addMaterial(caseId, {
            expected_version: currentVersion(),
            kind: 'photo',
            filename: file.name,
            media_type: mediaTypeOf(file) === 'other' ? 'png' : mediaTypeOf(file),
            sort_order: baseSort + index,
            position_key: (form.position_key || '').trim(),
            photo_caption: (form.photo_caption || '').trim(),
            content_base64: contentBase64,
          })
        }
        return true
      }, '上传部位照片')
      if (ok) {
        toast('ok', '上传完成', '已上传 ' + files.length + ' 张部位照片；系统未读取照片内容。')
        await reloadCaseAndStats({ photoSlots: true })
      }
      render()
      return
    }
    case 'photo-save': {
      const materialId = intOr(node.dataset.material, 0)
      const pick = $('[data-photo-pick="' + materialId + '"]')
      const caption = $('[data-photo-caption="' + materialId + '"]')
      const sort = $('[data-photo-sort="' + materialId + '"]')
      const result = await guarded(() => api.updatePhotoPlacement(caseId, materialId, {
        expected_version: currentVersion(),
        position_key: pick ? pick.value : '',
        photo_caption: caption ? caption.value : '',
        sort_order: sort ? intOr(sort.value, 0) : 0,
        note: '工作台照片位置指派',
      }), '保存照片位置')
      if (result) {
        toast('ok', '已保存', '照片 #' + materialId + ' 的目标位置已更新（系统只按位置插入，不读照片内容）。')
        await reloadCaseAndStats({ photoSlots: true })
      }
      render()
      return
    }
    case 'photo-clear': {
      const materialId = intOr(node.dataset.material, 0)
      const result = await guarded(() => api.updatePhotoPlacement(caseId, materialId, {
        expected_version: currentVersion(),
        position_key: '',
        note: '工作台清空照片目标位置',
      }), '清空照片位置')
      if (result) {
        toast('warn', '已清空位置', '照片 #' + materialId + ' 未指定位置，将触发 PHOTO_POSITION_MISSING。')
        await reloadCaseAndStats({ photoSlots: true })
      }
      render()
      return
    }
    case 'photo-slots-reload': {
      await guarded(() => loadPhotoSlots(), '加载照片栏位')
      render()
      return
    }
    case 'extract-run': {
      const host = $('#view-case')
      const materialIds = Array.from(host.querySelectorAll('[data-extract-material]:checked'))
        .map((item) => intOr(item.dataset.extractMaterial, 0)).filter(Boolean)
      const form = collect(host, 'data-extract')
      const result = await guarded(() => api.runExtract(caseId, {
        expected_version: currentVersion(),
        material_ids: materialIds,
        replace_existing: form.replace_existing !== false,
        use_llm: Boolean(form.use_llm),
      }), '运行提取')
      if (result) stageResultBox(result, '提取完成')
      await reloadCaseAndStats({ checklist: true })
      render()
      return
    }
    case 'field-save': {
      const key = node.dataset.key
      const card = node.closest('.field-card')
      if (!card) return
      const edits = collect(card, 'data-field-edit')
      const result = await guarded(() => api.updateField(caseId, key, {
        expected_version: currentVersion(),
        status: edits.status,
        confirmed_value: edits.confirmed_value,
        normalized_text: edits.normalized_text,
        unit: edits.unit,
        review_state: edits.review_state,
        note: edits.note || '',
      }), '保存字段核对结果')
      if (result) {
        toast('ok', '已保存', '字段 ' + key + ' 已更新为「' + labelOf(state.cfg.field_status_labels, result.status) +
          ' / ' + labelOf(state.cfg.review_state_labels, result.review_state) + '」。')
        await reloadCaseAndStats({ checklist: true })
      }
      render()
      return
    }
    case 'injury-save': {
      const injuryId = intOr(node.dataset.id, 0)
      const row = node.closest('[data-injury-row]')
      if (!row) return
      const edits = collect(row, 'data-injury-edit')
      const payload = { expected_version: currentVersion(), note: edits.note || '' }
      INJURY_EDIT_FIELDS.forEach(([key]) => {
        payload[key] = key === 'seq' ? intOr(edits[key], 0) : (edits[key] || '')
      })
      payload.status = edits.status
      payload.review_state = edits.review_state
      const result = await guarded(() => api.updateInjury(caseId, injuryId, payload), '保存损伤条目')
      if (result) {
        toast('ok', '已保存', '损伤 #' + injuryId + ' 已更新。')
        await reloadCaseAndStats({ checklist: true })
      }
      render()
      return
    }
    case 'injury-delete': {
      const injuryId = intOr(node.dataset.id, 0)
      if (!window.confirm('确认删除损伤 #' + injuryId + '？')) return
      const result = await guarded(() => api.deleteInjury(caseId, injuryId, currentVersion()), '删除损伤条目')
      if (result !== null) {
        toast('ok', '已删除', '损伤 #' + injuryId + ' 已删除。')
        await reloadCaseAndStats({ checklist: true })
      }
      render()
      return
    }
    case 'injury-create': {
      const host = $('#view-case')
      const data = collect(host, 'data-injury-new')
      const filled = ['body_part', 'side', 'injury_type', 'count', 'length', 'width', 'size_unit',
        'morphology', 'other_traits', 'raw_text'].some((key) => (data[key] || '').trim())
      if (!filled) {
        toast('warn', '内容不足', '至少需要填写一个损伤字段（InjuryCreate 校验）。')
        return
      }
      const injuries = caseInjuries()
      const maxSeq = injuries.reduce((acc, item) => Math.max(acc, num(item.seq, 0)), 0)
      const payload = { expected_version: currentVersion(), seq: maxSeq + 10, status: 'recorded', review_state: 'pending' }
      ;['body_part', 'side', 'injury_type', 'count', 'length', 'width', 'size_unit', 'morphology',
        'other_traits', 'raw_text'].forEach((key) => { payload[key] = (data[key] || '').trim() })
      const result = await guarded(() => api.createInjury(caseId, payload), '新增损伤条目')
      if (result) {
        toast('ok', '已新增', '新增损伤 #' + num(result.id, 0) + '（未填写的字段记为“未记录”，不做推断）。')
        await reloadCaseAndStats({ checklist: true })
      }
      render()
      return
    }
    case 'injury-move': {
      const injuryId = intOr(node.dataset.id, 0)
      const dir = intOr(node.dataset.dir, 1)
      const injuries = caseInjuries().slice()
      const index = injuries.findIndex((item) => num(item.id, 0) === injuryId)
      const target = index + dir
      if (index < 0 || target < 0 || target >= injuries.length) { toast('warn', '无法移动', '已到边界。'); return }
      const swapped = injuries.slice()
      const tmp = swapped[index]
      swapped[index] = swapped[target]
      swapped[target] = tmp
      const ids = swapped.map((item) => num(item.id, 0))
      const result = await guarded(() => api.reorderInjuries(caseId, ids, currentVersion()), '调整损伤顺序')
      if (result) {
        toast('ok', '顺序已更新', '新的顺序：' + ids.join(' → '))
        await reloadCaseAndStats({})
      }
      render()
      return
    }
    case 'normalize-run': {
      const host = $('#view-case')
      const form = collect(host, 'data-normalize')
      const result = await guarded(() => api.runNormalize(caseId, {
        expected_version: currentVersion(),
        target: form.target || 'all',
        rule_version: (form.rule_version || state.cfg.term_rule_version).trim(),
        confirm_safe: Boolean(form.confirm_safe),
      }), '运行术语整理')
      if (result) {
        state.normalizeResult = result
        stageResultBox(result, '术语整理完成')
      }
      await reloadCaseAndStats({ checklist: true })
      render()
      return
    }
    case 'check-run': {
      const host = $('#view-case')
      const form = collect(host, 'data-check')
      const documentId = form.document_id ? intOr(form.document_id, 0) : null
      const result = await guarded(() => api.runCheck(caseId, {
        expected_version: currentVersion(),
        document_id: documentId,
        include_layout: form.include_layout !== false,
      }), '运行校核')
      if (result) stageResultBox(result, '校核完成')
      await reloadCaseAndStats({ checklist: true })
      render()
      return
    }
    case 'check-resolve':
    case 'check-waive':
    case 'check-reopen': {
      const itemId = intOr(node.dataset.id, 0)
      const noteInput = $('[data-check-note="' + itemId + '"]')
      const note = noteInput ? noteInput.value.trim() : ''
      const status = act === 'check-resolve' ? 'resolved' : (act === 'check-waive' ? 'waived' : 'open')
      if (status === 'waived' && !note) {
        toast('warn', '必须填写理由', '豁免校核项必须填写理由（ChecklistItemUpdate 校验）。')
        return
      }
      const result = await guarded(() => api.updateChecklistItem(itemId, {
        expected_version: currentVersion(),
        status: status,
        note: note,
        resolved_by: '复核人',
      }), '处理校核项')
      if (result) {
        toast('ok', '已更新', '校核项 #' + itemId + ' 状态：' + labelOf(state.cfg.check_item_status_labels, status))
        await reloadCaseAndStats({ checklist: true })
      }
      render()
      return
    }
    case 'doc-create': {
      const host = $('#view-case')
      const form = collect(host, 'data-doc-create')
      const formats = Array.from(host.querySelectorAll('[data-doc-create-format]:checked'))
        .map((item) => item.dataset.docCreateFormat)
      if (!formats.length) { toast('warn', '缺少输出格式', '至少选择一种输出格式（docx/pdf）。'); return }
      const result = await guarded(() => api.createDocument(caseId, {
        expected_version: currentVersion(),
        doc_type: (form.doc_type || 'report').trim(),
        doc_status: form.doc_status || 'internal_draft',
        template_version: (form.template_version || state.cfg.template_version).trim(),
        formats: formats,
        note: form.note || '',
      }), '生成文稿')
      if (result) {
        toast('ok', '文稿已生成', '版本 v' + num(result.version_no, 1) + '（' + labelOf(state.cfg.doc_status_labels, result.doc_status) + '）')
        await reloadCaseAndStats({ checklist: true })
      }
      render()
      return
    }
    case 'doc-approve': {
      const documentId = intOr(node.dataset.id, 0)
      const result = await guarded(() => api.approveDocument(documentId, {
        expected_version: currentVersion(),
        document_id: documentId,
        include_layout: true,
      }), '标记已审核')
      if (result) {
        toast('ok', '已审核', '文稿 #' + documentId + ' 已标记为已审核。')
        await reloadCaseAndStats({ checklist: true })
      }
      render()
      return
    }
    case 'doc-deliver': {
      const documentId = intOr(node.dataset.id, 0)
      const result = await guarded(() => api.deliver(caseId, {
        expected_version: currentVersion(),
        document_id: documentId,
        formats: ['docx', 'pdf'],
        note: '工作台保存交付',
      }), '保存交付')
      if (result) {
        toast('ok', '已保存交付', '交付记录已保存（仅已审核文稿可交付，INV-8）。')
        await reloadCaseAndStats({})
      }
      render()
      return
    }
    case 'correction-create': {
      const host = $('#view-case')
      const form = collect(host, 'data-correction')
      if (!(form.reason || '').trim()) { toast('warn', '缺少原因', '修正原因 reason 必填。'); return }
      if ((form.before_text || '') === (form.after_text || '')) {
        toast('warn', '无需修正', '修正前后内容不能相同。')
        return
      }
      const payload = {
        expected_version: currentVersion(),
        kind: form.kind,
        target_type: form.target_type,
        target_id: intOr(form.target_id, 0),
        before_text: form.before_text || '',
        after_text: form.after_text || '',
        reason: (form.reason || '').trim(),
        confirmed_by: (form.confirmed_by || '复核人').trim(),
        scope: form.scope || 'case_only',
        scope_value: (form.scope_value || '').trim(),
        apply_to_case: form.apply_to_case !== false,
      }
      const result = await guarded(() => api.createCorrection(caseId, payload), '提交修正')
      if (result) {
        toast('ok', '修正已记录', '受影响项 ' + ((result.affected || []).length) + ' 项，失效复核 ' +
          ((result.invalidated || []).length) + ' 项。')
        await reloadCaseAndStats({ checklist: true })
      }
      render()
      return
    }
    case 'rules-search': {
      const host = $('#view-rules')
      Object.assign(state.ruleFilter, collect(host, 'data-rule-filter'))
      await guarded(() => loadRules(), '加载术语规则')
      render()
      return
    }
    case 'rule-create': {
      const host = $('#view-rules')
      const data = collect(host, 'data-rule-new')
      const payload = {
        code: (data.code || '').trim(),
        shorthand: (data.shorthand || '').trim(),
        pattern: (data.pattern || '').trim(),
        match_type: data.match_type || 'literal',
        template: (data.template || '').trim(),
        scope_field_key: (data.scope_field_key || '').trim(),
        scope_column: (data.scope_column || '').trim(),
        scope_writer: (data.scope_writer || '').trim(),
        boundary_note: (data.boundary_note || '').trim(),
        require_groups: (data.require_groups || '').split(',').map((item) => item.trim()).filter(Boolean),
        missing_group_note: (data.missing_group_note || '').trim(),
        requires_confirmation: data.requires_confirmation === 'true',
        priority: intOr(data.priority, 100),
        active: data.active !== 'false',
      }
      if (!payload.code || !payload.pattern) {
        toast('warn', '缺少必填项', '规则代码 code 与匹配式 pattern 必填。')
        return
      }
      const result = await guarded(() => api.createRule(payload), '新增术语规则')
      if (result) {
        toast('ok', '规则已新增', 'code=' + payload.code)
        await guarded(() => loadRules(), '刷新规则列表')
      }
      render()
      return
    }
    case 'rule-save': {
      const ruleId = intOr(node.dataset.id, 0)
      const rule = ruleById(ruleId)
      if (!rule) return
      const row = node.closest('[data-rule-row]')
      const data = collect(row, 'data-rule-edit')
      const payload = {
        expected_version: num(rule.version, 1),
        shorthand: data.shorthand || '',
        pattern: data.pattern || '',
        match_type: data.match_type || 'literal',
        template: data.template || '',
        scope_field_key: data.scope_field_key || '',
        scope_column: data.scope_column || '',
        scope_writer: data.scope_writer || '',
        boundary_note: data.boundary_note || '',
        require_groups: (data.require_groups || '').split(',').map((item) => item.trim()).filter(Boolean),
        missing_group_note: data.missing_group_note || '',
        requires_confirmation: data.requires_confirmation === 'true',
        priority: intOr(data.priority, 100),
        note: '工作台修改规则',
      }
      const result = await guarded(() => api.updateRule(ruleId, payload), '保存术语规则')
      if (result) {
        toast('ok', '规则已保存', 'code=' + (rule.code || '') + '，规则版本 v' + num(result.version, 1))
        await guarded(() => loadRules(), '刷新规则列表')
      }
      render()
      return
    }
    case 'rule-toggle': {
      const ruleId = intOr(node.dataset.id, 0)
      const rule = ruleById(ruleId)
      if (!rule) return
      const active = node.dataset.active === 'true'
      const result = await guarded(() => api.updateRule(ruleId, {
        expected_version: num(rule.version, 1),
        active: active,
        note: active ? '工作台启用规则' : '工作台停用规则',
      }), '切换规则状态')
      if (result) {
        toast('ok', '状态已更新', '规则 ' + (rule.code || '') + '：' + (active ? '启用' : '停用'))
        await guarded(() => loadRules(), '刷新规则列表')
      }
      render()
      return
    }
    case 'rule-delete': {
      const ruleId = intOr(node.dataset.id, 0)
      const rule = ruleById(ruleId)
      if (!rule) return
      if (!window.confirm('确认删除规则 ' + (rule.code || '') + '？')) return
      const result = await guarded(() => api.deleteRule(ruleId, num(rule.version, 1)), '删除术语规则')
      if (result !== null) {
        toast('ok', '已删除', '规则 ' + (rule.code || '') + ' 已删除。')
        await guarded(() => loadRules(), '刷新规则列表')
      }
      render()
      return
    }
    case 'config-reload': {
      await guarded(async () => { await loadConfig(); await loadHealth() }, '加载配置')
      render()
      return
    }
    case 'health-check': {
      const result = await guarded(() => loadHealth(), '探测后端')
      if (result) toast('ok', '后端可用', 'GET /api/health 返回：' + JSON.stringify(result))
      render()
      return
    }
    case 'ai-verify': {
      if (!state.apiKey) { toast('warn', '缺少密钥', '请先在上方输入 API Key（仅保存在内存）。'); return }
      const result = await guarded(() => api.verifyAi(state.apiKey), '测试模型连通性')
      if (result) {
        state.aiResult = result
        toast('ok', '连通性正常', JSON.stringify(result))
      }
      render()
      return
    }
    case 'api-key-clear': {
      state.apiKey = ''
      const input = $('#ai-key')
      if (input) input.value = ''
      toast('ok', '已清除', '内存中的密钥已清除。')
      render()
      return
    }
    default:
      return
  }
}

/* ---------------------------------------------------------------- change */

async function onChange(event) {
  const node = event.target
  if (!node) return
  if (node.matches('[data-filter]') && node.tagName === 'SELECT') {
    const host = $('#view-cases')
    Object.assign(state.filters, collect(host, 'data-filter'))
    state.page = 1
    await guarded(() => loadCases(), '加载案件列表')
    render()
    return
  }
  if (node.matches('[data-page-size]')) {
    state.pageSize = intOr(node.value, 20)
    state.page = 1
    await guarded(() => loadCases(), '加载案件列表')
    render()
    return
  }
  if (node.matches('[data-act="ocr-filter"]')) {
    state.ocrFilterMaterial = node.value
    state.ocrPage = 1
    await guarded(() => loadOcrBlocks(), '筛选识别结果')
    render()
    return
  }
  if (node.matches('[data-act="ocr-page-size"]')) {
    state.ocrPageSize = intOr(node.value, 20)
    state.ocrPage = 1
    await guarded(() => loadOcrBlocks(), '筛选识别结果')
    render()
    return
  }
  if (node.matches('[data-act="checklist-filter"]')) {
    state.checklistFilter[node.dataset.field] = node.value
    const ok = await guarded(() => loadChecklist(), '筛选校核清单')
    if (!ok) state.checklistFilter[node.dataset.field] = ''
    render()
    return
  }
  if (node.matches('[data-rule-filter]')) {
    const host = $('#view-rules')
    Object.assign(state.ruleFilter, collect(host, 'data-rule-filter'))
    await guarded(() => loadRules(), '加载术语规则')
    render()
    return
  }
  if (node.matches('[data-rule-draft-toggle]')) {
    state.ruleDraftOpen = node.checked
    render()
    return
  }
  if (node.matches('[data-upload="files"]') || node.matches('[data-photo-upload="files"]')) {
    const count = node.files ? node.files.length : 0
    if (count) toast('ok', '已选择文件', '共 ' + count + ' 个文件待上传（浏览器 FileReader 读为 base64）。')
    return
  }
  if (node.matches('[data-quick-add]')) {
    addQuickFiles(node.files)
    node.value = ''            // 允许再次选择同一个文件
    render()
    return
  }
  if (node.matches('[data-quick-format]')) {
    state.quick.formats = quickFormatsFromChoice(node.value)
    render()
    return
  }
  if (node.matches('[data-quick-position]')) {
    const id = intOr(node.getAttribute('data-quick-position'), 0)
    state.quick.files.forEach((item) => {
      if (num(item.id, 0) === id) item.position_key = node.value || ''
    })
    setQuickPhaseText(quickPhaseText())
    return
  }
}

/* ---------------------------------------------------------------- input */

function onInput(event) {
  const node = event.target
  if (!node) return
  if (node.matches('[data-act="api-key"]')) {
    // 只写入内存变量；不落 localStorage，也不写回 DOM 属性。
    state.apiKey = node.value
    return
  }
  if (node.matches('[data-quick-field]')) {
    // 与内存同步，避免重渲染丢失用户输入
    const key = node.getAttribute('data-quick-field')
    if (key && Object.prototype.hasOwnProperty.call(state.quick, key)) state.quick[key] = node.value
  }
}

/* ---------------------------------------------------------------- keydown */

async function onKeydown(event) {
  if (event.key !== 'Enter') return
  const node = event.target
  if (!node) return
  if (state.view === 'quick' && node.closest && node.closest('#view-quick')) {
    if (node.type === 'file') return                       // 文件框的回车交给浏览器
    if (node.dataset && node.dataset.act === 'quick-pick') { // 拖拽区：回车 = 选择文件
      event.preventDefault()
      pickQuickFiles()
      return
    }
    if (node.tagName === 'BUTTON' || node.tagName === 'A') return  // 自带 click 语义
    event.preventDefault()
    await submitQuickRun()
    return
  }
  if (node.id === 'f-search' || node.id === 'ck-cat') {
    event.preventDefault()
    const host = $('#view-cases')
    Object.assign(state.filters, collect(host, 'data-filter'))
    state.page = 1
    await guarded(() => loadCases(), '加载案件列表')
    render()
    return
  }
  if (node.matches('[data-rule-filter="search"]')) {
    event.preventDefault()
    const host = $('#view-rules')
    Object.assign(state.ruleFilter, collect(host, 'data-rule-filter'))
    await guarded(() => loadRules(), '加载术语规则')
    render()
  }
}

function onSubmit(event) {
  event.preventDefault()
}

/* -------------------------------------------------------------- drag/drop */

function dropZoneOf(event) {
  const node = event.target
  if (!node || !node.closest) return null
  return node.closest('[data-dropzone]')
}

function onDragEnter(event) {
  const zone = dropZoneOf(event)
  if (!zone) return
  event.preventDefault()
  zone.classList.add('is-drag')
}

function onDragOver(event) {
  const zone = dropZoneOf(event)
  if (!zone) return
  event.preventDefault()
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy'
  zone.classList.add('is-drag')
}

function onDragLeave(event) {
  const zone = dropZoneOf(event)
  if (!zone) return
  if (event.relatedTarget && zone.contains(event.relatedTarget)) return
  zone.classList.remove('is-drag')
}

function onDrop(event) {
  const zone = dropZoneOf(event)
  // 无论落在哪里都要阻止浏览器直接打开文件（避免丢失当前页面状态）
  event.preventDefault()
  if (!zone) return
  zone.classList.remove('is-drag')
  const files = (event.dataTransfer && event.dataTransfer.files)
    ? Array.from(event.dataTransfer.files) : []
  if (!files.length) {
    toast('warn', '没有读到文件', '如果是文件夹或浏览器不支持的类型，请改用“选择文件”按钮。')
    return
  }
  if (state.view !== 'quick') setView('quick')
  addQuickFiles(files)
  render()
}

/* ==========================================================================
 * 20. 启动
 * ========================================================================== */

async function bootstrap() {
  document.addEventListener('click', (event) => { void onClick(event) })
  document.addEventListener('change', (event) => { void onChange(event) })
  document.addEventListener('input', (event) => { onInput(event) })
  document.addEventListener('keydown', (event) => { void onKeydown(event) })
  document.addEventListener('submit', onSubmit)
  // 拖拽上传：只在主视图「整理」的拖拽区生效，drop 一律阻止浏览器打开文件
  document.addEventListener('dragenter', onDragEnter)
  document.addEventListener('dragover', onDragOver)
  document.addEventListener('dragleave', onDragLeave)
  document.addEventListener('drop', onDrop)

  const failures = []
  try {
    await withLoading(() => loadConfig())
  } catch (error) {
    failures.push('config')
    pushBanner('warn', '<strong>无法加载 GET /api/config。</strong>' +
      '界面已降级使用内置枚举与照片位置目录；服务端提示：' + esc(error && error.message ? error.message : String(error)))
  }
  try {
    await withLoading(() => loadHealth())
  } catch (error) {
    failures.push('health')
  }
  try {
    await withLoading(() => loadCases())
  } catch (error) {
    failures.push('cases')
    pushBanner('danger', '<strong>无法加载 GET /api/cases。</strong>服务端提示：' +
      esc(error && error.message ? error.message : String(error)))
  }
  try {
    await withLoading(() => loadStats())
  } catch (error) {
    failures.push('stats')
  }
  state.booted = true
  // 默认打开主视图「整理」：普通用户只需要“上传 → 结果”（Transparent）
  setView('quick')
  if (failures.length) {
    toast('warn', '部分接口不可用', '失败项：' + failures.join('、') + '（其余界面仍可使用）')
  }
}

// 浏览器中在 DOM 就绪后启动；在 Node 中导入本模块（静态自检）时不注册监听。
if (typeof window !== 'undefined' && typeof document !== 'undefined') {
  window.addEventListener('DOMContentLoaded', () => { void bootstrap() })
}

/* 供静态自检脚本读取（不影响浏览器运行）：纯函数与契约常量，可在 Node 中直接断言。
 * `renderQuickView` / `renderResultsView` / `quickState` / `setQuickResult` 只用于
 * %TEMP% 的离线冒烟脚本在无浏览器环境下核对渲染出的 HTML，浏览器中不经过它们。 */
export const __workbenchInternals = {
  ENDPOINT_MANIFEST,
  QUICK_RUN_ENDPOINT,
  QUICK_KIND_CHIPS,
  QUICK_ACCEPT,
  QUICK_PHOTO_POLICY,
  QUICK_MAX_FILE_BYTES,
  QUICK_GATEWAY_LIMIT_BYTES,
  normalizeConfig,
  buildQuickRunPayload,
  quickFormatsFromChoice,
  quickFormatChoice,
  quickDownloadUrl,
  quickHasFormat,
  quickPhaseText,
  mediaTypeOf,
  normalizeQuickResult,
  renderQuickView,
  renderResultsView,
  quickState: () => state.quick,
  setQuickResult: (result) => { state.quick.result = result },
}

