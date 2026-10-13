<script setup>
/**
 * InjuryPanel.vue —— 部位与损伤：逐条编辑与排序（R-3 / R-16 / R-36 / R-37）。
 *
 * 端点：
 *   api.injuries(id)                          GET    /api/cases/{id}/injuries
 *   api.createInjury(id, payload)             POST   /api/cases/{id}/injuries
 *   api.updateInjury(id, injuryId, payload)   PATCH  /api/cases/{id}/injuries/{injury_id}
 *   api.deleteInjury(id, injuryId, expectedVersion)  DELETE ?expected_version=N
 *   api.reorderInjuries(id, orderedIds, expectedVersion)  POST  /api/cases/{id}/injuries/reorder
 *
 * 损伤逐条保存，不合并；保留原有顺序与对应关系（R-3 / R-16）；
 * 不把不同记录行的部位与尺寸错误组合（R-36）；多义简写交由用户确认（R-37）。
 */
import { computed, reactive, ref, watch } from 'vue'
import { CircleAlert, Plus, RefreshCw, Trash2 } from 'lucide-vue-next'
import { api } from '../api.js'

const props = defineProps({
  caseId: { type: Number, default: 0 },
  caseVersion: { type: Number, default: 1 },
  injuries: { type: Array, default: () => [] },
  materials: { type: Array, default: () => [] },
  dict: { type: Object, default: () => ({}) },
})

const emit = defineEmits(['refresh', 'notify', 'error'])

const FIELDS = [
  { key: 'body_part', label: '部位', type: 'text' },
  { key: 'side', label: '左右侧', type: 'side' },
  { key: 'injury_type', label: '类型', type: 'text' },
  { key: 'count', label: '数量', type: 'text' },
  { key: 'length', label: '长度', type: 'text' },
  { key: 'width', label: '宽度', type: 'text' },
  { key: 'size_unit', label: '单位', type: 'text' },
  { key: 'morphology', label: '形态', type: 'text' },
  { key: 'other_traits', label: '其他已记录特征', type: 'text' },
]
const SIDE_VALUES = ['左', '右', '双侧', '正中', '']
const STATUS_KEYS = ['recorded', 'not_recorded', 'not_examined', 'negative', 'unclear']

const drafts = reactive({})
const busyId = ref(0)
const createOpen = ref(false)
const createBusy = ref(false)
const createError = ref('')
const newInjury = reactive({
  body_part: '',
  side: '',
  injury_type: '',
  count: '',
  length: '',
  width: '',
  size_unit: 'cm',
  morphology: '',
  other_traits: '',
  raw_text: '',
})

const statusLabels = computed(() => {
  const raw = props.dict?.fieldStatusLabels
  return raw && Object.keys(raw).length ? raw : { recorded: '明确记载', not_recorded: '未记录', not_examined: '明确未检', negative: '明确阴性所见', unclear: '待辨认或有冲突' }
})
const reviewLabels = computed(() => props.dict?.reviewStateLabels || { pending: '待核对', confirmed: '已确认' })
const statusKeys = computed(() => {
  const keys = Object.keys(statusLabels.value)
  return keys.length ? keys : STATUS_KEYS
})

const rows = computed(() => {
  const list = (props.injuries || []).slice()
  list.sort((a, b) => {
    const seqA = Number(a.seq || 0)
    const seqB = Number(b.seq || 0)
    if (seqA !== seqB) return seqA - seqB
    return Number(a.id || 0) - Number(b.id || 0)
  })
  return list
})

const unclearCount = computed(() => rows.value.filter((item) => item.status === 'unclear').length)

const materialNames = computed(() => {
  const map = {}
  ;(props.materials || []).forEach((item) => {
    map[item.id] = item.filename || `材料 #${item.id}`
  })
  return map
})

function statusLabel(key) {
  return statusLabels.value[key] || key || '未记录'
}

function reviewLabel(key) {
  return reviewLabels.value[key] || key || '待核对'
}

function statusChipClass(key) {
  if (key === 'recorded') return 'chip-recorded'
  if (key === 'not_recorded') return 'chip-not-recorded'
  if (key === 'not_examined') return 'chip-not-examined'
  if (key === 'negative') return 'chip-negative'
  if (key === 'unclear') return 'chip-unclear'
  return 'chip-info'
}

function orNotRecorded(value) {
  const text = value === null || value === undefined ? '' : String(value).trim()
  return text ? text : '未记录'
}

function toDraft(injury) {
  return {
    body_part: injury.body_part ?? '',
    side: injury.side ?? '',
    injury_type: injury.injury_type ?? '',
    count: injury.count ?? '',
    length: injury.length ?? '',
    width: injury.width ?? '',
    size_unit: injury.size_unit ?? '',
    morphology: injury.morphology ?? '',
    other_traits: injury.other_traits ?? '',
    raw_text: injury.raw_text ?? '',
    status: injury.status || 'recorded',
    review_state: injury.review_state || 'pending',
  }
}

function draftFor(injury) {
  const key = injury.id
  if (!drafts[key]) {
    // 正常路径下由下方 watch 预填；这里是兜底，避免模板读取到 undefined。
    drafts[key] = toDraft(injury)
  }
  return drafts[key]
}

/** 渲染前预填草稿，保证 draftFor() 在渲染期间只读取、不写入。 */
watch(
  () => props.injuries,
  (rows) => {
    Object.keys(drafts).forEach((key) => {
      delete drafts[key]
    })
    ;(rows || []).forEach((injury) => {
      drafts[injury.id] = toDraft(injury)
    })
  },
  { immediate: true, flush: 'pre' },
)

function sizeText(injury) {
  const parts = []
  if (injury.length) parts.push(String(injury.length))
  if (injury.width) parts.push(String(injury.width))
  if (!parts.length) return '未记录'
  return `${parts.join(' × ')} ${injury.size_unit || ''}`.trim()
}

function bboxText(bbox) {
  if (!Array.isArray(bbox) || bbox.length < 4) return '未记录'
  return bbox.map((value) => Number(value).toFixed(1)).join(', ')
}

async function save(injury) {
  if (!props.caseId) return
  const draft = draftFor(injury)
  busyId.value = injury.id
  try {
    await api.updateInjury(props.caseId, injury.id, {
      expected_version: props.caseVersion,
      ...draft,
    })
    emit('notify', `损伤第 ${injury.seq || injury.id} 条已保存`, 'success')
    emit('refresh')
  } catch (error) {
    emit('error', error)
  } finally {
    busyId.value = 0
  }
}

async function remove(injury) {
  if (!props.caseId) return
  if (!window.confirm(`确认删除损伤第 ${injury.seq || injury.id} 条？删除后需重新校核。`)) return
  busyId.value = injury.id
  try {
    await api.deleteInjury(props.caseId, injury.id, props.caseVersion)
    emit('notify', '损伤条目已删除', 'success')
    emit('refresh')
  } catch (error) {
    emit('error', error)
  } finally {
    busyId.value = 0
  }
}

/** 顺序调整：一次提交完整 id 列表，保持记录对应关系（R-7 / R-36）。 */
async function move(injury, direction) {
  if (!props.caseId) return
  const list = rows.value.map((item) => Number(item.id))
  const index = list.indexOf(Number(injury.id))
  const target = index + direction
  if (index < 0 || target < 0 || target >= list.length) return
  const next = list.slice()
  const moved = next.splice(index, 1)[0]
  next.splice(target, 0, moved)
  busyId.value = injury.id
  try {
    await api.reorderInjuries(props.caseId, next, props.caseVersion)
    emit('notify', '损伤顺序已调整（保留原有记录对应关系）', 'success')
    emit('refresh')
  } catch (error) {
    emit('error', error)
  } finally {
    busyId.value = 0
  }
}

function openCreate() {
  createOpen.value = true
  createError.value = ''
}

async function submitCreate() {
  if (!props.caseId) return
  const filled = Object.entries(newInjury).some(([key, value]) => key !== 'size_unit' && String(value).trim())
  if (!filled) {
    createError.value = '至少填写一个损伤字段；不得凭推测补写部位、数量、尺寸或形态（N-2～N-4）。'
    return
  }
  createBusy.value = true
  createError.value = ''
  try {
    await api.createInjury(props.caseId, {
      expected_version: props.caseVersion,
      seq: rows.value.length ? Number(rows.value[rows.value.length - 1].seq || rows.value.length) + 1 : 1,
      ...newInjury,
      status: 'recorded',
      review_state: 'pending',
    })
    emit('notify', '已新增一条损伤（逐条保存，不合并）', 'success')
    Object.keys(newInjury).forEach((key) => {
      if (key === 'size_unit') return
      newInjury[key] = ''
    })
    createOpen.value = false
    emit('refresh')
  } catch (error) {
    createError.value = error && error.message ? error.message : '新增损伤失败'
    emit('error', error)
  } finally {
    createBusy.value = false
  }
}
</script>

<template>
  <section class="panel" aria-label="损伤条目">
    <header class="flex flex-wrap items-center justify-between gap-3 border-b border-gray-200 p-4">
      <div class="min-w-0">
        <h3 class="text-sm font-semibold text-gray-950">部位与损伤（逐条编辑与排序）</h3>
        <p class="mt-1 text-xs text-gray-500">
          损伤逐条保存、不合并（R-16）；保留原有顺序和对应关系（R-3）；不得把不同记录行的部位与尺寸错误组合（R-36）。
        </p>
      </div>
      <div class="flex flex-wrap items-center gap-2">
        <span class="chip chip-info">共 {{ rows.length }} 条</span>
        <span v-if="unclearCount" class="chip chip-unclear">待辨认/冲突 {{ unclearCount }}</span>
        <button class="btn-primary" @click="openCreate">
          <Plus :size="16" />
          新增损伤
        </button>
      </div>
    </header>

    <div v-if="createOpen" class="border-b border-gray-200 bg-gray-50 p-4">
      <h4 class="text-sm font-semibold text-gray-900">新增损伤（只填写原文实际记载的内容）</h4>
      <p v-if="createError" class="mt-2 flex items-start gap-2 rounded-lg border border-red-300 bg-red-50 px-3 py-2 text-xs text-red-800">
        <CircleAlert :size="15" class="mt-0.5 shrink-0" />
        <span class="min-w-0 flex-1">{{ createError }}</span>
      </p>
      <div class="mt-3 grid gap-3 sm:grid-cols-3 xl:grid-cols-5">
        <label v-for="item in FIELDS" :key="item.key" class="block">
          <span class="mb-1 block text-xs font-medium text-gray-500">{{ item.label }}</span>
          <select v-if="item.type === 'side'" v-model="newInjury.side" class="form-input">
            <option v-for="value in SIDE_VALUES" :key="value || 'empty'" :value="value">
              {{ value || '未记录' }}
            </option>
          </select>
          <input v-else v-model="newInjury[item.key]" class="form-input" type="text" placeholder="未记录时留空" />
        </label>
        <label class="block sm:col-span-3 xl:col-span-5">
          <span class="mb-1 block text-xs font-medium text-gray-500">原文（保留原样，不改左右与否定词）</span>
          <textarea v-model="newInjury.raw_text" class="form-input" rows="2" />
        </label>
      </div>
      <div class="mt-3 flex gap-2">
        <button class="btn-primary" :disabled="createBusy" @click="submitCreate">
          <RefreshCw v-if="createBusy" :size="16" class="animate-spin" />
          保存新条目
        </button>
        <button class="btn-secondary" :disabled="createBusy" @click="createOpen = false">取消</button>
      </div>
    </div>

    <div class="space-y-3 p-4">
      <p v-if="!rows.length" class="py-8 text-center text-sm text-gray-500">
        暂无损伤条目（未记录）。损伤需逐条登记，不能只保存一段合并后的文字。
      </p>

      <article
        v-for="injury in rows"
        v-else
        :key="injury.id"
        class="rounded-lg border p-4"
        :class="injury.status === 'unclear' ? 'border-amber-400 bg-amber-50/40' : 'border-gray-200 bg-white'"
      >
        <header class="flex flex-wrap items-start justify-between gap-2">
          <h4 class="flex flex-wrap items-center gap-2 text-sm font-semibold text-gray-950">
            <span>第 {{ injury.seq || injury.id }} 条</span>
            <span class="chip" :class="statusChipClass(injury.status)">{{ statusLabel(injury.status) }}</span>
            <span class="chip" :class="injury.review_state === 'confirmed' ? 'chip-recorded' : 'chip-warning'">
              {{ reviewLabel(injury.review_state) }}
            </span>
            <span v-if="injury.status === 'unclear'" class="chip chip-blocking">
              <CircleAlert :size="13" />
              待辨认/冲突
            </span>
            <span class="text-xs font-normal text-gray-500">
              尺寸：{{ sizeText(injury) }}
              <template v-if="injury.confidence !== null && injury.confidence !== undefined">
                · 置信度 {{ Number(injury.confidence).toFixed(2) }}
              </template>
            </span>
          </h4>
          <div class="flex flex-wrap items-center gap-2">
            <button class="btn-secondary px-2 py-1.5" :disabled="busyId === injury.id" title="上移" @click="move(injury, -1)">↑</button>
            <button class="btn-secondary px-2 py-1.5" :disabled="busyId === injury.id" title="下移" @click="move(injury, 1)">↓</button>
            <button class="btn-danger px-2 py-1.5" :disabled="busyId === injury.id" @click="remove(injury)">
              <Trash2 :size="15" />
            </button>
          </div>
        </header>

        <div class="mt-3 grid gap-3 sm:grid-cols-3 xl:grid-cols-5">
          <label v-for="item in FIELDS" :key="item.key" class="block">
            <span class="mb-1 block text-xs font-medium text-gray-500">{{ item.label }}</span>
            <select v-if="item.type === 'side'" v-model="draftFor(injury).side" class="form-input">
              <option v-for="value in SIDE_VALUES" :key="value || 'empty'" :value="value">
                {{ value || '未记录' }}
              </option>
            </select>
            <input v-else v-model="draftFor(injury)[item.key]" class="form-input" type="text" />
          </label>
          <label class="block sm:col-span-3 xl:col-span-5">
            <span class="mb-1 block text-xs font-medium text-gray-500">原文</span>
            <textarea v-model="draftFor(injury).raw_text" class="form-input" rows="2" />
          </label>
          <label class="block">
            <span class="mb-1 block text-xs font-medium text-gray-500">信息状态</span>
            <select v-model="draftFor(injury).status" class="form-input">
              <option v-for="key in statusKeys" :key="key" :value="key">{{ statusLabel(key) }}</option>
            </select>
          </label>
          <label class="block">
            <span class="mb-1 block text-xs font-medium text-gray-500">复核状态</span>
            <select v-model="draftFor(injury).review_state" class="form-input">
              <option value="pending">{{ reviewLabel('pending') }}</option>
              <option value="confirmed">{{ reviewLabel('confirmed') }}</option>
            </select>
          </label>
        </div>

        <dl class="mt-3 grid gap-2 text-xs text-gray-600 sm:grid-cols-2 xl:grid-cols-4">
          <div>
            <dt class="text-gray-500">来源文件</dt>
            <dd class="mt-0.5 text-gray-800">
              {{ injury.source_material_id ? (materialNames[injury.source_material_id] || `材料 #${injury.source_material_id}`) : '未记录' }}
            </dd>
          </div>
          <div>
            <dt class="text-gray-500">页码</dt>
            <dd class="mt-0.5 text-gray-800">{{ injury.source_page ? `第 ${injury.source_page} 页` : '未记录' }}</dd>
          </div>
          <div>
            <dt class="text-gray-500">原页位置（bbox）</dt>
            <dd class="mt-0.5 break-words font-mono text-gray-800">{{ bboxText(injury.source_bbox) }}</dd>
          </div>
          <div>
            <dt class="text-gray-500">使用的规则</dt>
            <dd class="mt-0.5 break-words text-gray-800">
              {{ (Array.isArray(injury.applied_rule_codes) && injury.applied_rule_codes.length) ? injury.applied_rule_codes.join('、') : (injury.rule_note || '未使用规则（未记录）') }}
            </dd>
          </div>
        </dl>

        <div class="mt-3 flex flex-wrap items-center gap-2">
          <button class="btn-primary px-3 py-1.5" :disabled="busyId === injury.id" @click="save(injury)">
            <RefreshCw v-if="busyId === injury.id" :size="15" class="animate-spin" />
            保存该条
          </button>
          <span class="text-xs text-gray-500">
            只改本条的已记录内容；不得补写原文没有的数量、深度、形态或颜色（N-2～N-4）。
          </span>
        </div>
      </article>
    </div>
  </section>
</template>
