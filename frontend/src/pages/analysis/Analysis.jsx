// BS14 AI분석(대화·리포트 조건·생성 진행·일반 답변·표 보기) · BS15 리포트 생성 완료
import { useEffect, useRef, useState } from 'react'
import { allPages, useApp } from '../../store'
import { Band } from '../../components/layout'
import { Btn, Check, Modal, TextField } from '../../components/ui'
import Icon from '../../components/Icon'
import ApiResults from '../../components/ApiResults'
import { api } from '../../api/client'
import { previewUsable, previewExpired, comparisonPeriod, reportComparisonInputs, reportConditions } from '../../api/integration'
import { amount, percent } from '../../api/adapters'
import { useQuery, ErrorNotice } from '../../api/hooks'
import { Hs4Search } from '../../components/pickers'
import { monthOffset } from '../home/useDashboard'
import { countryName, productName } from '../../data/catalog'
import { ALL_SECTIONS } from '../../data/seed'
const range = (from, to) => `${from || '—'} ~ ${to || '—'}`
const PERIOD_PRESETS = [['3', '최근 3개월'], ['6', '최근 6개월'], ['12', '최근 12개월']]
import bandAnalysis from '../../assets/band-analysis.jpg'

const STATUS_TAG = {
  condition: ['amber', '조건 확인 중'],
  generating: ['red', '생성 중'],
  done: ['ok', '생성 완료'],
}
const sectionCodes = { '주요 지표(KPI)': 'KPI', '주요 수출국': 'TOP_COUNTRIES', '급성장·리스크 시장': 'GROWTH_RISK', '현지 이슈 기사': 'LOCAL_ISSUES' }
const optionsFor = c => ({ conditions: c, country: c.country_codes.map(countryName).join(' · '), product: c.hs_items.map(h => h.hs_code).join(', '), from: c.period.start.slice(0, 7), to: c.period.end.slice(0, 7), ...reportComparisonInputs(c), sections: ALL_SECTIONS })

function AiBlock({ children }) {
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
        <span style={{ width: 30, height: 30, borderRadius: 10, background: 'var(--rose)', display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}><Icon name="sparkle" color="#C8102E" stroke={2} /></span>
        <b style={{ fontSize: 14 }}>Borderless Sellers AI</b>
      </div>
      <div style={{ border: '1px solid var(--line)', borderRadius: '4px 18px 18px 18px', padding: '22px 24px', background: '#fff' }}>{children}</div>
    </div>
  )
}

function UserBubble({ text }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
      <div style={{ maxWidth: 560, background: 'var(--plum)', color: '#fff', borderRadius: '18px 18px 4px 18px', padding: '16px 20px', fontSize: 16, lineHeight: 1.6, whiteSpace: 'pre-line' }}>{text}</div>
    </div>
  )
}

/** 상태 1: 리포트 조건 선택 */
function ConditionCard({ message, onChange, onGenerate }) {
  const { run, notify } = useApp()
  const availability = useQuery('/data/availability')
  const [preview, setPreview] = useState(null)
  const [busy, setBusy] = useState(false)
  const request = useRef(null)
  const o = message.options
  const signature = JSON.stringify(o)
  const generate = async () => {
    if (busy) return
    setBusy(true)
    await run(async () => {
      if (!previewUsable(preview, signature)) {
        const conditions = reportConditions(o)
        const data = await api('/reports/preview', { method: 'POST', body: { conditions } })
        setPreview({ ...data, signature }); request.current = crypto.randomUUID()
      } else {
        try {
          await onGenerate({ preview_id: preview.preview_id, confirm_conditions: true, formats: ['PDF', 'DOCX'], sections: o.sections.map(s => sectionCodes[s]) }, request.current)
        } catch (error) {
          if (!previewExpired(error)) throw error
          setPreview(null); request.current = null
          notify('리포트 조건 확인이 만료되었습니다. 조건을 다시 확인해 주세요.')
        }
      }
    })
    setBusy(false)
  }
  const set = patch => onChange({ ...message, options: { ...o, ...patch } })
  if (message.locked) {
    return (
      <AiBlock>
        <p style={{ fontSize: 17, fontWeight: 800 }}>분석 조건을 확인했습니다.</p>
        <p style={{ marginTop: 8, fontSize: 15 }}>국가: {o.country || '전체'} · 품목: {productName(o.product)}{o.product && `(${o.product})`} · 분석: {range(o.from, o.to)}</p>
        <p className="muted" style={{ marginTop: 4 }}>비교: {range(o.compareFrom, o.compareTo)} · 포함: {o.sections.map(s => s.replace('(KPI)', '').replace(' 기사', '').replace(' 시장', '')).join(' / ')}</p>
      </AiBlock>
    )
  }
  const monthInput = (key, extra) => (
    <div className="input val sm" style={extra}>
      <input type="month" value={o[key]} aria-label={key} onChange={e => e.target.value && set({ [key]: e.target.value, preset: 'custom' })} />
    </div>
  )
  return (
    <AiBlock>
      <p style={{ fontSize: 17, fontWeight: 800 }}>분석 조건을 확인해 주세요.</p>
      <p className="muted" style={{ marginTop: 6, fontSize: 14 }}>품목: {productName(o.product)}{o.product && `(${o.product})`} · 국가: {o.country || '전체'} (국가 선택 생략 가능)</p>
      <div className="chips" style={{ marginTop: 16 }} role="group" aria-label="기간 빠른 선택">
        {PERIOD_PRESETS.map(([key, label]) => (
          <button key={key} type="button" className={'chip sm' + (message.preset === key ? ' on' : '')} aria-pressed={message.preset === key}
            onClick={() => onChange({ ...message, preset: key, options: { ...o, from: monthOffset(availability.data?.trade_end_month, 1 - Number(key)), to: availability.data?.trade_end_month, compareFrom: '', compareTo: '' } })}>{label}</button>
        ))}
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '90px 1fr 20px 1fr', gap: 10, alignItems: 'center', marginTop: 16 }}>
        <span className="s" style={{ fontSize: 14, fontWeight: 700 }}>직접 선택</span>
        {monthInput('from')}<span className="c s">~</span>{monthInput('to')}
        <span className="s" style={{ fontSize: 14, fontWeight: 700 }}>비교 기간</span>
        {monthInput('compareFrom')}<span className="c s">~</span>{monthInput('compareTo')}
      </div>
      <p className="note" style={{ marginTop: 8 }}>기본 비교: 전년 같은 기간 · 직접 지정한 연/월은 그대로 적용</p>
      <p style={{ fontSize: 15, fontWeight: 800, marginTop: 18 }}>어떤 항목을 포함할까요? <span className="s" style={{ fontWeight: 500 }}>(기본: 전체)</span></p>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginTop: 12 }}>
        {ALL_SECTIONS.map(s => (
          <Check key={s} checked={o.sections.includes(s)} onChange={on => set({ sections: on ? ALL_SECTIONS.filter(x => x === s || o.sections.includes(x)) : o.sections.filter(x => x !== s) })}>{s}</Check>
        ))}
      </div>
      <div className="between" style={{ marginTop: 18 }}>
        <span className="note">핵심 요약은 항상 포함 · 선택 응답이 없으면 전체 항목으로 생성</span>
        <Btn kind="red" size="md" icon="file" onClick={generate} disabled={busy || !o.from || !o.to || o.from > o.to}>{previewUsable(preview, signature) ? '확인한 조건으로 생성' : '리포트 조건 확인'}</Btn>
      </div>
      {preview?.signature === signature && <p className="note">확인된 기간: {preview.conditions.period.start} ~ {preview.conditions.period.end} · 비교: {preview.conditions.compare_period?.start || '없음'} ~ {preview.conditions.compare_period?.end || ''} · 유효기간 {preview.expires_at}. 위 조건으로 PDF·DOCX를 생성합니다.</p>}
      {o.from > o.to && <p className="field-err">시작 월이 종료 월보다 늦습니다.</p>}
    </AiBlock>
  )
}

function AnswerCard({ message, onSaveReport, onInterest }) {
  const a = message.answer
  const c = a.conditions
  const trend = a.tables.length === 1 && a.tables[0].rows.length && a.tables[0].rows.every(r => r.month) ? a.tables[0].rows : null
  const comparison = comparisonPeriod(c, trend)
  const metric = c?.trade_flow === 'IMPORT' ? 'import_amount_usd' : 'export_amount_usd'
  const prior = c?.trade_flow === 'IMPORT' ? 'previous_year_import_usd' : 'previous_year_export_usd'
  const total = trend?.every(r => typeof r[metric] === 'number') ? trend.reduce((sum, r) => sum + r[metric], 0) : null
  const previous = trend?.every(r => typeof r[prior] === 'number') ? trend.reduce((sum, r) => sum + r[prior], 0) : null
  const growth = total != null && previous > 0 ? (total / previous - 1) * 100 : null
  const flow = trend && trend.length > 1 && total != null ? trend.at(-1)[metric] > trend[0][metric] ? '증가' : trend.at(-1)[metric] < trend[0][metric] ? '감소' : '동일' : '—'
  return <AiBlock>
    {c && <>
      <p style={{ fontSize: 13, fontWeight: 700, color: 'var(--red-d)' }}>분석 조건 · {c.hs_items?.length ? `HS ${c.hs_items.map(h => h.hs_code).join(', ')}` : '전체 품목'} / {c.country_codes?.map(countryName).join(' · ') || '전체 국가'}</p>
      <p className="note">{range(c.period?.start, c.period?.end)} / 비교 {range(comparison?.start, comparison?.end)}</p>
    </>}
    {trend && <div style={{ display: 'flex', gap: 14, marginTop: 16 }}>
      {[[`${trend.length}개월 ${c?.trade_flow === 'IMPORT' ? '수입' : '수출'}액`, `${amount(total)}억 USD`, '#15181E'], ['전년 동기 대비', percent(growth), growth >= 0 ? '#C8102E' : '#1F5FD1'], ['월별 흐름', flow, '#15181E']].map(([label, value, color]) => (
        <div key={label} style={{ flex: 1, background: 'var(--bg)', borderRadius: 14, padding: '16px 18px' }}>
          <p className="muted">{label}</p><p style={{ fontSize: 24, fontWeight: 800, marginTop: 4, color }}>{value}</p>
        </div>
      ))}
    </div>}
    <p style={{ marginTop: 16, fontSize: 16, whiteSpace: 'pre-wrap', lineHeight: 1.8 }}>{a.text}</p>
    {trend && <p className="note">월별 흐름은 조회된 첫 달과 마지막 달의 금액을 비교합니다.</p>}
    {a.warnings?.map(w => <p className="note" key={w}>{w}</p>)}
    {a.tables.map((table, i) => <ApiResults key={i} table={table} />)}
    {a.articles.map(article => <div className="li" key={article.article_id}><div><b>{article.title}</b><p className="muted">{article.summary}</p><Btn to={`/articles?id=${article.article_id}`}>기사 상세</Btn></div></div>)}
    <div style={{ display: 'flex', gap: 10, marginTop: 18 }}>
      {a.conditions && <Btn kind="red" icon="file" onClick={() => onSaveReport(a.conditions)}>리포트로 저장</Btn>}
      {a.tables[0]?.result_id && <Btn icon="star" onClick={() => onInterest(a.tables[0].result_id)}>관심 목록 추가</Btn>}
    </div></AiBlock>
}

export default function Analysis({ route }) {
  const { chats, refreshChats, refreshReports, run, notify } = useApp()
  const [activeId, setActiveId] = useState(route.query.get('chat') || chats[0]?.id)
  const [input, setInput] = useState(route.query.get('ask') || '')
  const [modal, setModal] = useState(null)
  const [rename, setRename] = useState('')
  const [messages, setMessages] = useState([])
  const [job, setJob] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [draft, setDraft] = useState(null)
  const [readRevision, setReadRevision] = useState(0)
  const [hsGroup, setHsGroup] = useState(null)
  const endRef = useRef(null), pending = useRef(null)
  const active = chats.find(c => c.id === activeId)
  useEffect(() => {
    let cancelled = false, timer
    const load = async () => {
      if (!activeId) return
      try {
        const rows = await allPages(`/chat/sessions/${activeId}/messages`)
        if (cancelled) return
        setMessages(rows); setError(null)
        const latest = [...rows].reverse().find(m => m.job)?.job
        setJob(latest || null)
        if (latest && ['QUEUED', 'RUNNING'].includes(latest.status)) timer = setTimeout(load, 2000)
      } catch (e) { if (!cancelled) setError(e) }
    }
    load()
    return () => { cancelled = true; clearTimeout(timer) }
  }, [activeId, busy, readRevision])
  useEffect(() => { endRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }) }, [messages.length])
  const newChat = () => run(async () => {
    const data = await api('/chat/sessions', { method: 'POST', body: {} })
    await refreshChats(); setActiveId(data.session.session_id); setMessages([]); setJob(null); setDraft(null); setInput('')
  })
  const send = async () => {
    if (!input.trim() || busy) return
    setBusy(true)
    await run(async () => {
      let id = activeId
      if (!id) { const data = await api('/chat/sessions', { method: 'POST', body: {} }); id = data.session.session_id; setActiveId(id) }
      if (pending.current?.text !== input || pending.current?.sessionId !== id) pending.current = { text: input, sessionId: id, id: crypto.randomUUID() }
      await api(`/chat/sessions/${id}/messages`, { method: 'POST', body: { question: input, client_message_id: pending.current.id } })
      setInput(''); pending.current = null; await refreshChats()
    })
    setBusy(false)
  }
  const saveAsReport = conditions => setDraft({ options: optionsFor(conditions) })
  const addInterest = result_id => run(async () => { await api('/users/me/interests/items', { method: 'POST', body: { result_id } }); notify('관심 목록에 추가했습니다. 관심정보 화면을 새로고침하면 확인할 수 있습니다.') })
  const removeChat = () => run(async () => { await api(`/chat/sessions/${activeId}`, { method: 'DELETE' }); await refreshChats(); setActiveId(null); setMessages([]); setJob(null); setDraft(null); setModal(null); notify('채팅방을 삭제했습니다.') })
  const updateTitle = () => run(async () => { await api(`/chat/sessions/${activeId}`, { method: 'PATCH', body: { title: rename.trim() } }); await refreshChats(); setModal(null) })
  const clarify = async body => {
    setBusy(true)
    await run(() => api(`/chat/jobs/${job.job_id}/clarifications`, { method: 'POST', body: { clarification_id: job.clarification.clarification_id, ...body } }))
    setBusy(false)
  }
  const headerTag = job ? ['amber', ({ QUEUED: '분석 대기', RUNNING: '분석 중', NEEDS_INPUT: '조건 확인', SUCCEEDED: '답변 완료', FAILED: '분석 실패' })[job.status]] : null

  return (
    <>
      <Band image={bandAnalysis} imagePosition="center 60%" eyebrow="AI ANALYSIS" title="AI분석" lead="국가·품목·기간을 자연어로 질문하고, 대화 내용으로 분석 리포트를 생성합니다." />
      <main className="page">
        <div className="wrap" style={{ display: 'grid', gridTemplateColumns: '300px 1fr', gap: 24, alignItems: 'start' }}>
          <aside className="card col" style={{ gap: 10, padding: 18 }}>
            <Btn kind="red" block icon="plus" onClick={newChat}>새 채팅</Btn>
            <div className="between" style={{ marginTop: 6 }}>
              <p style={{ fontSize: 15, fontWeight: 800 }}>내 채팅방</p>
              <span className="note">최근 대화 순</span>
            </div>
            {chats.map(c => {
              const on = c.id === active?.id
              const tag = STATUS_TAG[c.status]
              return (
                <button key={c.id} type="button" className="reset" onClick={() => { setActiveId(c.id); setMessages([]); setJob(null); setDraft(null) }} aria-current={on}
                  style={{ padding: '14px 16px', borderRadius: 14, background: on ? 'var(--rose)' : '#fff', boxShadow: on ? 'inset 3px 0 0 var(--red)' : 'none', border: on ? 0 : '1px solid var(--line)' }}>
                  {tag && <span className={'tag ' + tag[0]}>{tag[0] === 'ok' && <span className="dot ok" style={{ marginRight: 5 }} />}{tag[1]}</span>}
                  <p style={{ fontSize: 15, fontWeight: 800, marginTop: tag ? 8 : 0 }}>{c.title}</p>
                  <p className="note">{c.date}</p>
                </button>
              )
            })}
            {!chats.length && <p className="muted">채팅방이 없습니다. 새 채팅을 시작하세요.</p>}
            <p style={{ fontSize: 14, fontWeight: 800, marginTop: 10 }}>채팅방 관리</p>
            <Btn icon="edit" disabled={!active} style={{ justifyContent: 'flex-start' }} onClick={() => { setRename(active.title); setModal('rename') }}>선택 채팅방 이름 변경</Btn>
            <Btn icon="trash" disabled={!active} style={{ justifyContent: 'flex-start' }} onClick={() => setModal('delete')}>선택 채팅방 삭제</Btn>
            <p className="note">삭제 전 확인 창을 표시합니다. 리포트 파일 삭제는 별도 처리합니다.</p>
          </aside>

          <section className="card" style={{ padding: 0, overflow: 'hidden', minWidth: 0 }}>
            <div className="between" style={{ padding: '18px 26px', borderBottom: '1px solid var(--line)' }}>
              <h2 className="t2">{active?.title || '새 채팅'}</h2>
              {headerTag && <span className={'tag ' + headerTag[0]}>{headerTag[0] === 'ok' && <span className="dot ok" style={{ marginRight: 6 }} />}{headerTag[1]}</span>}
            </div>
            <div className="col" style={{ gap: 18, padding: '24px 26px' }}>
              {!messages.length && (
                <AiBlock>
                  <p style={{ fontSize: 17, fontWeight: 800 }}>무엇을 분석해 드릴까요?</p>
                  <p className="muted" style={{ marginTop: 6, fontSize: 14 }}>국가·품목·기간을 함께 말해 주세요. 예) “중국 메모리 수출 추이와 증가 이유를 알려줘”, “베트남 화장품 최근 6개월 리포트로 만들어줘”</p>
                </AiBlock>
              )}
              <ErrorNotice error={error} retry={() => setReadRevision(v => v + 1)} />
              {messages.map(m => m.role === 'USER' ? <UserBubble key={m.message_id} text={m.content_masked} /> : m.answer ? <AnswerCard key={m.message_id} message={m} onSaveReport={saveAsReport} onInterest={addInterest} /> : <AiBlock key={m.message_id}><p>{m.content_masked}</p></AiBlock>)}
              {job && ['QUEUED', 'RUNNING'].includes(job.status) && <AiBlock><p role="status">서버에서 분석 중입니다. 다른 화면으로 이동해도 작업은 유지됩니다.</p></AiBlock>}
              {job?.status === 'FAILED' && <ErrorNotice error={job.error || new Error('분석에 실패했습니다.')} />}
              {job?.status === 'NEEDS_INPUT' && <AiBlock><p>{job.clarification.message}</p><div className="chips">{job.clarification.choices.map(c => <Btn key={c.choice_id} disabled={busy} onClick={() => clarify({ choice_ids: [c.choice_id] })}>{c.hs_item ? `HS ${c.hs_item.hs_code} · ` : ''}{c.label}</Btn>)}</div>
                {job.clarification.kind === 'HS_ITEM' && <><Hs4Search group={hsGroup} onConfirm={setHsGroup} buttonLabel={() => '하위 품목 선택'} onSelectAll={() => {}} />{hsGroup?.items.map(([code, name]) => <Btn key={code} disabled={busy} onClick={() => clarify({ hs_item: { hs_code: code, hs_ver: 'HS2022' } })}>{name} ({code})</Btn>)}</>}
              </AiBlock>}
              {draft && <ConditionCard message={draft} onChange={setDraft} onGenerate={async (body, key) => { const data = await api('/reports', { method: 'POST', body, idempotencyKey: key }); await refreshReports(); setDraft(null); window.location.hash = `/report?id=${data.job.report_id}` }} />}
              <div ref={endRef} />
              <form onSubmit={e => { e.preventDefault(); send() }} style={{ display: 'flex', gap: 10, alignItems: 'center', border: '1px solid #D5D9E0', borderRadius: 16, padding: '10px 10px 10px 18px', background: '#fff' }}>
                <input value={input} onChange={e => setInput(e.target.value)} aria-label="질문 입력" placeholder="질문하거나 “이 분석을 리포트로 만들어줘”라고 입력하세요."
                  style={{ flex: 1, fontSize: 15, border: 0, outline: 0, font: 'inherit', color: 'var(--ink)', background: 'transparent' }} />
                <Btn type="submit" kind="red" iconRight="arrowRight" disabled={!input.trim() || busy || ['QUEUED', 'RUNNING', 'NEEDS_INPUT'].includes(job?.status)}>전송</Btn>
              </form>
            </div>
          </section>
        </div>
      </main>

      <Modal open={modal === 'rename'} onClose={() => setModal(null)} width={480} label="채팅방 이름 변경">
        <p style={{ fontSize: 13, fontWeight: 800, color: 'var(--red-d)' }}>채팅방 관리</p>
        <h2 style={{ fontSize: 22, fontWeight: 800, marginTop: 6 }}>채팅방 이름 변경</h2>
        <div style={{ marginTop: 16 }}><TextField value={rename} onChange={setRename} aria-label="새 이름" maxLength={40} /></div>
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, marginTop: 20 }}>
          <Btn size="md" onClick={() => setModal(null)}>취소</Btn>
          <Btn kind="red" size="md" disabled={!rename.trim()} onClick={updateTitle}>저장</Btn>
        </div>
      </Modal>
      <Modal open={modal === 'delete'} onClose={() => setModal(null)} width={480} label="채팅방 삭제">
        <span style={{ width: 52, height: 52, borderRadius: 16, background: 'var(--rose)', display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}><Icon name="trash" size={24} color="#C8102E" /></span>
        <h2 style={{ fontSize: 22, fontWeight: 800, marginTop: 16 }}>‘{active?.title}’ 채팅방을 삭제할까요?</h2>
        <p className="muted" style={{ marginTop: 10, fontSize: 15, lineHeight: 1.7 }}>대화 내용이 삭제됩니다. 이 채팅에서 생성한 리포트는 리포트 센터에 그대로 남습니다.</p>
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, marginTop: 24 }}>
          <Btn size="md" onClick={() => setModal(null)}>취소</Btn>
          <Btn kind="red" size="md" onClick={removeChat}>삭제</Btn>
        </div>
      </Modal>
    </>
  )
}

