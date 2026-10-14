<script setup>
/**
 * 主视图「整理」：上传文件 → 一次 POST /api/quick-run → 结果（Transparent 主流程）。
 *
 * 行为与文案逐字对齐无构建工作台 frontend/static/workbench.js §18b：
 *   拖拽区 + 多选文件（accept="image/*,.pdf,.docx,.doc,.json"）→ 每个文件一行显示
 *   名称/大小/类型 + 类型芯片（默认「自动判断」= kind 留空）→ 只有选「部位照片」才出现
 *   目标位置下拉（选项来自 /api/config 的 photo_positions，首项「自动（按文件名推测）」）→
 *   输出格式三选（Word / PDF / 两者，默认两者）→ 可选项默认收起 → 提交（禁用/加载态/阶段文案）。
 *
 * 请求契约（`docs/architecture.md` 第 4 节，冻结）：
 *   POST /api/quick-run
 *   {"files":[{"filename","media_type","content_base64","kind","position_key"}],
 *    "formats":["docx","pdf"],"case_no":"","commission_unit":"","subject_name":"","note":""}
 *   `kind` 留空 = 交后端按文件名推断；`position_key` 只对部位照片发送
 *   （后端对非照片指定位置会 400「只有部位照片才能指定目标位置」）。
 *
 * 红线：
 *   - 本页不读取照片内容、不做任何图像推断（N-11 / R-55 / R-58），
 *     照片类型不进入识别，位置只由用户选择或按文件名推测；
 *   - 空白一律显示“未记录”，绝不显示“未见异常”（N-5）；
 *   - 系统不生成鉴定意见（N-1）；界面不引用任何外链资源。
 */
import { computed, onUnmounted, ref } from 'vue'
import { CircleAlert, FileText, Loader2, Upload } from 'lucide-vue-next'
import { api } from '../api.js'
import QuickRunResult from './QuickRunResult.vue'

/** 主视图唯一入口端点（冻结契约）。 */
const QUICK_RUN_ENDPOINT = '/api/quick-run'
/** 单文件提示阈值：超过它只提示不改行为（网关 nginx 侧上限 64 MB）。 */
const QUICK_MAX_FILE_BYTES = 30 * 1024 * 1024
/** 网关上单体请求上限（base64 后约为原文件的 4/3）。 */
const QUICK_GATEWAY_LIMIT_BYTES = 64 * 1024 * 1024
/** 文件选择框 accept：照片、PDF、Word、JSON（契约 media_type 覆盖范围）。 */
const QUICK_ACCEPT = 'image/*,.pdf,.docx,.doc,.json'
/** N-11/R-58 必须显著标注的照片口径（原文固定，不得改写）。 */
const QUICK_PHOTO_POLICY = '系统不读取照片内容，只按你选择的位置插入（未选择时按文件名推测，可在案件记录中修改）'
/** 本次没有照片的照片栏位在文稿里的固定写法（绝不拿别的栏位顶替，R-56）。 */
const PHOTO_PLACEHOLDER_TEXT = '（未提供照片）'
const NOT_RECORDED = '未记录'
/** 空值判定与 static 版一致：'/'、'—'、'-'、'无记录' 都算没有记录（N-5）。 */
const EMPTY_VALUES = ['', '/', '—', '-', '无记录']

/**
 * 每个文件的类型芯片：`kind` 留空 = 交后端按文件名推断（契约要求 kind 可空）。
 * 第二个元素是本工作台的中文标注；若 /api/config 给了更规范的标签，会把两者并列显示。
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

const FORMAT_OPTIONS = [
  ['both', '两者都要（推荐）'],
  ['docx', '只要 Word（.docx）'],
  ['pdf', '只要 PDF'],
]

/** 单次请求拿不到真实进度：界面按预计阶段推进，并明确说明“不回报百分比”。 */
const QUICK_PHASES = ['上传中', '识别中', '生成文稿']

const props = defineProps({
  view: { type: String, default: 'quick' },
  dict: { type: Object, default: () => ({}) },
  photoGroups: { type: Array, default: () => [] },
})

const emit = defineEmits(['navigate', 'open-case', 'notify', 'error', 'result', 'refresh'])

/* ------------------------------------------------------------------ 工具 */

function num(value, fallback = 0) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

function valText(value) {
  if (value === null || value === undefined) return NOT_RECORDED
  const text = String(value).trim()
  return EMPTY_VALUES.includes(text) ? NOT_RECORDED : text
}

function quickSizeText(bytes) {
  const value = num(bytes, 0)
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / (1024 * 1024)).toFixed(2)} MB`
}

/** 与 static 版 mediaTypeOf 一致：只看 MIME 与文件名后缀。 */
function mediaTypeOf(file) {
  const name = String(file?.name || '').toLowerCase()
  const type = String(file?.type || '').toLowerCase()
  if (type.includes('pdf') || name.endsWith('.pdf')) return 'pdf'
  if (type.includes('jpeg') || type.includes('jpg') || name.endsWith('.jpg') || name.endsWith('.jpeg')) return 'jpeg'
  if (type.includes('png') || name.endsWith('.png')) return 'png'
  if (type.includes('word') || name.endsWith('.docx')) return 'docx'
  if (type.includes('json') || name.endsWith('.json')) return 'json'
  return 'other'
}

/** 浏览器 FileReader 读为 base64（去掉 data URL 前缀）。只读字节，不解析图片内容（N-11）。 */
function readFileAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const result = String(reader.result || '')
      const comma = result.indexOf(',')
      resolve(comma >= 0 ? result.slice(comma + 1) : result)
    }
    reader.onerror = () => reject(new Error(`读取文件失败：${file?.name || ''}`))
    reader.readAsDataURL(file)
  })
}

/* ------------------------------------------------------------------ 状态 */

// files 保存浏览器 File 对象本身：重新渲染视图不会丢文件，失败时也不清空。
const files = ref([]) // [{id, file, name, size, lastModified, media_type, kind, position_key, oversize}]
const formatChoice = ref('both')
const caseNo = ref('')
const commissionUnit = ref('')
const subjectName = ref('')
const note = ref('')
const showOptional = ref(false)
const running = ref(false)
const phase = ref('')
const elapsed = ref(0)
const error = ref('')
const result = ref(null)
const dragActive = ref(false)

let quickSeq = 0
let phaseTimer = null
let secondsTimer = null
let startedAt = 0

/* ------------------------------------------------------------------ 计算 */

const formats = computed(() => {
  if (formatChoice.value === 'docx') return ['docx']
  if (formatChoice.value === 'pdf') return ['pdf']
  return ['docx', 'pdf']
})

const formatSummary = computed(() => {
  if (formatChoice.value === 'docx') return '只要 Word（.docx）'
  if (formatChoice.value === 'pdf') return '只要 PDF'
  return 'Word 与 PDF 都要'
})

const selectedBytes = computed(() => files.value.reduce((sum, item) => sum + num(item.size, 0), 0))
/** base64 编码后体积约为原始字节的 4/3，用来预估是否触及网关上限。 */
const estimateBytes = computed(() => Math.round((selectedBytes.value * 4) / 3))
const gatewayExceeded = computed(() => estimateBytes.value > QUICK_GATEWAY_LIMIT_BYTES)

const submitDisabled = computed(() => running.value || !files.value.length)

const phaseText = computed(() => {
  if (running.value) {
    const seconds = startedAt ? Math.max(1, elapsed.value) : 0
    return `${phase.value || '整理中'}…已用 ${seconds} 秒（一次请求，服务端不回报百分比）`
  }
  if (error.value) return `上次整理失败：${error.value}`
  if (result.value) return '已完成，请到「结果」页下载文稿。'
  if (!files.value.length) return '还没有选择文件'
  return `准备就绪：${formatSummary.value}，点“开始整理”即可`
})

const submitLabel = computed(() => (running.value ? `${phase.value || '整理中'}…` : '开始整理'))

/** 类型芯片文案：本工作台标注 + 后端规范标签并列，避免口径不一致。 */
function kindChipLabel(kind, fallback) {
  const backend = props.dict?.materialKindLabels?.[kind] || ''
  if (!fallback) return backend || kind
  if (backend && backend !== fallback) return `${fallback}（${backend}）`
  return fallback
}

/* ------------------------------------------------------------------ 文件选择 */

function notify(message, tone = 'info') {
  emit('notify', message, tone)
}

/** 加入待整理列表：按 名称|大小|修改时间 去重，重复项跳过而不是静默覆盖。 */
function addFiles(fileList) {
  const incoming = Array.from(fileList || [])
  if (!incoming.length) return 0
  const seen = new Set(files.value.map((item) => `${item.name}|${item.size}|${item.lastModified}`))
  const skipped = []
  const big = []
  let added = 0

  incoming.forEach((file) => {
    const name = file.name || '未命名文件'
    const size = num(file.size, 0)
    const lastModified = num(file.lastModified, 0)
    const key = `${name}|${size}|${lastModified}`
    if (seen.has(key)) {
      skipped.push(name)
      return
    }
    seen.add(key)
    quickSeq += 1
    files.value.push({
      id: quickSeq,
      file,
      name,
      size,
      lastModified,
      media_type: mediaTypeOf(file),
      kind: '', // 默认「自动判断」：kind 留空交后端按文件名推断
      position_key: '',
      oversize: size > QUICK_MAX_FILE_BYTES,
    })
    added += 1
    if (size > QUICK_MAX_FILE_BYTES) big.push(`${name}（${quickSizeText(size)}）`)
  })

  error.value = ''
  if (skipped.length) notify(`已跳过重复文件：${skipped.join('、')}`, 'error')
  if (big.length) {
    notify(`文件较大（>30 MB）：${big.join('、')}：上传会明显变慢；网关（nginx）单请求上限 64 MB，超过会被拒绝。`, 'error')
  }
  if (gatewayExceeded.value) {
    notify(`预计超过网关上限：base64 后约 ${quickSizeText(estimateBytes.value)}，超过 64 MB 网关上限，请分批整理。`, 'error')
  }
  if (added) notify(`已加入待整理列表：共 ${added} 个文件；类型默认“自动判断”，可在下方逐项修改。`, 'success')
  return added
}

function removeFile(id) {
  files.value = files.value.filter((item) => num(item.id, 0) !== num(id, 0))
}

function clearFiles() {
  if (!files.value.length) return
  files.value = []
  notify('已清空待整理列表，可以重新选择文件。', 'success')
}

function pickFiles() {
  fileInput.value?.click()
}

function setKind(item, kind) {
  item.kind = kind
  // 部位照片之外不发送 position_key（后端会 400）
  if (kind !== 'photo') item.position_key = ''
}

function onFileInputChange(event) {
  const input = event.target
  addFiles(input.files)
  input.value = '' // 允许再次选择同一个文件
}

/* ------------------------------------------------------------------ 拖拽 */

function onDragEnter() {
  dragActive.value = true
}

function onDragOver() {
  dragActive.value = true
}

function onDragLeave(event) {
  const zone = event.currentTarget
  if (event.relatedTarget && zone && zone.contains(event.relatedTarget)) return
  dragActive.value = false
}

function onDrop(event) {
  dragActive.value = false
  const dropped = Array.from(event.dataTransfer?.files || [])
  if (!dropped.length) {
    notify('没有读到文件：如果是文件夹或浏览器不支持的类型，请改用“选择文件”按钮。', 'error')
    return
  }
  if (props.view !== 'quick') emit('navigate', 'quick')
  addFiles(dropped)
}

/* ------------------------------------------------------------------ 提交 */

function startTicker() {
  stopTicker()
  phase.value = QUICK_PHASES[0]
  let index = 0
  phaseTimer = window.setInterval(() => {
    index = Math.min(index + 1, QUICK_PHASES.length - 1)
    phase.value = QUICK_PHASES[index]
  }, 7000)
  secondsTimer = window.setInterval(() => {
    elapsed.value = startedAt ? Math.max(1, Math.round((Date.now() - startedAt) / 1000)) : 0
  }, 1000)
}

function stopTicker() {
  if (phaseTimer) {
    window.clearInterval(phaseTimer)
    phaseTimer = null
  }
  if (secondsTimer) {
    window.clearInterval(secondsTimer)
    secondsTimer = null
  }
}

/** 契约要求的请求体：逐条只发契约字段，非照片绝不带 position_key。 */
function buildPayload(encoded) {
  return {
    files: encoded.map((item) => ({
      filename: item.filename,
      media_type: item.media_type || 'other',
      content_base64: item.content_base64 || '',
      kind: item.kind || '',
      position_key: item.kind === 'photo' ? item.position_key || '' : '',
    })),
    formats: formats.value,
    case_no: String(caseNo.value || '').trim(),
    commission_unit: String(commissionUnit.value || '').trim(),
    subject_name: String(subjectName.value || '').trim(),
    note: String(note.value || '').trim(),
  }
}

/** 响应归一化：契约字段缺失时降级为“未记录”而不是编造（N-5）。 */
function normalizeResult(raw) {
  const source = raw && typeof raw === 'object' ? raw : {}
  return {
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

/** 上一个响应在处理期间不得被后来的响应覆盖（防重复提交）。 */
function publishResult(next) {
  result.value = next
  const summary = next?.summary || {}
  emit('result', {
    has: Boolean(next),
    blocking: num(summary.blocking_count, 0),
    unresolved: num(summary.unresolved_count, 0),
  })
}

function handleQuickError(err) {
  const status = err?.status || 0
  const detail = err?.message ? String(err.message) : String(err || '未知错误')
  if (status === 404) {
    notify(
      `整理接口未就绪（404）：服务端尚未提供 POST ${QUICK_RUN_ENDPOINT}。已选文件仍保留；可改用「案件记录」里的四步流程完成同样的工作。`,
      'error',
    )
    return
  }
  if (status === 413) {
    notify(
      `文件过大（413）：请求体超过网关上限（64 MB）。请减少文件数量或压缩图片后重试；已选列表保持不变。服务端提示：${detail}`,
      'error',
    )
    return
  }
  // 409（版本冲突）等交给根组件的统一处理：提示「已被其他操作更新，请刷新后重试」并刷新。
  emit('error', err)
}

async function submit() {
  if (running.value) {
    notify('正在整理中：请等待当前整理完成，不要重复提交。', 'error')
    return
  }
  if (!files.value.length) {
    notify('还没有选择文件：请先拖入或选择照片、Word、PDF 等文件。', 'error')
    return
  }

  const picked = files.value.slice()
  running.value = true
  error.value = ''
  publishResult(null)
  startedAt = Date.now()
  elapsed.value = 0
  phase.value = '读取文件'

  try {
    const encoded = []
    for (let index = 0; index < picked.length; index += 1) {
      const item = picked[index]
      phase.value = `读取文件 ${index + 1}/${picked.length}：${item.name}`
      const content_base64 = await readFileAsBase64(item.file)
      encoded.push({
        filename: item.name,
        media_type: item.media_type,
        content_base64,
        kind: item.kind,
        position_key: item.position_key,
      })
    }

    startTicker()
    const response = await api.quickRun(buildPayload(encoded))
    stopTicker()

    const next = normalizeResult(response)
    running.value = false
    phase.value = ''
    publishResult(next)
    files.value = [] // 成功后才清空已选；失败一律保留
    emit('refresh')

    if (!next.documents.length) {
      notify('整理已返回，但没有文稿：请查看结果页的说明与处理步骤；可到「案件记录」检查材料与模板。', 'error')
    } else {
      notify(
        `整理完成：案件 ${next.case_no || `#${next.case_id}`}，生成 ${next.documents.length} 份文稿；待处理疑点 ${num(next.summary.unresolved_count, 0)} 条。`,
        'success',
      )
    }
    emit('navigate', 'results')
  } catch (err) {
    stopTicker()
    running.value = false
    phase.value = ''
    error.value = err?.message ? String(err.message) : String(err || '未知错误')
    handleQuickError(err)
  }
}

/** 「再整理一批」：清空文件与结果，回到「整理」。 */
function quickReset() {
  stopTicker()
  files.value = []
  publishResult(null)
  error.value = ''
  phase.value = ''
  elapsed.value = 0
  startedAt = 0
  running.value = false
  caseNo.value = '' // 案件编号必须清空，否则会撞“编号已重复”
  emit('navigate', 'quick')
}

function openCase(payload) {
  emit('open-case', payload)
}

onUnmounted(() => {
  stopTicker()
})

// 根组件在文档级 drop 时把文件转发进来（拖到页面任意位置都能落到主视图拖拽区）。
defineExpose({ addFiles })
</script>

<template>
  <div class="space-y-5">
    <!-- 结果视图 -->
    <QuickRunResult
      v-if="view === 'results'"
      :result="result"
      :dict="dict"
      @open-case="openCase"
      @again="quickReset"
      @go-quick="emit('navigate', 'quick')"
    />

    <!-- 主视图「整理」 -->
    <template v-else>
      <!-- 1. 说明卡 -->
      <section class="panel p-5">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <h2 class="flex flex-wrap items-center gap-2 text-base font-semibold text-gray-950">
            整理：上传 → 结果
            <span class="chip chip-not-recorded">POST {{ QUICK_RUN_ENDPOINT }}</span>
          </h2>
          <p class="text-xs text-gray-500">普通用户只需要这一页；核对与修改在次级界面「案件记录」</p>
        </div>
        <div class="mt-3 grid gap-3 sm:grid-cols-3">
          <div class="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2">
            <strong class="text-sm text-gray-900">① 上传</strong>
            <p class="mt-0.5 text-xs text-gray-500">照片 / Word / PDF / JSON，可多选、可拖拽</p>
          </div>
          <div class="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2">
            <strong class="text-sm text-gray-900">② 选择输出</strong>
            <p class="mt-0.5 text-xs text-gray-500">Word、PDF 或两者都要（默认两者）</p>
          </div>
          <div class="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2">
            <strong class="text-sm text-gray-900">③ 直接拿结果</strong>
            <p class="mt-0.5 text-xs text-gray-500">结果页显示产物与自动归类说明，可一键下载</p>
          </div>
        </div>

        <div class="mt-3 flex items-start gap-2 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <CircleAlert :size="18" class="mt-0.5 shrink-0" />
          <div class="min-w-0 flex-1">
            <p><strong>部位照片（N-11）：</strong>{{ QUICK_PHOTO_POLICY }}。系统不对照片做识别、描述、分类或推断，既不运行 OCR 也不调用视觉模型。</p>
            <p class="mt-1">
              也请放心：本次没有照片的照片栏位，文稿里写「{{ PHOTO_PLACEHOLDER_TEXT }}」，不会拿其他栏位顶替（R-56）；
              空白栏目一律是“{{ NOT_RECORDED }}”，绝不写成“未见异常”（N-5）。
            </p>
          </div>
        </div>
        <div class="mt-2 rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-700">
          <strong>关于“自动判断”：</strong>类型与目标位置留空时由服务端按文件名推测，结果页会逐条标注
          「按文件名推断」，并给出“去案件记录修改”的入口；推测结果不影响原件（原件只读、永不覆盖）。
        </div>
      </section>

      <!-- 2. 上传卡 -->
      <section class="panel p-5">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <h2 class="flex flex-wrap items-center gap-2 text-base font-semibold text-gray-950">
            上传文件
            <span class="chip chip-not-recorded">支持多选</span>
          </h2>
          <p class="text-xs text-gray-500">单个文件超过 30 MB 会提示；网关（nginx）单请求上限 64 MB</p>
        </div>

        <!-- 拖拽区 -->
        <div
          class="mt-3 cursor-pointer rounded-xl border-2 border-dashed px-4 py-8 text-center transition-colors"
          :class="dragActive ? 'border-blue-500 bg-blue-50' : 'border-gray-300 bg-gray-50 hover:border-blue-400 hover:bg-blue-50/40'"
          role="button"
          tabindex="0"
          aria-label="拖拽文件到此处，或按回车选择文件"
          data-dropzone
          @click="pickFiles"
          @keydown.enter.prevent="pickFiles"
          @keydown.space.prevent="pickFiles"
          @dragenter.prevent="onDragEnter"
          @dragover.prevent="onDragOver"
          @dragleave.prevent="onDragLeave"
          @drop.prevent="onDrop"
        >
          <span class="inline-flex h-10 w-10 items-center justify-center rounded-full bg-white text-blue-600 shadow-panel">
            <Upload :size="20" />
          </span>
          <p class="mt-2 text-sm font-semibold text-gray-900">把照片、Word、PDF 拖到这里</p>
          <p class="mt-0.5 text-xs text-gray-500">或点击本区域 / 按回车选择文件</p>
          <p class="mt-1 text-xs text-gray-400">accept：{{ QUICK_ACCEPT }}</p>
          <input
            ref="fileInput"
            class="sr-only"
            type="file"
            multiple
            :accept="QUICK_ACCEPT"
            aria-label="选择要整理的文件"
            @change="onFileInputChange"
            @click.stop
          />
        </div>

        <!-- 已选文件列表 -->
        <div class="mt-4">
          <div v-if="!files.length" class="rounded-lg border border-gray-200 bg-gray-50 px-4 py-3 text-sm text-gray-500">
            还没有选择文件。把照片、Word、PDF 拖进上面的方框，或点击“选择文件”。
          </div>
          <template v-else>
            <div class="mb-2 flex flex-wrap items-center gap-2">
              <span class="chip chip-not-recorded">已选 {{ files.length }} 个文件</span>
              <span class="chip chip-not-recorded">原始共 {{ quickSizeText(selectedBytes) }}</span>
              <span class="chip chip-not-recorded">base64 后约 {{ quickSizeText(estimateBytes) }}</span>
              <span v-if="gatewayExceeded" class="chip chip-blocking">超过网关 64 MB 上限，可能被拒绝</span>
              <button class="btn-secondary" @click="clearFiles">清空已选</button>
            </div>

            <div class="space-y-3">
              <article v-for="item in files" :key="item.id" class="rounded-lg border border-gray-200 p-3">
                <div class="flex flex-wrap items-center gap-2">
                  <FileText :size="16" class="shrink-0 text-gray-400" />
                  <span class="min-w-0 flex-1 break-all text-sm font-medium text-gray-900">{{ item.name }}</span>
                  <span class="chip chip-not-recorded">{{ quickSizeText(item.size) }}</span>
                  <span class="chip chip-not-recorded">media={{ item.media_type }}</span>
                  <span v-if="item.oversize" class="chip chip-warning">超过 30 MB</span>
                  <span v-if="item.kind === 'photo'" class="chip chip-info">部位照片：不参与识别</span>
                  <button class="btn-secondary" @click="removeFile(item.id)">移除</button>
                </div>

                <div class="mt-2 flex flex-wrap items-center gap-2">
                  <span class="text-xs text-gray-500">类型</span>
                  <div class="flex flex-wrap gap-1.5">
                    <button
                      v-for="chip in QUICK_KIND_CHIPS"
                      :key="chip[0] || 'auto'"
                      type="button"
                      class="cursor-pointer rounded-full px-2.5 py-1 text-xs font-medium transition-colors"
                      :class="item.kind === chip[0]
                        ? 'bg-blue-600 text-white'
                        : 'border border-gray-300 bg-white text-gray-600 hover:bg-gray-50'"
                      :aria-pressed="item.kind === chip[0] ? 'true' : 'false'"
                      :title="chip[0] ? `材料类型 kind=${chip[0]}` : 'kind 留空：交后端按文件名推断'"
                      @click="setKind(item, chip[0])"
                    >
                      {{ kindChipLabel(chip[0], chip[1]) }}
                    </button>
                  </div>

                  <label v-if="item.kind === 'photo'" class="flex items-center gap-2 text-xs text-gray-600">
                    目标位置
                    <select v-model="item.position_key" class="form-input w-auto py-1 text-xs">
                      <option value="">自动（按文件名推测）</option>
                      <optgroup v-for="group in photoGroups" :key="group.section" :label="group.label">
                        <option v-for="option in group.options" :key="option.key" :value="option.key">
                          {{ option.label }}{{ option.wide ? '（宽幅）' : '' }}
                        </option>
                      </optgroup>
                    </select>
                  </label>
                </div>

                <p v-if="item.kind === 'photo'" class="mt-2 text-xs text-gray-500">
                  部位照片：{{ QUICK_PHOTO_POLICY }}。
                </p>
              </article>
            </div>
          </template>
        </div>

        <!-- 输出格式 -->
        <div class="mt-4">
          <p class="text-sm font-medium text-gray-900">输出格式</p>
          <div class="mt-2 flex flex-wrap gap-2">
            <label
              v-for="option in FORMAT_OPTIONS"
              :key="option[0]"
              class="cursor-pointer rounded-full px-3 py-1.5 text-sm font-medium transition-colors"
              :class="formatChoice === option[0]
                ? 'bg-blue-600 text-white'
                : 'border border-gray-300 bg-white text-gray-600 hover:bg-gray-50'"
            >
              <input
                v-model="formatChoice"
                class="sr-only"
                type="radio"
                name="q-format"
                :value="option[0]"
              />
              {{ option[1] }}
            </label>
          </div>
          <p class="mt-1 text-xs text-gray-500">
            选择“只要 PDF”时，若服务端未安装 LibreOffice 会只得到 Word，并在结果页说明原因。
          </p>
        </div>

        <!-- 可选项 -->
        <div class="mt-4">
          <button v-if="!showOptional" class="btn-secondary" :aria-expanded="false" @click="showOptional = true">
            展开可选项（案件编号 / 委托单位 / 姓名 / 备注）
          </button>
          <span v-if="!showOptional" class="ml-2 text-xs text-gray-500">
            一般都是留空的：案件编号会自动生成，信息由系统从材料里读。
          </span>
          <template v-else>
            <button class="btn-secondary" :aria-expanded="true" @click="showOptional = false">收起可选项</button>
            <div class="mt-2 grid gap-3 sm:grid-cols-2">
              <label class="block">
                <span class="text-sm text-gray-700">案件编号（留空自动生成 FY-&lt;年份&gt;-&lt;序号&gt;）</span>
                <input v-model="caseNo" class="form-input mt-1" maxlength="64" placeholder="可留空" />
              </label>
              <label class="block">
                <span class="text-sm text-gray-700">委托单位</span>
                <input v-model="commissionUnit" class="form-input mt-1" maxlength="120" />
              </label>
              <label class="block">
                <span class="text-sm text-gray-700">姓名</span>
                <input v-model="subjectName" class="form-input mt-1" maxlength="80" />
              </label>
              <label class="block">
                <span class="text-sm text-gray-700">备注（交给系统的一次性说明）</span>
                <input v-model="note" class="form-input mt-1" maxlength="500" />
              </label>
            </div>
          </template>
        </div>

        <!-- 提交 -->
        <div class="mt-4 flex flex-wrap items-center gap-3">
          <button class="btn-primary" :disabled="submitDisabled" @click="submit">
            <Loader2 v-if="running" :size="16" class="animate-spin" />
            {{ submitLabel }}
          </button>
          <span class="text-sm text-gray-600" aria-live="polite">{{ phaseText }}</span>
        </div>
        <div class="mt-2 rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 text-xs text-slate-700">
          <strong>提交后会发生什么：</strong>文件一次性上传（浏览器 <code>FileReader</code> 读为 base64），
          服务端登记材料 → 推断类型与部位 → 提取字段与损伤 → 填入模板 → 生成 Word/PDF。
          一次请求完成，界面按“上传中 → 识别中 → 生成文稿”提示预计阶段，不会伪造百分比；
          期间请勿重复点击（按钮已禁用）。
        </div>
      </section>

      <!-- 3. 上次失败 -->
      <section v-if="error" class="rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-800">
        <strong>上次整理失败：</strong>{{ error }}　已选文件仍保留，可直接重试，不会重复上传已成功的部分。
      </section>
    </template>
  </div>
</template>
