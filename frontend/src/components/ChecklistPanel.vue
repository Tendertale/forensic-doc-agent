<script setup>
/**
 * ChecklistPanel.vue —— 校核清单：处理 / 豁免（阻塞项阻断待签发稿）。
 *
 * 端点：
 *   api.runCheck(id, payload)                 POST  /api/cases/{id}/check
 *   api.checklist(id, filters)                GET   /api/cases/{id}/checklist
 *   api.updateChecklistItem(itemId, payload)  PATCH /api/checklist/{item_id}
 *
 * 校核信息与文书正文分开：识别候选、内部提示与处理说明只放在校核表中（R-46）。
 * 豁免必须填写理由（后端 ChecklistItemUpdate 校验）。
 */
import { computed, reactive, ref } from 'vue'
import { CircleAlert, RefreshCw, ShieldAlert } from 'lucide-vue-next'
import { api } from '../api.js'

const props = defineProps({
  caseId: { type: Number, default: 0 },
  caseVersion: { type: Number, default: 1 },
  items: { type: Array, default: () => [] },
  dict: { type: Object, default: () => ({}) },
})

const emit = defineEmits(['refresh', 'notify', 'error'])

const CATEGORY_KEYS = ['recognition', 'content', 'source', 'format', 'correction']
const STATUS_KEYS = ['open', 'resolved', 'waived']
const SEVERITY_ORDER = { blocking: 0, warning: 1, info: 2 }

const categoryLabels = computed(() => {
  const raw = props.dict?.checkCategoryLabels
  return raw && Object.keys(raw).length
    ? raw
    : { recognition: '识别质量', content: '内容质量', source: '来源完整性', format: '格式质量', correction: '修改可靠性' }
})
const severityLabels = computed(() => {
  const raw = props.dict?.checkSeverityLabels
  return raw && Object.keys(raw).length ? raw : { blocking: '阻断', warning: '警告', info: '提示' }
})
const statusLabels = computed(() => {
  const raw = props.dict?.checkStatusLabels
  return raw && Object.keys(raw).length ? raw : { open: '待处理', resolved: '已解决', waived: '已豁免' }
})

const filterStatus = ref('open')
const filterCategory = ref('')
const filterSeverity = ref('')
const notes = reactive({})
const busyId = ref(0)
const checkPending = ref(false)
const checkDocumentId = ref(0)
const includeLayout = ref(true)

const rows = computed(() => {
  const list = (props.items || []).filter((item) => {
    if (filterStatus.value && item.status !== filterStatus.value) return false
    if (filterCategory.value && item.category !== filterCategory.value) return false
    if (filterSeverity.value && item.severity !== filterSeverity.value) return false
    return true
  })
  return list.slice().sort((a, b) => {
    const sevA = SEVERITY_ORDER[a.severity] ?? 9
    const sevB = SEVERITY_ORDER[b.severity] ?? 9
    if (sevA !== sevB) return sevA - sevB
    return Number(a.id || 0) - Number(b.id || 0)
  })
})

const counts = computed(() => {
  const all = props.items || []
  return {
    total: all.length,
    open: all.filter((item) => item.status === 'open').length,
    blocking: all.filter((item) => item.status === 'open' && item.severity === 'blocking').length,
    warning: all.filter((item) => item.status === 'open' && item.severity === 'warning').length,
  }
})

function categoryLabel(key) {
  return categoryLabels.value[key] || key || '未记录'
}

function severityLabel(key) {
  return severityLabels.value[key] || key || '未记录'
}

function statusLabel(key) {
  return statusLabels.value[key] || key || '未记录'
}

function severityChipClass(key) {
  if (key === 'blocking') return 'chip-blocking'
  if (key === 'warning') return 'chip-warning'
  return 'chip-info'
}

function statusChipClass(key) {
  if (key === 'resolved') return 'chip-recorded'
  if (key === 'waived') return 'chip-info'
  return 'chip-warning'
}

function sourceText(item) {
  const source = item.source
  if (!source || typeof source !== 'object') return '未记录'
  const parts = []
  if (source.material_id) parts.push(`材料 #${source.material_id}`)
  if (source.page || source.page_number) parts.push(`第 ${source.page || source.page_number} 页`)
  if (source.field_label || source.field_key) {
    parts.push(`字段 ${source.field_label || ''}${source.field_key ? `（${source.field_key}）` : ''}`)
  }
  if (source.injury_id) parts.push(`损伤 #${source.injury_id}`)
  if (source.document_id) parts.push(`文稿 #${source.document_id}`)
  if (source.code) parts.push(`代码 ${source.code}`)
  // 照片栏位类校核项（PHOTO_SLOT_EMPTY / PHOTO_POSITION_MISSING）的来源字段
  if (source.position_label || source.position_key) {
    const count = source.assigned_count === undefined || source.assigned_count === null
      ? ''
      : `，已指派 ${source.assigned_count} 张`
    parts.push(
      `栏位 ${source.position_label || ''}${source.position_key ? `（${source.position_key}）` : ''}${count}`,
    )
  }
  if (Array.isArray(source.conflict) && source.conflict.length) {
    parts.push(`冲突来源 ${source.conflict.length} 条`)
  }
  return parts.length ? parts.join(' · ') : '未记录'
}

async function update(item, status) {
  if (!props.caseId) return
  const note = String(notes[item.id] || '').trim()
  if (status === 'waived' && !note) {
    emit('notify', '豁免校核项必须填写理由', 'error')
    return
  }
  busyId.value = item.id
  try {
    await api.updateChecklistItem(item.id, {
      expected_version: props.caseVersion,
      status,
      note,
      resolved_by: '复核人',
    })
    emit('notify', `校核项已标记为「${statusLabel(status)}」`, 'success')
    emit('refresh')
  } catch (error) {
    emit('error', error)
  } finally {
    busyId.value = 0
  }
}

async function runCheck() {
  if (!props.caseId) return
  checkPending.value = true
  try {
    const payload = {
      expected_version: props.caseVersion,
      include_layout: !!includeLayout.value,
    }
    if (Number(checkDocumentId.value) > 0) payload.document_id = Number(checkDocumentId.value)
    const result = await api.runCheck(props.caseId, payload)
    const added = Number(result?.items ?? result?.created ?? result?.count ?? 0)
    emit('notify', `校核完成${added ? `：写入 ${added} 项` : ''}`, 'success')
    emit('refresh')
  } catch (error) {
    emit('error', error)
  } finally {
    checkPending.value = false
  }
}

async function reload() {
  if (!props.caseId) return
  try {
    await api.checklist(props.caseId, {
      status: filterStatus.value,
      category: filterCategory.value,
    })
    emit('refresh')
  } catch (error) {
    emit('error', error)
  }
}
</script>

<template>
  <section class="panel" aria-label="校核清单">
    <header class="border-b border-gray-200 p-4">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div class="min-w-0">
          <h3 class="flex items-center gap-2 text-sm font-semibold text-gray-950">
            <ShieldAlert :size="16" />
            校核清单（处理 / 豁免）
          </h3>
          <p class="mt-1 text-xs text-gray-500">
            校核信息与文书正文分开存放（R-46）；未解决的阻断项会阻止生成待签发稿（R-28）。豁免必须写明理由。
          </p>
        </div>
        <div class="flex flex-wrap items-center gap-2">
          <span class="chip chip-blocking">阻断 {{ counts.blocking }}</span>
          <span class="chip chip-warning">警告 {{ counts.warning }}</span>
          <span class="chip chip-info">待处理 {{ counts.open }} / 共 {{ counts.total }}</span>
        </div>
      </div>

      <div class="mt-3 flex flex-wrap items-end gap-3">
        <label class="w-36">
          <span class="mb-1 block text-xs font-medium text-gray-500">处理状态</span>
          <select v-model="filterStatus" class="form-input">
            <option value="">全部</option>
            <option v-for="key in STATUS_KEYS" :key="key" :value="key">{{ statusLabel(key) }}</option>
          </select>
        </label>
        <label class="w-40">
          <span class="mb-1 block text-xs font-medium text-gray-500">校核类别</span>
          <select v-model="filterCategory" class="form-input">
            <option value="">全部类别</option>
            <option v-for="key in CATEGORY_KEYS" :key="key" :value="key">{{ categoryLabel(key) }}</option>
          </select>
        </label>
        <label class="w-32">
          <span class="mb-1 block text-xs font-medium text-gray-500">严重级别</span>
          <select v-model="filterSeverity" class="form-input">
            <option value="">全部级别</option>
            <option value="blocking">{{ severityLabel('blocking') }}</option>
            <option value="warning">{{ severityLabel('warning') }}</option>
            <option value="info">{{ severityLabel('info') }}</option>
          </select>
        </label>
        <label class="w-32">
          <span class="mb-1 block text-xs font-medium text-gray-500">指定文稿 ID</span>
          <input v-model.number="checkDocumentId" class="form-input" type="number" min="0" placeholder="0=全部" />
        </label>
        <label class="flex items-center gap-2 pb-2 text-sm text-gray-700">
          <input v-model="includeLayout" type="checkbox" />
          检查排版
        </label>
        <button class="btn-primary" :disabled="checkPending" @click="runCheck">
          <RefreshCw :size="16" :class="checkPending ? 'animate-spin' : ''" />
          运行校核
        </button>
        <button class="btn-secondary" @click="reload">按筛选重新读取</button>
      </div>
    </header>

    <div class="space-y-3 p-4">
      <p v-if="!rows.length" class="py-8 text-center text-sm text-gray-500">
        当前筛选条件下没有校核项。可先「运行校核」检查错填、漏填、数值变化、左右侧与否定词变化及排版异常（R-6）。
      </p>

      <article
        v-for="item in rows"
        v-else
        :key="item.id"
        class="rounded-lg border p-4"
        :class="item.status === 'open' && item.severity === 'blocking'
          ? 'border-red-300 bg-red-50/40'
          : 'border-gray-200 bg-white'"
      >
        <header class="flex flex-wrap items-start justify-between gap-2">
          <h4 class="flex flex-wrap items-center gap-2 text-sm font-semibold text-gray-950">
            <span class="chip" :class="severityChipClass(item.severity)">{{ severityLabel(item.severity) }}</span>
            <span class="chip chip-info">{{ categoryLabel(item.category) }}</span>
            <span class="chip" :class="statusChipClass(item.status)">{{ statusLabel(item.status) }}</span>
            <span>{{ item.title || item.code || '校核项' }}</span>
            <span class="text-xs font-normal text-gray-400">{{ item.code || '' }}</span>
          </h4>
          <span class="text-xs text-gray-500">{{ sourceText(item) }}</span>
        </header>

        <p class="mt-2 break-words text-sm text-gray-800">{{ item.detail || '未记录详情' }}</p>
        <p v-if="item.suggestion" class="mt-1 break-words text-sm text-blue-800">建议：{{ item.suggestion }}</p>
        <p
          v-if="item.status === 'open' && item.severity === 'blocking'"
          class="mt-2 flex items-center gap-2 text-xs font-medium text-red-800"
        >
          <CircleAlert :size="14" />
          该项未解决前不能生成待签发稿；可修正事实后重新校核，或填写理由后豁免。
        </p>
        <p v-if="item.status !== 'open'" class="mt-2 text-xs text-gray-600">
          处理人：{{ item.resolved_by || '未记录' }} · 时间：{{ item.resolved_at || '未记录' }}
          <template v-if="item.note"> · 说明：{{ item.note }}</template>
        </p>

        <div v-if="item.status === 'open'" class="mt-3 flex flex-wrap items-end gap-2">
          <label class="min-w-[16rem] flex-1">
            <span class="mb-1 block text-xs font-medium text-gray-500">处理说明（豁免时必填）</span>
            <input v-model="notes[item.id]" class="form-input" type="text" placeholder="例如：已按记录本原文更正为右侧" />
          </label>
          <button class="btn-primary px-3 py-1.5" :disabled="busyId === item.id" @click="update(item, 'resolved')">
            标记已解决
          </button>
          <button class="btn-secondary px-3 py-1.5" :disabled="busyId === item.id" @click="update(item, 'waived')">
            豁免（需理由）
          </button>
        </div>
      </article>
    </div>
  </section>
</template>
