<script setup>
/**
 * MaterialPanel.vue —— 材料清单：登记、页质量标记、预处理、识别原文、原图预览。
 *
 * 端点（api.js 冻结方法名）：
 *   api.addMaterial(id, payload)                       POST   /api/cases/{id}/materials
 *   api.updateMaterialPage(id, materialId, page, payload) PATCH .../pages/{page}
 *   api.preprocess(id, materialId, payload)            POST   .../preprocess
 *   api.runOcr(id, payload)                            POST   /api/cases/{id}/ocr
 *   api.ocrBlocks(id, filters)                         GET    /api/cases/{id}/ocr-blocks
 *   原图预览：api.materialFileUrl(id, materialId, page, variant)
 *            → GET /api/cases/{id}/materials/{material_id}/file?page=N&variant=original
 *            （api.js 已封装该 URL 构造器，不新增方法）
 *
 * 原件只读：预处理生成的是识别用副本，原件不覆盖（N-8 / R-19）。
 * 部位照片（kind=photo）永不进入识别流程（N-11 / R-55）。
 */
import { computed, reactive, ref, watch } from 'vue'
import { CircleAlert, Eye, FileText, RefreshCw, ScanLine, Upload, X } from 'lucide-vue-next'
import { api } from '../api.js'

const props = defineProps({
  caseId: { type: Number, default: 0 },
  caseVersion: { type: Number, default: 1 },
  materials: { type: Array, default: () => [] },
  dict: { type: Object, default: () => ({}) },
})

const emit = defineEmits(['refresh', 'notify', 'error'])

const MEDIA_TYPES = ['pdf', 'jpeg', 'png', 'docx', 'json', 'other']
const OPERATIONS = [
  { key: 'rotate', label: '旋转' },
  { key: 'deskew', label: '纠偏' },
  { key: 'crop', label: '裁切' },
  { key: 'contrast', label: '对比度' },
  { key: 'grayscale', label: '灰度' },
  { key: 'autocontrast', label: '自动对比度' },
]

const kindLabels = computed(() => props.dict?.materialKindLabels || {})
const flagLabels = computed(() => props.dict?.qualityFlagLabels || {})
const kindKeys = computed(() => {
  const keys = Object.keys(kindLabels.value)
  return keys.length ? keys : ['commission', 'record_book', 'photo', 'template', 'approved_report', 'terminology_reference', 'supplement']
})
const flagKeys = computed(() => {
  const keys = Object.keys(flagLabels.value)
  return keys.length ? keys : ['blurred', 'cropped', 'rotated', 'occluded', 'altered']
})

const registerForm = reactive({
  kind: 'commission',
  filename: '',
  media_type: 'pdf',
  clarity_note: '',
  quality_flags: [],
})
const selectedFile = ref(null)
const registerBusy = ref(false)

const ocrEngine = ref('')
const ocrBusyId = ref(0)
const preprocessBusyId = ref(0)
const pageBusyKey = ref('')
const pageOps = reactive({})

/**
 * 识别引擎选项：可用性只能来自 /api/config 的 engines.ocr=[{name,available,detail}]。
 * 顶层 ocr_engines 只是名字数组，不含可用性——绝不据此显示 paddle 可用（TODO-DL-2）。
 */
const engineOptions = computed(() => {
  const rows = Array.isArray(props.dict?.ocrEngineOptions) ? props.dict.ocrEngineOptions : []
  if (rows.length) return rows
  return [
    { name: 'sidecar', available: true, detail: '离线 JSON 样例引擎（默认，可离线运行）' },
    { name: 'paddle', available: false, detail: 'PaddleOCR 未安装（TODO-DL-2）' },
    { name: 'none', available: true, detail: '不执行识别' },
  ]
})

watch(
  engineOptions,
  (rows) => {
    if (!rows.length) return
    if (rows.some((row) => row.name === ocrEngine.value)) return
    const preferred =
      rows.find((row) => row.name === props.dict?.defaultOcrEngine) ||
      rows.find((row) => row.available) ||
      rows[0]
    ocrEngine.value = preferred.name
  },
  { immediate: true },
)

function engineLabel(engine) {
  if (engine.available) return `${engine.name}（可用）`
  return `${engine.name}（不可用${engine.detail ? `：${engine.detail}` : ''}）`
}

const preview = reactive({
  open: false,
  materialId: 0,
  filename: '',
  page: 1,
  variant: 'original',
  loading: false,
  blocks: [],
  blockTotal: 0,
  blockPage: 1,
  blockPageSize: 20,
  error: '',
})

function flagLabel(key) {
  return flagLabels.value[key] || key
}

function kindLabel(key) {
  return kindLabels.value[key] || key || '未记录'
}

function isPhoto(material) {
  return String(material?.kind) === 'photo'
}

function pageCount(material) {
  const pages = Array.isArray(material?.pages) ? material.pages.length : 0
  return Number(material?.page_count || pages || 0)
}

function pageNumbers(material) {
  const pages = Array.isArray(material?.pages) ? material.pages : []
  if (pages.length) return pages.map((page) => Number(page.page_number || 1))
  const total = pageCount(material)
  return total ? Array.from({ length: total }, (_, index) => index + 1) : [1]
}

function pageRecord(material, pageNumber) {
  const pages = Array.isArray(material?.pages) ? material.pages : []
  return pages.find((page) => Number(page.page_number) === Number(pageNumber)) || {}
}

const DEFAULT_OPERATIONS = ['autocontrast']

function opsKey(materialId, pageNumber) {
  return `${materialId}:${pageNumber}`
}

/** 只读访问：渲染期间绝不写入 pageOps（否则会触发递归更新）。 */
function operationsFor(materialId, pageNumber) {
  const key = opsKey(materialId, pageNumber)
  return Object.prototype.hasOwnProperty.call(pageOps, key) ? pageOps[key] : DEFAULT_OPERATIONS
}

function toggleOperation(materialId, pageNumber, op) {
  const key = opsKey(materialId, pageNumber)
  const next = operationsFor(materialId, pageNumber).slice()
  const index = next.indexOf(op)
  if (index >= 0) next.splice(index, 1)
  else next.push(op)
  pageOps[key] = next
}

function toggleRegisterFlag(flag) {
  const index = registerForm.quality_flags.indexOf(flag)
  if (index >= 0) registerForm.quality_flags.splice(index, 1)
  else registerForm.quality_flags.push(flag)
}

function detectMediaType(filename) {
  const name = String(filename || '').toLowerCase()
  if (name.endsWith('.pdf')) return 'pdf'
  if (name.endsWith('.jpg') || name.endsWith('.jpeg')) return 'jpeg'
  if (name.endsWith('.png')) return 'png'
  if (name.endsWith('.docx')) return 'docx'
  if (name.endsWith('.json')) return 'json'
  return 'other'
}

function readAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const result = String(reader.result || '')
      const comma = result.indexOf(',')
      resolve(comma >= 0 ? result.slice(comma + 1) : result)
    }
    reader.onerror = () => reject(new Error('文件读取失败'))
    reader.readAsDataURL(file)
  })
}

function onFileChange(event) {
  const file = event.target.files && event.target.files[0] ? event.target.files[0] : null
  selectedFile.value = file
  if (!file) return
  registerForm.filename = file.name
  registerForm.media_type = detectMediaType(file.name)
}

async function registerMaterial() {
  if (!props.caseId) return
  if (!registerForm.filename.trim()) {
    emit('notify', '请先选择需要登记的材料文件', 'error')
    return
  }
  registerBusy.value = true
  try {
    let contentBase64 = ''
    if (selectedFile.value) {
      contentBase64 = await readAsBase64(selectedFile.value)
    }
    const payload = {
      // 乐观锁：POST /api/cases/{id}/materials 的 expected_version 在请求体（MaterialCreate）。
      expected_version: props.caseVersion,
      kind: registerForm.kind,
      filename: registerForm.filename.trim(),
      media_type: registerForm.media_type,
      page_count: 0,
      clarity_note: registerForm.clarity_note.trim(),
      quality_flags: registerForm.quality_flags.slice(),
      sort_order: props.materials.length * 10 + 10,
    }
    if (contentBase64) payload.content_base64 = contentBase64
    await api.addMaterial(props.caseId, payload)
    emit('notify', `材料已登记：${payload.filename}（原件只读保存）`, 'success')
    selectedFile.value = null
    registerForm.filename = ''
    registerForm.clarity_note = ''
    registerForm.quality_flags = []
    emit('refresh')
  } catch (error) {
    emit('error', error)
  } finally {
    registerBusy.value = false
  }
}

async function savePage(material, pageNumber, patch) {
  if (!props.caseId) return
  const key = `${material.id}:${pageNumber}`
  pageBusyKey.value = key
  try {
    await api.updateMaterialPage(props.caseId, material.id, pageNumber, {
      expected_version: props.caseVersion,
      ...patch,
    })
    emit('notify', `第 ${pageNumber} 页质量标记已保存`, 'success')
    emit('refresh')
  } catch (error) {
    emit('error', error)
  } finally {
    pageBusyKey.value = ''
  }
}

function togglePageQuality(material, pageNumber, flag) {
  const record = pageRecord(material, pageNumber)
  const flags = Array.isArray(record.quality_flags) ? record.quality_flags.slice() : []
  const index = flags.indexOf(flag)
  if (index >= 0) flags.splice(index, 1)
  else flags.push(flag)
  savePage(material, pageNumber, { quality_flags: flags })
}

async function runPreprocess(material, pageNumber) {
  if (!props.caseId) return
  const operations = operationsFor(material.id, pageNumber).slice()
  if (!operations.length) {
    emit('notify', '请至少选择一项预处理操作', 'error')
    return
  }
  preprocessBusyId.value = material.id
  try {
    await api.preprocess(props.caseId, material.id, {
      expected_version: props.caseVersion,
      operations,
      page_number: Number(pageNumber) || 0,
    })
    emit('notify', `已生成识别用副本（原件未改动）：${material.filename || material.id}`, 'success')
    emit('refresh')
  } catch (error) {
    emit('error', error)
  } finally {
    preprocessBusyId.value = 0
  }
}

async function runOcr(material) {
  if (!props.caseId) return
  if (material && isPhoto(material)) {
    emit('notify', '部位照片不进入识别流程：系统不读取照片内容（N-11）', 'error')
    return
  }
  ocrBusyId.value = material ? material.id : 0
  try {
    await api.runOcr(props.caseId, {
      expected_version: props.caseVersion,
      engine: ocrEngine.value,
      material_id: material ? material.id : null,
      force: false,
    })
    emit('notify', '识别完成，结果已写入识别结果表', 'success')
    emit('refresh')
  } catch (error) {
    emit('error', error)
  } finally {
    ocrBusyId.value = 0
  }
}

async function openPreview(material, pageNumber) {
  preview.open = true
  preview.materialId = material.id
  preview.filename = material.filename || `材料 #${material.id}`
  preview.page = Number(pageNumber) || 1
  preview.variant = 'original'
  preview.error = ''
  preview.blocks = []
  preview.blockTotal = 0
  preview.blockPage = 1
  await loadBlocks()
}

function closePreview() {
  preview.open = false
}

function previewUrl() {
  return api.materialFileUrl(props.caseId, preview.materialId, preview.page, preview.variant)
}

async function loadBlocks() {
  if (!props.caseId || !preview.materialId) return
  preview.loading = true
  preview.error = ''
  try {
    const payload = await api.ocrBlocks(props.caseId, {
      material_id: preview.materialId,
      page: preview.blockPage,
      page_size: preview.blockPageSize,
    })
    preview.blocks = Array.isArray(payload?.items) ? payload.items : Array.isArray(payload) ? payload : []
    preview.blockTotal = Number(payload?.total) || preview.blocks.length
  } catch (error) {
    preview.error = error && error.message ? error.message : '识别结果加载失败'
  } finally {
    preview.loading = false
  }
}

function changeBlockPage(next) {
  const totalPages = Math.max(1, Math.ceil(preview.blockTotal / preview.blockPageSize))
  preview.blockPage = Math.min(Math.max(1, next), totalPages)
  loadBlocks()
}

function bboxText(bbox) {
  if (!Array.isArray(bbox) || bbox.length < 4) return '未记录'
  return bbox.map((value) => Number(value).toFixed(1)).join(', ')
}
</script>

<template>
  <section class="panel" aria-label="材料清单">
    <header class="border-b border-gray-200 p-4">
      <h3 class="flex items-center gap-2 text-sm font-semibold text-gray-950">
        <FileText :size="16" />
        材料清单（选择材料：登记 → 预处理 → 识别原文）
      </h3>
      <p class="mt-1 text-xs text-gray-500">
        原件只读保存，预处理只生成识别用副本；部位照片只按你选择的位置插入，系统不读取照片内容（N-8 / N-11）。
      </p>
    </header>

    <div class="grid gap-3 border-b border-gray-200 p-4 lg:grid-cols-12">
      <label class="lg:col-span-2">
        <span class="mb-1 block text-xs font-medium text-gray-500">材料类型</span>
        <select v-model="registerForm.kind" class="form-input">
          <option v-for="key in kindKeys" :key="key" :value="key">{{ kindLabel(key) }}</option>
        </select>
      </label>
      <label class="lg:col-span-4">
        <span class="mb-1 block text-xs font-medium text-gray-500">文件（支持 PDF / JPG / PNG / DOCX）</span>
        <input class="form-input file:mr-2 file:rounded file:border-0 file:bg-gray-100 file:px-2 file:py-1" type="file" @change="onFileChange" />
      </label>
      <label class="lg:col-span-2">
        <span class="mb-1 block text-xs font-medium text-gray-500">文件类型</span>
        <select v-model="registerForm.media_type" class="form-input">
          <option v-for="type in MEDIA_TYPES" :key="type" :value="type">{{ type }}</option>
        </select>
      </label>
      <label class="lg:col-span-4">
        <span class="mb-1 block text-xs font-medium text-gray-500">清晰度备注</span>
        <input v-model="registerForm.clarity_note" class="form-input" type="text" placeholder="例如：第 2 页手写字迹偏淡" />
      </label>
      <div class="lg:col-span-8">
        <span class="mb-1 block text-xs font-medium text-gray-500">登记时的质量标记</span>
        <div class="flex flex-wrap gap-2">
          <button
            v-for="flag in flagKeys"
            :key="flag"
            type="button"
            class="rounded-full border px-3 py-1 text-xs"
            :class="registerForm.quality_flags.includes(flag)
              ? 'border-amber-300 bg-amber-50 text-amber-800'
              : 'border-gray-300 bg-white text-gray-600'"
            @click="toggleRegisterFlag(flag)"
          >
            {{ flagLabel(flag) }}
          </button>
        </div>
      </div>
      <div class="flex items-end gap-2 lg:col-span-4">
        <button class="btn-primary" :disabled="registerBusy" @click="registerMaterial">
          <RefreshCw v-if="registerBusy" :size="16" class="animate-spin" />
          <Upload v-else :size="16" />
          登记材料
        </button>
        <label class="flex items-center gap-2 text-xs text-gray-500">
          <span>识别引擎</span>
          <select v-model="ocrEngine" class="form-input w-44 py-1.5">
            <option
              v-for="engine in engineOptions"
              :key="engine.name"
              :value="engine.name"
              :disabled="!engine.available"
            >
              {{ engineLabel(engine) }}
            </option>
          </select>
        </label>
      </div>
    </div>

    <div class="flex flex-wrap items-center justify-between gap-2 border-b border-gray-200 px-4 py-3">
      <p class="text-sm text-gray-600">
        共 <span class="font-semibold tabular-nums">{{ materials.length }}</span> 份材料
      </p>
      <div class="flex gap-2">
        <button class="btn-secondary" :disabled="ocrBusyId !== 0" @click="runOcr(null)">
          <ScanLine :size="16" />
          识别本案件全部材料
        </button>
      </div>
    </div>

    <div class="divide-y divide-gray-100">
      <p v-if="!materials.length" class="px-4 py-8 text-center text-sm text-gray-500">
        尚未登记材料（未记录）。委托书、记录本与报告模板为必需材料。
      </p>

      <article v-for="material in materials" v-else :key="material.id" class="p-4">
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div class="min-w-0">
            <p class="flex flex-wrap items-center gap-2 text-sm font-medium text-gray-950">
              <span class="chip chip-info">{{ kindLabel(material.kind) }}</span>
              <span class="truncate">{{ material.filename || '未记录' }}</span>
              <span class="text-xs font-normal text-gray-500">
                {{ material.media_type || '未记录' }} · {{ pageCount(material) }} 页
                <template v-if="material.sha256"> · sha256 {{ String(material.sha256).slice(0, 12) }}…</template>
              </span>
            </p>
            <p class="mt-1 flex flex-wrap gap-1 text-xs text-gray-500">
              <span v-if="material.clarity_note">备注：{{ material.clarity_note }}</span>
              <span v-if="Array.isArray(material.quality_flags) && material.quality_flags.length">
                质量标记：
                <span
                  v-for="flag in material.quality_flags"
                  :key="flag"
                  class="chip chip-warning mr-1"
                >{{ flagLabel(flag) }}</span>
              </span>
              <span v-if="isPhoto(material)" class="chip chip-info">系统不读取照片内容</span>
            </p>
          </div>
          <div class="flex shrink-0 flex-wrap gap-2">
            <button
              class="btn-secondary px-3 py-1.5"
              :disabled="!pageNumbers(material).length"
              @click="openPreview(material, pageNumbers(material)[0])"
            >
              <Eye :size="15" />
              原图预览
            </button>
            <button
              class="btn-secondary px-3 py-1.5"
              :disabled="isPhoto(material) || ocrBusyId === material.id"
              :title="isPhoto(material) ? '部位照片不进入识别流程（N-11）' : '仅识别委托书 / 记录本 / 补充资料'"
              @click="runOcr(material)"
            >
              <RefreshCw v-if="ocrBusyId === material.id" :size="15" class="animate-spin" />
              <ScanLine v-else :size="15" />
              识别本材料
            </button>
          </div>
        </div>

        <div v-if="!isPhoto(material)" class="mt-3 overflow-x-auto">
          <table class="min-w-full divide-y divide-gray-200 text-xs">
            <thead class="bg-gray-50 text-left font-semibold text-gray-500">
              <tr>
                <th class="px-3 py-2">页</th>
                <th class="px-3 py-2">扫描件</th>
                <th class="px-3 py-2">文字层</th>
                <th class="px-3 py-2">质量标记</th>
                <th class="px-3 py-2">预处理操作</th>
                <th class="px-3 py-2 text-right">操作</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-gray-100">
              <tr v-for="pageNumber in pageNumbers(material)" :key="pageNumber">
                <td class="px-3 py-2 tabular-nums text-gray-700">第 {{ pageNumber }} 页</td>
                <td class="px-3 py-2 text-gray-700">
                  {{ pageRecord(material, pageNumber).is_scanned === undefined
                    ? '未记录'
                    : pageRecord(material, pageNumber).is_scanned ? '是' : '否' }}
                </td>
                <td class="px-3 py-2 text-gray-700">
                  {{ pageRecord(material, pageNumber).has_text_layer === undefined
                    ? '未记录'
                    : pageRecord(material, pageNumber).has_text_layer ? '有' : '无' }}
                </td>
                <td class="px-3 py-2">
                  <div class="flex flex-wrap gap-1">
                    <button
                      v-for="flag in flagKeys"
                      :key="flag"
                      type="button"
                      class="cursor-pointer rounded-full border px-2 py-0.5"
                      :class="(pageRecord(material, pageNumber).quality_flags || []).includes(flag)
                        ? 'border-amber-300 bg-amber-50 text-amber-800'
                        : 'border-gray-300 bg-white text-gray-500'"
                      :disabled="pageBusyKey === `${material.id}:${pageNumber}`"
                      @click="togglePageQuality(material, pageNumber, flag)"
                    >
                      {{ flagLabel(flag) }}
                    </button>
                  </div>
                </td>
                <td class="px-3 py-2">
                  <div class="flex flex-wrap gap-1">
                    <button
                      v-for="op in OPERATIONS"
                      :key="op.key"
                      type="button"
                      class="cursor-pointer rounded-full border px-2 py-0.5"
                      :class="operationsFor(material.id, pageNumber).includes(op.key)
                        ? 'border-blue-300 bg-blue-50 text-blue-800'
                        : 'border-gray-300 bg-white text-gray-500'"
                      @click="toggleOperation(material.id, pageNumber, op.key)"
                    >
                      {{ op.label }}
                    </button>
                  </div>
                </td>
                <td class="px-3 py-2">
                  <div class="flex items-center justify-end gap-2">
                    <button class="btn-secondary px-2 py-1" @click="openPreview(material, pageNumber)">
                      <Eye :size="14" />
                      原图
                    </button>
                    <button
                      class="btn-secondary px-2 py-1"
                      :disabled="preprocessBusyId === material.id"
                      @click="runPreprocess(material, pageNumber)"
                    >
                      <RefreshCw v-if="preprocessBusyId === material.id" :size="14" class="animate-spin" />
                      预处理
                    </button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-else class="mt-3 rounded-lg border border-sky-200 bg-sky-50 px-3 py-2 text-xs text-sky-800">
          部位照片：系统不读取照片内容，也不会对它运行识别或页面渲染推理；请到「部位照片」面板选择目标位置（N-11 / R-55 / R-58）。
        </p>
      </article>
    </div>

    <Transition name="fade">
      <div
        v-if="preview.open"
        class="fixed inset-0 z-[65] flex items-start justify-center overflow-y-auto bg-gray-950/40 p-4 sm:p-8"
        @click.self="closePreview"
      >
        <div class="panel w-full max-w-4xl" role="dialog" aria-modal="true" aria-label="原图预览">
          <header class="flex items-start justify-between gap-3 border-b border-gray-200 p-4">
            <div class="min-w-0">
              <h4 class="truncate text-sm font-semibold text-gray-950">原图预览：{{ preview.filename }}</h4>
              <p class="mt-0.5 text-xs text-gray-500">
                原件只读（N-8）。图片取自 api.materialFileUrl({{ caseId }}, {{ preview.materialId }}, {{ preview.page }}, 'original')
              </p>
            </div>
            <button class="cursor-pointer rounded-lg p-2 text-gray-500 hover:bg-gray-100" aria-label="关闭" @click="closePreview">
              <X :size="18" />
            </button>
          </header>

          <div class="grid gap-4 p-4 lg:grid-cols-2">
            <div class="space-y-3">
              <div class="flex flex-wrap items-center gap-2">
                <label class="text-xs text-gray-500" for="preview-page">页码</label>
                <input
                  id="preview-page"
                  v-model.number="preview.page"
                  class="form-input w-20 py-1.5"
                  type="number"
                  min="1"
                />
                <select v-model="preview.variant" class="form-input w-32 py-1.5">
                  <option value="original">原件</option>
                  <option value="derived">识别用副本</option>
                </select>
              </div>
              <div class="overflow-hidden rounded-lg border border-gray-200 bg-gray-50">
                <img :src="previewUrl()" :alt="`${preview.filename} 第 ${preview.page} 页`" class="mx-auto max-h-[26rem] w-auto" />
              </div>
              <p class="text-xs text-gray-500">
                图像加载失败时说明该页不存在或尚未生成副本；原件始终保留，不会被覆盖。
              </p>
            </div>

            <div class="space-y-3">
              <h5 class="text-sm font-semibold text-gray-900">识别结果（与原文对照）</h5>
              <p
                v-if="preview.error"
                class="flex items-start gap-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900"
              >
                <CircleAlert :size="15" class="mt-0.5 shrink-0" />
                <span class="min-w-0 flex-1">{{ preview.error }}</span>
              </p>
              <ul v-if="preview.blocks.length" class="max-h-[24rem] space-y-2 overflow-y-auto pr-1">
                <li v-for="block in preview.blocks" :key="block.id" class="rounded-lg border border-gray-200 p-2">
                  <p class="text-sm text-gray-900">{{ block.text || '（空文本）' }}</p>
                  <p class="mt-1 flex flex-wrap gap-2 text-xs text-gray-500">
                    <span>置信度 {{ Number(block.confidence || 0).toFixed(2) }}</span>
                    <span v-if="block.is_handwritten" class="chip chip-warning">手写</span>
                    <span>{{ block.engine || '未记录' }}</span>
                    <span>bbox {{ bboxText(block.bbox) }}</span>
                  </p>
                </li>
              </ul>
              <p v-else-if="!preview.loading" class="text-sm text-gray-500">
                未记录该页识别结果；可先对材料运行识别。
              </p>
              <div class="flex items-center justify-between gap-2">
                <button class="btn-secondary px-3 py-1.5" :disabled="preview.blockPage <= 1" @click="changeBlockPage(preview.blockPage - 1)">
                  上一页
                </button>
                <span class="text-xs text-gray-500">
                  第 {{ preview.blockPage }} 页 · 共 {{ preview.blockTotal }} 条
                </span>
                <button
                  class="btn-secondary px-3 py-1.5"
                  :disabled="preview.blockPage * preview.blockPageSize >= preview.blockTotal"
                  @click="changeBlockPage(preview.blockPage + 1)"
                >
                  下一页
                </button>
              </div>
              <p class="text-xs text-gray-500">
                低置信度识别结果只作为疑点优先级，不代替人工辨认（R-21 / R-45）。
              </p>
            </div>
          </div>
        </div>
      </div>
    </Transition>
  </section>
</template>
