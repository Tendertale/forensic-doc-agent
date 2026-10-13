<script setup>
/**
 * CaseTable.vue —— 案件表（服务端分页 / 搜索 / 筛选）。
 *
 * 展示案件摘要字段；所有筛选与翻页通过 emits 交给 App.vue 重新请求
 * `GET /api/cases`（服务端分页），组件自身不缓存全量数据。
 */
import { computed, ref, watch } from 'vue'
import { CircleAlert, Eye, Plus, RefreshCw, Search, Trash2 } from 'lucide-vue-next'

const props = defineProps({
  items: { type: Array, default: () => [] },
  total: { type: Number, default: 0 },
  page: { type: Number, default: 1 },
  pageSize: { type: Number, default: 20 },
  loading: { type: Boolean, default: false },
  filters: { type: Object, default: () => ({}) },
  dict: { type: Object, default: () => ({}) },
})

const emit = defineEmits(['filters', 'select', 'create', 'delete', 'refresh'])

const searchDraft = ref('')
let searchTimer = null

watch(
  () => props.filters?.search,
  (value) => {
    searchDraft.value = String(value || '')
  },
  { immediate: true },
)

const stageLabels = computed(() => props.dict?.stageLabels || {})
const materialKindLabels = computed(() => props.dict?.materialKindLabels || {})
const docStatusLabels = computed(() => props.dict?.docStatusLabels || {})

const stageKeys = computed(() => {
  const keys = props.dict?.stageKeys
  if (Array.isArray(keys) && keys.length) return keys
  return Object.keys(stageLabels.value)
})
const materialKindKeys = computed(() => Object.keys(materialKindLabels.value))
const docStatusKeys = computed(() => ['internal_draft', 'pending_issue', 'approved', 'superseded'])

const totalPages = computed(() => {
  const size = Number(props.pageSize) || 20
  return Math.max(1, Math.ceil((Number(props.total) || 0) / size))
})

function pushFilter(patch) {
  emit('filters', { ...patch, page: patch.page || 1 })
}

function onSearchInput() {
  if (searchTimer) window.clearTimeout(searchTimer)
  searchTimer = window.setTimeout(() => pushFilter({ search: searchDraft.value.trim() }), 350)
}

function goPage(next) {
  const target = Math.min(Math.max(1, next), totalPages.value)
  if (target === props.page) return
  emit('filters', { page: target })
}

function changePageSize(event) {
  const value = Number(event.target.value) || 20
  emit('filters', { page: 1, page_size: value })
}

function stageLabel(key) {
  return stageLabels.value[key] || key || '未记录'
}

function kindLabel(key) {
  return materialKindLabels.value[key] || key || '未记录'
}

function docStatusLabel(key) {
  if (!key) return '未记录'
  return docStatusLabels.value[key] || key
}

function num(value) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : 0
}

function stageTone(stage) {
  if (stage === 'delivered') return 'bg-emerald-50 text-emerald-700'
  if (stage === 'professional_review' || stage === 'check_document') return 'bg-blue-50 text-blue-700'
  if (stage === 'intake') return 'bg-gray-100 text-gray-600'
  return 'bg-sky-50 text-sky-700'
}
</script>

<template>
  <section class="panel overflow-hidden" aria-label="案件列表">
    <header class="flex flex-wrap items-end gap-3 border-b border-gray-200 p-4">
      <div class="min-w-[12rem] flex-1">
        <label class="mb-1 block text-xs font-medium text-gray-500" for="case-search">
          搜索（案件编号 / 委托单位 / 姓名 / 委托事项）
        </label>
        <div class="relative">
          <Search :size="16" class="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input
            id="case-search"
            v-model="searchDraft"
            class="form-input pl-9"
            type="search"
            placeholder="输入关键字后自动检索"
            @input="onSearchInput"
            @keyup.enter="pushFilter({ search: searchDraft.trim() })"
          />
        </div>
      </div>

      <div class="w-40">
        <label class="mb-1 block text-xs font-medium text-gray-500" for="case-stage">阶段</label>
        <select
          id="case-stage"
          class="form-input"
          :value="filters.stage || ''"
          @change="pushFilter({ stage: $event.target.value })"
        >
          <option value="">全部阶段</option>
          <option v-for="key in stageKeys" :key="key" :value="key">{{ stageLabel(key) }}</option>
        </select>
      </div>

      <div class="w-40">
        <label class="mb-1 block text-xs font-medium text-gray-500" for="case-doc-status">文稿状态</label>
        <select
          id="case-doc-status"
          class="form-input"
          :value="filters.doc_status || ''"
          @change="pushFilter({ doc_status: $event.target.value })"
        >
          <option value="">全部状态</option>
          <option v-for="key in docStatusKeys" :key="key" :value="key">{{ docStatusLabel(key) }}</option>
        </select>
      </div>

      <div class="w-40">
        <label class="mb-1 block text-xs font-medium text-gray-500" for="case-material-kind">材料类型</label>
        <select
          id="case-material-kind"
          class="form-input"
          :value="filters.material_kind || ''"
          @change="pushFilter({ material_kind: $event.target.value })"
        >
          <option value="">全部材料</option>
          <option v-for="key in materialKindKeys" :key="key" :value="key">{{ kindLabel(key) }}</option>
        </select>
      </div>

      <div class="w-40">
        <label class="mb-1 block text-xs font-medium text-gray-500" for="case-blocking">阻断项</label>
        <select
          id="case-blocking"
          class="form-input"
          :value="filters.has_blocking || ''"
          @change="pushFilter({ has_blocking: $event.target.value })"
        >
          <option value="">不限</option>
          <option value="true">存在未解决阻断项</option>
          <option value="false">无未解决阻断项</option>
        </select>
      </div>

      <div class="flex gap-2">
        <button class="btn-secondary" :disabled="loading" @click="emit('refresh')">
          <RefreshCw :size="16" :class="loading ? 'animate-spin' : ''" />
          刷新
        </button>
        <button class="btn-primary" @click="emit('create')">
          <Plus :size="16" />
          新建案件
        </button>
      </div>
    </header>

    <div class="overflow-x-auto">
      <table class="min-w-full divide-y divide-gray-200 text-sm">
        <thead class="bg-gray-50 text-left text-xs font-semibold uppercase tracking-wide text-gray-500">
          <tr>
            <th class="px-4 py-3">案件编号</th>
            <th class="px-4 py-3">被检验对象</th>
            <th class="px-4 py-3">委托单位</th>
            <th class="px-4 py-3">阶段</th>
            <th class="px-4 py-3">文稿</th>
            <th class="px-4 py-3 text-right">材料</th>
            <th class="px-4 py-3 text-right">字段</th>
            <th class="px-4 py-3 text-right">损伤</th>
            <th class="px-4 py-3 text-right">待核对</th>
            <th class="px-4 py-3 text-right">阻断</th>
            <th class="px-4 py-3">更新时间</th>
            <th class="px-4 py-3 text-right">操作</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-gray-100 bg-white">
          <tr v-if="loading">
            <td colspan="12" class="px-4 py-10 text-center text-gray-500">
              <span class="inline-flex items-center gap-2">
                <RefreshCw :size="16" class="animate-spin" />
                正在加载案件列表…
              </span>
            </td>
          </tr>
          <tr v-else-if="!items.length">
            <td colspan="12" class="px-4 py-10 text-center text-gray-500">
              暂无符合条件的案件。可先「新建案件」登记委托书、记录本与模板。
            </td>
          </tr>
          <tr
            v-for="row in items"
            v-else
            :key="row.id"
            class="cursor-pointer transition-colors hover:bg-blue-50/50"
            @click="emit('select', row)"
          >
            <td class="px-4 py-3 font-medium text-gray-950">
              {{ row.case_no || '未记录' }}
            </td>
            <td class="px-4 py-3 text-gray-700">{{ row.subject_name || '未记录' }}</td>
            <td class="px-4 py-3 text-gray-700">{{ row.commission_unit || '未记录' }}</td>
            <td class="px-4 py-3">
              <span class="chip" :class="stageTone(row.stage)">{{ stageLabel(row.stage) }}</span>
            </td>
            <td class="px-4 py-3 text-gray-700">{{ docStatusLabel(row.doc_status) }}</td>
            <td class="px-4 py-3 text-right tabular-nums text-gray-700">{{ num(row.material_count) }}</td>
            <td class="px-4 py-3 text-right tabular-nums text-gray-700">{{ num(row.field_count) }}</td>
            <td class="px-4 py-3 text-right tabular-nums text-gray-700">{{ num(row.injury_count) }}</td>
            <td class="px-4 py-3 text-right">
              <span
                class="tabular-nums"
                :class="num(row.unresolved_count) > 0 ? 'font-semibold text-amber-700' : 'text-gray-500'"
              >
                {{ num(row.unresolved_count) }}
              </span>
            </td>
            <td class="px-4 py-3 text-right">
              <span
                class="inline-flex items-center gap-1 tabular-nums"
                :class="num(row.blocking_count) > 0 ? 'font-semibold text-red-700' : 'text-gray-500'"
              >
                <CircleAlert v-if="num(row.blocking_count) > 0" :size="14" />
                {{ num(row.blocking_count) }}
              </span>
            </td>
            <td class="px-4 py-3 whitespace-nowrap text-gray-500">{{ row.updated_at || '未记录' }}</td>
            <td class="px-4 py-3">
              <div class="flex items-center justify-end gap-2">
                <button
                  class="btn-secondary px-3 py-1.5"
                  @click.stop="emit('select', row)"
                >
                  <Eye :size="15" />
                  打开
                </button>
                <button
                  class="btn-danger px-3 py-1.5"
                  :title="`删除 ${row.case_no || ''}`"
                  @click.stop="emit('delete', row)"
                >
                  <Trash2 :size="15" />
                </button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <footer class="flex flex-wrap items-center justify-between gap-3 border-t border-gray-200 px-4 py-3 text-sm">
      <p class="text-gray-600">
        共 <span class="font-semibold tabular-nums">{{ total }}</span> 条 · 第
        <span class="font-semibold tabular-nums">{{ page }}</span> /
        <span class="tabular-nums">{{ totalPages }}</span> 页
      </p>
      <div class="flex items-center gap-2">
        <label class="text-gray-500" for="case-page-size">每页</label>
        <select id="case-page-size" class="form-input w-20 py-1.5" :value="pageSize" @change="changePageSize">
          <option :value="10">10</option>
          <option :value="20">20</option>
          <option :value="50">50</option>
          <option :value="100">100</option>
        </select>
        <button class="btn-secondary px-3 py-1.5" :disabled="page <= 1" @click="goPage(page - 1)">
          上一页
        </button>
        <button class="btn-secondary px-3 py-1.5" :disabled="page >= totalPages" @click="goPage(page + 1)">
          下一页
        </button>
      </div>
    </footer>
  </section>
</template>
