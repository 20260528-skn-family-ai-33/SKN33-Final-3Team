// 관리자 알림(API-80)의 읽음 상태와 종 숫자. 회원 알림함(FR-305)과 분리한다.
// TODO: API-80의 DB 보완이 정해지면 서버 저장으로 옮긴다. 지금은 알림 ID만 이 브라우저에 저장하므로 다른 PC·브라우저에는 이어지지 않는다.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../api/client'
import { allPages, useApp } from '../store'

const POLL_MS = 30000
const COUNT_LIMIT = 100
const CHANGED = 'admin-alert-read-changed'
const storageKey = userId => `admin-alert-read:${userId}`

function loadRead(userId) {
  try {
    const value = JSON.parse(localStorage.getItem(storageKey(userId)))
    return new Set(Array.isArray(value) ? value.map(String) : [])
  } catch { return new Set() }
}

function saveRead(userId, ids) {
  const next = loadRead(userId)
  ids.forEach(id => next.add(String(id)))
  try { localStorage.setItem(storageKey(userId), JSON.stringify([...next])) } catch { /* 저장을 막은 브라우저는 안 읽음으로 남는다. */ }
  window.dispatchEvent(new Event(CHANGED))
}

export function useAdminAlertRead() {
  const { session } = useApp()
  const userId = session?.id
  const [version, setVersion] = useState(0)
  useEffect(() => {
    const changed = () => setVersion(value => value + 1)
    window.addEventListener(CHANGED, changed)
    window.addEventListener('storage', changed) // 다른 탭에서 읽은 내용도 반영한다.
    return () => { window.removeEventListener(CHANGED, changed); window.removeEventListener('storage', changed) }
  }, [])
  // eslint-disable-next-line react-hooks/exhaustive-deps -- version이 바뀌면 저장값을 다시 읽는다.
  const read = useMemo(() => loadRead(userId), [userId, version])
  const isRead = useCallback(id => read.has(String(id)), [read])
  const markRead = useCallback(ids => saveRead(userId, ids), [userId])
  const markAllRead = useCallback(async category => {
    const items = await allPages('/admin/alerts', { category })
    saveRead(userId, items.map(item => item.alert_id))
  }, [userId])
  return { isRead, markRead, markAllRead }
}

/** 종 숫자: 최신 관리자 알림 100건 중 이 브라우저에서 아직 읽지 않은 건수. 30초마다 전체 이력을 읽지 않게 범위를 제한한다. */
export function useAdminAlertCount(enabled) {
  const { isRead } = useAdminAlertRead()
  const [ids, setIds] = useState([])
  useEffect(() => {
    if (!enabled) return undefined
    let active = true
    const load = () => {
      if (document.visibilityState === 'hidden') return
      api('/admin/alerts', { query: { page_size: COUNT_LIMIT } })
        .then(data => { if (active) setIds(data.items.map(item => item.alert_id)) })
        .catch(() => { /* 건수 조회 실패가 화면을 막지 않게 다음 주기에 다시 조회한다. */ })
    }
    load()
    const timer = setInterval(load, POLL_MS)
    return () => { active = false; clearInterval(timer) }
  }, [enabled])
  return enabled ? ids.filter(id => !isRead(id)).length : 0
}
