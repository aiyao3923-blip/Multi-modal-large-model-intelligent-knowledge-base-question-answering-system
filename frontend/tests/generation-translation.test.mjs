import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import * as Vue from 'vue'
import { renderToString } from '@vue/server-renderer'
import { parse, compileScript, compileStyle, compileTemplate } from '@vue/compiler-sfc'
import { compile } from '@vue/compiler-dom'

// Every transport, session, clipboard and download is a synthetic in-memory double.
// These tests never access login, real model endpoints, tokens or persisted user data.
const streamSource = await readFile(new URL('../src/api/stream.js', import.meta.url), 'utf8')
const apiSource = await readFile(new URL('../src/api/index.js', import.meta.url), 'utf8')
const formatSource = await readFile(new URL('../src/utils/format.js', import.meta.url), 'utf8')
const stripModule = source => source.replace(/^import .*$/gm, '').replace(/^export /gm, '')
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }
const settle = async () => { for (let i = 0; i < 6; i++) { await Promise.resolve(); await Vue.nextTick() } }
const encoder = new TextEncoder()
const frame = data => JSON.stringify(data) + '\n'
const result = data => frame({ event: 'result', data })
const bytes = (text, size = 7) => { const value = encoder.encode(text), chunks = []; for (let i = 0; i < value.length; i += size) chunks.push(value.slice(i, i + size)); return chunks }
const body = (chunks, cancel = () => {}) => new ReadableStream({ start(controller) { chunks.forEach(chunk => controller.enqueue(chunk)); controller.close() }, cancel })
const ndjson = text => new Response(body(bytes(text, 4093)), { headers: { 'Content-Type': 'application/x-ndjson; charset=utf-8' } })
function client({ fetch: request = async () => { throw new Error('Unexpected network attempt') }, baseURL = '/api' } = {}) {
  const calls = { fetch: [], logout: 0, routes: [] }
  const user = { token: 'isolated-test-session', logout() { calls.logout++; this.token = '' } }
  const router = { currentRoute: { value: { path: '/chat', fullPath: '/chat?kb_id=1' } }, replace: value => calls.routes.push(value) }
  const api = new Function('http', 'useUserStore', 'router', 'fetch', 'window', stripModule(streamSource) + '\nreturn { readNdjson, streamRequest }')(
    { defaults: { baseURL } }, () => user, router,
    (...args) => { calls.fetch.push(args); return request(...args) }, { location: { origin: 'https://workspace.test' } },
  )
  return { ...api, user, router, calls }
}
const protocolFailure = error => error.response?.status === 502 && typeof error.response?.data?.detail === 'string'

test('NDJSON incrementally decodes UTF-8, CRLF, blank lines and a final line without newline', async () => {
  const progress = [], data = { answer: '# 结论🙂\n完整段落', generation_status: 'generated' }
  const text = '\ufeff\r\n' + frame({ event: 'status', stage: 'retrieving', received_chars: 0 }).replace('\n', '\r\n')
    + frame({ event: 'heartbeat', stage: 'generating', elapsed_seconds: 181, received_chars: 9 })
    + result(data).trimEnd()
  const response = await client().readNdjson(body(bytes(text, 1)), { onProgress: event => progress.push(event) })
  assert.deepEqual(response, data); assert.equal(progress.length, 2)
  assert.equal(progress[0].received_chars, 0); assert.equal(progress[1].elapsed_seconds, 181)
})

test('Long complete results are not truncated and progress never exposes incremental answer text', async () => {
  const progress = [], answer = '# 翻译\n' + 'English 中文🙂 [来源1]\n'.repeat(10000)
  const data = { answer, generation_status: 'generated' }
  const text = frame({ event: 'status', stage: 'generating', received_chars: 40001 })
    + frame({ event: 'heartbeat', stage: 'validating', elapsed_seconds: 3600, received_chars: answer.length }) + result(data)
  const response = await client().readNdjson(body(bytes(text, 4093)), { onProgress: value => progress.push(value) })
  assert.equal(response.answer, answer); assert.equal(progress.length, 2)
  assert.equal(progress.some(event => 'answer' in event || 'translation' in event), false)
})

for (const [name, text] of [
  ['empty EOF', ''], ['progress-only EOF', frame({ event: 'status', stage: 'generating' })],
  ['malformed JSON', '{DO_NOT_ECHO_RAW_PAYLOAD'], ['unknown event', frame({ event: 'delta', text: 'unverified body' })],
  ['invalid stage', frame({ event: 'status', stage: 'not-a-stage' })],
  ['invalid count', frame({ event: 'status', stage: 'generating', received_chars: -1 })],
  ['invalid heartbeat', frame({ event: 'heartbeat', stage: 'generating', elapsed_seconds: -1 })],
  ['missing result data', frame({ event: 'result' })],
  ['duplicate result in same chunk', result({ answer: 'first' }) + result({ answer: 'second' })],
  ['progress after terminal', result({ answer: 'first' }) + frame({ event: 'status', stage: 'validating' })],
  ['error after terminal', result({ answer: 'first' }) + frame({ event: 'error', status: 502, detail: '失败' })],
]) test(`NDJSON rejects ${name} without returning a partial answer`, async () => {
  await assert.rejects(client().readNdjson(body([encoder.encode(text)])), error => {
    assert.ok(protocolFailure(error)); assert.doesNotMatch(error.message, /DO_NOT_ECHO_RAW_PAYLOAD|unverified body/); return true
  })
})

test('A second terminal in a later chunk and incomplete UTF-8 both fail closed', async () => {
  await assert.rejects(client().readNdjson(body([encoder.encode(result({ answer: 'first' })), encoder.encode(result({ answer: 'second' }))])), protocolFailure)
  await assert.rejects(client().readNdjson(body([new Uint8Array([0xe4, 0xb8])])), protocolFailure)
})

test('NDJSON error preserves the safe status/detail shape expected by the existing UI', async () => {
  await assert.rejects(client().readNdjson(body(bytes(frame({ event: 'error', status: 403, detail: '无权翻译该回答' })))), error => {
    assert.equal(error.response.status, 403); assert.equal(error.response.data.detail, '无权翻译该回答'); assert.equal(error.detail, '无权翻译该回答'); return true
  })
})

test('Aborting an idle reader releases the stream immediately without waiting for a terminal', async () => {
  let cancelled = 0
  const stream = new ReadableStream({ cancel() { cancelled++ } }), controller = new AbortController()
  const pending = client().readNdjson(stream, { signal: controller.signal })
  controller.abort(); await assert.rejects(pending, { name: 'AbortError' })
  assert.equal(cancelled, 1); assert.equal(stream.locked, false)
})

test('Stream POST uses only same-origin /api, the current synthetic token and no total timeout', async () => {
  const input = { question: '  ' + '文'.repeat(40001) + '\n ' }, controller = new AbortController(), progress = []
  const h = client({ fetch: async () => ndjson(frame({ event: 'status', stage: 'generating' }) + result({ answer: '完整' })) })
  assert.deepEqual(await h.streamRequest('/qa/ask/stream', input, { signal: controller.signal, onProgress: event => progress.push(event) }), { answer: '完整' })
  const [url, options] = h.calls.fetch[0]
  assert.equal(String(url), 'https://workspace.test/api/qa/ask/stream'); assert.equal(options.method, 'POST')
  assert.equal(options.signal, controller.signal); assert.equal(options.credentials, 'same-origin'); assert.equal(options.redirect, 'error')
  assert.equal(options.headers.Authorization, 'Bearer isolated-test-session')
  assert.equal(options.headers.Accept, 'application/x-ndjson'); assert.equal(options.headers['Content-Type'], 'application/json')
  assert.deepEqual(JSON.parse(options.body), input); assert.equal('timeout' in options, false)
  assert.equal(h.calls.fetch.length, 1); assert.equal(progress.length, 1)
  assert.doesNotMatch(streamSource, /\bsetTimeout\s*\(|\bconsole\.|localStorage\.setItem/)
})

for (const [baseURL, path] of [['https://untrusted.test/api', '/qa/ask/stream'], ['/elsewhere', '/qa/ask/stream'], ['/api', '//untrusted.test/api'], ['/api', 'https://untrusted.test/api'], ['/api', '/../auth'], ['/api', '/qa/../../outside'], ['/api', '/qa\\outside']]) {
  test(`Unsafe stream route is refused before dispatch (${baseURL}, ${path})`, async () => {
    const h = client({ baseURL }); await assert.rejects(h.streamRequest(path, {})); assert.equal(h.calls.fetch.length, 0)
  })
}

test('HTTP and NDJSON 401 use the existing token-generation guard and local redirect behavior', async () => {
  for (const response of [new Response(JSON.stringify({ detail: '登录已失效' }), { status: 401, headers: { 'Content-Type': 'application/json' } }), ndjson(frame({ event: 'error', status: 401, detail: '登录已失效' }))]) {
    const h = client({ fetch: async () => response })
    await assert.rejects(h.streamRequest('/qa/ask/stream', {}), error => error.response?.status === 401)
    assert.equal(h.calls.logout, 1); assert.deepEqual(h.calls.routes, [{ path: '/login', query: { redirect: '/chat?kb_id=1' } }])
  }
  const response = deferred(), h = client({ fetch: () => response.promise }), pending = h.streamRequest('/qa/ask/stream', {})
  h.user.token = 'newer-isolated-session'; response.resolve(new Response('{}', { status: 401 }))
  await assert.rejects(pending); assert.equal(h.calls.logout, 0); assert.equal(h.calls.routes.length, 0)
})

test('Wrong MIME, HTTP validation, network failure and pre-abort never replay the POST', async () => {
  for (const response of [new Response('{}', { headers: { 'Content-Type': 'application/json' } }), new Response(JSON.stringify({ detail: [{ msg: 'invalid' }] }), { status: 422 })]) {
    const h = client({ fetch: async () => response }); await assert.rejects(h.streamRequest('/qa/ask/stream', {})); assert.equal(h.calls.fetch.length, 1)
  }
  const failed = client({ fetch: async () => { throw new TypeError('failed fetch') } }); await assert.rejects(failed.streamRequest('/qa/ask/stream', {})); assert.equal(failed.calls.fetch.length, 1)
  const controller = new AbortController(); controller.abort()
  const h = client(); await assert.rejects(h.streamRequest('/qa/ask/stream', {}, { signal: controller.signal }), { name: 'AbortError' }); assert.equal(h.calls.fetch.length, 0)
})

test('Transport cancellation aborts body consumption without logout or a second request', async () => {
  let cancelled = 0
  const h = client({ fetch: async () => new Response(new ReadableStream({ cancel() { cancelled++ } }), { headers: { 'Content-Type': 'application/x-ndjson' } }) })
  const controller = new AbortController(), pending = h.streamRequest('/qa/ask/stream', {}, { signal: controller.signal }); await settle()
  controller.abort(); await assert.rejects(pending, { name: 'AbortError' })
  assert.equal(cancelled, 1); assert.equal(h.calls.fetch.length, 1); assert.equal(h.calls.logout, 0)
})

test('askApi and translateApi forward the unchanged payload/options and leave other HTTP APIs intact', async () => {
  const streams = [], calls = []
  const http = { get: (...args) => calls.push(['get', ...args]), post: (...args) => calls.push(['post', ...args]) }
  const api = new Function('http', 'streamRequest', stripModule(apiSource) + '\nreturn { askApi, translateApi, docsApi, feedbackApi, uploadDocApi }')(http, (...args) => streams.push(args))
  const question = { kb_id: 1, question: '文'.repeat(40001) }, options = { signal: new AbortController().signal, onProgress() {} }
  api.askApi(question, options); api.translateApi({ message_id: 101, target_language: 'en' }, options)
  assert.deepEqual(streams, [['/qa/ask/stream', question, options], ['/qa/translate/stream', { message_id: 101, target_language: 'en' }, options]])
  api.docsApi(1); api.feedbackApi({ message_id: 101, feedback_type: 'like' }); api.uploadDocApi(1, 'form')
  assert.deepEqual(calls, [['get', '/kbs/1/docs', { params: { page: 1, size: 20 } }], ['post', '/qa/feedback', { message_id: 101, feedback_type: 'like' }], ['post', '/kbs/1/docs', 'form', { timeout: 120000 }]])
})

const panelPath = 'components/TranslationPanel.vue'
const panelSource = await readFile(new URL('../src/' + panelPath, import.meta.url), 'utf8')
const { descriptor, errors } = parse(panelSource, { filename: panelPath })
assert.deepEqual(errors, [])
const script = compileScript(descriptor, { id: 'translation-test' })
assert.deepEqual(compileTemplate({ source: descriptor.template.content, filename: panelPath, id: 'translation-test', compilerOptions: { bindingMetadata: script.bindings } }).errors, [])
for (const style of descriptor.styles) assert.deepEqual(compileStyle({ source: style.content, filename: panelPath, id: 'translation-test', scoped: style.scoped }).errors, [])
const identifiers = node => !node ? [] : node.type === 'Identifier' ? [node.name] : node.type === 'ObjectPattern' ? node.properties.flatMap(item => identifiers(item.value || item.argument)) : node.type === 'ArrayPattern' ? node.elements.flatMap(identifiers) : identifiers(node.left || node.argument)
const statements = script.scriptSetupAst.filter(node => node.type !== 'ImportDeclaration')
const panelCode = statements.map(node => descriptor.scriptSetup.content.slice(node.start, node.end)).join('\n')
const names = statements.flatMap(node => node.type === 'FunctionDeclaration' ? [node.id.name] : node.type === 'VariableDeclaration' ? node.declarations.flatMap(item => identifiers(item.id)) : [])
const panelRender = new Function('Vue', compile(descriptor.template.content, { mode: 'function', prefixIdentifiers: true }).code)(Vue)
const panels = new Set()
function panel({ props: input = {}, translate = async data => ({ translation: '# 完整译文\n正文', target_language: data.target_language, mode: 'local', generation_status: 'generated', source_message_id: data.message_id ?? null }), clipboardFails = false, downloadFails = false } = {}) {
  const props = Vue.reactive({ modelValue: true, sourceText: 'Original text', sourceMessageId: null, ...input })
  const calls = { api: [], copied: [], downloads: [], emits: [], revoked: [], selected: 0, focused: 0 }, hooks = [], urls = new Map(), timers = []
  const document = { body: { appendChild() {} }, createElement: () => ({ click() { if (downloadFails) throw new Error('blocked'); calls.downloads.push({ name: this.download, blob: urls.get(this.href) }) }, remove() {} }) }
  const URL = { createObjectURL: blob => { const id = `blob:isolated/${urls.size}`; urls.set(id, blob); return id }, revokeObjectURL: id => { calls.revoked.push(id); urls.delete(id) } }
  const downloadText = new Function('URL', 'Blob', 'document', 'setTimeout', stripModule(formatSource) + '\nreturn downloadText')(URL, Blob, document, fn => timers.push(fn))
  const bindings = { ref: Vue.ref, computed: Vue.computed, watch: Vue.watch, nextTick: Vue.nextTick, onBeforeUnmount: fn => hooks.push(fn), defineProps: () => props,
    defineEmits: () => (...args) => { calls.emits.push(args); if (args[0] === 'update:modelValue') props.modelValue = args[1] }, AbortController, downloadText,
    navigator: { clipboard: { writeText: async text => { if (clipboardFails) throw new Error('denied'); calls.copied.push(text) } } },
    translateApi: (...args) => { calls.api.push(args); return translate(...args) },
  }
  const scope = Vue.effectScope(), state = scope.run(() => new Function('bindings', `const {${Object.keys(bindings)}} = bindings;\n${panelCode}\nreturn {${names.map(name => `get ${name}(){return ${name}}`)}}`)(bindings))
  state.resultPreview.value = Vue.markRaw({ focus() { calls.focused++ }, select() { calls.selected++ } })
  let disposed = false
  const h = { state, props, calls, urls, timers, async html() {
    const app = Vue.createSSRApp({ setup: () => ({ ...state, ...props }), render: panelRender })
    app.component('ElDialog', { props: ['modelValue'], setup: (p, { slots }) => () => p.modelValue ? Vue.h('section', { role: 'dialog' }, [slots.default?.(), slots.footer?.()]) : null })
    app.component('ElButton', { setup: (_p, { attrs, slots }) => () => Vue.h('button', attrs, slots.default?.()) })
    return renderToString(app)
  }, dispose() { if (!disposed) { disposed = true; hooks.forEach(fn => fn()); scope.stop(); timers.forEach(fn => fn()) } } }
  panels.add(h); return h
}
test.afterEach(() => { for (const h of panels) h.dispose(); panels.clear() })

for (const language of ['zh', 'en']) test(`Translation to ${language} preserves >40000 input characters and complete Markdown output`, async () => {
  const text = '  ' + '文English🙂\n'.repeat(6000) + '  ', output = '# 译文\n' + '完整 English [来源9] <script>inert</script>\n'.repeat(4000)
  const h = panel({ props: { sourceText: text }, translate: async data => ({ translation: output, target_language: data.target_language, mode: 'online', generation_status: 'generated', source_message_id: null }) })
  h.state.targetLanguage.value = language; await h.state.startTranslation()
  assert.deepEqual(h.calls.api[0][0], { text, target_language: language }); assert.ok(h.calls.api[0][1].signal instanceof AbortSignal)
  assert.equal(typeof h.calls.api[0][1].onProgress, 'function'); assert.equal(h.state.draft.value, text)
  assert.equal(h.state.translationResult.value.translation, output)
  const html = await h.html(); assert.doesNotMatch(html, /maxlength|data-source-rank|<script>/); assert.match(html, /&lt;script&gt;/)
  assert.match(html, /当前模型通道/); assert.match(html, /外发/)
  await h.state.copyTranslation(); assert.deepEqual(h.calls.copied, [output])
  h.state.downloadTranslation(); assert.equal(h.calls.downloads.length, 1)
  const file = h.calls.downloads[0]; assert.match(file.name, /\.md$/); assert.equal(file.blob.type, 'text/markdown;charset=utf-8')
  assert.equal(await file.blob.text(), output); assert.equal(file.blob.size, Buffer.byteLength(output, 'utf8'))
  h.timers.forEach(fn => fn()); assert.equal(h.urls.size, 0)
})

test('Message translation submits only the authorized message id; any edit switches permanently to text for that opening', async () => {
  const h = panel({ props: { sourceText: 'Original', sourceMessageId: 101 } })
  await h.state.startTranslation(); assert.deepEqual(h.calls.api[0][0], { message_id: 101, target_language: 'zh' })
  h.state.draft.value = 'Edited'; h.state.markSourceEdited(); await h.state.startTranslation()
  assert.deepEqual(h.calls.api[1][0], { text: 'Edited', target_language: 'zh' })
  h.state.draft.value = 'Original'; await h.state.startTranslation()
  assert.deepEqual(h.calls.api[2][0], { text: 'Original', target_language: 'zh' })
  assert.equal(h.props.sourceText, 'Original'); assert.equal(h.props.sourceMessageId, 101)
})

test('Translation validates blank text, guards duplicates and Stop drops late output/progress while retaining the source', async () => {
  const reply = deferred(), h = panel({ translate: () => reply.promise })
  h.state.draft.value = '  \n '; await h.state.startTranslation(); assert.equal(h.calls.api.length, 0)
  h.state.draft.value = 'Preserved source'; h.state.markSourceEdited()
  const pending = h.state.startTranslation(); await settle(); await h.state.startTranslation(); assert.equal(h.calls.api.length, 1)
  h.calls.api[0][1].onProgress({ event: 'status', stage: 'translating', received_chars: 40001 })
  assert.match(await h.html(), /40001/)
  h.state.stopTranslation(); assert.equal(h.calls.api[0][1].signal.aborted, true); assert.equal(h.state.translating.value, false)
  reply.resolve({ translation: 'LATE_RESULT', target_language: 'zh', mode: 'local', generation_status: 'generated', source_message_id: null }); await pending
  assert.equal(h.state.translationResult.value, null); assert.equal(h.state.draft.value, 'Preserved source')
  h.calls.api[0][1].onProgress({ event: 'status', stage: 'translating', received_chars: 99999 })
  assert.doesNotMatch(await h.html(), /LATE_RESULT|99999/)
})

test('Closing, changing source and unmount abort translation and prevent stale results crossing message boundaries', async () => {
  for (const action of ['close', 'source', 'unmount']) {
    const response = deferred(), h = panel({ translate: () => response.promise })
    const pending = h.state.startTranslation(); await settle()
    if (action === 'close') h.state.setVisible(false)
    else if (action === 'source') { h.props.sourceText = 'NEW_SOURCE'; h.props.sourceMessageId = 202; await settle() }
    else h.dispose()
    assert.equal(h.calls.api[0][1].signal.aborted, true)
    response.resolve({ translation: 'PRIVATE_OLD_RESULT', target_language: 'zh', mode: 'local', generation_status: 'generated', source_message_id: null }); await pending
    assert.equal(h.state.translationResult.value, null)
    if (action === 'source') assert.equal(h.state.draft.value, 'NEW_SOURCE')
  }
})

test('A failed translation never overwrites the original or a previous complete result, and clipboard/download failures have usable fallbacks', async () => {
  let failed = false
  const h = panel({ clipboardFails: true, downloadFails: true, translate: async data => {
    if (failed) throw { response: { status: 503, data: { detail: '翻译服务暂不可用' } } }
    return { translation: 'LAST_COMPLETE', target_language: data.target_language, mode: 'local', generation_status: 'generated', source_message_id: null }
  } })
  await h.state.startTranslation(); failed = true; await h.state.startTranslation()
  assert.equal(h.state.translationResult.value.translation, 'LAST_COMPLETE'); assert.equal(h.state.draft.value, 'Original text')
  assert.match(h.state.errorMessage.value, /翻译服务暂不可用/); assert.match(await h.html(), /上次完成/)
  await h.state.copyTranslation(); assert.equal(h.calls.selected, 1); assert.equal(h.calls.focused, 1)
  assert.match(h.state.copyStatus.value, /手动复制/)
  h.state.downloadTranslation(); assert.match(h.state.copyStatus.value, /下载未完成/)
  assert.equal(h.calls.api.length, 2)
})

for (const invalid of [{ generation_status: 'failed' }, { target_language: 'en' }, { source_message_id: 999 }, { translation: '' }]) test(`Incomplete or mismatched translation results are not displayed (${Object.keys(invalid)[0]})`, async () => {
  const h = panel({ translate: async () => ({ translation: 'UNVERIFIED', target_language: 'zh', mode: 'local', generation_status: 'generated', source_message_id: null, ...invalid }) })
  await h.state.startTranslation(); assert.equal(h.state.translationResult.value, null); assert.ok(h.state.errorMessage.value)
})

test('Abort after a result but before clean EOF must not deliver a complete answer', async () => {
  let controller, cancelled = 0
  const stream = new ReadableStream({ start(value) { controller = value }, cancel() { cancelled++ } })
  const abort = new AbortController(), pending = client().readNdjson(stream, { signal: abort.signal })
  controller.enqueue(encoder.encode(result({ answer: 'NOT_COMMITTED' }))); await settle()
  abort.abort(); await assert.rejects(pending, { name: 'AbortError' }); assert.equal(cancelled, 1)
})

test('Backend translation 499 is a stopped task, not a partial result or an automatic retry', async () => {
  const h = panel({ translate: async () => { throw { response: { status: 499, data: { detail: '请求已取消' } } } } })
  await h.state.startTranslation()
  assert.equal(h.state.translating.value, false); assert.equal(h.state.translationResult.value, null)
  assert.equal(h.state.errorMessage.value, ''); assert.match(h.state.infoMessage.value, /停止/)
  assert.equal(h.state.draft.value, 'Original text'); assert.equal(h.calls.api.length, 1)
})

test('A cancelled translation cannot clear the busy flag or overwrite a newer translation', async () => {
  const old = deferred(), fresh = deferred()
  const h = panel({ translate: data => data.target_language === 'zh' ? old.promise : fresh.promise })
  const first = h.state.startTranslation(); await settle(); h.state.stopTranslation()
  h.state.targetLanguage.value = 'en'; const second = h.state.startTranslation(); await settle()
  old.reject({ response: { status: 503, data: { detail: 'OLD_ERROR' } } }); await first
  assert.equal(h.state.translating.value, true); assert.equal(h.state.errorMessage.value, '')
  fresh.resolve({ translation: 'NEW_COMPLETE', target_language: 'en', mode: 'local', generation_status: 'generated', source_message_id: null }); await second
  assert.equal(h.state.translationResult.value.translation, 'NEW_COMPLETE'); assert.equal(h.state.translating.value, false)
})
