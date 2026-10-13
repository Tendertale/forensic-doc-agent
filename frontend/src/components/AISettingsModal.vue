<script setup>
/**
 * AISettingsModal.vue —— 模型接入（密钥仅内存）。
 *
 * 硬约束（AGENTS.md 第 9 条）：`X-DeepSeek-API-Key` 只走请求头，
 * **绝不**写入数据库、日志、源码或构建产物。本组件因此：
 *   1. 密钥只保存在组件内的 ref 中，不写入 localStorage / sessionStorage / cookie；
 *   2. 提交后立即清空输入框，只保留「已配置」状态标记；
 *   3. 关闭面板时清空密钥与状态。
 *
 * 端点：api.verifyAi(apiKey) → POST /api/ai/verify（请求头 X-DeepSeek-API-Key）。
 * 模型可选能力默认关闭（R-21：不用上下文猜测替代辨认）。
 */
import { computed, ref, watch } from 'vue'
import { CircleAlert, CircleCheck, KeyRound, RefreshCw, X } from 'lucide-vue-next'

const props = defineProps({
  open: { type: Boolean, default: false },
  status: { type: String, default: 'unconfigured' },
  busy: { type: Boolean, default: false },
})

const emit = defineEmits(['close', 'verify'])

const draftKey = ref('')
const reveal = ref(false)
const acknowledged = ref(false)

const statusText = computed(() => {
  if (props.status === 'ready') return '已连接'
  if (props.status === 'failed') return '连接失败'
  return '未配置'
})

const statusTone = computed(() => {
  if (props.status === 'ready') return 'chip-recorded'
  if (props.status === 'failed') return 'chip-blocking'
  return 'chip-not-recorded'
})

watch(
  () => props.open,
  (value) => {
    if (!value) {
      draftKey.value = ''
      reveal.value = false
      acknowledged.value = false
    }
  },
)

function close() {
  draftKey.value = ''
  reveal.value = false
  emit('close')
}

function submit() {
  const key = draftKey.value.trim()
  if (!key) return
  emit('verify', key)
  draftKey.value = ''
  reveal.value = false
}
</script>

<template>
  <Transition name="fade">
    <div
      v-if="open"
      class="fixed inset-0 z-[75] flex items-start justify-center overflow-y-auto bg-gray-950/40 p-4 sm:p-8"
      @click.self="close"
    >
      <div class="panel w-full max-w-xl" role="dialog" aria-modal="true" aria-label="模型接入">
        <header class="flex items-start justify-between gap-3 border-b border-gray-200 p-5">
          <div>
            <h2 class="flex items-center gap-2 text-base font-semibold text-gray-950">
              <KeyRound :size="18" />
              模型接入
            </h2>
            <p class="mt-1 text-xs text-gray-500">
              密钥只在本次会话的内存中使用，不写入数据库、日志、源码或构建产物（AGENTS.md 第 9 条）。
            </p>
          </div>
          <button class="cursor-pointer rounded-lg p-2 text-gray-500 hover:bg-gray-100" aria-label="关闭" @click="close">
            <X :size="18" />
          </button>
        </header>

        <div class="space-y-4 p-5">
          <p class="flex flex-wrap items-center gap-2 text-sm text-gray-700">
            <span class="chip" :class="statusTone">当前状态：{{ statusText }}</span>
            <span class="text-xs text-gray-500">连通性测试：POST /api/ai/verify（请求头 X-DeepSeek-API-Key）</span>
          </p>

          <label class="block">
            <span class="mb-1 block text-xs font-medium text-gray-500">API Key（提交后立即从输入框清除）</span>
            <div class="flex gap-2">
              <input
                v-model="draftKey"
                class="form-input"
                :type="reveal ? 'text' : 'password'"
                autocomplete="off"
                spellcheck="false"
                placeholder="sk-…（只保存在内存）"
                @keyup.enter="submit"
              />
              <button class="btn-secondary px-3" type="button" @click="reveal = !reveal">
                {{ reveal ? '隐藏' : '显示' }}
              </button>
            </div>
          </label>

          <div class="rounded-lg border border-amber-300 bg-amber-50 p-3 text-xs text-amber-900">
            <p class="flex items-start gap-2">
              <CircleAlert :size="15" class="mt-0.5 shrink-0" />
              <span>
                密钥不得出现在源码、日志、数据库或前端构建产物中；本界面不会把密钥写入本地存储，也不会在请求体里发送。
              </span>
            </p>
            <label class="mt-2 flex items-center gap-2">
              <input v-model="acknowledged" type="checkbox" />
              我确认仅在本机内存中使用该密钥
            </label>
          </div>

          <div class="rounded-lg border border-gray-200 bg-gray-50 p-3 text-xs text-gray-600">
            <p class="font-medium text-gray-700">能力边界</p>
            <ul class="mt-1 space-y-1">
              <li>模型默认关闭；低置信度识别结果只用于安排核对优先级，不用上下文猜测替代辨认（R-21 / R-45）。</li>
              <li>系统不生成鉴定意见，死亡原因、死亡时间、致伤工具与损伤形成机制只能由专业人员输入（N-1）。</li>
              <li>模型请求期间后端不持有 SQLite 写事务（AGENTS.md 第 10 条）。</li>
            </ul>
          </div>
        </div>

        <footer class="flex items-center justify-between gap-2 border-t border-gray-200 p-4">
          <p class="text-xs text-gray-500">
            测试成功后只保留「已连接」状态标记，不回显密钥。
          </p>
          <div class="flex gap-2">
            <button class="btn-secondary" :disabled="busy" @click="close">关闭</button>
            <button class="btn-primary" :disabled="busy || !draftKey.trim() || !acknowledged" @click="submit">
              <RefreshCw v-if="busy" :size="16" class="animate-spin" />
              <CircleCheck v-else :size="16" />
              测试连接
            </button>
          </div>
        </footer>
      </div>
    </div>
  </Transition>
</template>
