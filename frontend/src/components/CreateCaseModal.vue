<script setup>
/**
 * CreateCaseModal.vue —— 建案（含需求第一章「任务信息」表 intake）。
 *
 * 请求体对应 schemas.CaseCreate：
 *   case_no / commission_unit / commission_matter / commission_date / case_summary /
 *   subject_name / template_version / term_rule_version / note / intake{...}
 * case_no 留空由后端自动生成 FY-<年份>-<序号>。
 */
import { computed, reactive, ref, watch } from 'vue'
import { CircleAlert, RefreshCw, X } from 'lucide-vue-next'

const props = defineProps({
  open: { type: Boolean, default: false },
  saving: { type: Boolean, default: false },
  error: { type: String, default: '' },
  dict: { type: Object, default: () => ({}) },
})

const emit = defineEmits(['close', 'submit'])

const form = reactive({
  case_no: '',
  commission_unit: '',
  commission_matter: '',
  commission_date: '',
  case_summary: '',
  subject_name: '',
  template_version: '',
  term_rule_version: '',
  note: '',
  intake: {
    input_materials: '',
    output_documents: '',
    template_requirement: '',
    content_requirement: '',
    format_requirement: '',
    final_artifacts: 'docx,pdf',
    open_questions: '',
  },
})

const localError = ref('')

const templateDefaults = computed(() => {
  const raw = props.dict || {}
  return {
    templateVersion: raw.templateVersion || raw.defaultTemplateVersion || 'report-template-v1',
    termRuleVersion: raw.termRuleVersion || raw.defaultTermRuleVersion || 'term-rules-v1',
  }
})

watch(
  () => props.open,
  (value) => {
    if (!value) return
    localError.value = ''
    form.template_version = form.template_version || templateDefaults.value.templateVersion
    form.term_rule_version = form.term_rule_version || templateDefaults.value.termRuleVersion
  },
)

const artifactOptions = [
  { key: 'docx', label: 'Word 报告草稿（.docx）' },
  { key: 'pdf', label: 'PDF 预览（.pdf）' },
]

const selectedArtifacts = computed(() =>
  String(form.intake.final_artifacts || '')
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean),
)

function toggleArtifact(key) {
  const current = new Set(selectedArtifacts.value)
  if (current.has(key)) current.delete(key)
  else current.add(key)
  form.intake.final_artifacts = Array.from(current).join(',') || 'docx'
}

function close() {
  if (props.saving) return
  emit('close')
}

function submit() {
  localError.value = ''
  if (!form.commission_unit.trim() && !form.subject_name.trim() && !form.commission_matter.trim()) {
    localError.value = '至少填写委托单位、被检验对象姓名或委托事项中的一项，便于后续检索。'
    return
  }
  if (!selectedArtifacts.value.length) {
    localError.value = '最终产物只能是 docx 或 pdf。'
    return
  }
  emit('submit', {
    case_no: form.case_no.trim(),
    commission_unit: form.commission_unit.trim(),
    commission_matter: form.commission_matter.trim(),
    commission_date: form.commission_date.trim(),
    case_summary: form.case_summary.trim(),
    subject_name: form.subject_name.trim(),
    template_version: form.template_version.trim() || templateDefaults.value.templateVersion,
    term_rule_version: form.term_rule_version.trim() || templateDefaults.value.termRuleVersion,
    note: form.note.trim(),
    intake: {
      input_materials: form.intake.input_materials.trim(),
      output_documents: form.intake.output_documents.trim(),
      template_requirement: form.intake.template_requirement.trim(),
      content_requirement: form.intake.content_requirement.trim(),
      format_requirement: form.intake.format_requirement.trim(),
      final_artifacts: selectedArtifacts.value.join(','),
      open_questions: form.intake.open_questions.trim(),
    },
  })
}
</script>

<template>
  <Transition name="fade">
    <div
      v-if="open"
      class="fixed inset-0 z-[70] flex items-start justify-center overflow-y-auto bg-gray-950/40 p-4 sm:p-8"
      @click.self="close"
    >
      <div class="panel w-full max-w-3xl" role="dialog" aria-modal="true" aria-label="新建案件">
        <header class="flex items-start justify-between gap-3 border-b border-gray-200 p-5">
          <div>
            <h2 class="text-base font-semibold text-gray-950">新建案件</h2>
            <p class="mt-1 text-xs text-gray-500">
              案件编号留空时由后端自动生成（FY-&lt;年份&gt;-&lt;序号&gt;）。事实字段请以委托书与记录本原文为准，系统不补写原文没有的内容（N-2～N-4）。
            </p>
          </div>
          <button class="cursor-pointer rounded-lg p-2 text-gray-500 hover:bg-gray-100" aria-label="关闭" @click="close">
            <X :size="18" />
          </button>
        </header>

        <div class="max-h-[70vh] space-y-5 overflow-y-auto p-5">
          <div
            v-if="localError || error"
            class="flex items-start gap-2 rounded-lg border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-800"
          >
            <CircleAlert :size="17" class="mt-0.5 shrink-0" />
            <p class="min-w-0 flex-1">{{ localError || error }}</p>
          </div>

          <fieldset class="grid gap-4 sm:grid-cols-2">
            <legend class="mb-2 text-sm font-semibold text-gray-900">案件与委托</legend>
            <label class="block">
              <span class="mb-1 block text-xs font-medium text-gray-500">案件编号（留空自动生成）</span>
              <input v-model="form.case_no" class="form-input" type="text" placeholder="例如 FY-2026-0001" />
            </label>
            <label class="block">
              <span class="mb-1 block text-xs font-medium text-gray-500">委托日期</span>
              <input v-model="form.commission_date" class="form-input" type="text" placeholder="按原件记载填写，如 2026-03-05" />
            </label>
            <label class="block">
              <span class="mb-1 block text-xs font-medium text-gray-500">委托单位</span>
              <input v-model="form.commission_unit" class="form-input" type="text" placeholder="未记录时留空" />
            </label>
            <label class="block">
              <span class="mb-1 block text-xs font-medium text-gray-500">委托事项</span>
              <input v-model="form.commission_matter" class="form-input" type="text" placeholder="不得改写成鉴定意见（N-1）" />
            </label>
            <label class="block sm:col-span-2">
              <span class="mb-1 block text-xs font-medium text-gray-500">案情简介</span>
              <textarea v-model="form.case_summary" class="form-input" rows="3" placeholder="保留委托书来源属性；不作为检验人员直接观察所见" />
            </label>
          </fieldset>

          <fieldset class="grid gap-4 sm:grid-cols-2">
            <legend class="mb-2 text-sm font-semibold text-gray-900">被检验对象与文书配置</legend>
            <label class="block">
              <span class="mb-1 block text-xs font-medium text-gray-500">姓名</span>
              <input v-model="form.subject_name" class="form-input" type="text" />
            </label>
            <label class="block">
              <span class="mb-1 block text-xs font-medium text-gray-500">模板版本</span>
              <input v-model="form.template_version" class="form-input" type="text" />
            </label>
            <label class="block">
              <span class="mb-1 block text-xs font-medium text-gray-500">术语规则版本</span>
              <input v-model="form.term_rule_version" class="form-input" type="text" />
            </label>
            <label class="block">
              <span class="mb-1 block text-xs font-medium text-gray-500">备注</span>
              <input v-model="form.note" class="form-input" type="text" placeholder="处理记录用，不进入文书正文" />
            </label>
          </fieldset>

          <fieldset class="space-y-4">
            <legend class="mb-2 text-sm font-semibold text-gray-900">任务信息（intake）</legend>
            <div class="grid gap-4 sm:grid-cols-2">
              <label class="block">
                <span class="mb-1 block text-xs font-medium text-gray-500">输入材料</span>
                <textarea v-model="form.intake.input_materials" class="form-input" rows="2" placeholder="委托书、记录本、模板、补充要求…" />
              </label>
              <label class="block">
                <span class="mb-1 block text-xs font-medium text-gray-500">输出文档</span>
                <textarea v-model="form.intake.output_documents" class="form-input" rows="2" placeholder="报告草稿、检验记录整理稿…" />
              </label>
              <label class="block">
                <span class="mb-1 block text-xs font-medium text-gray-500">模板要求</span>
                <textarea v-model="form.intake.template_requirement" class="form-input" rows="2" placeholder="指定版本、栏目范围" />
              </label>
              <label class="block">
                <span class="mb-1 block text-xs font-medium text-gray-500">内容要求</span>
                <textarea v-model="form.intake.content_requirement" class="form-input" rows="2" placeholder="第一版重点栏目（R-8）" />
              </label>
              <label class="block">
                <span class="mb-1 block text-xs font-medium text-gray-500">格式要求</span>
                <textarea v-model="form.intake.format_requirement" class="form-input" rows="2" placeholder="以有效模板与审核样例为准（R-42）" />
              </label>
              <label class="block">
                <span class="mb-1 block text-xs font-medium text-gray-500">待确认问题</span>
                <textarea v-model="form.intake.open_questions" class="form-input" rows="2" placeholder="需要专业人员确认的问题" />
              </label>
            </div>
            <div>
              <span class="mb-1 block text-xs font-medium text-gray-500">最终产物（只能是 docx / pdf）</span>
              <div class="flex flex-wrap gap-2">
                <button
                  v-for="option in artifactOptions"
                  :key="option.key"
                  type="button"
                  class="rounded-lg border px-3 py-2 text-sm"
                  :class="selectedArtifacts.includes(option.key)
                    ? 'border-blue-300 bg-blue-50 text-blue-800'
                    : 'border-gray-300 bg-white text-gray-600'"
                  @click="toggleArtifact(option.key)"
                >
                  {{ selectedArtifacts.includes(option.key) ? '✓ ' : '' }}{{ option.label }}
                </button>
              </div>
            </div>
          </fieldset>
        </div>

        <footer class="flex items-center justify-end gap-2 border-t border-gray-200 p-4">
          <button class="btn-secondary" :disabled="saving" @click="close">取消</button>
          <button class="btn-primary" :disabled="saving" @click="submit">
            <RefreshCw v-if="saving" :size="16" class="animate-spin" />
            {{ saving ? '建案中…' : '建立案件' }}
          </button>
        </footer>
      </div>
    </div>
  </Transition>
</template>
