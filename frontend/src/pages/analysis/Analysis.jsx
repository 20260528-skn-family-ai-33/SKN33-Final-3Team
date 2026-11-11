// BS14 AI분석(대화·리포트 조건·생성 진행·일반 답변·표 보기) · BS15 리포트 생성 완료
import { Fragment, useEffect, useRef, useState } from 'react'
import { allPages, useApp } from '../../store'
import { navigate } from '../../router'
import { Band } from '../../components/layout'
import { Btn, Check, Modal, TextField } from '../../components/ui'
import Icon from '../../components/Icon'
import ApiResults from '../../components/ApiResults'
import { api, streamAnswer } from '../../api/client'
import { requestId } from '../../api/requestId'
import { amount, percent, reportProduct, kstDateTimeText, monthEndDate } from '../../api/adapters'
import { usePendingAction, useQuery, ErrorNotice } from '../../api/hooks'
import { previewExpired, comparisonPeriod, reportComparisonInputs, reportConditions } from '../../api/integration'
import { monthOffset } from '../home/useDashboard'
import { countryName, productChoiceLabel } from '../../data/catalog'
import { ALL_SECTIONS } from '../../data/seed'
const range = (from, to) => `${from || '—'} ~ ${monthEndDate(to) || '—'}`
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
const sectionCodes = { '주요 지표(KPI)': 'KPI', '주요 수출국': 'TOP_COUNTRIES', '유망시장(증가·감소 예상)': 'GROWTH_RISK', '현지 이슈 기사': 'LOCAL_ISSUES' }
const optionsFor = (c, choices) => ({ conditions: c, country: c.country_codes.map(countryName).join(' · '), product: c.hs_items.map(h => reportProduct(h.hs_code, choices?.find(choice => choice.hs_item?.hs_code === h.hs_code)?.label || h.name_ko)).join(', '), from: c.period.start.slice(0, 7), to: c.period.end.slice(0, 7), ...reportComparisonInputs(c), sections: ALL_SECTIONS })

function AiBlock({ children, className, id }) {
  return (
    <div className={['chat-ai', className].filter(Boolean).join(' ')} id={id}>
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
    <div className="chat-user" style={{ display: 'flex', justifyContent: 'flex-end' }}>
      <div style={{ maxWidth: '75%', background: 'var(--plum)', color: '#fff', borderRadius: '18px 18px 4px 18px', padding: '16px 20px', fontSize: 16, lineHeight: 1.6, whiteSpace: 'pre-line' }}>{text}</div>
    </div>
  )
}

/** 상태 1: 리포트 조건 선택 */
function ConditionCard({ id, message, onChange, onGenerate, onClose }) {
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
      <AiBlock id={id}>
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
    // 연도를 고르기 전에도 월 칸을 잠그지 않는다(Tab이 항상 연도→월 순서로 움직이게). 그때 월은 최신 연도 기준이다.
    const monthYear = year || (end || '').slice(0, 4)
    const availableMonths = monthsFor(monthYear)
    const change = value => onChange({ ...message, preset: 'custom', options: { ...o, [key]: value } })
    return <div className="report-month">
      <select aria-label={monthLabels[key] + ' 연도'} value={year} disabled={!years.length} onChange={e => {
        const y = e.target.value, months = monthsFor(y)
        change(y ? y + '-' + (months.includes(month) ? month : months[0]) : '')
      }}>
        <option value="">연도</option>{year && !years.includes(year) && <option disabled>{year}</option>}
        {years.map(y => <option key={y}>{y}</option>)}
      </select>
      <select aria-label={monthLabels[key] + ' 월'} value={month} disabled={!years.length} onChange={e => change(monthYear + '-' + e.target.value)}>
        {!month && <option value="">월</option>}{month && !availableMonths.includes(month) && <option value={month} disabled>{Number(month)}월</option>}
        {availableMonths.map(m => <option key={m} value={m}>{Number(m)}월</option>)}
      </select>
    </div>
  }
  return (
    <AiBlock id={id}>
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
        <span className="s" style={{ fontSize: 14, fontWeight: 700 }}>분석 기간</span>
        {monthInput('from')}<span className="c s">~</span>{monthInput('to')}
        <span className="s" style={{ fontSize: 14, fontWeight: 700 }}>비교 기간</span>
        {monthInput('compareFrom')}<span className="c s">~</span>{monthInput('compareTo')}
      </div>
      <ErrorNotice error={availability.error} retry={availability.reload} />
      <p className="note" style={{ marginTop: 8 }}>비교 기간을 비워 두면 전년 같은 기간과 비교합니다.</p>
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
        <p>분석 기간 {range(preview?.conditions.period.start, preview?.conditions.period.end)}</p>
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

function ReportGeneration({initial,onComplete,onDismiss}) {
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
  return <AiBlock id={`report-${initial.report_id}`}><div role="status"><b>{job.status==='READY'?'리포트 생성 완료':job.status==='FAILED'?'리포트 생성 실패':'리포트 생성 중'}</b>
    {active && <span className="chat-loading-ring" aria-label="리포트 생성 진행 중"/>}</div>
    {active && <p className="note">채팅을 계속할 수 있습니다. 완료되면 여기에서 알려드립니다.</p>}
    <ErrorNotice error={error || job.error} retry={()=>setRetry(v=>v+1)}/>
    {job.status==='FAILED' && <Btn onClick={onDismiss}>실패 안내 닫기</Btn>}
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
      {a.article_period && !c.period
        ? <p className="note">기사 검색 기간 {a.article_period.start} ~ {a.article_period.end}</p>
        : c.period && <p className="note">{range(c.period.start, c.period.end)}{comparison && ` / 비교 ${range(comparison.start, comparison.end)}`}</p>}
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
    {!a.tasks?.length && a.tables.map((table, i) => <ApiResults key={i} table={table} flow={c?.trade_flow} />)}
    {a.articles.map((article, index) => <div className="li" key={`${article.task_id || ''}:${article.article_id}`}><div>
      <b>[{index + 1}] {article.title}</b>
      <p className="muted">{article.source_code} · {article.published_date}</p>
      {article.sentences?.length ? article.sentences.map((s, i) => <details key={i}>
        <summary>{s.text} · 근거 보기</summary>
        {s.quotes.map((quote, j) => <blockquote key={j}>{quote}</blockquote>)}
      </details>) : (article.summary || article.evidence_sufficient === false) && <p>{article.summary || (article.unavailable ? '답변 뒤 공개가 바뀌어 볼 수 없는 기사입니다.' : a.order_by === 'published_date_desc' ? '발행일 기준 최신 기사' : '본문 근거 부족')}</p>}
      {/* unavailable: 검색 뒤 숨겨진 기사. [n] 번호만 지키려고 남긴 자리라 상세로 가지 않는다(D-102) */}
      {!article.unavailable && <Btn to={`/articles?id=${article.article_id}`}>기사 상세</Btn>}
      {/^https?:\/\//i.test(article.source_url || '') && <a href={article.source_url} target="_blank" rel="noreferrer">원문 보기</a>}
    </div></div>)}
    <div style={{ display: 'flex', gap: 10, marginTop: 18 }}>
      {a.report_id && <><p role="status">리포트 생성 완료</p><Btn to={`/report?id=${a.report_id}`}>리포트 센터에서 이 리포트 열기</Btn></>}
      {/* 리포트는 수출 통계 기간이 있어야 만든다. 기사만 조회한 답변(LOCAL_ISSUES)은 period가 null이다 */}
      {a.conditions?.period && <Btn kind="red" icon="file" onClick={() => onSaveReport(a.conditions)}>리포트로 저장</Btn>}
      {a.tables[0]?.result_id && <Btn icon="star" onClick={() => onInterest(a)}>관심 목록 추가</Btn>}
    </div></AiBlock>
}

const ARTICLE_ACTIONS_ENABLED = false  // 다시 쓰면 true로 바꾸고 Articles.jsx 버튼 주석을 푼다.

export default function Analysis({ route }) {
  const { session, chats, refreshChats, refreshReports, addInterests, run, notify } = useApp()
  // 기사 탭의 "AI에게 물어보기"는 사용 중지(2026-11-11). 주소로 직접 열어도 선택 기사 화면을 띄우지 않는다.
  const [articleId, setArticleId] = useState(() => ARTICLE_ACTIONS_ENABLED && route.query.get('new') === '1' ? route.query.get('article') : null)
  const selectedArticle = useQuery(`/articles/${articleId}`, {}, !!articleId)
  const [activeId, setActiveId] = useState(articleId ? null : route.query.get('chat') || chats[0]?.id)
  const [input, setInput] = useState(route.query.get('new') === '1' ? '' : route.query.get('ask') || '')
  const [modal, setModal] = useState(null)
  const [rename, setRename] = useState('')
  const [messages, setMessages] = useState([])
  const [job, setJob] = useState(null)
  const [error, setError] = useState(null)
  const [statusError, setStatusError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [draft, setDraft] = useState(null)
  const [sending, setSending] = useState(null)   // 접수 응답 전까지 보여 줄 질문(15번 로딩)
  const [scrollTo, setScrollTo] = useState(null)  // 리포트 생성 중 위치로 이동할 요소 id
  const [drawer, setDrawer] = useState(false)   // 휴대폰 폭의 채팅방 목록 슬라이더
  const topRef = useRef(null)
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
  // 지금 보고 있는 채팅방. 응답이 오기 전에 방을 바꿨으면 그 응답으로 이 방의 작업 상태를 덮어쓰지 않는다
  // (덮어쓰면 B방의 중단 버튼이 A방 작업을 취소한다).
  const activeRef=useRef(activeId); activeRef.current=activeId
  const active = chats.find(c => c.id === activeId)
  useEffect(() => {
    let cancelled = false, timer, checkingStatus = false
    const controller = new AbortController()
    const alreadyAnswered = latest => latest?.status === 'NEEDS_INPUT' && acceptedClarifications.current.has(latest.clarification?.clarification_id)
    const showMessages = (rows, latest) => {
      setMessages(completedMessages(rows, latest))
      // 대화는 표시하되 이미 답한 확인 요청은 오래된 목록으로 되살리지 않는다.
      if (!alreadyAnswered(latest)) setJob(latest || null)
    }
    const checkStatus = async (rows, snapshot) => {
      if (cancelled || checkingStatus) return
      checkingStatus = true
      setStatusError(null)
      try {
        const latest = (await api(`/chat/jobs/${snapshot.job_id}`, { signal: controller.signal })).job
        if (cancelled) return
        showMessages(rows, latest)
        if (running(latest.status) || alreadyAnswered(latest)) timer = setTimeout(load, 2000)
      } catch (e) {
        if (!cancelled) setStatusError({ sessionId: activeId, revision: readRevision, error: e, retry: () => checkStatus(rows, snapshot) })
      } finally { checkingStatus = false }
    }
    const load = async () => {
      if (!activeId) return
      try {
        const rows = await allPages(`/chat/sessions/${activeId}/messages`)
        if (cancelled) return
        setError(null); setStatusError(null)
        const latest = [...rows].reverse().find(m => m.job)?.job
        // 상태 조회가 늦거나 실패해도 이미 받은 대화는 해당 작업과 함께 표시한다.
        showMessages(rows, latest)
        // The job endpoint also checks whether a pending checkpoint has expired.
        if (latest && (running(latest.status)||latest.status==='NEEDS_INPUT')) await checkStatus(rows, latest)
      } catch (e) { if (!cancelled) setError(e) }
    }
    load()
    return () => { cancelled = true; clearTimeout(timer); controller.abort() }
  }, [activeId, readRevision])
  useEffect(() => { endRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }) }, [messages.length])
  // 리포트 생성이 시작되면(채팅 승인 → WAITING_REPORT, 조건 폼 → 생성 접수) 생성 중 표시가 있는 곳으로 이동한다
  const waitingReport = job?.status === 'WAITING_REPORT' ? job.job_id : null
  const scrolledReport = useRef(null)   // 같은 작업의 폴링마다 다시 끌어오지 않는다
  useEffect(() => {
    const waiting = waitingReport && scrolledReport.current !== waitingReport
    const id = scrollTo || (waiting ? `job-progress-${waitingReport}` : null)
    if (!id) return
    const frame = requestAnimationFrame(() => {
      const target = document.getElementById(id)
      if (!target) return   // 아직 그려지지 않았다. 메시지를 다시 읽으면 다시 찾는다
      target.scrollIntoView({ block: 'center', behavior: 'smooth' })
      if (id === scrollTo) setScrollTo(null); else scrolledReport.current = waitingReport
    })
    return () => cancelAnimationFrame(frame)
  }, [scrollTo, waitingReport, messages])
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
    await refreshChats(); clearArticle(); setActiveId(data.session.session_id); setMessages([]); setJob(null); setDraft(null); setInput(''); setDrawer(false)
  })
  const clearArticle = () => {
    if (!articleId) return
    setArticleId(null)
    navigate('/analysis', { replace: true })
  }
  // newRoom: 기사 화면의 "AI에게 물어보기"처럼 새 채팅방을 만들어 바로 묻는다(23번)
  const send = async (text = input, newRoom = false, mode = null) => {
    if (!text.trim() || busy) return
    const input = text
    setBusy(true); setSending(input)
    let accepted = false
    await run(async () => {
      let id = newRoom ? null : activeId
      if (!id) { const data = await api('/chat/sessions', { method: 'POST', body: {} }); id = data.session.session_id; setActiveId(id) }
      if (pending.current?.text !== input || pending.current?.sessionId !== id || pending.current?.mode !== mode) {
        pending.current = { text: input, sessionId: id, mode, id: requestId() }
      }
      const data = await api(`/chat/sessions/${id}/messages`, { method: 'POST', body: {
        question: input, client_message_id: pending.current.id,
        ...(mode ? { article_action: { article_id: articleId, mode } } : {}),
      } })
      pending.current = null
      accepted = true
      if (activeRef.current!==id) { await refreshChats(); return }   // 응답 전에 방을 옮겼으면 그 방의 작업을 덮어쓰지 않는다
      clearArticle()
      setJob(data.job); setDraft(null); setInput(''); setSending(null)
      setReadRevision(v => v + 1); await refreshChats()
    })
    // 접수에 실패하면 질문을 입력창에 남겨 다시 보낼 수 있게 한다. 기사에서 온 질문은 주소에서 이미 지웠으므로 여기 말고는 남지 않는다
    if (!accepted && !mode) setInput(text)
    setBusy(false); setSending(null)
  }
  const askedFromLink = useRef(false)
  useEffect(() => {
    const question = route.query.get('ask')
    if (route.query.get('new') !== '1' || !question || askedFromLink.current) return
    askedFromLink.current = true
    navigate('/analysis', { replace: true })   // 새로고침해도 같은 질문을 다시 보내지 않는다
    send(question, true)
  }, [])   // eslint-disable-line react-hooks/exhaustive-deps -- 처음 열 때 한 번만
  const saveAsReport = (conditions, after) => { setDraft({ options: optionsFor(conditions), after }); setScrollTo('report-form') }
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
  // 지금 열린 방을 다시 누르면 내용을 비우지 않는다. 대화 다시 불러오기는 activeId가 바뀔 때만 돌기 때문이다.
  const openChat = id => {
    setDrawer(false)
    if (id === activeId) return
    clearArticle(); setActiveId(id); setMessages([]); setJob(null); setDraft(null)
  }
  const removeChat = () => run(async () => { await api(`/chat/sessions/${activeId}`, { method: 'DELETE' }); await refreshChats(); setActiveId(null); setMessages([]); setJob(null); setDraft(null); setModal(null); notify('채팅방을 삭제했습니다.') })
  const updateTitle = () => run(async () => { await api(`/chat/sessions/${activeId}`, { method: 'PATCH', body: { title: rename.trim() } }); await refreshChats(); setModal(null) })
  const clarify = async body => {
    const id=job?.clarification?.clarification_id
    if (!id || clarificationRequest.current || acceptedClarifications.current.has(id)) return
    clarificationRequest.current=id
    const room=activeId
    setBusy(true)
    try {
      const data = await run(() => api(`/chat/jobs/${job.job_id}/clarifications`, { method: 'POST', body: { clarification_id:id, ...body } }))
      if (data?.job && (data.job.status!=='NEEDS_INPUT' || data.job.clarification?.clarification_id!==id)) acceptedClarifications.current.add(id)
      if (activeRef.current!==room) return   // 다른 방으로 옮겼다. 그 방의 작업·입력을 바꾸지 않는다
      // 리포트 생성·취소는 버튼으로만 받는다(D-92). 서버가 직접 입력한 승인·거절을 무시했으면 버튼을 안내하고 입력은 남긴다
      const ignored = body.text && job.clarification.kind==='REPORT_CONFIRM' && data?.job?.status==='NEEDS_INPUT'
        && data.job.clarification?.clarification_id===id
      if (data?.job) { setJob(data.job); if (!ignored) setInput(''); setDraft(null) }
      if (ignored) notify('리포트 생성·취소는 아래 버튼으로 선택해 주세요.')
      setReadRevision(v => v + 1)
    } finally { clarificationRequest.current=null; setBusy(false) }
  }
  const renderMessage = m => {
    // 새 작업이 목록 조회보다 먼저 끝나도 이전 답변 카드에 그 작업의 스트림을 연결하지 않는다.
    if (m.role !== 'USER') return m.answer
      ? <AnswerCard key={m.message_id} message={m} streamJobId={m===messages.at(-1) && job?.status==='SUCCEEDED' && turnJobs.get(turnOf.get(m.message_id))===job.job_id ? job.job_id : undefined} onSaveReport={conditions => saveAsReport(conditions, turnOf.get(m.message_id))} onInterest={addInterest}/>
      : <AiBlock key={m.message_id}><p style={{ whiteSpace: 'pre-line' }}>{m.content_masked}</p></AiBlock>
    const turn = m.job?.job_id === job?.job_id ? job : m.job
    return <div className="chat-turn" key={m.message_id}>
      <UserBubble text={m.content_masked}/>
      {turn && !['CANCELLED','FAILED','EXPIRED'].includes(turn.status) && (running(turn.status) || turn.progress?.plan?.length>0) && <AiBlock id={`job-progress-${turn.job_id}`}><Progress job={turn}/></AiBlock>}
      {turn?.clarification_history?.filter(h=>h.id!==job?.clarification?.clarification_id).map(h=><div className="chat-turn" key={h.id}>
        <AiBlock><p style={{ whiteSpace: 'pre-line' }}>{h.message}</p></AiBlock>{h.answer && <UserBubble text={h.answer}/>}
      </div>)}
      {turn?.status === 'FAILED' && <AiBlock><ErrorNotice error={turn.error || new Error('분석에 실패했습니다.')} /></AiBlock>}
      {turn?.status === 'EXPIRED' && <AiBlock><p>답변 시간이 지났습니다. 새 질문을 입력해 주세요.</p></AiBlock>}
      {turn?.status === 'CANCELLED' && <AiBlock><p>질문을 중단했습니다. 새 질문을 입력할 수 있습니다.</p></AiBlock>}
    </div>
  }
  // 리포트 위치는 질문(USER 메시지 ID) 단위로 기억한다. 답변 직후 화면은 임시 답변 ID(completed-…)를 쓰므로
  // 답변 ID에 묶으면 다음 조회에서 위치를 잃는다. 리포트 칸은 그 질문 턴의 마지막 메시지 뒤에 둔다.
  const turnOf = new Map(), lastOfTurn = new Map(), turnJobs = new Map()
  messages.reduce((turn, m) => {
    const t = m.role === 'USER' ? m.message_id : turn
    if (m.role === 'USER') turnJobs.set(t, m.job?.job_id)
    turnOf.set(m.message_id, t); lastOfTurn.set(t, m.message_id)
    return t
  }, null)
  const reports = chatReports[activeId] || []
  const dismissReport = id => setChatReports(previous => ({ ...previous, [activeId]: (previous[activeId] || []).filter(r => r.report_id !== id) }))
  const reportCard = report => <ReportGeneration key={report.report_id} initial={report} onComplete={refreshReports} onDismiss={() => dismissReport(report.report_id)}/>
  // 답이 필요한 칸: 직접 입력이 되면 입력창, 버튼으로만 답하면 되묻기 카드
  const waiting = job?.status === 'NEEDS_INPUT' && !busy
  const waitInput = waiting && job.clarification?.allow_text
  const unplaced = reports.filter(r => !lastOfTurn.has(r.after))
  // 리포트 조건 폼은 "리포트로 저장"을 누른 답변 바로 아래에 연다(19번). 위치를 모르면 맨 아래
  const placedDraft = Boolean(draft?.after && lastOfTurn.has(draft.after))
  const conditionCard = draft && <ConditionCard id="report-form" message={draft} onClose={()=>setDraft(null)} onChange={setDraft} onGenerate={async (body, key) => {
                const data=await api('/reports',{method:'POST',body,idempotencyKey:key})
                setChatReports(previous=>({...previous,[activeId]:[...(previous[activeId]||[]).filter(r=>r.report_id!==data.job.report_id),{...data.job,after:draft.after}]}))
                setDraft(null); setScrollTo(`report-${data.job.report_id}`)
                await refreshReports()
              }}/>
  const headerTag = job ? [job.status==='SUCCEEDED'?'ok':'amber', ({ QUEUED: '분석 대기', RUNNING: '분석 중', WAITING_REPORT:'리포트 만드는 중', NEEDS_INPUT: '조건 확인', SUCCEEDED: '답변 완료', FAILED: '분석 실패', EXPIRED:'시간 초과', CANCELLED:'중단됨' })[job.status]] : null

  return (
    <>
      <Band image={bandAnalysis} imagePosition="center 60%"  title="AI챗봇" lead="국가·품목·기간을 자연어로 질문하고, 대화 내용으로 분석 리포트를 생성합니다." />
      <main className="page">
        <div className="wrap chat-lay">
          {drawer && <div className="chat-scrim" aria-hidden="true" onClick={() => setDrawer(false)} />}
          <aside id="chat-rooms" className={'card col chat-side' + (drawer ? ' open' : '')} style={{ gap: 10, padding: 18 }} aria-label="내 채팅방"
            onKeyDown={e => { if (e.key === 'Escape') setDrawer(false) }}>
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
                <button key={c.id} type="button" className="reset" onClick={() => openChat(c.id)} aria-current={on}
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

          <section ref={topRef} className="card" style={{ padding: 0, overflow: 'hidden', minWidth: 0 }}>
            <div className="between" style={{ padding: '18px 20px', borderBottom: '1px solid var(--line)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0 }}>
                <Btn className="chat-drawer-btn" aria-label="채팅방 목록 열기" aria-controls="chat-rooms" aria-expanded={drawer} onClick={() => setDrawer(true)}><Icon name="menu" /></Btn>
                <h2 className="t2">{active?.title || '새 채팅'}</h2>
              </div>
              {headerTag && <span className={'tag ' + headerTag[0]}>{headerTag[0] === 'ok' && <span className="dot ok" style={{ marginRight: 6 }} />}{headerTag[1]}</span>}
            </div>
            <div className="col chat-flow" style={{ gap: 18, padding: '24px 20px' }}>
              {articleId ? <AiBlock>
                <ErrorNotice error={selectedArticle.error} retry={selectedArticle.reload} />
                {selectedArticle.data?.article ? <>
                  <p>「{selectedArticle.data.article.title}」 기사에 대해 무엇을 도와드릴까요?</p>
                  <div className="chips">
                    {[
                      ['summary', '기사 요약', '기사 내용을 요약해 줘.'],
                      ['related', '유사 기사 검색', '유사한 다른 기사를 찾아줘.'],
                      ['both', '요약과 유사 기사 검색', '기사 내용을 요약하고 유사한 다른 기사도 찾아줘.'],
                    ].map(([mode, label, question]) =>
                      <Btn key={mode} disabled={busy || running(job?.status)}
                        onClick={() => send(`「${selectedArticle.data.article.title.slice(0, 200)}」 ${question}`, false, mode)}>
                        {label}
                      </Btn>)}
                  </div>
                </> : !selectedArticle.error && <p role="status">선택한 기사를 불러오고 있습니다.</p>}
              </AiBlock> : !messages.length && (
                <AiBlock>
                  <p style={{ fontSize: 17, fontWeight: 800 }}>무엇을 분석해 드릴까요?</p>
                  <p className="muted" style={{ marginTop: 6, fontSize: 14 }}>국가·품목·기간을 함께 말해 주세요. 예) “중국 메모리 수출 추이와 증가 이유를 알려줘”, “베트남 화장품 최근 6개월 리포트로 만들어줘”</p>
                </AiBlock>
              )}
              <ErrorNotice error={error} retry={() => setReadRevision(v => v + 1)} />
              {statusError && statusError.sessionId === activeId && statusError.revision === readRevision && <div>
                <p className="note">작업 상태를 확인하지 못했습니다. 표시된 대화 내역은 유지됩니다.</p>
                <ErrorNotice error={statusError.error} />
                <Btn onClick={statusError.retry}>작업 상태 다시 조회</Btn>
              </div>}
              {unplaced.map(reportCard)}
              {messages.map(m => <Fragment key={m.message_id}>{renderMessage(m)}
                {placedDraft && lastOfTurn.get(draft.after) === m.message_id && conditionCard}
                {reports.filter(r => r.after && lastOfTurn.get(r.after) === m.message_id).map(reportCard)}
              </Fragment>)}
              {sending && <div className="chat-turn"><UserBubble text={sending}/><AiBlock><p role="status">질문을 접수하고 있습니다 <span className="chat-loading-ring" aria-hidden="true"/></p></AiBlock></div>}
              {running(job?.status) && !messages.some(m=>m.job?.job_id===job.job_id) && <AiBlock id={`job-progress-${job.job_id}`}><Progress job={job}/></AiBlock>}
              {job?.status === 'NEEDS_INPUT' && <AiBlock className={'chat-clarification' + (waiting && !waitInput ? ' chat-wait' : '')}><PromptActions key={job.clarification.clarification_id} prompt={job.clarification} busy={busy} reply={clarify} onDirectInput={()=>inputRef.current?.focus()}/></AiBlock>}
              {(running(job?.status)||job?.status==='NEEDS_INPUT') && <Btn size="sm" disabled={busy} onClick={()=>setModal('stop')}>이 질문 중단</Btn>}
              {draft && !placedDraft && conditionCard}
              <div ref={endRef} />
              <form className={waitInput ? 'chat-wait' : undefined} onSubmit={e => { e.preventDefault(); if(job?.status==='NEEDS_INPUT') clarify({text:input.trim()}); else send() }} style={{ display: 'flex', gap: 10, alignItems: 'center', border: '1px solid #D5D9E0', borderRadius: 16, padding: '10px 10px 10px 18px', background: '#fff' }}>
                <input ref={inputRef} value={input} disabled={busy || running(job?.status) || (job?.status==='NEEDS_INPUT' && !job.clarification?.allow_text)} onChange={e => setInput(e.target.value)} aria-label="질문 입력" placeholder={job?.clarification?.kind==='COUNTRY'?'국가명 또는 국가 코드 입력':job?.status==='NEEDS_INPUT' ? '선택지를 고르거나 답을 직접 입력하세요.' : '분석할 내용을 질문하세요.'}
                  style={{ flex: 1, minWidth: 0, fontSize: 15, border: 0, outline: 0, font: 'inherit', color: 'var(--ink)', background: 'transparent' }} />
                <Btn type="submit" kind="red" iconRight="arrowRight" disabled={!input.trim() || busy || running(job?.status) || (job?.status==='NEEDS_INPUT' && !job.clarification?.allow_text)}>전송</Btn>
              </form>
            </div>
          </section>
          <div className="chat-jump" role="group" aria-label="대화 이동">
            <Btn aria-label="대화 맨 위로" onClick={() => topRef.current?.scrollIntoView({ block: 'start', behavior: 'smooth' })}><Icon name="chevronUp" /></Btn>
            <Btn aria-label="대화 맨 아래로" onClick={() => inputRef.current?.closest('form')?.scrollIntoView({ block: 'end', behavior: 'smooth' })}><Icon name="chevronDown" /></Btn>
          </div>
        </div>
      </main>

      <Modal open={modal === 'stop'} onClose={()=>setModal(null)} width={420} label="질문 중단"><h2>이 질문을 중단할까요?</h2><p>이미 접수된 리포트는 계속 생성됩니다.</p><div className="chips"><Btn onClick={()=>setModal(null)}>계속 진행</Btn><Btn kind="red" onClick={async()=>{setModal(null);setBusy(true);const data=await run(()=>api(`/chat/jobs/${job.job_id}/cancel`,{method:'POST'}));if(data?.job)setJob(data.job);setReadRevision(v=>v+1);setBusy(false)}}>중단</Btn></div></Modal>
      <Modal open={!!interest} onClose={()=>!savingInterest&&setInterest(null)} width={480} label="관심 목록 추가">
        <h2>관심 목록에 추가할 항목</h2><p className="note">저장할 국가·품목을 선택하세요. 품목은 HS6 단위로 등록됩니다.</p>
        <div className="col" style={{gap:12,maxHeight:320,overflowY:'auto',margin:'16px 0'}}>
          {interest?.countries.map(c=>{const saved=interest.savedCountries.has(c);return <Check key={c} disabled={savingInterest||saved} checked={saved||interest.country_codes.includes(c)} onChange={on=>setInterest({...interest,country_codes:on?[...interest.country_codes,c]:interest.country_codes.filter(v=>v!==c)})}>{countryName(c)}{saved&&' · 이미 등록됨'}</Check>})}
          {interest?.products.map(h=>{const saved=interest.savedProducts.has(h.hs_ver+h.hs_code);return <Check key={h.hs_ver+h.hs_code} disabled={savingInterest||saved} checked={saved||interest.hs_items.includes(h)} onChange={on=>setInterest({...interest,hs_items:on?[...interest.hs_items,h]:interest.hs_items.filter(v=>v!==h)})}>{productChoiceLabel(h.hs_code,h.name_ko)}{saved&&' · 이미 등록됨'}</Check>})}
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

