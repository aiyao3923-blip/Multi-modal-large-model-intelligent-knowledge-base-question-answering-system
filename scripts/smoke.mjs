// 本地隔离实例冒烟：登录与管理页读取；不提交问题、不调用模型、不修改业务数据。
// node scripts/smoke.mjs --check 仅检查本机配置，不启动/连接浏览器。
import { spawnSync } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import { statSync } from 'node:fs'
import { homedir } from 'node:os'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

export function loadConfig(args = [], env = process.env) {
  for (const arg of args) {
    if (!['--check', '--help'].includes(arg)) throw new Error(`Unknown argument: ${arg}`)
  }
  let url
  try { url = new URL(env.SMOKE_BASE_URL || 'http://127.0.0.1:5173') }
  catch { throw new Error('SMOKE_BASE_URL must be a local HTTP(S) origin.') }
  if (!['http:', 'https:'].includes(url.protocol) || !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)
    || url.username || url.password || url.pathname !== '/' || url.search || url.hash) {
    throw new Error('SMOKE_BASE_URL must be a loopback HTTP(S) origin without credentials, path, query or fragment.')
  }
  const task = env.SMOKE_TASK || 'kbqa-local-smoke'
  if (!/^[a-zA-Z0-9][a-zA-Z0-9_-]{0,47}$/.test(task)) throw new Error('SMOKE_TASK must be a short path-safe task name.')
  return { baseUrl: url.origin, task, isolated: env.SMOKE_ISOLATED === '1', check: args.includes('--check'), help: args.includes('--help') }
}

export function resolveTabbitCli(env = process.env, { platform = process.platform, home = homedir(), isFile } = {}) {
  const paths = platform === 'win32' ? path.win32 : path.posix
  const cli = env.TABBIT_CLI || (platform === 'win32'
    ? env.LOCALAPPDATA && paths.join(env.LOCALAPPDATA, 'Tabbit', 'LocalAgent', 'bin', 'tabbit-cli.exe')
    : paths.join(home, '.local', 'bin', 'tabbit-cli'))
  if (!cli || !paths.isAbsolute(cli)) throw new Error('TABBIT_CLI must be an absolute path to the installed stable Tabbit launcher.')
  const exists = isFile || (file => { try { return statSync(file).isFile() } catch { return false } })
  if (!exists(cli)) throw new Error('Tabbit stable launcher not found. Install/configure Tabbit or set TABBIT_CLI; this script downloads nothing.')
  return cli
}

// Limit this smoke to same-origin reads and fixture login. In particular, model
// health probes, questions, uploads and business mutations are never allowed.
export function isAllowedRequest(baseUrl, requestUrl, method) {
  let url
  try { url = new URL(requestUrl) } catch { return false }
  if (url.origin !== baseUrl || url.username || url.password) return false
  if (method === 'GET' || method === 'HEAD') return true
  return method === 'POST' && url.pathname === '/api/auth/login' && !url.search && !url.hash
}

async function browserSmoke({ baseUrl }, { page }, allowRequest) {
  if (page.url() !== 'about:blank') throw new Error('A fresh smoke task must own a blank page.')
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  let ownsLogin = false
  try {
    await page.route('**/*', route => allowRequest(baseUrl, route.request().url(), route.request().method()) ? route.continue() : route.abort('blockedbyclient'))
    await page.goto(baseUrl + '/login', { waitUntil: 'domcontentloaded', timeout: 15000 })
    const health = await page.evaluate(async () => { const response = await fetch('/api/health'); return response.ok ? response.json() : null })
    if (!health || !String(health.app).includes('隔离验收')) throw new Error('The server is not the isolated paper-workspace fixture; no login was attempted.')
    await page.locator('#login-username').fill('reader_test')
    await page.locator('#login-password').fill('SyntheticOnly2026!')
    await Promise.all([
      page.waitForURL(url => url.origin === baseUrl && url.pathname === '/', { timeout: 15000 }),
      page.locator('.login-submit').click(),
    ])
    ownsLogin = true
    await page.getByRole('textbox', { name: '向当前知识库提问' }).waitFor({ state: 'visible' })
    const checks = [{ path: '/admin/kbs', api: '/api/kbs' }, { path: '/admin/tasks', api: '/api/tasks/page' }]
    for (const check of checks) {
      const response = page.waitForResponse(response => new URL(response.url()).pathname === check.api && response.request().method() === 'GET')
      await page.goto(baseUrl + check.path, { waitUntil: 'domcontentloaded' })
      if (!(await response).ok()) throw new Error('Read failed: ' + check.api)
    }
    if (errors.length) throw new Error('Page error count: ' + errors.length)
    const finalScreenshot = await page.screenshot({ fullPage: false })
    console.log(JSON.stringify({ login: 'passed', pages: checks.map(item => item.path), chat: 'skipped: no model call is submitted', modelCalls: false }))
    return finalScreenshot
  } finally {
    if (ownsLogin && !page.isClosed()) await page.getByRole('button', { name: '退出登录', exact: true }).click().catch(() => {})
    await page.close()
  }
}

export function buildProgram(config) {
  return `return await (${browserSmoke.toString()})(${JSON.stringify({ baseUrl: config.baseUrl })}, { page }, ${isAllowedRequest.toString()});`
}

export function runSmoke(config, cli, spawn = spawnSync, now = Date.now) {
  if (!config.isolated) throw new Error('Confirm an isolated local instance with SMOKE_ISOLATED=1 before running browser smoke.')
  const requestId = `smoke-${now()}`
  const task = `${config.task}-${randomUUID().slice(0, 8)}` // Fresh task: never reuse a user-owned blank tab.
  const options = { encoding: 'utf8', shell: false, windowsHide: true, timeout: 190000, maxBuffer: 1024 * 1024 }
  const execution = spawn(cli, ['nodejs', '--task', task, '--request-id', requestId, '--timeout-ms', '180000'], {
    ...options, input: buildProgram(config),
  })
  let receipt
  try { receipt = JSON.parse(execution.stdout) } catch { /* An uncertain result must not be replayed. */ }
  if (execution.error || !['succeeded', 'failed'].includes(receipt?.status)
    || (receipt.status === 'succeeded' && execution.status !== 0)
    || (receipt.status === 'failed' && execution.status !== 70)) {
    throw new Error(`Tabbit outcome is uncertain (${receipt?.status || execution.error?.code || 'no valid JSON'}). Do not rerun or finish blindly; inspect receipt for task=${task}, request-id=${requestId}.`)
  }
  // Finish exactly once only after a definitive outcome. This releases ownership;
  // it does not discard unrelated or previously retained user tabs.
  const finished = spawn(cli, ['finish', '--task', task], { ...options, timeout: 30000 })
  if (receipt.status === 'failed') {
    throw new Error(`Tabbit smoke failed; inspect receipt for task=${task}, request-id=${requestId}.${finished.error || finished.status !== 0 ? ' Task finish also failed.' : ''}`)
  }
  if (finished.error || finished.status !== 0) throw new Error(`Smoke returned successfully but task finish failed; inspect task=${task} before further browser actions.`)
  return receipt
}

export function main(args = process.argv.slice(2), { env = process.env, spawn = spawnSync, write = console.log, ...cliOptions } = {}) {
  const config = loadConfig(args, env)
  if (config.help) {
    write('Usage: node scripts/smoke.mjs [--check|--help]\nSMOKE_BASE_URL: loopback frontend origin (default http://127.0.0.1:5173)\nTABBIT_CLI: optional absolute stable launcher path\nSMOKE_TASK: task-name prefix (default kbqa-local-smoke; a fresh suffix is added)\nSMOKE_ISOLATED=1: required for actual browser/login smoke; use a disposable backend with external models disabled.\n--check: filesystem/config only; no browser or network. No model calls or business mutations are performed.')
    return
  }
  if (!config.check && !config.isolated) throw new Error('Confirm an isolated local instance with SMOKE_ISOLATED=1 before running browser smoke.')
  const cli = resolveTabbitCli(env, cliOptions)
  if (config.check) {
    write(JSON.stringify({ baseUrl: config.baseUrl, cli, taskPrefix: config.task, validation: 'filesystem/config only', browserStarted: false, modelCalls: false }, null, 2))
    return
  }
  write(JSON.stringify(runSmoke(config, cli, spawn), null, 2))
}

// Importing helpers for unit tests must never connect to the browser.
if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) {
  try { main() } catch (error) { console.error('FAILED:', error.message); process.exitCode = 1 }
}


