<script setup>
/**
 * CaseWorkspace.vue —— 案件工作台（整页抽屉）。
 *
 * 四步主流程（R-1）作为一级入口：
 *   ① 选择材料 → MaterialPanel + PhotoPanel
 *   ② 核对疑点 → FieldReviewPanel + InjuryPanel + ChecklistPanel
 *   ③ 查看文档 → DocumentPanel
 *   ④ 确认修改 → CorrectionModal（四类修正）+ 鉴定意见手工录入（N-1）
 *
 * 组件间只通过 props / emits 通信；写操作全部携带 expected_version（案件版本，乐观锁）。
 * 409 冲突由 App.vue 的全局处理器统一刷新案件详情与列表。
 */
import { computed, ref, watch } from 'vue'
import {
  BookOpen, CircleAlert, CircleCheck, ClipboardList, FileText, Image, Layers, RefreshCw,
  Settings, X,
} from 'lucide-vue-next'
import { api } from '../api.js'
import WorkflowStepper from './WorkflowStepper.vue'
import MaterialPanel from './MaterialPanel.vue'
import PhotoPanel from './PhotoPanel.vue'
import FieldReviewPanel from './FieldReviewPanel.vue'
import InjuryPanel from './InjuryPanel.vue'
import TerminologyRulesPanel from './TerminologyRulesPanel.vue'
import DocumentPanel from './DocumentPanel.vue'
import ChecklistPanel from './ChecklistPanel.vue'
import CorrectionModal from './CorrectionModal.vue'

const props = defineProps({
  caseDetail: { type: Object, default: null },
  loading: { type: Boolean, default: false },
  dict: { type: Object, default: () => ({}) },
  photoGroups: { type: Array, default: () => [] },
  config: { type: Object, default: () => ({}) },
})

const emit = defineEmits(['close', 'refresh', 'notify', 'error'])

const step = ref('materials')
const stagePending = ref(false)
const showCorrection = ref(false)
const normalizePending = ref(false)
const opinionDraft = ref('')
const opinionNote = ref('')
const opinionPending = ref(false)

const caseId = computed(() => Number(props.caseDetail?.id || 0))
const version = computed(() => Number(props.caseDetail?.version || 1))
const materials = computed(() => props.caseDetail?.materials || [])
const fields = computed(() => props.caseDetail?.fields || [])
const injuries = computed(() => props.caseDetail?.injuries || [])
const documents = computed(() => props.caseDetail?.documents || [])
const checklist = computed(() => props.caseDetail?.checklist || [])
const corrections = computed(() => props.caseDetail?.corrections || [])
const intake = computed(() => props.caseDetail?.intake || {})

const stageLabels = computed(() => props.dict?.stageLabels || {})
const stageKeys = computed(() => {
  const keys = props.dict?.stageKeys
  if (Array.isArray(keys) && keys.length) return keys
  return Object.keys(stageLabels.value)
})
const checkSeverityLabels = computed(() => props.dict?.checkSeverityLabels || {})
const correctionKindLabels = computed(() => props.dict?.correctionKindLabels || {})

const openChecklist = computed(() => checklist.value.filter((item) => item.status === 'open'))
const blockingOpen = computed(() =>
  openChecklist.value.filter((item) => String(item.severity) === 'blocking'),
)
const unclearFields = computed(() => fields.value.filter((item) => item.status === 'unclear'))
const pendingFields = computed(() =>
  fields.value.filter((item) => item.review_state === 'pending' || item.status === 'unclear'),
)
const photoMaterials = computed(() => materials.value.filter((item) => item.kind === 'photo'))
const unplacedPhotos = computed(() => photoMaterials.value.filter((item) => !item.position_key))

const steps = computed(() => [
  { key: 'materials', label: '① 选择材料', hint: '登记 / 预处理 / 识别原文', icon: Layers, badge: materials.value.length },
  { key: 'review', label: '② 核对疑点', hint: '字段 / 损伤 / 校核清单', icon: ClipboardList, badge: pendingFields.value.length + openChecklist.value.length },
  { key: 'documents', label: '③ 查看文档', hint: '生成 / 下载 / 审核', icon: FileText, badge: documents.value.length },
  { key: 'corrections', label: '④ 确认修改', hint: '四类修正与专业意见', icon: Settings, badge: corrections.value.length },
])

const transitions = computed(() => {
  const raw = props.config?.stage_transitions || props.config?.stageTransitions
  const current = props.caseDetail?.stage
  if (raw && typeof raw === 'object' && Array.isArray(raw[current])) return raw[current]
  return []
})

watch(
  () => props.caseDetail?.id,
  () => {
    step.value = blockingOpen.value.length ? 'review' : 'materials'
  },
  { immediate: true },
)

watch(
  () => props.caseDetail?.opinion_text,
  (value) => {
    opinionDraft.value = String(value || '')
  },
  { immediate: true },
)

const intakeRows = computed(() => [
  { label: '输入材料', value: intake.value.input_materials },
  { label: '输出文档', value: intake.value.output_documents },
  { label: '模板要求', value: intake.value.template_requirement },
  { label: '内容要求', value: intake.value.content_requirement },
  { label: '格式要求', value: intake.value.format_requirement },
  { label: '最终产物', value: intake.value.final_artifacts },
  { label: '待确认问题', value: intake.value.open_questions },
])

function notify(message, tone = 'info') {
  emit('notify', message, tone)
}

async function changeStage(payload) {
  if (!caseId.value) return
  stagePending.value = true
  try {
    await api.updateStage(caseId.value, {
      expected_version: version.value,
      stage: payload.stage,
      note: '前端阶段调整',
    })
    notify(`阶段已调整为「${stageLabels.value[payload.stage] || payload.stage}」`, 'success')
    emit('refresh')
  } catch (error) {
    emit('error', error)
  } finally {
    stagePending.value = false
  }
}

async function runNormalize(confirmSafe) {
  if (!caseId.value) return
  normalizePending.value = true
  try {
    const result = await api.runNormalize(caseId.value, {
      expected_version: version.value,
      target: 'all',
      rule_version: props.caseDetail?.term_rule_version || 'term-rules-v1',
      confirm_safe: !!confirmSafe,
    })
    const changed = Number(result?.changed ?? result?.updated ?? 0)
    notify(
      confirmSafe
        ? `术语整理完成：已直接确认 ${changed} 项无歧义结果`
        : `术语整理完成：生成 ${changed} 项待确认草稿（多义表达需人工确认，R-23）`,
      'success',
    )
    emit('refresh')
  } catch (error) {
    emit('error', error)
  } finally {
    normalizePending.value = false
  }
}

/**
 * 鉴定意见（N-1）：只能由专业人员手工输入，系统不生成、不推断。
 * 落库通道：PATCH /api/cases/{id}，请求体 expected_version + opinion_text
 * （schemas.CaseUpdate 已声明 opinion_text，database.update_case 已支持更新；已实测 PATCH 成功）。
 */
async function saveOpinion() {
  if (!caseId.value) return
  const text = opinionDraft.value.trim()
  opinionNote.value = ''
  opinionPending.value = true
  try {
    const updated = await api.updateCase(caseId.value, {
      expected_version: version.value,
      opinion_text: text,
    })
    if (String(updated?.opinion_text ?? '') !== text) {
      opinionNote.value =
        '后端返回的鉴定意见与本次提交内容不一致，无法确认已保存；请刷新案件后核对后再试。'
      notify(opinionNote.value, 'error')
      emit('refresh')
      return
    }
    notify('鉴定意见已保存（内容由专业人员提供，系统未生成或推断任何内容，N-1）', 'success')
    emit('refresh')
  } catch (error) {
    const message = error && error.message ? error.message : '保存失败'
    opinionNote.value = `未保存：${message}`
    notify(opinionNote.value, 'error')
  } finally {
    opinionPending.value = false
  }
}

function goStep(target) {
  step.value = target
}
</script>

<template>
  <Transition name="fade">
    <div class="fixed inset-0 z-[55] flex flex-col bg-gray-100">
      <header class="shrink-0 border-b border-gray-200 bg-white">
        <div class="mx-auto flex max-w-[1600px] flex-wrap items-center gap-3 px-4 py-3 sm:px-6">
          <div class="min-w-0 flex-1">
            <h2 class="flex flex-wrap items-center gap-2 text-base font-semibold text-gray-950">
              <span class="truncate">{{ caseDetail?.case_no || '案件详情' }}</span>
              <span v-if="caseDetail?.subject_name" class="chip chip-info">{{ caseDetail.subject_name }}</span>
              <span v-if="blockingOpen.length" class="chip chip-blocking">
                <CircleAlert :size="13" />
                {{ blockingOpen.length }} 项阻断
              </span>
              <span v-if="unclearFields.length" class="chip chip-unclear">
                待辨认/冲突 {{ unclearFields.length }}
              </span>
            </h2>
            <p class="mt-0.5 truncate text-xs text-gray-500">
              {{ caseDetail?.commission_unit || '委托单位未记录' }} ·
              {{ caseDetail?.commission_matter || '委托事项未记录' }} · 案件版本 v{{ version }}
            </p>
          </div>
          <button class="btn-secondary" :disabled="loading" @click="emit('refresh')">
            <RefreshCw :size="16" :class="loading ? 'animate-spin' : ''" />
            刷新
          </button>
          <button class="btn-secondary" @click="emit('close')">
            <X :size="16" />
            关闭
          </button>
        </div>
      </header>

      <div class="flex-1 overflow-y-auto">
        <div class="mx-auto max-w-[1600px] space-y-4 px-4 py-4 sm:px-6">
          <div
            v-if="blockingOpen.length"
            class="flex items-start gap-2 rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-800"
          >
            <CircleAlert :size="18" class="mt-0.5 shrink-0" />
            <p class="min-w-0 flex-1">
              存在 {{ blockingOpen.length }} 项未解决阻断项，不能生成待签发稿（R-28）。请到「③ 查看文档 → 校核清单」逐项处理或豁免。
            </p>
          </div>

          <div v-if="loading && !caseDetail" class="panel p-6 text-center text-sm text-gray-500">
            <RefreshCw :size="18" class="mx-auto animate-spin" />
            <p class="mt-2">正在加载案件详情…</p>
          </div>

          <template v-else>
            <WorkflowStepper
              :stage-keys="stageKeys"
              :stage-labels="stageLabels"
              :current="caseDetail?.stage || ''"
              :transitions="transitions"
              :pending="stagePending"
              :version="version"
              :updated-at="caseDetail?.updated_at || ''"
              @change="changeStage"
            />

            <nav class="panel flex flex-wrap gap-2 p-2" aria-label="四步主流程">
              <button
                v-for="item in steps"
                :key="item.key"
                class="flex min-w-[11rem] flex-1 cursor-pointer items-start gap-3 rounded-lg px-3 py-2.5 text-left transition-colors"
                :class="step === item.key ? 'bg-blue-50 text-blue-800' : 'text-gray-600 hover:bg-gray-50'"
                @click="goStep(item.key)"
              >
                <component :is="item.icon" :size="18" class="mt-0.5 shrink-0" />
                <span class="min-w-0 flex-1">
                  <span class="block truncate text-sm font-medium">{{ item.label }}</span>
                  <span class="mt-0.5 block truncate text-xs text-gray-400">{{ item.hint }}</span>
                </span>
                <span
                  v-if="item.badge > 0"
                  class="mt-0.5 shrink-0 rounded-full bg-gray-100 px-2 py-0.5 text-xs font-semibold text-gray-600"
                >
                  {{ item.badge }}
                </span>
              </button>
            </nav>

            <!-- ① 选择材料 -->
            <div v-if="step === 'materials'" class="space-y-4">
              <MaterialPanel
                :case-id="caseId"
                :case-version="version"
                :materials="materials"
                :dict="dict"
                @refresh="emit('refresh')"
                @notify="notify"
                @error="emit('error', $event)"
              />
              <PhotoPanel
                :case-id="caseId"
                :case-version="version"
                :materials="photoMaterials"
                :photo-groups="photoGroups"
                :dict="dict"
                @refresh="emit('refresh')"
                @notify="notify"
                @error="emit('error', $event)"
              />
              <section class="panel p-5">
                <h3 class="flex items-center gap-2 text-sm font-semibold text-gray-950">
                  <BookOpen :size="16" />
                  任务信息（建案时登记）
                </h3>
                <dl class="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                  <div
                    v-for="row in intakeRows"
                    :key="row.label"
                    class="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2"
                  >
                    <dt class="text-xs text-gray-500">{{ row.label }}</dt>
                    <dd class="mt-0.5 break-words text-sm text-gray-800">
                      {{ row.value && String(row.value).trim() ? row.value : '未记录' }}
                    </dd>
                  </div>
                </dl>
              </section>
            </div>

            <!-- ② 核对疑点 -->
            <div v-else-if="step === 'review'" class="space-y-4">
              <section class="panel flex flex-wrap items-center justify-between gap-3 p-4">
                <div class="min-w-0">
                  <h3 class="text-sm font-semibold text-gray-950">整理术语（简写 → 认可术语）</h3>
                  <p class="mt-0.5 text-xs text-gray-500">
                    多义表达交由用户确认，不自动猜测（R-23 / R-37）；不一致的来源在字段卡中并列展示，不静默选择一方（R-44）。
                  </p>
                </div>
                <div class="flex gap-2">
                  <button class="btn-secondary" :disabled="normalizePending" @click="runNormalize(false)">
                    <RefreshCw v-if="normalizePending" :size="16" class="animate-spin" />
                    生成整理草稿
                  </button>
                  <button class="btn-primary" :disabled="normalizePending" @click="runNormalize(true)">
                    整理并确认无歧义项
                  </button>
                </div>
              </section>

              <FieldReviewPanel
                :case-id="caseId"
                :case-version="version"
                :fields="fields"
                :materials="materials"
                :dict="dict"
                @refresh="emit('refresh')"
                @notify="notify"
                @error="emit('error', $event)"
              />
              <InjuryPanel
                :case-id="caseId"
                :case-version="version"
                :injuries="injuries"
                :materials="materials"
                :dict="dict"
                @refresh="emit('refresh')"
                @notify="notify"
                @error="emit('error', $event)"
              />
              <ChecklistPanel
                :case-id="caseId"
                :case-version="version"
                :items="checklist"
                :dict="dict"
                @refresh="emit('refresh')"
                @notify="notify"
                @error="emit('error', $event)"
              />
            </div>

            <!-- ③ 查看文档 -->
            <div v-else-if="step === 'documents'" class="space-y-4">
              <DocumentPanel
                :case-id="caseId"
                :case-version="version"
                :case-detail="caseDetail"
                :documents="documents"
                :dict="dict"
                @refresh="emit('refresh')"
                @notify="notify"
                @error="emit('error', $event)"
                @open-correction="showCorrection = true"
              />
              <ChecklistPanel
                :case-id="caseId"
                :case-version="version"
                :items="checklist"
                :dict="dict"
                @refresh="emit('refresh')"
                @notify="notify"
                @error="emit('error', $event)"
              />
            </div>

            <!-- ④ 确认修改 -->
            <div v-else class="space-y-4">
              <section class="panel p-5">
                <div class="flex flex-wrap items-center justify-between gap-3">
                  <div class="min-w-0">
                    <h3 class="text-sm font-semibold text-gray-950">四类修正（识别 / 术语 / 格式 / 工作顺序）</h3>
                    <p class="mt-0.5 text-xs text-gray-500">
                      每次修正保存修改前后内容、原因、确认人、适用范围与受影响文稿（R-38）；事实变化会让旧复核状态失效（R-40）。
                    </p>
                  </div>
                  <button class="btn-primary" @click="showCorrection = true">
                    <Settings :size="16" />
                    录入修正
                  </button>
                </div>

                <div class="mt-4 overflow-x-auto">
                  <table class="min-w-full divide-y divide-gray-200 text-sm">
                    <thead class="bg-gray-50 text-left text-xs font-semibold text-gray-500">
                      <tr>
                        <th class="px-3 py-2">类型</th>
                        <th class="px-3 py-2">对象</th>
                        <th class="px-3 py-2">修改前</th>
                        <th class="px-3 py-2">修改后</th>
                        <th class="px-3 py-2">原因</th>
                        <th class="px-3 py-2">适用范围</th>
                        <th class="px-3 py-2">确认人</th>
                        <th class="px-3 py-2">时间</th>
                      </tr>
                    </thead>
                    <tbody class="divide-y divide-gray-100">
                      <tr v-if="!corrections.length">
                        <td colspan="8" class="px-3 py-8 text-center text-gray-500">
                          暂无修正记录。识别、术语、格式与工作顺序四类修正都可在此录入。
                        </td>
                      </tr>
                      <tr v-for="row in corrections" v-else :key="row.id">
                        <td class="px-3 py-2">
                          <span class="chip chip-info">
                            {{ correctionKindLabels[row.kind] || row.kind || '未记录' }}
                          </span>
                        </td>
                        <td class="px-3 py-2 text-gray-600">
                          {{ row.target_type || '未记录' }} #{{ row.target_id ?? '未记录' }}
                          <span v-if="row.applied" class="ml-1 chip chip-recorded">已应用</span>
                        </td>
                        <td class="px-3 py-2 max-w-[16rem] break-words text-gray-700">
                          {{ row.before_text || '未记录' }}
                        </td>
                        <td class="px-3 py-2 max-w-[16rem] break-words text-gray-900">
                          {{ row.after_text || '未记录' }}
                        </td>
                        <td class="px-3 py-2 max-w-[14rem] break-words text-gray-700">{{ row.reason || '未记录' }}</td>
                        <td class="px-3 py-2 text-gray-600">
                          {{ row.scope === 'case_only' ? '仅当前案件' : `${row.scope}：${row.scope_value || '未记录'}` }}
                        </td>
                        <td class="px-3 py-2 text-gray-600">{{ row.confirmed_by || '未记录' }}</td>
                        <td class="px-3 py-2 whitespace-nowrap text-gray-500">{{ row.created_at || '未记录' }}</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </section>

              <section class="panel p-5">
                <h3 class="text-sm font-semibold text-gray-950">
                  鉴定意见（<span class="text-amber-700">由专业人员提供</span>）
                </h3>
                <p class="mt-1 text-xs text-gray-500">
                  该栏目只能手工输入。系统不生成、不推断死亡原因、死亡时间、致伤工具或损伤形成机制（N-1）。
                  保存走 <code class="rounded bg-gray-100 px-1">PATCH /api/cases/{id}</code>（带
                  <code class="rounded bg-gray-100 px-1">expected_version</code> 与
                  <code class="rounded bg-gray-100 px-1">opinion_text</code>）。
                </p>
                <textarea
                  v-model="opinionDraft"
                  class="form-input mt-3"
                  rows="4"
                  placeholder="由专业人员在这里手工填写；留空表示未记录，不会自动填充任何内容。"
                />
                <p
                  v-if="opinionNote"
                  class="mt-2 flex items-start gap-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900"
                >
                  <CircleAlert :size="15" class="mt-0.5 shrink-0" />
                  <span class="min-w-0 flex-1">{{ opinionNote }}</span>
                </p>
                <div class="mt-3 flex flex-wrap items-center gap-2">
                  <button class="btn-primary" :disabled="opinionPending" @click="saveOpinion">
                    <RefreshCw v-if="opinionPending" :size="16" class="animate-spin" />
                    <CircleCheck v-else :size="16" />
                    保存鉴定意见
                  </button>
                  <button class="btn-secondary" @click="opinionDraft = String(caseDetail?.opinion_text || '')">
                    撤销修改
                  </button>
                  <span class="text-xs text-gray-500">
                    当前记录：{{ caseDetail?.opinion_text && caseDetail.opinion_text.trim() ? '已填写' : '未记录' }}
                  </span>
                </div>
              </section>

              <section class="panel p-5">
                <h3 class="flex items-center gap-2 text-sm font-semibold text-gray-950">
                  <Image :size="16" />
                  部位照片位置核对（N-11）
                </h3>
                <p class="mt-1 text-xs text-gray-500">
                  未指定目标位置的照片不会进入文稿，并产出 <code class="rounded bg-gray-100 px-1">PHOTO_POSITION_MISSING</code> 警告校核项（R-57）。
                </p>
                <p v-if="unplacedPhotos.length" class="mt-2 text-sm text-amber-800">
                  仍有 {{ unplacedPhotos.length }} 张照片未指定位置：
                  {{ unplacedPhotos.map((item) => item.filename || `#${item.id}`).join('、') }}
                </p>
                <p v-else-if="photoMaterials.length" class="mt-2 text-sm text-emerald-700">
                  共 {{ photoMaterials.length }} 张部位照片均已指定目标位置。
                </p>
                <p v-else class="mt-2 text-sm text-gray-500">尚未上传部位照片（未记录）。</p>
                <button class="btn-secondary mt-3" @click="goStep('materials')">回到①选择材料调整位置</button>
              </section>

              <TerminologyRulesPanel :dict="dict" @error="emit('error', $event)" @notify="notify" />
            </div>
          </template>

          <section class="panel p-5">
            <h3 class="text-sm font-semibold text-gray-950">处理记录</h3>
            <ul class="mt-3 space-y-2 text-sm">
              <li
                v-for="event in (props.caseDetail?.events || [])"
                :key="event.id"
                class="flex flex-wrap items-baseline gap-2 border-b border-gray-100 pb-2 last:border-0"
              >
                <span class="text-gray-400">{{ event.created_at || '未记录' }}</span>
                <span class="font-medium text-gray-800">{{ event.title || event.event_type || '未记录' }}</span>
                <span class="min-w-0 flex-1 break-words text-gray-600">{{ event.detail || '' }}</span>
                <span class="text-xs text-gray-500">{{ event.actor || '系统' }}</span>
              </li>
              <li v-if="!(props.caseDetail?.events || []).length" class="text-gray-500">暂无处理记录。</li>
            </ul>
          </section>
        </div>
      </div>

      <CorrectionModal
        :open="showCorrection"
        :case-id="caseId"
        :case-version="version"
        :fields="fields"
        :injuries="injuries"
        :documents="documents"
        :dict="dict"
        @close="showCorrection = false"
        @done="emit('refresh')"
        @notify="notify"
        @error="emit('error', $event)"
      />
    </div>
  </Transition>
</template>
