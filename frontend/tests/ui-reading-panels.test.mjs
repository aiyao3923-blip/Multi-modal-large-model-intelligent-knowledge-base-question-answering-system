import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import * as Vue from 'vue'
import { renderToString } from '@vue/server-renderer'
import { parse, compileScript, compileTemplate, compileStyle } from '@vue/compiler-sfc'
import { compile, parse as parseHtml } from '@vue/compiler-dom'

// Real SFC setup, refs/watchers and compiled rendering; only external effects are doubles.
// This is NOT browser, native clipboard/download, CSS geometry or model E2E evidence.
const compiled = {}
const identifiers = node => !node ? [] : node.type === 'Identifier' ? [node.name] : node.type === 'ObjectPattern' ? node.properties.flatMap(item => identifiers(item.value || item.argument)) : node.type === 'ArrayPattern' ? node.elements.flatMap(identifiers) : identifiers(node.left || node.argument)
for (const name of ['CitationReader', 'TranslationPanel']) {
  const source = await readFile(new URL(`../src/components/${name}.vue`, import.meta.url), 'utf8')
  const { descriptor, errors } = parse(source, { filename: name })
  assert.deepEqual(errors, [])
  const script = compileScript(descriptor, { id: name })
  assert.deepEqual(compileTemplate({ source: descriptor.template.content, filename: name, id: name, compilerOptions: { bindingMetadata: script.bindings } }).errors, [])
  for (const style of descriptor.styles) assert.deepEqual(compileStyle({ source: style.content, filename: name, id: name, scoped: true }).errors, [])
  const statements = script.scriptSetupAst.filter(node => node.type !== 'ImportDeclaration')
  const code = statements.map(node => descriptor.scriptSetup.content.slice(node.start, node.end)).join('\n')
  const names = statements.flatMap(node => node.type === 'FunctionDeclaration' ? [node.id.name] : node.type === 'VariableDeclaration' ? node.declarations.flatMap(item => identifiers(item.id)) : [])
  compiled[name] = {
    source, css: descriptor.styles.map(style => style.content).join('\n'),
    create: bindings => new Function('bindings', `const {${Object.keys(bindings)}} = bindings;\n${code}\nreturn {${names.map(key => `get ${key}(){return ${key}}`)}}`)(bindings),
    render: new Function('Vue', compile(descriptor.template.content, { mode: 'function', prefixIdentifiers: true }).code)(Vue),
  }
}
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }
const settle = async () => { await Promise.resolve(); await Vue.nextTick(); await Promise.resolve() }
const elements = html => { const found = []; const visit = node => { if (node.type === 1) found.push(node); for (const child of node.children || []) visit(child) }; visit(parseHtml(html)); return found }
const attr = (node, name) => node.props.find(prop => prop.name === name)?.value?.content
const hasAttr = (node, name) => node.props.some(prop => prop.name === name)
const hasClass = (node, name) => (attr(node, 'class') || '').split(/\s+/).includes(name)
const content = node => typeof node === 'string' ? node : Array.isArray(node) ? node.map(content).join('') : node && typeof node.children === 'string' ? node.children : Array.isArray(node?.children) ? content(node.children) : ''
const flatten = tree => !tree ? [] : Array.isArray(tree) ? tree.flatMap(flatten) : [tree, ...(Array.isArray(tree.children) ? tree.children.flatMap(flatten) : [])]
const active = new Set()
function harness(name, { props: input = {}, file, translate, clipboardFails = false, downloadFails = false } = {}) {
  const defaults = name === 'CitationReader' ? { kbId: 1, citation: null, sources: [] } : { modelValue: true, sourceText: 'Original text', sourceMessageId: null }
  const props = Vue.reactive({ ...defaults, ...input }), hooks = [], scope = Vue.effectScope()
  const calls = { api: [], emitted: [], copies: [], downloads: [], revoked: [], focus: 0, selected: 0 }, urls = new Map(), timers = []
  const URL = { createObjectURL(blob) { const url = `blob:test/${urls.size}`; urls.set(url, blob); return url }, revokeObjectURL(url) { urls.delete(url); calls.revoked.push(url) } }
  const bindings = {
    ref: Vue.ref, computed: Vue.computed, watch: Vue.watch, nextTick: Vue.nextTick,
    onBeforeUnmount: fn => hooks.push(fn), defineProps: () => props,
    defineEmits: () => (...args) => { calls.emitted.push(args); if (args[0] === 'update:modelValue') props.modelValue = args[1] }, defineExpose() {},
    AbortController, Blob, URL, setTimeout: fn => timers.push(fn),
    document: { createElement: () => ({ click() { calls.downloads.push({ name: this.download, blob: urls.get(this.href) }) } }) },
    navigator: { clipboard: { writeText: async text => { if (clipboardFails) throw new Error('denied'); calls.copies.push(text) } } },
    downloadText: (...args) => { if (downloadFails) throw new Error('blocked'); calls.downloads.push(args) },
    documentFileApi: (...args) => { calls.api.push(args); return file ? file(...args) : Promise.resolve(new Blob(['original'], { type: 'text/plain' })) },
    translateApi: (...args) => { calls.api.push(args); return translate ? translate(...args) : Promise.resolve({ generation_status: 'generated', translation: '# 完整译文\n[来源1] END', target_language: args[0].target_language, source_message_id: args[0].message_id ?? null, mode: 'local' }) },
  }
  const state = scope.run(() => compiled[name].create(bindings))
  if (state.resultPreview) state.resultPreview.value = Vue.markRaw({ focus() { calls.focus++ }, select() { calls.selected++ } })
  let disposed = false
  const h = { props, state, calls, urls, trees: [], async html() {
    h.trees = []
    const keep = tree => { h.trees.push(tree); return tree }
    const app = Vue.createSSRApp({ emits: ['select', 'close'], setup: () => ({ ...state, ...props }), render(...args) { return keep(compiled[name].render.apply(this, args)) } }, { onSelect: item => calls.emitted.push(['select', item]), onClose: () => calls.emitted.push(['close']) })
    app.component('AppIcon', { render: () => Vue.h('i', { 'aria-hidden': 'true' }) })
    app.component('ElDialog', { props: ['modelValue', 'title'], setup: (p, { slots }) => () => p.modelValue ? keep(Vue.h('section', { role: 'dialog', 'aria-label': p.title }, [slots.default?.(), slots.footer?.()])) : null })
    app.component('ElButton', { props: ['type'], setup: (_p, { attrs, slots }) => () => keep(Vue.h('button', { ...attrs, type: 'button' }, slots.default?.())) })
    return renderToString(app)
  }, async click(label, { wait = true } = {}) {
    await h.html()
    const button = h.trees.flatMap(flatten).find(node => node.type === 'button' && (node.props?.['aria-label'] === label || content(node).trim() === label))
    assert.ok(button, `rendered button: ${label}`)
    assert.ok(!button.props.disabled, `enabled button: ${label}`)
    const pending = button.props.onClick({})
    return wait ? pending : { pending }
  }, dispose() { if (!disposed) { disposed = true; hooks.forEach(fn => fn()); scope.stop(); timers.forEach(fn => fn()) } } }
  active.add(h); return h
}
test.afterEach(() => { for (const h of active) h.dispose(); active.clear() })
const citation = (rank = 1) => ({ rank, doc_id: rank, doc_name: `论文-${rank}.txt`, page_no: 3, chunk_id: rank, content_excerpt: `来源片段-${rank}` })

test('Reading panels compile and carry responsive, readable and keyboard-visible CSS contracts (not geometry)', () => {
  const reader = compiled.CitationReader.css, translation = compiled.TranslationPanel.css
  assert.match(reader, /var\(--color-source, #0F766E\)/)
  assert.match(reader, /var\(--color-source-soft, #ECFDF5\)/)
  assert.match(reader, /\.source-excerpt\s*\{[^}]*font-size: 16px/)
  assert.match(reader, /\.text-preview\s*\{[^}]*16px\/1\.8/)
  assert.match(reader, /\.source-pagination-items\s*\{[^}]*overflow-y: auto/)
  assert.match(reader, /\.source-pagination button\s*\{[^}]*min-width: 44px; min-height: 44px/)
  assert.doesNotMatch(reader, /line-clamp|text-overflow:\s*ellipsis/)
  assert.match(translation, /grid-template-columns: minmax\(0, 1fr\) minmax\(0, 1fr\)/)
  assert.match(translation, /@media \(max-width: 767px\)[\s\S]*grid-template-columns: minmax\(0, 1fr\)/)
  assert.match(translation, /\.translation-text\s*\{[^}]*font: 16px\/1\.8/)
  assert.match(translation, /\.translation-footer :deep\(\.el-button\)\s*\{[^}]*min-height: 44px/)
  for (const css of [reader, translation]) {
    assert.match(css, /focus-visible/)
    assert.doesNotMatch(css, /font-size:\s*(?:[0-9]|1[01])px/)
    assert.doesNotMatch(css, /animation:/)
  }
})

test('Reader empty and missing metadata states are honest and original actions are disabled', async () => {
  const h = harness('CitationReader')
  let html = await h.html(); assert.match(html, /从来源开始阅读/); assert.doesNotMatch(html, /来源 1/)
  h.props.citation = {}; await settle(); html = await h.html()
  for (const text of ['来源序号未知', '文件名未知', '页码未知', '文本片段', '未提供原文标识', '这条历史引用没有保存片段。']) assert.ok(html.includes(text), text)
  const buttons = elements(html).filter(node => node.tag === 'button')
  assert.equal(buttons.filter(node => hasAttr(node, 'disabled')).length, 2)
  assert.equal(h.calls.api.length, 0)
})

test('Reader renders all 85 sources, full filenames/excerpts, pressed state and unchanged select/close emissions', async () => {
  const sources = Array.from({ length: 85 }, (_, i) => citation(i + 1))
  sources[84].doc_name = '目录/'.repeat(80) + 'FINAL-85.txt'
  sources[84].content_excerpt = '证据🙂\n'.repeat(1500) + '<script>inert</script> EXCERPT_END'
  const h = harness('CitationReader', { props: { sources, citation: sources[84] } })
  let html = await h.html()
  const buttons = elements(html).filter(node => node.tag === 'button' && attr(node, 'aria-label')?.startsWith('阅读来源 '))
  assert.equal(buttons.length, 85)
  assert.equal(attr(buttons[84], 'aria-current'), 'true'); assert.equal(attr(buttons[84], 'aria-pressed'), 'true')
  assert.ok(html.includes(sources[84].doc_name)); assert.match(html, /EXCERPT_END/); assert.match(html, /&lt;script&gt;inert&lt;\/script&gt;/)
  assert.match(html, /来源 85/); assert.match(html, /第 3 页/); assert.match(html, /excerpt/); assert.match(html, /original/)
  // Real compiled template handlers, not recreated event behavior.
  await h.click('阅读来源 1：论文-1.txt'); await h.click('关闭原文证据')
  assert.deepEqual(h.calls.emitted, [['select', h.props.sources[0]], ['close']])
  h.props.citation = h.props.sources[0]; await settle(); html = await h.html()
  assert.match(html, /来源片段-1/); assert.doesNotMatch(html, /EXCERPT_END/)
})

test('Reader original download loading/403 are visible even while reading the excerpt; no false success', async () => {
  const reply = deferred(), h = harness('CitationReader', { props: { citation: citation() }, file: () => reply.promise })
  const { pending } = await h.click('下载当前原文', { wait: false }); await settle()
  assert.match(await h.html(), /正在加载原文/)
  reply.reject({ response: { status: 403 } }); await pending
  const html = await h.html()
  assert.match(html, /role="alert"/); assert.match(html, /没有这份论文的访问权限/); assert.match(html, /重试下载/)
  assert.doesNotMatch(html, /下载成功|已下载/); assert.equal(h.calls.downloads.length, 0); assert.equal(h.urls.size, 0)
})

test('Reader original text remains complete and inert; switching resets to excerpt with no extra request', async () => {
  const text = '<script>inert</script>\n' + '原文'.repeat(20000) + 'ORIGINAL_END'
  const h = harness('CitationReader', { props: { citation: citation() }, file: async () => new Blob([text], { type: 'text/plain' }) })
  await h.click('论文原文 original')
  const html = await h.html()
  assert.match(html, /ORIGINAL_END/); assert.doesNotMatch(html, /<script>/)
  assert.ok(elements(html).some(node => node.tag === 'pre' && attr(node, 'tabindex') === '0'))
  assert.deepEqual(h.calls.api, [[1, 1]])
  h.props.citation = citation(2); await settle()
  assert.equal(h.state.view.value, 'excerpt'); assert.equal(h.state.textContent.value, null); assert.equal(h.calls.api.length, 1)
})

test('Translation shows disclosure, two semantic reading columns, labels, language and disabled output actions', async () => {
  const h = harness('TranslationPanel', { props: { sourceText: '', sourceMessageId: 101 } })
  const html = await h.html(), nodes = elements(html)
  assert.match(html, /外发提醒/); assert.match(html, /在线通道会将所选文本外发给模型服务/)
  assert.match(html, /原回答不会被覆盖，译文不保存到会话/)
  assert.ok(nodes.some(node => attr(node, 'aria-label') === '待翻译原文'))
  assert.ok(nodes.some(node => attr(node, 'aria-label') === '翻译结果'))
  assert.equal(nodes.filter(node => hasClass(node, 'translation-column')).length, 2)
  assert.ok(nodes.some(node => node.tag === 'label' && attr(node, 'for') === 'translation-source'))
  assert.ok(nodes.some(node => node.tag === 'select' && attr(node, 'aria-describedby') === 'translation-disclosure'))
  assert.equal(nodes.filter(node => node.tag === 'button' && hasAttr(node, 'disabled')).length, 3)
  assert.equal(h.calls.api.length, 0)
  h.state.languageSelect.value = Vue.markRaw({ focus() { h.calls.focus++ } })
  const dialog = h.trees.find(node => node.props?.onOpened)
  assert.ok(dialog, 'dialog opened event binds the focus handler')
  dialog.props.onOpened(); assert.equal(h.calls.focus, 1)
  h.props.modelValue = false; dialog.props.onOpened(); assert.equal(h.calls.focus, 1)
})

test('Translation progress keeps full-result area honest and Stop cancels with unchanged message-only request', async () => {
  const reply = deferred(), h = harness('TranslationPanel', { props: { sourceMessageId: 101 }, translate: () => reply.promise })
  const { pending } = await h.click('开始翻译', { wait: false }); await settle()
  assert.deepEqual(h.calls.api[0][0], { message_id: 101, target_language: 'zh' })
  h.calls.api[0][1].onProgress({ stage: 'validating', received_chars: 40999, elapsed_seconds: 12.7 })
  let html = await h.html()
  assert.match(html, /正在校验完整译文/); assert.match(html, /40999 字符（过程统计）/); assert.match(html, /12 秒/)
  assert.match(html, /不展示未完成内容/); assert.match(html, /停止翻译/)
  assert.doesNotMatch(html, /id="translation-result"/)
  await h.click('停止翻译'); assert.equal(h.calls.api[0][1].signal.aborted, true)
  reply.resolve({ generation_status: 'generated', translation: 'LATE_PRIVATE_RESULT', target_language: 'zh', source_message_id: 101, mode: 'local' }); await pending
  html = await h.html(); assert.match(html, /翻译已停止/); assert.doesNotMatch(html, /LATE_PRIVATE_RESULT/)
  assert.equal(h.state.draft.value, 'Original text'); assert.equal(h.state.translationResult.value, null)
})

test('Translation labels a prior result honestly after stopped retry and keeps complete copy/Markdown actions', async () => {
  const reply = deferred(); let requests = 0
  const text = '# 完整译文\n' + '保留 [来源1] 🙂\n'.repeat(3000) + 'TRANSLATION_END'
  const h = harness('TranslationPanel', { translate: async data => ++requests === 1 ? { generation_status: 'generated', translation: text, target_language: data.target_language, source_message_id: null, mode: 'local' } : reply.promise })
  await h.click('开始翻译')
  let html = await h.html(); assert.match(html, /完整译文 · 中文/); assert.match(html, /TRANSLATION_END/)
  const { pending } = await h.click('开始翻译', { wait: false }); await settle(); await h.click('停止翻译')
  html = await h.html(); assert.match(html, /上次完成的译文/); assert.match(html, /翻译已停止/); assert.doesNotMatch(html, /翻译已完成/)
  assert.ok(elements(html).some(node => node.tag === 'textarea' && hasAttr(node, 'readonly') && attr(node, 'aria-label') === '上次完成的译文，只读 Markdown 文本'))
  await h.click('复制译文'); assert.deepEqual(h.calls.copies, [text]); assert.match(h.state.copyStatus.value, /完整译文已复制/)
  await h.click('下载 Markdown'); assert.deepEqual(h.calls.downloads, [['译文-zh.md', text, 'text/markdown;charset=utf-8']])
  assert.match(h.state.copyStatus.value, /已请求下载/); assert.doesNotMatch(h.state.copyStatus.value, /下载成功|已下载/)
  reply.resolve({ translation: 'LATE' }); await pending
})

test('Translation failure, clipboard denial and blocked download retain real fallbacks without success claims', async () => {
  let failed = false
  const h = harness('TranslationPanel', { clipboardFails: true, downloadFails: true, translate: async data => {
    if (failed) throw { response: { status: 503, data: { detail: '服务暂不可用' } } }
    return { generation_status: 'generated', translation: 'SAVED_RESULT', target_language: data.target_language, source_message_id: null, mode: 'online' }
  } })
  await h.click('开始翻译'); failed = true; await h.click('开始翻译')
  let html = await h.html(); assert.match(html, /role="alert"/); assert.match(html, /服务暂不可用/); assert.match(html, /SAVED_RESULT/); assert.match(html, /上次完成的译文/)
  await h.click('复制译文'); assert.equal(h.calls.focus, 1); assert.equal(h.calls.selected, 1)
  html = await h.html(); assert.match(html, /手动复制/); assert.doesNotMatch(html, /完整译文已复制/)
  await h.click('下载 Markdown'); html = await h.html(); assert.match(html, /下载未完成/); assert.doesNotMatch(html, /已请求下载|下载成功/)
  assert.equal(h.state.draft.value, 'Original text'); assert.equal(h.calls.downloads.length, 0)
})

test('Translation close uses the existing update event and drops late output', async () => {
  const reply = deferred(), h = harness('TranslationPanel', { translate: () => reply.promise })
  const { pending } = await h.click('开始翻译', { wait: false }); await settle(); await h.click('关闭并停止')
  assert.deepEqual(h.calls.emitted, [['update:modelValue', false]])
  assert.equal(h.calls.api[0][1].signal.aborted, true)
  reply.resolve({ translation: 'PRIVATE_LATE' }); await pending
  assert.equal(h.state.translationResult.value, null); assert.doesNotMatch(await h.html(), /PRIVATE_LATE|role="dialog"/)
})
