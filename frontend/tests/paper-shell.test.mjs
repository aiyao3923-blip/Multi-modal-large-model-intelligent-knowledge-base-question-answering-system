import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile, access } from 'node:fs/promises'
import vm from 'node:vm'
import * as Vue from 'vue'
import { createMemoryHistory, createRouter as createVueRouter } from 'vue-router'
import { renderToString } from 'vue/server-renderer'
import { parse, compileScript, compileTemplate, compileStyle } from '@vue/compiler-sfc'

// Source-only and mocked event tests. Never start a browser, service or real login.
const root = new URL('../src/', import.meta.url)
const read = path => readFile(new URL(path, root), 'utf8')
const shellSource = await read('components/WorkspaceShell.vue')
const routerSource = await read('router/index.js')
const loginSource = await read('views/Login.vue')
const css = await read('styles.css')
const scriptOf = source => parse(source).descriptor.scriptSetup.content.replace(/^import .*$/gm, '')
const ref = value => ({ value })
const computed = getter => ({ get value() { return getter() } })

function luminance(hex) {
  const values = hex.slice(1).match(/../g).map(value => parseInt(value, 16) / 255)
    .map(value => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4)
  return values[0] * .2126 + values[1] * .7152 + values[2] * .0722
}
function contrast(a, b) {
  const x = luminance(a), y = luminance(b)
  return (Math.max(x, y) + .05) / (Math.min(x, y) + .05)
}

test('Paper workspace tokens match the shared contract and retain readable contrast', () => {
  const colors = Object.fromEntries([...css.matchAll(/--color-([a-z-]+):\s*(#[a-f\d]{6})/gi)].map(match => [match[1], match[2].toUpperCase()]))
  for (const [name, value] of Object.entries({ page: '#F4F6F9', surface: '#FFFFFF', ink: '#172033', secondary: '#5D687B', line: '#DFE4ED', accent: '#365CF5', success: '#167C61', warning: '#9A6700', danger: '#C23B4B' })) assert.equal(colors[name], value, name)
  for (const [fg, bg] of [['ink', 'surface'], ['secondary', 'page'], ['muted', 'page'], ['muted', 'surface'], ['surface', 'accent'], ['accent', 'page'], ['accent', 'accent-soft'], ['success', 'success-soft'], ['warning', 'warning-soft'], ['danger', 'danger-soft'], ['info', 'info-soft']]) {
    assert.ok(contrast(colors[fg], colors[bg]) >= 4.5, `${fg}/${bg}: ${contrast(colors[fg], colors[bg]).toFixed(2)}`)
  }
  for (const name of ['focus', 'control-border']) assert.ok(contrast(colors[name], colors.surface) >= 3, name)
  assert.match(css, /--font-body:[^;]*"Segoe UI"[^;]*"PingFang SC"[^;]*"Microsoft YaHei"[^;]*sans-serif/)
  assert.match(css, /--font-display:\s*var\(--font-body\)/)
  assert.doesNotMatch(css, /Songti|SimSun|Noto Serif|cinnabar|#AC3F28|#F4F1EB/i)
})

test('Existing chat aliases and the 216/56/272 shell dimensions remain available', () => {
  for (const name of ['brand', 'brand-hover', 'brand-soft', 'ink', 'muted', 'text-secondary', 'line', 'surface', 'page']) assert.match(css, new RegExp(`--${name}:\\s*var\\(--color-`))
  for (const [name, value] of [['sidebar-width', 216], ['header-height', 56], ['drawer-width', 272]]) assert.match(css, new RegExp(`--workspace-${name}:\\s*${value}px`))
  assert.match(css, /--page-padding:\s*24px/)
  assert.match(css, /max-width: 899\.98px[^}]*--page-padding:\s*16px/)
  assert.match(shellSource, /matchMedia\('\(max-width: 899\.98px\)'\)/)
  assert.match(shellSource, /@media \(max-width: 899\.98px\)/)
  assert.match(css, /prefers-reduced-motion: reduce/)
  assert.match(shellSource, /prefers-reduced-motion: reduce/)
  assert.match(css, /:focus-visible/)
  assert.match(shellSource, /workspace-content \{[^}]*min-width: 0; min-height: 0/)
})

function userFor(role = 'user') {
  return { token: 'isolated-session', checked: true, isLogin: true, isAdmin: role === 'admin', isOperator: ['admin', 'operator'].includes(role), role, user: { username: 'fixture-reader' }, logout() {} }
}
function routing(user = userFor()) {
  const state = { user }
  const sandbox = {
    useUserStore: () => state.user,
    createWebHistory: createMemoryHistory,
    createRouter(options) {
      const router = createVueRouter(options)
      router.beforeEach = callback => { state.guard = callback }
      router.afterEach = callback => { state.after = callback }
      state.router = router
      return router
    },
    document: { title: '' },
  }
  vm.runInNewContext(routerSource.replace(/^import .*$/gm, '').replace('export default router', ''), sandbox)
  state.check = path => state.guard(state.router.resolve(path))
  return state
}

test('Library owners can enter knowledge and import routes without operator privileges', async () => {
  const routes = routing()
  const layout = routes.router.options.routes.find(route => route.path === '/admin')
  assert.equal(layout.meta?.op, undefined)
  assert.equal(layout.meta?.admin, undefined)
  assert.equal(layout.redirect, '/admin/kbs')
  for (const path of ['/admin/kbs', '/admin/kbs/42', '/admin/tasks']) assert.equal(await routes.check(path), true, path)
  for (const path of ['/admin/overview', '/admin/feedbacks', '/admin/stats', '/admin/channels', '/admin/users']) assert.equal(await routes.check(path), '/', path)
})

test('Auxiliary tools and sensitive settings retain independent role guards', async () => {
  const operator = routing(userFor('operator')), admin = routing(userFor('admin'))
  for (const path of ['/admin/overview', '/admin/feedbacks', '/admin/stats']) assert.equal(await operator.check(path), true, path)
  for (const path of ['/admin/channels', '/admin/users']) { assert.equal(await operator.check(path), '/'); assert.equal(await admin.check(path), true) }
  for (const role of ['user', 'operator', 'admin']) assert.equal(await routing(userFor(role)).check('/login'), '/')
})

test('Unverified or missing sessions cannot enter an owner route', async () => {
  const anonymous = userFor()
  anonymous.token = ''; anonymous.isLogin = false
  const destination = await routing(anonymous).check('/admin/kbs/42?tab=docs')
  assert.equal(destination.path, '/login')
  assert.equal(destination.query.redirect, '/admin/kbs/42?tab=docs')
  const expired = userFor()
  let checks = 0
  expired.checked = false
  expired.validateSession = async () => { checks++; expired.checked = true; expired.isLogin = false; expired.token = '' }
  assert.equal((await routing(expired).check('/admin/tasks')).path, '/login')
  assert.equal(checks, 1)
})

test('Removed template bookmarks redirect to knowledge and every live page import exists', async () => {
  await assert.rejects(access(new URL('views/admin/TemplateManage.vue', root)), { code: 'ENOENT' })
  assert.doesNotMatch(routerSource, /import\([^)]*TemplateManage/)
  assert.doesNotMatch(shellSource, /templates|行业模板|sidebar-note|environment-tag|nav-marker/)
  const routes = routing()
  for (const path of ['/admin/templates', '/admin/templates/', '/admin/templates/retired']) assert.equal(routes.router.resolve(path).matched.at(-1)?.redirect, '/admin/kbs', path)
  for (const match of routerSource.matchAll(/import\('@\/([^']+)'\)/g)) await access(new URL(match[1], root))
})

function shell() {
  const document = { activeElement: null }
  const route = { path: '/', fullPath: '/', meta: {} }
  const watchers = [], mounted = [], unmounted = []
  const listeners = new Map(), mediaListeners = new Map()
  const media = { matches: true, addEventListener: (name, callback) => mediaListeners.set(name, callback), removeEventListener: (name, callback) => { assert.equal(mediaListeners.get(name), callback); mediaListeners.delete(name) } }
  const sandbox = {
    ref, computed, document, useRoute: () => route, useRouter: () => ({ replace() {} }), useUserStore: () => userFor('admin'),
    nextTick: callback => Promise.resolve().then(callback),
    watch: (target, callback) => watchers.push({ target, callback }),
    onMounted: callback => mounted.push(callback), onBeforeUnmount: callback => unmounted.push(callback),
    window: { matchMedia: () => media, addEventListener: (name, callback) => listeners.set(name, callback), removeEventListener: (name, callback) => { assert.equal(listeners.get(name), callback); listeners.delete(name) } },
  }
  vm.runInNewContext(scriptOf(shellSource) + '\nglobalThis.api = { primaryLinks, toolLinks, settingsLinks, sidebar, closeButton, openButton, mainContent, compact, menuOpen, toolsOpen, settingsOpen, closeNavigation, onNavigationKey, onNavigationFocus, onCompactChange, focusableNavigation };', sandbox)
  const api = sandbox.api
  const node = (name, visible = true, inert = false) => ({ name, focus() { document.activeElement = this }, getClientRects: () => visible ? [{}] : [], closest: () => inert ? {} : null })
  const first = node('close'), middle = node('primary-link'), last = node('logout')
  const controls = [first, node('closed-details-link', false), middle, node('inert-control', true, true), last]
  const aside = node('sidebar')
  aside.querySelectorAll = () => controls
  aside.contains = value => value === aside || controls.includes(value)
  api.sidebar.value = aside; api.closeButton.value = first
  api.openButton.value = node('opener'); api.mainContent.value = node('main')
  const syncMenu = () => watchers.find(watcher => watcher.target === api.menuOpen).callback(api.menuOpen.value)
  const changeRoute = () => watchers.find(watcher => typeof watcher.target === 'function').callback()
  return { ...api, api, document, first, middle, last, route, syncMenu, changeRoute, mounted, unmounted, listeners, mediaListeners }
}
const key = (value, shiftKey = false) => ({ key: value, shiftKey, prevented: false, preventDefault() { this.prevented = true } })

test('Primary navigation is the reading workflow; admin tools are separate disclosures', () => {
  const state = shell()
  assert.deepEqual(Array.from(state.primaryLinks, link => link.label), ['论文问答', '知识库', '导入任务'])
  assert.deepEqual(Array.from(state.settingsLinks, link => link.to), ['/admin/channels', '/admin/users'])
  assert.match(shellSource, /<details v-if="user\.isOperator"/)
  assert.match(shellSource, /<details v-if="user\.isAdmin"/)
  assert.match(shellSource, /id="main-content" tabindex="-1"/)
  assert.match(shellSource, /:aria-modal=/)
  assert.match(shellSource, /:inert="compact && !menuOpen"/)
  assert.match(shellSource, /:inert="compact && menuOpen"/)
})

test('Drawer opens on its close control, traps visible controls and restores focus on Escape', async () => {
  const state = shell()
  state.menuOpen.value = true
  await state.syncMenu()
  assert.equal(state.document.activeElement, state.first)
  assert.deepEqual(Array.from(state.focusableNavigation(), item => item.name), ['close', 'primary-link', 'logout'])
  let event = key('Tab', true)
  state.onNavigationKey(event)
  assert.equal(event.prevented, true); assert.equal(state.document.activeElement, state.last)
  event = key('Tab')
  state.onNavigationKey(event)
  assert.equal(event.prevented, true); assert.equal(state.document.activeElement, state.first)
  state.middle.focus(); event = key('Tab'); state.onNavigationKey(event)
  assert.equal(event.prevented, false, 'normal movement inside the drawer remains native')
  event = key('Escape'); state.onNavigationKey(event); await state.syncMenu()
  assert.equal(event.prevented, true); assert.equal(state.menuOpen.value, false)
  assert.equal(state.document.activeElement, state.openButton.value)
})

test('Focus cannot escape an open drawer and route/breakpoint changes close it safely', async () => {
  const state = shell(), outside = { name: 'outside' }
  state.menuOpen.value = true; state.document.activeElement = outside
  state.onNavigationKey(key('Tab')); assert.equal(state.document.activeElement, state.first)
  state.document.activeElement = outside; state.onNavigationKey(key('Tab', true)); assert.equal(state.document.activeElement, state.last)
  state.onNavigationFocus({ target: outside }); assert.equal(state.document.activeElement, state.first)
  state.route.path = '/admin/channels'; state.route.fullPath = state.route.path; state.changeRoute()
  assert.equal(state.menuOpen.value, false); assert.equal(state.settingsOpen.value, true)
  await state.syncMenu(); assert.equal(state.document.activeElement, state.openButton.value)
  state.menuOpen.value = true; state.first.focus(); state.onCompactChange({ matches: false })
  await Promise.resolve()
  assert.equal(state.compact.value, false); assert.equal(state.menuOpen.value, false); assert.equal(state.document.activeElement, state.mainContent.value)
  const event = key('Tab'); state.onNavigationKey(event); assert.equal(event.prevented, false)
})

test('Drawer listener lifecycle is balanced and no focusable controls is safe', () => {
  const state = shell()
  state.mounted.forEach(callback => callback())
  assert.equal(state.listeners.size, 2); assert.equal(state.mediaListeners.size, 1)
  state.menuOpen.value = true; state.sidebar.value.querySelectorAll = () => []
  const event = key('Tab'); state.onNavigationKey(event)
  assert.equal(event.prevented, true); assert.equal(state.document.activeElement, state.sidebar.value)
  state.unmounted.forEach(callback => callback())
  assert.equal(state.listeners.size, 0); assert.equal(state.mediaListeners.size, 0)
})

async function componentFrom(path) {
  const { descriptor } = parse(await read(path), { filename: path })
  let source = compileScript(descriptor, { id: path, inlineTemplate: true }).content
  const sandbox = {}
  source = source.replace(/import\s*\{([^}]+)\}\s*from\s*['"]vue['"];?/g, (_match, names) => {
    for (const item of names.split(',')) { const [name, alias] = item.trim().split(/\s+as\s+/); sandbox[alias || name] = Vue[name] }
    return ''
  }).replace('export default', 'globalThis.component =')
  vm.runInNewContext(source, sandbox)
  return sandbox.component
}

test('Heading renders only its title and supplied actions while accepting legacy props', async () => {
  const heading = await componentFrom('components/PageHeading.vue')
  const html = await renderToString(Vue.createSSRApp({ render: () => Vue.h(heading, { title: '知识库', description: 'LEGACY_DESCRIPTION', eyebrow: 'LEGACY_EYEBROW' }, { default: () => Vue.h('button', '上传论文') }) }))
  assert.match(html, /<h1>知识库<\/h1>/); assert.match(html, /上传论文/)
  assert.doesNotMatch(html, /LEGACY_DESCRIPTION|LEGACY_EYEBROW|<p\b/)
  const empty = await renderToString(Vue.createSSRApp({ render: () => Vue.h(heading, { title: '导入任务' }) }))
  assert.doesNotMatch(empty, /class="page-actions"/)
  const named = await renderToString(Vue.createSSRApp({ render: () => Vue.h(heading, { title: '知识库' }, { actions: () => Vue.h('button', '新建') }) }))
  assert.match(named, /新建/)
})

test('Empty state keeps a real next action and does not render an empty paragraph', async () => {
  const emptyState = await componentFrom('components/EmptyState.vue')
  const app = Vue.createSSRApp({ render: () => Vue.h(emptyState, { title: '暂无论文' }, { default: () => Vue.h('button', '上传论文') }) })
  app.component('AppIcon', { render: () => Vue.h('svg') })
  const html = await renderToString(app)
  assert.match(html, /暂无论文/); assert.match(html, /上传论文/); assert.doesNotMatch(html, /<p\b/)
})

function login(redirect, failure) {
  const calls = [], locations = [], sessions = []
  const sandbox = {
    ref, reactive: value => value, useRoute: () => ({ query: { redirect } }), useRouter: () => ({ replace: async value => locations.push(value) }),
    onMounted: () => {}, onBeforeUnmount: () => {}, readSavedLogin: async () => null, saveLogin: async () => false, readLoginInputs: (_element, fallback) => ({ ...fallback }),
    useUserStore: () => ({ setLogin: (...value) => sessions.push(value) }),
    loginApi: async value => { calls.push(value); if (failure) throw failure; return { token: 'isolated-result', user: { username: 'fixture-reader' } } },
  }
  vm.runInNewContext(scriptOf(loginSource) + '\nglobalThis.api = { doLogin, form, loginForm, loading, error };', sandbox)
  sandbox.api.loginForm.value = { validate: async () => true }
  sandbox.api.form.username = ' fixture-reader '
  sandbox.api.form.password = 'test-only-input'
  return { ...sandbox.api, calls, locations, sessions }
}

test('Login has no demo preset and redirects every role to reading or a safe local path', async () => {
  assert.doesNotMatch(loginSource, /fillDemo|demo-access|login-story|story-index|user\.isOperator/)
  for (const id of ['login-username', 'login-password']) assert.ok(loginSource.includes(`id="${id}"`))
  assert.match(loginSource, /role="alert"/); assert.match(loginSource, /native-type="submit"/)
  for (const [target, expected] of [[undefined, '/'], ['/admin/kbs/42?tab=docs', '/admin/kbs/42?tab=docs'], ['https://example.invalid', '/'], ['//example.invalid', '/'], ['/\\example.invalid', '/'], ['/login?redirect=/', '/'], [['/admin/kbs'], '/'], ['/admin/\u0000kbs', '/']]) {
    const state = login(target)
    assert.equal(state.calls.length, 0, 'render/setup never initiates login')
    await state.doLogin()
    assert.equal(state.locations[0], expected)
    assert.equal(state.form.password, '', 'clear the local form after a successful login')
    assert.equal(state.calls[0].username, 'fixture-reader')
  }
})

test('Login preserves inline failure, validation and duplicate-submit protection', async () => {
  const invalid = login('/')
  invalid.loginForm.value.validate = async () => { throw new Error('invalid fixture') }
  await invalid.doLogin(); assert.equal(invalid.calls.length, 0)
  const busy = login('/')
  busy.loading.value = true; await busy.doLogin(); assert.equal(busy.calls.length, 0)
  const failed = login('/', { response: { data: { detail: 'fixture login unavailable' } } })
  await failed.doLogin()
  assert.equal(failed.loading.value, false)
  assert.equal(failed.error.value, 'fixture login unavailable')
})

test('Login blocks a second submit while asynchronous validation is still pending', async () => {
  const state = login('/')
  let release
  const validation = new Promise(resolve => { release = resolve })
  state.loginForm.value.validate = () => validation
  const first = state.doLogin(), second = state.doLogin()
  assert.equal(state.loading.value, true)
  release(true)
  await Promise.all([first, second])
  assert.equal(state.calls.length, 1)
  assert.equal(state.locations.length, 1)
  assert.equal(state.loading.value, false)
})
test('Secondary tools drop duplicate exposition but retain dangerous-action confirmation', async () => {
  const task = await read('views/admin/TaskCenter.vue'), user = await read('views/admin/UserManage.vue')
  const channel = await read('views/admin/ChannelManage.vue'), feedback = await read('views/admin/FeedbackCenter.vue'), overview = await read('views/admin/Overview.vue')
  assert.doesNotMatch(task, /task-stage-index|task-process|const steps=/)
  assert.doesNotMatch(user, /role-index|admin-annotation/)
  assert.doesNotMatch(channel, /channel-routing|admin-annotation/)
  assert.doesNotMatch(feedback, /feedback-intro/)
  assert.doesNotMatch(overview, /overview-index|overview-mode|capabilitiesApi/)
  for (const source of [task, user, channel]) { assert.match(source, /ElMessageBox\.confirm/); assert.match(source, /role="alert"/); assert.match(source, /loading/) }
  assert.match(channel, /title="检测会向此端点发送请求，在线服务可能计费"/)
  assert.match(channel, /问题和知识片段可能发送到/)
  assert.match(user, /确认重置密码/)
  assert.match(feedback, /处理记录不代表已完成答案复测/)
})

test('All owned Vue/CSS files compile and shell icons resolve to the existing icon set', async () => {
  const owned = ['components/WorkspaceShell.vue', 'components/PageHeading.vue', 'components/EmptyState.vue', 'views/Login.vue', ...['Overview', 'TaskCenter', 'FeedbackCenter', 'StatsView', 'ChannelManage', 'UserManage'].map(name => `views/admin/${name}.vue`)]
  for (const path of owned) {
    const source = await read(path), { descriptor, errors } = parse(source, { filename: path })
    assert.deepEqual(errors, [], path)
    const script = compileScript(descriptor, { id: path })
    const template = compileTemplate({ source: descriptor.template.content, filename: path, id: path, compilerOptions: { bindingMetadata: script.bindings } })
    assert.deepEqual(template.errors, [], path)
    for (const style of descriptor.styles) assert.deepEqual(compileStyle({ source: style.content, filename: path, id: path, scoped: style.scoped }).errors, [], path)
    assert.ok(!source.includes('\uFFFD'), path)
  }
  for (const path of ['styles.css', 'views/admin/common.css']) assert.deepEqual(compileStyle({ source: await read(path), filename: path, id: path }).errors, [], path)
  const icons = new Set([...(await read('components/AppIcon.vue')).matchAll(/^\s{2}([A-Za-z]+):/gm)].map(match => match[1]))
  for (const match of shellSource.matchAll(/<AppIcon name="([^"]+)"/g)) assert.ok(icons.has(match[1]), match[1])
  for (const link of shell().primaryLinks) assert.ok(icons.has(link.icon), link.icon)
})

