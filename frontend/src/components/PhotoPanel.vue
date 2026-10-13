<script setup>
/**
 * PhotoPanel.vue —— 部位照片（N-11 / R-54～R-58）。
 *
 * 硬性要求：
 * 1. 上传（kind="photo"）+ 缩略图 + **用户自选目标位置** + 照片说明 + 顺序调整；
 * 2. 位置只能从 /api/config 的 photo_positions 目录中选择；
 * 3. 界面显著标注「系统不读取照片内容，只按你选择的位置插入」；
 * 4. 位置指派走 api.updatePhotoPlacement(id, materialId, payload)
 *    → PATCH /api/cases/{id}/materials/{material_id}/placement；
 *    缩略图走 api.materialFileUrl(id, materialId, page, variant)（api.js 已封装，不新增方法）。
 * 5. 未指定位置的照片不会进入文稿，并产生 PHOTO_POSITION_MISSING 警告（R-57）；
 *    可用 api.photoSlots(id) 读取照片栏位视图核对缺失栏位。
 */
import { computed, reactive, ref } from 'vue'
import { Camera, CircleAlert, Eye, MapPin, RefreshCw, Upload, X } from 'lucide-vue-next'
import { api } from '../api.js'

const props = defineProps({
  caseId: { type: Number, default: 0 },
  caseVersion: { type: Number, default: 1 },
  materials: { type: Array, default: () => [] },
  photoGroups: { type: Array, default: () => [] },
  dict: { type: Object, default: () => ({}) },
})

const emit = defineEmits(['refresh', 'notify', 'error'])

const registerForm = reactive({ filename: '', position_key: '', photo_caption: '' })
const selectedFile = ref(null)
const uploadBusy = ref(false)
const placementBusyId = ref(0)
const previewMaterial = ref(null)
const captionDraft = reactive({})

const photos = computed(() => {
  const rows = Array.isArray(props.materials) ? props.materials.slice() : []
  rows.sort((a, b) => {
    const orderA = Number(a.sort_order || 0)
    const orderB = Number(b.sort_order || 0)
    if (orderA !== orderB) return orderA - orderB
    return Number(a.id || 0) - Number(b.id || 0)
  })
  return rows
})

const positions = computed(() => {
  const rows = []
  ;(props.photoGroups || []).forEach((group) => {
    ;(group.options || []).forEach((option) => rows.push({ ...option, sectionLabel: group.label }))
  })
  return rows
})

const allPositionOptions = computed(() => {
  if (positions.value.length) return positions.value
  return []
})

const missingCount = computed(() => photos.value.filter((item) => !item.position_key).length)

const sectionLabels = computed(() => props.dict?.photoSectionLabels || {})

function positionLabel(key) {
  if (!key) return '未指定位置'
  const hit = allPositionOptions.value.find((option) => option.key === key)
  return hit ? hit.label : key
}

function thumbnailUrl(material) {
  return api.materialFileUrl(props.caseId, material.id, 1, 'original')
}

function detectMediaType(filename) {
  const name = String(filename || '').toLowerCase()
  if (name.endsWith('.pdf')) return 'pdf'
  if (name.endsWith('.png')) return 'png'
  if (name.endsWith('.jpg') || name.endsWith('.jpeg')) return 'jpeg'
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
}

async function uploadPhoto() {
  if (!props.caseId) return
  if (!registerForm.filename.trim()) {
    emit('notify', '请先选择照片文件', 'error')
    return
  }
  if (!registerForm.position_key) {
    emit('notify', '请先为该照片选择目标位置；未指定位置的照片不会进入文稿（R-57）', 'error')
    return
  }
  uploadBusy.value = true
  try {
    let contentBase64 = ''
    if (selectedFile.value) contentBase64 = await readAsBase64(selectedFile.value)
    const payload = {
      // 乐观锁：POST /api/cases/{id}/materials 的 expected_version 在请求体（MaterialCreate）。
      expected_version: props.caseVersion,
      kind: 'photo',
      filename: registerForm.filename.trim(),
      media_type: detectMediaType(registerForm.filename),
      page_count: 1,
      position_key: registerForm.position_key,
      photo_caption: registerForm.photo_caption.trim(),
      sort_order: photos.value.length ? Number(photos.value[photos.value.length - 1].sort_order || 0) + 10 : 10,
    }
    if (contentBase64) payload.content_base64 = contentBase64
    await api.addMaterial(props.caseId, payload)
    emit('notify', `照片已上传并指派到「${positionLabel(payload.position_key)}」（系统未解析照片内容）`, 'success')
    selectedFile.value = null
    registerForm.filename = ''
    registerForm.photo_caption = ''
    emit('refresh')
  } catch (error) {
    emit('error', error)
  } finally {
    uploadBusy.value = false
  }
}

async function savePlacement(material, patch, successText) {
  if (!props.caseId) return
  placementBusyId.value = material.id
  try {
    await api.updatePhotoPlacement(props.caseId, material.id, {
      expected_version: props.caseVersion,
      ...patch,
    })
    emit('notify', successText, 'success')
    emit('refresh')
  } catch (error) {
    emit('error', error)
  } finally {
    placementBusyId.value = 0
  }
}

function onPositionChange(material, event) {
  const key = event.target.value
  savePlacement(material, { position_key: key }, `已指派位置：${positionLabel(key)}（系统只按位置插入，不读取照片内容）`)
}

function onCaptionBlur(material) {
  const next = String(captionDraft[material.id] ?? material.photo_caption ?? '').trim()
  if (next === String(material.photo_caption || '')) return
  savePlacement(material, { photo_caption: next }, '照片说明已保存')
}

function onOrderChange(material, event) {
  const value = Number(event.target.value)
  if (!Number.isFinite(value) || value < 0) return
  savePlacement(material, { sort_order: value }, `次序已调整为 ${value}`)
}

/** 上移/下移：只写目标照片自己的 sort_order（避免同一次操作产生多次版本冲突）。 */
function move(material, direction) {
  const index = photos.value.findIndex((item) => item.id === material.id)
  if (index < 0) return
  const neighbor = direction < 0 ? photos.value[index - 1] : photos.value[index + 1]
  if (!neighbor) return
  const neighborOrder = Number(neighbor.sort_order || 0)
  const currentOrder = Number(material.sort_order || 0)
  let target = direction < 0 ? neighborOrder - 1 : neighborOrder + 1
  if (target < 0) target = 0
  if (target === currentOrder) target = direction < 0 ? currentOrder - 1 : currentOrder + 1
  savePlacement(material, { sort_order: Math.max(0, target) }, `次序已调整（第 ${Math.max(0, target)} 位）`)
}

function openPreview(material) {
  previewMaterial.value = material
}

function closePreview() {
  previewMaterial.value = null
}

function previewUrl() {
  if (!previewMaterial.value) return ''
  return api.materialFileUrl(props.caseId, previewMaterial.value.id, 1, 'original')
}

/* -------- 照片栏位视图（GET /api/cases/{id}/photo-slots，R-57） -------- */
const slots = ref(null)
const slotsBusy = ref(false)
const slotsError = ref('')

const slotRows = computed(() => {
  const raw = slots.value
  if (!raw) return []
  const list = Array.isArray(raw) ? raw : Array.isArray(raw.slots) ? raw.slots : []
  return list
    .map((item) => ({
      key: String(item.position_key || item.key || ''),
      label: String(item.label || positionLabel(item.position_key || item.key)),
      sectionLabel: String(item.section_label || ''),
      anchor: String(item.anchor || `photo_${item.position_key || item.key || ''}`),
      order: Number(item.order || 0),
      missing: item.missing === undefined ? !(Array.isArray(item.photos) ? item.photos.length : Number(item.count || 0)) : !!item.missing,
      count: Number(item.count ?? (Array.isArray(item.photos) ? item.photos.length : 0)),
      photos: Array.isArray(item.photos) ? item.photos : [],
    }))
    .filter((item) => item.key)
    .sort((a, b) => a.order - b.order)
})

const slotMissingCount = computed(() => slotRows.value.filter((row) => row.missing || !row.count).length)

/* 实测冻结形状：{photo_policy, placeholder_text, slots:[...], missing_count, assigned_count} */
const slotPlaceholderText = computed(() =>
  (slots.value && slots.value.placeholder_text) || '（未提供照片）')
const slotPolicyText = computed(() => (slots.value && slots.value.photo_policy) || '')
const slotAssignedCount = computed(() => {
  const raw = slots.value
  if (!raw || raw.assigned_count === undefined || raw.assigned_count === null) return null
  return Number(raw.assigned_count) || 0
})

async function loadSlots() {
  if (!props.caseId) return
  slotsBusy.value = true
  slotsError.value = ''
  try {
    slots.value = await api.photoSlots(props.caseId)
  } catch (error) {
    slots.value = null
    slotsError.value = error && error.message ? error.message : '照片栏位读取失败'
  } finally {
    slotsBusy.value = false
  }
}
</script>

<template>
  <section class="panel" aria-label="部位照片">
    <header class="border-b border-gray-200 p-4">
      <h3 class="flex items-center gap-2 text-sm font-semibold text-gray-950">
        <Camera :size="16" />
        部位照片（上传 → 选目标位置 → 调顺序）
      </h3>
      <p class="mt-2 flex items-start gap-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm font-medium text-amber-900">
        <CircleAlert :size="17" class="mt-0.5 shrink-0" />
        <span>
          <strong>系统不读取照片内容，只按你选择的位置插入</strong>（N-11 / R-55）。系统不会识别、描述或推断照片内容，
          也不会对照片运行识别或页面渲染推理；位置只能从位置目录中选择（R-58）。
        </span>
      </p>
    </header>

    <div class="grid gap-3 border-b border-gray-200 p-4 lg:grid-cols-12">
      <label class="lg:col-span-4">
        <span class="mb-1 block text-xs font-medium text-gray-500">照片文件（JPG / PNG / PDF）</span>
        <input class="form-input file:mr-2 file:rounded file:border-0 file:bg-gray-100 file:px-2 file:py-1" type="file" accept=".jpg,.jpeg,.png,.pdf" @change="onFileChange" />
      </label>
      <label class="lg:col-span-3">
        <span class="mb-1 block text-xs font-medium text-gray-500">目标位置（必选，来自位置目录）</span>
        <select v-model="registerForm.position_key" class="form-input">
          <option value="">请选择位置</option>
          <optgroup
            v-for="group in (photoGroups && photoGroups.length ? photoGroups : [])"
            :key="group.section"
            :label="group.label || sectionLabels[group.section] || group.section"
          >
            <option v-for="option in group.options" :key="option.key" :value="option.key">
              {{ option.label }}（{{ option.anchor }}）
            </option>
          </optgroup>
        </select>
      </label>
      <label class="lg:col-span-3">
        <span class="mb-1 block text-xs font-medium text-gray-500">照片说明（由你填写，系统不生成）</span>
        <input v-model="registerForm.photo_caption" class="form-input" type="text" placeholder="例如：左前臂尺侧，现场拍摄" />
      </label>
      <div class="flex items-end lg:col-span-2">
        <button class="btn-primary w-full" :disabled="uploadBusy" @click="uploadPhoto">
          <RefreshCw v-if="uploadBusy" :size="16" class="animate-spin" />
          <Upload v-else :size="16" />
          上传照片
        </button>
      </div>
      <p v-if="!allPositionOptions.length" class="lg:col-span-12 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
        /api/config 未返回 photo_positions 位置目录，当前无法选择目标位置；请检查配置接口。
      </p>
    </div>

    <div class="flex flex-wrap items-center justify-between gap-2 border-b border-gray-200 px-4 py-3">
      <p class="text-sm text-gray-600">
        共 <span class="font-semibold tabular-nums">{{ photos.length }}</span> 张照片
        <span v-if="missingCount" class="ml-2 text-amber-800">
          · {{ missingCount }} 张未指定位置（不会进入文稿，将产生 PHOTO_POSITION_MISSING 警告）
        </span>
      </p>
      <button class="btn-secondary" :disabled="!photos.length" @click="emit('refresh')">
        <RefreshCw :size="16" />
        刷新
      </button>
      <button class="btn-secondary" :disabled="slotsBusy" @click="loadSlots">
        <RefreshCw v-if="slotsBusy" :size="16" class="animate-spin" />
        读取照片栏位视图
      </button>
    </div>

    <div v-if="slots || slotsError" class="border-b border-gray-200 p-4">
      <p v-if="slotsError" class="flex items-start gap-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900">
        <CircleAlert :size="15" class="mt-0.5 shrink-0" />
        <span class="min-w-0 flex-1">{{ slotsError }}</span>
      </p>
      <template v-if="slotRows.length">
        <p class="text-xs text-gray-600">
          照片栏位视图：共 {{ slotRows.length }} 个栏位（已指派
          {{ slotAssignedCount === null ? '未记录' : `${slotAssignedCount} 张` }}，空栏位
          <span class="font-semibold text-amber-800">{{ slotMissingCount }}</span>
          个）。本次没有照片的栏位在文稿中写入「{{ slotPlaceholderText }}」，不会用其他栏位的照片顶替（N-12 / R-57）。
        </p>
        <p v-if="slotPolicyText" class="mt-1 text-xs text-gray-500">服务端照片策略：{{ slotPolicyText }}</p>
        <ul class="mt-2 flex flex-wrap gap-2">
          <li
            v-for="row in slotRows"
            :key="row.key"
            class="chip"
            :class="row.count ? 'chip-recorded' : 'chip-warning'"
          >
            {{ row.label }}（{{ row.anchor }}）{{ row.count ? `${row.count} 张` : `空栏位 → ${slotPlaceholderText}` }}
            <span v-if="row.sectionLabel" class="text-gray-500">· {{ row.sectionLabel }}</span>
          </li>
        </ul>
      </template>
      <p v-else-if="!slotsError" class="text-xs text-gray-500">栏位视图未返回位置项（未记录）。</p>
    </div>

    <div class="p-4">
      <p v-if="!photos.length" class="py-8 text-center text-sm text-gray-500">
        尚未上传部位照片（未记录）。上传时请为每张照片选择一个目标位置。
      </p>

      <div v-else class="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        <article v-for="material in photos" :key="material.id" class="rounded-lg border border-gray-200 p-3">
          <div class="aspect-[4/3] overflow-hidden rounded-lg border border-gray-200 bg-gray-50">
            <img
              :src="thumbnailUrl(material)"
              :alt="material.photo_caption || material.filename"
              class="h-full w-full object-contain"
              @click="openPreview(material)"
            />
          </div>

          <p class="mt-2 truncate text-sm font-medium text-gray-950" :title="material.filename">
            {{ material.filename || `照片 #${material.id}` }}
          </p>

          <label class="mt-2 block">
            <span class="mb-1 block text-xs font-medium text-gray-500">目标位置</span>
            <select
              class="form-input py-1.5"
              :value="material.position_key || ''"
              :disabled="placementBusyId === material.id"
              @change="onPositionChange(material, $event)"
            >
              <option value="">未指定位置（不会进入文稿）</option>
              <optgroup
                v-for="group in (photoGroups && photoGroups.length ? photoGroups : [])"
                :key="group.section"
                :label="group.label || sectionLabels[group.section] || group.section"
              >
                <option v-for="option in group.options" :key="option.key" :value="option.key">
                  {{ option.label }}
                </option>
              </optgroup>
            </select>
          </label>

          <p v-if="!material.position_key" class="mt-1 flex items-center gap-1 text-xs font-medium text-amber-800">
            <CircleAlert :size="13" />
            未指定位置
          </p>
          <p v-else class="mt-1 flex items-center gap-1 text-xs text-gray-600">
            <MapPin :size="13" />
            {{ positionLabel(material.position_key) }} · 锚点 {{ material.position_key ? `photo_${material.position_key}` : '未记录' }}
          </p>

          <label class="mt-2 block">
            <span class="mb-1 block text-xs font-medium text-gray-500">照片说明</span>
            <input
              v-model="captionDraft[material.id]"
              class="form-input py-1.5"
              type="text"
              :placeholder="material.photo_caption || '未填写说明（未记录）'"
              @blur="onCaptionBlur(material)"
            />
          </label>

          <div class="mt-2 flex items-end gap-2">
            <label class="w-24">
              <span class="mb-1 block text-xs font-medium text-gray-500">次序</span>
              <input
                class="form-input py-1.5"
                type="number"
                min="0"
                :value="Number(material.sort_order || 0)"
                :disabled="placementBusyId === material.id"
                @change="onOrderChange(material, $event)"
              />
            </label>
            <div class="flex flex-1 items-center justify-end gap-1">
              <button
                class="btn-secondary px-2 py-1.5"
                :disabled="placementBusyId === material.id"
                title="上移一位"
                @click="move(material, -1)"
              >
                ↑
              </button>
              <button
                class="btn-secondary px-2 py-1.5"
                :disabled="placementBusyId === material.id"
                title="下移一位"
                @click="move(material, 1)"
              >
                ↓
              </button>
              <button class="btn-secondary px-2 py-1.5" @click="openPreview(material)">
                <Eye :size="15" />
              </button>
            </div>
          </div>
        </article>
      </div>

      <p class="mt-4 text-xs text-gray-500">
        生成文稿时按位置栏位插入照片（同一位置多张按次序从小到大），保持纵横比、不裁切照片内容（R-56）。
        顺序调整每次只写入一张照片的次序，写操作均带 expected_version，冲突会提示刷新后重试。
      </p>
    </div>

    <Transition name="fade">
      <div
        v-if="previewMaterial"
        class="fixed inset-0 z-[65] flex items-center justify-center bg-gray-950/50 p-4"
        @click.self="closePreview"
      >
        <div class="panel w-full max-w-2xl p-4">
          <header class="flex items-start justify-between gap-3">
            <div class="min-w-0">
              <h4 class="truncate text-sm font-semibold text-gray-950">{{ previewMaterial.filename || '照片预览' }}</h4>
              <p class="mt-0.5 text-xs text-gray-500">
                系统未解析该照片内容；下方位置与说明均由你指定。
              </p>
            </div>
            <button class="cursor-pointer rounded-lg p-2 text-gray-500 hover:bg-gray-100" aria-label="关闭" @click="closePreview">
              <X :size="18" />
            </button>
          </header>
          <img :src="previewUrl()" :alt="previewMaterial.filename" class="mt-3 max-h-[70vh] w-full object-contain" />
          <p class="mt-2 text-xs text-gray-600">
            目标位置：{{ positionLabel(previewMaterial.position_key) }} · 说明：
            {{ previewMaterial.photo_caption || '未记录' }} · 次序 {{ Number(previewMaterial.sort_order || 0) }}
          </p>
        </div>
      </div>
    </Transition>
  </section>
</template>
