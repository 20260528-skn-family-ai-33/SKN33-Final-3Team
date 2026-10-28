import { useCallback, useEffect, useState } from 'react'
import { api } from '../../api/client'
export default function useMarketSeries(queries) {
  const key = JSON.stringify(queries)
  const [state, setState] = useState({}), [revision, setRevision] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    Promise.all(JSON.parse(key).map(query => api('/trade/trends', { query, signal: controller.signal })))
      .then(data => { if (!controller.signal.aborted) setState({ key, revision, data, loading: false, error: null }) })
      .catch(error => { if (!controller.signal.aborted) setState({ key, revision, data: [], loading: false, error }) })
    return () => controller.abort()
  }, [key, revision])
  const reload = useCallback(() => setRevision(n => n + 1), [])
  return { ...(state.key === key && state.revision === revision ? state : { data: [], loading: true, error: null }), reload }
}
