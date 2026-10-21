import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import * as Vue from 'vue'
import { renderToString } from '@vue/server-renderer'
import { parse, compileScript, compileTemplate, compileStyle } from '@vue/compiler-sfc'
import { compile, parse as parseHtml } from '@vue/compiler-dom'
import { DEFAULT_LAYOUT, LIMITS, clamp, normalizeLayout, resizeBounds, readLayout, writeLayout } from '../src/utils/workspaceLayout.js'
import { escapeHtml, safeLink, renderMarkdown } from '../src/utils/markdown.js'
import { formatDate, modeNames } from '../src/utils/format.js'

// Execute the current SFC functions with real Vue refs/watchers, as in paper-library.test.
// All storage, HTTP, pointer/focus nodes, timers and Blob URLs below are isolated doubles.
// PDF fixtures test transport/lifecycle only; no native PDF viewer, browser or live login.
const compiled = {}
const paths = { Layout: 'components/ResizableWorkspace.vue', Reader: 'components/CitationReader.vue', Chat: 'views/chat/ChatView.vue', Markdown: 'components/MarkdownContent.vue' }
const identifiers = node => !node ? [] : node.type === 'Identifier' ? [node.name] : node.type === 'ObjectPattern' ? node.properties.flatMap(item => identifiers(item.value || item.argument)) : node.type === 'ArrayPattern' ? node.elements.flatMap(identifiers) : identifiers(node.left || node.argument)
for (const [name, path] of Object.entries(paths)) {
  const url = new URL('../src/' + path, import.meta.url), source = await readFile(url, 'utf8')
  const { descriptor, errors } = parse(source, { filename: path })
  assert.deepEqual(errors, [])
  const script = compileScript(descriptor, { id: name })
  assert.deepEqual(compileTemplate({ source: descriptor.template.content, filename: path, id: name, compilerOptions: { bindingMetadata: script.bindings } }).errors, [])
  for (const style of descriptor.styles) assert.deepEqual(compileStyle({ source: style.src ? await readFile(new URL(style.src, url), 'utf8') : style.content, filename: path, id: name, scoped: style.scoped }).errors, [])
  const statements = script.scriptSetupAst.filter(node => node.type !== 'ImportDeclaration')
  const code = statements.map(node => descriptor.scriptSetup.content.slice(node.start, node.end)).join('\n')
  const names = statements.flatMap(node => node.type === 'FunctionDeclaration' ? [node.id.name] : node.type === 'VariableDeclaration' ? node.declarations.flatMap(item => identifiers(item.id)) : [])
  compiled[name] = {
    create: bindings => new Function('bindings', `const {${Object.keys(bindings)}} = bindings;\n${code}\nreturn {${names.map(key => `get ${key}(){return ${key}}`)}};`)(bindings),
    render: new Function('Vue', compile(descriptor.template.content, { mode: 'function', prefixIdentifiers: true }).code)(Vue),
  }
}
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }
const settle = async () => { for (let i = 0; i < 6; i++) { await Promise.resolve(); await Vue.nextTick() } }
const memory = initial => { const data = new Map(Object.entries(initial || {})); return { data, getItem: key => data.get(key) ?? null, setItem: (key, value) => data.set(key, value) } }
const kb = id => ({ id, name: `LIBRARY_${id}`, status: 'enabled', doc_count: 1 })
const paper = id => ({ id: id * 10, kb_id: id, filename: `ONLY_KB_${id}_PAPER.pdf`, status: 'success', page_count: 4 })
const session = id => ({ id: id * 11, kb_id: id, title: `ONLY_KB_${id}_SESSION`, updated_at: '2026-09-18T00:00:00Z' })
const citation = id => ({ doc_id: id * 10, doc_name: `ONLY_KB_${id}_PAPER.pdf`, chunk_id: id * 100, rank: id, page_no: 3, content_excerpt: `EXCERPT_${id}` })
const pdf = () => new Blob(['%PDF-test-fixture'], { type: 'application/pdf' })
const apiFailure = status => ({ response: { status } })
const live = new Set()
let uid = 0
function harness(name, { props: input = {}, api = {}, storage = memory(), width = 1200, query = {}, storageGetterThrows = false } = {}) {
  const props = Vue.reactive({ ...input }), route = Vue.reactive({ query: { ...query } })
  const calls = { api: [], emitted: [], downloads: [], revoked: [], created: [], messages: [], panels: [] }
  const timers = new Map(), urls = new Map(), listeners = new Map(), mount = [], unmount = [], observers = []
  let serial = 0, disposed = false
  const document = { activeElement: null, createElement: () => { const link = { click() { calls.downloads.push({ href: this.href, name: this.download, blob: urls.get(this.href) }) } }; return link } }
  const node = label => Vue.markRaw({ label, isConnected: true, focus() { document.activeElement = this }, getClientRects: () => [{}], setPointerCapture(id) { this.pointer = id } })
  const window = { addEventListener: (key, fn) => listeners.set(key, fn), removeEventListener: (key, fn) => { assert.equal(listeners.get(key), fn); listeners.delete(key) } }
  Object.defineProperty(window, 'localStorage', { get() { if (storageGetterThrows) throw new Error('storage denied'); return storage } })
  const defaults = {
    kbsApi: async () => [kb(1), kb(2)], sessionsApi: async () => [session(1), session(2)], capabilitiesApi: async () => ({ mode: 'local' }),
    docsApi: async id => ({ items: [paper(id)], total: 1 }), sessionMessagesApi: async id => [{ id, role: 'assistant', content: `ANSWER_${id}` }], documentFileApi: async () => pdf(),
  }
  const bindings = {
    ref: Vue.ref, computed: Vue.computed, reactive: Vue.reactive, watch: Vue.watch, nextTick: Vue.nextTick,
    getCurrentInstance: () => ({ uid: ++uid }), onMounted: fn => mount.push(fn), onBeforeUnmount: fn => unmount.push(fn),
    defineProps: definitions => { for (const [key, def] of Object.entries(definitions)) if (props[key] === undefined && def && typeof def === 'object' && 'default' in def) props[key] = typeof def.default === 'function' ? def.default() : def.default; return props },
    defineEmits: () => (...args) => calls.emitted.push(args), defineExpose() {},
    useRoute: () => route, useUserStore: () => ({ user: { id: 7 }, role: 'user', isAdmin: false, isOperator: false }),
    window, document, localStorage: storage, Blob, AbortController,
    ResizeObserver: class { constructor(fn) { this.resize = value => fn([{ contentRect: { width: value } }]); observers.push(this) } observe(value) { this.target = value } disconnect() { this.disconnected = true } },
    URL: { createObjectURL: blob => { const value = `blob:isolated/${++serial}`; urls.set(value, blob); calls.created.push(value); return value }, revokeObjectURL: value => { calls.revoked.push(value); urls.delete(value) } },
    setTimeout: (fn, delay) => { const id = ++serial; timers.set(id, { fn, delay }); return id }, clearTimeout: id => timers.delete(id),
    ElMessage: Object.fromEntries(['error', 'warning', 'success'].map(kind => [kind, text => calls.messages.push({ kind, text })])),
    ElMessageBox: { confirm: async () => { throw new Error('unexpected dialog') }, prompt: async () => { throw new Error('unexpected dialog') } },
    DEFAULT_LAYOUT, clamp, readLayout, resizeBounds, writeLayout, renderMarkdown, formatDate, modeNames,
  }
  for (const key of [...Object.keys(defaults), 'askApi', 'renameSessionApi', 'deleteSessionApi', 'feedbackApi']) bindings[key] = (...args) => { calls.api.push([key, ...args]); const fn = api[key] || defaults[key]; assert.equal(typeof fn, 'function', `unexpected ${key}`); return fn(...args) }
  const scope = Vue.effectScope(), state = scope.run(() => compiled[name].create(bindings))
  if (name === 'Layout') state.root.value = Vue.markRaw({ clientWidth: width })
  if (name === 'Chat') state.workspace.value = Vue.markRaw(Object.fromEntries(['openSidebar', 'closeInspector', 'openInspector'].map(key => [key, () => calls.panels.push(key)])))
  const h = { state, props, route, api, storage, calls, urls, timers, listeners, observers, node, document,
    mount: async () => { for (const fn of mount) await fn(); await settle() },
    html: slots => render(name, state, props, slots),
    drain: async delay => { for (const [id, timer] of [...timers]) if (timer.delay <= delay) { timers.delete(id); await timer.fn() } await settle() },
    dispose() { if (!disposed) { disposed = true; unmount.forEach(fn => fn()); scope.stop() } },
  }
  live.add(h); return h
}
test.afterEach(async () => { for (const h of live) { h.dispose(); await h.drain(1000) } live.clear() })
function render(name, state, props, slots = {}) {
  const component = { setup: () => ({ ...state, ...props, formatDate, modeNames }), render: compiled[name].render }
  const app = Vue.createSSRApp({ render: () => Vue.h(component, null, slots) })
  const wrapper = { inheritAttrs: false, setup: (_p, { attrs, slots }) => () => Vue.h('div', attrs, slots.default?.()) }
  for (const key of ['ElButton', 'ElSelect', 'ElOption', 'ElSkeleton']) app.component(key, wrapper)
  app.component('AppIcon', { render: () => null })
  app.component('RouterLink', { props: ['to'], setup: (p, { slots }) => () => Vue.h('a', { href: p.to }, slots.default?.()) })
  app.component('ElDialog', { props: ['modelValue'], setup: (p, { slots }) => () => p.modelValue ? Vue.h('section', {}, slots.default?.()) : null })
  app.component('MarkdownContent', { props: ['content'], setup: p => () => Vue.h('div', { innerHTML: renderMarkdown(p.content) }) })
  app.component('CitationReader', { render: () => null })
  app.component('TranslationPanel', { render: () => null })
  app.component('ResizableWorkspace', { setup: (_p, { slots }) => () => Vue.h('div', {}, [slots.toolbar?.({ toggleSidebar() {}, sidebarOpen: true, toggleInspector() {}, inspectorOpen: false, toggleFocus() {}, focused: false, reset() {} }), slots.sidebar?.(), slots.default?.(), slots.inspector?.({ close() {} })]) })
  return renderToString(app)
}
function elements(html) { const result = []; const walk = node => { if (node.type === 1) result.push(node); for (const child of node.children || []) walk(child) }; walk(parseHtml(html)); return result }
const attribute = (node, name) => node.props.find(prop => prop.type === 6 && prop.name === name)?.value?.content
const event = (key, extra = {}) => ({ key, shiftKey: false, prevented: false, preventDefault() { this.prevented = true }, ...extra })
const apiCalls = (h, name) => h.calls.api.filter(call => call[0] === name)

test('Layout normalizes malformed values, clamps dimensions and persists only layout preferences', () => {
  for (const value of [null, undefined, 'bad', [], { left: NaN, right: Infinity, sidebar: 'false' }]) assert.deepEqual(normalizeLayout(value), DEFAULT_LAYOUT)
  const input = { left: 191.2, right: 900, sidebar: false, content: 'not-a-preference' }
  assert.deepEqual(normalizeLayout(input), { left: 220, right: 480, sidebar: false }); assert.equal(input.left, 191.2)
  assert.equal(normalizeLayout({ left: 248.7, right: 384.1 }).left, 249)
  assert.equal(clamp(-20, 10, 30), 10); assert.equal(clamp(40, 10, 30), 30)
  const store = memory({ bad: '{invalid json' })
  assert.deepEqual(readLayout(store, 'bad'), DEFAULT_LAYOUT)
  writeLayout(store, 'layout', input); assert.deepEqual(JSON.parse(store.data.get('layout')), { left: 220, right: 480, sidebar: false })
  const denied = { getItem() { throw new Error('denied') }, setItem() { throw new Error('quota') } }
  assert.deepEqual(readLayout(denied, 'layout'), DEFAULT_LAYOUT); assert.doesNotThrow(() => writeLayout(denied, 'layout', input))
  assert.deepEqual(readLayout(null, 'layout'), DEFAULT_LAYOUT)
  for (const side of ['left', 'right']) { const bounds = resizeBounds(side, 1600, 300); assert.equal(bounds[0], LIMITS[side][0]); assert.ok(1600 - bounds[1] - 300 - 12 >= 760) }
})

test('Docked column combinations preserve at least 760px for answers whenever available', async () => {
  const h = harness('Layout'); await h.mount()
  for (const width of [360, 400, 899, 1000, 1039, 1126, 1200, 1302, 1366, 1376, 1600, 1920]) {
    h.observers[0].resize(width); await settle()
    for (const left of [220, 240, 320]) for (const right of [320, 360, 480]) {
      Object.assign(h.state.layout, { left, right }); h.state.sidebar.value = true; h.state.inspector.value = true
      const budget = width - (h.state.leftDocked.value ? h.state.leftWidth.value + 6 : 0) - (h.state.rightDocked.value ? h.state.rightWidth.value + 6 : 0)
      assert.ok(budget >= Math.min(760, width), `width=${width}, left=${left}, right=${right}, answer=${budget}`)
    }
  }
  const nodes = elements(await h.html())
  const separators = nodes.filter(node => attribute(node, 'role') === 'separator')
  assert.equal(separators.length, 2)
  assert.equal(attribute(separators[0], 'aria-controls'), h.state.sidebarId)
  assert.equal(Number(attribute(separators[1], 'aria-valuenow')), h.state.rightWidth.value)
})

test('Pointer capture and keyboard resizing enforce bounds, direction, steps and persistence', async () => {
  const h = harness('Layout', { width: 1600 }); await h.mount(); h.state.openInspector()
  const handle = h.node('divider'), e = event(undefined, { button: 0, clientX: 100, pointerId: 9, currentTarget: handle })
  h.state.startResize({ ...e, button: 2 }, 'left'); assert.equal(h.state.drag.value, null)
  h.state.startResize(e, 'left'); assert.equal(handle.pointer, 9); assert.equal(e.prevented, true)
  const original = h.state.layout.left
  h.state.moveResize({ pointerId: 99, clientX: 200 }); assert.equal(h.state.layout.left, original)
  h.state.moveResize({ pointerId: 9, clientX: 180 }); assert.equal(h.state.layout.left, original + 80)
  h.state.moveResize({ pointerId: 9, clientX: 9999 }); assert.equal(h.state.layout.left, h.state.leftBounds.value[1])
  h.state.stopResize(); assert.equal(h.state.drag.value, null)
  h.state.resizeKey(event('Home'), 'left'); assert.equal(h.state.layout.left, 220)
  h.state.resizeKey(event('ArrowRight', { shiftKey: true }), 'left'); assert.equal(h.state.layout.left, 284)
  const right = h.state.rightWidth.value
  h.state.resizeKey(event('ArrowLeft'), 'right'); assert.equal(h.state.layout.right, right + 16)
  h.state.resizeKey(event('End'), 'right'); assert.equal(h.state.layout.right, h.state.rightBounds.value[1])
  h.state.startResize({ ...e, clientX: 500 }, 'right'); h.state.moveResize({ pointerId: 9, clientX: 900 }); h.state.stopResize()
  assert.ok(h.state.layout.right >= 320)
  const saved = readLayout(h.storage, 'paper-workspace-layout-v1'); assert.equal(saved.right, h.state.layout.right)
  const ignored = event('Enter'); h.state.resizeKey(ignored, 'left'); assert.equal(ignored.prevented, false)
  const restored = harness('Layout', { storage: h.storage, width: 1400 }); await restored.mount(); assert.equal(restored.state.layout.right, saved.right)
})

test('Narrow panels use modal overlays, focus cycling/Escape and observer cleanup', async () => {
  const h = harness('Layout', { width: 640 }); await h.mount()
  assert.equal(h.state.sidebarVisible.value, false); assert.equal(h.state.columns.value, 'minmax(0, 1fr)')
  const opener = h.node('opener'), first = h.node('first'), last = h.node('last')
  const panel = Vue.markRaw({ querySelector: () => first, querySelectorAll: () => [first, last] })
  h.state.sidebarElement.value = panel; h.state.inspectorElement.value = panel; opener.focus()
  h.state.openSidebar(); await settle(); assert.equal(h.document.activeElement, first)
  let e = event('Tab', { shiftKey: true, currentTarget: panel }); h.state.trapFocus(e, true); assert.equal(h.document.activeElement, last)
  e = event('Tab', { currentTarget: panel }); h.state.trapFocus(e, true); assert.equal(h.document.activeElement, first)
  const nodes = elements(await h.html()); assert.equal(nodes.filter(node => attribute(node, 'aria-modal') === 'true').length, 1)
  assert.equal(nodes.filter(node => attribute(node, 'role') === 'separator').length, 0)
  h.listeners.get('keydown')(event('Escape')); assert.equal(h.state.sidebarVisible.value, false); assert.equal(h.document.activeElement, opener)
  h.state.openInspector(); await settle(); assert.equal(h.state.inspector.value, true); assert.equal(h.state.sidebarVisible.value, false)
  h.state.toggleFocus(); assert.equal(h.state.columns.value, 'minmax(0, 1fr)'); h.listeners.get('keydown')(event('Escape')); assert.equal(h.state.focused.value, false)
  h.state.resetLayout(); assert.equal(h.state.layout.left, DEFAULT_LAYOUT.left)
  h.dispose(); assert.equal(h.observers[0].disconnected, true); assert.equal(h.listeners.size, 0)
})

test('Storage getters may throw; changing a layout key restores isolated normalized dimensions', async () => {
  const blocked = harness('Layout', { storageGetterThrows: true }); await blocked.mount(); assert.doesNotThrow(() => blocked.state.resetLayout())
  const store = memory({ one: JSON.stringify({ left: 220, right: 310, sidebar: false }), two: JSON.stringify({ left: 999, right: -1, sidebar: true }) })
  const h = harness('Layout', { props: { storageKey: 'one' }, storage: store }); await h.mount()
  assert.equal(h.state.layout.left, 220); assert.equal(h.state.sidebarVisible.value, false)
  h.props.storageKey = 'two'; await settle(); assert.equal(h.state.layout.left, 320); assert.equal(h.state.layout.right, 320); assert.equal(h.state.sidebar.value, true)
})

test('Chat renders only the selected library papers/sessions, including search and route switching', async () => {
  const h = harness('Chat'); await h.mount()
  assert.equal(h.state.currentKbId.value, 1)
  let html = await h.html(); assert.ok(html.includes(paper(1).filename)); assert.ok(!html.includes(paper(2).filename))
  h.state.sidebarTab.value = 'sessions'; html = await h.html()
  assert.ok(html.includes(session(1).title)); assert.ok(!html.includes(session(2).title))
  h.route.query.kb = '2'; await settle(); assert.equal(h.state.currentKbId.value, 2)
  assert.deepEqual(h.state.filteredSessions.value.map(item => item.kb_id), [2])
  html = await h.html(); assert.ok(html.includes(session(2).title)); assert.ok(!html.includes(session(1).title))
  h.state.sessionSearch.value = 'absent'; assert.equal(h.state.filteredSessions.value.length, 0)
  h.state.sidebarTab.value = 'papers'; html = await h.html(); assert.ok(html.includes(paper(2).filename)); assert.ok(!html.includes(paper(1).filename))
  assert.equal(h.storage.data.get('selected-kb-7'), '2')
})

async function renderRetrieval(retrieval) {
  const h = harness('Chat', { api: { sessionMessagesApi: async id => [{ id, role: 'assistant', content: 'ISOLATED_EVIDENCE', retrieval }] } }); await h.mount()
  await h.state.openSession(session(1))
  const requests = h.calls.api.length, nodes = elements(await h.html())
  const details = nodes.find(node => node.tag === 'details' && attribute(node, 'class') === 'retrieval-details')
  assert.ok(details); assert.equal(details.props.some(prop => prop.name === 'open'), false)
  assert.equal(h.calls.api.length, requests); assert.equal(apiCalls(h, 'askApi').length, 0)
  const text = node => node.type === 2 ? node.content : (node.children || []).map(text).join('')
  const summary = details.children.find(node => node.tag === 'summary'); assert.ok(summary)
  return { summary: text(summary), text: text(details) }
}

for (const [name, counts, summary, limited] of [
  ['complete coverage', { covered_documents: 3, total_documents: 3, source_count: 6 }, '论文总览 · 3/3篇资料 · 6条证据', false],
  ['partial coverage', { covered_documents: 2, total_documents: 5, source_count: 4 }, '论文总览 · 2/5篇资料 · 4条证据', true],
]) test(`Chat overview renders ${name} as representative excerpts, not a vector failure`, async () => {
  const result = await renderRetrieval({ method: 'overview', vector_status: 'not_used', vector_hits: 0, keyword_hits: 0, ...counts, coverage_limited: limited })
  assert.equal(result.summary, summary)
  assert.match(result.text, /代表片段.*并非全文通读/)
  assert.equal(result.text.includes('仅覆盖部分资料'), limited)
  assert.doesNotMatch(result.text, /向量|关键词匹配|not_used|undefined|NaN/)
})

test('Chat overview preserves zero evidence and reports empty or uncovered libraries honestly', async () => {
  for (const total of [0, 5]) {
    const result = await renderRetrieval({ method: 'overview', vector_status: 'not_used', source_count: 0, covered_documents: 0, total_documents: total, coverage_limited: total > 0 })
    assert.equal(result.summary, `论文总览 · 0/${total}篇资料 · 0条证据`)
    assert.match(result.text, /代表片段.*并非全文通读/)
    assert.equal(result.text.includes('仅覆盖部分资料'), total > 0)
    assert.doesNotMatch(result.text, /向量|not_used|undefined|NaN/)
  }
})

test('Chat overview uses unknown counts for missing or malformed historical metadata', async () => {
  for (const value of [undefined, null, NaN, -1, '<img src=x onerror=bad()>']) {
    const result = await renderRetrieval({ method: 'overview', vector_status: 'not_used', source_count: 2, covered_documents: value, total_documents: value })
    assert.equal(result.summary, '论文总览 · —/—篇资料 · 2条证据')
    assert.doesNotMatch(result.text, /向量|not_used|undefined|NaN|仅覆盖部分资料|onerror/)
  }
  const result = await renderRetrieval({ method: 'overview', vector_status: 'not_used' })
  assert.equal(result.summary, '论文总览 · —/—篇资料 · —条证据')
})

test('Chat keeps legacy hybrid, vector and keyword retrieval summaries and status details unchanged', async () => {
  for (const [method, name, status, notice] of [
    ['hybrid', '向量 + 关键词', 'ready', ''], ['hybrid', '向量 + 关键词', 'unavailable', ' · 向量不可用'],
    ['vector', '向量检索', 'ready', ''], ['keyword', '关键词检索', 'unconfigured', ' · 向量未配置'],
  ]) {
    const result = await renderRetrieval({ method, source_count: 3, vector_hits: 2, keyword_hits: 1, vector_status: status })
    assert.equal(result.summary, `${name} · 3 条证据`)
    assert.ok(result.text.includes(`向量匹配 2 · 关键词匹配 1${notice}`))
    assert.doesNotMatch(result.text, /论文总览|篇资料|代表片段|undefined|NaN/)
    if (status === 'ready') assert.doesNotMatch(result.text, /向量不可用|向量未配置/)
  }
})

test('Late document success/failure cannot overwrite the new library or its loading state', async () => {
  for (const outcome of ['resolve', 'reject']) {
    const old = deferred(), fresh = deferred(), h = harness('Chat', { api: { docsApi: id => id === 1 ? old.promise : fresh.promise } }); await h.mount()
    h.state.activeCitation.value = citation(1); h.route.query.kb = '2'; await settle()
    assert.equal(h.state.documents.value.length, 0); assert.equal(h.state.activeCitation.value, null)
    old[outcome](outcome === 'resolve' ? { items: [paper(1)], total: 1 } : apiFailure(403)); await settle()
    assert.equal(h.state.docsLoading.value, true); assert.equal(h.state.docsError.value, '')
    fresh.resolve({ items: [paper(2)], total: 1 }); await settle()
    assert.deepEqual(h.state.documents.value.map(item => item.kb_id), [2]); assert.equal(h.state.docsLoading.value, false)
    h.state.docPage.value = 4; h.state.documentSearch.value = ' first '; await settle(); h.state.documentSearch.value = 'vector'; await settle(); await h.drain(220)
    assert.deepEqual(apiCalls(h, 'docsApi').at(-1).slice(1), [2, 1, 30, { q: 'vector' }])
  }
})

test('Switching library invalidates pending history before another session is opened', async () => {
  for (const outcome of ['resolve', 'reject']) {
    const old = deferred(), fresh = deferred(), h = harness('Chat', { api: { sessionMessagesApi: id => id === 11 ? old.promise : fresh.promise } }); await h.mount()
    const first = h.state.openSession(session(1)); h.route.query.kb = '2'; await settle()
    assert.equal(h.state.currentSessionId.value, null); assert.equal(h.state.messages.value.length, 0)
    const second = h.state.openSession(session(2)); fresh.resolve([{ id: 222, role: 'assistant', content: 'KB2_ANSWER' }]); await second
    old[outcome](outcome === 'resolve' ? [{ id: 111, role: 'assistant', content: 'STALE_KB1' }] : apiFailure(404)); await first; await settle()
    assert.equal(h.state.currentSessionId.value, 22); assert.deepEqual(h.state.messages.value.map(item => item.id), [222]); assert.equal(h.state.historyError.value, '')
    assert.equal(h.state.historyLoading.value, false)
  }
})

test('Session refresh races and unmounted document requests discard obsolete responses', async () => {
  const h = harness('Chat'); await h.mount(); const old = deferred(), fresh = deferred()
  let count = 0; h.api.sessionsApi = () => ++count === 1 ? old.promise : fresh.promise
  const first = h.state.refreshSessionList(), second = h.state.refreshSessionList()
  fresh.resolve([session(2)]); await second; old.resolve([session(1)]); await first
  assert.deepEqual(h.state.sessions.value.map(item => item.kb_id), [2])
  const pending = deferred(); h.api.docsApi = () => pending.promise; const load = h.state.loadDocuments(); const before = h.state.documents.value
  h.dispose(); pending.resolve({ items: [paper(99)], total: 1 }); await load; assert.equal(h.state.documents.value, before)
})

test('Sending remains bound to the chosen library; inline source selection uses that answer records', async () => {
  const answer = deferred(), h = harness('Chat', { api: { askApi: () => answer.promise } }); await h.mount()
  h.state.question.value = 'Compare methods'; const send = h.state.send(); await settle()
  assert.deepEqual(apiCalls(h, 'askApi')[0][1], { session_id: null, kb_id: 1, question: 'Compare methods' })
  assert.ok(apiCalls(h, 'askApi')[0][2].signal instanceof AbortSignal)
  assert.equal(typeof apiCalls(h, 'askApi')[0][2].onProgress, 'function')
  h.route.query.kb = '2'; await settle(); await h.state.openSession(session(2)); assert.equal(h.state.currentKbId.value, 1)
  answer.resolve({ session_id: 11, message_id: 101, answer: 'Evidence [来源1]', mode: 'local', generation_status: 'generated', citations: [citation(1)] }); await send
  const message = h.state.messages.value.at(-1); assert.equal(message.kb_id, 1)
  h.state.openRank(message, 1); assert.equal(h.state.activeCitation.value.doc_id, 10); assert.ok(h.calls.panels.includes('openInspector'))
  h.state.openRank(message, 999); assert.equal(h.state.activeCitation.value.doc_id, 10); assert.equal(h.calls.messages.at(-1).kind, 'warning')
})

test('Reader PDF preview uses scoped file requests and revokes its URL on citation switch/unmount', async () => {
  const h = harness('Reader', { props: { kbId: 1, citation: citation(1) } }); await h.state.showOriginal()
  assert.deepEqual(apiCalls(h, 'documentFileApi')[0], ['documentFileApi', 1, 10]); assert.equal(h.urls.size, 1)
  const frame = elements(await h.html()).find(node => node.tag === 'iframe'); assert.equal(attribute(frame, 'src'), h.state.fileUrl.value + '#page=3&view=FitH')
  const first = h.state.fileUrl.value; h.props.kbId = 2; h.props.citation = citation(2); await settle()
  assert.equal(h.state.view.value, 'excerpt'); assert.equal(h.urls.size, 0); assert.ok(h.calls.revoked.includes(first))
  await h.state.showOriginal(); assert.deepEqual(apiCalls(h, 'documentFileApi').at(-1), ['documentFileApi', 2, 20])
  h.dispose(); assert.equal(h.urls.size, 0); assert.equal(h.state.fileBlob, null)
})

test('Reader drops late original responses/errors, including text decoding after a switch', async () => {
  for (const outcome of ['resolve', 'reject']) {
    const late = deferred(), h = harness('Reader', { props: { kbId: 1, citation: citation(1) }, api: { documentFileApi: id => id === 1 ? late.promise : Promise.resolve(pdf()) } })
    const first = h.state.showOriginal(); h.props.kbId = 2; h.props.citation = citation(2); await settle(); await h.state.showOriginal()
    const url = h.state.fileUrl.value; late[outcome](outcome === 'resolve' ? pdf() : apiFailure(403)); await first
    assert.equal(h.state.fileUrl.value, url); assert.equal(h.state.error.value, ''); assert.equal(h.calls.created.length, 1)
  }
  const text = deferred(), blob = new Blob(['pending text']); blob.text = () => text.promise
  const h = harness('Reader', { props: { kbId: 1, citation: { ...citation(1), doc_name: 'paper.txt' } }, api: { documentFileApi: async () => blob } })
  const first = h.state.loadFile(); await settle(); h.props.kbId = 2; h.props.citation = citation(2); await settle(); text.resolve('STALE_TEXT'); await first
  assert.equal(h.state.textContent.value, null); assert.equal(h.state.fileBlob, null)
})

test('Reader reports forbidden/missing/empty files honestly and an ordinary retry can recover', async () => {
  for (const failure of [apiFailure(403), apiFailure(404), new Blob([])]) {
    let count = 0
    const h = harness('Reader', { props: { kbId: 1, citation: citation(1) }, api: { documentFileApi: async () => { if (++count === 1) { if (failure instanceof Blob) return failure; throw failure } return pdf() } } })
    await h.state.showOriginal(); assert.equal(h.state.loading.value, false); assert.ok(h.state.error.value); assert.equal(h.urls.size, 0)
    assert.ok(elements(await h.html()).some(node => attribute(node, 'role') === 'alert'))
    await h.state.loadFile(); assert.equal(count, 2); assert.equal(h.state.error.value, ''); assert.equal(h.urls.size, 1)
  }
})

test('A failed Blob text read must not cache an unusable file and bypass Retry', async () => {
  const broken = new Blob(['text']); broken.text = async () => { throw new Error('fixture read failure') }
  let count = 0
  const h = harness('Reader', { props: { kbId: 1, citation: { ...citation(1), doc_name: 'paper.txt' } }, api: { documentFileApi: async () => ++count === 1 ? broken : new Blob(['RECOVERED_TEXT']) } })
  await h.state.showOriginal(); assert.ok(h.state.error.value)
  await h.state.loadFile()
  assert.equal(count, 2, 'Retry must fetch again after blob.text() rejects; a failed cache is not success')
  assert.equal(h.state.textContent.value, 'RECOVERED_TEXT'); assert.equal(h.state.error.value, '')
})

test('Switching citation during a cached download must not label the old Blob as the new paper', async () => {
  const h = harness('Reader', { props: { kbId: 1, citation: citation(1) } }); await h.state.loadFile()
  const originalBlob = h.state.fileBlob, originalName = h.props.citation.doc_name
  const pending = h.state.downloadOriginal()
  h.props.kbId = 2; h.props.citation = citation(2)
  await pending; await settle()
  const mismatched = h.calls.downloads.filter(item => item.name !== originalName || item.blob !== originalBlob)
  assert.deepEqual(mismatched.map(item => ({ filename: item.name, retainedOriginalBlob: item.blob === originalBlob })), [],
    'A superseded download must cancel or retain its original file/metadata, never combine identities')
})

test('HTML/SVG stay download-only; text previews escape markup and download URLs are released', async () => {
  const attack = '<svg onload="globalThis.PWNED=1"><script>bad()</script></svg>'
  for (const [name, type] of [['paper.pdf', 'text/html'], ['paper.svg', 'application/pdf']]) {
    const h = harness('Reader', { props: { kbId: 1, citation: { ...citation(1), doc_name: name } }, api: { documentFileApi: async () => new Blob([attack], { type }) } })
    await h.state.showOriginal(); assert.equal(h.state.isPdf.value, false); assert.equal(h.state.fileUrl.value, '')
    assert.equal(elements(await h.html()).some(node => ['iframe', 'svg', 'script'].includes(node.tag)), false)
    await h.state.downloadOriginal(); assert.equal(h.calls.downloads.length, 1); assert.equal(h.urls.size, 1)
    h.dispose(); await h.drain(1000); assert.equal(h.urls.size, 0)
  }
  const h = harness('Reader', { props: { kbId: 1, citation: { ...citation(1), doc_name: 'paper.md' } }, api: { documentFileApi: async () => new Blob([attack], { type: 'text/markdown' }) } })
  await h.state.showOriginal(); assert.equal(h.state.textContent.value, attack)
  const nodes = elements(await h.html()); assert.ok(nodes.some(node => node.tag === 'pre')); assert.equal(nodes.some(node => ['svg', 'script', 'iframe'].includes(node.tag)), false)
})

test('Unmounting an original request prevents URL creation after its late response', async () => {
  const late = deferred(), h = harness('Reader', { props: { kbId: 1, citation: citation(1) }, api: { documentFileApi: () => late.promise } })
  const pending = h.state.showOriginal(); h.dispose(); late.resolve(pdf()); await pending
  assert.equal(h.calls.created.length, 0); assert.equal(h.state.fileBlob, null)
})

test('Markdown renders bounded inline citation buttons but never executable imported HTML', () => {
  const input = '[来源1] [来源999] [来源0] [来源1000] `code [来源2]`\n\n```txt\n[来源3]\n```\n\n<script>globalThis.PWNED=1</script>\n\n<img src=x onerror=bad()>\n\n<button data-source-rank="99" onclick="bad()">fake</button>\n\n![<svg onload=bad()>](https://example.invalid/image.png)\n\n[bad](javascript:alert(1)) [encoded](jav&#x61;script:alert(1)) [data](data:text/html,bad) [relative](//example.invalid/x) [safe](https://example.invalid/paper?a=1&b=2)'
  const html = renderMarkdown(input), nodes = elements(html)
  assert.deepEqual(nodes.filter(node => node.tag === 'button').map(node => attribute(node, 'data-source-rank')), ['1', '999'])
  assert.equal(nodes.some(node => ['script', 'img', 'svg', 'iframe', 'style', 'object'].includes(node.tag)), false)
  for (const node of nodes) assert.equal(node.props.some(prop => /^on/i.test(prop.name || '')), false)
  const links = nodes.filter(node => node.tag === 'a'); assert.equal(links.length, 1)
  assert.equal(attribute(links[0], 'href'), 'https://example.invalid/paper?a=1&b=2'); assert.equal(attribute(links[0], 'rel'), 'noopener noreferrer')
  assert.equal(safeLink('javascript:bad()'), null); assert.equal(safeLink('https://example.invalid/" onclick="bad'), null)
  assert.equal(escapeHtml('<>&"\''), '&lt;&gt;&amp;&quot;&#39;')
})

test('MarkdownContent dispatches scoped citation clicks and reactive content remains escaped', async () => {
  const h = harness('Markdown', { props: { content: '[来源7]' } })
  const button = { dataset: { sourceRank: '7' } }, inside = { contains: node => node === button }
  h.state.onSourceClick({ target: { closest: () => button }, currentTarget: inside })
  assert.deepEqual(h.calls.emitted, [['source', 7]])
  h.state.onSourceClick({ target: { closest: () => button }, currentTarget: { contains: () => false } })
  h.state.onSourceClick({ target: {}, currentTarget: inside }); assert.equal(h.calls.emitted.length, 1)
  h.props.content = '<img src=x onerror=bad()>\n\n[来源2]'; await settle()
  const nodes = elements(await h.html()); assert.equal(nodes.some(node => node.tag === 'img'), false)
  assert.equal(attribute(nodes.find(node => node.tag === 'button'), 'data-source-rank'), '2')
})

for (const size of [2001, 40001]) test(`Chat preserves ${size} characters from route, suggestion and request through a complete response`, async () => {
  const text = '文'.repeat(size), output = '# 完整回答\n' + 'long answer 文🙂\n'.repeat(5000)
  const h = harness('Chat', { query: { q: text }, api: { askApi: async () => ({ session_id: 11, message_id: 101, answer: output, mode: 'local', generation_status: 'generated' }) } }); await h.mount()
  assert.equal(h.state.question.value, text)
  const input = elements(await h.html()).find(node => attribute(node, 'id') === 'question-input')
  assert.equal(attribute(input, 'maxlength'), undefined); assert.doesNotMatch(await h.html(), /\/2000/)
  h.state.useSuggestion(text + '\n尾部'); assert.equal(h.state.question.value, text + '\n尾部')
  await h.state.send()
  assert.equal(apiCalls(h, 'askApi')[0][1].question, text + '\n尾部')
  assert.equal(h.state.messages.value.at(-1).content, output)
  assert.equal(h.state.messages.value.filter(message => message.role === 'assistant').length, 1)
})

test('Chat progress is not answer content; Stop aborts once, preserves the exact draft and ignores late progress/results', async () => {
  const answer = deferred(), h = harness('Chat', { api: { askApi: () => answer.promise } }); await h.mount()
  const text = '  完整问题\n' + '文'.repeat(40001) + '\n  '
  h.state.question.value = text
  const sending = h.state.send(); await settle(); await h.state.send()
  assert.equal(apiCalls(h, 'askApi').length, 1)
  const options = apiCalls(h, 'askApi')[0][2]
  options.onProgress({ event: 'status', stage: 'generating', received_chars: 40001 })
  options.onProgress({ event: 'heartbeat', stage: 'generating', elapsed_seconds: 181 })
  let html = await h.html()
  assert.match(html, /综合生成/); assert.match(html, /已接收 40001 字符/); assert.match(html, /一次显示完整回答/)
  const stop = elements(html).find(node => node.tag === 'button' && (node.children || []).some(child => child.type === 2 && child.content.includes('停止')))
  assert.ok(stop); assert.equal(attribute(stop, 'disabled'), undefined)
  assert.equal(h.state.messages.value.filter(message => message.role === 'assistant').length, 0)
  options.onProgress({ event: 'status', stage: 'validating', received_chars: 80000 })
  assert.match(await h.html(), /校验完整回答/)
  h.state.stopAsking(); h.state.stopAsking()
  assert.equal(options.signal.aborted, true); assert.equal(h.state.asking.value, false)
  assert.equal(h.state.question.value, text); assert.match(h.state.sendError.value, /停止.*刷新/)
  assert.equal(h.state.messages.value.length, 0)
  options.onProgress({ event: 'status', stage: 'generating', received_chars: 99999 })
  answer.resolve({ session_id: 99, message_id: 999, answer: 'LATE_ANSWER', generation_status: 'generated' }); await sending
  assert.equal(h.state.currentSessionId.value, null); assert.equal(h.state.messages.value.length, 0)
  assert.equal(h.state.question.value, text); assert.doesNotMatch(await h.html(), /LATE_ANSWER|99999/)
  assert.equal(apiCalls(h, 'askApi').length, 1)
})

test('A stopped request cannot unlock or overwrite a new request or another library', async () => {
  const old = deferred(), fresh = deferred()
  const h = harness('Chat', { api: { askApi: data => data.kb_id === 1 ? old.promise : fresh.promise } }); await h.mount()
  h.state.question.value = 'OLD_QUESTION'; const first = h.state.send(); await settle()
  h.state.stopAsking(); h.state.currentKbId.value = 2; h.state.onKbChange(); await settle()
  h.state.question.value = 'NEW_QUESTION'; const second = h.state.send(); await settle()
  old.reject(apiFailure(503)); await first
  assert.equal(h.state.asking.value, true); assert.equal(h.state.sendError.value, '')
  assert.equal(h.state.messages.value.length, 1); assert.equal(h.state.messages.value[0].content, 'NEW_QUESTION')
  fresh.resolve({ session_id: 22, message_id: 202, answer: 'NEW_ANSWER', mode: 'local', generation_status: 'generated' }); await second
  assert.equal(h.state.currentKbId.value, 2); assert.equal(h.state.currentSessionId.value, 22)
  assert.equal(h.state.messages.value.at(-1).content, 'NEW_ANSWER'); assert.equal(h.state.asking.value, false)
})

test('Forced KB changes and unmount both abort an in-flight answer without leaking its body into the next view', async () => {
  for (const action of ['switch', 'unmount']) {
    const answer = deferred(), h = harness('Chat', { api: { askApi: () => answer.promise } }); await h.mount()
    h.state.question.value = 'PRIVATE_OLD_QUESTION'; const sending = h.state.send(); await settle()
    const options = apiCalls(h, 'askApi')[0][2]
    if (action === 'switch') { h.state.currentKbId.value = 2; h.state.onKbChange(); await settle() } else h.dispose()
    assert.equal(options.signal.aborted, true)
    answer.resolve({ session_id: 11, message_id: 101, answer: 'PRIVATE_LATE_BODY', generation_status: 'generated' }); await sending
    assert.equal(h.state.messages.value.some(message => message.content === 'PRIVATE_LATE_BODY'), false)
    assert.notEqual(h.state.currentSessionId.value, 11)
  }
})

test('Chat stream failure is explicit and never retries or appends an incomplete assistant', async () => {
  const h = harness('Chat', { api: { askApi: async () => { throw { response: { status: 502, data: { detail: '未收到完整结果' } } } } } }); await h.mount()
  h.state.question.value = '文'.repeat(40001); await h.state.send()
  assert.equal(h.state.messages.value.length, 0); assert.equal(h.state.question.value.length, 40001)
  assert.match(h.state.sendError.value, /未收到完整结果/); assert.match(h.state.sendError.value, /刷新/)
  assert.equal(apiCalls(h, 'askApi').length, 1); assert.equal(h.state.asking.value, false)
})

test('Only a generated new answer displays the model body; degraded answers retain citations with an honest label', async () => {
  const h = harness('Chat', { api: { askApi: async () => ({ session_id: 11, message_id: 101, answer: 'UNVALIDATED_EXCERPT', generation_status: 'unavailable', mode: 'degraded', notice: '生成未完成，请稍后重试。', citations: Array.from({ length: 34 }, (_, index) => ({ ...citation(1), rank: index + 1, doc_id: 10 + index })) }) } }); await h.mount()
  h.state.question.value = 'Summarize'; await h.state.send()
  const message = h.state.messages.value.at(-1), html = await h.html()
  assert.notEqual(message.content, 'UNVALIDATED_EXCERPT'); assert.match(html, /生成未完成/)
  assert.doesNotMatch(html, /UNVALIDATED_EXCERPT|检索证据/)
  assert.equal(message.citations[0].doc_id, 10); assert.equal(message.citations.length, 34)
  h.state.openRank(message, 34); assert.equal(h.state.activeCitation.value.doc_id, 43)
  h.state.openRank(message, 1); assert.equal(h.state.activeCitation.value.doc_id, 10)
})

test('Translation opens independently or from its exact assistant source and closes on a library change', async () => {
  const h = harness('Chat'); await h.mount()
  h.state.openTranslation(); assert.equal(h.state.translationVisible.value, true)
  assert.deepEqual(h.state.translationSource.value, { text: '', messageId: null })
  const message = { id: 101, role: 'assistant', content: '# ORIGINAL\n[来源1]', status: 'generated' }
  h.state.messages.value = [message]; h.state.openTranslation(message)
  assert.deepEqual(h.state.translationSource.value, { text: message.content, messageId: 101 })
  assert.match(await h.html(), /中英翻译/); assert.match(await h.html(), />翻译/)
  assert.equal(h.state.messages.value[0].content, message.content)
  h.state.currentKbId.value = 2; h.state.onKbChange(); await settle()
  assert.equal(h.state.translationVisible.value, false)
  h.state.openTranslation({ id: 1, role: 'user', content: 'NOT_AN_ANSWER' })
  assert.equal(h.state.translationVisible.value, false)
})

test('A backend 499 preserves the entire question and asks for a refresh without saving an assistant or replaying', async () => {
  const h = harness('Chat', { api: { askApi: async () => { throw { response: { status: 499, data: { detail: '请求已取消' } } } } } }); await h.mount()
  const question = '  ' + '文'.repeat(40001) + '\n'
  h.state.question.value = question; await h.state.send()
  assert.equal(h.state.question.value, question); assert.equal(h.state.asking.value, false)
  assert.equal(h.state.messages.value.length, 0); assert.match(h.state.sendError.value, /停止.*服务器已完成.*刷新/)
  assert.equal(apiCalls(h, 'askApi').length, 1)
})


test('UI-R2 migration preserves legacy values and isolates user layout keys', () => {
  const key = 'paper-workspace-layout-v1-42', target = 'paper-workspace-layout-v2-42'
  const old = JSON.stringify({left:248,right:384,sidebar:false})
  const store = memory({[key]:old})
  assert.deepEqual(readLayout(store,key), {left:248,right:384,sidebar:false})
  assert.equal(store.data.get(key), old)
  assert.deepEqual(JSON.parse(store.data.get(target)), {left:248,right:384,sidebar:false})
  writeLayout(store,key,{left:240,right:360,sidebar:true,secret:'not-persisted'})
  assert.equal(store.data.get(key), old)
  assert.equal(JSON.parse(store.data.get(target)).secret, undefined)
  assert.deepEqual(readLayout(store,'paper-workspace-layout-v1-43'),DEFAULT_LAYOUT)
})

test('UI-R2 landing and automatic source collapse never overwrite the saved sidebar preference', async () => {
  const h = harness('Layout',{width:1376,props:{landing:true}}); await h.mount()
  assert.equal(h.state.sidebar.value,true); assert.equal(h.state.sidebarVisible.value,false)
  h.props.landing=false; await settle(); assert.equal(h.state.leftDocked.value,true)
  h.state.openInspector(); assert.equal(h.state.leftDocked.value,true)
  h.observers[0].resize(1302); await settle()
  assert.equal(h.state.leftDocked.value,false); assert.equal(h.state.rightDocked.value,true)
  assert.equal(h.state.sidebar.value,true)
  h.state.closeInspector(); assert.equal(h.state.leftDocked.value,true)
  h.dispose()
})
