// 머리글 종 아이콘을 누르면 뜨는 최근 알림 팝오버(BS13)
import { useEffect } from 'react'
import { navigate } from '../router'
import { useApp, useNotifications } from '../store'
import { notificationTypes } from '../data/seed'
import { Btn } from './ui'

export default function AlertPopover({ onClose }) {
  const { run } = useApp()
  const { notifications, setNotifications, unread, refreshNotifications, hiddenNotificationIds, hideNotifications } = useNotifications()
  const hidden = new Set(hiddenNotificationIds)
  const visible = notifications.filter(n => !hidden.has(String(n.id)))
  useEffect(() => { void run(refreshNotifications) }, [refreshNotifications, run])
  const open = n => {
    setNotifications(list => list.map(x => (x.id === n.id ? { ...x, read: true } : x)))
    onClose()
    navigate(`/account/notifications?id=${n.id}`)
  }
  return (
    <>
      <div className="pop-dismiss" onClick={onClose} />
      <div className="alert-pop" role="dialog" aria-label="최근 알림">
        <div className="between" style={{ padding: '18px 20px', gap: 10, flexWrap: 'wrap', borderBottom: '1px solid var(--line)' }}>
          <span style={{ fontSize: 18, fontWeight: 800 }}>
            알림 {unread > 0 && <span className="tag red" style={{ marginLeft: 6 }}>미확인 {unread}건</span>}
          </span>
          <div style={{ display: 'flex', gap: 12 }}>
            <button type="button" className="reset muted" onClick={() => setNotifications(list => list.map(x => ({ ...x, read: true })))}>모두 읽음</button>
            <button type="button" className="reset muted" disabled={!visible.length} title="미니 알림함에서만 모두 삭제" onClick={() => hideNotifications(visible.map(n => n.id))}>모두 삭제</button>
          </div>
        </div>
        {visible.slice(0, 3).map(n => {
          const t = notificationTypes[n.type]
          return (
            <div key={n.id} className="alert-pop-row">
              <button type="button" className="item" onClick={() => open(n)}>
                <span className="dot" style={{ marginTop: 7, visibility: n.read ? 'hidden' : 'visible' }} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="between"><span className={'tag ' + t.tone}>{t.label}</span><span className="note">{n.time}</span></div>
                  <p style={{ marginTop: 6, fontSize: 15, fontWeight: 800 }}>{n.title}</p>
                  <p className="note">선택하면 알림함의 우측 상세에서 확인</p>
                </div>
              </button>
              <button type="button" className="reset alert-pop-remove" aria-label={`${n.title} 미니 알림 삭제`} title="미니 알림함에서만 삭제" onClick={() => hideNotifications([n.id])}>×</button>
            </div>
          )
        })}
        {!visible.length && <p className="muted" style={{ padding: '22px 20px' }}>새 알림이 없습니다.</p>}
        <div style={{ padding: '14px 20px', background: 'var(--bg)' }}>
          <Btn block onClick={() => { onClose(); navigate('/account/notifications') }}>알림함 전체 보기</Btn>
        </div>
      </div>
    </>
  )
}
