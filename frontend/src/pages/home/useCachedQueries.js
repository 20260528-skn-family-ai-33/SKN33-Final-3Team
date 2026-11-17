import { useCallback, useEffect, useState } from 'react'
import { api } from '../../api/client'
import { mapLimited } from '../../api/integration'

// Share completed and in-flight requests across dashboard sections. A period switch
// keeps the pending fetch reusable; only its obsolete subscriber is cancelled.
export default function useCachedQueries(cache, path, queries) {
  const key = JSON.stringify(queries)
  const [revision, setRevision] = useState(0)
  const [state, setState] = useState({})
  useEffect(() => {
    const keys = JSON.parse(key).map(query => `${path}|${JSON.stringify(query)}`)
    return cache.subscribe(keys, reason => {
      // A retry in the table also resets the card's error and expiry timer.
      // Partial batch successes must not automatically retry failed graph requests.
      if (reason === 'invalidate' || keys.every(queryKey => cache.peek(queryKey))) setRevision(n => n + 1)
    })
  }, [cache, path, key])
  useEffect(() => {
    const controller = new AbortController()
    const selected = JSON.parse(key)
    let timer
    mapLimited(selected, 4, query => cache.load(`${path}|${JSON.stringify(query)}`,
      signal => api(path, { query, signal })), controller.signal)
      .then(() => {
        if (controller.signal.aborted) return
        setState({ cache, path, key, revision, error: null })
        const entries = selected.map(query => cache.peek(`${path}|${JSON.stringify(query)}`))
        if (entries.length && entries.every(Boolean)) {
          timer = setTimeout(() => setRevision(n => n + 1), Math.min(...entries.map(cache.remaining)) + 1)
        }
      })
      .catch(error => {
        if (!controller.signal.aborted) setState({ cache, path, key, revision, error })
      })
    return () => { controller.abort(); clearTimeout(timer) }
  }, [cache, path, key, revision])
  const reload = useCallback(() => {
    for (const query of JSON.parse(key)) cache.remove(`${path}|${JSON.stringify(query)}`)
    setRevision(n => n + 1)
  }, [cache, path, key])
  const entries = queries.map(query => cache.peek(`${path}|${JSON.stringify(query)}`))
  const ready = entries.every(Boolean)
  const error = !ready && state.cache === cache && state.path === path && state.key === key && state.revision === revision ? state.error : null
  return { data: ready ? entries.map(entry => entry.data) : [], loading: !ready && !error, error, reload }
}
