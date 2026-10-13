<script setup>
import { ClipboardList, FileText, FolderOpen, Layers, Ruler, CircleAlert } from 'lucide-vue-next'
import { computed } from 'vue'

const props = defineProps({
  stats: { type: Object, default: () => ({}) },
  loading: { type: Boolean, default: false },
  dict: { type: Object, default: () => ({}) },
})

function num(...keys) {
  for (const key of keys) {
    const value = props.stats?.[key]
    if (typeof value === 'number' && Number.isFinite(value)) return value
    if (typeof value === 'string' && value.trim() !== '' && Number.isFinite(Number(value))) return Number(value)
  }
  return 0
}

const cards = computed(() => [
  {
    key: 'cases',
    label: '案件总数',
    note: '当前全部案件',
    icon: FolderOpen,
    tone: 'bg-blue-50 text-blue-700',
    value: num('cases', 'total_cases', 'case_count', 'total'),
  },
  {
    key: 'unresolved',
    label: '待核对字段',
    note: '待辨认或有冲突',
    icon: CircleAlert,
    tone: 'bg-amber-50 text-amber-700',
    value: num('unresolved_fields', 'unresolved_field_count', 'pending_fields', 'unresolved_count'),
  },
  {
    key: 'blocking',
    label: '未解决阻断项',
    note: '阻止生成待签发稿',
    icon: ClipboardList,
    tone: 'bg-red-50 text-red-700',
    value: num('blocking_items', 'blocking_item_count', 'blocking_count', 'open_blocking'),
  },
  {
    key: 'documents',
    label: '文稿版本',
    note: '草稿 / 待签发 / 已审核',
    icon: FileText,
    tone: 'bg-emerald-50 text-emerald-700',
    value: num('documents', 'document_count', 'doc_versions'),
  },
  {
    key: 'materials',
    label: '材料总数',
    note: '原件只读保存',
    icon: Layers,
    tone: 'bg-sky-50 text-sky-700',
    value: num('materials', 'material_count'),
  },
  {
    key: 'injuries',
    label: '损伤条目',
    note: '逐条保存不合并',
    icon: Ruler,
    tone: 'bg-slate-100 text-slate-700',
    value: num('injuries', 'injury_count'),
  },
])

const stageRows = computed(() => {
  const source = props.stats?.by_stage ?? props.stats?.stages
  const rows = []
  if (Array.isArray(source)) {
    source.forEach((item) => {
      const key = item?.stage ?? item?.key ?? item?.name ?? ''
      const count = Number(item?.count ?? item?.total ?? 0)
      if (key) rows.push({ key, label: props.dict?.stageLabels?.[key] || key, count })
    })
  } else if (source && typeof source === 'object') {
    Object.entries(source).forEach(([key, count]) => {
      rows.push({ key, label: props.dict?.stageLabels?.[key] || key, count: Number(count) || 0 })
    })
  }
  return rows
})

const kindRows = computed(() => {
  const source = props.stats?.by_material_kind ?? props.stats?.material_kinds
  const rows = []
  if (Array.isArray(source)) {
    source.forEach((item) => {
      const key = item?.kind ?? item?.key ?? item?.name ?? ''
      const count = Number(item?.count ?? item?.total ?? 0)
      if (key) rows.push({ key, label: props.dict?.materialKindLabels?.[key] || key, count })
    })
  } else if (source && typeof source === 'object') {
    Object.entries(source).forEach(([key, count]) => {
      rows.push({ key, label: props.dict?.materialKindLabels?.[key] || key, count: Number(count) || 0 })
    })
  }
  return rows
})

function barWidth(rows, count) {
  const max = rows.reduce((acc, row) => (row.count > acc ? row.count : acc), 0)
  if (!max) return '0%'
  return `${Math.round((count / max) * 100)}%`
}
</script>

<template>
  <section aria-label="案件统计">
    <div class="grid grid-cols-2 gap-4 xl:grid-cols-3 2xl:grid-cols-6">
      <article v-for="card in cards" :key="card.key" class="panel min-h-[126px] p-5">
        <div class="flex items-start justify-between gap-3">
          <div class="min-w-0">
            <p class="truncate text-sm font-medium text-gray-500">{{ card.label }}</p>
            <div v-if="loading" class="mt-3 h-8 w-16 animate-pulse rounded bg-gray-100" />
            <p v-else class="mt-2 text-3xl font-semibold tabular-nums text-gray-950">{{ card.value }}</p>
          </div>
          <span class="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg" :class="card.tone">
            <component :is="card.icon" :size="20" />
          </span>
        </div>
        <p class="mt-3 truncate text-xs text-gray-500">{{ card.note }}</p>
      </article>
    </div>

    <div v-if="stageRows.length || kindRows.length" class="mt-4 grid gap-4 lg:grid-cols-2">
      <div v-if="stageRows.length" class="panel p-5">
        <h2 class="text-sm font-semibold text-gray-900">阶段分布</h2>
        <ul class="mt-3 space-y-2">
          <li v-for="row in stageRows" :key="row.key" class="flex items-center gap-3 text-sm">
            <span class="w-28 shrink-0 truncate text-gray-600" :title="row.label">{{ row.label }}</span>
            <span class="h-2 min-w-0 flex-1 overflow-hidden rounded-full bg-gray-100">
              <span
                class="block h-full rounded-full bg-blue-500"
                :style="{ width: (stageRows[0] && Math.max(...stageRows.map((item) => item.count))) ? Math.round((row.count / Math.max(...stageRows.map((item) => item.count))) * 100) + '%' : '0%' }"
              />
            </span>
            <span class="w-8 shrink-0 text-right tabular-nums text-gray-700">{{ row.count }}</span>
          </li>
        </ul>
      </div>
      <div v-if="kindRows.length" class="panel p-5">
        <h2 class="text-sm font-semibold text-gray-900">材料类型分布</h2>
        <ul class="mt-3 space-y-2">
          <li v-for="row in kindRows" :key="row.key" class="flex items-center gap-3 text-sm">
            <span class="w-28 shrink-0 truncate text-gray-600" :title="row.label">{{ row.label }}</span>
            <span class="h-2 min-w-0 flex-1 overflow-hidden rounded-full bg-gray-100">
              <span
                class="block h-full rounded-full bg-emerald-500"
                :style="{ width: (kindRows[0] && Math.max(...kindRows.map((item) => item.count))) ? Math.round((row.count / Math.max(...kindRows.map((item) => item.count))) * 100) + '%' : '0%' }"
              />
            </span>
            <span class="w-8 shrink-0 text-right tabular-nums text-gray-700">{{ row.count }}</span>
          </li>
        </ul>
      </div>
    </div>
  </section>
</template>
