// 공통 알림 팝업(API-61·62). 표시 확인은 읽음 처리와 별개이므로 read_at을 바꾸지 않는다.
import { useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import { mapPopupGroup, notificationTarget, popupItemTag } from '../api/adapters'
import { notificationTypes } from '../data/seed'
import { navigate } from '../router'
import { useNotifications } from '../store'
import { Btn, Modal } from './ui'

const POLL_SECONDS = 30
const SHOWN_ITEMS = 5
const TONE_COLORS = { amber: 'var(--amber)', plum: 'var(--plum)', ok: 'var(--ok)' }

export default function NotificationPopups() {
  const { refreshNotifications } = useNotifications()
  const [current, setCurrent] = useState(null)
  const nextDelay = useRef(0) // 다음 조회까지 기다릴 시간(ms)
  const pendingAck = useRef(Promise.resolve())

  // 한 번에 한 묶음만 보여 준다. 전달 ID는 5분 뒤 만료되므로 다음 묶음은 닫은 뒤 새로 받는다.
  useEffect(() => {
    if (current) return undefined
    let active = true, timer
    const load = async () => {
      let wait = POLL_SECONDS
      if (document.visibilityState !== 'hidden') {
        try {
          await pendingAck.current // 앞 묶음의 표시 확인이 끝난 뒤 조회해야 같은 묶음이 다시 오지 않는다.
          const data = await api('/notifications/popups')
          if (!active) return
          wait = data.retry_after_seconds || POLL_SECONDS
          if (data.groups.length) { setCurrent(mapPopupGroup(data.groups[0])); return }
        } catch { /* 팝업 실패가 화면을 막지 않게 다음 주기에 다시 조회한다. */ }
      }
      if (active) timer = setTimeout(load, wait * 1000)
    }
    timer = setTimeout(load, nextDelay.current)
    nextDelay.current = 0
    return () => { active = false; clearTimeout(timer) }
  }, [current])

  // 실제로 표시한 묶음만 전달 완료로 기록한다. 실패하면 다음 조회에서 다시 전달된다.
  const deliveryId = current?.deliveryId
  useEffect(() => {
    if (!deliveryId) return
    // 확인이 실패하면 같은 묶음이 다시 오므로, 닫자마자 또 뜨지 않게 한 주기 기다린다.
    pendingAck.current = api(`/notifications/popups/${deliveryId}/ack`, { method: 'POST', body: {} }).catch(() => { nextDelay.current = POLL_SECONDS * 1000 })
    void refreshNotifications(true).catch(() => {})
  }, [deliveryId, refreshNotifications])

  if (!current) return null
  const close = () => setCurrent(null)
  const openInbox = () => {
    close()
    const single = current.items.length === 1 ? `&id=${current.items[0].id}` : ''
    navigate(`/account/notifications?tab=${current.type}${single}`)
  }
  // 항목을 직접 고른 것이므로 읽음으로 바꾸고 해당 기사·리포트를 연다. 연 화면을 바로 가리지 않게 다음 팝업은 한 주기 뒤에 보여 준다.
  const openItem = n => {
    nextDelay.current = POLL_SECONDS * 1000
    close()
    void api(`/notifications/${n.id}/read`, { method: 'PATCH', body: {} }).then(() => refreshNotifications(true)).catch(() => {})
    navigate(notificationTarget(n))
  }
  const t = notificationTypes[current.type]
  const rest = current.items.length - SHOWN_ITEMS
  return (
    <Modal onClose={close} width={480} label={`${t.label} 알림`}>
      <span className={'tag ' + t.tone}>{t.label}</span>
      <h2 style={{ fontSize: 22, fontWeight: 800, marginTop: 10 }}>새 알림 {current.items.length}건</h2>
      <ul className="col" style={{ gap: 10, margin: '16px 0', padding: 0, listStyle: 'none', maxHeight: 380, overflowY: 'auto' }}>
        {current.items.slice(0, SHOWN_ITEMS).map(n => {
          const tag = popupItemTag(n)
          return (
            <li key={n.id}>
              <button type="button" className="reset" onClick={() => openItem(n)} style={{ display: 'block', width: '100%', textAlign: 'left', cursor: 'pointer', border: '1px solid var(--line2)', borderLeft: `4px solid ${TONE_COLORS[tag?.tone] || 'var(--line2)'}`, borderRadius: 10, padding: '12px 14px' }}>
                {tag && <span className={'tag ' + tag.tone} style={{ marginBottom: 6 }}>{tag.label}</span>}
                <b style={{ display: 'block' }}>{n.title}</b>
                <span className="note" style={{ display: 'block', marginTop: 4 }}>{n.body}</span>
              </button>
            </li>
          )
        })}
      </ul>
      {rest > 0 && <p className="note">외 {rest}건은 알림함에서 확인할 수 있습니다.</p>}
      <div className="chips" style={{ marginTop: 18, justifyContent: 'flex-end' }}>
        <Btn onClick={close}>닫기</Btn>
        <Btn kind="red" onClick={openInbox}>알림함에서 보기</Btn>
      </div>
    </Modal>
  )
}
