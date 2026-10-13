<script setup>
/**
 * TerminologyRulesPanel.vue —— 术语规则管理（简写对照与转换边界）。
 *
 * 端点：
 *   api.rules(filters)                   GET    /api/terminology-rules?active_only=&search=
 *   api.createRule(payload)              POST   /api/terminology-rules
 *   api.updateRule(ruleId, payload)      PATCH  /api/terminology-rules/{rule_id}
 *   api.deleteRule(ruleId, expectedVersion)  DELETE ?expected_version=N
 *
 * 注意：/api/terminology-rules/** 的 expected_version 指的是**规则自身**的 version
 * （architecture 第 4 节），不是案件版本；本组件因此不接收 caseVersion。
 * 多义简写必须 requires_confirmation=true，交由用户确认（R-23 / R-37）。
 */
import { computed, reactive, ref, onMounted } from 'vue'
import { BookOpen, CircleAlert, Plus, RefreshCw, Trash2 } from 'lucide-vue-next'
import { api } from '../api.js'

const props = defineProps({
  dict: { type: Object, default: () => ({}) },
})

const emit = defineEmits(['notify', 'error'])

const rules = ref([])
const loading = ref(false)
const search = ref('')
const activeOnly = ref(false)
const busyId = ref(0)
const createOpen = ref(false)
const createBusy = ref(false)
const localError = ref('')

const newRule = reactive({
  code: '',
  shorthand: '',
  pattern: '',
  match_type: 'literal',
  template: '',
  scope_field_key: '',
  scope_column: '',
  scope_writer: '',
  boundary_note: '',
  require_groups: '',
  missing_group_note: '',
  requires_confirmation: true,
  priority: 100,
  active: true,
})

const edits = reactive({})

const visibleRules = computed(() => {
  const text = search.value.trim()
  if (!text) return rules.value
  return rules.value.filter((rule) =>
    [rule.code, rule.shorthand, rule.pattern, rule.template].filter(Boolean).join(' ').includes(text),
  )
})

async function load() {
  loading.value = true
  localError.value = ''
  try {
    const payload = await api.rules({ active_only: activeOnly.value ? 'true' : '', search: search.value.trim() })
    const items = Array.isArray(payload) ? payload : Array.isArray(payload?.items) ? payload.items : []
    rules.value = items
  } catch (error) {
    localError.value = error && error.message ? error.message : '规则加载失败'
  } finally {
    loading.value = false
  }
}

function draftFor(rule) {
  if (!edits[rule.id]) {
    edits[rule.id] = {
      shorthand: rule.shorthand ?? '',
      pattern: rule.pattern ?? '',
      match_type: rule.match_type || 'literal',
      template: rule.template ?? '',
      boundary_note: rule.boundary_note ?? '',
      requires_confirmation: !!rule.requires_confirmation,
      priority: Number(rule.priority ?? 100),
      active: !!rule.active,
    }
  }
  return edits[rule.id]
}

async function save(rule) {
  const draft = draftFor(rule)
  busyId.value = rule.id
  try {
    await api.updateRule(rule.id, {
      expected_version: rule.version,
      ...draft,
    })
    emit('notify', `术语规则已保存：${rule.code}`, 'success')
    delete edits[rule.id]
    await load()
  } catch (error) {
    emit('error', error)
  } finally {
    busyId.value = 0
  }
}

async function remove(rule) {
  if (!window.confirm(`确认删除术语规则 ${rule.code}？`)) return
  busyId.value = rule.id
  try {
    await api.deleteRule(rule.id, rule.version)
    emit('notify', '术语规则已删除', 'success')
    await load()
  } catch (error) {
    emit('error', error)
  } finally {
    busyId.value = 0
  }
}

async function submitCreate() {
  localError.value = ''
  if (!newRule.code.trim() || !newRule.pattern.trim()) {
    localError.value = '规则代码与匹配表达式为必填项。'
    return
  }
  createBusy.value = true
  try {
    await api.createRule({
      code: newRule.code.trim(),
      shorthand: newRule.shorthand.trim(),
      pattern: newRule.pattern.trim(),
      match_type: newRule.match_type,
      template: newRule.template.trim(),
      scope_field_key: newRule.scope_field_key.trim(),
      scope_column: newRule.scope_column.trim(),
      scope_writer: newRule.scope_writer.trim(),
      boundary_note: newRule.boundary_note.trim(),
      require_groups: String(newRule.require_groups || '')
        .split(',')
        .map((item) => item.trim())
        .filter(Boolean),
      missing_group_note: newRule.missing_group_note.trim(),
      requires_confirmation: !!newRule.requires_confirmation,
      priority: Number(newRule.priority) || 100,
      active: !!newRule.active,
    })
    emit('notify', '术语规则已新增', 'success')
    newRule.code = ''
    newRule.shorthand = ''
    newRule.pattern = ''
    newRule.template = ''
    newRule.boundary_note = ''
    createOpen.value = false
    await load()
  } catch (error) {
    localError.value = error && error.message ? error.message : '新增规则失败'
    emit('error', error)
  } finally {
    createBusy.value = false
  }
}

onMounted(load)
</script>

<template>
  <section class="panel" aria-label="术语规则">
    <header class="border-b border-gray-200 p-4">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div class="min-w-0">
          <h3 class="flex items-center gap-2 text-sm font-semibold text-gray-950">
            <BookOpen :size="16" />
            术语规则（简写 → 认可术语）
          </h3>
          <p class="mt-1 text-xs text-gray-500">
            常见表达用固定规则展开；多义表达必须要求人工确认，不用上下文猜测（R-23 / R-37）；不得改变左右侧与否定词（N-2～N-4）。
          </p>
        </div>
        <div class="flex flex-wrap items-center gap-2">
          <span class="chip chip-info">共 {{ rules.length }} 条</span>
          <button class="btn-primary" @click="createOpen = !createOpen">
            <Plus :size="16" />
            新增规则
          </button>
        </div>
      </div>

      <div class="mt-3 flex flex-wrap items-end gap-3">
        <label class="min-w-[14rem] flex-1">
          <span class="mb-1 block text-xs font-medium text-gray-500">关键字（代码 / 简写 / 表达式 / 模板）</span>
          <input v-model="search" class="form-input" type="search" placeholder="输入后按回车检索" @keyup.enter="load" />
        </label>
        <label class="flex items-center gap-2 pb-2 text-sm text-gray-700">
          <input v-model="activeOnly" type="checkbox" @change="load" />
          仅启用的规则
        </label>
        <button class="btn-secondary" :disabled="loading" @click="load">
          <RefreshCw :size="16" :class="loading ? 'animate-spin' : ''" />
          重新读取
        </button>
      </div>
    </header>

    <p
      v-if="localError"
      class="m-4 flex items-start gap-2 rounded-lg border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-800"
    >
      <CircleAlert :size="17" class="mt-0.5 shrink-0" />
      <span class="min-w-0 flex-1">{{ localError }}</span>
    </p>

    <div v-if="createOpen" class="border-b border-gray-200 bg-gray-50 p-4">
      <h4 class="text-sm font-semibold text-gray-900">新增规则</h4>
      <div class="mt-3 grid gap-3 sm:grid-cols-3 xl:grid-cols-4">
        <label class="block">
          <span class="mb-1 block text-xs font-medium text-gray-500">规则代码（唯一）</span>
          <input v-model="newRule.code" class="form-input" type="text" placeholder="例如 LB-010" />
        </label>
        <label class="block">
          <span class="mb-1 block text-xs font-medium text-gray-500">简写</span>
          <input v-model="newRule.shorthand" class="form-input" type="text" placeholder="例如 退指" />
        </label>
        <label class="block">
          <span class="mb-1 block text-xs font-medium text-gray-500">匹配表达式</span>
          <input v-model="newRule.pattern" class="form-input" type="text" placeholder="literal 或 regex" />
        </label>
        <label class="block">
          <span class="mb-1 block text-xs font-medium text-gray-500">匹配方式</span>
          <select v-model="newRule.match_type" class="form-input">
            <option value="literal">literal（字面）</option>
            <option value="regex">regex（正则）</option>
          </select>
        </label>
        <label class="block">
          <span class="mb-1 block text-xs font-medium text-gray-500">规范化模板</span>
          <input v-model="newRule.template" class="form-input" type="text" placeholder="例如 指压后褪色" />
        </label>
        <label class="block">
          <span class="mb-1 block text-xs font-medium text-gray-500">适用栏目（字段 key）</span>
          <input v-model="newRule.scope_field_key" class="form-input" type="text" placeholder="留空表示不限" />
        </label>
        <label class="block">
          <span class="mb-1 block text-xs font-medium text-gray-500">适用记录栏目 / 书写者</span>
          <input v-model="newRule.scope_column" class="form-input" type="text" />
          <input v-model="newRule.scope_writer" class="form-input mt-1" type="text" placeholder="书写者" />
        </label>
        <label class="block">
          <span class="mb-1 block text-xs font-medium text-gray-500">转换边界说明</span>
          <input v-model="newRule.boundary_note" class="form-input" type="text" placeholder="说明不能转换的情形" />
        </label>
        <label class="block">
          <span class="mb-1 block text-xs font-medium text-gray-500">必需分组（逗号分隔）</span>
          <input v-model="newRule.require_groups" class="form-input" type="text" />
        </label>
        <label class="block">
          <span class="mb-1 block text-xs font-medium text-gray-500">缺分组提示</span>
          <input v-model="newRule.missing_group_note" class="form-input" type="text" />
        </label>
        <label class="block">
          <span class="mb-1 block text-xs font-medium text-gray-500">优先级</span>
          <input v-model.number="newRule.priority" class="form-input" type="number" min="0" />
        </label>
        <div class="space-y-2 pt-5 text-sm text-gray-700">
          <label class="flex items-center gap-2">
            <input v-model="newRule.requires_confirmation" type="checkbox" />
            需要人工确认（多义简写必选）
          </label>
          <label class="flex items-center gap-2">
            <input v-model="newRule.active" type="checkbox" />
            启用
          </label>
        </div>
      </div>
      <div class="mt-3 flex gap-2">
        <button class="btn-primary" :disabled="createBusy" @click="submitCreate">
          <RefreshCw v-if="createBusy" :size="16" class="animate-spin" />
          保存规则
        </button>
        <button class="btn-secondary" :disabled="createBusy" @click="createOpen = false">取消</button>
      </div>
    </div>

    <div class="space-y-3 p-4">
      <p v-if="loading" class="py-8 text-center text-sm text-gray-500">正在读取规则…</p>
      <p v-else-if="!visibleRules.length" class="py-8 text-center text-sm text-gray-500">
        没有符合条件的规则（未记录）。可新增简写对照规则，并为多义表达勾选「需要人工确认」。
      </p>

      <article v-for="rule in visibleRules" v-else :key="rule.id" class="rounded-lg border border-gray-200 p-4">
        <header class="flex flex-wrap items-start justify-between gap-2">
          <h4 class="flex flex-wrap items-center gap-2 text-sm font-semibold text-gray-950">
            <span>{{ rule.code }}</span>
            <span v-if="rule.shorthand" class="chip chip-info">简写：{{ rule.shorthand }}</span>
            <span class="chip" :class="rule.active ? 'chip-recorded' : 'chip-not-recorded'">
              {{ rule.active ? '启用' : '停用' }}
            </span>
            <span v-if="rule.requires_confirmation" class="chip chip-unclear">需人工确认</span>
            <span class="text-xs font-normal text-gray-500">优先级 {{ rule.priority ?? '未记录' }} · 版本 v{{ rule.version ?? 1 }}</span>
          </h4>
          <button class="btn-danger px-2 py-1.5" :disabled="busyId === rule.id" @click="remove(rule)">
            <Trash2 :size="15" />
          </button>
        </header>

        <div class="mt-3 grid gap-3 sm:grid-cols-3 xl:grid-cols-4">
          <label class="block">
            <span class="mb-1 block text-xs font-medium text-gray-500">简写</span>
            <input v-model="draftFor(rule).shorthand" class="form-input" type="text" />
          </label>
          <label class="block">
            <span class="mb-1 block text-xs font-medium text-gray-500">匹配表达式</span>
            <input v-model="draftFor(rule).pattern" class="form-input" type="text" />
          </label>
          <label class="block">
            <span class="mb-1 block text-xs font-medium text-gray-500">匹配方式</span>
            <select v-model="draftFor(rule).match_type" class="form-input">
              <option value="literal">literal</option>
              <option value="regex">regex</option>
            </select>
          </label>
          <label class="block">
            <span class="mb-1 block text-xs font-medium text-gray-500">规范化模板</span>
            <input v-model="draftFor(rule).template" class="form-input" type="text" />
          </label>
          <label class="block">
            <span class="mb-1 block text-xs font-medium text-gray-500">转换边界说明</span>
            <input v-model="draftFor(rule).boundary_note" class="form-input" type="text" />
          </label>
          <label class="block">
            <span class="mb-1 block text-xs font-medium text-gray-500">优先级</span>
            <input v-model.number="draftFor(rule).priority" class="form-input" type="number" min="0" />
          </label>
          <div class="space-y-2 pt-5 text-sm text-gray-700">
            <label class="flex items-center gap-2">
              <input v-model="draftFor(rule).requires_confirmation" type="checkbox" />
              需要人工确认
            </label>
            <label class="flex items-center gap-2">
              <input v-model="draftFor(rule).active" type="checkbox" />
              启用
            </label>
          </div>
        </div>

        <dl class="mt-3 grid gap-2 text-xs text-gray-600 sm:grid-cols-3">
          <div>
            <dt class="text-gray-500">适用栏目</dt>
            <dd class="mt-0.5 text-gray-800">{{ rule.scope_field_key || '不限（未记录）' }}</dd>
          </div>
          <div>
            <dt class="text-gray-500">适用记录栏目 / 书写者</dt>
            <dd class="mt-0.5 text-gray-800">
              {{ rule.scope_column || '未记录' }} / {{ rule.scope_writer || '未记录' }}
            </dd>
          </div>
          <div>
            <dt class="text-gray-500">必需分组</dt>
            <dd class="mt-0.5 text-gray-800">
              {{ Array.isArray(rule.require_groups) && rule.require_groups.length ? rule.require_groups.join('、') : '未记录' }}
            </dd>
          </div>
        </dl>

        <div class="mt-3 flex items-center gap-2">
          <button class="btn-primary px-3 py-1.5" :disabled="busyId === rule.id" @click="save(rule)">
            <RefreshCw v-if="busyId === rule.id" :size="15" class="animate-spin" />
            保存规则
          </button>
          <span class="text-xs text-gray-500">
            规则端点的 expected_version 使用规则自身版本（architecture 第 4 节）。
          </span>
        </div>
      </article>
    </div>
  </section>
</template>
