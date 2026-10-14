<script setup>
/**
 * 结果视图：提交成功后显示产物（Word / PDF）、摘要、自动归类说明、处理步骤与系统说明。
 *
 * 文案与结构逐字对齐无构建工作台 frontend/static/workbench.js §18b 的
 * `quickResultHeader` / `quickDocumentsCard` / `quickSummaryCard` /
 * `quickAssignmentsCard` / `quickStepsCard` / `quickNotesCard`。
 *
 * 红线：空白一律显示“未记录”，绝不显示“未见异常”（N-5）；
 *       系统不生成鉴定意见（N-1）；下载地址只接受同源地址（本工作台无任何外链）。
 */
import { computed } from 'vue'
import { api } from '../api.js'
import { CircleAlert, CircleCheck, Download, FileText } from 'lucide-vue-next'

const props = defineProps({
  result: { type: Object, default: null },
  dict: { type: Object, default: () => ({}) },
})

const emit = defineEmits(['open-case', 'again', 'go-quick'])

const NOT_RECORDED = '未记录'

/** 本次未导出 PDF 时的固定说明（与 static 版逐字一致）。 */
const PDF_REASON =
  '本次未导出 PDF：服务端 LibreOffice 不可用或导出失败（校核项 DOCUMENT_PDF_MISSING）。' +
  'Word 仍可下载；可在「案件记录 → 文稿」重新生成后再取 PDF。'

function num(value, fallback = 0) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

/** 空白即“未记录”（N-5）。永远不返回“未见异常”。 */
function valText(value) {
  if (value === null || value === undefined) return NOT_RECORDED
  const text = String(value).trim()
  if (text === '' || text === '/' || text === '—' || text === '-' || text === '无记录') return NOT_RECORDED
  return text
}

function labelOf(map, key, fallback) {
  if (key === null || key === undefined || key === '') return fallback || ''
  return (map && map[key]) || key
}

/* ------------------------------------------------------------ 取值 */

const caseId = computed(() => num(props.result?.case_id, 0))
const caseNo = computed(() => String(props.result?.case_no || ''))
const stageLabel = computed(() => labelOf(props.dict?.stageLabels, props.result?.stage, props.result?.stage || ''))
const documents = computed(() => (Array.isArray(props.result?.documents) ? props.result.documents : []))
const assignments = computed(() => (Array.isArray(props.result?.assignments) ? props.result.assignments : []))
const steps = computed(() => (Array.isArray(props.result?.steps) ? props.result.steps : []))
const notes = computed(() =>
  (Array.isArray(props.result?.notes) ? props.result.notes : []).filter((item) => typeof item === 'string'),
)
const summary = computed(() => (props.result?.summary && typeof props.result.summary === 'object' ? props.result.summary : {}))
const blockingCount = computed(() => num(summary.value.blocking_count, 0))

/** 服务端案件地址只有在同源时才显示为链接（不把用户导向站外）。 */
const caseUrl = computed(() => {
  const url = String(props.result?.case_url || '')
  return urlAllowed(url) ? url : ''
})

const summaryCards = computed(() => [
  { label: '材料', value: num(summary.value.material_count, 0), cls: '' },
  { label: '字段', value: num(summary.value.field_count, 0), cls: '' },
  { label: '损伤', value: num(summary.value.injury_count, 0), cls: '' },
  { label: '文稿', value: num(summary.value.document_count, 0), cls: '' },
  { label: '待处理疑点', value: num(summary.value.unresolved_count, 0), cls: 'text-amber-700' },
  { label: '阻断项', value: blockingCount.value, cls: blockingCount.value ? 'text-red-700' : '' },
])

/* ------------------------------------------------------------ 下载地址 */

/**
 * 下载地址白名单：只接受同源相对路径或与当前页面同源的绝对地址。
 * 本工作台不引用任何外部资源，也不允许把用户导向站外地址；
 * 不满足时返回空串，由调用方把按钮置灰并说明原因。
 */
function urlAllowed(url) {
  const text = String(url || '').trim()
  if (!text) return false
  if (text.indexOf('://') < 0) return true
  if (text.charAt(0) === '/') return true
  if (typeof window === 'undefined' || !window.location) return false
  return text.indexOf(`${window.location.origin}/`) === 0
}

/** 文稿下载地址：优先用响应里的 download_urls，缺失或站外时回退 api.downloadUrl()（同一端点）。 */
function downloadUrl(doc, fmt) {
  const item = doc || {}
  const urls = item.download_urls && typeof item.download_urls === 'object' ? item.download_urls : {}
  const direct = fmt === 'pdf' ? urls.pdf : urls.docx
  if (direct && urlAllowed(direct)) return String(direct)
  if (item.id !== undefined && item.id !== null && item.id !== '') return api.downloadUrl(item.id, fmt)
  return ''
}

/** 该文稿在本次响应里是否真的产出了指定格式（缺失时按钮必须置灰）。 */
function hasFormat(doc, fmt) {
  const item = doc || {}
  const urls = item.download_urls && typeof item.download_urls === 'object' ? item.download_urls : {}
  if (fmt === 'pdf') {
    if (item.has_pdf !== undefined && item.has_pdf !== null) return Boolean(item.has_pdf)
    return Boolean(urls.pdf || item.filename_pdf || item.pdf_path)
  }
  if (item.has_docx !== undefined && item.has_docx !== null) return Boolean(item.has_docx)
  return Boolean(urls.docx || item.filename_docx || item.docx_path)
}

function docTitle(doc) {
  const item = doc || {}
  return item.filename_docx || item.filename_pdf || `文稿 #${num(item.id, 0)}`
}

function docxButtontitle(doc, hasDocx) {
  if (hasDocx) return '服务端未返回可用的下载地址'
  return '本次未生成 Word 文件'
}

/* ------------------------------------------------------------ 归类说明 */

function kindLabel(row) {
  return row && row.kind_label ? row.kind_label : labelOf(props.dict?.materialKindLabels, row?.kind, NOT_RECORDED)
}

function kindFlag(row) {
  if (!row || row.kind_inferred === undefined || row.kind_inferred === null) {
    return { text: '来源未标注', cls: 'chip-not-recorded' }
  }
  if (row.kind_inferred) return { text: '按文件名推断，可修改', cls: 'chip-warning' }
  return { text: '已指定', cls: 'chip-recorded' }
}

function positionLabel(row) {
  if (!row) return NOT_RECORDED
  if (row.position_key) {
    const label = row.position_label || labelOf(photoLabelMap(), row.position_key, row.position_key)
    return { text: label, flag: row.position_inferred ? '按文件名推测' : '已指定', cls: row.position_inferred ? 'chip-warning' : 'chip-recorded' }
  }
  return { text: NOT_RECORDED, flag: '未指定位置：不会进入文稿', cls: 'chip-blocking' }
}

function photoLabelMap() {
  // 结果行已由后端给出 position_label；这里只作为缺失时的兜底（不猜测、不推断）。
  return {}
}

function reasonsOf(row) {
  const reasons = []
  if (row && row.kind_reason) reasons.push(`类型：${row.kind_reason}`)
  if (row && row.kind === 'photo' && row.position_reason) reasons.push(`位置：${row.position_reason}`)
  return reasons.join('；')
}
</script>

<template>
  <div class="space-y-5">
    <!-- 还没有结果 -->
    <section v-if="!result" class="panel p-5">
      <h2 class="text-base font-semibold text-gray-950">结果</h2>
      <p class="mt-2 text-sm text-gray-600">
        还没有整理结果。到「整理」页上传文件，完成后会自动跳到本页。
      </p>
      <div class="mt-4">
        <button class="btn-primary" @click="emit('go-quick')">去整理</button>
      </div>
    </section>

    <template v-else>
      <!-- 1. 结果头 -->
      <section class="panel p-5">
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div class="flex min-w-0 flex-wrap items-center gap-2">
            <CircleCheck :size="20" class="shrink-0 text-emerald-600" />
            <h2 class="text-base font-semibold text-gray-950">整理完成</h2>
            <span v-if="caseNo" class="chip chip-info">{{ caseNo }}</span>
            <span v-else class="chip chip-warning">案件编号未记录</span>
            <span v-if="stageLabel" class="chip chip-not-recorded">阶段：{{ stageLabel }}</span>
          </div>
          <div class="flex flex-wrap gap-2">
            <button v-if="caseId" class="btn-primary" @click="emit('open-case', { id: caseId, goto: '' })">
              查看案件记录
            </button>
            <button class="btn-secondary" @click="emit('again')">再整理一批</button>
          </div>
        </div>

        <p class="mt-3 text-sm text-gray-600">
          文稿已生成，可直接用下面的按钮下载。系统不生成鉴定意见（N-1）；空白栏目一律是“未记录”，绝不写成“未见异常”（N-5）。
        </p>
        <p v-if="caseUrl" class="mt-1 break-all text-xs text-gray-500">
          服务端案件地址：<a class="text-blue-700 underline" :href="caseUrl">{{ caseUrl }}</a>（同源地址，仅本机/内网可访问）
        </p>
        <div
          v-if="blockingCount > 0"
          class="mt-3 flex items-start gap-2 rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-800"
        >
          <CircleAlert :size="18" class="mt-0.5 shrink-0" />
          <p class="min-w-0 flex-1">
            <strong>存在阻断项，不能生成待签发稿，请到案件记录处理。</strong>
            阻断项 {{ blockingCount }} 条。
          </p>
        </div>
      </section>

      <!-- 2. 产物 -->
      <section class="panel p-5">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <h2 class="flex items-center gap-2 text-base font-semibold text-gray-950">
            产物（Word / PDF）
            <span class="chip chip-not-recorded">{{ documents.length }} 份</span>
          </h2>
          <p class="text-xs text-gray-500">
            下载地址来自响应 download_urls，缺失时回退 GET /api/documents/{id}/download?fmt=
          </p>
        </div>

        <div v-if="!documents.length" class="mt-3 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          服务端本次没有返回任何文稿。常见原因：缺少报告模板（template 材料）、所有材料都未能识别、或渲染被阻断。
          请到「案件记录」核对材料清单与校核清单（notes 与处理步骤见下）。
        </div>

        <div v-else class="mt-3 space-y-3">
          <article v-for="doc in documents" :key="doc.id ?? docTitle(doc)" class="rounded-lg border border-gray-200 p-4">
            <div class="flex flex-wrap items-start justify-between gap-3">
              <div class="min-w-0">
                <p class="flex items-center gap-2 text-sm font-semibold text-gray-900">
                  <FileText :size="16" class="shrink-0 text-gray-400" />
                  <span class="break-all">{{ docTitle(doc) }}</span>
                </p>
                <div class="mt-1.5 flex flex-wrap items-center gap-1.5">
                  <span class="chip chip-info">
                    {{ labelOf(dict?.docStatusLabels, doc.doc_status, doc.doc_status || NOT_RECORDED) }}
                  </span>
                  <span class="chip chip-not-recorded">版本 v{{ num(doc.version_no, 1) }}</span>
                  <span class="chip chip-not-recorded">类型 {{ doc.doc_type || NOT_RECORDED }}</span>
                  <span :class="hasFormat(doc, 'docx') ? 'chip chip-recorded' : 'chip chip-not-recorded'">
                    {{ hasFormat(doc, 'docx') ? 'Word 已生成' : '无 Word' }}
                  </span>
                  <span :class="hasFormat(doc, 'pdf') ? 'chip chip-recorded' : 'chip chip-warning'">
                    {{ hasFormat(doc, 'pdf') ? 'PDF 已生成' : '无 PDF' }}
                  </span>
                </div>
              </div>

              <div class="flex flex-wrap gap-2">
                <a
                  v-if="hasFormat(doc, 'docx') && downloadUrl(doc, 'docx')"
                  class="btn-primary"
                  :href="downloadUrl(doc, 'docx')"
                  download
                >
                  <Download :size="16" />
                  下载 Word（.docx）
                </a>
                <button v-else class="btn-secondary" disabled :title="docxButtontitle(doc, hasFormat(doc, 'docx'))">
                  Word 不可用
                </button>

                <a
                  v-if="hasFormat(doc, 'pdf') && downloadUrl(doc, 'pdf')"
                  class="btn-primary"
                  :href="downloadUrl(doc, 'pdf')"
                  download
                >
                  <Download :size="16" />
                  下载 PDF
                </a>
                <button v-else class="btn-secondary" disabled :title="PDF_REASON">PDF 不可用</button>
              </div>
            </div>

            <dl class="mt-3 grid gap-2 text-sm sm:grid-cols-2">
              <div class="flex gap-2">
                <dt class="w-24 shrink-0 text-gray-500">Word 文件名</dt>
                <dd class="min-w-0 break-all" :class="doc.filename_docx ? 'text-gray-900' : 'text-gray-400'">
                  {{ valText(doc.filename_docx) }}
                </dd>
              </div>
              <div class="flex gap-2">
                <dt class="w-24 shrink-0 text-gray-500">PDF 文件名</dt>
                <dd class="min-w-0 break-all" :class="doc.filename_pdf ? 'text-gray-900' : 'text-gray-400'">
                  {{ valText(doc.filename_pdf) }}
                </dd>
              </div>
            </dl>

            <p
              v-if="!hasFormat(doc, 'pdf')"
              class="mt-3 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900"
            >
              {{ PDF_REASON }}
            </p>
            <p
              v-if="doc.blocked_reason"
              class="mt-3 rounded-lg border border-red-300 bg-red-50 px-3 py-2 text-xs text-red-800"
            >
              服务端阻断原因：{{ doc.blocked_reason }}
            </p>
          </article>
        </div>
      </section>

      <!-- 3. 摘要 -->
      <section class="panel p-5">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <h2 class="text-base font-semibold text-gray-950">摘要</h2>
          <div class="flex flex-wrap gap-1.5">
            <span class="chip chip-warning">警告 {{ num(summary.warning_count, 0) }}</span>
            <span class="chip chip-not-recorded">提示 {{ num(summary.info_count, 0) }}</span>
          </div>
        </div>
        <div class="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">
          <div v-for="card in summaryCards" :key="card.label" class="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2">
            <p class="text-2xl font-semibold tabular-nums" :class="card.cls || 'text-gray-950'">{{ card.value }}</p>
            <p class="mt-0.5 text-xs text-gray-500">{{ card.label }}</p>
          </div>
        </div>
        <div
          v-if="blockingCount"
          class="mt-3 rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-800"
        >
          <strong>存在阻断项，不能生成待签发稿，请到案件记录处理。</strong>
        </div>
        <div v-else class="mt-3 rounded-lg border border-emerald-300 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
          没有阻断项；待处理疑点 {{ num(summary.unresolved_count, 0) }} 条仍需专业人员核对后才能签发。
        </div>
      </section>

      <!-- 4. 自动归类说明 -->
      <section v-if="assignments.length" class="panel p-5">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <h2 class="flex items-center gap-2 text-base font-semibold text-gray-950">
            自动归类说明
            <span class="chip chip-not-recorded">{{ assignments.length }} 个文件</span>
          </h2>
          <p class="text-xs text-gray-500">标注“按文件名推断，可修改”的条目请到案件记录确认</p>
        </div>
        <div class="mt-3 overflow-x-auto">
          <table class="w-full min-w-[46rem] border-collapse text-sm">
            <thead>
              <tr class="border-b border-gray-200 text-left text-xs uppercase tracking-wide text-gray-500">
                <th class="px-2 py-2 font-medium">文件</th>
                <th class="px-2 py-2 font-medium">类型</th>
                <th class="px-2 py-2 font-medium">目标位置（仅部位照片）</th>
                <th class="px-2 py-2 font-medium">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(row, index) in assignments" :key="`${row.filename}-${index}`" class="border-b border-gray-100 align-top">
                <td class="px-2 py-2">
                  <strong class="break-all text-gray-900">{{ row.filename || '未命名文件' }}</strong>
                  <p v-if="reasonsOf(row)" class="mt-0.5 text-xs text-gray-500">{{ reasonsOf(row) }}</p>
                </td>
                <td class="px-2 py-2">
                  <span class="whitespace-nowrap">{{ kindLabel(row) }}</span>
                  <span class="ml-1" :class="kindFlag(row).cls">{{ kindFlag(row).text }}</span>
                </td>
                <td class="px-2 py-2">
                  <template v-if="row.kind === 'photo'">
                    <span :class="row.position_key ? 'text-gray-900' : 'text-gray-400'">{{ positionLabel(row).text }}</span>
                    <span class="ml-1" :class="positionLabel(row).cls">{{ positionLabel(row).flag }}</span>
                  </template>
                  <span v-else class="text-gray-400">不适用</span>
                </td>
                <td class="px-2 py-2">
                  <button
                    v-if="caseId"
                    class="btn-secondary"
                    @click="emit('open-case', { id: caseId, goto: row.kind === 'photo' ? 'photo' : 'materials' })"
                  >
                    去案件记录修改
                  </button>
                  <span v-else class="text-xs text-gray-500">缺少 case_id</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <!-- 5. 服务端处理步骤 -->
      <section v-if="steps.length" class="panel p-5">
        <h2 class="flex items-center gap-2 text-base font-semibold text-gray-950">
          服务端处理步骤
          <span class="chip chip-not-recorded">steps</span>
        </h2>
        <ul class="mt-3 space-y-2">
          <li v-for="(step, index) in steps" :key="`${step?.step}-${index}`" class="flex items-start gap-2 text-sm">
            <span :class="step && step.ok ? 'chip chip-recorded' : 'chip chip-blocking'">
              {{ step && step.ok ? '完成' : '未完成' }}
            </span>
            <span class="min-w-0 flex-1">
              <strong class="text-gray-900">{{ (step && step.step) || NOT_RECORDED }}</strong>
              <span v-if="step && step.detail" class="text-gray-500">：{{ step.detail }}</span>
            </span>
          </li>
        </ul>
      </section>

      <!-- 6. 系统说明 -->
      <section v-if="notes.length" class="panel p-5">
        <h2 class="text-base font-semibold text-gray-950">系统说明（notes）</h2>
        <p class="mt-1 text-xs text-gray-500">未提供模板、PDF 导出失败原因、照片栏位提示等</p>
        <ul class="mt-3 list-disc space-y-1 pl-5 text-sm text-gray-700">
          <li v-for="(note, index) in notes" :key="index">{{ note }}</li>
        </ul>
      </section>
    </template>
  </div>
</template>
