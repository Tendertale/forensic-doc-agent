<script setup>
/**
 * 侧边导航：分两层（与无构建工作台 frontend/static/index.html 的导航分组一致）。
 *
 *   主流程（Transparent）：整理（上传）→ 结果（下载 Word / PDF）
 *   案卷与复核（次级界面）：案件记录、案件工作台（四步核对）、复核中心、术语规则、系统配置
 *
 * 普通用户只需要主流程两页；四步主流程（R-1）能力一个不少，全部收在次级界面。
 * 底部「红线提示」与 static 版逐字一致：空白写“未记录”、鉴定意见只由专业人员输入、
 * 系统不读取照片内容、未提供照片的栏位写「（未提供照片）」。
 */
import { BookOpen, ClipboardList, FileText, FolderOpen, Inbox, Settings, Upload, X } from 'lucide-vue-next'

const props = defineProps({
  activeView: { type: String, required: true },
  mobileOpen: { type: Boolean, default: false },
  counts: { type: Object, default: () => ({}) },
  aiEnabled: { type: Boolean, default: false },
  aiStatus: { type: String, default: 'unconfigured' },
  demoData: { type: Boolean, default: false },
})

const emit = defineEmits(['navigate', 'close', 'configure'])

const navigationGroups = [
  {
    title: '主流程（Transparent）',
    items: [
      { id: 'quick', label: '整理', hint: '上传文件', icon: Upload, badge: '' },
      { id: 'results', label: '结果', hint: '下载 Word / PDF', icon: FileText, badge: 'results' },
    ],
  },
  {
    title: '案卷与复核（次级界面）',
    items: [
      { id: 'cases', label: '案件记录', hint: '案件列表', icon: Inbox, badge: 'cases' },
      { id: 'case', label: '案件工作台', hint: '四步核对', icon: FolderOpen, badge: '' },
      { id: 'review', label: '复核中心', hint: '疑点与阻断项', icon: ClipboardList, badge: 'review' },
      { id: 'rules', label: '术语规则', hint: '简写对照与转换边界', icon: BookOpen, badge: 'rules' },
      { id: 'settings', label: '系统配置', hint: '引擎与模型接入', icon: Settings, badge: '' },
    ],
  },
]

function badgeValue(key) {
  if (key === 'cases') return Number(props.counts?.cases || 0)
  if (key === 'review') return Number(props.counts?.blocking || 0) + Number(props.counts?.unresolved || 0)
  if (key === 'rules') return Number(props.counts?.rules || 0)
  return 0
}

/** 「结果」用文字徽标而不是数字：尚未整理 / 待处理 N / 阻断 N / 已完成。 */
function resultsLabel() {
  return props.counts?.resultsLabel || '尚未整理'
}

function resultsClass() {
  const tone = props.counts?.resultsTone || 'muted'
  if (tone === 'danger') return 'bg-red-100 text-red-700'
  if (tone === 'warning') return 'bg-amber-100 text-amber-800'
  if (tone === 'ok') return 'bg-emerald-50 text-emerald-700'
  return 'bg-gray-100 text-gray-600'
}
</script>

<template>
  <Transition name="fade">
    <button
      v-if="mobileOpen"
      class="fixed inset-0 z-40 cursor-pointer bg-gray-950/30 lg:hidden"
      aria-label="关闭导航"
      @click="emit('close')"
    />
  </Transition>

  <aside
    class="fixed inset-y-0 left-0 z-50 flex w-60 flex-col border-r border-gray-200 bg-white transition-transform duration-200 lg:translate-x-0"
    :class="mobileOpen ? 'translate-x-0' : '-translate-x-full'"
  >
    <div class="flex h-16 shrink-0 items-center justify-between border-b border-gray-200 px-5">
      <div class="flex min-w-0 items-center gap-3">
        <span class="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-blue-600 text-white">
          <ClipboardList :size="20" stroke-width="2.2" />
        </span>
        <div class="min-w-0">
          <p class="truncate text-[15px] font-semibold text-gray-950">尸表检验文书</p>
          <p class="truncate text-xs text-gray-500">整理工作台</p>
        </div>
      </div>
      <button
        class="cursor-pointer rounded-lg p-2 text-gray-500 hover:bg-gray-100 lg:hidden"
        aria-label="关闭导航"
        @click="emit('close')"
      >
        <X :size="19" />
      </button>
    </div>

    <nav class="flex-1 space-y-1 overflow-y-auto px-3 py-4" aria-label="主导航">
      <template v-for="group in navigationGroups" :key="group.title">
        <p class="px-3 pb-1 pt-3 text-[11px] font-semibold uppercase tracking-wide text-gray-400">
          {{ group.title }}
        </p>
        <button
          v-for="item in group.items"
          :key="item.id"
          class="flex w-full cursor-pointer items-start gap-3 rounded-lg px-3 py-2.5 text-left text-sm font-medium transition-colors"
          :class="activeView === item.id ? 'bg-blue-50 text-blue-700' : 'text-gray-600 hover:bg-gray-50 hover:text-gray-950'"
          @click="emit('navigate', item.id)"
        >
          <component :is="item.icon" :size="19" class="mt-0.5 shrink-0" />
          <span class="min-w-0 flex-1">
            <span class="block truncate">{{ item.label }}</span>
            <span class="mt-0.5 block truncate text-xs font-normal text-gray-400">{{ item.hint }}</span>
          </span>
          <span
            v-if="item.badge === 'results'"
            class="mt-0.5 shrink-0 whitespace-nowrap rounded-full px-1.5 py-0.5 text-[11px] font-semibold"
            :class="resultsClass()"
          >
            {{ resultsLabel() }}
          </span>
          <span
            v-else-if="item.badge && badgeValue(item.badge) > 0"
            class="mt-0.5 min-w-6 shrink-0 rounded-full bg-amber-100 px-1.5 py-0.5 text-center text-xs font-semibold text-amber-700"
          >
            {{ badgeValue(item.badge) }}
          </span>
        </button>
      </template>

      <button
        class="mt-2 flex w-full cursor-pointer items-start gap-3 rounded-lg px-3 py-2.5 text-left text-sm font-medium text-gray-600 transition-colors hover:bg-gray-50 hover:text-gray-950"
        @click="emit('configure')"
      >
        <Settings :size="19" class="mt-0.5 shrink-0" />
        <span class="min-w-0 flex-1">
          <span class="block truncate">模型接入</span>
          <span class="mt-0.5 block truncate text-xs font-normal text-gray-400">密钥仅内存</span>
        </span>
        <span
          class="mt-1.5 h-2 w-2 shrink-0 rounded-full"
          :class="!aiEnabled ? 'bg-gray-400' : aiStatus === 'ready' ? 'bg-emerald-500' : 'bg-amber-500'"
        />
      </button>

      <div class="mt-4 rounded-lg border border-gray-200 bg-gray-50 px-3 py-3 text-[11px] leading-5 text-gray-600">
        <p class="font-semibold text-gray-700">红线提示</p>
        <ul class="mt-1 list-disc space-y-0.5 pl-4">
          <li>空白字段一律显示“未记录”，不写成“未见异常”。</li>
          <li>鉴定意见只能由专业人员手工输入。</li>
          <li>部位照片：系统不读取照片内容，只按你选择的位置插入（未选择时按文件名推测，可在案件记录中修改）。</li>
          <li>本次没有照片的照片栏位写“（未提供照片）”，不会拿其他栏位顶替。</li>
        </ul>
      </div>
    </nav>

    <div class="border-t border-gray-200 p-4">
      <div
        v-if="demoData"
        class="mb-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-800"
      >
        当前为内置演示数据，不代表任何真实案件。
      </div>
      <div class="flex items-center gap-3 rounded-lg bg-gray-50 px-3 py-3">
        <span class="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-gray-900 text-sm font-semibold text-white">
          检
        </span>
        <div class="min-w-0">
          <p class="truncate text-sm font-medium text-gray-900">复核人</p>
          <p class="truncate text-xs text-gray-500">专业复核席</p>
        </div>
      </div>
    </div>
  </aside>
</template>
