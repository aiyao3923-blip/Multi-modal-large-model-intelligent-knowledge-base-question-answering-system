import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import * as Vue from 'vue'
import { renderToString } from '@vue/server-renderer'
import { parse, compileScript, compileTemplate, compileStyle } from '@vue/compiler-sfc'
import { compile, parse as parseHtml } from '@vue/compiler-dom'
import { formatDate, modeNames } from '../src/utils/format.js'
import { renderMarkdown } from '../src/utils/markdown.js'

// Execute the actual Chat setup + render, using the existing SFC compilation style.
// All API, clipboard, storage, timers and panel nodes are isolated doubles; no server,
// persisted user data, native downloads or browser is involved in these assertions.
const url = new URL('../src/views/chat/ChatView.vue', import.meta.url)
const source = await readFile(url, 'utf8')
const css = await readFile(new URL('../src/views/chat/chat.css', import.meta.url), 'utf8')
const { descriptor, errors } = parse(source, { filename: 'ChatView.vue' })
assert.deepEqual(errors, [])
const script = compileScript(descriptor, { id: 'ui-chat-r2' })
assert.deepEqual(compileTemplate({ source: descriptor.template.content, filename: 'ChatView.vue', id: 'ui-chat-r2', compilerOptions: { bindingMetadata: script.bindings } }).errors, [])
assert.deepEqual(compileStyle({ source: css, filename: 'chat.css', id: 'ui-chat-r2', scoped: true }).errors, [])
const identifiers = node => !node ? [] : node.type === 'Identifier' ? [node.name] : node.type === 'ObjectPattern' ? node.properties.flatMap(item => identifiers(item.value || item.argument)) : node.type === 'ArrayPattern' ? node.elements.flatMap(identifiers) : identifiers(node.left || node.argument)
const statements = script.scriptSetupAst.filter(node => node.type !== 'ImportDeclaration')
const code = statements.map(node => descriptor.scriptSetup.content.slice(node.start, node.end)).join('\n')
const names = statements.flatMap(node => node.type === 'FunctionDeclaration' ? [node.id.name] : node.type === 'VariableDeclaration' ? node.declarations.flatMap(item => identifiers(item.id)) : [])
const render = new Function('Vue', compile(descriptor.template.content, { mode: 'function', prefixIdentifiers: true }).code)(Vue)
const create = bindings => new Function('bindings', `const {${Object.keys(bindings)}} = bindings;\n${code}\nreturn {${names.map(key => `get ${key}(){return ${key}}`)}}`)(bindings)
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }
const settle = async () => { for (let i = 0; i < 8; i++) { await Promise.resolve(); await Vue.nextTick() } }
const kb = (id = 1, extra = {}) => ({ id, name: `研究资料库 ${id}`, owner_id: 7, status: 'enabled', doc_count: 4, granted_roles: [], ...extra })
const doc = (id = 1, status = 'success') => ({ id, filename: `真实资料_${id}.pdf`, status, page_count: 12 })
const result = (extra = {}) => ({ session_id: 11, message_id: 12, answer: '完整答案', mode: 'local', generation_status: 'generated', citations: [], ...extra })
const live = new Set()
function harness({ api = {}, libraries = [kb(), kb(2)], actor = { id: 7 }, query = {} } = {}) {
  const mount = [], unmount = [], timers = new Map(), store = new Map(), calls = { api: [], panels: [], copy: [], download: [], notice: [], workspaceProps: [], focus: 0 }
  const user = Vue.reactive({ user: actor, role: 'user', isAdmin: false }), route = Vue.reactive({ query })
  const defaults = { kbsApi: async () => libraries, sessionsApi: async () => [], capabilitiesApi: async () => ({ mode: 'local' }), docsApi: async () => ({ items: [doc(1), doc(2, 'parsing'), doc(3, 'failed'), doc(4)], total: 4 }), sessionMessagesApi: async () => [] }
  let serial = 0
  const bindings = {
    ref: Vue.ref, computed: Vue.computed, watch: Vue.watch, nextTick: Vue.nextTick, onMounted: fn => mount.push(fn), onBeforeUnmount: fn => unmount.push(fn), useUserStore: () => user, useRoute: () => route,
    localStorage: { getItem: key => store.get(key) ?? null, setItem: (key, value) => store.set(key, value) },
    setTimeout: (fn, delay) => { timers.set(++serial, { fn, delay }); return serial }, clearTimeout: id => timers.delete(id),
    navigator: { clipboard: { writeText: async text => calls.copy.push(text) } }, AbortController,
    ElMessage: Object.fromEntries(['success', 'warning', 'error'].map(kind => [kind, text => calls.notice.push({ kind, text })])),
    ElMessageBox: { prompt: async () => { throw new Error('Unexpected prompt') }, confirm: async () => { throw new Error('Unexpected confirm') } },
    formatDate, modeNames, downloadText: (...args) => calls.download.push(args),
  }
  for (const name of [...Object.keys(defaults), 'askApi', 'renameSessionApi', 'deleteSessionApi', 'feedbackApi']) bindings[name] = (...args) => {
    calls.api.push([name, ...args]); const fn = api[name] || defaults[name]; assert.equal(typeof fn, 'function', `Unexpected ${name}`); return fn(...args)
  }
  const scope = Vue.effectScope(), state = scope.run(() => create(bindings))
  state.workspace.value = Vue.markRaw(Object.fromEntries(['openSidebar', 'openInspector', 'closeInspector'].map(key => [key, () => calls.panels.push(key)])))
  const h = { state, user, route, api, calls, store,
    async mount() { mount.forEach(fn => fn()); await settle() },
    async html({ sidebar = false } = {}) {
      const component = { setup: () => ({ ...state, formatDate, modeNames }), render }
      const app = Vue.createSSRApp(component)
      const wrapper = { setup: (_p, { attrs, slots }) => () => Vue.h('div', attrs, slots.default?.()) }
      for (const name of ['ElButton', 'ElSelect', 'ElOption', 'ElSkeleton']) app.component(name, wrapper)
      app.component('AppIcon', { render: () => null })
      app.component('RouterLink', { props: ['to'], setup: (p, { slots }) => () => Vue.h('a', { href: p.to }, slots.default?.()) })
      app.component('ElDialog', { props: ['modelValue'], setup: (p, { slots }) => () => p.modelValue ? Vue.h('section', null, [slots.default?.(), slots.footer?.()]) : null })
      app.component('MarkdownContent', { props: ['content'], setup: p => () => Vue.h('div', { class: 'rendered-answer', innerHTML: renderMarkdown(p.content) }) })
      app.component('CitationReader', { render: () => null })
      app.component('TranslationPanel', { props: ['sourceText'], setup: p => () => Vue.h('aside', { class: 'translation-fixture' }, p.sourceText) })
      app.component('ResizableWorkspace', { props: ['landing', 'storageKey'], setup: (p, { slots }) => () => {
        calls.workspaceProps.push({ landing: p.landing, storageKey: p.storageKey })
        return Vue.h('div', null, [slots.toolbar?.({ toggleSidebar() {}, sidebarOpen: sidebar, toggleInspector() {}, inspectorOpen: false, toggleFocus() {}, focused: false, reset() {} }), sidebar ? slots.sidebar?.() : null, slots.default?.()])
      } })
      return renderToString(app)
    },
    dispose() { unmount.forEach(fn => fn()); scope.stop(); timers.clear() },
  }
  live.add(h); return h
}
test.afterEach(() => { for (const h of live) h.dispose(); live.clear() })
const elements = html => { const list = []; const walk = n => { if (n.type === 1) list.push(n); (n.children || []).forEach(walk) }; walk(parseHtml(html)); return list }
const attr = (node, name) => node.props.find(p => p.type === 6 && p.name === name)?.value?.content
const hasAttr = (node, name) => node.props.some(p => p.type === 6 && p.name === name)
const text = node => node.type === 2 ? node.content : (node.children || []).map(text).join('')
const questions = html => elements(html).filter(n => n.tag === 'textarea' && attr(n, 'id') === 'question-input')
const apiCalls = (h, name) => h.calls.api.filter(call => call[0] === name)

test('UI-R2 landing renders only three real files, honest scope, one composer and v1 key + landing prop', async () => {
  const h = harness(); await h.mount(); const html = await h.html(), nodes = elements(html)
  assert.equal(h.state.isLanding.value, true); assert.equal(questions(html).length, 1)
  assert.match(html, /让资料成为有出处的回答/); assert.match(html, /私人知识库/); assert.match(html, /2 份资料已就绪/)
  assert.match(html, /真实资料_1.pdf/); assert.match(html, /真实资料_2.pdf/); assert.match(html, /真实资料_3.pdf/); assert.doesNotMatch(html, /真实资料_4.pdf/)
  assert.match(html, /解析中/); assert.match(html, /解析失败/); assert.match(html, /库中资料，不代表本次已引用/)
  assert.equal(nodes.filter(n => attr(n, 'class')?.includes('rendered-answer')).length, 0)
  assert.deepEqual(h.calls.workspaceProps.at(-1), { landing: true, storageKey: 'paper-workspace-layout-v1-7' })
  assert.equal(nodes.filter(n => n.tag === 'label' && attr(n, 'for') === 'question-input').map(text).join(''), '你的问题')
  assert.ok(html.indexOf('提问输入区') < html.indexOf('landing-documents'), 'mobile DOM order puts the real composer before document summary')
  assert.equal(h.store.has('paper-workspace-layout-v1-7'), false)
})

test('UI-R2 focus and suggestions fill the same draft without invoking ask', async () => {
  const h = harness(); await h.mount()
  h.state.questionInput.value = { focus: () => h.calls.focus++ }
  h.state.question.value = '尚未发送的草稿'; await h.state.focusQuestion()
  assert.equal(h.state.question.value, '尚未发送的草稿'); assert.equal(h.calls.focus, 1)
  h.state.useSuggestion(h.state.suggestions[2]); await settle()
  assert.equal(h.state.question.value, h.state.suggestions[2]); assert.equal(h.calls.focus, 2)
  assert.equal(apiCalls(h, 'askApi').length, 0); assert.equal(questions(await h.html()).length, 1)
})

test('UI-R2 switches to wide reading for pending question, shows only process statistics, and restores draft on stop', async () => {
  const pending = deferred(), h = harness({ api: { askApi: () => pending.promise } }); await h.mount()
  const draft = '  ' + '问题🙂'.repeat(13000) + '\nEND_OF_DRAFT  '
  h.state.question.value = draft; const sending = h.state.send(); await settle()
  assert.equal(h.state.isLanding.value, false); assert.equal(h.state.messages.value.length, 1)
  const request = apiCalls(h, 'askApi')[0]; assert.equal(request[1].question, draft)
  request[2].onProgress({ stage: 'validating', received_chars: 50000, elapsed_seconds: 31, answer: 'NEVER_SHOW_UNVALIDATED_BODY' })
  const working = await h.html(); assert.equal(questions(working).length, 1); assert.match(working, /停止/); assert.match(working, /正在校验完整回答/); assert.match(working, /50000 字符/)
  assert.doesNotMatch(working, /NEVER_SHOW_UNVALIDATED_BODY|让资料成为有出处的回答/)
  h.state.stopAsking(); assert.equal(request[2].signal.aborted, true)
  assert.equal(h.state.question.value, draft); assert.equal(h.state.isLanding.value, true)
  pending.resolve(result({ answer: 'OBSOLETE_RESULT' })); await sending
  assert.equal(h.state.messages.value.length, 0); assert.equal(apiCalls(h, 'askApi').length, 1)
  const stopped = await h.html(); assert.equal(questions(stopped).length, 1); assert.match(stopped, /END_OF_DRAFT/); assert.doesNotMatch(stopped, /OBSOLETE_RESULT/)
})

test('UI-R2 complete answer renders wide, retains every source, and full copy / export / translation', async () => {
  const answer = '# 结果\n' + '完整内容🙂 '.repeat(9000) + '\nANSWER_TAIL'
  const citations = Array.from({ length: 20 }, (_, i) => ({ rank: i + 1, doc_id: 100 + i, doc_name: `引用文档_${i + 1}.pdf`, page_no: i ? null : 3, content_excerpt: '证据_' + i }))
  const h = harness({ api: { askApi: async () => result({ answer, citations }) } }); await h.mount()
  h.state.question.value = '请总结'; await h.state.send(); const html = await h.html()
  assert.equal(h.state.isLanding.value, false); assert.equal(questions(html).length, 1); assert.doesNotMatch(html, /让资料成为有出处的回答/)
  assert.match(html, /ANSWER_TAIL/); assert.match(html, /20 个引用来源/); assert.match(html, /引用文档_20.pdf/)
  const disclosure = elements(html).find(n => attr(n, 'class') === 'answer-sources'); assert.equal(hasAttr(disclosure, 'open'), false)
  const message = h.state.messages.value.at(-1); h.state.openRank(message, 20)
  assert.equal(h.state.activeCitation.value.doc_id, 119); assert.equal(h.state.sourceList.value.length, 20); assert.ok(h.calls.panels.includes('openInspector'))
  await h.state.copyMessage(message); assert.equal(h.calls.copy.at(-1), answer)
  h.state.openTranslation(message); assert.deepEqual(h.state.translationSource.value, { text: answer, messageId: 12 })
  h.state.exportConversation(); await h.state.copyExport()
  assert.match(h.state.exportData.value.content, /ANSWER_TAIL/); assert.match(h.state.exportData.value.content, /引用文档_20.pdf/); assert.match(h.state.exportData.value.content, /证据_19/)
  assert.equal(h.calls.copy.at(-1), h.state.exportData.value.content)
  h.state.currentKbId.value = 2; h.state.onKbChange(); await settle()
  assert.equal(h.state.translationVisible.value, false); assert.equal(h.state.sourceList.value.length, 0); assert.equal(h.state.activeCitation.value, null)
})

test('UI-R2 failures and terminal non-generated results never masquerade as a complete answer', async () => {
  for (const response of ['network', result({ answer: '' }), result({ answer: 'UNVALIDATED_PARTIAL', generation_status: 'failed', mode: 'degraded', notice: '生成未完成' })]) {
    const h = harness({ api: { askApi: async () => { if (response === 'network') throw new Error('offline'); return response } } }); await h.mount()
    h.state.question.value = '保留原始问题'; await h.state.send(); const html = await h.html()
    assert.doesNotMatch(html, /UNVALIDATED_PARTIAL/); assert.equal(questions(html).length, 1)
    if (response?.generation_status !== 'failed') { assert.equal(h.state.question.value, '保留原始问题'); assert.equal(h.state.isLanding.value, true); assert.match(html, /问题已保留/) }
    else assert.match(html, /生成未完成/)
    assert.equal(apiCalls(h, 'askApi').length, 1)
  }
})

test('UI-R2 distinguishes no library, owner empty library, shared read-only and explicit denied management', async () => {
  const none = harness({ libraries: [] }); await none.mount(); const empty = await none.html()
  assert.equal(questions(empty).length, 0); assert.match(empty, /新建知识库/); assert.equal(none.state.isLanding.value, false)
  for (const library of [kb(1, { doc_count: 0 }), kb(1, { doc_count: 0, owner_id: 88 }), kb(1, { doc_count: 0, can_manage: false })]) {
    const h = harness({ libraries: [library], api: { docsApi: async () => ({ items: [], total: 0 }) } }); await h.mount()
    const html = await h.html({ sidebar: true }), readOnly = library.owner_id !== 7 || library.can_manage === false
    assert.equal(h.state.canManageKb.value, !readOnly); assert.equal(h.state.noReadyDocuments.value, true)
    assert.equal(hasAttr(questions(html)[0], 'disabled'), true)
    h.state.question.value = '不会绕过空资料防线'; await h.state.send(); assert.equal(apiCalls(h, 'askApi').length, 0)
    if (readOnly) {
      assert.match(html, /共享 · 只读/); assert.match(html, /联系库所有者/)
      for (const action of elements(html).filter(n => ['button', 'a'].includes(n.tag))) assert.doesNotMatch(text(action), /上传|管理资料/)
    } else assert.match(html, /上传 \/ 查看导入任务/)
  }
  const server = harness({ libraries: [kb(1, { owner_id: 88, can_manage: true })] }); await server.mount(); assert.equal(server.state.canManageKb.value, true)
})

test('UI-R2 counts readiness only for a complete unfiltered snapshot and never labels a partial page as complete', async () => {
  const h = harness({ libraries: [kb(1, { doc_count: 80 })], api: { docsApi: async () => ({ items: [doc(1, 'pending'), doc(2, 'failed')], total: 80 }) } }); await h.mount()
  assert.equal(h.state.readyDocumentCount.value, null); assert.equal(h.state.noReadyDocuments.value, false)
  assert.match(await h.html(), /80 份资料 · 就绪数量待确认/)
  h.api.docsApi = async () => ({ items: [doc(2, 'failed')], total: 1 }); h.state.documentSearch.value = '匹配'; await h.state.loadDocuments()
  assert.equal(h.state.readyDocumentCount.value, null)
  const unavailable = harness({ api: { docsApi: async () => ({ items: [doc(1, 'pending'), doc(2, 'parsing'), doc(3, 'failed')], total: 3 }) } }); await unavailable.mount()
  assert.equal(unavailable.state.readyDocumentCount.value, 0); assert.equal(unavailable.state.composerBlocked.value, true)
  assert.match(await unavailable.html(), /暂无就绪资料/)
})

test('UI-R2 source errors and request races show honest state without substituting fake cards', async () => {
  const h = harness({ api: { docsApi: async () => { throw new Error('unavailable') } } }); await h.mount()
  const html = await h.html(); assert.match(html, /论文列表加载失败/); assert.match(html, /重新加载/); assert.doesNotMatch(html, /真实资料_1/)
  const first = deferred(); h.api.docsApi = id => id === 1 ? first.promise : Promise.resolve({ items: [doc(202)], total: 1 })
  const pending = h.state.loadDocuments(); h.state.currentKbId.value = 2; h.state.onKbChange(); await settle()
  first.resolve({ items: [doc(101)], total: 1 }); await pending
  assert.equal(h.state.documents.value[0].id, 202); assert.match(await h.html(), /真实资料_202/); assert.doesNotMatch(await h.html(), /真实资料_101/)
})

test('UI-R2 grouped toolbar contains reading settings and export, preserves font bounds and Escape focus restoration', async () => {
  const h = harness(); await h.mount(); const nodes = elements(await h.html()), menus = nodes.filter(n => n.tag === 'details' && attr(n, 'class') === 'toolbar-menu')
  assert.equal(menus.length, 2)
  assert.match(text(menus[0]), /阅读设置.*正文字号.*专注阅读.*恢复默认布局与字号/s)
  assert.match(text(menus[1]), /更多.*导出当前对话/s)
  assert.equal(hasAttr(nodes.find(n => attr(n, 'aria-label') === '导出当前对话'), 'disabled'), true)
  assert.equal(h.state.readerFont.value, 16); h.state.changeFont(100); assert.equal(h.state.readerFont.value, 20)
  h.state.changeFont(-100); assert.equal(h.state.readerFont.value, 14)
  let reset = 0; h.state.resetReading(() => reset++); assert.equal(reset, 1); assert.equal(h.state.readerFont.value, 16)
  assert.equal(h.store.get('paper-reader-font-7'), '16')
  let focused = 0; const details = { open: true, querySelector: tag => { assert.equal(tag, 'summary'); return { focus() { focused++ } } } }
  h.state.closeToolMenu({ currentTarget: { closest: tag => { assert.equal(tag, 'details'); return details } } })
  assert.equal(details.open, false); assert.equal(focused, 1)
})

test('UI-R2 Enter respects composition and Shift, and sources are cleared with translation when leaving scope', async () => {
  const h = harness({ api: { askApi: async () => result() } }); await h.mount(); h.state.question.value = '输入法草稿'
  const event = extra => ({ key: 'Enter', preventDefault() { this.prevented = true }, ...extra })
  for (const e of [event({ isComposing: true }), event({ shiftKey: true }), event({ keyCode: 229 })]) { h.state.onQuestionKeydown(e); assert.equal(e.prevented, undefined) }
  h.state.composing.value = true; h.state.onQuestionKeydown(event({})); h.state.composing.value = false
  assert.equal(apiCalls(h, 'askApi').length, 0)
  const e = event({}); h.state.onQuestionKeydown(e); await settle(); assert.equal(e.prevented, true); assert.equal(apiCalls(h, 'askApi').length, 1)
  h.state.openCitation({ doc_id: 1 }, [{ doc_id: 1 }]); h.state.openTranslation(h.state.messages.value.at(-1))
  h.state.newSession(); assert.equal(h.state.activeCitation.value, null); assert.deepEqual(h.state.sourceList.value, []); assert.equal(h.state.translationVisible.value, false)
  assert.equal(h.state.isLanding.value, true)
})

test('UI-R2 owned styles keep 56px toolbar, body 16px, information >=14px and container-based input-first fallback', () => {
  assert.match(css, /\.conversation-toolbar\s*\{[^}]*height: 56px/)
  assert.match(css, /@container chat-content \(min-width: 1048px\)/)
  assert.match(css, /grid-template-columns: minmax\(0, 28fr\) minmax\(0, 16fr\) minmax\(0, 56fr\)/)
  assert.match(css, /max-height: 30vh/); assert.match(css, /max-width: 920px/)
  assert.match(css, /--color-source: #0F766E/); assert.match(css, /--color-source-soft: #ECFDF5/)
  assert.doesNotMatch(css, /font-size:\s*(?:[0-9]|1[0-3])px\b/)
  assert.match(css, /font:\s*16px\/1\.7/); assert.match(css, /prefers-reduced-motion/); assert.match(css, /summary:focus-visible/)
})

test('UI-R2 readiness uses request-bound search, not a changed search field during an in-flight response', async () => {
  const h = harness({ libraries: [kb(1, { doc_count: 80 })], api: { docsApi: async () => ({ items: [doc(1)], total: 80 }) } }); await h.mount()
  const pending = deferred(); h.api.docsApi = () => pending.promise
  h.state.documentSearch.value = '仅失败资料'; const load = h.state.loadDocuments()
  h.state.documentSearch.value = ''; pending.resolve({ items: [doc(9, 'failed')], total: 1 }); await load
  assert.equal(h.state.readyDocumentCount.value, null); assert.equal(h.state.noReadyDocuments.value, false)
  assert.match(await h.html(), /80 份资料 · 就绪数量待确认/)
})

test('Closed toolbar does not swallow Escape needed to leave focused reading', async () => {
  const h = harness(); await h.mount()
  const details = { open: false, querySelector: () => ({ focus() {} }) }
  const event = { key: 'Escape', currentTarget: { closest: () => details }, prevented: false, stopped: false, preventDefault() { this.prevented = true }, stopPropagation() { this.stopped = true } }
  h.state.closeToolMenu(event); assert.equal(event.stopped, false); assert.equal(event.prevented, false)
  details.open = true; h.state.closeToolMenu(event); assert.equal(event.stopped, true); assert.equal(event.prevented, true); assert.equal(details.open, false)
})
