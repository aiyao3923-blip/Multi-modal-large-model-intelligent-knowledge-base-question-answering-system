import http from './http'
import { useUserStore } from '@/stores/user'
import router from '@/router'

const stages = new Set(['retrieving', 'generating', 'validating', 'translating'])
const abortError = () => new DOMException('请求已停止', 'AbortError')
function checkAbort(signal) { if (signal?.aborted) throw abortError() }
function streamError(detail, status = 502, code = 'ERR_STREAM_PROTOCOL') {
  const error = new Error(detail)
  error.code = code
  error.detail = detail
  if (status) error.response = { status, data: { detail } }
  return error
}
const protocolError = () => streamError('响应流格式不完整或顺序异常，请先刷新核对记录。')

// Progress is metadata, never model text. Commit the single complete result only
// after clean EOF; a second terminal or trailing event invalidates the response.
export async function readNdjson(body, { signal, onProgress } = {}) {
  checkAbort(signal)
  if (!body || typeof body.getReader !== 'function') throw protocolError()
  const reader = body.getReader(), decoder = new TextDecoder('utf-8', { fatal: true })
  let buffer = '', terminal = false, result, complete = false, cancelled = false
  let rejectAbort
  const aborted = new Promise((_, reject) => { rejectAbort = reject })
  const cancel = () => {
    if (cancelled) return
    cancelled = true
    // Do not wait for a stalled source's cancellation handshake to release the UI.
    try { void reader.cancel().catch(() => {}) } catch { /* Already closed. */ }
  }
  const onAbort = () => { rejectAbort(abortError()); cancel() }
  signal?.addEventListener('abort', onAbort, { once: true })

  function consumeLine(line) {
    checkAbort(signal)
    if (!line.trim()) return
    if (terminal) throw protocolError()
    let item
    try { item = JSON.parse(line) } catch { throw protocolError() }
    if (!item || typeof item !== 'object' || Array.isArray(item)) throw protocolError()
    if (item.event === 'result') {
      if (!item.data || typeof item.data !== 'object' || Array.isArray(item.data)) throw protocolError()
      terminal = true
      result = item.data
      return
    }
    if (item.event === 'error') {
      if (!Number.isInteger(item.status) || item.status < 400 || item.status > 599 || typeof item.detail !== 'string' || !item.detail.trim()) throw protocolError()
      throw streamError(item.detail, item.status, 'ERR_STREAM_RESPONSE')
    }
    if (item.event !== 'status' && item.event !== 'heartbeat') throw protocolError()
    if (typeof item.stage !== 'string' || !item.stage || (item.event === 'status' && !stages.has(item.stage))) throw protocolError()
    if (item.received_chars !== undefined && (!Number.isSafeInteger(item.received_chars) || item.received_chars < 0)) throw protocolError()
    if (item.event === 'heartbeat' && (!Number.isFinite(item.elapsed_seconds) || item.elapsed_seconds < 0)) throw protocolError()
    const progress = { event: item.event, stage: item.stage }
    if (item.received_chars !== undefined) progress.received_chars = item.received_chars
    if (item.elapsed_seconds !== undefined) progress.elapsed_seconds = item.elapsed_seconds
    onProgress?.(progress)
  }
  function consumeBuffer() {
    let start = 0, end
    while ((end = buffer.indexOf('\n', start)) !== -1) {
      consumeLine(buffer.slice(start, end))
      start = end + 1
    }
    buffer = buffer.slice(start)
  }
  async function read() {
    while (true) {
      checkAbort(signal)
      const { value, done } = await reader.read()
      checkAbort(signal)
      try { buffer += done ? decoder.decode() : decoder.decode(value, { stream: true }) } catch { throw protocolError() }
      consumeBuffer()
      if (done) break
    }
    consumeLine(buffer)
    if (!terminal) throw streamError('连接已结束，但未收到完整结果，请先刷新核对记录。')
    complete = true
    return result
  }
  try {
    if (signal?.aborted) onAbort()
    return await Promise.race([read(), aborted])
  } catch (error) {
    if (signal?.aborted || error?.name === 'AbortError') throw abortError()
    if (error?.response) throw error
    throw protocolError()
  } finally {
    signal?.removeEventListener('abort', onAbort)
    if (!complete) cancel()
    try { reader.releaseLock() } catch { /* Cancellation may have released the reader. */ }
  }
}

function requestUrl(path) {
  // Bearer credentials must never follow a configurable base URL off origin or
  // escape /api. Refuse redirects too, even when the first destination is safe.
  const origin = window.location.origin
  let base, url
  try {
    base = new URL(http.defaults.baseURL || '/api', origin + '/')
    if (typeof path !== 'string' || !path.startsWith('/') || path.startsWith('//') || /[\\?#]/.test(path)) throw new Error()
    url = new URL(base.pathname.replace(/\/+$/, '') + path, origin)
  } catch { throw streamError('已拒绝不安全的流请求地址。', 400, 'ERR_STREAM_ORIGIN') }
  if (base.origin !== origin || base.username || base.password || base.search || base.hash
    || (base.pathname !== '/api' && !base.pathname.startsWith('/api/'))
    || url.origin !== origin || !url.pathname.startsWith('/api/')) {
    throw streamError('已拒绝不安全的流请求地址。', 400, 'ERR_STREAM_ORIGIN')
  }
  return url.href
}

export async function streamRequest(path, data, { signal, onProgress } = {}) {
  checkAbort(signal)
  const url = requestUrl(path), sessionToken = useUserStore().token
  const headers = { Accept: 'application/x-ndjson', 'Content-Type': 'application/json' }
  if (sessionToken) headers.Authorization = 'Bearer ' + sessionToken
  let response
  try {
    response = await fetch(url, {
      method: 'POST', headers, body: JSON.stringify(data), signal,
      credentials: 'same-origin', redirect: 'error', cache: 'no-store',
    })
    checkAbort(signal)
    if (!response.ok) {
      let payload
      try { payload = await response.json() } catch { /* Use a safe fallback, not the raw response. */ }
      checkAbort(signal)
      const detail = typeof payload?.detail === 'string' && payload.detail.trim() ? payload.detail
        : response.status === 422 ? '请检查输入内容是否符合要求。' : '请求未完成，请稍后重试。'
      throw streamError(detail, response.status, 'ERR_STREAM_RESPONSE')
    }
    if (response.headers.get('content-type')?.split(';')[0].trim().toLowerCase() !== 'application/x-ndjson') throw protocolError()
    return await readNdjson(response.body, { signal, onProgress })
  } catch (error) {
    try { if (response?.body && !response.body.locked) void response.body.cancel().catch(() => {}) } catch { /* Already consumed. */ }
    if (signal?.aborted || error?.name === 'AbortError') throw abortError()
    if (error?.response?.status === 401) {
      const user = useUserStore()
      // Match http.js: a late rejection for an old session cannot log out a new one.
      if (user.token === sessionToken) {
        user.logout()
        if (router.currentRoute.value.path !== '/login') router.replace({ path: '/login', query: { redirect: router.currentRoute.value.fullPath } })
      }
    }
    if (error?.response) throw error
    throw streamError('无法连接服务，请确认后端已启动。', null, 'ERR_NETWORK')
  }
}
