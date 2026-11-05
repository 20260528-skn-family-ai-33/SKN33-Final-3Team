// 공통 알림 팝업(API-61·62). 표시 확인은 읽음 처리와 별개이므로 read_at을 바꾸지 않는다.
import { useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import { mapPopupGroup } from '../api/adapters'
import { notificationTypes } from '../data/seed'
import { navigate } from '../router'
import { useNotifications } from '../store'
import { Btn, Modal } from './ui'

const POLL_SECONDS = 30
const SHOWN_ITEMS = 3

export default function NotificationPopups() {
  const { refreshNotifications } = useNotifications()
  const [groups, setGroups] = useState([])
  const acknowledged = useRef(new Set())
  const current = groups[0]

  // 보여 줄 묶음이 남아 있는 동안에는 새 전달 묶음을 만들지 않는다.
  useEffect(() => {
    if (current) return undefined
    let active = true, timer
    const load = async () => {
      let wait = POLL_SECONDS
      if (document.visibilityState !== 'hidden') {
        try {
          const data = await api('/notifications/popups')
          if (!active) return
          wait = data.retry_after_seconds || POLL_SECONDS
          if (data.groups.length) { setGroups(data.groups.map(mapPopupGroup)); return }
        } catch { /* 팝업 실패가 화면을 막지 않게 다음 주기에 다시 조회한다. */ }
      }
      if (active) timer = setTimeout(load, wait * 1000)
    }
    void load()
    return () => { active = false; clearTimeout(timer) }
  }, [current])

  // 실제로 표시한 묶음만 전달 완료로 기록한다. 실패하면 다음 조회에서 다시 전달된다.
  useEffect(() => {
    if (!current || acknowledged.current.has(current.deliveryId)) return
    acknowledged.current.add(current.deliveryId)
    void api(`/notifications/popups/${current.deliveryId}/ack`, { method: 'POST', body: {} }).catch(() => {})
    void refreshNotifications(true).catch(() => {})
  }, [current, refreshNotifications])

  if (!current) return null
  const close = () => setGroups(list => list.slice(1))
  const openInbox = () => {
    close()
    const single = current.items.length === 1 ? `&id=${current.items[0].id}` : ''
    navigate(`/account/notifications?tab=${current.type}${single}`)
  }
  const t = notificationTypes[current.type]
  const rest = current.items.length - SHOWN_ITEMS
  return (
    <Modal onClose={close} width={480} label={`${t.label} 알림`}>
      <span className={'tag ' + t.tone}>{t.label}</span>
      <h2 style={{ fontSize: 22, fontWeight: 800, marginTop: 10 }}>새 알림 {current.items.length}건</h2>
      <ul className="col" style={{ gap: 12, margin: '16px 0', padding: 0, listStyle: 'none' }}>
        {current.items.slice(0, SHOWN_ITEMS).map(n => (
          <li key={n.id}><b>{n.title}</b><p className="note" style={{ marginTop: 4 }}>{n.body}</p></li>
        ))}
      </ul>
      {rest > 0 && <p className="note">외 {rest}건은 알림함에서 확인할 수 있습니다.</p>}
      <div className="chips" style={{ marginTop: 18, justifyContent: 'flex-end' }}>
        <Btn onClick={close}>닫기</Btn>
        <Btn kind="red" onClick={openInbox}>알림함에서 보기</Btn>
      </div>
    </Modal>
  )
}
