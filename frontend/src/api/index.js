import http from './http'
import { streamRequest } from './stream'

export const loginApi = (data) => http.post('/auth/login', data, { silent: true })
export const meApi = () => http.get('/auth/me', { silent: true })

export const kbsApi = () => http.get('/kbs')
export const createKbApi = (data) => http.post('/kbs', {
  name: data.name, description: data.description ?? '', role_codes: data.role_codes ?? [],
})
export const kbApi = (id) => http.get(`/kbs/${id}`)
export const patchKbApi = (id, data) => http.patch(`/kbs/${id}`, data)
export const grantKbApi = (id, roleCodes) => http.post(`/kbs/${id}/grant`, { role_codes: roleCodes })
export const kbStatsApi = (id) => http.get(`/kbs/${id}/stats`)

export const docsApi = (kbId, page = 1, size = 20, filters = {}) => http.get(`/kbs/${kbId}/docs`, { params: { page, size, ...filters } })
export const uploadDocApi = (kbId, formData) =>
  http.post(`/kbs/${kbId}/docs`, formData, { timeout: 120000 })
export const deleteDocApi = (kbId, docId) => http.delete(`/kbs/${kbId}/docs/${docId}`)
export const reparseDocApi = (kbId, docId) => http.post(`/kbs/${kbId}/docs/${docId}/reparse`)
export const tasksApi = (kbId, filters = {}) => http.get('/tasks', { params: { kb_id: kbId, ...filters } })
export const documentFileApi = async (kbId, docId) => {
  let blob = await http.get(`/kbs/${kbId}/docs/${docId}/file`, { params: { download: true }, responseType: 'blob', silent: true })
  if (blob instanceof Blob && blob.size === 0) {
    const data = await http.get(`/kbs/${kbId}/docs/${docId}/file`, { params: { encoding: 'base64' }, silent: true })
    if (!data || !Number.isSafeInteger(data.size) || data.size <= 0 || data.size > 50 * 1024 * 1024 || typeof data.content_base64 !== 'string') throw new Error('Invalid document response')
    const decoded = atob(data.content_base64)
    if (decoded.length !== data.size) throw new Error('Incomplete document response')
    blob = new Blob([Uint8Array.from(decoded, char => char.charCodeAt(0))], { type: 'application/octet-stream' })
  }
  // A binary download avoids browser PDF handlers consuming an inline XHR body.
  // Only a verified PDF signature is promoted to embeddable MIME; HTML stays inert.
  if (blob instanceof Blob && blob.size >= 5 && await blob.slice(0, 5).text() === '%PDF-') return new Blob([blob], { type: 'application/pdf' })
  return blob
}
export const chunksApi = (docId) => http.get(`/docs/${docId}/chunks`)

export const sessionsApi = () => http.get('/qa/sessions')
export const createSessionApi = (data) => http.post('/qa/sessions', data)
export const renameSessionApi = (id, title) => http.patch(`/qa/sessions/${id}`, null, { params: { title } })
export const deleteSessionApi = (id) => http.delete(`/qa/sessions/${id}`)
export const askApi = (data, options = {}) => streamRequest('/qa/ask/stream', data, options)
export const translateApi = (data, options = {}) => streamRequest('/qa/translate/stream', data, options)
export const sessionMessagesApi = (id) => http.get(`/qa/sessions/${id}/messages`)
export const feedbackApi = (data) => http.post('/qa/feedback', data)

export const feedbacksApi = (status) => http.get('/qa/feedbacks', { params: { status } })
export const handleFeedbackApi = (id, action, note) =>
  http.post(`/qa/feedbacks/${id}/handle`, null, { params: { action, note } })

export const statsOverviewApi = () => http.get('/stats/overview')
export const statsTrendApi = (kbId) => http.get('/stats/trend', { params: { kb_id: kbId } })

export const usersApi = () => http.get('/sys/users')
export const createUserApi = (data) => http.post('/sys/users', data)
export const patchUserApi = (id, data) => http.patch(`/sys/users/${id}`, data)
export const rolesApi = () => http.get('/sys/roles')
export const channelsApi = () => http.get('/sys/channels')
export const createChannelApi = (data) => http.post('/sys/channels', data)
export const patchChannelApi = (id, data) => http.patch(`/sys/channels/${id}`, data)
export const channelHealthApi = (id) => http.post(`/sys/channels/${id}/health`)
export const paramsApi = () => http.get('/sys/params')
export const patchParamApi = (key, value) => http.patch(`/sys/params/${key}`, null, { params: { value } })

export const capabilitiesApi = () => http.get('/qa/capabilities')
export const tasksPageApi = (params) => http.get('/tasks/page', { params })
export const feedbacksPageApi = (params) => http.get('/qa/feedbacks/page', { params })
