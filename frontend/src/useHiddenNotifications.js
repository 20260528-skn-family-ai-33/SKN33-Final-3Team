import { useCallback, useEffect, useState } from 'react'

const storageKey = owner => `bs-mini-alert-hidden:${owner}`
function readHidden(owner) {
  if (!owner) return []
  try {
    const ids = JSON.parse(sessionStorage.getItem(storageKey(owner)) || '[]')
    return Array.isArray(ids) ? ids.filter(id => typeof id === 'string') : []
  } catch { return [] }
}

// 미니 알림함 표시만 탭·계정별로 보관한다. 원본 알림과 읽음 상태는 수정하지 않는다.
export function useHiddenNotifications(owner) {
  const [state, setState] = useState(() => ({ owner, ids: readHidden(owner) }))
  const ids = state.owner === owner ? state.ids : readHidden(owner)
  if (state.owner !== owner) setState({ owner, ids })
  useEffect(() => {
    if (!owner) return
    try { sessionStorage.setItem(storageKey(owner), JSON.stringify(ids)) } catch { /* 저장 제한 시 현재 화면에서만 유지한다. */ }
  }, [owner, ids])
  const hide = useCallback(values => {
    if (!owner) return
    setState(previous => {
      const ids = [...new Set([...(previous.owner === owner ? previous.ids : readHidden(owner)), ...values.map(String)])]
      return { owner, ids }
    })
  }, [owner])
  return [ids, hide]
}
