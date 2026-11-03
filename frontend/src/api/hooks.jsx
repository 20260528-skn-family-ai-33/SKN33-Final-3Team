/* eslint-disable react-refresh/only-export-components */
import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import { Btn } from '../components/ui'

export function usePendingAction() {
  const busy = useRef(false)
  const [pending, setPending] = useState(false)
  const runOnce = async action => {
    if (busy.current) return
    busy.current = true
    setPending(true)
    try {
      return await action()
    } finally {
      busy.current = false
      setPending(false)
    }
  }
  return [pending, runOnce]
}

export function useDebouncedValue(value, delay = 300) {
  const [settled, setSettled] = useState(value)
  useEffect(() => { const timer = setTimeout(() => setSettled(value), delay); return () => clearTimeout(timer) }, [value, delay])
  return settled
}

// keep: 다시 불러오는 동안 같은 경로의 이전 결과를 계속 보여 준다(목록이 비었다가 다시 차며 화면이 튀지 않게).
// 문자열을 넘기면 그 값(예: 페이지를 뺀 조회 조건)이 같을 때만 이전 결과를 쓴다. 탭·분류가 바뀌면 이전 행을 바로 비운다.
export function useQuery(path, query = {}, enabled = true, interval = 0, method = 'GET', keep = false) {
  const key = JSON.stringify(query)
  const [state, setState] = useState({ data: null, error: null, loading: true })
  const [revision, reload] = useState(0)
  const requestKey = `${method}|${path}|${key}|${revision}|${enabled}`
  useEffect(() => {
    const controller = new AbortController(); let timer
    if (!enabled) return () => controller.abort()
    const load = async () => {
      try {
        const data = await api(path, { method, [method === 'GET' ? 'query' : 'body']: JSON.parse(key), signal: controller.signal })
        if (!controller.signal.aborted) setState({ key: requestKey, group: keep, data, error: null, loading: false })
      } catch (error) {
        if (!controller.signal.aborted) setState({ key: requestKey, group: keep, data: null, error, loading: false })
      }
      if (interval && !controller.signal.aborted) timer = setTimeout(load, interval)
    }
    load()
    return () => { controller.abort(); clearTimeout(timer) }
  }, [path, key, enabled, revision, interval, requestKey, method, keep])
  const reloadQuery = useCallback(() => reload(n => n + 1), [])
  const previous = keep && state.data && state.key?.startsWith(`${method}|${path}|`) && (keep === true || state.group === keep) ? state.data : null
  return { ...(enabled ? state.key === requestKey ? state : { data: previous, error: null, loading: true } : { data: null, error: null, loading: false }), reload: reloadQuery }
}
export function ErrorNotice({ error, retry }) {
  if (!error) return null
  return <div role="alert" className="field-err"><p>{error.message}</p>
    {error.status === 429 && <p>{error.retryAfter || 60}초 후 다시 시도해 주세요.</p>}
    {retry && error.status !== 429 && <Btn onClick={retry}>다시 불러오기</Btn>}</div>
}
export function QueryState({ state, children }) {
  if (state.loading) return <p role="status" className="muted">불러오는 중…</p>
  if (state.error) return <ErrorNotice error={state.error} retry={state.reload} />
  return children(state.data)
}
export function Value({ value }) {
  if (value == null) return '—'
  if (typeof value === 'boolean') return value ? '예' : '아니오'
  if (typeof value === 'number') return value.toLocaleString('ko-KR', { maximumFractionDigits: 2 })
  if (Array.isArray(value)) return value.map(v => typeof v === 'object' ? v.name_ko || v.hs_code || v.label || '' : v).join(', ') || '—'
  if (typeof value === 'object') return value.name_ko || value.hs_code || value.label || '—'
  return String(value)
}
export function Pagination({ page, info, onChange }) {
  return <div className="between"><Btn disabled={page <= 1} onClick={() => onChange(page - 1)}>이전</Btn>
    <span>{page} 페이지 {info?.total != null ? `· 총 ${info.total}건` : ''}</span>
    <Btn disabled={!info || page * info.page_size >= (info.total ?? info.total_count ?? 0)} onClick={() => onChange(page + 1)}>다음</Btn></div>
}
