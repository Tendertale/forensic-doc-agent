<script setup>
/**
 * DocumentPanel.vue —— 文稿版本、生成、下载、审核、交付。
 *
 * 端点：
 *   api.createDocument(id, payload)          POST  /api/cases/{id}/documents
 *   api.documents(id)                        GET   /api/cases/{id}/documents
 *   api.approveDocument(documentId, payload) POST  /api/documents/{document_id}/approve
 *   api.deliver(id, payload)                 POST  /api/cases/{id}/deliver
 *   api.downloadUrl(documentId, fmt)         GET   /api/documents/{document_id}/download?fmt=docx|pdf
 *
 * 已审核文稿只读（N-8）；待签发稿被阻断时后端返回 409（R-28）。
 * 生成的是模板副本，原件与已审核文稿不被覆盖（R-24 / N-8）。
 */
import { computed, reactive, ref } from 'vue'
import { CircleAlert, Download, FileText, RefreshCw, Settings } from 'lucide-vue-next'
import { api } from '../api.js'

const props = defineProps({
  caseId: { type: Number, default: 0 },
  caseVersion: { type: Number, default: 1 },
  caseDetail: { type: Object, default: null },
  documents: { type: Array, default: () => [] },
  dict: { type: Object, default: () => ({}) },
})

const emit = defineEmits(['refresh', 'notify', 'error', 'open-correction'])

const docStatusLabels = computed(() => {
  const raw = props.dict?.docStatusLabels
  return raw && Object.keys(raw).length
    ? raw
    : { internal_draft: '内部草稿', pending_issue: '待签发稿', approved: '已审核', superseded: '已被替代' }
})

const form = reactive({
  doc_type: 'report',
  doc_status: 'internal_draft',
  template_version: '',
  formats: ['docx', 'pdf'],
  note: '',
})

const busy = ref(false)
const approveBusyId = ref(0)
const deliverBusy = ref(false)

const rows = computed(() => {
  const list = (props.documents || []).slice()
  list.sort((a, b) => Number(b.version_no || 0) - Number(a.version_no || 0))
  return list
})

const blockingOpen = computed(() =>
  (props.caseDetail?.checklist || []).filter(
    (item) => item.status === 'open' && item.severity === 'blocking',
  ),
)

const unresolvedOpen = computed(() =>
  (props.caseDetail?.fields || []).filter((item) => item.status === 'unclear'),
)

const latest = computed(() => rows.value[0] || null)

const templateVersion = computed(
  () => form.template_version || props.caseDetail?.template_version || 'report-template-v1',
)

function docStatusLabel(key) {
  if (!key) return '未记录'
  return docStatusLabels.value[key] || key
}

function docStatusChipClass(key) {
  if (key === 'approved') return 'chip-recorded'
  if (key === 'pending_issue') return 'chip-warning'
  if (key === 'superseded') return 'chip-not-recorded'
  return 'chip-info'
}

const FORMATS = [
  { key: 'docx', label: 'Word 草稿（.docx）' },
  { key: 'pdf', label: 'PDF 预览（.pdf）' },
]

function toggleFormat(key) {
  const index = form.formats.indexOf(key)
  if (index >= 0) {
    if (form.formats.length === 1) return
    form.formats.splice(index, 1)
  } else {
    form.formats.push(key)
  }
}

async function createDocument() {
  if (!props.caseId) return
  busy.value = true
  try {
    const created = await api.createDocument(props.caseId, {
      expected_version: props.caseVersion,
      doc_type: form.doc_type.trim() || 'report',
      doc_status: form.doc_status,
      template_version: templateVersion.value,
      formats: form.formats.slice(),
      note: form.note.trim(),
    })
    const status = created && created.doc_status ? created.doc_status : form.doc_status
    emit(
      'notify',
      status === 'internal_draft'
        ? '已生成内部草稿（可带疑点，会在草稿中标注【待核对】）'
        : '已生成文稿版本',
      'success',
    )
    form.note = ''
    emit('refresh')
  } catch (error) {
    if (error && error.status === 409) {
      emit(
        'notify',
        '存在未解决的阻断项，不能生成待签发稿；请先处理校核清单（R-28）',
        'error',
      )
    }
    emit('error', error)
  } finally {
    busy.value = false
  }
}

function download(row, fmt) {
  if (!row || !row.id) return
  const url = api.downloadUrl(row.id, fmt)
  window.open(url, '_blank', 'noopener')
  emit('notify', `已请求下载 ${fmt.toUpperCase()}（文件名格式：案件编号＋文档类型＋版本，R-51）`, 'success')
}

async function approve(row) {
  if (!row || !row.id) return
  approveBusyId.value = row.id
  try {
    await api.approveDocument(row.id, {
      expected_version: props.caseVersion,
      document_id: row.id,
      include_layout: true,
    })
    emit('notify', '已标记为已审核（旧版本继续保留，新版本重新进入复核流程，R-41）', 'success')
    emit('refresh')
  } catch (error) {
    emit('error', error)
  } finally {
    approveBusyId.value = 0
  }
}

async function deliver(row) {
  if (!props.caseId) return
  deliverBusy.value = true
  try {
    const payload = {
      expected_version: props.caseVersion,
      formats: form.formats.slice(),
      note: form.note.trim(),
    }
    if (row && row.id) payload.document_id = row.id
    await api.deliver(props.caseId, payload)
    emit('notify', '交付已保存（系统只生成文书，不代替复核、签名与盖章程序，R-29）', 'success')
    emit('refresh')
  } catch (error) {
    emit('error', error)
  } finally {
    deliverBusy.value = false
  }
}
</script>

<template>
  <section class="panel" aria-label="文稿版本">
    <header class="border-b border-gray-200 p-4">
      <h3 class="flex items-center gap-2 text-sm font-semibold text-gray-950">
        <FileText :size="16" />
        文稿版本（查看文档：生成 → 下载 → 审核 → 交付）
      </h3>
      <p class="mt-1 text-xs text-gray-500">
        按模板副本填充，保持模板的标题、段落、表格与页面格式（R-5 / R-24）；已审核文稿只读（N-8）；
        待签发稿必须没有未解决阻断项（R-28）。
      </p>
    </header>

    <div
      v-if="blockingOpen.length || unresolvedOpen.length"
      class="border-b border-amber-200 bg-amber-50 p-4 text-sm text-amber-900"
    >
      <p class="flex items-start gap-2">
        <CircleAlert :size="17" class="mt-0.5 shrink-0" />
        <span>
          <template v-if="blockingOpen.length">
            存在 {{ blockingOpen.length }} 项未解决阻断项，生成「待签发稿」会被拒绝（HTTP 409）。
          </template>
          <template v-if="unresolvedOpen.length">
            仍有 {{ unresolvedOpen.length }} 个待辨认/冲突字段，草稿会保留待核对标记，需在进入待签发稿前处理（R-46）。
          </template>
        </span>
      </p>
      <button class="btn-secondary mt-2" @click="emit('open-correction')">去录入修正（④ 确认修改）</button>
    </div>

    <div class="grid gap-3 border-b border-gray-200 p-4 lg:grid-cols-12">
      <label class="lg:col-span-2">
        <span class="mb-1 block text-xs font-medium text-gray-500">文档类型</span>
        <input v-model="form.doc_type" class="form-input" type="text" placeholder="report / inspection_record" />
      </label>
      <label class="lg:col-span-3">
        <span class="mb-1 block text-xs font-medium text-gray-500">文稿状态</span>
        <select v-model="form.doc_status" class="form-input">
          <option value="internal_draft">{{ docStatusLabel('internal_draft') }}（可带疑点）</option>
          <option value="pending_issue">{{ docStatusLabel('pending_issue') }}（须无阻断项）</option>
        </select>
      </label>
      <label class="lg:col-span-3">
        <span class="mb-1 block text-xs font-medium text-gray-500">模板版本</span>
        <input v-model="form.template_version" class="form-input" type="text" :placeholder="templateVersion" />
      </label>
      <div class="lg:col-span-4">
        <span class="mb-1 block text-xs font-medium text-gray-500">输出格式</span>
        <div class="flex flex-wrap gap-2">
          <button
            v-for="item in FORMATS"
            :key="item.key"
            type="button"
            class="rounded-lg border px-3 py-2 text-xs"
            :class="form.formats.includes(item.key)
              ? 'border-blue-300 bg-blue-50 text-blue-800'
              : 'border-gray-300 bg-white text-gray-600'"
            @click="toggleFormat(item.key)"
          >
            {{ form.formats.includes(item.key) ? '✓ ' : '' }}{{ item.label }}
          </button>
        </div>
      </div>
      <label class="lg:col-span-8">
        <span class="mb-1 block text-xs font-medium text-gray-500">本次生成备注</span>
        <input v-model="form.note" class="form-input" type="text" placeholder="处理记录用，不进入文书正文" />
      </label>
      <div class="flex items-end gap-2 lg:col-span-4">
        <button class="btn-primary" :disabled="busy" @click="createDocument">
          <RefreshCw v-if="busy" :size="16" class="animate-spin" />
          生成文稿
        </button>
        <button class="btn-secondary" :disabled="deliverBusy || !latest" @click="deliver(latest)">
          保存交付
        </button>
      </div>
    </div>

    <div class="overflow-x-auto">
      <table class="min-w-full divide-y divide-gray-200 text-sm">
        <thead class="bg-gray-50 text-left text-xs font-semibold text-gray-500">
          <tr>
            <th class="px-4 py-3">版本</th>
            <th class="px-4 py-3">类型</th>
            <th class="px-4 py-3">状态</th>
            <th class="px-4 py-3">模板 / 规则版本</th>
            <th class="px-4 py-3">渲染引擎</th>
            <th class="px-4 py-3">待核对项</th>
            <th class="px-4 py-3">生成时间</th>
            <th class="px-4 py-3 text-right">操作</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-gray-100">
          <tr v-if="!rows.length">
            <td colspan="8" class="px-4 py-8 text-center text-gray-500">
              暂无文稿版本（未记录）。生成内部草稿可带疑点，待签发稿需先解决全部阻断项。
            </td>
          </tr>
          <tr v-for="row in rows" v-else :key="row.id">
            <td class="px-4 py-3 font-medium text-gray-950">
              v{{ row.version_no ?? '未记录' }}
              <span v-if="row.doc_status !== 'approved'" class="ml-1 text-xs text-gray-400">（草稿状态明确标识，R-51）</span>
            </td>
            <td class="px-4 py-3 text-gray-700">{{ row.doc_type || '未记录' }}</td>
            <td class="px-4 py-3">
              <span class="chip" :class="docStatusChipClass(row.doc_status)">{{ docStatusLabel(row.doc_status) }}</span>
            </td>
            <td class="px-4 py-3 text-gray-600">
              {{ row.template_version || '未记录' }} / {{ row.term_rule_version || '未记录' }}
            </td>
            <td class="px-4 py-3 text-gray-600">{{ row.render_engine || '未报告' }}</td>
            <td class="px-4 py-3 text-gray-600">
              {{ Array.isArray(row.unresolved) && row.unresolved.length ? row.unresolved.length : '未记录' }}
              <span v-if="row.blocked_reason" class="mt-1 block text-xs text-red-700">{{ row.blocked_reason }}</span>
            </td>
            <td class="px-4 py-3 whitespace-nowrap text-gray-500">{{ row.created_at || '未记录' }}</td>
            <td class="px-4 py-3">
              <div class="flex flex-wrap items-center justify-end gap-2">
                <button class="btn-secondary px-3 py-1.5" @click="download(row, 'docx')">
                  <Download :size="15" />
                  docx
                </button>
                <button class="btn-secondary px-3 py-1.5" @click="download(row, 'pdf')">
                  <Download :size="15" />
                  pdf
                </button>
                <button
                  class="btn-primary px-3 py-1.5"
                  :disabled="row.doc_status === 'approved' || approveBusyId === row.id"
                  :title="row.doc_status === 'approved' ? '已审核文稿只读（N-8）' : '标记为已审核'"
                  @click="approve(row)"
                >
                  <RefreshCw v-if="approveBusyId === row.id" :size="15" class="animate-spin" />
                  {{ row.doc_status === 'approved' ? '已审核' : '标记已审核' }}
                </button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <footer class="flex flex-wrap items-center justify-between gap-2 border-t border-gray-200 px-4 py-3 text-xs text-gray-500">
      <p>下载地址由 api.downloadUrl(documentId, fmt) 生成；文件名格式为「案件编号＋文档类型＋版本」（R-51）。</p>
      <button class="btn-secondary px-3 py-1.5" @click="emit('refresh')">
        <RefreshCw :size="15" />
        刷新版本列表
      </button>
    </footer>

    <p class="flex items-start gap-2 border-t border-gray-200 px-4 py-3 text-xs text-gray-500">
      <Settings :size="14" class="mt-0.5 shrink-0" />
      系统中的文书生成环节与司法鉴定的复核、统一文本格式、鉴定人签名和机构盖章等人工程序衔接，不代替这些程序（R-29）。
    </p>
  </section>
</template>
