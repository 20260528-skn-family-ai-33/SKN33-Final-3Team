import test from 'node:test'
import assert from 'node:assert/strict'
import { api, ApiError, queryString, setCsrf, streamAnswer } from '../src/api/client.js'

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

test('only version conflicts tell the user to reload and review before saving', () => {
  const response = new Response(null, { status: 409 })
  const conflict = new ApiError(response, { code: 'VERSION_CONFLICT', message: '기사 정보가 변경되었습니다.' })
  assert.equal(conflict.message, '기사 정보가 변경되었습니다. 새로고침한 뒤 최신 내용을 확인하고 다시 저장해 주세요.')
  const prerequisite = new ApiError(response, { code: 'INTERESTS_REQUIRED', message: '관심 등록이 필요합니다.' })
  assert.equal(prerequisite.message, '관심 등록이 필요합니다.')
})

test('an unavailable upstream with a non-JSON gateway response has a readable retry message', async () => {
  const old = globalThis.fetch
  try {
    globalThis.fetch = async () => new Response('Bad Gateway', { status: 502 })
    await assert.rejects(api('/auth/login'), e => e instanceof ApiError && e.status === 502 &&
      e.message === '서비스에 일시적으로 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.')
  } finally { globalThis.fetch = old }
})


test('final answer SSE decodes fragmented Korean and requires done event', async()=>{
 const old=globalThis.fetch
 try{
  const bytes=new TextEncoder().encode('event: delta\ndata: {"text":"검증 완료"}\n\nevent: done\ndata: {}\n\n')
  globalThis.fetch=async()=>new Response(new ReadableStream({start(c){for(const byte of bytes)c.enqueue(Uint8Array.of(byte));c.close()}}))
  const chunks=[];assert.equal(await streamAnswer('1',text=>chunks.push(text)),'검증 완료');assert.deepEqual(chunks,['검증 완료'])
  globalThis.fetch=async()=>new Response('event: delta\ndata: {"text":"일부"}\n\n')
  await assert.rejects(streamAnswer('1',()=>{}),/중단/)
 }finally{globalThis.fetch=old}
})
