import test from 'node:test'
import assert from 'node:assert/strict'
import { api, ApiError, queryString, setCsrf } from '../src/api/client.js'

test('query arrays repeat and IDs remain strings', () => {
  assert.equal(queryString({ country_codes: ['US', 'CN'], id: '18446744073709551615', empty: '', zero: 0, flag: false }), '?country_codes=US&country_codes=CN&id=18446744073709551615&zero=0&flag=false')
})
test('mutation includes cookie, CSRF and stable idempotency header; 204 is accepted', async () => {
  const old = globalThis.fetch
  try {
    setCsrf('csrf-value')
    globalThis.fetch = async (url, options) => {
      assert.equal(url, '/api/v1/reports')
      assert.equal(options.credentials, 'include')
      assert.equal(options.headers['X-CSRF-Token'], 'csrf-value')
      assert.equal(options.headers['Idempotency-Key'], 'retry-key')
      assert.equal(options.body, '{"preview_id":"preview_1"}')
      return new Response(null, { status: 204 })
    }
    assert.equal(await api('/reports', { method: 'POST', body: { preview_id: 'preview_1' }, idempotencyKey: 'retry-key' }), null)
  } finally { globalThis.fetch = old; setCsrf(null) }
})
test('server 409/429/503 are preserved, never replaced by empty results', async () => {
  const old = globalThis.fetch
  try {
    for (const [status, code] of [[409, 'VERSION_CONFLICT'], [429, 'RATE_LIMITED'], [503, 'DATA_NOT_READY']]) {
      globalThis.fetch = async () => new Response(JSON.stringify({ code, message: '준비 중', request_id: 'trace' }), { status, headers: { 'Retry-After': '60' } })
      await assert.rejects(api('/articles'), e => e instanceof ApiError && e.code === code && e.status === status && e.requestId === 'trace' && e.retryAfter === 60)
    }
  } finally { globalThis.fetch = old }
})
test('download keeps binary payload and aborted requests stay aborted', async () => {
  const old = globalThis.fetch
  try {
    globalThis.fetch = async () => new Response('%PDF-local', { headers: { 'Content-Type': 'application/pdf' } })
    assert.equal(await (await api('/reports/1/files/pdf', { blob: true })).text(), '%PDF-local')
    globalThis.fetch = async () => { throw new DOMException('cancelled', 'AbortError') }
    await assert.rejects(api('/articles'), { name: 'AbortError' })
  } finally { globalThis.fetch = old }
})
