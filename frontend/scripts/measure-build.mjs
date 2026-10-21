import { readFile, writeFile } from 'node:fs/promises'
import { gzipSync } from 'node:zlib'
const dist = new URL('../dist/', import.meta.url)
const manifest = JSON.parse(await readFile(new URL('.vite/manifest.json', dist), 'utf8'))
const pages = {
  '登录': ['src/views/Login.vue'],
  '智能问答': ['src/views/chat/ChatLayout.vue', 'src/views/chat/ChatView.vue'],
  '工作概览': ['src/views/admin/AdminLayout.vue', 'src/views/admin/Overview.vue'],
  '知识库管理': ['src/views/admin/AdminLayout.vue', 'src/views/admin/KbList.vue'],
  '文档与解析': ['src/views/admin/AdminLayout.vue', 'src/views/admin/KbDetail.vue'],
  '运营统计': ['src/views/admin/AdminLayout.vue', 'src/views/admin/StatsView.vue'],
}
function closure(entries) {
  const visited = new Set(), files = new Set(['index.html', 'favicon.svg'])
  const visit = key => {
    if (visited.has(key)) return
    const chunk = manifest[key]
    if (!chunk) throw new Error('Manifest entry not found: ' + key)
    visited.add(key); files.add(chunk.file)
    for (const file of [...chunk.css || [], ...chunk.assets || []]) files.add(file)
    for (const imported of chunk.imports || []) visit(imported)
  }
  for (const key of ['index.html', ...entries]) visit(key)
  return [...files].sort()
}
const measurements = new Map()
async function measure(file) {
  if (!measurements.has(file)) {
    const bytes = await readFile(new URL(file, dist))
    measurements.set(file, { file, bytes: bytes.length, gzipBytes: gzipSync(bytes).length, kind: file.endsWith('.js') ? 'js' : file.endsWith('.css') ? 'css' : 'other' })
  }
  return measurements.get(file)
}
const routes = {}
for (const [name, entries] of Object.entries(pages)) {
  const files = await Promise.all(closure(entries).map(measure))
  const totals = Object.fromEntries(['js', 'css', 'other'].map(kind => [kind, files.filter(item => item.kind === kind).reduce((sum, item) => ({ bytes: sum.bytes + item.bytes, gzipBytes: sum.gzipBytes + item.gzipBytes, files: sum.files + 1 }), { bytes: 0, gzipBytes: 0, files: 0 })]))
  routes[name] = { ...totals, totalBytes: files.reduce((sum, item) => sum + item.bytes, 0), totalGzipBytes: files.reduce((sum, item) => sum + item.gzipBytes, 0), includesChartEngine: files.some(item => /assets\/(?:charts|chart-renderer)-/.test(item.file)), assets: files }
}
const allJs = await Promise.all([...new Set(Object.values(manifest).map(item => item.file).filter(file => file.endsWith('.js')))].map(measure))
const result = { generatedAt: new Date().toISOString(), method: 'Unique static dependency closure of entry + route + layout. Each file gzip-compressed independently; estimates, not measured network transfer. Runtime-selected other routes excluded.', maxJsChunk: allJs.sort((a, b) => b.bytes - a.bytes)[0], routes }
if (routes['登录'].includesChartEngine || routes['智能问答'].includesChartEngine) throw new Error('Chart engine leaked into login/chat dependency closure')
await writeFile(new URL('../../storage/refactor-validation/build-after.json', import.meta.url), JSON.stringify(result, null, 2))
console.log(JSON.stringify({ maxJsChunk: result.maxJsChunk, routes: Object.fromEntries(Object.entries(routes).map(([name, value]) => [name, { jsKB: +(value.js.bytes / 1000).toFixed(2), cssKB: +(value.css.bytes / 1000).toFixed(2), totalKB: +(value.totalBytes / 1000).toFixed(2), totalGzipKB: +(value.totalGzipBytes / 1000).toFixed(2), chart: value.includesChartEngine }])) }, null, 2))
