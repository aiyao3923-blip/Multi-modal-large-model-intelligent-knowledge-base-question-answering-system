import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import vm from 'node:vm'

const storeSource = await readFile(new URL('../src/stores/user.js', import.meta.url), 'utf8')
const httpSource = await readFile(new URL('../src/api/http.js', import.meta.url), 'utf8')
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }
function createStore(pending) {
  const values = new Map()
  const sandbox = {
    parseJson(value, fallback) { try { return JSON.parse(value) ?? fallback } catch { return fallback } },
    localStorage: { getItem: key => values.get(key), setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) },
    loadApi: () => Promise.resolve({ meApi: () => pending.promise }),
    defineStore(_name, options) {
      const store = options.state()
      for (const [name, getter] of Object.entries(options.getters)) Object.defineProperty(store, name, { get: () => getter(store) })
      for (const [name, action] of Object.entries(options.actions)) store[name] = action.bind(store)
      return () => store
    },
  }
  vm.runInNewContext(storeSource.replace(/^import .*$/gm, '').replace('export const useUserStore', 'globalThis.useUserStore').replace("import('@/api')", 'loadApi()'), sandbox)
  return sandbox.useUserStore()
}
for (const outcome of ['resolve', 'reject']) {
  test('An old session validation ' + outcome + ' cannot replace a newer login', async () => {
    const pending = deferred(), store = createStore(pending)
    store.setLogin('isolated-old', { username: 'old', role_code: 'admin' })
    store.checked = false
    const validation = store.validateSession()
    store.setLogin('isolated-new', { username: 'new', role_code: 'user' })
    if (outcome === 'resolve') pending.resolve({ username: 'old', role_code: 'admin' })
    else pending.reject(new Error('expired old request'))
    await validation
    assert.equal(store.token, 'isolated-new')
    assert.equal(store.user.username, 'new')
    assert.equal(store.isAdmin, false)
  })
}

test('Only a 401 for the current token clears authentication and redirects', async () => {
  const hooks = {}, redirects = []
  const store = { token: 'isolated-old', logout() { this.token = ''; this.loggedOut = true } }
  const http = { interceptors: { request: { use(fn) { hooks.request = fn } }, response: { use(_ok, fail) { hooks.failure = fail } } } }
  const sandbox = { axios: { create: () => http, isCancel: () => false }, useUserStore: () => store, ElMessage: { error() {} }, router: { currentRoute: { value: { path: '/', fullPath: '/' } }, replace: value => redirects.push(value) } }
  vm.runInNewContext(httpSource.replace(/^import .*$/gm, '').replace('export default http', 'globalThis.http = http'), sandbox)
  const oldConfig = hooks.request({ headers: {} })
  store.token = 'isolated-new'
  await assert.rejects(hooks.failure({ response: { status: 401 }, config: oldConfig }))
  assert.equal(store.token, 'isolated-new'); assert.equal(redirects.length, 0)
  const currentConfig = hooks.request({ headers: {} })
  await assert.rejects(hooks.failure({ response: { status: 401 }, config: currentConfig }))
  assert.equal(store.token, ''); assert.equal(redirects.length, 1)
})
