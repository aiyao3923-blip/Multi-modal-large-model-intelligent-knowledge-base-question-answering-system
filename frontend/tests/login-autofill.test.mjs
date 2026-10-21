import test from 'node:test'
import assert from 'node:assert/strict'
import vm from 'node:vm'
import { readFile } from 'node:fs/promises'
import { parse } from '@vue/compiler-sfc'
import { readSavedLogin, saveLogin, readLoginInputs } from '../src/utils/loginCredentials.js'

// Isolated synthetic credentials only; never contacts a live login endpoint or password store.
const synthetic = { username: 'admin', password: 'synthetic-login-test-only' }
function browser({ credential = { type: 'password', id: synthetic.username, password: synthetic.password }, failGet = false, failStore = false, secure = true } = {}) {
  const calls = []
  class PasswordCredential {
    constructor(value) { Object.assign(this, value); this.type = 'password' }
  }
  const credentials = {
    async get(options) {
      assert.equal(this, credentials)
      calls.push(['get', options])
      if (failGet) throw new Error('synthetic browser refusal')
      return credential
    },
    async store(value) {
      assert.equal(this, credentials)
      calls.push(['store', value])
      if (failStore) throw new Error('synthetic browser refusal')
      return value
    },
  }
  return { scope: { isSecureContext: secure, PasswordCredential, navigator: { credentials } }, calls }
}
const loginSource = await readFile(new URL('../src/views/Login.vue', import.meta.url), 'utf8')
const helperSource = await readFile(new URL('../src/utils/loginCredentials.js', import.meta.url), 'utf8')
const script = parse(loginSource).descriptor.scriptSetup.content.replace(/^import .*$/gm, '')
function loginHarness({ saved = null, dom, loginError, store } = {}) {
  const calls = [], sessions = [], destinations = [], saves = [], mounted = [], unmount = []
  const sandbox = {
    ref: value => ({ value }), reactive: value => value,
    onMounted: callback => mounted.push(callback), onBeforeUnmount: callback => unmount.push(callback),
    useRoute: () => ({ query: {} }), useRouter: () => ({ replace: async target => destinations.push(target) }),
    useUserStore: () => ({ setLogin: (...args) => sessions.push(args) }),
    readSavedLogin: async () => saved,
    readLoginInputs,
    saveLogin: value => { saves.push({ ...value }); return store ? store(value) : Promise.resolve(true) },
    loginApi: async value => { calls.push({ ...value }); if (loginError) throw loginError; return { token: 'synthetic-token', user: { username: value.username } } },
  }
  vm.runInNewContext(script + '\nglobalThis.api = { form, loginForm, loading, error, autoFilled, markEdited, prefillSavedLogin, doLogin };', sandbox)
  const api = sandbox.api
  api.loginForm.value = { validate: async () => true, $el: dom }
  return { ...api, calls, sessions, destinations, saves, mount: () => Promise.all(mounted.map(fn => fn())), dispose: () => unmount.forEach(fn => fn()) }
}
function inputs(username, password) {
  const fields = { username: { value: username }, password: { value: password } }
  return { fields, querySelector: selector => fields[selector.match(/name="([^"]+)"/)[1]] }
}
function deferred() {
  let resolve
  const promise = new Promise(r => { resolve = r })
  return { promise, resolve }
}

test('Credential reads are silent, origin-owned browser operations and do not initiate login', async () => {
  const b = browser()
  assert.deepEqual(await readSavedLogin(b.scope), synthetic)
  assert.deepEqual(b.calls, [['get', { password: true, mediation: 'silent' }]])
})

test('Insecure, unsupported and refused credential reads fail closed to manual input', async () => {
  for (const scope of [{}, { isSecureContext: true }, browser({ secure: false }).scope, { isSecureContext: true, navigator: { credentials: {} }, PasswordCredential: class {} }]) {
    assert.equal(await readSavedLogin(scope), null)
  }
  assert.equal(await readSavedLogin(browser({ failGet: true }).scope), null)
  const unsafe = browser({ secure: false })
  assert.equal(await readSavedLogin(unsafe.scope), null)
  assert.deepEqual(unsafe.calls, [])
})

test('Credential reads reject wrong types, empty passwords and invalid account names', async () => {
  for (const credential of [null, {}, { type: 'public-key', id: 'admin', password: synthetic.password },
    { type: 'password', id: '', password: synthetic.password }, { type: 'password', id: ' ', password: synthetic.password },
    { type: 'password', id: 'a'.repeat(65), password: synthetic.password }, { type: 'password', id: 1, password: synthetic.password },
    { type: 'password', id: 'admin', password: '' }, { type: 'password', id: 'admin', password: 123 }]) {
    assert.equal(await readSavedLogin(browser({ credential }).scope), null)
  }
})

test('Saving uses a native PasswordCredential and never copies passwords into application storage', async () => {
  const b = browser()
  for (const name of ['localStorage', 'sessionStorage', 'document', 'fetch']) Object.defineProperty(b.scope, name, { get() { throw new Error('Forbidden application persistence or network') } })
  assert.equal(await saveLogin(synthetic, b.scope), true)
  assert.equal(b.calls.length, 1)
  assert.equal(b.calls[0][0], 'store')
  assert.ok(b.calls[0][1] instanceof b.scope.PasswordCredential)
  assert.equal(b.calls[0][1].id, synthetic.username)
  assert.equal(b.calls[0][1].password, synthetic.password)
})

test('Unsupported, declined or invalid credential saving is best-effort and never throws', async () => {
  assert.equal(await saveLogin(synthetic, {}), false)
  assert.equal(await saveLogin(synthetic, browser({ secure: false }).scope), false)
  assert.equal(await saveLogin(synthetic, browser({ failStore: true }).scope), false)
  for (const value of [null, {}, { username: 'admin', password: '' }, { username: ' ', password: synthetic.password }]) {
    const b = browser()
    assert.equal(await saveLogin(value, b.scope), false)
    assert.deepEqual(b.calls, [])
  }
  const b = browser()
  b.scope.PasswordCredential = class { constructor() { throw new Error('Unsupported platform') } }
  assert.equal(await saveLogin(synthetic, b.scope), false)
})

test('Native autofill DOM values are used even when Vue receives no input event', () => {
  assert.deepEqual(readLoginInputs(inputs('other-reader', 'synthetic-dom-only'), synthetic), { username: 'other-reader', password: 'synthetic-dom-only' })
  assert.deepEqual(readLoginInputs(inputs('', ''), synthetic), { username: '', password: '' })
  assert.deepEqual(readLoginInputs(undefined, synthetic), synthetic)
  assert.deepEqual(readLoginInputs({ querySelector: () => null }, synthetic), synthetic)
})

test('Login offers standard password-manager fields, masks the password and has no plaintext preset', () => {
  for (const [name, autocomplete] of [['username', 'username'], ['password', 'current-password']]) {
    assert.match(loginSource, new RegExp(`name="${name}"[^>]*autocomplete="${autocomplete}"`))
  }
  assert.match(loginSource, /method="post" action="\/api\/auth\/login" autocomplete="on"/)
  assert.match(loginSource, /username: 'admin', password: ''/)
  assert.match(loginSource, /showPassword \? 'text' : 'password'/)
  assert.doesNotMatch(loginSource + helperSource, /localStorage|sessionStorage|document\.cookie|VITE_.*PASSWORD|console\./)
  assert.match(loginSource, /@input="markEdited"/)
})

test('Mount fills saved admin credentials but never logs in or persists on page load', async () => {
  const h = loginHarness({ saved: synthetic })
  assert.equal(h.form.username, 'admin')
  assert.equal(h.form.password, '')
  await h.mount()
  assert.equal(h.form.username, 'admin')
  assert.equal(h.form.password, synthetic.password)
  assert.equal(h.autoFilled.value, true)
  assert.equal(h.calls.length, 0)
  assert.equal(h.saves.length, 0)
})

test('Absent credentials and different saved accounts leave the administrator form unchanged', async () => {
  for (const saved of [null, { username: 'different-reader', password: synthetic.password }]) {
    const h = loginHarness({ saved })
    await h.mount()
    assert.equal(h.form.username, 'admin')
    assert.equal(h.form.password, '')
    assert.equal(h.autoFilled.value, false)
    assert.equal(h.calls.length, 0)
  }
})

test('Slow autofill never overrides typed, DOM-filled, submitting or unmounted forms', async () => {
  for (const scenario of ['edited', 'username', 'password', 'dom-username', 'dom-password', 'submitting', 'unmounted']) {
    const pending = deferred(), dom = inputs('admin', '')
    const h = loginHarness({ saved: pending.promise, dom })
    const work = h.mount()
    if (scenario === 'edited') h.markEdited()
    if (scenario === 'username') h.form.username = 'typed-reader'
    if (scenario === 'password') h.form.password = 'synthetic-typed-password'
    if (scenario === 'dom-username') dom.fields.username.value = 'browser-selected-reader'
    if (scenario === 'dom-password') dom.fields.password.value = 'synthetic-native-fill'
    if (scenario === 'submitting') h.loading.value = true
    if (scenario === 'unmounted') h.dispose()
    const before = { ...h.form }
    pending.resolve(synthetic)
    await work
    assert.deepEqual({ ...h.form }, before, scenario)
    assert.equal(h.autoFilled.value, false, scenario)
    assert.equal(h.calls.length, 0, scenario)
  }
})

test('A successful manual login synchronizes native values, saves best-effort and clears form state', async () => {
  const h = loginHarness({ dom: inputs(' admin ', synthetic.password), store: () => new Promise(() => {}) })
  await h.doLogin()
  assert.deepEqual(h.calls, [synthetic])
  assert.deepEqual(h.saves, [synthetic])
  assert.equal(h.sessions.length, 1)
  assert.deepEqual(h.destinations, ['/'])
  assert.equal(h.form.password, '')
  assert.equal(h.loading.value, false, 'pending native save must not hold the submit spinner')
})

test('Rejected login does not save credentials or create a session and stays retryable', async () => {
  const h = loginHarness({ loginError: { response: { data: { detail: 'synthetic login rejected' } } } })
  Object.assign(h.form, synthetic)
  await h.doLogin()
  assert.equal(h.saves.length, 0)
  assert.equal(h.sessions.length, 0)
  assert.equal(h.destinations.length, 0)
  assert.equal(h.error.value, 'synthetic login rejected')
  assert.equal(h.loading.value, false)
})

test('Unmount releases local form password and user edits clear the autofill hint', async () => {
  const h = loginHarness({ saved: synthetic })
  await h.mount()
  h.markEdited()
  assert.equal(h.autoFilled.value, false)
  h.dispose()
  assert.equal(h.form.password, '')
})
test('A logout render tolerates a cleared user before the router unmounts the reader', async () => {
  const Vue = await import('vue')
  const { compile } = await import('@vue/compiler-dom')
  const { renderToString } = await import('vue/server-renderer')
  const source = await readFile(new URL('../src/views/chat/ChatView.vue', import.meta.url), 'utf8')
  const render = new Function('Vue', compile(parse(source).descriptor.template.content, { mode: 'function', prefixIdentifiers: true }).code)(Vue)
  for (const activeUser of [{ id: 7 }, null]) {
    const app = Vue.createSSRApp({
      setup: () => ({ user: { user: activeUser }, readerFont: 16, exportVisible: false, feedbackVisible: false }),
      render,
    })
    app.component('ResizableWorkspace', { props: ['storageKey'], render() { return Vue.h('div', { 'data-layout-key': this.storageKey }) } })
    app.component('el-dialog', { render: () => null })
    app.config.warnHandler = () => {}
    const html = await renderToString(app)
    if (activeUser) assert.match(html, /data-layout-key="paper-workspace-layout-v1-7"/)
    else assert.doesNotMatch(html, /data-layout-key=/)
  }
})