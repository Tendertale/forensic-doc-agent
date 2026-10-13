<script setup>
/**
 * FieldReviewPanel.vue —— 字段核对（R-15 / R-44 / R-45 / N-1 / N-5）。
 *
 * 硬性要求：
 * 1. 每个字段**同屏**展示 R-15 的来源链：
 *    原文 → 来源文件 → 页码 → 原页位置（bbox）→ 识别结果 → 确认后的值 →
 *    规范化文字 → 使用的规则 → 复核状态；
 * 2. 空白字段显示「未记录」，**绝不**显示「未见异常」（N-5）；
 * 3. `unclear` 必须有醒目「待辨认/冲突」标记，并展示冲突双方来源，不静默选择一方（R-44）；
 * 4. `opinion` 栏目标注「由专业人员提供」，只手工输入，系统不生成（N-1）；
 * 5. 原图预览走 api.materialFileUrl(id, materialId, page, variant)（api.js 已封装）。
 */
import { computed, reactive, ref, watch } from 'vue'
import { CircleAlert, Eye, RefreshCw, Search, X } from 'lucide-vue-next'
import { api } from '../api.js'

const props = defineProps({
  caseId: { type: Number, default: 0 },
  caseVersion: { type: Number, default: 1 },
  fields: { type: Array, default: () => [] },
  materials: { type: Array, default: () => [] },
  dict: { type: Object, default: () => ({}) },
})

const emit = defineEmits(['refresh', 'notify', 'error'])

const STATUS_KEYS = ['recorded', 'not_recorded', 'not_examined', 'negative', 'unclear']
const FALLBACK_STATUS_LABELS = {
  recorded: '明确记载',
  not_recorded: '未记录',
  not_examined: '明确未检',
  negative: '明确阴性所见',
  unclear: '待辨认或有冲突',
}

const statusLabels = computed(() => {
  const raw = props.dict?.fieldStatusLabels
  return raw && Object.keys(raw).length ? raw : FALLBACK_STATUS_LABELS
})
const reviewLabels = computed(() => props.dict?.reviewStateLabels || { pending: '待核对', confirmed: '已确认' })
const statusKeys = computed(() => {
  const keys = Object.keys(statusLabels.value)
  return keys.length ? keys : STATUS_KEYS
})

const drafts = reactive({})
const busyKey = ref('')
const filterStatus = ref('')
const onlyPending = ref(false)
const keyword = ref('')
const preview = reactive({ open: false, materialId: 0, page: 1, filename: '' })

const materialNames = computed(() => {
  const map = {}
  ;(props.materials || []).forEach((item) => {
    map[item.id] = item.filename || `材料 #${item.id}`
  })
  return map
})

/** /api/config 的 field_definitions（含 required / critical / template_anchor）。 */
const definitionIndex = computed(() => {
  const map = {}
  ;(props.dict?.fieldDefinitions || []).forEach((item) => {
    if (item && item.key) map[item.key] = item
  })
  return map
})

const requiredKeys = computed(() => {
  const keys = props.dict?.requiredFieldKeys
  if (Array.isArray(keys) && keys.length) return new Set(keys.map(String))
  return new Set(
    (props.dict?.fieldDefinitions || []).filter((item) => item && item.required).map((item) => item.key),
  )
})

const criticalKeys = computed(() => {
  const keys = props.dict?.criticalFieldKeys
  if (Array.isArray(keys) && keys.length) return new Set(keys.map(String))
  return new Set(
    (props.dict?.fieldDefinitions || []).filter((item) => item && item.critical).map((item) => item.key),
  )
})

function isRequired(field) {
  return requiredKeys.value.has(field.field_key)
}

function isCritical(field) {
  return criticalKeys.value.has(field.field_key)
}

function anchorOf(field) {
  const definition = definitionIndex.value[field.field_key]
  return definition && definition.template_anchor ? String(definition.template_anchor) : ''
}

const visibleFields = computed(() => {
  const text = keyword.value.trim()
  return (props.fields || []).filter((field) => {
    if (filterStatus.value && field.status !== filterStatus.value) return false
    if (onlyPending.value && field.review_state === 'confirmed' && field.status !== 'unclear') return false
    if (text) {
      const haystack = [
        field.field_label,
        field.field_key,
        field.raw_text,
        field.confirmed_value,
        field.normalized_text,
      ]
        .filter(Boolean)
        .join(' ')
      if (!haystack.includes(text)) return false
    }
    return true
  })
})

const stats = computed(() => {
  const rows = props.fields || []
  return {
    total: rows.length,
    unclear: rows.filter((item) => item.status === 'unclear').length,
    pending: rows.filter((item) => item.review_state === 'pending').length,
  }
})

function draftFor(field) {
  const key = field.field_key
  if (!drafts[key]) {
    // 正常路径下由下方 watch 预填；这里是兜底，避免模板读取到 undefined。
    drafts[key] = {
      status: field.status || 'not_recorded',
      confirmed_value: field.confirmed_value ?? '',
      normalized_text: field.normalized_text ?? '',
      unit: field.unit ?? '',
      review_state: field.review_state || 'pending',
    }
  }
  return drafts[key]
}

/**
 * 在渲染前预填所有字段草稿（immediate + flush:'pre'），
 * 使模板中的 draftFor() 只读取、不在渲染期间写入响应式对象。
 */
watch(
  () => props.fields,
  (rows) => {
    Object.keys(drafts).forEach((key) => {
      delete drafts[key]
    })
    ;(rows || []).forEach((field) => {
      drafts[field.field_key] = {
        status: field.status || 'not_recorded',
        confirmed_value: field.confirmed_value ?? '',
        normalized_text: field.normalized_text ?? '',
        unit: field.unit ?? '',
        review_state: field.review_state || 'pending',
      }
    })
  },
  { immediate: true, flush: 'pre' },
)

function statusLabel(key) {
  if (!key) return '未记录'
  return statusLabels.value[key] || key
}

function reviewLabel(key) {
  if (!key) return '待核对'
  return reviewLabels.value[key] || key
}

function statusChipClass(key) {
  if (key === 'recorded') return 'chip-recorded'
  if (key === 'not_recorded') return 'chip-not-recorded'
  if (key === 'not_examined') return 'chip-not-examined'
  if (key === 'negative') return 'chip-negative'
  if (key === 'unclear') return 'chip-unclear'
  return 'chip-info'
}

/** 空白字段一律「未记录」；绝不显示「未见异常」（N-5）。 */
function orNotRecorded(value) {
  const text = value === null || value === undefined ? '' : String(value).trim()
  return text ? text : '未记录'
}

function isOpinionField(field) {
  return String(field.field_key) === 'opinion' || String(field.field_group) === 'opinion'
}

function sourceFileName(field) {
  if (!field.source_material_id) return '未记录'
  return materialNames.value[field.source_material_id] || `材料 #${field.source_material_id}`
}

function bboxText(bbox) {
  if (!Array.isArray(bbox) || bbox.length < 4) return '未记录'
  return bbox.map((value) => Number(value).toFixed(1)).join(', ')
}

/** 识别结果：优先 recognized_value / ocr_text，退回原文；均空则「未记录」。 */
function recognizeText(field) {
  const value = field.recognized_value ?? field.ocr_text ?? field.raw_text
  return orNotRecorded(value)
}

function confidenceText(field) {
  const value = Number(field.confidence)
  if (!Number.isFinite(value)) return '未记录'
  return value.toFixed(2)
}

function lowConfidence(field) {
  const value = Number(field.confidence)
  return Number.isFinite(value) && value < 0.75
}

/**
 * R-44：冲突双方来源。
 * 后端 conflict 元素形状（extractor.ExtractedField.conflict）：
 *   { material_id, page_number, block_index, block_id, value, confidence }
 * 这里逐项兼容，并把来源材料名 / 页码 / 识别块与置信度都展示出来。
 */
function conflicts(field) {
  const raw = field.conflict ?? field.conflicts
  if (!Array.isArray(raw)) return []
  return raw
    .map((item, index) => {
      if (typeof item === 'string') {
        return { key: `${field.field_key}-${index}`, source: '来源未标注', value: item, note: '' }
      }
      if (!item || typeof item !== 'object') return null
      const materialId = item.material_id ?? item.source_material_id
      const page = item.page_number ?? item.page ?? item.source_page
      const source =
        item.source ||
        item.source_file ||
        item.source_label ||
        item.filename ||
        (materialId ? materialNames.value[materialId] || `材料 #${materialId}` : '') ||
        '来源未标注'
      const notes = []
      if (page) notes.push(`第 ${page} 页`)
      if (item.block_index !== undefined && item.block_index !== null && item.block_index !== '') {
        notes.push(`第 ${item.block_index} 块`)
      }
      if (item.block_id !== undefined && item.block_id !== null && item.block_id !== '') {
        notes.push(`识别块 #${item.block_id}`)
      }
      const confidence = Number(item.confidence)
      if (Number.isFinite(confidence)) notes.push(`置信度 ${confidence.toFixed(2)}`)
      return {
        key: `${field.field_key}-${index}`,
        source: String(source) + (page && !String(source).includes('页') ? ` · 第 ${page} 页` : ''),
        value: orNotRecorded(item.value ?? item.text ?? item.confirmed_value ?? item.raw_text ?? ''),
        note: notes.join(' · '),
      }
    })
    .filter(Boolean)
}

function ruleText(field) {
  const codes = Array.isArray(field.applied_rule_codes) ? field.applied_rule_codes.filter(Boolean) : []
  if (codes.length) return codes.join('、')
  if (field.rule_note) return String(field.rule_note)
  return '未使用规则（未记录）'
}

function openPreview(field) {
  if (!field.source_material_id) {
    emit('notify', '该字段未记录来源材料，无法预览原图', 'error')
    return
  }
  preview.open = true
  preview.materialId = field.source_material_id
  preview.page = Number(field.source_page) || 1
  preview.filename = materialNames.value[field.source_material_id] || `材料 #${field.source_material_id}`
}

function closePreview() {
  preview.open = false
}

function previewUrl() {
  return api.materialFileUrl(props.caseId, preview.materialId, preview.page, 'original')
}

function confidenceOrder(field) {
  const value = Number(field.confidence)
  return Number.isFinite(value) ? value : 2
}

const sortedFields = computed(() =>
  visibleFields.value.slice().sort((a, b) => {
    const unclearDiff = (a.status === 'unclear' ? 0 : 1) - (b.status === 'unclear' ? 0 : 1)
    if (unclearDiff !== 0) return unclearDiff
    return confidenceOrder(a) - confidenceOrder(b)
  }),
)

async function save(field) {
  if (!props.caseId) return
  const draft = draftFor(field)
  busyKey.value = field.field_key
  try {
    await api.updateField(props.caseId, field.field_key, {
      expected_version: props.caseVersion,
      status: draft.status,
      confirmed_value: draft.confirmed_value,
      normalized_text: draft.normalized_text,
      unit: draft.unit,
      review_state: draft.review_state,
    })
    emit('notify', `字段已保存：${field.field_label || field.field_key}`, 'success')
    emit('refresh')
  } catch (error) {
    emit('error', error)
  } finally {
    busyKey.value = ''
  }
}

function markNotRecorded(field) {
  const draft = draftFor(field)
  draft.status = 'not_recorded'
  draft.normalized_text = '未记录'
  save(field)
}

function confirmField(field) {
  const draft = draftFor(field)
  draft.review_state = 'confirmed'
  save(field)
}

function useRecognized(field) {
  const draft = draftFor(field)
  draft.confirmed_value = String(field.recognized_value ?? field.ocr_text ?? field.raw_text ?? '')
  if (!draft.confirmed_value.trim()) {
    emit('notify', '该字段没有识别结果，请手工填写或标记为「未记录」', 'error')
  }
}
</script>

<template>
  <section class="panel" aria-label="字段核对">
    <header class="border-b border-gray-200 p-4">
      <h3 class="text-sm font-semibold text-gray-950">字段核对（R-15 来源链同屏展示）</h3>
      <p class="mt-1 text-xs text-gray-500">
        原文 → 来源文件 → 页码 → 原页位置（bbox）→ 识别结果 → 确认后的值 → 规范化文字 → 使用的规则 → 复核状态。
        空白字段显示「未记录」，绝不写成「未见异常」（N-5）；关键事实（身份、尺寸、单位、左右侧、部位、否定词）请逐项人工核对（R-45）。
      </p>

      <div class="mt-3 flex flex-wrap items-end gap-3">
        <label class="w-40">
          <span class="mb-1 block text-xs font-medium text-gray-500">信息状态</span>
          <select v-model="filterStatus" class="form-input">
            <option value="">全部状态</option>
            <option v-for="key in statusKeys" :key="key" :value="key">{{ statusLabel(key) }}</option>
          </select>
        </label>
        <label class="min-w-[12rem] flex-1">
          <span class="mb-1 block text-xs font-medium text-gray-500">关键字（字段名 / 原文 / 确认值）</span>
          <div class="relative">
            <Search :size="16" class="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
            <input v-model="keyword" class="form-input pl-9" type="search" placeholder="输入关键字筛选字段" />
          </div>
        </label>
        <label class="flex items-center gap-2 pb-2 text-sm text-gray-700">
          <input v-model="onlyPending" type="checkbox" />
          只看待核对
        </label>
        <div class="flex gap-2 pb-0.5">
          <span class="chip chip-unclear">待辨认/冲突 {{ stats.unclear }}</span>
          <span class="chip chip-warning">待核对 {{ stats.pending }}</span>
          <span class="chip chip-info">共 {{ stats.total }}</span>
        </div>
      </div>
    </header>

    <div class="space-y-3 p-4">
      <p v-if="!sortedFields.length" class="py-8 text-center text-sm text-gray-500">
        没有符合条件的字段。请先运行「提取字段」，或调整筛选条件。
      </p>

      <article
        v-for="field in sortedFields"
        v-else
        :key="field.field_key"
        class="rounded-lg border p-4"
        :class="field.status === 'unclear' ? 'border-amber-400 bg-amber-50/40' : 'border-gray-200 bg-white'"
      >
        <header class="flex flex-wrap items-start justify-between gap-2">
          <div class="min-w-0">
            <h4 class="flex flex-wrap items-center gap-2 text-sm font-semibold text-gray-950">
              <span>{{ field.field_label || field.field_key }}</span>
              <span class="text-xs font-normal text-gray-500">{{ field.field_key }} · {{ field.field_group || '未分组' }}</span>
              <span class="chip" :class="statusChipClass(field.status)">{{ statusLabel(field.status) }}</span>
              <span class="chip" :class="field.review_state === 'confirmed' ? 'chip-recorded' : 'chip-warning'">
                {{ reviewLabel(field.review_state) }}
              </span>
              <span v-if="field.status === 'unclear'" class="chip chip-blocking">
                <CircleAlert :size="13" />
                待辨认/冲突，必须人工处理
              </span>
              <span v-if="isOpinionField(field)" class="chip chip-info">由专业人员提供（只手工输入，N-1）</span>
              <span v-if="isRequired(field)" class="chip chip-warning">必填</span>
              <span v-if="isCritical(field)" class="chip chip-blocking">关键事实：逐项人工核对（R-45）</span>
              <span v-if="anchorOf(field)" class="text-xs font-normal text-gray-400">模板锚点 {{ anchorOf(field) }}</span>
              <span v-if="lowConfidence(field)" class="chip chip-warning">低置信度 {{ confidenceText(field) }}</span>
            </h4>
          </div>
          <button class="btn-secondary px-3 py-1.5" @click="openPreview(field)">
            <Eye :size="15" />
            查看原图
          </button>
        </header>

        <dl class="mt-3 grid gap-3 text-sm sm:grid-cols-2 xl:grid-cols-3">
          <div class="rounded-lg border border-gray-200 bg-white px-3 py-2">
            <dt class="text-xs text-gray-500">① 原文</dt>
            <dd class="mt-0.5 break-words text-gray-900">{{ orNotRecorded(field.raw_text) }}</dd>
          </div>
          <div class="rounded-lg border border-gray-200 bg-white px-3 py-2">
            <dt class="text-xs text-gray-500">② 来源文件</dt>
            <dd class="mt-0.5 break-words text-gray-900">{{ sourceFileName(field) }}</dd>
          </div>
          <div class="rounded-lg border border-gray-200 bg-white px-3 py-2">
            <dt class="text-xs text-gray-500">③ 页码</dt>
            <dd class="mt-0.5 text-gray-900">
              {{ field.source_page ? `第 ${field.source_page} 页` : '未记录' }}
            </dd>
          </div>
          <div class="rounded-lg border border-gray-200 bg-white px-3 py-2">
            <dt class="text-xs text-gray-500">④ 原页位置（bbox）</dt>
            <dd class="mt-0.5 break-words font-mono text-xs text-gray-900">{{ bboxText(field.source_bbox) }}</dd>
          </div>
          <div class="rounded-lg border border-gray-200 bg-white px-3 py-2">
            <dt class="text-xs text-gray-500">⑤ 识别结果</dt>
            <dd class="mt-0.5 break-words text-gray-900">{{ recognizeText(field) }}</dd>
            <dd class="mt-1 text-xs text-gray-500">置信度 {{ confidenceText(field) }}（仅用于安排核对优先级）</dd>
          </div>
          <div class="rounded-lg border border-blue-200 bg-blue-50/40 px-3 py-2">
            <dt class="text-xs text-gray-500">⑥ 确认后的值（人工）</dt>
            <dd class="mt-1">
              <textarea
                v-model="draftFor(field).confirmed_value"
                class="form-input"
                rows="2"
                :placeholder="isOpinionField(field) ? '由专业人员提供，系统不生成' : '未记录时留空'"
              />
            </dd>
            <dd class="mt-1 flex flex-wrap items-center gap-2">
              <label class="flex items-center gap-1 text-xs text-gray-600">
                <span>单位</span>
                <input v-model="draftFor(field).unit" class="form-input w-20 py-1" type="text" placeholder="cm" />
              </label>
              <button
                v-if="!isOpinionField(field)"
                class="cursor-pointer text-xs text-blue-700 underline"
                @click="useRecognized(field)"
              >
                用识别结果填充
              </button>
            </dd>
          </div>
          <div class="rounded-lg border border-gray-200 bg-white px-3 py-2">
            <dt class="text-xs text-gray-500">⑦ 规范化文字</dt>
            <dd class="mt-1">
              <textarea
                v-model="draftFor(field).normalized_text"
                class="form-input"
                rows="2"
                placeholder="未记录时由系统写「未记录」，不写「未见异常」"
              />
            </dd>
          </div>
          <div class="rounded-lg border border-gray-200 bg-white px-3 py-2">
            <dt class="text-xs text-gray-500">⑧ 使用的规则</dt>
            <dd class="mt-0.5 break-words text-gray-900">{{ ruleText(field) }}</dd>
            <dd v-if="field.rule_note" class="mt-1 text-xs text-gray-500">{{ field.rule_note }}</dd>
          </div>
          <div class="rounded-lg border border-gray-200 bg-white px-3 py-2">
            <dt class="text-xs text-gray-500">⑨ 复核状态</dt>
            <dd class="mt-1 space-y-2">
              <select v-model="draftFor(field).status" class="form-input py-1.5">
                <option v-for="key in statusKeys" :key="key" :value="key">{{ statusLabel(key) }}</option>
              </select>
              <select v-model="draftFor(field).review_state" class="form-input py-1.5">
                <option value="pending">{{ reviewLabel('pending') }}</option>
                <option value="confirmed">{{ reviewLabel('confirmed') }}</option>
              </select>
            </dd>
          </div>
        </dl>

        <div
          v-if="conflicts(field).length"
          class="mt-3 rounded-lg border border-amber-300 bg-amber-50 p-3"
        >
          <p class="flex items-center gap-2 text-sm font-semibold text-amber-900">
            <CircleAlert :size="16" />
            来源冲突：必须显式处理，不能静默选择其中一方（R-44）
          </p>
          <ul class="mt-2 space-y-1 text-sm text-amber-900">
            <li v-for="item in conflicts(field)" :key="item.key" class="flex flex-wrap gap-2">
              <span class="font-medium">{{ item.source }}</span>
              <span class="min-w-0 flex-1 break-words">{{ item.value }}</span>
              <span v-if="item.note" class="text-xs text-amber-800">{{ item.note }}</span>
            </li>
          </ul>
          <p class="mt-2 text-xs text-amber-800">
            请在下方「确认后的值」写明依据哪一方来源，并在备注或校核清单中记录处理结果。
          </p>
        </div>
        <p
          v-else-if="field.status === 'unclear'"
          class="mt-3 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900"
        >
          该字段被标记为「待辨认或有冲突」，但接口未返回冲突明细；请对照原图人工辨认后在确认值中写明，不要猜测（R-21）。
        </p>

        <div class="mt-3 flex flex-wrap items-center gap-2">
          <button class="btn-primary px-3 py-1.5" :disabled="busyKey === field.field_key" @click="save(field)">
            <RefreshCw v-if="busyKey === field.field_key" :size="15" class="animate-spin" />
            保存核对
          </button>
          <button class="btn-secondary px-3 py-1.5" :disabled="busyKey === field.field_key" @click="markNotRecorded(field)">
            标记为未记录
          </button>
          <button class="btn-secondary px-3 py-1.5" :disabled="busyKey === field.field_key" @click="confirmField(field)">
            确认该项
          </button>
          <span class="text-xs text-gray-500">
            保存会把字段写回案件并让依赖该字段的旧文稿复核状态失效（R-40）。
          </span>
        </div>
      </article>
    </div>

    <Transition name="fade">
      <div
        v-if="preview.open"
        class="fixed inset-0 z-[65] flex items-start justify-center overflow-y-auto bg-gray-950/40 p-4 sm:p-8"
        @click.self="closePreview"
      >
        <div class="panel w-full max-w-3xl" role="dialog" aria-modal="true" aria-label="字段原图">
          <header class="flex items-start justify-between gap-3 border-b border-gray-200 p-4">
            <div class="min-w-0">
              <h4 class="truncate text-sm font-semibold text-gray-950">原图对照：{{ preview.filename }}</h4>
              <p class="mt-0.5 text-xs text-gray-500">
                图片取自 api.materialFileUrl({{ caseId }}, {{ preview.materialId }}, {{ preview.page }}, 'original')（原件只读，N-8）
              </p>
            </div>
            <button class="cursor-pointer rounded-lg p-2 text-gray-500 hover:bg-gray-100" aria-label="关闭" @click="closePreview">
              <X :size="18" />
            </button>
          </header>
          <div class="p-4">
            <label class="mb-2 flex items-center gap-2 text-xs text-gray-500">
              <span>页码</span>
              <input v-model.number="preview.page" class="form-input w-20 py-1.5" type="number" min="1" />
            </label>
            <div class="overflow-hidden rounded-lg border border-gray-200 bg-gray-50">
              <img :src="previewUrl()" :alt="`${preview.filename} 第 ${preview.page} 页`" class="mx-auto max-h-[70vh] w-auto" />
            </div>
          </div>
        </div>
      </div>
    </Transition>
  </section>
</template>
