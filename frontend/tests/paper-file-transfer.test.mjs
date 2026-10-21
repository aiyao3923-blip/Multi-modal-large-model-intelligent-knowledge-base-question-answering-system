import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

async function client(get) {
  const source = (await readFile(new URL('../src/api/index.js', import.meta.url), 'utf8')).replace(/^import .*$/gm, '').replace(/export const /g, 'const ')
  return new Function('http', source + '\nreturn { documentFileApi };')({ get })
}

test('PDF binary signature is verified before granting preview MIME', async () => {
  const calls = [], api = await client(async (...args) => { calls.push(args); return new Blob(['%PDF-1.7\nsynthetic'], { type: 'application/octet-stream' }) })
  const pdf = await api.documentFileApi(3, 9)
  assert.equal(pdf.type, 'application/pdf'); assert.equal(await pdf.text(), '%PDF-1.7\nsynthetic')
  assert.equal(calls.length, 1); assert.equal(calls[0][0], '/kbs/3/docs/9/file')
  assert.deepEqual(calls[0][1].params, { download: true })
})

test('Empty successful binary uses same scoped JSON transfer without changing document identity', async () => {
  const calls = [], content = '%PDF-1.7\nfixture'
  const api = await client(async (...args) => { calls.push(args); return calls.length === 1 ? new Blob([]) : { size: content.length, content_base64: btoa(content), mime_type: 'application/pdf' } })
  const blob = await api.documentFileApi(3, 9)
  assert.equal(await blob.text(), content); assert.equal(blob.type, 'application/pdf')
  assert.deepEqual(calls.map(c => c[0]), ['/kbs/3/docs/9/file', '/kbs/3/docs/9/file'])
  assert.deepEqual(calls[1][1].params, { encoding: 'base64' })
})

test('Transfer fallback never turns HTML into PDF from an untrusted MIME label', async () => {
  let count = 0; const content = '<script>alert(1)</script>'
  const api = await client(async () => ++count === 1 ? new Blob([]) : { size: content.length, content_base64: btoa(content), mime_type: 'application/pdf' })
  assert.equal((await api.documentFileApi(1, 2)).type, 'application/octet-stream')
})

test('Invalid, oversized or incomplete JSON transfer is rejected, not downloaded', async () => {
  for (const data of [{ size: 0, content_base64: '' }, { size: 52_428_801, content_base64: '' }, { size: 99, content_base64: btoa('short') }, { size: 5, content_base64: '%%%' }]) {
    let count = 0; const api = await client(async () => ++count === 1 ? new Blob([]) : data)
    await assert.rejects(api.documentFileApi(1, 2))
  }
})

test('An authorization error is propagated without a second transfer attempt', async () => {
  let calls = 0; const denied = Object.assign(new Error('denied'), { response: { status: 403 } })
  const api = await client(async () => { calls++; throw denied })
  await assert.rejects(api.documentFileApi(1, 2), error => error === denied); assert.equal(calls, 1)
})