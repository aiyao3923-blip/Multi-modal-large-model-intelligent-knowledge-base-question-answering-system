import test from 'node:test'
import assert from 'node:assert/strict'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { readFile } from 'node:fs/promises'
import { spawnSync } from 'node:child_process'
import viteConfig from '../vite.config.js'
import { loadConfig, resolveTabbitCli, isAllowedRequest, buildProgram, runSmoke, main } from '../../scripts/smoke.mjs'

const cli = process.execPath // A harmless local executable for filesystem-only checks; never launch Tabbit in tests.
const env = { TABBIT_CLI: cli, SMOKE_BASE_URL: 'http://127.0.0.1:5187', SMOKE_ISOLATED: '1' }
const config = loadConfig([], env)
const success = { status: 0, stdout: JSON.stringify({ status: 'succeeded', result: { checked: true, nextAction: { path: 'path-returned-by-cli.png' } } }), stderr: '' }
const noSpawn = () => { throw new Error('A browser/CLI must not be started by this test') }

test('Smoke configuration is cwd-independent and defaults to loopback', () => {
  const defaults = loadConfig([], {})
  assert.equal(defaults.baseUrl, 'http://127.0.0.1:5173')
  assert.equal(defaults.isolated, false)
  assert.equal(config.baseUrl, 'http://127.0.0.1:5187')
  for (const url of ['http://localhost:4173/', 'https://127.0.0.1:4173', 'http://[::1]:5173/']) {
    assert.equal(loadConfig([], { SMOKE_BASE_URL: url }).baseUrl, new URL(url).origin)
  }
})

test('Smoke refuses external URLs, URL credentials and ambiguous base paths', () => {
  for (const url of ['https://example.com', 'http://localhost.example.com', 'http://0.0.0.0:5173', 'http://192.168.1.2:5173', 'file:///C:/index.html', 'http://user:password@localhost:5173', 'http://localhost:5173/admin', 'http://localhost:5173/?token=x', 'http://localhost:5173/#x', 'not-a-url']) {
    assert.throws(() => loadConfig([], { SMOKE_BASE_URL: url }), /SMOKE_BASE_URL/)
  }
  assert.throws(() => loadConfig(['--allow-external'], {}), /Unknown argument/)
  assert.throws(() => loadConfig([], { SMOKE_TASK: '../other-task' }), /SMOKE_TASK/)
})

test('Tabbit resolution uses the installed per-user stable launcher, never npm caches', () => {
  const winEnv = { LOCALAPPDATA: String.raw`C:\Users\本机 用户\AppData\Local` }
  assert.equal(resolveTabbitCli(winEnv, { platform: 'win32', isFile: () => true }), path.win32.join(winEnv.LOCALAPPDATA, 'Tabbit', 'LocalAgent', 'bin', 'tabbit-cli.exe'))
  assert.equal(resolveTabbitCli({}, { platform: 'darwin', home: '/Users/test user', isFile: () => true }), '/Users/test user/.local/bin/tabbit-cli')
  assert.equal(resolveTabbitCli(env, { isFile: () => true }), cli)
  assert.throws(() => resolveTabbitCli({ TABBIT_CLI: 'relative-cli.exe' }, { isFile: () => true }), /absolute/)
  assert.throws(() => resolveTabbitCli(env, { isFile: () => false }), /Tabbit.*not found/)
})

test('Help and --check never spawn a CLI or navigate a browser', () => {
  const output = []
  main(['--help'], { env: {}, spawn: noSpawn, write: line => output.push(line) })
  assert.match(output[0], /--check/)
  main(['--check'], { env: { ...env, SMOKE_ISOLATED: '0' }, spawn: noSpawn, write: line => output.push(line) })
  const result = JSON.parse(output[1])
  assert.equal(result.cli, cli)
  assert.equal(result.browserStarted, false)
  assert.equal(result.modelCalls, false)
  assert.equal(result.validation, 'filesystem/config only')
  assert.throws(() => main([], { env: { ...env, SMOKE_ISOLATED: '0' }, spawn: noSpawn }), /SMOKE_ISOLATED=1/)
})

test('Only same-origin GET/HEAD and the exact login POST are allowed', () => {
  const base = config.baseUrl
  for (const method of ['GET', 'HEAD']) assert.equal(isAllowedRequest(base, base + '/api/kbs', method), true)
  assert.equal(isAllowedRequest(base, base + '/api/auth/login', 'POST'), true)
  for (const [url, method] of [
    [base + '/api/qa/ask', 'POST'], [base + '/api/qa/sessions', 'POST'],
    [base + '/api/kbs/1/docs', 'POST'], [base + '/api/sys/channels/1/health', 'POST'],
    [base + '/api/sys/channels/1', 'PATCH'], [base + '/api/kbs/1/docs/1', 'DELETE'],
    [base + '/api/auth/login?redirect=https://example.com', 'POST'],
    ['https://example.com/track', 'GET'], ['http://127.0.0.1:9999/api/auth/login', 'POST'],
    ['not-a-url', 'GET'],
  ]) assert.equal(isAllowedRequest(base, url, method), false, `${method} ${url}`)
})

test('Browser program uses owned Tabbit page and current login controls without model calls', async () => {
  const source = buildProgram(config)
  assert.match(source, /page\.route/)
  assert.match(source, /#login-username/)
  assert.match(source, /#login-password/)
  assert.match(source, /login-submit/)
  assert.match(source, /chat: 'skipped/)
  assert.match(source, /return finalScreenshot/)
  assert.doesNotMatch(source, /chromium\.launch|browser\.close|newContext|createRequire|_npx|npm-cache|Administrator/)
  assert.doesNotMatch(source, /\.fill\([^\n]*(?:question|材料不齐全)/)
  const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
  assert.doesNotThrow(() => new AsyncFunction('page', source))
  const login = await readFile(new URL('../src/views/Login.vue', import.meta.url), 'utf8')
  for (const id of ['login-username', 'login-password']) assert.ok(login.includes(`id="${id}"`))
})

test('Successful CLI execution sends UTF-8 stdin with no shell, then finishes once', () => {
  const calls = []
  const response = runSmoke(config, cli, (file, args, options) => {
    calls.push({ file, args, options })
    return success
  }, () => 12345)
  assert.equal(response.status, 'succeeded')
  assert.deepEqual(response.result.nextAction, { path: 'path-returned-by-cli.png' })
  assert.equal(calls.length, 2)
  assert.equal(calls[0].file, cli)
  const task = calls[0].args[2]
  assert.match(task, new RegExp('^' + config.task + '-[a-f0-9]{8}$'))
  assert.deepEqual(calls[0].args, ['nodejs', '--task', task, '--request-id', 'smoke-12345', '--timeout-ms', '180000'])
  assert.equal(calls[0].options.encoding, 'utf8')
  assert.equal(calls[0].options.shell, false)
  assert.equal(calls[0].options.windowsHide, true)
  assert.equal(calls[0].options.input, buildProgram(config))
  assert.deepEqual(calls[1].args, ['finish', '--task', task])
})

test('A definitive CLI failure is not success and the task is finished once', () => {
  const calls = []
  assert.throws(() => runSmoke(config, cli, (_file, args) => {
    calls.push(args)
    return calls.length === 1 ? { status: 70, stdout: JSON.stringify({ status: 'failed', result: {} }), stderr: '' } : success
  }), /failed/)
  assert.equal(calls.length, 2)
  assert.equal(calls[1][0], 'finish')
})

test('Queued, interrupted, timeout and malformed outcomes never trigger automatic replay/finish', () => {
  for (const result of [
    { status: 75, stdout: JSON.stringify({ status: 'queued', result: {} }) },
    { status: 75, stdout: JSON.stringify({ status: 'running', result: {} }) },
    { status: 70, stdout: JSON.stringify({ status: 'interrupted', result: {} }) },
    { status: null, stdout: '', error: new Error('ETIMEDOUT') },
    { status: 0, stdout: 'not-json' },
  ]) {
    let calls = 0
    assert.throws(() => runSmoke(config, cli, () => { calls++; return result }), /receipt/)
    assert.equal(calls, 1)
  }
})

test('Failed finish is reported instead of claiming success', () => {
  let calls = 0
  assert.throws(() => runSmoke(config, cli, () => {
    calls++
    return calls === 1 ? success : { status: 70, stdout: '{}', stderr: '' }
  }), /finish/)
  assert.equal(calls, 2)
})

test('Smoke --check works from a different cwd with no project Playwright dependency', () => {
  const script = fileURLToPath(new URL('../../scripts/smoke.mjs', import.meta.url))
  const result = spawnSync(process.execPath, [script, '--check'], {
    cwd: path.dirname(process.execPath), env: { ...process.env, ...env }, encoding: 'utf8', windowsHide: true,
  })
  assert.equal(result.status, 0, result.stderr)
  assert.equal(JSON.parse(result.stdout).browserStarted, false)
})

test('Existing Vite dev/preview proxy keeps 8001 default and honors isolated-port overrides', t => {
  const previous = process.env.VITE_API_PROXY
  t.after(() => { if (previous === undefined) delete process.env.VITE_API_PROXY; else process.env.VITE_API_PROXY = previous })
  process.env.VITE_API_PROXY = ''
  const defaults = viteConfig({ mode: 'development' })
  for (const settings of [defaults.server, defaults.preview]) {
    assert.equal(settings.proxy['/api'].target, 'http://127.0.0.1:8001')
    assert.equal(settings.host, '127.0.0.1')
    assert.equal(settings.strictPort, true)
  }
  process.env.VITE_API_PROXY = 'http://127.0.0.1:18087'
  for (const mode of ['development', 'production']) {
    const configured = viteConfig({ mode })
    assert.equal(configured.server.proxy['/api'].target, process.env.VITE_API_PROXY)
    assert.equal(configured.preview.proxy['/api'].target, process.env.VITE_API_PROXY)
  }
})

