// Dashboard-owned memory only: no persisted responses or shared account state.
export const DASHBOARD_CACHE_TTL = 5 * 60 * 1000
export function createQueryCache({ ttl = DASHBOARD_CACHE_TTL, maximum = 64, clock = Date.now } = {}) {
  const entries = new Map()
  const subscribers = new Set()
  const notify = (key, reason) => {
    for (const subscription of subscribers) {
      if (subscription.keys.has(key)) subscription.listener(reason)
    }
  }
  const peek = key => {
    const entry = entries.get(key)
    return entry?.data !== undefined && entry.expires > clock() ? entry : null
  }
  const remove = (key, announce = true, reason = 'invalidate') => {
    entries.get(key)?.controller?.abort()
    entries.delete(key)
    if (announce) notify(key, reason)
  }
  return {
    peek,
    remaining: entry => Math.max(0, entry.expires - clock()),
    remove,
    // Explicit retries notify every subscriber, including a shared table/card.
    retry: key => remove(key, true, 'retry'),
    clear: () => { for (const key of entries.keys()) remove(key, false) },
    subscribe(keys, listener) {
      const subscription = { keys: new Set(keys), listener }
      subscribers.add(subscription)
      return () => subscribers.delete(subscription)
    },
    load(key, loader) {
      const cached = peek(key)
      if (cached) return Promise.resolve(cached.data)
      const pending = entries.get(key)
      if (pending?.promise) return pending.promise
      const controller = new AbortController()
      const entry = { controller }
      entries.set(key, entry)
      while (entries.size > maximum) remove(entries.keys().next().value)
      entry.promise = Promise.resolve().then(() => loader(controller.signal)).then(data => {
        if (entries.get(key) === entry && !controller.signal.aborted) {
          entries.set(key, { data, expires: clock() + ttl })
          notify(key, 'success')
        }
        return data
      }, error => {
        if (entries.get(key) === entry) entries.delete(key)
        throw error
      })
      return entry.promise
    },
  }
}
