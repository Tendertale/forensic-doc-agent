/**
 * 无构建依赖的前端接口层（frontend/static/api.js）。
 *
 * 与已冻结的 `frontend/src/api.js` **端点、方法名、参数顺序完全一致**，
 * 唯一差别是本文件不依赖打包器，因此不能使用 `import.meta.env`：
 * 基址固定为同源 `/api`（由 FastAPI 提供，见 docs/architecture.md 第 7.3 节）。
 *
 * 契约约定（第 4 节）：
 * - 所有写操作必须携带 `expected_version`；POST/PATCH 放请求体，DELETE 放查询参数。
 * - 非 2xx 一律抛出带 `status` / `message` 的 Error；409 由调用方触发“刷新后重试”。
 */

const API_BASE = '/api'

function buildQuery(filters = {}) {
  const params = new URLSearchParams()
  Object.entries(filters).forEach(([key, value]) => {
    if (value === '' || value === null || value === undefined) return
    if (Array.isArray(value)) {
      value.forEach((item) => params.append(key, item))
      return
    }
    params.append(key, String(value))
  })
  const query = params.toString()
  return query ? `?${query}` : ''
}

async function request(path, options = {}) {
  const { headers = {}, ...requestOptions } = options
  const response = await fetch(`${API_BASE}${path}`, {
    ...requestOptions,
    headers: {
      'Content-Type': 'application/json',
      ...headers,
    },
  })

  if (!response.ok) {
    let message = `请求失败 (${response.status})`
    try {
      const payload = await response.json()
      if (typeof payload.detail === 'string') {
        message = payload.detail
      } else if (Array.isArray(payload.detail)) {
        message = payload.detail
          .map((item) => (item && item.msg ? item.msg : JSON.stringify(item)))
          .join('；')
      }
    } catch {
      // 响应没有 JSON 正文时保留 HTTP 兜底文案。
    }
    const error = new Error(message)
    error.status = response.status
    throw error
  }

  if (response.status === 204) return null
  const contentType = response.headers.get('content-type') || ''
  if (!contentType.includes('application/json')) return response
  return response.json()
}

export const api = {
  health: () => request('/health'),
  config: () => request('/config'),
  stats: () => request('/stats'),

  verifyAi: (apiKey) =>
    request('/ai/verify', { method: 'POST', headers: { 'X-DeepSeek-API-Key': apiKey } }),

  listCases: (filters = {}) => request(`/cases${buildQuery(filters)}`),
  case: (id) => request(`/cases/${id}`),
  createCase: (payload) => request('/cases', { method: 'POST', body: JSON.stringify(payload) }),
  updateCase: (id, payload) => request(`/cases/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  updateStage: (id, payload) => request(`/cases/${id}/stage`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteCase: (id, expectedVersion) =>
    request(`/cases/${id}${buildQuery({ expected_version: expectedVersion })}`, { method: 'DELETE' }),

  addMaterial: (id, payload) => request(`/cases/${id}/materials`, { method: 'POST', body: JSON.stringify(payload) }),
  listMaterials: (id) => request(`/cases/${id}/materials`),
  updateMaterialPage: (id, materialId, pageNumber, payload) =>
    request(`/cases/${id}/materials/${materialId}/pages/${pageNumber}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  preprocess: (id, materialId, payload) =>
    request(`/cases/${id}/materials/${materialId}/preprocess`, { method: 'POST', body: JSON.stringify(payload) }),
  materialFileUrl: (id, materialId, page = 1, variant = 'original') =>
    `${API_BASE}/cases/${id}/materials/${materialId}/file${buildQuery({ page, variant })}`,

  // 部位照片：只写目标位置元数据，不读取照片内容（N-11）
  updatePhotoPlacement: (id, materialId, payload) =>
    request(`/cases/${id}/materials/${materialId}/placement`, { method: 'PATCH', body: JSON.stringify(payload) }),
  photoSlots: (id) => request(`/cases/${id}/photo-slots`),

  runOcr: (id, payload) => request(`/cases/${id}/ocr`, { method: 'POST', body: JSON.stringify(payload) }),
  ocrBlocks: (id, filters = {}) => request(`/cases/${id}/ocr-blocks${buildQuery(filters)}`),

  runExtract: (id, payload) => request(`/cases/${id}/extract`, { method: 'POST', body: JSON.stringify(payload) }),
  fields: (id) => request(`/cases/${id}/fields`),
  updateField: (id, fieldKey, payload) =>
    request(`/cases/${id}/fields/${encodeURIComponent(fieldKey)}`, { method: 'PATCH', body: JSON.stringify(payload) }),

  injuries: (id) => request(`/cases/${id}/injuries`),
  createInjury: (id, payload) => request(`/cases/${id}/injuries`, { method: 'POST', body: JSON.stringify(payload) }),
  updateInjury: (id, injuryId, payload) =>
    request(`/cases/${id}/injuries/${injuryId}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteInjury: (id, injuryId, expectedVersion) =>
    request(`/cases/${id}/injuries/${injuryId}${buildQuery({ expected_version: expectedVersion })}`, {
      method: 'DELETE',
    }),
  reorderInjuries: (id, orderedIds, expectedVersion) =>
    request(`/cases/${id}/injuries/reorder${buildQuery({ expected_version: expectedVersion })}`, {
      method: 'POST',
      body: JSON.stringify({ ids: orderedIds }),
    }),

  runNormalize: (id, payload) => request(`/cases/${id}/normalize`, { method: 'POST', body: JSON.stringify(payload) }),

  rules: (filters = {}) => request(`/terminology-rules${buildQuery(filters)}`),
  createRule: (payload) => request('/terminology-rules', { method: 'POST', body: JSON.stringify(payload) }),
  updateRule: (ruleId, payload) =>
    request(`/terminology-rules/${ruleId}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteRule: (ruleId, expectedVersion) =>
    request(`/terminology-rules/${ruleId}${buildQuery({ expected_version: expectedVersion })}`, { method: 'DELETE' }),

  createDocument: (id, payload) => request(`/cases/${id}/documents`, { method: 'POST', body: JSON.stringify(payload) }),
  documents: (id) => request(`/cases/${id}/documents`),
  approveDocument: (documentId, payload) =>
    request(`/documents/${documentId}/approve`, { method: 'POST', body: JSON.stringify(payload) }),

  runCheck: (id, payload) => request(`/cases/${id}/check`, { method: 'POST', body: JSON.stringify(payload) }),
  checklist: (id, filters = {}) => request(`/cases/${id}/checklist${buildQuery(filters)}`),
  updateChecklistItem: (itemId, payload) =>
    request(`/checklist/${itemId}`, { method: 'PATCH', body: JSON.stringify(payload) }),

  createCorrection: (id, payload) =>
    request(`/cases/${id}/corrections`, { method: 'POST', body: JSON.stringify(payload) }),
  corrections: (id) => request(`/cases/${id}/corrections`),

  deliver: (id, payload) => request(`/cases/${id}/deliver`, { method: 'POST', body: JSON.stringify(payload) }),

  downloadUrl: (documentId, fmt) => `${API_BASE}/documents/${documentId}/download?fmt=${fmt}`,
}

export default api
