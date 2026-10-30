import test from 'node:test'
import assert from 'node:assert/strict'
import { webcrypto } from 'node:crypto'
import { requestId } from '../src/api/requestId.js'

test('request IDs use native UUID when available', () => {
  assert.equal(requestId({ randomUUID: () => 'native-id' }), 'native-id')
})

test('HTTP fallback produces independent RFC 4122 v4 IDs', () => {
  const insecureCrypto = { getRandomValues: array => webcrypto.getRandomValues(array) }
  const ids = Array.from({ length: 1000 }, () => requestId(insecureCrypto))
  assert.equal(new Set(ids).size, ids.length)
  for (const id of ids) assert.match(id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
})

test('absence of secure random data fails explicitly instead of using weak IDs', () => {
  assert.throws(() => requestId({}), /안전한 요청 ID/)
})
