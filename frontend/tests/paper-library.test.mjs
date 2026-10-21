import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import * as Vue from 'vue'
import { renderToString } from '@vue/server-renderer'
import { parse, compileScript, compileTemplate, compileStyle } from '@vue/compiler-sfc'
import { compile } from '@vue/compiler-dom'
import { parse as parseModule } from '@babel/parser'
import { formatBytes, formatDate, formatNumber } from '../src/utils/format.js'

// Component-logic + Vue reactivity + rendered-template tests, not source-regex checks.
// HTTP, current-user state, timers, dialogs and Blob URLs are isolated test doubles.
// No live authentication, database, storage, config or native PDF viewer is used.
const compiledPages = {}
for (const name of ['KbList', 'KbDetail']) {
  const filename = new URL(`../src/views/admin/${name}.vue`, import.meta.url)
  const source = await readFile(filename, 'utf8')
  const result = parse(source, { filename: filename.pathname })
  assert.deepEqual(result.errors, [])
  const { descriptor } = result
  const script = compileScript(descriptor, { id: `paper-library-${name}` })
  const template = compileTemplate({ source: descriptor.template.content, filename: filename.pathname, id: name, compilerOptions: { bindingMetadata: script.bindings } })
  assert.deepEqual(template.errors, [])
  for (const style of descriptor.styles) assert.deepEqual(compileStyle({ source: style.content, filename: filename.pathname, id: `data-v-${name}`, scoped: style.scoped }).errors, [])
  const statements = script.scriptSetupAst.filter(node => node.type !== 'ImportDeclaration')
  const code = statements.map(node => descriptor.scriptSetup.content.slice(node.start, node.end)).join('\n')
  const names = statements.flatMap(node => node.type === 'FunctionDeclaration' ? [node.id.name] : node.type === 'VariableDeclaration' ? node.declarations.map(item => item.id.name) : []).filter(Boolean)
  compiledPages[name] = {
    create: bindings => new Function('bindings', `const { ${Object.keys(bindings).join(',')} } = bindings;\n${code}\nreturn { ${names.map(name => `get ${name}() { return ${name} }`).join(',')} };`)(bindings),
    render: new Function('Vue', compile(descriptor.template.content, { mode: 'function', prefixIdentifiers: true }).code)(Vue),
  }
}

const apiSource = await readFile(new URL('../src/api/index.js', import.meta.url), 'utf8')
const apiAst = parseModule(apiSource, { sourceType: 'module' })
const apiExports = apiAst.program.body.filter(node => node.type === 'ExportNamedDeclaration' && node.declaration?.type === 'VariableDeclaration')
const kbExportNames = ['kbsApi', 'createKbApi', 'kbApi', 'patchKbApi', 'grantKbApi', 'kbStatsApi', 'docsApi', 'uploadDocApi', 'deleteDocApi', 'reparseDocApi', 'chunksApi', 'documentFileApi']
function isolatedApi(http) {
  const declarations = apiExports.filter(node => kbExportNames.includes(node.declaration.declarations[0].id.name))
  return new Function('http', declarations.map(node => apiSource.slice(node.declaration.start, node.declaration.end)).join('\n') + `\nreturn {${kbExportNames.join(',')}}`)(http)
}

const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }
const settle = async () => { for (let i = 0; i < 6; i++) { await Promise.resolve(); await Vue.nextTick() } }
const denied = (status, detail = '服务返回的原因') => Object.assign(new Error('fixture failure'), { response: { status, data: { detail } } })
const library = (id = 1, extra = {}) => ({ id, owner_id: 7, name: `论文库 ${id}`, description: '', status: 'enabled', doc_count: 2, chunk_count: 18, granted_roles: [], created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-18T01:02:00Z', ...extra })
const paper = (kbId = 1, extra = {}) => ({ id: kbId * 10, kb_id: kbId, filename: `paper-${kbId}.pdf`, file_type: 'pdf', file_size: 1024, status: 'success', page_count: 7, chunk_count: 18, error_message: '', created_at: '2026-09-18T01:02:00Z', version: null, ...extra })
const pageOf = (id = 1, extra = {}) => ({ items: [paper(id)], total: 1, page: 1, size: 20, versioning_enabled: false, ...extra })
// Binary transport fixture only; never presented as a real, validated PDF document.
const pdfBlob = () => new Blob([new Uint8Array([37, 80, 68, 70, 45])], { type: 'application/pdf' })
const active = new Set()
test.afterEach(() => { for (const harness of [...active]) harness.dispose() })

function harness(name, { api = {}, query = {}, id = '1', user: actor = {} } = {}) {
  const route = Vue.reactive({ path: name === 'KbList' ? '/admin/kbs' : `/admin/kbs/${id}`, params: { id }, query: { ...query } })
  const user = Vue.reactive({ user: { id: 7 }, role: 'user', isAdmin: false, ...actor })
  const calls = Object.fromEntries(kbExportNames.map(key => [key, []]))
  Object.assign(calls, { push: [], replace: [], messages: [], confirmations: [], createdUrls: [], revokedUrls: [] })
  const mountHooks = [], unmountHooks = [], guards = [], timers = new Map(), urls = new Map()
  let clockId = 0, urlId = 0, disposed = false
  const defaults = {
    kbsApi: async () => [library()], createKbApi: async data => library(42, data), kbApi: async id => library(id),
    kbStatsApi: async id => ({ kb_id: id, doc_count: 1, chunk_count: 18, success_docs: 1, failed_docs: 0, pending_docs: 0, last_upload_at: '2026-09-18T01:02:00Z' }),
    docsApi: async id => pageOf(id), uploadDocApi: async id => paper(id, { is_new: true, status: 'pending' }),
    patchKbApi: async (id, data) => library(id, data), grantKbApi: async () => ({ message: '已保存' }),
    deleteDocApi: async () => ({ message: '已删除' }), reparseDocApi: async () => ({ message: '已加入解析队列' }),
    chunksApi: async id => [{ id: 100, document_id: id, seq_no: 1, page_no: 3, content: '论文中的真实摘录（测试夹具）' }],
    documentFileApi: async () => pdfBlob(),
  }
  const bindings = {
    ref: Vue.ref, computed: Vue.computed, watch: Vue.watch,
    onMounted: callback => mountHooks.push(callback), onBeforeUnmount: callback => unmountHooks.push(callback),
    defineAsyncComponent: () => ({ render: () => null }),
    useRoute: () => route, useRouter: () => ({ push: async location => { calls.push.push(location) }, replace: async location => { calls.replace.push(location); route.query = { ...location.query } } }),
    useUserStore: () => user, onBeforeRouteLeave: callback => guards.push(callback), onBeforeRouteUpdate: callback => guards.push(callback),
    ElMessage: Object.fromEntries(['success', 'warning', 'error'].map(type => [type, text => calls.messages.push({ type, text })])),
    ElMessageBox: { confirm: async (...args) => { calls.confirmations.push(args); return api.confirm ? api.confirm(...args) : true } },
    setTimeout: (callback, delay) => { const id = ++clockId; timers.set(id, { callback, delay }); return id }, clearTimeout: id => timers.delete(id),
    document: { visibilityState: 'visible' }, Blob, FormData,
    URL: { createObjectURL: blob => { const url = `blob:paper-test/${++urlId}`; urls.set(url, blob); calls.createdUrls.push(url); return url }, revokeObjectURL: url => { urls.delete(url); calls.revokedUrls.push(url) } },
    formatBytes, formatDate, formatNumber,
  }
  for (const key of kbExportNames) bindings[key] = (...args) => { calls[key].push(args); return (api[key] || defaults[key])(...args) }
  const scope = Vue.effectScope()
  const state = scope.run(() => compiledPages[name].create(bindings))
  const instance = {
    state, route, user, calls, api, guards, timers, urls,
    mount: async () => { for (const hook of mountHooks) await hook(); await settle() },
    tick: async delay => { for (const [key, value] of [...timers]) if (value.delay === delay) { timers.delete(key); await value.callback() } await settle() },
    html: () => renderPage(name, state),
    dispose: () => { if (disposed) return; disposed = true; for (const hook of unmountHooks) hook(); scope.stop(); timers.clear(); active.delete(instance) },
  }
  active.add(instance)
  return instance
}

function renderPage(name, state) {
  const app = Vue.createSSRApp({ setup: () => ({ ...state, formatBytes, formatDate, formatNumber }), render: compiledPages[name].render })
  const wrapper = Vue.defineComponent({ inheritAttrs: false, setup: (_props, { attrs, slots }) => () => Vue.h('div', attrs, slots.default?.()) })
  for (const name of ['ElDropdown', 'ElDropdownMenu', 'ElDropdownItem', 'ElForm', 'ElCheckboxGroup', 'ElCheckbox', 'ElUpload', 'ElSkeleton', 'ElProgress', 'ElPagination', 'VersionManager']) app.component(name, wrapper)
  app.component('AppIcon', { render: () => null })
  app.component('StatusBadge', { props: ['status', 'label'], setup: props => () => Vue.h('span', {}, props.label || props.status) })
  app.component('RouterLink', { props: ['to'], setup: (props, { slots }) => () => { const to = props.to; const query = typeof to === 'object' ? new URLSearchParams(to.query).toString() : ''; return Vue.h('a', { href: typeof to === 'string' ? to : to.path + (query ? '?' + query : '') }, slots.default?.()) } })
  app.component('ElButton', { props: ['type', 'loading'], setup: (_props, { attrs, slots }) => () => Vue.h('button', { ...attrs, type: 'button' }, slots.default?.()) })
  app.component('ElInput', { props: ['modelValue', 'type'], setup: (props, { attrs }) => () => Vue.h('input', { ...attrs, value: props.modelValue }) })
  app.component('ElSelect', { props: ['modelValue'], setup: (_props, { attrs, slots }) => () => Vue.h('select', attrs, slots.default?.()) })
  app.component('ElOption', { props: ['label', 'value'], setup: props => () => Vue.h('option', { value: props.value }, props.label) })
  app.component('ElFormItem', { props: ['label'], setup: (props, { slots }) => () => Vue.h('label', {}, [props.label, slots.default?.()]) })
  const dialog = { props: ['modelValue', 'title'], setup: (props, { slots }) => () => props.modelValue ? Vue.h('section', { role: 'dialog', 'aria-label': props.title }, [slots.default?.(), slots.footer?.()]) : null }
  app.component('ElDialog', dialog); app.component('ElDrawer', dialog)
  return renderToString(app)
}

test('KB client creates private libraries, ignores obsolete template input, and scopes authorized file requests', async () => {
  const requests = []
  const http = Object.fromEntries(['get', 'post', 'patch', 'delete'].map(method => [method, async (...args) => { requests.push([method, ...args]); return { accepted: true } }]))
  const api = isolatedApi(http)
  await api.createKbApi({ name: '学生论文', template_id: 99, import_samples: true })
  assert.deepEqual(requests[0], ['post', '/kbs', { name: '学生论文', description: '', role_codes: [] }])
  await api.createKbApi({ name: '显式共享', description: '研究资料', role_codes: ['user'] })
  assert.deepEqual(requests[1][2].role_codes, ['user'])
  await api.documentFileApi(12, 34)
  assert.deepEqual(requests[2], ['get', '/kbs/12/docs/34/file', { params: { download: true }, responseType: 'blob', silent: true }])
  assert.equal(apiExports.some(node => node.declaration.declarations[0].id.name === 'templatesApi'), false)
})

test('Creating from the list has no prefills, trims input, sends no roles, and opens upload in the new library', async () => {
  const h = harness('KbList', { query: { create: '1', template: '99' } }); await h.mount()
  assert.equal(h.state.editorVisible.value, true)
  assert.deepEqual(h.state.form.value, { name: '', description: '' })
  h.state.form.value = { name: '  机器学习论文  ', description: '  课程阅读  ' }
  await h.state.save()
  assert.deepEqual(h.calls.createKbApi, [[{ name: '机器学习论文', description: '课程阅读', role_codes: [] }]])
  assert.deepEqual(h.calls.push, [{ path: '/admin/kbs/42', query: { upload: '1' } }])
  assert.equal(h.calls.kbsApi.length, 1)
})

test('Blank names never create a library, and API errors stay visible without losing the draft', async () => {
  const h = harness('KbList', { api: { createKbApi: async () => { throw denied(409, '名称已存在') } } }); await h.mount()
  h.state.openCreate(); h.state.form.value.name = '   '; await h.state.save()
  assert.equal(h.calls.createKbApi.length, 0); assert.match(h.state.editorError.value, /名称/)
  h.state.form.value.name = '我的论文'; await h.state.save()
  assert.equal(h.state.editorVisible.value, true); assert.equal(h.state.form.value.name, '我的论文')
  assert.equal(h.state.editorError.value, '名称已存在'); assert.equal(h.state.saving.value, false)
})

test('Ordinary owners can manage their library; sharing remains an explicit separate action', async () => {
  const owned = library(1), shared = library(2, { owner_id: 8, granted_roles: ['user'] })
  const h = harness('KbList', { api: { kbsApi: async () => [owned, shared] } }); await h.mount()
  assert.equal(h.state.canManage(owned), true); assert.equal(h.state.canManage(shared), false)
  h.user.role = 'operator'; assert.equal(h.state.canManage(shared), false)
  h.state.openGrant(owned); assert.deepEqual(h.state.grantRoles.value, [])
  assert.equal(h.calls.grantKbApi.length, 0)
  h.state.grantRoles.value = ['user']; await h.state.submitGrant()
  assert.deepEqual(h.calls.grantKbApi, [[1, ['user']]])
  h.user.isAdmin = true; assert.equal(h.state.canManage(shared), false)
})

test('List rendering uses real counts and timestamps, hides empty descriptions, and links with kb_id', async () => {
  const h = harness('KbList', { api: { kbsApi: async () => [library(5, { name: '毕业论文', doc_count: 17, chunk_count: 321 }), library(6, { owner_id: 8, name: '共享文献', granted_roles: ['user'], updated_at: null })] } }); await h.mount()
  const html = await h.html()
  assert.match(html, /17 份文档/); assert.match(html, /321 个片段/)
  assert.match(html, /更新于/); assert.match(html, /创建于/)
  assert.match(html, /href="\/\?kb_id=5"/); assert.doesNotMatch(html, /\?kb=/)
  assert.match(html, /管理论文库：毕业论文/); assert.doesNotMatch(html, /管理论文库：共享文献/)
  assert.doesNotMatch(html, /尚未填写|行业|提示词模板|政务|library-description/)
  assert.match(html, /label for="kb-search"/)
})

test('List search resets pagination and older requests cannot overwrite newer results', async () => {
  const old = deferred(), h = harness('KbList'); await h.mount()
  h.api.kbsApi = () => old.promise
  const pending = h.state.load()
  h.api.kbsApi = async () => [library(2, { name: '论文 B', description: '机器学习' })]
  await h.state.load(); old.resolve([library(1)]); await pending
  assert.equal(h.state.kbs.value[0].id, 2)
  h.state.page.value = 3; h.state.search.value = '  机器学习  '; await settle()
  assert.equal(h.state.page.value, 1); assert.equal(h.state.filtered.value.length, 1)
  h.state.search.value = '不存在'; await settle(); assert.equal(h.state.filtered.value.length, 0)
  assert.match(await h.html(), /没有匹配的论文库/)
})

test('List refresh distinguishes temporary failures from access revocation', async () => {
  const h = harness('KbList'); await h.mount()
  h.api.kbsApi = async () => { throw denied(503, '暂不可用') }; await h.state.load()
  assert.equal(h.state.kbs.value.length, 1); assert.equal(h.state.error.value, '暂不可用')
  h.api.kbsApi = async () => { throw denied(403) }; await h.state.load()
  assert.deepEqual(h.state.kbs.value, []); assert.equal(h.state.loaded.value, false)
  assert.match(await h.html(), /论文库未能加载/)
})

test('Detail uses the route KB for data, pagination and chat; ordinary owners can upload', async () => {
  const h = harness('KbDetail', { id: '23' }); await h.mount()
  assert.equal(h.state.canManage.value, true)
  assert.deepEqual(h.calls.kbApi, [[23]])
  assert.deepEqual(h.calls.docsApi[0], [23, 1, 20, { q: undefined, status: undefined }])
  h.state.ask(); assert.deepEqual(h.calls.push, [{ path: '/', query: { kb_id: 23 } }])
  h.state.openUpload(); assert.equal(h.state.uploadVisible.value, true)
  h.state.page.value = 2; await settle(); assert.equal(h.calls.docsApi.at(-1)[1], 2)
  h.state.size.value = 50; await settle(); assert.deepEqual(h.calls.docsApi.at(-1).slice(0, 3), [23, 1, 50])
  h.state.search.value = '  论文标题  '; await h.tick(300)
  assert.equal(h.calls.docsApi.at(-1)[3].q, '论文标题')
})

test('Upload deep links are consumed only after loading the same owned library', async () => {
  const h = harness('KbDetail', { id: '3', query: { upload: '1', context: 'reading' } }); await h.mount()
  assert.equal(h.state.uploadVisible.value, true)
  assert.deepEqual(h.calls.replace.at(-1), { path: '/admin/kbs/3', query: { context: 'reading' } })
  const shared = harness('KbDetail', { query: { upload: '1' }, api: { kbApi: async id => library(id, { owner_id: 8 }) } }); await shared.mount()
  assert.equal(shared.state.canManage.value, false); assert.equal(shared.state.uploadVisible.value, false)
  await shared.state.startUpload(); assert.equal(shared.calls.uploadDocApi.length, 0)
})

test('Invalid KB routes fail closed without making requests or enabling chat/upload', async () => {
  const h = harness('KbDetail', { id: 'not-a-kb' }); await h.mount()
  assert.equal(h.calls.kbApi.length, 0); assert.equal(h.calls.docsApi.length, 0)
  assert.equal(h.state.accessStatus.value, 404); assert.equal(h.state.canManage.value, false)
  h.state.ask(); h.state.openUpload(); assert.deepEqual(h.calls.push, []); assert.equal(h.state.uploadVisible.value, false)
})

test('A route change synchronously clears old documents, filters, versions, excerpts and Blob URLs', async () => {
  const next = deferred(), h = harness('KbDetail'); await h.mount()
  const doc = h.state.docs.value[0]
  await h.state.preview(doc); await h.state.readOriginal(doc)
  h.state.openVersion({ ...doc, version: { version_id: 10, version_label: 'v1' } })
  h.state.search.value = '旧范围'; h.state.status.value = 'success'; h.state.page.value = 2; await settle()
  h.api.docsApi = id => id === 2 ? next.promise : Promise.resolve(pageOf(id))
  h.route.params.id = '2'
  assert.equal(h.state.kb.value, null); assert.deepEqual(h.state.docs.value, []); assert.equal(h.state.total.value, 0)
  assert.equal(h.state.page.value, 1); assert.equal(h.state.search.value, ''); assert.equal(h.state.status.value, '')
  assert.equal(h.state.previewVisible.value, false); assert.deepEqual(h.state.previewChunks.value, [])
  assert.equal(h.state.versionVisible.value, false); assert.equal(h.state.readerUrl.value, '')
  assert.equal(h.urls.size, 0); assert.equal(h.calls.revokedUrls.length, 1)
  next.resolve(pageOf(2)); await settle()
  assert.equal(h.state.kb.value.id, 2); assert.equal(h.state.docs.value[0].kb_id, 2)
  assert.deepEqual(h.calls.docsApi.at(-1), [2, 1, 20, { q: undefined, status: undefined }])
})

test('A late old-KB load cannot populate the new route, even before the initial mount completes', async () => {
  const old = deferred(), h = harness('KbDetail', { api: { kbApi: id => id === 1 ? old.promise : Promise.resolve(library(id)) } })
  const mount = h.mount(); h.route.params.id = '2'; await settle()
  assert.equal(h.state.kb.value.id, 2)
  old.resolve(library(1)); await mount
  assert.equal(h.state.kb.value.id, 2); assert.equal(h.state.docs.value[0].kb_id, 2)
})

test('Late excerpt responses and closed drawers cannot resurrect old content', async () => {
  const old = deferred(), h = harness('KbDetail', { api: { chunksApi: () => old.promise } }); await h.mount()
  const pending = h.state.preview(h.state.docs.value[0])
  h.route.params.id = '2'; await settle()
  old.resolve([{ id: 999, document_id: 10, seq_no: 1, page_no: 1, content: 'OLD SCOPE' }]); await pending
  assert.equal(h.state.previewVisible.value, false); assert.deepEqual(h.state.previewChunks.value, [])
  const closed = deferred(); h.api.chunksApi = () => closed.promise
  const second = h.state.preview(h.state.docs.value[0]); h.state.previewVisible.value = false
  closed.resolve([{ id: 888, content: 'CLOSED DRAWER' }]); await second
  assert.deepEqual(h.state.previewChunks.value, [])
})

for (const status of [401, 403, 404]) test(`HTTP ${status} removes loaded documents, open excerpts, version dialog and original-file URLs`, async () => {
  const h = harness('KbDetail'); await h.mount()
  const doc = h.state.docs.value[0]
  await h.state.preview(doc); await h.state.readOriginal(doc)
  h.state.openVersion({ ...doc, version: { version_id: doc.id } })
  h.api.kbApi = async () => { throw denied(status) }; await h.state.load()
  assert.equal(h.state.kb.value, null); assert.deepEqual(h.state.docs.value, [])
  assert.equal(h.state.previewVisible.value, false); assert.deepEqual(h.state.previewChunks.value, [])
  assert.equal(h.state.versionVisible.value, false); assert.equal(h.state.readerVisible.value, false); assert.equal(h.urls.size, 0)
})

test('File reading uses a scoped authorized Blob, actual PDF MIME and supplied page number; URLs are released', async () => {
  const h = harness('KbDetail'); await h.mount()
  const doc = h.state.docs.value[0]
  await h.state.readOriginal(doc, 3)
  assert.deepEqual(h.calls.documentFileApi, [[1, doc.id]])
  assert.equal(h.state.readerIsPdf.value, true); assert.equal(h.state.readerSource.value, 'blob:paper-test/1#page=3')
  const html = await h.html()
  assert.match(html, /<iframe/); assert.match(html, /referrerpolicy="no-referrer"/)
  assert.match(html, /download="paper-1.pdf"/); assert.doesNotMatch(html, /[?&](?:token|access_token)=/)
  await h.state.readOriginal({ ...doc, id: 11 }, 2)
  assert.deepEqual(h.calls.revokedUrls, ['blob:paper-test/1']); assert.equal(h.urls.size, 1)
  h.state.closeReader(); assert.equal(h.urls.size, 0)
  await h.state.readOriginal(doc); h.dispose(); assert.equal(h.urls.size, 0)
  assert.equal(h.calls.revokedUrls.length, 3)
})

test('Non-PDF data is never embedded as PDF, even when its filename ends in .pdf', async () => {
  const h = harness('KbDetail', { api: { documentFileApi: async () => new Blob(['<script>not executed</script>'], { type: 'text/html' }) } }); await h.mount()
  await h.state.readOriginal(h.state.docs.value[0])
  assert.equal(h.state.readerIsPdf.value, false)
  const html = await h.html(); assert.doesNotMatch(html, /<iframe/); assert.match(html, /下载原文件后阅读/)
  assert.match(html, /download="paper-1.pdf"/); assert.doesNotMatch(html, /<script>/)
})

test('Closing or switching scope during file loading discards the response before allocating a URL', async () => {
  const first = deferred(), h = harness('KbDetail', { api: { documentFileApi: () => first.promise } }); await h.mount()
  const pending = h.state.readOriginal(h.state.docs.value[0]); h.state.closeReader()
  first.resolve(pdfBlob()); await pending
  assert.deepEqual(h.calls.createdUrls, [])
  const second = deferred(); h.api.documentFileApi = () => second.promise
  const other = h.state.readOriginal(h.state.docs.value[0]); h.route.params.id = '2'; await settle()
  second.resolve(pdfBlob()); await other
  assert.deepEqual(h.calls.createdUrls, []); assert.equal(h.state.readerDoc.value, null)
})

test('Blob API error bodies are decoded and shown; an empty response is not offered as a file', async () => {
  const failure = denied(403)
  failure.response.data = new Blob([JSON.stringify({ detail: '无权读取原文' })], { type: 'application/json' })
  const h = harness('KbDetail', { api: { documentFileApi: async () => { throw failure } } }); await h.mount()
  await h.state.readOriginal(h.state.docs.value[0]); assert.equal(h.state.readerError.value, '无权读取原文')
  assert.equal(h.state.readerUrl.value, ''); assert.equal(h.state.readerLoading.value, false)
  h.api.documentFileApi = async () => new Blob([])
  await h.state.readOriginal(h.state.docs.value[0]); assert.match(h.state.readerError.value, /原文件读取失败/)
  assert.equal(h.urls.size, 0)
})

test('Document titles, versions and citations preserve actual API values without inventing page numbers', async () => {
  const doc = paper(1, { version: { version_id: 10, series_title: '已录入的论文标题', version_label: 'v2', publication_state: 'published', can_manage: false, updated_at: '2026-09-18T03:04:00Z' } })
  const h = harness('KbDetail', { api: { docsApi: async () => pageOf(1, { items: [doc], versioning_enabled: true }), chunksApi: async () => [{ id: 101, document_id: 10, seq_no: 8, content: '无页码摘录 <script>alert(1)</script>', page_no: 0 }, { id: 102, document_id: 10, seq_no: 9, content: '有页码摘录', page_no: 4 }] } }); await h.mount()
  assert.equal(h.state.documentTitle(doc), '已录入的论文标题'); assert.equal(h.state.documentTitle(paper()), 'paper-1.pdf')
  assert.equal(h.state.canWriteDoc(doc), false)
  h.state.openVersion(doc); assert.equal(h.state.versionDoc.value.version.version_id, 10)
  await h.state.preview(doc)
  assert.equal(h.state.previewChunks.value[0].seq_no, 8); assert.equal(h.state.previewChunks.value[0].page_no, 0)
  const html = await h.html()
  assert.match(html, /已录入的论文标题/); assert.match(html, /v2 · 已发布/)
  assert.match(html, /原文第 4 页/); assert.doesNotMatch(html, /原文第 0 页|<script>/)
  assert.match(html, /&lt;script&gt;/)
})

test('Deletion remains confirmed and scope-bound, including an A → B → A route change during confirmation', async () => {
  const confirm = deferred(), h = harness('KbDetail', { api: { confirm: () => confirm.promise } }); await h.mount()
  const pending = h.state.remove(h.state.docs.value[0])
  assert.equal(h.calls.confirmations.length, 1); assert.equal(h.calls.deleteDocApi.length, 0)
  h.route.params.id = '2'; await settle(); h.route.params.id = '1'; await settle()
  confirm.resolve(true); await pending
  assert.equal(h.calls.deleteDocApi.length, 0)
})

test('Delete and reparse failures are visible and do not claim success; cancel sends no mutation', async () => {
  const h = harness('KbDetail', { api: { deleteDocApi: async () => { throw denied(409, '当前版本不能删除') }, reparseDocApi: async () => { throw denied(409, '已有解析任务') } } }); await h.mount()
  const doc = h.state.docs.value[0]
  await h.state.remove(doc); assert.equal(h.state.operationError.value, '当前版本不能删除')
  await h.state.reparse(doc); assert.equal(h.state.operationError.value, '已有解析任务')
  assert.equal(h.state.busyDocs.value.size, 0); assert.equal(h.calls.messages.filter(item => item.type === 'success').length, 0)
  h.api.confirm = async () => { throw 'cancel' }
  await h.state.remove(doc); assert.equal(h.calls.deleteDocApi.length, 1)
})

test('Versioned libraries describe archiving truthfully and honor the returned server result', async () => {
  const h = harness('KbDetail', { api: { docsApi: async () => pageOf(1, { versioning_enabled: true }), deleteDocApi: async () => ({ message: '版本已归档，原文件和历史修订保留' }) } }); await h.mount()
  await h.state.remove(h.state.docs.value[0])
  assert.match(h.calls.confirmations[0][0], /原文件与历史版本保留/)
  assert.equal(h.calls.confirmations[0][2].confirmButtonText, '确认归档')
  assert.equal(h.calls.messages.at(-1).text, '版本已归档，原文件和历史修订保留')
})

function uploadFile(name, uid) { const raw = new Blob(['paper upload fixture'], { type: 'text/plain' }); return { name, uid, raw, size: raw.size, status: 'ready' } }
test('Upload uses FormData in the owned route scope and distinguishes duplicates from new uploads', async () => {
  const h = harness('KbDetail', { id: '9', api: { uploadDocApi: async id => paper(id, { is_new: false, status: 'success' }) } }); await h.mount()
  h.state.uploadFiles.value = [uploadFile('notes.txt', 1)]; await h.state.startUpload()
  assert.equal(h.calls.uploadDocApi[0][0], 9)
  assert.ok(h.calls.uploadDocApi[0][1] instanceof FormData)
  assert.equal(await h.calls.uploadDocApi[0][1].get('file').text(), 'paper upload fixture')
  assert.equal(h.state.uploadResult.value.existing, 1); assert.equal(h.state.uploadResult.value.success, 1)
  assert.equal(h.state.pendingUploads.value, 0); assert.equal(h.state.uploadDone.value, true)
  assert.equal(h.state.uploading.value, false)
})

test('Uploading blocks route guards; a forced scope switch cannot upload the remaining files to either wrong scope', async () => {
  const first = deferred(), h = harness('KbDetail', { api: { uploadDocApi: () => first.promise } }); await h.mount()
  h.state.uploadFiles.value = [uploadFile('first.txt', 1), uploadFile('second.txt', 2)]
  const pending = h.state.startUpload()
  assert.equal(h.state.uploading.value, true); assert.ok(h.guards.every(guard => guard() === false))
  h.route.params.id = '2'; await settle(); first.resolve(paper(1)); await pending
  assert.equal(h.calls.uploadDocApi.length, 1); assert.equal(h.calls.uploadDocApi[0][0], 1)
  assert.deepEqual(h.state.uploadFiles.value, []); assert.equal(h.state.uploadResult.value.success, 0)
  assert.equal(h.state.docs.value[0].kb_id, 2); assert.ok(h.guards.every(guard => guard() === true))
})

test('Empty, unsupported and oversized upload selections are rejected without network requests', async () => {
  const h = harness('KbDetail'); await h.mount()
  for (const file of [{ name: 'empty.pdf', uid: 1, size: 0 }, { name: 'program.exe', uid: 2, size: 12 }, { name: 'huge.pdf', uid: 3, size: 51 * 1024 * 1024 }]) {
    h.state.uploadFiles.value = [file]; h.state.validateFile(file); assert.deepEqual(h.state.uploadFiles.value, [])
  }
  await h.state.startUpload(); assert.equal(h.calls.uploadDocApi.length, 0)
})

test('Unmounted list/detail requests cannot publish late data or schedule more polling', async () => {
  const oldList = deferred(), list = harness('KbList', { api: { kbsApi: () => oldList.promise } })
  const listMount = list.mount(); list.dispose(); oldList.resolve([library(1)]); await listMount
  assert.deepEqual(list.state.kbs.value, [])
  const oldDetail = deferred(), detail = harness('KbDetail', { api: { docsApi: () => oldDetail.promise } })
  const detailMount = detail.mount(); detail.dispose(); oldDetail.resolve(pageOf(1)); await detailMount
  assert.deepEqual(detail.state.docs.value, []); assert.equal(detail.timers.size, 0)
})

test('Explicit server can_manage wins over local role guesses, while legacy owner responses still work', async () => {
  const list = harness('KbList'); await list.mount()
  assert.equal(list.state.canManage(library(1, { can_manage: false })), false)
  assert.equal(list.state.canManage(library(2, { owner_id: 8, can_manage: true })), true)
  assert.equal(list.state.canManage(library(1)), true)
  const detail = harness('KbDetail', { api: { kbApi: async id => library(id, { can_manage: false }) } }); await detail.mount()
  assert.equal(detail.state.canManage.value, false)
  detail.state.openUpload(); assert.equal(detail.state.uploadVisible.value, false)
  await detail.state.remove(detail.state.docs.value[0]); assert.equal(detail.calls.confirmations.length, 0)
  detail.api.kbApi = async id => library(id, { owner_id: 8, can_manage: true }); await detail.state.load()
  assert.equal(detail.state.canManage.value, true)
})


test('Legacy responses do not grant non-owner administrators library write controls', async () => {
  const h = harness('KbDetail', { user: { isAdmin: true, role: 'admin' }, api: { kbApi: async () => library(1, { owner_id: 88 }) } }); await h.mount()
  assert.equal(h.state.canManage.value, false)
  assert.doesNotMatch(await h.html(), /管理论文：|class="paper-upload-zone"/)
  const list = harness('KbList', { user: { isAdmin: true, role: 'admin' } }); await list.mount(); list.state.openGrant(list.state.kbs.value[0]); await settle()
  assert.match(await list.html(), /共享仅授予读取权限/)
  assert.doesNotMatch(await list.html(), /所有者与系统管理员保留访问和管理权限/)
})

test('UI-R2 library view keeps permissions and access filters separate from card density', async () => {
  const h = harness('KbList', { api: { kbsApi: async () => [library(1), library(2, { owner_id: 99, can_manage: false, granted_roles: ['user'] })] } }); await h.mount()
  assert.equal(h.state.viewMode.value, 'cards')
  h.state.setViewMode('list'); assert.equal(h.state.viewMode.value, 'list')
  h.state.setViewMode('invalid'); assert.equal(h.state.viewMode.value, 'list')
  h.state.accessFilter.value = 'readonly'; assert.deepEqual(h.state.filtered.value.map(x => x.id), [2])
  let html = await h.html(); assert.match(html, /共享只读/); assert.doesNotMatch(html, /管理论文库：论文库 2/)
  h.state.accessFilter.value = 'managed'; assert.deepEqual(h.state.filtered.value.map(x => x.id), [1])
  h.state.resetFilters(); assert.equal(h.state.filtered.value.length, 2)
})

test('UI-R2 document table retains actual status, source reading and error rows', async () => {
  const h = harness('KbDetail', { api: { docsApi: async () => pageOf(1,{items:[paper(1),paper(1,{id:11,status:'failed',error_message:'解析失败证据'})],total:2}) } }); await h.mount()
  const html = await h.html()
  assert.match(html, /<table\b/); assert.match(html, /<th scope="col">资料名称/); assert.match(html, /状态与版本/)
  assert.match(html, /解析失败证据/); assert.match(html, /阅读原文/); assert.match(html, /查看片段/)
  assert.match(html, /可管理此知识库/)
})
