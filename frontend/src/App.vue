<script setup>
/**
 * 法医尸表检验文书整理 —— 前端根组件（Vue 3 源码，已通过 `npm run build` 构建验证）。
 *
 * 职责：
 * 1. 拉取 /api/config 与 /api/stats，构建全局字典（阶段、状态、材料类型…）传给子组件；
 * 2. 主流程（Transparent）：默认首屏是「整理」（QuickRun.vue：上传 → 一次 POST /api/quick-run → 结果），
 *    普通用户只看到上传与结果；案件字段梳理退到次级界面「案件记录」；
 * 3. 次级界面：案件列表、案件工作台（四步主流程 R-1）、复核中心、术语规则、系统配置；
 * 4. 全局 toast / 错误横幅，以及 409「已被其他操作更新，请刷新后重试」的统一处理。
 *
 * 组件间只通过 props / emits 通信；所有接口调用走 api.js。
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { CircleAlert, CircleCheck, CircleX, KeyRound, RefreshCw, Settings } from 'lucide-vue-next'
import { api } from './api.js'
import Sidebar from './components/Sidebar.vue'
import StatsCards from './components/StatsCards.vue'
import QuickRun from './components/QuickRun.vue'
import CaseTable from './components/CaseTable.vue'
import CreateCaseModal from './components/CreateCaseModal.vue'
import CaseWorkspace from './components/CaseWorkspace.vue'
import TerminologyRulesPanel from './components/TerminologyRulesPanel.vue'
import AISettingsModal from './components/AISettingsModal.vue'

/* ------------------------------------------------------------------ *
 * 本地兜底字典：/api/config 未返回或缺少某个键时使用，保证界面不出现英文枚举。
 * ------------------------------------------------------------------ */
const FALLBACK = {
  stageLabels: {
    intake: '接收材料',
    image_check: '检查图像',
    ocr: '识别原文',
    extract: '提取字段',
    verify_facts: '核对关键事实',
    normalize: '整理术语',
    render: '填充模板',
    check_document: '校核文档',
    professional_review: '专业复核',
    delivered: '保存交付',
  },
  fieldStatusLabels: {
    recorded: '明确记载',
    not_recorded: '未记录',
    not_examined: '明确未检',
    negative: '明确阴性所见',
    unclear: '待辨认或有冲突',
  },
  reviewStateLabels: { pending: '待核对', confirmed: '已确认' },
  materialKindLabels: {
    commission: '委托书',
    record_book: '记录本',
    photo: '部位照片',
    template: '报告模板',
    approved_report: '已审核报告',
    terminology_reference: '简写对照表',
    supplement: '补充资料',
  },
  qualityFlagLabels: {
    blurred: '模糊',
    cropped: '裁切',
    rotated: '方向异常',
    occluded: '遮挡',
    altered: '涂改',
  },
  docStatusLabels: {
    internal_draft: '内部草稿',
    pending_issue: '待签发稿',
    approved: '已审核',
    superseded: '已被替代',
  },
  checkCategoryLabels: {
    recognition: '识别质量',
    content: '内容质量',
    source: '来源完整性',
    format: '格式质量',
    correction: '修改可靠性',
  },
  checkSeverityLabels: { blocking: '阻断', warning: '警告', info: '提示' },
  checkStatusLabels: { open: '待处理', resolved: '已解决', waived: '已豁免' },
  correctionKindLabels: {
    recognition: '识别纠正',
    terminology: '术语纠正',
    format: '格式纠正',
    workflow_order: '工作顺序调整',
  },
  correctionScopeLabels: {
    case_only: '仅当前案件',
    field_key: '适用栏目',
    column: '适用记录栏目',
    writer: '适用书写者',
    template: '适用模板',
  },
  photoSectionLabels: { full: '全身', head: '头颈部', body: '躯干', limb: '四肢', other: '其他' },
}

const PHOTO_SECTION_KEYS = ['full', 'head', 'body', 'limb', 'other']

/* ------------------------------------------------------------------ *
 * 全局状态
 * ------------------------------------------------------------------ */
// 默认首屏 = 主视图「整理」：普通用户只需要“上传 → 结果”（Transparent 主流程）。
const activeView = ref('quick')
const sidebarOpen = ref(false)

// 主视图「整理」的实例：文件拖到页面任意位置都由它接管（与 static 版的文档级拖拽一致）。
const quickRef = ref(null)
// 「结果」导航徽标：尚未整理 / 待处理 N / 阻断 N / 已完成（对齐 static 版 renderResultsNavBadge）。
const quickBadge = ref({ has: false, blocking: 0, unresolved: 0 })

const config = ref({})
const configLoaded = ref(false)
const configError = ref('')

const stats = ref({})
const statsLoading = ref(false)

const casesPayload = ref({ items: [], total: 0, page: 1, page_size: 20 })
const listLoading = ref(false)
const listError = ref('')
const filters = ref({
  search: '',
  stage: '',
  doc_status: '',
  material_kind: '',
  has_blocking: '',
  page: 1,
  page_size: 20,
})

const selectedCase = ref(null)
const caseLoading = ref(false)
const workspaceOpen = ref(false)

const showCreate = ref(false)
const createSaving = ref(false)
const createError = ref('')

const showAi = ref(false)
const aiKey = ref('')
const aiStatus = ref('unconfigured')
const aiBusy = ref(false)

const toast = ref({ visible: false, tone: 'info', message: '' })
let toastTimer = null

/* ------------------------------------------------------------------ *
 * 工具
 * ------------------------------------------------------------------ */
function isPlainObject(value) {
  return !!value && typeof value === 'object' && !Array.isArray(value)
}

function firstArray(...values) {
  for (const value of values) {
    if (Array.isArray(value)) return value
  }
  return []
}

function notify(message, tone = 'info') {
  toast.value = { visible: true, tone, message: String(message || '') }
  if (toastTimer) window.clearTimeout(toastTimer)
  toastTimer = window.setTimeout(() => {
    toast.value = { ...toast.value, visible: false }
  }, 6000)
}

function dismissToast() {
  toast.value = { ...toast.value, visible: false }
}

/**
 * 全局错误处理。409 统一刷新案件详情与列表，并提示「已被其他操作更新，请刷新后重试」，
 * 同时保留后端给出的具体原因（阻断项、编号重复、非法状态流转）。
 */
async function handleError(error) {
  const status = error && error.status ? error.status : 0
  const detail = error && error.message ? String(error.message) : ''
  if (status === 409) {
    const base = '已被其他操作更新，请刷新后重试'
    notify(detail && detail !== base ? `${base}：${detail}` : base, 'error')
    listError.value = detail
    await refreshAll()
    return
  }
  if (status === 404) {
    notify(detail || '资源不存在，可能已被删除', 'error')
    await refreshAll()
    return
  }
  notify(detail || '操作失败，请稍后重试', 'error')
}

/* ------------------------------------------------------------------ *
 * 全局字典（传给所有子组件，避免各自硬编码英文枚举）
 * /api/config 的实际形态（backend/app/routes.py）：
 *   - 枚举字典是 [{key,label}] 数组：field_statuses / review_states / material_kinds /
 *     doc_statuses / check_categories / check_severities / check_item_statuses /
 *     correction_kinds / correction_scopes / image_quality_flags / photo_sections / stages
 *   - 目录：field_definitions / field_groups / photo_positions / required_field_keys
 *   - 状态：engines / todo_downloads / template_version / term_rule_version
 * 这里同时兼容「对象字典」与「{key,label} 数组」两种形态。
 * ------------------------------------------------------------------ */
function toLabelMap(...values) {
  for (const value of values) {
    if (isPlainObject(value)) return value
    if (Array.isArray(value)) {
      const map = {}
      value.forEach((item) => {
        if (!item || typeof item !== 'object') return
        const key = item.key ?? item.name ?? item.position_key ?? item.value
        if (key === undefined || key === null || key === '') return
        map[String(key)] = String(item.label ?? item.name ?? key)
      })
      if (Object.keys(map).length) return map
    }
  }
  return null
}

const dict = computed(() => {
  const raw = config.value || {}
  const stages = firstArray(raw.stages)
  const stageOrder = firstArray(raw.stage_order, raw.case_stages)
  const stageKeys = stages.length
    ? stages.map((item) => (typeof item === 'string' ? item : item && item.key)).filter(Boolean)
    : stageOrder.length
      ? stageOrder.map(String)
      : Object.keys(FALLBACK.stageLabels)
  return {
    stageLabels:
      toLabelMap(stages, raw.stage_labels, raw.stageLabels) || FALLBACK.stageLabels,
    stageKeys,
    fieldStatusLabels:
      toLabelMap(raw.field_statuses, raw.field_status_labels, raw.fieldStatusLabels) ||
      FALLBACK.fieldStatusLabels,
    reviewStateLabels:
      toLabelMap(raw.review_states, raw.review_state_labels, raw.reviewStateLabels) ||
      FALLBACK.reviewStateLabels,
    materialKindLabels:
      toLabelMap(raw.material_kinds, raw.material_kind_labels, raw.materialKindLabels) ||
      FALLBACK.materialKindLabels,
    qualityFlagLabels:
      toLabelMap(raw.image_quality_flags, raw.quality_flag_labels, raw.qualityFlagLabels) ||
      FALLBACK.qualityFlagLabels,
    docStatusLabels:
      toLabelMap(raw.doc_statuses, raw.doc_status_labels, raw.docStatusLabels) ||
      FALLBACK.docStatusLabels,
    checkCategoryLabels:
      toLabelMap(raw.check_categories, raw.check_category_labels, raw.checkCategoryLabels) ||
      FALLBACK.checkCategoryLabels,
    checkSeverityLabels:
      toLabelMap(raw.check_severities, raw.check_severity_labels, raw.checkSeverityLabels) ||
      FALLBACK.checkSeverityLabels,
    checkStatusLabels:
      toLabelMap(raw.check_item_statuses, raw.check_status_labels, raw.checkStatusLabels) ||
      FALLBACK.checkStatusLabels,
    correctionKindLabels:
      toLabelMap(raw.correction_kinds, raw.correction_kind_labels, raw.correctionKindLabels) ||
      FALLBACK.correctionKindLabels,
    correctionScopeLabels:
      toLabelMap(raw.correction_scopes, raw.correction_scope_labels, raw.correctionScopeLabels) ||
      FALLBACK.correctionScopeLabels,
    photoSectionLabels:
      toLabelMap(raw.photo_sections, raw.photo_section_labels, raw.photoSectionLabels) ||
      FALLBACK.photoSectionLabels,
    fieldGroupLabels: toLabelMap(raw.field_groups) || {},
    fieldDefinitions: firstArray(raw.field_definitions),
    requiredFieldKeys: firstArray(raw.required_field_keys, raw.requiredFieldKeys),
    criticalFieldKeys: firstArray(raw.critical_field_keys, raw.criticalFieldKeys),
    ocrMaterialKinds: firstArray(raw.ocr_material_kinds).map(String),
    /**
     * 识别引擎选项（实测冻结形态）：可用性只能来自 engines.ocr=[{name,available,detail}]。
     * 顶层 ocr_engines **只是名字数组**，不含可用性 —— 绝不能据此把 paddle 显示为可用；
     * 此时一律标为不可用并在界面上说明原因。
     */
    ocrEngineOptions: (() => {
      const engines = isPlainObject(raw.engines) ? raw.engines : null
      const list = engines ? firstArray(engines.ocr) : []
      if (list.length) {
        return list
          .map((item) => {
            if (typeof item === 'string') {
              return { name: item, available: false, detail: '后端未提供该引擎的可用性（engines.ocr 缺失），按不可用处理' }
            }
            if (!isPlainObject(item)) return null
            const name = String(item.name || item.engine || '')
            if (!name) return null
            return {
              name,
              available: !!item.available,
              detail: String(item.detail || item.description || ''),
            }
          })
          .filter(Boolean)
      }
      return firstArray(raw.ocr_engines).map((name) => ({
        name: String(name),
        available: false,
        detail: '后端 /api/config 未提供该引擎的可用性（engines.ocr 缺失），按不可用处理',
      }))
    })(),
    defaultOcrEngine: String(raw.default_ocr_engine || 'sidecar'),
    // 供建案表单与文稿面板使用的默认版本号
    templateVersion: String(raw.template_version || ''),
    termRuleVersion: String(raw.term_rule_version || ''),
    draftMarker: String(raw.draft_marker || '【待核对】'),
    photoPolicy: String(raw.photo_policy || '系统不读取照片内容，只按用户指定的位置插入（N-11）'),
    demoNote: String(raw.demo_note || ''),
  }
})

/** 部位照片位置目录：/api/config 的 photo_positions（[{key,label,section,section_label,order,anchor,wide}]）。 */
const photoPositions = computed(() => {
  const raw = config.value || {}
  const source = firstArray(raw.photo_positions, raw.photoPositions, raw.photo_position_definitions)
  const rows = []
  source.forEach((item) => {
    if (typeof item === 'string') {
      rows.push({ key: item, label: item, section: 'other', sectionLabel: '', order: 900, wide: false, anchor: `photo_${item}` })
      return
    }
    if (!isPlainObject(item)) return
    const key = String(item.key || item.value || '')
    if (!key) return
    rows.push({
      key,
      label: String(item.label || item.name || key),
      section: String(item.section || 'other'),
      sectionLabel: String(item.section_label || ''),
      order: Number(item.order || 900),
      wide: !!item.wide,
      anchor: String(item.anchor || `photo_${key}`),
    })
  })
  if (!rows.length && isPlainObject(raw.photo_positions)) {
    Object.entries(raw.photo_positions).forEach(([key, label], index) => {
      rows.push({
        key,
        label: typeof label === 'string' ? label : key,
        section: 'other',
        sectionLabel: '',
        order: 100 + index,
        wide: false,
        anchor: `photo_${key}`,
      })
    })
  }
  return rows.sort((a, b) => a.order - b.order)
})

/** 用常量分组顺序渲染位置下拉（optgroup），标签优先取后端 photo_sections。 */
const photoPositionGroups = computed(() => {
  const labels = dict.value.photoSectionLabels || {}
  const order = PHOTO_SECTION_KEYS.slice()
  photoPositions.value.forEach((item) => {
    if (item.section && !order.includes(item.section)) order.push(item.section)
  })
  return order
    .map((section) => ({
      section,
      label:
        labels[section] ||
        (photoPositions.value.find((item) => item.section === section) || {}).sectionLabel ||
        section,
      options: photoPositions.value.filter((item) => item.section === section),
    }))
    .filter((group) => group.options.length)
})

const aiEnabled = computed(() => aiStatus.value === 'ready')
const demoData = computed(() => !!config.value?.demo_data || !!config.value?.demoData)

/** 统计数字容错：后端字段名可能有多种写法，取不到时回落为 0。 */
function statNum(...keys) {
  for (const key of keys) {
    const value = stats.value?.[key]
    if (typeof value === 'number' && Number.isFinite(value)) return value
    if (typeof value === 'string' && value.trim() !== '' && Number.isFinite(Number(value))) return Number(value)
  }
  return 0
}

const sidebarCounts = computed(() => ({
  cases: statNum('cases', 'total_cases', 'case_count', 'total') || casesPayload.value.total,
  blocking: statNum('blocking_items', 'blocking_item_count', 'blocking_count', 'open_blocking'),
  unresolved: statNum('unresolved_fields', 'unresolved_field_count', 'pending_fields', 'unresolved_count'),
  rules: statNum('rules', 'rule_count', 'terminology_rules'),
  resultsLabel: resultsLabel.value,
  resultsTone: resultsTone.value,
}))

/** 「结果」导航徽标文案（对齐 static 版：阻断 N / 待处理 N / 已完成 / 尚未整理）。 */
const resultsLabel = computed(() => {
  const badge = quickBadge.value
  if (!badge.has) return '尚未整理'
  if (Number(badge.blocking) > 0) return `阻断 ${Number(badge.blocking)}`
  if (Number(badge.unresolved) > 0) return `待处理 ${Number(badge.unresolved)}`
  return '已完成'
})

const resultsTone = computed(() => {
  const badge = quickBadge.value
  if (!badge.has) return 'muted'
  if (Number(badge.blocking) > 0) return 'danger'
  if (Number(badge.unresolved) > 0) return 'warning'
  return 'ok'
})

/**
 * 传给 StatsCards 的统计视图（实测修正）。
 * `GET /api/stats` 实际键名：case_count/total_cases、document_count、unresolved_field_count、
 * blocking_count、material_counts（对象）、by_material_kind（[{kind,label,count}]）。
 * 而 StatsCards（主 Agent 冻结、前端只读）读的是 `materials|material_count`、`documents`、
 * `unresolved_fields`、`blocking_items`。这里在 App.vue 侧补别名，卡片不再显示 0，
 * 且不改动任何冻结文件。
 */
const statsView = computed(() => {
  const raw = stats.value || {}
  const kindRows = Array.isArray(raw.by_material_kind) ? raw.by_material_kind : []
  const materialTotal = kindRows.reduce((sum, item) => sum + (Number(item && item.count) || 0), 0)
  const view = { ...raw }
  if (view.material_count === undefined && view.materials === undefined && materialTotal) {
    view.material_count = materialTotal
  }
  if (view.documents === undefined && raw.document_count !== undefined) view.documents = raw.document_count
  if (view.unresolved_fields === undefined && raw.unresolved_field_count !== undefined) {
    view.unresolved_fields = raw.unresolved_field_count
  }
  if (view.blocking_items === undefined && raw.blocking_count !== undefined) {
    view.blocking_items = raw.blocking_count
  }
  // 损伤总数：/api/stats 目前不提供（已作为契约缺口上报），后端补齐后自动透传
  if (view.injuries === undefined && raw.injury_count !== undefined) view.injuries = raw.injury_count
  return view
})

/**
 * 引擎状态行。/api/config 的 engines 实际形态：
 *   { docx: {preferred, docxtpl_available, todo}, libreoffice: "<path>",
 *     libreoffice_available: bool, ocr: [{name, available, detail}] }
 */
const engineRows = computed(() => {
  const raw = config.value || {}
  const rows = []
  const engines = isPlainObject(raw.engines) ? raw.engines : null
  if (engines) {
    const docx = engines.docx
    if (isPlainObject(docx)) {
      rows.push({ key: 'docx_engine', label: 'Word 生成引擎', value: String(docx.preferred || '未报告') })
      rows.push({
        key: 'docxtpl_available',
        label: 'docxtpl（TODO-DL-1）',
        value: docx.docxtpl_available ? '可用' : `不可用，回退内置渲染器${docx.todo ? `（${docx.todo}）` : ''}`,
      })
    }
    rows.push({
      key: 'libreoffice',
      label: 'LibreOffice（PDF 导出）',
      value: engines.libreoffice_available
        ? String(engines.libreoffice || '已检测到')
        : '不可用，PDF 导出会记入校核清单',
    })
    const ocr = firstArray(engines.ocr)
    ocr.forEach((item) => {
      const name = item && item.name ? String(item.name) : '未知引擎'
      rows.push({
        key: `ocr_${name}`,
        label: `识别引擎：${name}`,
        value: item && item.available ? '可用' : `不可用${item && item.detail ? `：${item.detail}` : ''}`,
      })
    })
  } else {
    const candidates = [
      ['ocr_engines', '识别引擎（仅名称，后端未提供可用性）'],
      ['ocr_engine', '识别引擎'],
      ['default_ocr_engine', '默认识别引擎'],
      ['docx_engine', 'Word 生成引擎'],
      ['docxtpl_available', 'docxtpl（TODO-DL-1）'],
      ['pypdfium2_available', 'pypdfium2（TODO-DL-4）'],
      ['libreoffice', 'LibreOffice'],
      ['libreoffice_available', 'LibreOffice 可用'],
      ['pdf_engine', 'PDF 渲染引擎'],
      ['llm_enabled', '模型接入'],
    ]
    candidates.forEach(([key, label]) => {
      if (raw[key] === undefined || raw[key] === null) return
      rows.push({ key, label, value: formatScalar(raw[key]) })
    })
  }
  return rows
})

/** 需要下载的待办（后端 todo_downloads）：缺能力在此显式报告，不静默失败。 */
const todoRows = computed(() =>
  firstArray(config.value?.todo_downloads).map((item) => ({
    code: String(item?.code || ''),
    item: String(item?.item || ''),
    available: !!item?.available,
  })),
)

function formatScalar(value) {
  if (value === null || value === undefined || value === '') return '未报告'
  if (typeof value === 'string') return value || '未报告'
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  if (Array.isArray(value)) return value.length ? value.map((item) => formatScalar(item)).join('、') : '未报告'
  if (isPlainObject(value)) {
    const parts = Object.entries(value).map(([key, item]) => `${key}=${formatScalar(item)}`)
    return parts.length ? parts.join('；') : '未报告'
  }
  return String(value)
}

/* ------------------------------------------------------------------ *
 * 数据加载
 * ------------------------------------------------------------------ */
async function loadConfig() {
  try {
    config.value = await api.config()
    configLoaded.value = true
    configError.value = ''
    const reported = String(config.value?.ocr_status || config.value?.ai_status || '')
    if (!aiEnabled.value && reported === 'ready') aiStatus.value = 'ready'
  } catch (error) {
    configError.value = error && error.message ? error.message : '配置加载失败'
  }
}

async function loadStats() {
  statsLoading.value = true
  try {
    stats.value = await api.stats()
  } catch (error) {
    await handleError(error)
  } finally {
    statsLoading.value = false
  }
}

async function loadCases() {
  listLoading.value = true
  try {
    const payload = await api.listCases({
      search: filters.value.search,
      stage: filters.value.stage,
      doc_status: filters.value.doc_status,
      material_kind: filters.value.material_kind,
      has_blocking: filters.value.has_blocking,
      page: filters.value.page,
      page_size: filters.value.page_size,
    })
    casesPayload.value = {
      items: firstArray(payload && payload.items),
      total: Number(payload && payload.total) || 0,
      page: Number(payload && payload.page) || filters.value.page,
      page_size: Number(payload && payload.page_size) || filters.value.page_size,
    }
    listError.value = ''
  } catch (error) {
    listError.value = error && error.message ? error.message : '案件列表加载失败'
  } finally {
    listLoading.value = false
  }
}

async function loadCaseDetail(caseId) {
  if (!caseId) return null
  caseLoading.value = true
  try {
    const detail = await api.case(caseId)
    selectedCase.value = detail
    return detail
  } catch (error) {
    await handleError(error)
    return null
  } finally {
    caseLoading.value = false
  }
}

async function refreshAll() {
  await Promise.all([loadCases(), loadStats()])
  if (selectedCase.value && selectedCase.value.id) {
    await loadCaseDetail(selectedCase.value.id)
  }
}

/** 刷新当前案件（子组件完成写操作后统一调用）。 */
async function refreshCase() {
  if (selectedCase.value && selectedCase.value.id) {
    await loadCaseDetail(selectedCase.value.id)
  }
  await Promise.all([loadStats(), loadCases()])
}

/* ------------------------------------------------------------------ *
 * 交互
 * ------------------------------------------------------------------ */
function navigate(view) {
  // 主流程（Transparent）：整理 / 结果，两页共用同一个 QuickRun 实例，文件与结果都不丢。
  if (view === 'case') {
    // 「案件工作台」= 四步核对：必须先在案件记录里选中案件。
    if (selectedCase.value && selectedCase.value.id) {
      sidebarOpen.value = false
      activeView.value = 'case'
      workspaceOpen.value = true
      return
    }
    sidebarOpen.value = false
    notify('请先在「案件记录」里打开一个案件，再进入四步核对工作台', 'info')
    activeView.value = 'cases'
    loadCases()
    return
  }

  activeView.value = view
  sidebarOpen.value = false
  if (view === 'review') {
    filters.value = { ...filters.value, has_blocking: 'true', page: 1 }
    loadCases()
  } else if (view === 'cases') {
    loadCases()
  }
}

/** 主视图提交成功后的回调：只更新「结果」徽标（刷新由 @refresh 单独触发）。 */
function onQuickResult(info) {
  quickBadge.value = {
    has: Boolean(info && info.has),
    blocking: Number(info && info.blocking) || 0,
    unresolved: Number(info && info.unresolved) || 0,
  }
}

/** 整理成功或再整理一批后：刷新统计与案件列表（失败不影响结果页）。 */
async function refreshAfterQuick() {
  await Promise.all([loadCases(), loadStats()])
}

/** 结果页「查看案件记录 / 去案件记录修改」：打开该案件的案件工作台。 */
async function openCaseFromResult(payload) {
  const id = Number(payload && payload.id) || 0
  if (!id) {
    notify('缺少案件 ID：服务端响应里没有 case_id，无法打开案件记录', 'error')
    return
  }
  closeWorkspace()
  selectedCase.value = null
  activeView.value = 'case'
  workspaceOpen.value = true
  const detail = await loadCaseDetail(id)
  if (!detail) {
    workspaceOpen.value = false
    activeView.value = 'results'
  }
}

/**
 * 文档级拖拽兜底：drop 一律阻止浏览器直接打开文件（否则会丢当前页面状态），
 * 拖到主视图拖拽区时交给 QuickRun 处理；落在别处只阻止默认行为。
 */
function dropZoneOf(event) {
  const target = event.target
  if (target && typeof target.closest === 'function') return target.closest('[data-dropzone]')
  return null
}

function onDocumentDragEnter(event) {
  const zone = dropZoneOf(event)
  if (!zone) return
  event.preventDefault()
}

function onDocumentDragOver(event) {
  const zone = dropZoneOf(event)
  if (!zone) return
  event.preventDefault()
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy'
}

function onDocumentDrop(event) {
  event.preventDefault()
  if (!dropZoneOf(event)) return
  const dropped = Array.from(event.dataTransfer?.files || [])
  if (!dropped.length) {
    notify('没有读到文件：如果是文件夹或浏览器不支持的类型，请改用“选择文件”按钮', 'error')
    return
  }
  if (activeView.value !== 'quick') {
    // 与 static 版一致：拖动文件到主界面时自动回到「整理」页。
    activeView.value = 'quick'
    sidebarOpen.value = false
  }
  quickRef.value?.addFiles(dropped)
}

function updateFilters(patch) {
  filters.value = { ...filters.value, ...patch }
  loadCases()
}

async function openCase(row) {
  if (!row || !row.id) return
  workspaceOpen.value = true
  selectedCase.value = null
  await loadCaseDetail(row.id)
}

function closeWorkspace() {
  workspaceOpen.value = false
  selectedCase.value = null
}

async function submitCase(payload) {
  createSaving.value = true
  createError.value = ''
  try {
    const created = await api.createCase(payload)
    showCreate.value = false
    notify(`案件已建立：${created && created.case_no ? created.case_no : '新建案件'}`, 'success')
    await refreshAll()
    if (created && created.id) await openCase(created)
  } catch (error) {
    createError.value = error && error.message ? error.message : '建案失败'
    await handleError(error)
  } finally {
    createSaving.value = false
  }
}

async function removeCase(row) {
  if (!row || !row.id) return
  const label = row.case_no || `案件 #${row.id}`
  if (!window.confirm(`确认删除 ${label}？原件与生成文稿不会被保留，此操作不可撤销。`)) return
  try {
    await api.deleteCase(row.id, row.version)
    notify(`${label} 已删除`, 'success')
    if (selectedCase.value && selectedCase.value.id === row.id) closeWorkspace()
    await refreshAll()
  } catch (error) {
    await handleError(error)
  }
}

async function verifyAi(key) {
  if (!key || !String(key).trim()) {
    notify('请先填写 API Key（只在内存中使用，不写入数据库或源码）', 'error')
    return
  }
  aiBusy.value = true
  try {
    const result = await api.verifyAi(String(key).trim())
    aiStatus.value = 'ready'
    notify(
      result && result.detail ? String(result.detail) : '模型连接正常，密钥仅保存在内存中',
      'success',
    )
  } catch (error) {
    aiStatus.value = 'failed'
    await handleError(error)
  } finally {
    aiBusy.value = false
  }
}

onMounted(async () => {
  document.addEventListener('dragenter', onDocumentDragEnter)
  document.addEventListener('dragover', onDocumentDragOver)
  document.addEventListener('drop', onDocumentDrop)
  await loadConfig()
  await Promise.all([loadCases(), loadStats()])
})

onUnmounted(() => {
  document.removeEventListener('dragenter', onDocumentDragEnter)
  document.removeEventListener('dragover', onDocumentDragOver)
  document.removeEventListener('drop', onDocumentDrop)
  if (toastTimer) window.clearTimeout(toastTimer)
})
</script>

<template>
  <div class="min-h-screen bg-gray-100 text-gray-900">
    <Sidebar
      :active-view="activeView"
      :mobile-open="sidebarOpen"
      :counts="sidebarCounts"
      :ai-enabled="aiEnabled"
      :ai-status="aiStatus"
      :demo-data="demoData"
      @navigate="navigate"
      @close="sidebarOpen = false"
      @configure="showAi = true"
    />

    <div class="lg:pl-60">
      <header class="sticky top-0 z-30 border-b border-gray-200 bg-white/95 backdrop-blur">
        <div class="mx-auto flex max-w-[1600px] items-center gap-3 px-4 py-3 sm:px-6">
          <button
            class="btn-secondary px-3 py-2 lg:hidden"
            aria-label="打开导航"
            @click="sidebarOpen = true"
          >
            <Settings :size="18" />
          </button>
          <div class="min-w-0 flex-1">
            <h1 class="truncate text-lg font-semibold text-gray-950">
              法医尸表检验文书整理工作台
            </h1>
            <p class="truncate text-xs text-gray-500">
              主界面：上传 → 结果（一次点击拿到 Word / PDF）· 次级界面「案件记录」保留四步核对：选择材料 → 核对疑点 → 查看文档 → 确认修改（R-1）
            </p>
          </div>
          <button class="btn-secondary" :disabled="listLoading || statsLoading" @click="refreshAll">
            <RefreshCw :size="16" :class="listLoading || statsLoading ? 'animate-spin' : ''" />
            <span class="hidden sm:inline">刷新</span>
          </button>
          <button class="btn-primary" @click="showCreate = true">
            <span class="text-base leading-none">＋</span>
            <span class="hidden sm:inline">新建案件</span>
          </button>
          <button class="btn-secondary" @click="showAi = true">
            <KeyRound :size="16" />
            <span class="hidden sm:inline">模型接入</span>
          </button>
        </div>
      </header>

      <Transition name="fade">
        <div
          v-if="configError"
          class="mx-auto mt-4 flex max-w-[1600px] items-start gap-3 px-4 sm:px-6"
        >
          <div
            class="flex w-full items-start gap-2 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900"
          >
            <CircleAlert :size="18" class="mt-0.5 shrink-0" />
            <p class="min-w-0 flex-1">
              配置接口未取回（{{ configError }}）。界面使用内置字典兜底显示，部分下拉项可能为空。
            </p>
          </div>
        </div>
      </Transition>

      <Transition name="fade">
        <div v-if="listError" class="mx-auto mt-4 max-w-[1600px] px-4 sm:px-6">
          <div
            class="flex items-start gap-2 rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-800"
          >
            <CircleAlert :size="18" class="mt-0.5 shrink-0" />
            <p class="min-w-0 flex-1">案件列表加载失败：{{ listError }}</p>
          </div>
        </div>
      </Transition>

      <main class="mx-auto max-w-[1600px] space-y-5 px-4 py-5 sm:px-6">
        <!-- 主流程（Transparent）：整理 → 结果。
             用 v-show 常驻而不是 v-if：在「整理」与「结果」之间来回切换、或去次级界面看案件，
             已选文件、输出格式与上一次结果都不会丢；文档级拖拽也始终有落点。 -->
        <div v-show="activeView === 'quick' || activeView === 'results'">
          <QuickRun
            ref="quickRef"
            :view="activeView === 'results' ? 'results' : 'quick'"
            :dict="dict"
            :photo-groups="photoPositionGroups"
            @navigate="navigate"
            @open-case="openCaseFromResult"
            @notify="notify"
            @error="handleError"
            @result="onQuickResult"
            @refresh="refreshAfterQuick"
          />
        </div>

        <!-- 案件列表 -->
        <template v-if="activeView === 'cases'">
          <StatsCards :stats="statsView" :loading="statsLoading" :dict="dict" />
          <CaseTable
            :items="casesPayload.items"
            :total="casesPayload.total"
            :page="casesPayload.page"
            :page-size="casesPayload.page_size"
            :loading="listLoading"
            :filters="filters"
            :dict="dict"
            @filters="updateFilters"
            @select="openCase"
            @create="showCreate = true"
            @delete="removeCase"
            @refresh="refreshAll"
          />
        </template>

        <!-- 复核中心：只看有阻断项的案件 -->
        <template v-else-if="activeView === 'review'">
          <section class="panel p-5">
            <h2 class="text-base font-semibold text-gray-950">复核中心</h2>
            <p class="mt-1 text-sm text-gray-600">
              仅列出存在未解决阻断项的案件；打开案件后默认进入「核对疑点」步骤。阻断项未解决前不能生成待签发稿（R-28）。
            </p>
            <div class="mt-3 flex flex-wrap gap-2">
              <button class="btn-secondary" @click="updateFilters({ has_blocking: 'true', page: 1 })">
                只看有阻断项
              </button>
              <button class="btn-secondary" @click="updateFilters({ has_blocking: '', page: 1 })">
                显示全部案件
              </button>
            </div>
          </section>
          <CaseTable
            :items="casesPayload.items"
            :total="casesPayload.total"
            :page="casesPayload.page"
            :page-size="casesPayload.page_size"
            :loading="listLoading"
            :filters="filters"
            :dict="dict"
            @filters="updateFilters"
            @select="openCase"
            @create="showCreate = true"
            @delete="removeCase"
            @refresh="refreshAll"
          />
        </template>

        <!-- 术语规则 -->
        <template v-else-if="activeView === 'rules'">
          <TerminologyRulesPanel :dict="dict" @error="handleError" @notify="notify" />
        </template>

        <!-- 系统配置 -->
        <template v-else-if="activeView === 'settings'">
          <section class="panel p-5">
            <h2 class="text-base font-semibold text-gray-950">系统配置</h2>
            <p class="mt-1 text-sm text-gray-600">
              引擎与运行状态取自 <code class="rounded bg-gray-100 px-1">GET /api/config</code>；缺能力时降级运行并在此显式报告，不静默失败。
            </p>
            <dl class="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              <div
                v-for="row in engineRows"
                :key="row.key"
                class="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2"
              >
                <dt class="text-xs text-gray-500">{{ row.label }}</dt>
                <dd class="mt-0.5 break-words text-sm font-medium text-gray-900">{{ row.value }}</dd>
              </div>
              <div v-if="!engineRows.length" class="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2">
                <dt class="text-xs text-gray-500">配置状态</dt>
                <dd class="mt-0.5 text-sm font-medium text-gray-900">
                  {{ configLoaded ? '接口未报告引擎字段' : '尚未取回配置' }}
                </dd>
              </div>
            </dl>
            <div class="mt-4 flex flex-wrap gap-2">
              <button class="btn-secondary" @click="loadConfig">
                <RefreshCw :size="16" />
                重新读取配置
              </button>
              <button class="btn-primary" @click="showAi = true">
                <KeyRound :size="16" />
                模型接入（密钥仅内存）
              </button>
            </div>
          </section>

          <section class="panel p-5">
            <h2 class="text-base font-semibold text-gray-950">部位照片位置目录</h2>
            <p class="mt-1 text-sm text-gray-600">
              位置只能从此目录中选择；系统不读取照片内容，只按你选择的位置插入（N-11 / R-55 / R-58）。
            </p>
            <div class="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              <div
                v-for="group in photoPositionGroups"
                :key="group.section"
                class="rounded-lg border border-gray-200 p-3"
              >
                <p class="text-xs font-semibold text-gray-500">{{ group.label }}</p>
                <ul class="mt-2 space-y-1 text-sm text-gray-800">
                  <li v-for="option in group.options" :key="option.key">
                    {{ option.label }}
                    <span class="text-xs text-gray-400">（{{ option.key }} → {{ option.anchor }}）</span>
                  </li>
                </ul>
              </div>
              <p v-if="!photoPositionGroups.length" class="text-sm text-gray-500">
                /api/config 未返回照片位置目录，部位照片面板将提示无法选择位置。
              </p>
            </div>
          </section>

          <section class="panel p-5">
            <h2 class="text-base font-semibold text-gray-950">需要下载的能力（显式降级报告）</h2>
            <p class="mt-1 text-sm text-gray-600">
              取自 <code class="rounded bg-gray-100 px-1">GET /api/config</code> 的
              <code class="rounded bg-gray-100 px-1">todo_downloads</code>：缺能力时降级运行并在此报告，不静默失败、不伪装成成功。
            </p>
            <ul class="mt-3 grid gap-2 sm:grid-cols-2">
              <li
                v-for="row in todoRows"
                :key="row.code"
                class="flex items-center justify-between gap-3 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-sm"
              >
                <span class="min-w-0"><span class="font-medium text-gray-800">{{ row.code }}</span> · {{ row.item }}</span>
                <span class="chip" :class="row.available ? 'chip-recorded' : 'chip-warning'">
                  {{ row.available ? '已可用' : '未安装 / 降级' }}
                </span>
              </li>
              <li v-if="!todoRows.length" class="text-sm text-gray-500">配置接口未返回待办清单。</li>
            </ul>
          </section>

          <section class="panel p-5">
            <h2 class="text-base font-semibold text-gray-950">已知限制</h2>
            <ul class="mt-2 space-y-1 text-sm text-gray-700">
              <li>TODO-DL-3：本机 <code class="rounded bg-gray-100 px-1">npm install</code> / <code class="rounded bg-gray-100 px-1">npm run build</code> 已可用，Vue 3 源码已通过 <code class="rounded bg-gray-100 px-1">vite build</code> 构建验证（产物在 <code class="rounded bg-gray-100 px-1">frontend/dist</code>），并与无构建工作台 <code class="rounded bg-gray-100 px-1">frontend/static</code> 并存；浏览器内的真实拖拽与点击下载仍未自动化验证，需人工点一遍。</li>
              <li>TODO-DL-2：PaddleOCR 未安装时识别引擎不可用，接口会返回 409。</li>
              <li>TODO-DL-1 / TODO-DL-4：模板填充与 PDF 渲染自动回退，回退结果在此页面显式报告。</li>
            </ul>
          </section>
        </template>
      </main>
    </div>

    <CreateCaseModal
      :open="showCreate"
      :saving="createSaving"
      :error="createError"
      :dict="dict"
      @close="showCreate = false"
      @submit="submitCase"
    />

    <AISettingsModal
      :open="showAi"
      :status="aiStatus"
      :busy="aiBusy"
      @close="showAi = false"
      @verify="verifyAi"
    />

    <CaseWorkspace
      v-if="workspaceOpen"
      :case-detail="selectedCase"
      :loading="caseLoading"
      :dict="dict"
      :photo-groups="photoPositionGroups"
      :config="config"
      @close="closeWorkspace"
      @refresh="refreshCase"
      @notify="notify"
      @error="handleError"
    />

    <Transition name="fade">
      <div
        v-if="toast.visible"
        class="fixed bottom-5 right-5 z-[60] w-[min(26rem,calc(100vw-2.5rem))]"
        role="status"
      >
        <div
          class="flex items-start gap-2 rounded-lg border px-4 py-3 text-sm shadow-panel"
          :class="toast.tone === 'error'
            ? 'border-red-300 bg-red-50 text-red-800'
            : toast.tone === 'success'
              ? 'border-emerald-300 bg-emerald-50 text-emerald-800'
              : 'border-gray-300 bg-white text-gray-800'"
        >
          <CircleX v-if="toast.tone === 'error'" :size="18" class="mt-0.5 shrink-0" />
          <CircleCheck v-else-if="toast.tone === 'success'" :size="18" class="mt-0.5 shrink-0" />
          <CircleAlert v-else :size="18" class="mt-0.5 shrink-0" />
          <p class="min-w-0 flex-1 break-words">{{ toast.message }}</p>
          <button
            class="shrink-0 cursor-pointer rounded p-1 hover:bg-black/5"
            aria-label="关闭提示"
            @click="dismissToast"
          >
            <CircleX :size="16" />
          </button>
        </div>
      </div>
    </Transition>
  </div>
</template>
