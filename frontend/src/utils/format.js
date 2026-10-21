export const roleNames = { admin: '系统管理员', operator: '知识库运营员', user: '问答用户' }
export const modeNames = { local: '本地模型', online: '在线模型', degraded: '仅检索证据', none: '未找到依据' }
export const statusNames = { enabled: '已启用', disabled: '已停用', created: '待启用', pending: '待解析', parsing: '解析中', queued: '排队中', extracting: '内容抽取', chunking: '文档分段', vectorizing: '构建索引', success: '已完成', failed: '处理失败', handled: '已处理', closed: '已关闭' }
export function parseJson(value, fallback = []) { try { return JSON.parse(value || 'null') ?? fallback } catch { return fallback } }
export function formatNumber(value) { return new Intl.NumberFormat('zh-CN').format(Number(value) || 0) }
export function formatBytes(value) { const n = Number(value) || 0; return n < 1024 ? n + ' B' : n < 1048576 ? (n / 1024).toFixed(1) + ' KB' : (n / 1048576).toFixed(1) + ' MB' }
export function formatDate(value, short = false) {
  if (!value) return '—'
  const normalized = /(?:Z|[+-]\d{2}:?\d{2})$/.test(value) ? value : value.replace(' ', 'T') + 'Z'
  const d = new Date(normalized)
  if (Number.isNaN(d.getTime())) return '—'
  return new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', ...(short ? {} : { hour: '2-digit', minute: '2-digit', hour12: false }) }).format(d)
}
export function downloadText(name, content, type = 'text/markdown;charset=utf-8') {
  const url = URL.createObjectURL(new Blob([content], { type }))
  const a = document.createElement('a')
  a.href = url
  a.download = name
  a.hidden = true
  // Attach for browsers that ignore downloads from detached anchors.
  document.body.appendChild(a)
  try { a.click() } finally {
    a.remove()
    // Give the browser time to begin consuming the blob before releasing it.
    setTimeout(() => URL.revokeObjectURL(url), 30000)
  }
}
