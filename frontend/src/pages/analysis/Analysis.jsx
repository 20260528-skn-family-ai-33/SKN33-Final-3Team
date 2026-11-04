// BS14 AI분석(대화·리포트 조건·생성 진행·일반 답변·표 보기) · BS15 리포트 생성 완료
import { useEffect, useRef, useState } from 'react'
import { allPages, useApp } from '../../store'
import { Band } from '../../components/layout'
import { Btn, Check, Modal, TextField } from '../../components/ui'
import Icon from '../../components/Icon'
import ApiResults from '../../components/ApiResults'
import { api, streamAnswer } from '../../api/client'
import { requestId } from '../../api/requestId'
import { amount, percent, reportProduct, kstDateTimeText } from '../../api/adapters'
import { usePendingAction, useQuery, ErrorNotice } from '../../api/hooks'
import { previewExpired, comparisonPeriod, reportComparisonInputs, reportConditions } from '../../api/integration'
import { monthOffset } from '../home/useDashboard'
import { countryName, productName } from '../../data/catalog'
import { ALL_SECTIONS } from '../../data/seed'
const range = (from, to) => `${from || '—'} ~ ${to || '—'}`
const PERIOD_PRESETS = [['3', '최근 3개월'], ['6', '최근 6개월'], ['12', '최근 12개월']]
import bandAnalysis from '../../assets/band-analysis.jpg'
import './conversation.css'
import { PromptActions, Progress } from './ConversationSteps.jsx'
import TaskResults from './TaskResults.jsx'
import { completedMessages } from './completedMessages'
import { CountryPicker, InterestProducts } from '../../components/pickers'
import { countryCode } from '../../data/catalog'

const running = status => ['QUEUED','RUNNING','WAITING_REPORT'].includes(status)

const STATUS_TAG = {
  condition: ['amber', '조건 확인 중'],
  generating: ['red', '생성 중'],
  done: ['ok', '생성 완료'],
}
const sectionCodes = { '주요 지표(KPI)': 'KPI', '주요 수출국': 'TOP_COUNTRIES', '급성장·리스크 시장': 'GROWTH_RISK', '현지 이슈 기사': 'LOCAL_ISSUES' }
const optionsFor = (c, choices) => ({ conditions: c, country: c.country_codes.map(countryName).join(' · '), product: c.hs_items.map(h => reportProduct(h.hs_code, choices?.find(choice => choice.hs_item?.hs_code === h.hs_code)?.label || h.name_ko)).join(', '), from: c.period.start.slice(0, 7), to: c.period.end.slice(0, 7), ...reportComparisonInputs(c), sections: ALL_SECTIONS })

function AiBlock({ children, className }) {
  return (
    <div className={className}>
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
function ConditionCard({ message, onChange, onGenerate, onClose }) {
  const { run, notify } = useApp()
  const availability = useQuery('/data/availability')
  const [preview, setPreview] = useState(null)
  const [busy, runOnce] = usePendingAction()
  const request = useRef(null)
  const o = message.options
  const signature = JSON.stringify(o)
  const loadPreview = async () => {
    const data = await api('/reports/preview', { method: 'POST', body: { conditions: reportConditions(o) } })
    setPreview({ ...data, signature })
    request.current = requestId()
    return data
  }
  const confirm = () => runOnce(() => run(loadPreview))
  const generate = () => runOnce(() => run(async () => {
    const create = p => onGenerate({ preview_id: p.preview_id, confirm_conditions: true, formats: ['PDF', 'DOCX'], sections: o.sections.map(s => sectionCodes[s]) }, request.current)
    try {
      await create(preview)
    } catch (error) {
      if (!previewExpired(error)) throw error
      const fresh = await loadPreview()
      if (JSON.stringify(fresh.conditions) !== JSON.stringify(preview.conditions)) {
        notify('유효기간이 지나 생성 조건이 바뀌었습니다. 새 조건을 확인해 주세요.')
        return
      }
      await create(fresh)
    }
  }))
  const start = availability.data?.trade_start_month
  const end = availability.data?.trade_end_month
  const years = start && end ? Array.from({ length: Number(end.slice(0, 4)) - Number(start.slice(0, 4)) + 1 }, (_, i) => String(Number(end.slice(0, 4)) - i)) : []
  const incompleteComparison = Boolean(o.compareFrom) !== Boolean(o.compareTo)
  const reversedComparison = o.compareFrom && o.compareTo && o.compareFrom > o.compareTo
  const set = patch => onChange({ ...message, options: { ...o, ...patch } })
  const setScope = patch => {
    const conditions={...o.conditions,...patch}
    set({conditions,country:[...(conditions.include_korea_total?['한국(대한민국) 전체']:[]),...conditions.country_codes.map(countryName)].join(' · '),
      product:conditions.hs_items.map(h=>reportProduct(h.hs_code)).join(', ')})
  }
  if (message.locked) {
    return (
      <AiBlock>
        <p style={{ fontSize: 17, fontWeight: 800 }}>분석 조건을 확인했습니다.</p>
        <p style={{ marginTop: 8, fontSize: 15 }}>국가: {o.country || '전체'} · 품목: {o.product || '전체 품목'} · 분석: {range(o.from, o.to)}</p>
        <p className="muted" style={{ marginTop: 4 }}>비교: {range(o.compareFrom, o.compareTo)} · 포함: {o.sections.map(s => s.replace('(KPI)', '').replace(' 기사', '').replace(' 시장', '')).join(' / ')}</p>
      </AiBlock>
    )
  }
  const monthLabels = { from: '분석 시작', to: '분석 종료', compareFrom: '비교 시작', compareTo: '비교 종료' }
  const monthInput = key => {
    const [year = '', month = ''] = (o[key] || '').split('-')
    const monthsFor = y => Array.from({ length: 12 }, (_, i) => String(i + 1).padStart(2, '0')).filter(m => y + '-' + m >= start && y + '-' + m <= end)
    const availableMonths = monthsFor(year)
    const change = value => onChange({ ...message, preset: 'custom', options: { ...o, [key]: value } })
    return <div className="report-month">
      <select aria-label={monthLabels[key] + ' 연도'} value={year} disabled={!years.length} onChange={e => {
        const y = e.target.value, months = monthsFor(y)
        change(y ? y + '-' + (months.includes(month) ? month : months[0]) : '')
      }}>
        <option value="">연도</option>{year && !years.includes(year) && <option disabled>{year}</option>}
        {years.map(y => <option key={y}>{y}</option>)}
      </select>
      <select aria-label={monthLabels[key] + ' 월'} value={month} disabled={!years.includes(year)} onChange={e => change(year + '-' + e.target.value)}>
        {!month && <option value="">월</option>}{month && !availableMonths.includes(month) && <option value={month} disabled>{Number(month)}월</option>}
        {availableMonths.map(m => <option key={m} value={m}>{Number(m)}월</option>)}
      </select>
    </div>
  }
  return (
    <AiBlock>
      <div className="between"><p style={{ fontSize: 17, fontWeight: 800 }}>분석 조건을 확인해 주세요.</p><Btn size="sm" onClick={onClose}>닫기</Btn></div>
      <p className="muted" style={{ marginTop: 6, fontSize: 14 }}>품목: {o.product || '전체 품목'} · 국가: {o.country || '전체'} (국가 선택 생략 가능)</p>
      <details style={{marginTop:16}}><summary>국가 조건 선택 · {o.conditions.country_codes.length+(o.conditions.include_korea_total?1:0)}개</summary>
        <p className="note">여러 국가를 선택할 수 있습니다. 선택하지 않으면 전체 국가입니다.</p>
        <p className="note">한국(대한민국)은 전체 수출액입니다. 다른 교역 상대국과 함께 선택하면 월별 금액을 비교합니다. 한국 전체와 상대국 금액은 합산하지 않습니다.</p>
        <CountryPicker includeKorea showSelected value={[...(o.conditions.include_korea_total?['한국(대한민국)']:[]),...o.conditions.country_codes.map(countryName)]}
          onChange={names=>setScope({include_korea_total:names.includes('한국(대한민국)'),country_codes:names.filter(n=>n!=='한국(대한민국)').map(countryCode)})}/>
      </details>
      <details style={{marginTop:16}}><summary>품목 조건 선택 · {o.conditions.hs_items.length}개</summary>
        <p className="note">품목을 검색하고 체크박스로 여러 품목을 선택하세요. 선택하지 않으면 전체 품목입니다.</p>
        <InterestProducts products={o.conditions.hs_items.map(h=>h.hs_code)} setProducts={update=>{
          const previous=o.conditions.hs_items.map(h=>h.hs_code)
          const codes=typeof update==='function'?update(previous):update
          setScope({hs_items:codes.map(code=>({hs_code:code,hs_ver:'HS2022'}))})
        }}/>
      </details>
      <div className="chips" style={{ marginTop: 16 }} role="group" aria-label="기간 빠른 선택">
        {PERIOD_PRESETS.map(([key, label]) => (
          <button key={key} type="button" disabled={!start || !end || monthOffset(end, 1 - Number(key)) < start} className={'chip sm' + (message.preset === key ? ' on' : '')} aria-pressed={message.preset === key}
            onClick={() => onChange({ ...message, preset: key, options: { ...o, from: monthOffset(end, 1 - Number(key)), to: end, compareFrom: '', compareTo: '' } })}>{label}</button>
        ))}
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '90px 1fr 20px 1fr', gap: 10, alignItems: 'center', marginTop: 16 }}>
        <span className="s" style={{ fontSize: 14, fontWeight: 700 }}>직접 선택</span>
        {monthInput('from')}<span className="c s">~</span>{monthInput('to')}
        <span className="s" style={{ fontSize: 14, fontWeight: 700 }}>비교 기간</span>
        {monthInput('compareFrom')}<span className="c s">~</span>{monthInput('compareTo')}
      </div>
      <ErrorNotice error={availability.error} retry={availability.reload} />
      <p className="note" style={{ marginTop: 8 }}>기본 비교: 전년 같은 기간 · 직접 지정한 연/월은 그대로 적용</p>
      <p style={{ fontSize: 15, fontWeight: 800, marginTop: 18 }}>어떤 항목을 포함할까요? <span className="s" style={{ fontWeight: 500 }}>(기본: 전체)</span></p>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginTop: 12 }}>
        {ALL_SECTIONS.map(s => (
          <Check key={s} checked={o.sections.includes(s)} onChange={on => set({ sections: on ? ALL_SECTIONS.filter(x => x === s || o.sections.includes(x)) : o.sections.filter(x => x !== s) })}>{s}</Check>
        ))}
      </div>
      <div className="between" style={{ marginTop: 18 }}>
        <span className="note">핵심 요약은 항상 포함 · 선택 응답이 없으면 전체 항목으로 생성</span>
        <Btn kind="red" size="md" icon="file" onClick={confirm} disabled={busy || !o.from || !o.to || o.from > o.to || incompleteComparison || reversedComparison}>리포트 조건 확인</Btn>
      </div>
      <Modal open={!!preview && preview.signature === signature} onClose={() => { if (!busy) setPreview(null) }} label="리포트 생성 조건 확인" width={520}>
        <h2>이 조건으로 리포트를 생성할까요?</h2>
        <p>{o.country || '전체 국가'} · {o.product || '전체 품목'}</p>
        <p>분석 기간 {preview?.conditions.period.start} ~ {preview?.conditions.period.end}</p>
        <p>비교 기간 {preview?.conditions.compare_period ? range(preview.conditions.compare_period.start, preview.conditions.compare_period.end) : '없음'}</p>
        <div className="chips"><Btn disabled={busy} onClick={() => setPreview(null)}>조건 수정</Btn>
          <Btn kind="red" disabled={busy} onClick={generate}>확인한 조건으로 생성</Btn></div>
      </Modal>
      {o.from > o.to && <p className="field-err">시작 월이 종료 월보다 늦습니다.</p>}
      {incompleteComparison && <p className="field-err">비교 시작 월과 종료 월을 모두 선택해 주세요.</p>}
      {reversedComparison && <p className="field-err">비교 시작 월이 종료 월보다 늦습니다.</p>}
    </AiBlock>
  )
}

function ReportGeneration({initial,onComplete}) {
  const [job,setJob]=useState(initial)
  const [error,setError]=useState(null)
  const [retry,setRetry]=useState(0)
  useEffect(()=>{
    const controller=new AbortController(); let timer
    const poll=async()=>{
      try {
        const data=await api(`/reports/${initial.report_id}/status`,{signal:controller.signal})
        if(controller.signal.aborted)return
        setJob(data.job);setError(null)
        if(!['READY','FAILED'].includes(data.job.status))timer=setTimeout(poll,Math.max(2,data.job.retry_after_seconds||2)*1000)
        else onComplete()
      } catch(e){if(!controller.signal.aborted)setError(e)}
    }
    poll();return ()=>{controller.abort();clearTimeout(timer)}
  },[initial.report_id,retry,onComplete])
  const active=!['READY','FAILED'].includes(job.status)
  return <AiBlock><div role="status"><b>{job.status==='READY'?'리포트 생성 완료':job.status==='FAILED'?'리포트 생성 실패':'리포트 생성 중'}</b>
    {active && <span className="chat-loading-ring" aria-label="리포트 생성 진행 중"/>}</div>
    {active && <p className="note">채팅을 계속할 수 있습니다. 완료되면 여기에서 알려드립니다.</p>}
    <ErrorNotice error={error || job.error} retry={()=>setRetry(v=>v+1)}/>
    {job.status==='READY' && <Btn to={`/report?id=${job.report_id}`}>리포트 센터에서 이 리포트 열기</Btn>}
  </AiBlock>
}

function AnswerCard({ message, onSaveReport, onInterest, streamJobId }) {
  const [stream,setStream]=useState(null)
  useEffect(()=>{
    if(!streamJobId)return
    const controller=new AbortController()
    let active=true
    const timeout=setTimeout(()=>controller.abort(),8000)
    streamAnswer(streamJobId,text=>setStream({id:streamJobId,text,done:false}),controller.signal)
      .catch(()=>{})
      .finally(()=>{clearTimeout(timeout);if(active)setStream({id:streamJobId,done:true})})
    return ()=>{active=false;clearTimeout(timeout);controller.abort()}
  },[streamJobId])
  const a = message.answer
  const c = a.conditions
  const trend = !a.tasks?.length && a.tables.length === 1 && a.tables[0].rows.length && a.tables[0].rows.every(r => r.month) ? a.tables[0].rows : null
  const comparison = comparisonPeriod(c, trend)
  const metric = c?.trade_flow === 'IMPORT' ? 'import_amount_usd' : 'export_amount_usd'
  const prior = c?.trade_flow === 'IMPORT' ? 'previous_year_import_usd' : 'previous_year_export_usd'
  const total = trend?.every(r => typeof r[metric] === 'number') ? trend.reduce((sum, r) => sum + r[metric], 0) : null
  const previous = trend?.every(r => typeof r[prior] === 'number') ? trend.reduce((sum, r) => sum + r[prior], 0) : null
  const growth = total != null && previous > 0 ? (total / previous - 1) * 100 : null
  const flow = trend && trend.length > 1 && total != null ? trend.at(-1)[metric] > trend[0][metric] ? '증가' : trend.at(-1)[metric] < trend[0][metric] ? '감소' : '동일' : '—'
  const receiving=streamJobId && (stream?.id!==streamJobId||!stream.done)
  return <AiBlock>
    {c && <>
      <p style={{ fontSize: 13, fontWeight: 700, color: 'var(--red-d)' }}>분석 조건 · {c.hs_items?.length ? `HS ${c.hs_items.map(h => h.hs_code).join(', ')} / ` : ''}{c.country_codes?.map(countryName).join(' · ') || '전체 국가'}</p>
      <p className="note">{range(c.period?.start, c.period?.end)} / 비교 {range(comparison?.start, comparison?.end)}</p>
    </>}
    {trend && <div style={{ display: 'flex', flexWrap: 'wrap', gap: 14, marginTop: 16 }}>
      {[[`${trend.length}개월 ${c?.trade_flow === 'IMPORT' ? '수입' : '수출'}액`, `${amount(total)}억 USD`, '#15181E'], ['전년 동기 대비', percent(growth), growth >= 0 ? '#C8102E' : '#1F5FD1'], ['월별 흐름', flow, '#15181E']].map(([label, value, color]) => (
        <div key={label} style={{ flex: '1 1 140px', background: 'var(--bg)', borderRadius: 14, padding: '16px 18px' }}>
          <p className="muted">{label}</p><p style={{ fontSize: 24, fontWeight: 800, marginTop: 4, color }}>{value}</p>
        </div>
      ))}
    </div>}
    {receiving && <><p role="status" className="note">검증 완료 · 답변 수신 중</p><p style={{whiteSpace:'pre-wrap',lineHeight:1.8}}>{stream?.id===streamJobId?stream.text:''}</p></>}
    {a.tasks?.length ? <TaskResults answer={a} hideText={receiving}/> : !receiving && <p style={{ marginTop: 16, fontSize: 16, whiteSpace: 'pre-wrap', lineHeight: 1.8 }}>{a.text}</p>}
    {trend && <p className="note">월별 흐름은 조회된 첫 달과 마지막 달의 금액을 비교합니다.</p>}
    {a.warnings?.filter(w => w !== a.text).map(w => <p className="note" key={w}>{w}</p>)}
    {!a.tasks?.length && a.tables.map((table, i) => <ApiResults key={i} table={table} />)}
    {a.articles.map(article => <div className="li" key={article.article_id}><div><b>{article.title}</b><p className="muted">{article.summary}</p><Btn to={`/articles?id=${article.article_id}`}>기사 상세</Btn></div></div>)}
    <div style={{ display: 'flex', gap: 10, marginTop: 18 }}>
      {a.report_id && <><p role="status">리포트 생성 완료</p><Btn to={`/report?id=${a.report_id}`}>리포트 센터에서 이 리포트 열기</Btn></>}
      {/* 리포트는 수출 통계 기간이 있어야 만든다. 기사만 조회한 답변(LOCAL_ISSUES)은 period가 null이다 */}
      {a.conditions?.period && <Btn kind="red" icon="file" onClick={() => onSaveReport(a.conditions)}>리포트로 저장</Btn>}
      {a.tables[0]?.result_id && <Btn icon="star" onClick={() => onInterest(a)}>관심 목록 추가</Btn>}
    </div></AiBlock>
}

export default function Analysis({ route }) {
  const { session, chats, refreshChats, refreshReports, addInterests, run, notify } = useApp()
  const [activeId, setActiveId] = useState(route.query.get('chat') || chats[0]?.id)
  const [input, setInput] = useState(route.query.get('ask') || '')
  const [modal, setModal] = useState(null)
  const [rename, setRename] = useState('')
  const [messages, setMessages] = useState([])
  const [job, setJob] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [draft, setDraft] = useState(null)
  const reportStorageKey=`analysis-reports:${session?.id}`
  const [chatReports,setChatReports]=useState(()=>{
    try { return JSON.parse(sessionStorage.getItem(reportStorageKey)||'{}') } catch { return {} }
  })
  useEffect(()=>{try {sessionStorage.setItem(reportStorageKey,JSON.stringify(chatReports))}catch{/* Storage can be disabled. */}},[chatReports,reportStorageKey])
  const [interest, setInterest] = useState(null)
  const [savingInterest, setSavingInterest] = useState(false)
  const [readRevision, setReadRevision] = useState(0)
  const endRef = useRef(null), pending = useRef(null)
  const inputRef=useRef(null)
  const clarificationRequest=useRef(null), acceptedClarifications=useRef(new Set())
  const active = chats.find(c => c.id === activeId)
  useEffect(() => {
    let cancelled = false, timer
    const load = async () => {
      if (!activeId) return
      try {
        const rows = await allPages(`/chat/sessions/${activeId}/messages`)
        if (cancelled) return
        setMessages(rows); setError(null)
        let latest = [...rows].reverse().find(m => m.job)?.job
        // The job endpoint also checks whether a pending checkpoint has expired.
        if (latest && (running(latest.status)||latest.status==='NEEDS_INPUT')) latest=(await api(`/chat/jobs/${latest.job_id}`)).job
        if (cancelled) return
        if (latest?.status==='NEEDS_INPUT' && acceptedClarifications.current.has(latest.clarification?.clarification_id)) {
          timer=setTimeout(load,2000)
          return
        }
        setMessages(completedMessages(rows,latest))
        setJob(latest || null)
        if (latest && running(latest.status)) timer = setTimeout(load, 2000)
      } catch (e) { if (!cancelled) setError(e) }
    }
    load()
    return () => { cancelled = true; clearTimeout(timer) }
  }, [activeId, readRevision])
  useEffect(() => { endRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }) }, [messages.length])
  const clarificationId=job?.status==='NEEDS_INPUT' ? job.clarification?.clarification_id : null
  useEffect(() => {
    if (!clarificationId) return
    const frame=requestAnimationFrame(()=>{
      inputRef.current?.closest('form')?.scrollIntoView({block:'end',behavior:'smooth'})
      if (job.clarification?.allow_text) inputRef.current?.focus({preventScroll:true})
    })
    return ()=>cancelAnimationFrame(frame)
  },[clarificationId,job?.clarification?.allow_text])
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
      if (pending.current?.text !== input || pending.current?.sessionId !== id) pending.current = { text: input, sessionId: id, id: requestId() }
      const data = await api(`/chat/sessions/${id}/messages`, { method: 'POST', body: { question: input, client_message_id: pending.current.id } })
      setJob(data.job); setDraft(null); setInput(''); pending.current = null
      setReadRevision(v => v + 1); await refreshChats()
    })
    setBusy(false)
  }
  const saveAsReport = conditions => setDraft({ options: optionsFor(conditions) })
  const addInterest = answer => {
    const rows=answer.tables.flatMap(t=>t.rows||[])
    const countries=[...new Set([...(answer.conditions?.country_codes||[]),...rows.map(r=>r.country_code).filter(Boolean)])]
    const products=[...new Map([...(answer.conditions?.hs_items||[]),...rows.filter(r=>r.hs_code&&r.hs_ver)].filter(h=>h.hs_code.length===6).map(h=>[h.hs_ver+h.hs_code,{hs_code:h.hs_code,hs_ver:h.hs_ver}])).values()]
    // 이미 등록한 국가·품목은 선택할 수 없게 표시한다.
    return run(async()=>{
      const {interests:saved}=await api('/users/me/interests')
      setInterest({countries,products,country_codes:[],hs_items:[],
        savedCountries:new Set(saved.country_codes),savedProducts:new Set(saved.hs_items.map(h=>h.hs_ver+h.hs_code))})
    })
  }
  const selectableInterest=interest&&(interest.countries.some(c=>!interest.savedCountries.has(c))||
    interest.products.some(h=>!interest.savedProducts.has(h.hs_ver+h.hs_code)))
  const saveInterest = async () => {
    if (!interest.country_codes.length && !interest.hs_items.length) { window.alert('체크된 항목이 없습니다'); return }
    setSavingInterest(true)
    const saved=await addInterests(interest.country_codes,interest.hs_items)
    if (saved) setInterest(null)  // 'STALE'(세션 전환)도 닫는다. 다른 계정에서 다시 저장하지 않게 한다
    if (saved===true) notify('선택한 국가·품목을 관심 목록에 추가했습니다.')
    setSavingInterest(false)
  }
  const removeChat = () => run(async () => { await api(`/chat/sessions/${activeId}`, { method: 'DELETE' }); await refreshChats(); setActiveId(null); setMessages([]); setJob(null); setDraft(null); setModal(null); notify('채팅방을 삭제했습니다.') })
  const updateTitle = () => run(async () => { await api(`/chat/sessions/${activeId}`, { method: 'PATCH', body: { title: rename.trim() } }); await refreshChats(); setModal(null) })
  const clarify = async body => {
    const id=job?.clarification?.clarification_id
    if (!id || clarificationRequest.current || acceptedClarifications.current.has(id)) return
    clarificationRequest.current=id
    setBusy(true)
    try {
      const data = await run(() => api(`/chat/jobs/${job.job_id}/clarifications`, { method: 'POST', body: { clarification_id:id, ...body } }))
      if (data?.job) {
        if (data.job.status!=='NEEDS_INPUT' || data.job.clarification?.clarification_id!==id) acceptedClarifications.current.add(id)
        setJob(data.job); setInput(''); setDraft(null)
      }
      setReadRevision(v => v + 1)
    } finally { clarificationRequest.current=null; setBusy(false) }
  }
  const headerTag = job ? [job.status==='SUCCEEDED'?'ok':'amber', ({ QUEUED: '분석 대기', RUNNING: '분석 중', WAITING_REPORT:'리포트 만드는 중', NEEDS_INPUT: '조건 확인', SUCCEEDED: '답변 완료', FAILED: '분석 실패', EXPIRED:'시간 초과', CANCELLED:'중단됨' })[job.status]] : null

  return (
    <>
      <Band image={bandAnalysis} imagePosition="center 60%" eyebrow="AI CHATBOT" title="AI챗봇" lead="국가·품목·기간을 자연어로 질문하고, 대화 내용으로 분석 리포트를 생성합니다." />
      <main className="page">
        <div className="wrap chat-lay">
          <aside className="card col chat-side" style={{ gap: 10, padding: 18 }}>
            <Btn kind="red" block icon="plus" onClick={newChat}>새 채팅</Btn>
            <div className="between" style={{ marginTop: 6 }}>
              <p style={{ fontSize: 15, fontWeight: 800 }}>내 채팅방</p>
              <span className="note">최근 대화 순 · 한국 시각(KST)</span>
            </div>
            <div className="col chat-list" style={{ gap: 10 }}>
            {chats.map(c => {
              const on = c.id === active?.id
              const tag = STATUS_TAG[c.status]
              return (
                <button key={c.id} type="button" className="reset" onClick={() => { setActiveId(c.id); setMessages([]); setJob(null); setDraft(null) }} aria-current={on}
                  style={{ padding: '14px 16px', borderRadius: 14, background: on ? 'var(--rose)' : '#fff', boxShadow: on ? 'inset 3px 0 0 var(--red)' : 'none', border: on ? 0 : '1px solid var(--line)' }}>
                  {tag && <span className={'tag ' + tag[0]}>{tag[0] === 'ok' && <span className="dot ok" style={{ marginRight: 5 }} />}{tag[1]}</span>}
                  <p style={{ fontSize: 15, fontWeight: 800, marginTop: tag ? 8 : 0 }}>{c.title}</p>
                  <p className="note">{kstDateTimeText(c.updated_at)}</p>
                </button>
              )
            })}
            </div>
            {!chats.length && <p className="muted">채팅방이 없습니다. 새 채팅을 시작하세요.</p>}
            <p style={{ fontSize: 14, fontWeight: 800, marginTop: 10 }}>채팅방 관리</p>
            <div className="col chat-manage" style={{ gap: 10 }}>
              <Btn icon="edit" disabled={!active} style={{ justifyContent: 'flex-start' }} onClick={() => { setRename(active.title); setModal('rename') }}>선택 채팅방 이름 변경</Btn>
              <Btn icon="trash" disabled={!active} style={{ justifyContent: 'flex-start' }} onClick={() => setModal('delete')}>선택 채팅방 삭제</Btn>
            </div>
            <p className="note">삭제 전 확인 창을 표시합니다. 리포트 파일 삭제는 별도 처리합니다.</p>
          </aside>

          <section className="card" style={{ padding: 0, overflow: 'hidden', minWidth: 0 }}>
            <div className="between" style={{ padding: '18px 26px', borderBottom: '1px solid var(--line)' }}>
              <h2 className="t2">{active?.title || '새 채팅'}</h2>
              {headerTag && <span className={'tag ' + headerTag[0]}>{headerTag[0] === 'ok' && <span className="dot ok" style={{ marginRight: 6 }} />}{headerTag[1]}</span>}
            </div>
            <div className="col chat-flow" style={{ gap: 18, padding: '24px 26px' }}>
              {!messages.length && (
                <AiBlock>
                  <p style={{ fontSize: 17, fontWeight: 800 }}>무엇을 분석해 드릴까요?</p>
                  <p className="muted" style={{ marginTop: 6, fontSize: 14 }}>국가·품목·기간을 함께 말해 주세요. 예) “중국 메모리 수출 추이와 증가 이유를 알려줘”, “베트남 화장품 최근 6개월 리포트로 만들어줘”</p>
                </AiBlock>
              )}
              <ErrorNotice error={error} retry={() => setReadRevision(v => v + 1)} />
              {messages.map(m => {
                if (m.role !== 'USER') return m.answer
                  ? <AnswerCard key={m.message_id} message={m} streamJobId={m===messages.at(-1) && job?.status==='SUCCEEDED' ? job.job_id : undefined} onSaveReport={saveAsReport} onInterest={addInterest}/>
                  : <AiBlock key={m.message_id}><p>{m.content_masked}</p></AiBlock>
                const turn = m.job?.job_id === job?.job_id ? job : m.job
                return <div className="chat-turn" key={m.message_id}>
                  <UserBubble text={m.content_masked}/>
                  {turn && !['CANCELLED','FAILED','EXPIRED'].includes(turn.status) && (running(turn.status) || turn.progress?.plan?.length>0) && <AiBlock><Progress job={turn}/></AiBlock>}
                  {turn?.clarification_history?.filter(h=>h.id!==job?.clarification?.clarification_id).map(h=><div className="chat-turn" key={h.id}>
                    <AiBlock><p>{h.message}</p></AiBlock>{h.answer && <UserBubble text={h.answer}/>}
                  </div>)}
                </div>
              })}
              {running(job?.status) && !messages.some(m=>m.job?.job_id===job.job_id) && <AiBlock><Progress job={job}/></AiBlock>}
              {job?.status === 'FAILED' && <ErrorNotice error={job.error || new Error('분석에 실패했습니다.')} />}
              {job?.status === 'EXPIRED' && <AiBlock><p>답변 시간이 지났습니다. 새 질문을 입력해 주세요.</p></AiBlock>}
              {job?.status === 'CANCELLED' && <AiBlock><p>질문을 중단했습니다. 새 질문을 입력할 수 있습니다.</p></AiBlock>}
              {job?.status === 'NEEDS_INPUT' && <AiBlock className="chat-clarification"><PromptActions key={job.clarification.clarification_id} prompt={job.clarification} busy={busy} reply={clarify} onDirectInput={()=>inputRef.current?.focus()}/></AiBlock>}
              {(running(job?.status)||job?.status==='NEEDS_INPUT') && <Btn size="sm" disabled={busy} onClick={()=>setModal('stop')}>이 질문 중단</Btn>}
              {draft && <ConditionCard message={draft} onClose={()=>setDraft(null)} onChange={setDraft} onGenerate={async (body, key) => {
                const data=await api('/reports',{method:'POST',body,idempotencyKey:key})
                setChatReports(previous=>({...previous,[activeId]:[...(previous[activeId]||[]).filter(r=>r.report_id!==data.job.report_id),data.job]}))
                setDraft(null)
                await refreshReports()
              }}/>}
              {(chatReports[activeId]||[]).map(report=><ReportGeneration key={report.report_id} initial={report} onComplete={refreshReports}/>)}
              <div ref={endRef} />
              <form onSubmit={e => { e.preventDefault(); if(job?.status==='NEEDS_INPUT') clarify({text:input.trim()}); else send() }} style={{ display: 'flex', gap: 10, alignItems: 'center', border: '1px solid #D5D9E0', borderRadius: 16, padding: '10px 10px 10px 18px', background: '#fff' }}>
                <input ref={inputRef} value={input} disabled={busy || running(job?.status) || (job?.status==='NEEDS_INPUT' && !job.clarification?.allow_text)} onChange={e => setInput(e.target.value)} aria-label="질문 입력" placeholder={job?.clarification?.kind==='COUNTRY'?'국가명 또는 국가 코드 입력':job?.status==='NEEDS_INPUT' ? '선택지를 고르거나 답을 직접 입력하세요.' : '분석할 내용을 질문하세요.'}
                  style={{ flex: 1, fontSize: 15, border: 0, outline: 0, font: 'inherit', color: 'var(--ink)', background: 'transparent' }} />
                <Btn type="submit" kind="red" iconRight="arrowRight" disabled={!input.trim() || busy || running(job?.status) || (job?.status==='NEEDS_INPUT' && !job.clarification?.allow_text)}>전송</Btn>
              </form>
            </div>
          </section>
        </div>
      </main>

      <Modal open={modal === 'stop'} onClose={()=>setModal(null)} width={420} label="질문 중단"><h2>이 질문을 중단할까요?</h2><p>이미 접수된 리포트는 계속 생성됩니다.</p><div className="chips"><Btn onClick={()=>setModal(null)}>계속 진행</Btn><Btn kind="red" onClick={async()=>{setModal(null);setBusy(true);const data=await run(()=>api(`/chat/jobs/${job.job_id}/cancel`,{method:'POST'}));if(data?.job)setJob(data.job);setReadRevision(v=>v+1);setBusy(false)}}>중단</Btn></div></Modal>
      <Modal open={!!interest} onClose={()=>!savingInterest&&setInterest(null)} width={480} label="관심 목록 추가">
        <h2>관심 목록에 추가할 항목</h2><p className="note">저장할 국가·품목을 선택하세요. 품목은 HS6 단위로 등록됩니다.</p>
        <div className="col" style={{gap:12,maxHeight:320,overflowY:'auto',margin:'16px 0'}}>
          {interest?.countries.map(c=>{const saved=interest.savedCountries.has(c);return <Check key={c} disabled={savingInterest||saved} checked={saved||interest.country_codes.includes(c)} onChange={on=>setInterest({...interest,country_codes:on?[...interest.country_codes,c]:interest.country_codes.filter(v=>v!==c)})}>{countryName(c)}{saved&&' · 이미 등록됨'}</Check>})}
          {interest?.products.map(h=>{const saved=interest.savedProducts.has(h.hs_ver+h.hs_code);return <Check key={h.hs_ver+h.hs_code} disabled={savingInterest||saved} checked={saved||interest.hs_items.includes(h)} onChange={on=>setInterest({...interest,hs_items:on?[...interest.hs_items,h]:interest.hs_items.filter(v=>v!==h)})}>{productName(h.hs_code)} ({h.hs_code}){saved&&' · 이미 등록됨'}</Check>})}
          {interest&&!interest.countries.length&&!interest.products.length&&<p>추가할 국가·HS6 품목이 없습니다. 국가나 품목을 지정해 분석해 주세요.</p>}
          {interest&&(interest.countries.length>0||interest.products.length>0)&&!selectableInterest&&<p role="status">모두 이미 등록된 항목입니다.</p>}
        </div><div className="chips"><Btn disabled={savingInterest} onClick={()=>setInterest(null)}>취소</Btn><Btn kind="red" disabled={savingInterest||!selectableInterest} onClick={saveInterest}>선택 항목 저장</Btn></div>
      </Modal>
      <Modal open={modal === 'rename'} onClose={() => setModal(null)} width={480} label="채팅방 이름 변경">
        <p style={{ fontSize: 13, fontWeight: 800, color: 'var(--red-d)' }}>채팅방 관리</p>
        <h2 style={{ fontSize: 22, fontWeight: 800, marginTop: 6 }}>채팅방 이름 변경</h2>
        <div style={{ marginTop: 16 }}><TextField value={rename} onChange={setRename} onEnter={() => rename.trim() && updateTitle()} aria-label="새 이름" maxLength={40} /></div>
        <p className="note" style={{ marginTop: 6, textAlign: 'right' }}>{rename.length} / 40자 · Enter로 저장</p>
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

