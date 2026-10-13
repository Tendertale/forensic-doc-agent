<script setup>
/**
 * CorrectionModal.vue —— 四类修正录入（需求第七章 / R-38 / R-39 / R-40）。
 *
 * 请求体对应 schemas.CorrectionCreate：
 *   expected_version, kind, target_type, target_id, before_text, after_text,
 *   reason, confirmed_by, scope, scope_value, apply_to_case
 *
 * 四类：识别纠正（recognition）、术语纠正（terminology）、格式纠正（format）、
 * 工作顺序调整（workflow_order）。每次修正都保存修改前后内容、原因、确认人、
 * 适用范围与受影响文稿（R-38）；默认只作用于当前案件（R-39）。
 */
import { computed, reactive, ref, watch } from 'vue'
import { CircleAlert, RefreshCw, X } from 'lucide-vue-next'
import { api } from '../api.js'

const props = defineProps({
  open: { type: Boolean, default: false },
  caseId: { type: Number, default: 0 },
  caseVersion: { type: Number, default: 1 },
  fields: { type: Array, default: () => [] },
  injuries: { type: Array, default: () => [] },
  documents: { type: Array, default: () => [] },
  dict: { type: Object, default: () => ({}) },
})

const emit = defineEmits(['close', 'done', 'notify', 'error'])

const KIND_HINTS = {
  recognition: '例如：「这个字是右，不是左」→ 修改事实字段，并更新依赖该字段的段落与文稿。',
  terminology: '例如：「这里的退指指压褪色」→ 确认适用范围后更新对应转换结果。',
  format: '例如：「尺寸按我们样例里的形式写」→ 更新排版或表达规则，事实数值不变。',
  workflow_order: '例如：「先整理损伤，再整理一般尸表所见」→ 调整审阅或输出顺序，保持记录对应关系。',
}

const kindLabels = computed(() => {
  const raw = props.dict?.correctionKindLabels
  return raw && Object.keys(raw).length
    ? raw
    : { recognition: '识别纠正', terminology: '术语纠正', format: '格式纠正', workflow_order: '工作顺序调整' }
})
const scopeLabels = computed(() => {
  const raw = props.dict?.correctionScopeLabels
  return raw && Object.keys(raw).length
    ? raw
    : { case_only: '仅当前案件', field_key: '适用栏目', column: '适用记录栏目', writer: '适用书写者', template: '适用模板' }
})

const TARGET_LABELS = {
  field: '事实字段',
  injury: '损伤条目',
  terminology_rule: '术语规则',
  document: '文稿',
  case: '案件',
}

const KIND_TARGETS = {
  recognition: ['field', 'injury', 'case'],
  terminology: ['field', 'injury', 'terminology_rule'],
  format: ['document', 'case'],
  workflow_order: ['document', 'case'],
}

const form = reactive({
  kind: 'recognition',
  target_type: 'field',
  target_id: 0,
  before_text: '',
  after_text: '',
  reason: '',
  confirmed_by: '复核人',
  scope: 'case_only',
  scope_value: '',
  apply_to_case: true,
})

const busy = ref(false)
const localError = ref('')

const targetOptions = computed(() => {
  if (form.target_type === 'field') {
    return (props.fields || []).map((item) => ({
      id: Number(item.id ?? 0),
      label: `${item.field_label || item.field_key}（${item.field_key}）`,
      before: item.confirmed_value || item.raw_text || '',
    }))
  }
  if (form.target_type === 'injury') {
    return (props.injuries || []).map((item) => ({
      id: Number(item.id ?? 0),
      label: `第 ${item.seq || item.id} 条 · ${item.body_part || '部位未记录'}`,
      before: item.raw_text || item.morphology || '',
    }))
  }
  if (form.target_type === 'document') {
    return (props.documents || []).map((item) => ({
      id: Number(item.id ?? 0),
      label: `v${item.version_no ?? '?'} · ${item.doc_type || 'report'} · ${item.doc_status || ''}`,
      before: item.note || item.blocked_reason || '',
    }))
  }
  return []
})

const allowedTargets = computed(() => KIND_TARGETS[form.kind] || ['field', 'injury', 'terminology_rule', 'document', 'case'])

const needsAfterText = computed(() => ['recognition', 'terminology', 'format'].includes(form.kind))

watch(
  () => props.open,
  (value) => {
    if (!value) return
    localError.value = ''
    if (!allowedTargets.value.includes(form.target_type)) {
      form.target_type = allowedTargets.value[0]
      form.target_id = 0
    }
  },
)

watch(
  () => form.kind,
  () => {
    if (!allowedTargets.value.includes(form.target_type)) {
      form.target_type = allowedTargets.value[0]
      form.target_id = 0
      form.before_text = ''
    }
  },
)

function onTargetChange(event) {
  const id = Number(event.target.value) || 0
  form.target_id = id
  const hit = targetOptions.value.find((item) => item.id === id)
  if (hit && !form.before_text.trim()) form.before_text = String(hit.before || '')
}

function close() {
  if (busy.value) return
  emit('close')
}

async function submit() {
  if (!props.caseId) return
  localError.value = ''
  if (form.before_text === form.after_text) {
    localError.value = '修正前后的内容不能相同。'
    return
  }
  if (needsAfterText.value && !form.after_text.trim()) {
    localError.value = '该修正类型必须给出修正后的内容。'
    return
  }
  if (!form.reason.trim()) {
    localError.value = '必须填写修改原因（R-38）。'
    return
  }
  if (form.scope !== 'case_only' && !form.scope_value.trim()) {
    localError.value = '非「仅当前案件」范围必须填写适用范围（栏目 / 书写者 / 模板）。'
    return
  }
  busy.value = true
  try {
    await api.createCorrection(props.caseId, {
      expected_version: props.caseVersion,
      kind: form.kind,
      target_type: form.target_type,
      target_id: Number(form.target_id) || 0,
      before_text: form.before_text,
      after_text: form.after_text,
      reason: form.reason.trim(),
      confirmed_by: form.confirmed_by.trim() || '复核人',
      scope: form.scope,
      scope_value: form.scope === 'case_only' ? '' : form.scope_value.trim(),
      apply_to_case: !!form.apply_to_case,
    })
    emit('notify', '修正已记录并应用；相关字段的旧复核状态已失效，需重新核对（R-40）', 'success')
    form.before_text = ''
    form.after_text = ''
    form.reason = ''
    form.scope_value = ''
    form.target_id = 0
    emit('done')
    emit('close')
  } catch (error) {
    localError.value = error && error.message ? error.message : '修正提交失败'
    emit('error', error)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <Transition name="fade">
    <div
      v-if="open"
      class="fixed inset-0 z-[75] flex items-start justify-center overflow-y-auto bg-gray-950/40 p-4 sm:p-8"
      @click.self="close"
    >
      <div class="panel w-full max-w-2xl" role="dialog" aria-modal="true" aria-label="录入修正">
        <header class="flex items-start justify-between gap-3 border-b border-gray-200 p-5">
          <div>
            <h2 class="text-base font-semibold text-gray-950">录入修正（四类）</h2>
            <p class="mt-1 text-xs text-gray-500">
              默认先将纠正应用于当前案件；拟用于以后案件的术语规则才需要填写适用范围（R-39）。
            </p>
          </div>
          <button class="cursor-pointer rounded-lg p-2 text-gray-500 hover:bg-gray-100" aria-label="关闭" @click="close">
            <X :size="18" />
          </button>
        </header>

        <div class="space-y-4 p-5">
          <p
            v-if="localError"
            class="flex items-start gap-2 rounded-lg border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-800"
          >
            <CircleAlert :size="17" class="mt-0.5 shrink-0" />
            <span class="min-w-0 flex-1">{{ localError }}</span>
          </p>

          <div class="grid gap-4 sm:grid-cols-2">
            <label class="block">
              <span class="mb-1 block text-xs font-medium text-gray-500">修正类型</span>
              <select v-model="form.kind" class="form-input">
                <option v-for="(label, key) in kindLabels" :key="key" :value="key">{{ label }}</option>
              </select>
            </label>
            <label class="block">
              <span class="mb-1 block text-xs font-medium text-gray-500">修正对象</span>
              <select v-model="form.target_type" class="form-input">
                <option v-for="key in allowedTargets" :key="key" :value="key">{{ TARGET_LABELS[key] || key }}</option>
              </select>
            </label>
          </div>

          <p class="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-xs text-gray-600">
            {{ KIND_HINTS[form.kind] || '' }}
          </p>

          <div v-if="targetOptions.length" class="grid gap-3 sm:grid-cols-2">
            <label class="block">
              <span class="mb-1 block text-xs font-medium text-gray-500">选择具体对象（自动带出修改前内容）</span>
              <select class="form-input" :value="form.target_id" @change="onTargetChange">
                <option :value="0">请选择</option>
                <option v-for="option in targetOptions" :key="option.id" :value="option.id">{{ option.label }}</option>
              </select>
            </label>
            <label class="block">
              <span class="mb-1 block text-xs font-medium text-gray-500">对象 ID（可手工填写）</span>
              <input v-model.number="form.target_id" class="form-input" type="number" min="0" />
            </label>
          </div>
          <label v-else class="block">
            <span class="mb-1 block text-xs font-medium text-gray-500">对象 ID（案件级填 0）</span>
            <input v-model.number="form.target_id" class="form-input" type="number" min="0" />
          </label>

          <div class="grid gap-4 sm:grid-cols-2">
            <label class="block">
              <span class="mb-1 block text-xs font-medium text-gray-500">修改前内容</span>
              <textarea v-model="form.before_text" class="form-input" rows="3" />
            </label>
            <label class="block">
              <span class="mb-1 block text-xs font-medium text-gray-500">
                修改后内容<template v-if="needsAfterText">（必填）</template>
              </span>
              <textarea v-model="form.after_text" class="form-input" rows="3" />
            </label>
          </div>

          <label class="block">
            <span class="mb-1 block text-xs font-medium text-gray-500">修改原因（必填，R-38）</span>
            <input v-model="form.reason" class="form-input" type="text" placeholder="例如：对照记录本第 2 页原字为「右」" />
          </label>

          <div class="grid gap-4 sm:grid-cols-2">
            <label class="block">
              <span class="mb-1 block text-xs font-medium text-gray-500">适用范围</span>
              <select v-model="form.scope" class="form-input">
                <option v-for="(label, key) in scopeLabels" :key="key" :value="key">{{ label }}</option>
              </select>
            </label>
            <label class="block">
              <span class="mb-1 block text-xs font-medium text-gray-500">适用范围取值（非「仅当前案件」必填）</span>
              <input
                v-model="form.scope_value"
                class="form-input"
                type="text"
                :disabled="form.scope === 'case_only'"
                placeholder="栏目名 / 书写者 / 模板版本"
              />
            </label>
          </div>

          <div class="grid gap-4 sm:grid-cols-2">
            <label class="block">
              <span class="mb-1 block text-xs font-medium text-gray-500">确认人</span>
              <input v-model="form.confirmed_by" class="form-input" type="text" />
            </label>
            <label class="flex items-center gap-2 self-end pb-2 text-sm text-gray-700">
              <input v-model="form.apply_to_case" type="checkbox" />
              应用到当前案件并失效旧复核状态（R-40）
            </label>
          </div>
        </div>

        <footer class="flex items-center justify-end gap-2 border-t border-gray-200 p-4">
          <button class="btn-secondary" :disabled="busy" @click="close">取消</button>
          <button class="btn-primary" :disabled="busy" @click="submit">
            <RefreshCw v-if="busy" :size="16" class="animate-spin" />
            提交修正
          </button>
        </footer>
      </div>
    </div>
  </Transition>
</template>
