// BS27 알림함 전체 · BS28 리포트 발행 · BS29 주요이슈 · BS30 급증 기사
import { useEffect } from 'react'
import { navigate, withQuery } from '../../router'
import { useApp } from '../../store'
import { Btn, Check, Chip } from '../../components/ui'
import { notificationTypes } from '../../data/seed'


const TABS = [['all', '전체'], ['surge', '급증 기사'], ['major', '주요 이슈'], ['report', '리포트 발행']]

function Detail({ n }) {
  const t = notificationTypes[n.type]
  const articleLink = n.articleId ? `/articles?id=${n.articleId}` : '/articles'
  return (
    <section className="card" style={{ padding: '28px 30px' }} aria-live="polite">
      <p style={{ fontSize: 13, fontWeight: 800, color: 'var(--sub)' }}>알림 상세</p>
      <span className={'tag ' + t.tone} style={{ marginTop: 12 }}>{n.type === 'report' ? '리포트 발행 · 구독형' : t.label}</span>
      <h3 style={{ fontSize: 26, fontWeight: 800, marginTop: 10 }}>{n.title}</h3>
      <p className="note">수신 {n.received}</p>
      <div style={{ height: 1, background: 'var(--line)', margin: '20px 0' }} />
      <p style={{ fontSize: 17, fontWeight: 700 }}>{n.body}</p>
      {n.type === 'report' && (
        <>
          <div style={{ marginTop: 14, display: 'grid', gridTemplateColumns: '100px 1fr', gap: 8, fontSize: 15 }}>
            <span className="s">국가 · 품목</span><b>{n.target}</b>
            <span className="s">대상 기간</span><b>{n.period}</b>
            <span className="s">유형</span><b>{n.cycle}</b>
          </div>
          <div style={{ marginTop: 24 }}><Btn kind="red" size="md" icon="file" to={`/report?id=${n.reportId}`}>구독 리포트 열기</Btn></div>
        </>
      )}
      {n.type === 'surge' && (
        <div style={{ display: 'flex', gap: 12, marginTop: 16 }}>
          <div style={{ flex: 1, background: 'var(--amber-bg)', borderRadius: 14, padding: '16px 18px' }}>
            <p className="note">급증 배수</p>
            <p style={{ fontSize: 30, fontWeight: 800, color: 'var(--amber)' }}>{n.ratio}</p>
          </div>
          <div style={{ flex: 2, background: 'var(--bg)', borderRadius: 14, padding: '16px 18px' }}>
            <p className="note">산식</p>
            <p style={{ fontSize: 15, fontWeight: 700, marginTop: 4 }}>{n.formula}</p>
            <p className="note">{n.basis}</p>
          </div>
        </div>
      )}
      {n.type === 'major' && (
        <p className="muted" style={{ marginTop: 8, fontSize: 15, lineHeight: 1.8 }}>
          원문의 발행일·출처와 기사 요약을 함께 확인합니다.<br />
          전체 사용자 대상 주요 이슈로 전달되었습니다.
        </p>
      )}
      {n.type !== 'report' && <div style={{ marginTop: 24 }}><Btn kind="ink" size="md" iconRight="external" to={articleLink}>기사 원문 보기</Btn></div>}
      <div style={{ marginTop: 28, background: 'var(--bg)', borderRadius: 12, padding: '14px 16px' }}>
        {n.type === 'report' && <p className="note">리포트 발행 알림에는 주간·월간·분기 구독형만 등록합니다.</p>}
        <p className="note">상세 확인 시 읽음 처리하며 미확인 건수를 갱신합니다.</p>
        {n.type !== 'report' && <p className="note">유형별 탭을 선택하면 해당 알림 목록만 표시합니다.</p>}
      </div>
    </section>
  )
}

export default function Inbox({ route }) {
  const { notifications, setNotifications } = useApp()
  const tab = TABS.some(([key]) => key === route.query.get('tab')) ? route.query.get('tab') : 'all'
  const unreadOnly = route.query.get('unread') === '1'
  const list = notifications.filter(n => (tab === 'all' || n.type === tab) && (!unreadOnly || !n.read || n.id === route.query.get('id')))
  const selected = list.find(n => n.id === route.query.get('id')) || list[0]
  const tabLabel = TABS.find(([k]) => k === tab)?.[1]

  useEffect(() => {
    if (selected && !selected.read && (route.query.get('id') || !unreadOnly)) setNotifications(ns => ns.map(n => (n.id === selected.id ? { ...n, read: true } : n)))
  }, [selected, setNotifications, route, unreadOnly])

  return (
    <>
      <section className="card" style={{ padding: '20px 24px' }}>
        <div className="between" style={{ flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
            <h2 className="t2" style={{ fontSize: 22 }}>알림함</h2>
            <div className="chips">
              {TABS.map(([key, label]) => <Chip key={key} sm on={tab === key} to={withQuery(route, { tab: key === 'all' ? null : key, id: null })}>{label}</Chip>)}
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
            <Check checked={unreadOnly} onChange={on => navigate(withQuery(route, { unread: on ? '1' : null }), { replace: true })} className="muted" style={{ fontSize: 13 }}>미확인만</Check>
            <button type="button" className="reset more" style={{ fontSize: 13 }} onClick={() => setNotifications(ns => ns.map(n => ({ ...n, read: true })))}>모두 읽음</button>
          </div>
        </div>
        <p className="muted" style={{ marginTop: 8 }}>선택한 유형: <b style={{ color: 'var(--red-d)' }}>{tabLabel}</b></p>
      </section>
      <div style={{ display: 'grid', gridTemplateColumns: '380px 1fr', gap: 20, alignItems: 'stretch' }} className="resp-stack">
        <section className="card col" style={{ gap: 10, padding: 18, minHeight: 560 }}>
          {list.map(n => {
            const t = notificationTypes[n.type]
            const on = n.id === selected?.id
            return (
              <button key={n.id} type="button" className="reset" aria-current={on} onClick={() => navigate(withQuery(route, { id: n.id }), { replace: true })}
                style={{ padding: '16px 18px', borderRadius: 14, background: on ? 'var(--rose)' : '#fff', boxShadow: on ? 'inset 3px 0 0 var(--red)' : 'none', border: on ? 0 : '1px solid var(--line)' }}>
                <div className="between"><span className={'tag ' + t.tone}>{t.label}</span><span className="note">{n.time}</span></div>
                <p style={{ fontSize: 15, fontWeight: 800, marginTop: 8 }}>{!n.read && <span className="dot" style={{ marginRight: 6, verticalAlign: 2 }} />}{n.title}</p>
              </button>
            )
          })}
          {!list.length && <p className="muted" style={{ padding: 8 }}>{unreadOnly ? '미확인 알림이 없습니다.' : '이 유형의 알림이 없습니다.'}</p>}
          <div style={{ flex: 1 }} />
          <p className="note">유형과 미확인 조건을 함께 적용합니다.</p>
        </section>
        {selected ? <Detail n={selected} /> : (
          <section className="card" style={{ padding: '28px 30px' }}>
            <p className="muted">선택한 알림이 없습니다.</p>
          </section>
        )}
      </div>
    </>
  )
}
