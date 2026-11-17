import test from 'node:test'
import assert from 'node:assert/strict'
import { createQueryCache } from '../src/api/queryCache.js'

test('period reuse, expiry, and concurrent requests', async () => {
  let now = 0, calls = 0
  const cache = createQueryCache({ ttl: 300, clock: () => now })
  const load = () => { calls++; return { items: [calls] } }
  const a = cache.load('3', load), b = cache.load('3', load)
  assert.equal(a, b)
  await a
  await cache.load('6', load)
  assert.deepEqual(await cache.load('3', load), { items: [1] })
  assert.equal(calls, 2)
  now = 301
  assert.equal(cache.peek('3'), null)
  await cache.load('3', load)
  assert.equal(calls, 3)
})

test('failures retry, explicit invalidation refetches, and entries are bounded', async () => {
  const cache = createQueryCache({ maximum: 2 })
  await assert.rejects(cache.load('a', () => Promise.reject(new Error('offline'))))
  assert.equal(await cache.load('a', () => 1), 1)
  cache.remove('a')
  assert.equal(await cache.load('a', () => 2), 2)
  await cache.load('b', () => 3)
  await cache.load('c', () => 4)
  assert.equal(cache.peek('a'), null)
})

test('clearing an account cache aborts pending requests and ignores late success', async () => {
  const cache = createQueryCache()
  let resolve, signal
  const pending = cache.load('same', value => { signal = value; return new Promise(done => { resolve = done }) })
  await Promise.resolve()
  cache.clear()
  assert.equal(signal.aborted, true)
  assert.equal(await cache.load('same', () => 'new account'), 'new account')
  resolve('old account')
  await pending
  assert.equal(cache.peek('same').data, 'new account')
})

test('shared subscribers see successful retries and explicit invalidation, but not unrelated queries', async () => {
  const cache = createQueryCache(), events = []
  const unsubscribe = cache.subscribe(['shared'], reason => events.push(reason))
  await assert.rejects(cache.load('shared', () => Promise.reject(new Error('offline'))))
  assert.deepEqual(events, [])
  cache.remove('shared')
  await cache.load('shared', () => 1)
  await cache.load('shared', () => 2)
  await cache.load('other', () => 3)
  assert.deepEqual(events, ['invalidate', 'success'])
  unsubscribe()
  cache.remove('shared')
  assert.equal(events.length, 2)
})
