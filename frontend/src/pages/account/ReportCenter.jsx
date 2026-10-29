// BS16 채팅 생성 · BS17 주간 · BS18 월간 · BS31 분기 리포트 목록, BS20 구독 삭제 확인
import { useEffect, useState } from 'react'
import { navigate, withQuery } from '../../router'
import { useApp } from '../../store'
import { Btn, Chip, Modal, Select, TextField } from '../../components/ui'
import Icon from '../../components/Icon'
import { productName } from '../../data/catalog'
import { reportKinds } from '../../data/seed'


const TABS = ['weekly', 'monthly', 'chat', 'quarterly']
const CYCLE = { weekly: '주간', monthly: '월간', quarterly: '분기' }
const SORTS = [['new', '최신 생성일순'], ['old', '오래된 순'], ['title', '제목순']]

function ReportItem({ report }) {
  const { downloadReport } = useApp()
  const ready = report.status === 'ready'
  const generating = report.status === 'generating'
  return (
    <div className="li" style={{ alignItems: 'flex-start', padding: '20px 22px' }}>
      <span style={{ width: 46, height: 54, borderRadius: 8, background: ready ? 'var(--rose)' : '#F1F2F5', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flex: 'none' }}>
        <Icon name="file" size={22} color={ready ? '#C8102E' : '#8A919C'} stroke={1.8} />
      </span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <span className="tag plum">{reportKinds[report.kind].label}</span>
          <p style={{ fontSize: 17, fontWeight: 800 }}>{report.title}</p>
        </div>
        <p className="muted" style={{ marginTop: 6 }}>{report.meta}</p>
        {ready && report.note && <p style={{ marginTop: 8, fontSize: 13, fontWeight: 700, color: 'var(--plum)' }}>{report.note}</p>}
        {report.status === 'delayed' && (
          <>
            <p style={{ marginTop: 8, fontSize: 13, fontWeight: 700, color: 'var(--amber)' }}>
              <Icon name="alert" size={14} color="#B45309" stroke={2} style={{ verticalAlign: -2, marginRight: 4 }} />생성 지연 · 자동 재시도 후에도 완료되지 않았습니다.
            </p>
            <p className="note">다음 배치에서 다시 시도합니다. 완료 전에는 다운로드할 수 없습니다.</p>
          </>
        )}
        {generating && <p style={{ marginTop: 8, fontSize: 13, fontWeight: 700, color: 'var(--red-d)' }}>생성 중 · 완료되면 바로 열람할 수 있습니다.</p>}
      </div>
      {ready ? (
        <div style={{ display: 'flex', gap: 8 }}>
          <Btn kind="ink" to={`/report?id=${report.id}`}>열람</Btn>
          <Btn icon="download" disabled={!report.available_formats?.includes('PDF')} onClick={() => downloadReport(report.id, 'PDF')}>PDF</Btn>
          <Btn icon="download" disabled={!report.available_formats?.includes('DOCX')} onClick={() => downloadReport(report.id, 'DOCX')}>DOCX</Btn>
        </div>
      ) : <span className={'tag ' + (generating ? 'red' : 'amber')}>{generating ? '생성 중' : '다운로드 불가'}</span>}
    </div>
  )
}

export default function ReportCenter({ route }) {
  const { reports, subscription, deleteSubscription, profile, notify, refreshReports, fail } = useApp()
  const generating = reports.some(r => r.status === 'generating' || r.status === 'delayed')
  useEffect(() => {
    let stopped = false, timer
    const load = async () => { try { await refreshReports(); if (!stopped && generating) timer = setTimeout(load, 3000) } catch (e) { if (!stopped) fail(e) } }
    load(); return () => { stopped = true; clearTimeout(timer) }
  }, [refreshReports, generating, fail])
  const tab = TABS.includes(route.query.get('tab')) ? route.query.get('tab') : 'chat'
  const confirm = route.query.get('confirm') === 'delete'
  const [query, setQuery] = useState('')
  const [sort, setSort] = useState('new')
  const target = `${productName(profile.products[0])} · ${profile.countries[0] || '전체 국가'}`
  const list = reports
    .filter(r => r.kind === tab && r.title.includes(query.trim()))
    .sort((a, b) => (sort === 'title' ? a.title.localeCompare(b.title) : sort === 'old' ? a.created.localeCompare(b.created) : b.created.localeCompare(a.created)))
  const subscribed = tab !== 'chat' && subscription.cycles.includes(tab)
  const deleteCycle = tab === 'chat' ? null : tab

  const removeSubscription = async () => {
    if (!await deleteSubscription(deleteCycle)) return
    navigate(withQuery(route, { confirm: null }))
    notify(`${CYCLE[deleteCycle]} 구독을 삭제했습니다. 기존에 발행된 리포트는 계속 조회할 수 있습니다.`)
  }

  return (
    <>
      <section className="card">
        <div className="between">
          <h2 className="t1" style={{ fontSize: 24 }}>리포트 센터{tab === 'quarterly' ? ' / 분기리포트' : ''}</h2>
          <Btn kind="red" icon="plus" to="/analysis">새 리포트 만들기</Btn>
        </div>
        <div style={{ marginTop: 18 }}>
          <div className="chips">
            {TABS.map(t => <Chip key={t} sm on={tab === t} to={`/account/reports?tab=${t}`}>{reportKinds[t].tab}</Chip>)}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 10, marginTop: 16 }}>
          <TextField sm style={{ flex: 1 }} placeholder="리포트 제목 검색" icon="search" value={query} onChange={setQuery} aria-label="리포트 제목 검색" />
          <Select sm style={{ width: 180 }} value={sort} onChange={setSort} options={SORTS} aria-label="정렬" />
        </div>
        <p style={{ fontSize: 16, fontWeight: 800, marginTop: 22 }}>{reportKinds[tab].tab} · <span style={{ color: 'var(--red)' }}>{list.length}건</span></p>
        <div className="col" style={{ gap: 10, marginTop: 12 }}>
          {list.map(r => <ReportItem key={r.id} report={r} />)}
          {!list.length && (
            <div style={{ background: 'var(--bg)', borderRadius: 12, padding: '22px 18px', textAlign: 'center' }}>
              <p className="muted">{query ? '검색 결과가 없습니다.' : `조회할 ${reportKinds[tab].tab}가 없습니다.`}</p>
            </div>
          )}
        </div>
        {tab === 'chat' && <p className="note" style={{ marginTop: 14 }}>생성 완료 시 요청형 목록에 등록합니다. 조건·기준 기간·생성일을 보존합니다. 재열람·다운로드는 재계산 없이 최초 생성된 결과와 PDF를 제공합니다.</p>}
        {tab === 'quarterly' && (
          <>
            <div style={{ marginTop: 16, background: 'var(--bg)', borderRadius: 12, padding: '14px 16px' }}>
              <p className="note">발행된 분기 리포트가 없으면 “조회할 분기 리포트가 없습니다.”를 표시합니다.</p>
              <p className="note">리포트 열람 → 리포트 열람 화면 / PDF·DOCX → 생성 당시 저장 파일 다운로드</p>
            </div>
            <Btn icon="arrowLeft" to="/account/reports?tab=chat" style={{ marginTop: 16 }}>센터로 돌아가기</Btn>
          </>
        )}
      </section>

      {subscribed && (
        <section className="card">
          <div className="between">
            <div>
              <p style={{ fontSize: 13, fontWeight: 700, color: 'var(--red-d)' }}>구독 관리</p>
              <p style={{ fontSize: 18, fontWeight: 800, marginTop: 4 }}>{CYCLE[tab]} 구독 · {target}</p>
              <p className="muted" style={{ marginTop: 4 }}>구독을 수정하거나 삭제해도 이미 발행된 리포트는 유지됩니다. · 출력 양식 {subscription.formats.join('·')}</p>
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <Btn icon="edit" to="/account/subscription">수정</Btn>
              <Btn kind="danger" icon="trash" to={withQuery(route, { confirm: 'delete' })}>삭제</Btn>
            </div>
          </div>
        </section>
      )}

      {tab !== 'chat' && !subscription.cycles.length && (
        <section className="card" style={{ background: 'var(--plum)', border: 0, color: '#fff' }}>
          <div className="between">
            <div>
              <span className="tag" style={{ background: 'rgba(255,255,255,.14)', color: '#fff' }}>최초 진입 · 미구독 상태</span>
              <p style={{ fontSize: 20, fontWeight: 800, marginTop: 10 }}>아직 구독 중인 리포트가 없습니다.</p>
              <p style={{ marginTop: 4, color: '#D6D3E6' }}>구독 설정에서 발행 주기와 출력 양식을 선택하세요. 최초 발행 전에는 리포트 목록이 비어 있습니다.</p>
            </div>
            <Btn kind="red" size="md" iconRight="arrowRight" to="/account/subscription">구독 설정</Btn>
          </div>
        </section>
      )}

      <Modal open={confirm && !!deleteCycle && subscription.cycles.includes(deleteCycle)} onClose={() => navigate(withQuery(route, { confirm: null }))} width={520} label="구독 삭제 확인">
        <span style={{ width: 52, height: 52, borderRadius: 16, background: 'var(--rose)', display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}><Icon name="trash" size={24} color="#C8102E" /></span>
        <h2 style={{ fontSize: 22, fontWeight: 800, marginTop: 16 }}>구독을 삭제하고 다음 발행을 중단할까요?</h2>
        <div style={{ marginTop: 14, border: '1px solid var(--line)', borderRadius: 12, padding: '12px 16px', fontSize: 14 }}>
          <b>{CYCLE[deleteCycle]} 구독</b> · {target} · {subscription.formats.join('·')}
        </div>
        <p className="muted" style={{ marginTop: 14, fontSize: 15, lineHeight: 1.7 }}>삭제하면 이 구독의 향후 리포트 발행이 중단됩니다.<br />기존에 발행된 리포트는 목록에서 계속 조회할 수 있습니다.</p>
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, marginTop: 24 }}>
          <Btn size="md" onClick={() => navigate(withQuery(route, { confirm: null }))}>취소</Btn>
          <Btn kind="red" size="md" onClick={removeSubscription}>구독 삭제</Btn>
        </div>
      </Modal>
    </>
  )
}
