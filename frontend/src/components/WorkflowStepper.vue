<script setup>
/**
 * WorkflowStepper.vue —— 10 阶段步骤条 + 推进/回退。
 *
 * 阶段顺序与相邻流转规则来自 constants.CASE_STAGES / STAGE_TRANSITIONS
 * （后端 PATCH /api/cases/{id}/stage 只允许前进一格或回退一格）。
 * 组件本身不判断能不能推进：只能对 `transitions` 中列出的目标阶段发起请求。
 */
import { computed } from 'vue'
import { ChevronLeft, ChevronRight, RefreshCw } from 'lucide-vue-next'

const props = defineProps({
  stageKeys: { type: Array, default: () => [] },
  stageLabels: { type: Object, default: () => ({}) },
  current: { type: String, default: '' },
  transitions: { type: Array, default: () => [] },
  pending: { type: Boolean, default: false },
  version: { type: Number, default: 1 },
  updatedAt: { type: String, default: '' },
})

const emit = defineEmits(['change'])

const steps = computed(() =>
  props.stageKeys.map((key, index) => ({
    key,
    index,
    label: props.stageLabels[key] || key,
  })),
)

const currentIndex = computed(() => {
  const index = steps.value.findIndex((step) => step.key === props.current)
  return index < 0 ? 0 : index
})

/**
 * 允许回退/前进的一格。
 * 优先采用后端返回的 transitions；未返回时按阶段顺序取相邻一格
 * （与前端的相邻一格规则一致，最终仍由后端 PATCH 校验）。
 */
const prevTarget = computed(() => {
  const fromServer = props.transitions.find(
    (key) => steps.value.findIndex((s) => s.key === key) < currentIndex.value,
  )
  if (fromServer) return fromServer
  if (props.transitions.length) return ''
  return steps.value[currentIndex.value - 1]?.key || ''
})

const nextTarget = computed(() => {
  const fromServer = props.transitions.find(
    (key) => steps.value.findIndex((s) => s.key === key) > currentIndex.value,
  )
  if (fromServer) return fromServer
  if (props.transitions.length) return ''
  return steps.value[currentIndex.value + 1]?.key || ''
})

function stepState(step) {
  if (step.key === props.current) return 'current'
  return step.index < currentIndex.value ? 'done' : 'todo'
}

function labelOf(key) {
  return props.stageLabels[key] || key || '—'
}

function change(target) {
  if (!target || props.pending) return
  emit('change', { stage: target })
}
</script>

<template>
  <section class="panel p-4" aria-label="案件阶段">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div class="min-w-0">
        <h2 class="text-sm font-semibold text-gray-950">案件阶段</h2>
        <p class="mt-0.5 text-xs text-gray-500">
          当前：<span class="font-medium text-gray-800">{{ labelOf(current) }}</span>
          · 案件版本 v{{ version }}
          <template v-if="updatedAt"> · 更新于 {{ updatedAt }}</template>
        </p>
      </div>
      <div class="flex items-center gap-2">
        <button
          class="btn-secondary"
          :disabled="!prevTarget || pending"
          :title="prevTarget ? `回退到：${labelOf(prevTarget)}` : '已是最早阶段'"
          @click="change(prevTarget)"
        >
          <ChevronLeft :size="16" />
          回退一格
        </button>
        <button
          class="btn-primary"
          :disabled="!nextTarget || pending"
          :title="nextTarget ? `推进到：${labelOf(nextTarget)}` : '已是最末阶段'"
          @click="change(nextTarget)"
        >
          {{ pending ? '处理中…' : '推进一格' }}
          <RefreshCw v-if="pending" :size="16" class="animate-spin" />
          <ChevronRight v-else :size="16" />
        </button>
      </div>
    </div>

    <ol class="mt-4 flex flex-wrap gap-2">
      <li
        v-for="step in steps"
        :key="step.key"
        class="flex min-w-[8.5rem] flex-1 items-center gap-2 rounded-lg border px-3 py-2 text-xs"
        :class="stepState(step) === 'current'
          ? 'border-blue-300 bg-blue-50 text-blue-800'
          : stepState(step) === 'done'
            ? 'border-emerald-200 bg-emerald-50 text-emerald-800'
            : 'border-gray-200 bg-white text-gray-500'"
      >
        <span
          class="flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold"
          :class="stepState(step) === 'current'
            ? 'bg-blue-600 text-white'
            : stepState(step) === 'done'
              ? 'bg-emerald-500 text-white'
              : 'bg-gray-200 text-gray-600'"
        >
          {{ step.index + 1 }}
        </span>
        <span class="min-w-0 truncate" :title="step.label">{{ step.label }}</span>
      </li>
    </ol>

    <p
      v-if="!steps.length"
      class="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800"
    >
      未取到阶段目录（/api/config 未返回 stages），推进/回退暂不可用；这不影响材料、字段与文稿操作。
    </p>
    <p
      v-else-if="!nextTarget && !prevTarget"
      class="mt-3 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-xs text-gray-600"
    >
      已处于阶段两端（{{ labelOf(current) }}），不可再推进或回退。
    </p>
  </section>
</template>
