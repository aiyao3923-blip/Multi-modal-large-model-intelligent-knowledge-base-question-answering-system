import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile, readdir } from 'node:fs/promises'
import { parse, compileScript, compileTemplate, compileStyle } from '@vue/compiler-sfc'
import { resolveCssTokens, chartDataRows } from '../src/utils/chartTheme.js'

const sourceRoot = new URL('../src/', import.meta.url)
async function filesIn(dir) {
  const entries = await readdir(dir, { withFileTypes: true })
  return (await Promise.all(entries.map(item => item.isDirectory() ? filesIn(new URL(item.name + '/', dir)) : [new URL(item.name, dir)]))).flat()
}
function luminance(hex) {
  const values = hex.replace('#', '').match(/../g).map(x => parseInt(x, 16) / 255).map(x => x <= .04045 ? x / 12.92 : ((x + .055) / 1.055) ** 2.4)
  return values[0] * .2126 + values[1] * .7152 + values[2] * .0722
}
function contrast(a, b) { const x = luminance(a), y = luminance(b); return (Math.max(x, y) + .05) / (Math.min(x, y) + .05) }

test('Paper workspace tokens use blue/slate roles and measured contrast', async () => {
  const css = await readFile(new URL('../src/styles.css', import.meta.url), 'utf8')
  const colors = Object.fromEntries([...css.matchAll(/--color-([a-z-]+):\s*(#[A-Fa-f0-9]{6})/g)].map(m => [m[1], m[2].toUpperCase()]))
  assert.equal(colors.page, '#F4F6F9'); assert.equal(colors.surface, '#FFFFFF'); assert.equal(colors.ink, '#172033'); assert.equal(colors.accent, '#365CF5')
  for (const [fg, bg] of [['ink','surface'],['secondary','page'],['muted','surface'],['muted','page'],['surface','accent'],['accent','page'],['success','success-soft'],['warning','warning-soft'],['danger','danger-soft'],['info','info-soft']]) {
    assert.ok(contrast(colors[fg], colors[bg]) >= 4.5, fg + '/' + bg + ' text contrast')
  }
  for (const fg of ['focus', 'control-border']) assert.ok(contrast(colors[fg], colors.surface) >= 3, fg + ' non-text contrast')
  assert.match(css, /prefers-reduced-motion: reduce/)
  assert.match(css, /--font-display:/)
  assert.match(css, /\.el-button--primary:not\(\.is-plain\):not\(\.is-text\):not\(\.is-link\)/, 'filled-primary text override must not wash out link/text buttons')
})

test('All app color-token references are defined, not canvas-incompatible leftovers', async () => {
  const css = await readFile(new URL('../src/styles.css', import.meta.url), 'utf8')
  const defined = new Set([...css.matchAll(/(--color-[a-z-]+)\s*:/g)].map(m => m[1]))
  for (const file of await filesIn(sourceRoot)) {
    if (!/\.(vue|css|js)$/.test(file.pathname)) continue
    const text = await readFile(file, 'utf8')
    assert.ok(!text.includes(String.fromCharCode(65533)), 'UTF-8 replacement character in ' + file.pathname)
    for (const match of text.matchAll(/var\((--color-[a-z-]+)\)/g)) assert.ok(defined.has(match[1]), file.pathname + ': ' + match[1])
    assert.doesNotMatch(text, /#(?:167c63|243632|596d51|82af79)\b/i, 'legacy theme in ' + file.pathname)
  }
})

test('Canvas token resolver is deep, non-mutating and preserves formatter functions', () => {
  const formatter = value => String(value)
  const original = { color: ['var(--color-accent)'], axis: { color: 'var(--missing, #123456)' }, formatter }
  const result = resolveCssTokens(original, name => name === '--color-accent' ? ' #AC3F28 ' : '')
  assert.deepEqual(result.color, ['#AC3F28']); assert.equal(result.axis.color, '#123456'); assert.equal(result.formatter, formatter)
  assert.equal(original.color[0], 'var(--color-accent)')
  assert.equal(resolveCssTokens(null, () => ''), null)
})

test('Chart data alternatives preserve zero, missing values and complete multi-series rows', () => {
  const rows = chartDataRows({ xAxis: { data: ['周一', '周二'] }, series: [{ name: '问答', data: [0, 3] }, { name: '引用', data: [null, 2] }] })
  assert.equal(rows.length, 4); assert.equal(rows[0].item, '周一'); assert.equal(rows[0].value, '0'); assert.equal(rows[2].value, '—'); assert.equal(rows[3].series, '引用')
  const pie = chartDataRows({ series: { name: '来源', data: [{ name: '资料甲', value: 0 }, { name: '资料乙', value: 4 }] } })
  assert.equal(pie[0].item, '资料甲'); assert.equal(pie[0].value, '0'); assert.equal(pie[1].value, '4')
  assert.deepEqual(chartDataRows(), [])
})

test('All actual Vue pages and shared components compile without template/style syntax errors', async () => {
  const files = (await filesIn(sourceRoot)).filter(file => file.pathname.endsWith('.vue'))
  assert.ok(files.length >= 20)
  for (const file of files) {
    const source = await readFile(file, 'utf8')
    const { descriptor, errors } = parse(source, { filename: file.pathname })
    assert.deepEqual(errors, [], file.pathname)
    const id = 'sfc-' + files.indexOf(file)
    const script = descriptor.scriptSetup || descriptor.script ? compileScript(descriptor, { id }) : null
    if (descriptor.template) {
      const template = compileTemplate({ source: descriptor.template.content, filename: file.pathname, id, compilerOptions: { bindingMetadata: script?.bindings } })
      assert.deepEqual(template.errors, [], file.pathname + ' template')
    }
    for (const style of descriptor.styles) {
      const result = compileStyle({ source: style.content, filename: file.pathname, id, scoped: style.scoped })
      assert.deepEqual(result.errors, [], file.pathname + ' style')
    }
  }
})

test('Shared shell retains role guards, a skip target and reversible mobile modal navigation', async () => {
  const shell = await readFile(new URL('../src/components/WorkspaceShell.vue', import.meta.url), 'utf8')
  assert.match(shell, /v-if="user\.isOperator"/); assert.match(shell, /v-if="user\.isAdmin"/)
  assert.match(shell, /id="main-content" tabindex="-1"/)
  assert.match(shell, /aria-modal/); assert.match(shell, /:inert/); assert.match(shell, /event\.key === 'Escape'/)
  assert.match(shell, /removeEventListener\('change', onCompactChange\)/)
})
