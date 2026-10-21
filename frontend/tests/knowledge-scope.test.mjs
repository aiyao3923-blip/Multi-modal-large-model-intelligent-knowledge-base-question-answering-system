import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { parse, compileScript } from '@vue/compiler-sfc'

const file = new URL('../src/views/admin/KbDetail.vue', import.meta.url)
const text = await readFile(file, 'utf8')
const { descriptor } = parse(text, { filename: file.pathname })
const compiled = compileScript(descriptor, { id: 'knowledge-scope-test' })
const node = compiled.scriptSetupAst.find(n => n.type === 'FunctionDeclaration' && n.id?.name === 'load')
assert.ok(node, 'Actual KbDetail load function is present')
const loadSource = descriptor.scriptSetup.content.slice(node.start, node.end)
const ref = value => ({ value })
const denied = status => Object.assign(new Error('denied'), { response: { status, data: { detail: '服务返回的错误' } } })

function harness(overrides = {}) {
  const state = {
    kbId: ref(1), loading: ref(false), error: ref(''), accessStatus: ref(null),
    kb: ref({ id: 1, name: 'previously authorized' }), stats: ref({ doc_count: 1 }), docs: ref([{ id: 10, filename: 'previous document' }]), total: ref(1),
    search: ref(''), status: ref(''), page: ref(1), size: ref(20), polling: ref(true),
    previewVisible: ref(true), previewChunks: ref([{ content: 'previous excerpt' }]),
  }
  const calls = { schedule: 0, clear: 0 }
  const bindings = { ...state, ...{
    kbApi: async id => ({ id, name: 'loaded' }),
    kbStatsApi: async () => ({ doc_count: 2 }),
    docsApi: async () => ({ items: [{ id: 20 }], total: 2 }),
    schedulePoll: () => calls.schedule++, clearTimeout: () => calls.clear++,
  }, ...overrides }
  const create = new Function('bindings', 'const {' + Object.keys(bindings).join(',') + '} = bindings; let requestVersion=0, alive=true, pollTimer=1, previewVersion=0; ' + loadSource + '; return {load, dispose(){alive=false;requestVersion++}}')
  return { ...create(bindings), state, calls }
}

test('Knowledge detail successful refresh uses the actual API results', async () => {
  const h = harness(); await h.load()
  assert.equal(h.state.kb.value.name, 'loaded'); assert.equal(h.state.docs.value[0].id, 20)
  assert.equal(h.state.stats.value.doc_count, 2); assert.equal(h.state.total.value, 2)
  assert.equal(h.state.error.value, ''); assert.equal(h.state.loading.value, false); assert.equal(h.calls.schedule, 1)
})
for (const status of [403, 404]) test('HTTP ' + status + ' removes revoked/deleted knowledge and open citation content', async () => {
  const h = harness({ kbApi: async () => { throw denied(status) } }); await h.load()
  assert.equal(h.state.kb.value, null); assert.deepEqual(h.state.docs.value, []); assert.deepEqual(h.state.stats.value, {})
  assert.equal(h.state.total.value, 0); assert.equal(h.state.previewVisible.value, false); assert.deepEqual(h.state.previewChunks.value, [])
  assert.equal(h.state.polling.value, false); assert.equal(h.state.accessStatus.value, status); assert.equal(h.calls.clear, 1)
  assert.match(h.state.error.value, status === 403 ? /无权/ : /不存在/)
})
test('Temporary 503 is explicit and retains the last same-scope read snapshot', async () => {
  const h = harness({ kbApi: async () => { throw denied(503) } }); await h.load()
  assert.equal(h.state.kb.value.id, 1); assert.equal(h.state.docs.value[0].id, 10)
  assert.equal(h.state.error.value, '服务返回的错误'); assert.equal(h.state.polling.value, false); assert.equal(h.state.loading.value, false)
})
test('A stale load cannot overwrite a later knowledge scope', async () => {
  let finish
  const pending = new Promise(resolve => { finish = resolve })
  const h = harness({ kbApi: id => id === 1 ? pending : Promise.resolve({ id, name: 'new scope' }) })
  const first = h.load(); h.state.kbId.value = 2; await h.load(); finish({ id: 1, name: 'stale' }); await first
  assert.equal(h.state.kb.value.id, 2); assert.equal(h.state.kb.value.name, 'new scope'); assert.equal(h.calls.schedule, 1)
})
test('A completed request after unmount neither updates data nor schedules polling', async () => {
  let finish
  const pending = new Promise(resolve => { finish = resolve })
  const h = harness({ kbApi: () => pending }); const task = h.load(); h.dispose(); finish({ id: 99 }); await task
  assert.equal(h.state.kb.value.id, 1); assert.equal(h.calls.schedule, 0)
})
test('Uploads are guarded in both the visible control and handler until scope is valid', () => {
  assert.match(text, /:disabled="!kb \|\| loading \|\| Boolean\(error\)"/)
  assert.match(text, /if \(uploading\.value \|\| !kb\.value \|\| error\.value \|\| loading\.value\) return/)
})
