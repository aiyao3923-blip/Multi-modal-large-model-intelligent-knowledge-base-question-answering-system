import http from './http'

// The shared client already has baseURL /api. These requests never run on import.
// Keep errors local to VersionManager; the shared 401/logout handling still applies.
const requestConfig = ({ signal, onUploadProgress } = {}) => ({
  silent: true,
  ...(signal ? { signal } : {}),
  ...(onUploadProgress ? { onUploadProgress } : {}),
})

export const versionDetailApi = (versionId, options = {}) =>
  http.get(`/documents/${versionId}/version`, requestConfig(options))

export const seriesVersionsApi = (seriesId, { page = 1, size = 20, ...options } = {}) =>
  http.get(`/document-series/${seriesId}/versions`, {
    ...requestConfig(options), params: { page, size },
  })

export const versionRevisionsApi = (versionId, { page = 1, size = 20, ...options } = {}) =>
  http.get(`/documents/${versionId}/revisions`, {
    ...requestConfig(options), params: { page, size },
  })

// Frozen HTTP contract: expected_revision + reason + only changed fields (flat).
export const patchVersionMetadataApi = (versionId, payload, options = {}) =>
  http.patch(`/documents/${versionId}/metadata`, payload, requestConfig(options))

export const publishVersionApi = (versionId, payload, options = {}) =>
  http.post(`/documents/${versionId}/publish`, payload, requestConfig(options))

export const withdrawVersionApi = (versionId, payload, options = {}) =>
  http.post(`/documents/${versionId}/withdraw`, payload, requestConfig(options))

export const archiveVersionApi = (versionId, payload, options = {}) =>
  http.post(`/documents/${versionId}/archive`, payload, requestConfig(options))

// Do not set Content-Type manually: the browser supplies the multipart boundary.
export const uploadVersionApi = (seriesId, formData, options = {}) =>
  http.post(`/document-series/${seriesId}/versions`, formData, requestConfig(options))

export const reparseVersionApi = (kbId, versionId, options = {}) =>
  http.post(`/kbs/${kbId}/docs/${versionId}/reparse`, undefined, requestConfig(options))
