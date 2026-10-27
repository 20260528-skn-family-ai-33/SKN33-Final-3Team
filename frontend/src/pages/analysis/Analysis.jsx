// BS14 AI분석(대화·리포트 조건·생성 진행·일반 답변·표 보기) · BS15 리포트 생성 완료
import { useEffect, useRef, useState } from 'react'
import { REPORT_STAGES, useApp } from '../../store'
import { Band } from '../../components/layout'
import { Btn, Check, Modal, TextField } from '../../components/ui'
import Icon from '../../components/Icon'
import { LineChart } from '../../components/charts'
import { niceTicks } from '../../utils/chart'
import { productName } from '../../data/catalog'
import { ALL_SECTIONS } from '../../data/seed'
import { PERIOD_PRESETS, figures, parseQuestion, presetRange, range, reportParagraphs } from '../../data/analysis'
import { downloadDocx, downloadExcel } from '../../utils/exports'
import bandAnalysis from '../../assets/band-analysis.jpg'

const STATUS_TAG = {
  condition: ['amber', '조건 확인 중'],
  generating: ['red', '생성 중'],
  done: ['ok', '생성 완료'],
}
const HEADER_TAG = {
  condition: ['amber', '조건 선택'],
  generating: ['red', '리포트 생성 중'],
  done: ['ok', '리포트 생성 완료'],
}
const today = () => new Date().toISOString().slice(0, 10).replaceAll('-', '.')

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
  const o = message.options
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
            onClick={() => onChange({ ...message, preset: key, options: { ...o, ...presetRange(key) } })}>{label}</button>
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
        <Btn kind="red" size="md" icon="file" onClick={() => onGenerate({ ...o, sections: o.sections.length ? o.sections : ALL_SECTIONS })} disabled={o.from > o.to}>리포트 생성</Btn>
      </div>
      {o.from > o.to && <p className="field-err">시작 월이 종료 월보다 늦습니다.</p>}
    </AiBlock>
  )
}

/** 상태 2: 생성 진행 상태 */
function ProgressCard({ report, stage }) {
  return (
    <section className="card" style={{ borderColor: 'var(--line)' }} aria-live="polite">
      <div className="between">
        <p style={{ fontSize: 18, fontWeight: 800 }}>리포트를 생성하고 있습니다 <span className="tag red" style={{ marginLeft: 6 }}>작성 중 · {Math.min(stage + 1, REPORT_STAGES.length)} / {REPORT_STAGES.length}</span></p>
        <span className="muted">예상 남은 시간 약 {Math.max(1, REPORT_STAGES.length - stage)}초</span>
      </div>
      <div style={{ display: 'flex', gap: 10, marginTop: 20 }}>
        {REPORT_STAGES.map((s, i) => {
          const done = i < stage, on = i === stage
          return (
            <div key={s} style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 8, alignItems: 'flex-start' }}>
              <div style={{ height: 6, width: '100%', borderRadius: 3, background: done ? '#C8102E' : on ? '#15181E' : '#E3E6EA' }} />
              <span style={{ fontSize: 14, fontWeight: on ? 800 : 600, color: done || on ? '#15181E' : '#8A919C' }}>{done ? `✓ ${s}` : on ? `${s} · 진행 중` : s}</span>
            </div>
          )
        })}
      </div>
      <p className="muted" style={{ marginTop: 18 }}>화면을 이동해도 작업은 유지됩니다. 완료 상태와 결과는 채팅방 및 요청형 리포트 목록에서 확인합니다. {report && `(${report.title})`}</p>
    </section>
  )
}

/** BS15: 생성 완료 */
function DoneCard({ report }) {
  if (!report) return <AiBlock><p className="muted">리포트를 찾을 수 없습니다. 리포트 센터에서 확인해 주세요.</p></AiBlock>
  const o = report.options
  const f = figures(o)
  return (
    <AiBlock>
      <p style={{ fontSize: 17, fontWeight: 800 }}>분석 조건을 확인했습니다.</p>
      <p style={{ marginTop: 8, fontSize: 15 }}>국가: {o.country || '전체'} · 품목: {productName(o.product)}{o.product && `(${o.product})`} · 분석: {range(o.from, o.to)}</p>
      <p className="muted" style={{ marginTop: 4 }}>비교: {range(o.compareFrom, o.compareTo)} · 포함: {o.sections.map(s => s.replace('(KPI)', 'KPI').replace('주요 지표KPI', 'KPI').replace(' 기사', '').replace(' 시장', '')).join(' / ')}</p>
      <div style={{ marginTop: 18, borderRadius: 16, background: '#fff', border: '1px solid #F1A9B4', padding: '20px 22px', boxShadow: 'inset 4px 0 0 var(--red)' }}>
        <div style={{ display: 'flex', gap: 14, alignItems: 'center' }}>
          <span style={{ width: 48, height: 56, borderRadius: 8, background: 'var(--rose)', display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}><Icon name="file" size={24} color="#C8102E" stroke={1.8} /></span>
          <div style={{ flex: 1 }}>
            <p style={{ fontSize: 16, fontWeight: 800 }}>리포트 생성이 완료되었습니다</p>
            <p style={{ fontSize: 15, marginTop: 2 }}>{report.title}.pdf <span className="note">· {report.pages || 12}쪽 · {report.created}</span></p>
            <p className="note" style={{ color: 'var(--red-d)' }}>요청형 리포트 목록에 등록되었습니다. 조건·기준 기간·생성일이 함께 저장됩니다.</p>
          </div>
        </div>
        <div style={{ display: 'flex', gap: 10, marginTop: 16, flexWrap: 'wrap' }}>
          <Btn kind="red" icon="download" to={`/report?id=${report.id}&print=1`}>PDF 다운로드</Btn>
          <Btn kind="red" icon="download" onClick={() => downloadDocx(report.title, reportParagraphs(report))}>DOCX 다운로드</Btn>
          <Btn kind="ink" to={`/report?id=${report.id}`}>리포트 보기</Btn>
          <Btn to="/account/reports?tab=chat">요청형 리포트 목록</Btn>
          <Btn onClick={() => downloadExcel([['월', '수출액(억 USD)', '비교 기간 수출액(억 USD)'], ...f.labels.map((l, i) => [l, f.current[i], f.previous[i]])], `${report.title}_데이터`)}>데이터 엑셀 다운로드</Btn>
        </div>
      </div>
      <p className="note" style={{ marginTop: 12 }}>추가로 국가 비교, 기간 변경, 기사 근거 설명을 요청할 수 있습니다.</p>
    </AiBlock>
  )
}

/** 상태 3·4: 일반 AI 분석 답변 + 표 보기 */
function AnswerCard({ message, onSaveReport, onInterest }) {
  const [table, setTable] = useState(false)
  const o = message.options
  const f = figures(o)
  const ticks = niceTicks([...f.current, ...f.previous])
  const lastYear = Number(o.from.slice(0, 4))
  const excel = () => downloadExcel(
    [['월', `${lastYear}년 수출액`, `${lastYear - 1}년 수출액`, '증감률'], ...f.labels.map((l, i) => [l, f.current[i], f.previous[i], `${(((f.current[i] / f.previous[i]) - 1) * 100).toFixed(1)}%`])],
    `${o.country || '전체'}_${productName(o.product)}_월별수출`,
  )
  const article = f.news[0]
  return (
    <AiBlock>
      <p style={{ fontSize: 13, fontWeight: 700, color: 'var(--red-d)' }}>분석 조건 · {o.product ? `HS ${o.product}` : '전체 품목'} / {o.country || '전체 국가'}</p>
      <p className="note">{range(o.from, o.to)} / 비교 {range(o.compareFrom, o.compareTo)} · 단위 억 USD · 관세청</p>
      <div style={{ display: 'flex', gap: 14, marginTop: 16 }}>
        {[[`${f.labels.length}개월 수출액`, `${f.total}억 USD`, '#15181E'], ['전년 동기 대비', f.growthText, f.growth >= 0 ? '#C8102E' : '#1F5FD1'], ['월별 흐름', f.flow, '#15181E']].map(([l, v, c]) => (
          <div key={l} style={{ flex: 1, background: 'var(--bg)', borderRadius: 14, padding: '16px 18px' }}>
            <p className="muted">{l}</p>
            <p style={{ fontSize: 24, fontWeight: 800, marginTop: 4, color: c }}>{v}</p>
          </div>
        ))}
      </div>
      <div style={{ marginTop: 18, border: '1px solid var(--line)', borderRadius: 14, padding: '16px 18px' }}>
        <div className="between">
          <b>월별 수출 추이</b>
          <div style={{ display: 'flex', gap: 14, alignItems: 'center' }}>
            <span className="lg"><i style={{ background: '#C8102E' }} />{lastYear}년</span>
            <span className="lg"><i style={{ background: '#9A95BF' }} />{lastYear - 1}년</span>
            <Btn kind="lav" onClick={() => setTable(t => !t)} aria-pressed={table}>{table ? '차트로 보기' : '표로 보기'}</Btn>
          </div>
        </div>
        {table ? (
          <div style={{ marginTop: 14 }}>
            <div className="between">
              <p style={{ fontSize: 16, fontWeight: 800 }}>{o.country || '전체'} / {productName(o.product)} <span className="muted" style={{ fontWeight: 500 }}>· 단위: 억 USD · 출처: 관세청</span></p>
              <Btn kind="red" icon="download" onClick={excel}>엑셀 다운로드</Btn>
            </div>
            <div className="th" style={{ gridTemplateColumns: '1fr 1fr 1fr 1fr', marginTop: 16 }}><span>월</span><span className="r">{lastYear}년 수출액</span><span className="r">{lastYear - 1}년 수출액</span><span className="r">증감률</span></div>
            {f.labels.map((l, i) => {
              const g = ((f.current[i] / f.previous[i]) - 1) * 100
              return (
                <div key={l} className="tr" style={{ gridTemplateColumns: '1fr 1fr 1fr 1fr' }}>
                  <span className="b">{l}</span><span className="r b">{f.current[i].toFixed(1)}</span><span className="r s">{f.previous[i].toFixed(1)}</span>
                  <span className="r b" style={{ color: g >= 0 ? '#C8102E' : '#1F5FD1' }}>{g >= 0 ? '+' : ''}{g.toFixed(1)}%</span>
                </div>
              )
            })}
            <p className="note" style={{ marginTop: 12 }}>차트로 돌아가기 → 같은 결과 유지 · 다운로드는 숫자 형식과 표시 단위를 보존합니다.</p>
          </div>
        ) : (
          <LineChart labels={f.labels} yTicks={ticks} series={[{ color: '#C8102E', values: f.current }, { color: '#9A95BF', values: f.previous }]} />
        )}
      </div>
      <p style={{ fontSize: 15, fontWeight: 800, marginTop: 18 }}>증가 이유 · 기사 요약 <span className="tag">시연 예시</span></p>
      <p style={{ marginTop: 8, fontSize: 15, lineHeight: 1.7, color: '#2E333C' }}>
        기사에서는 {o.country || '주요 시장'}의 서버·전자제품 수요 확대가 {productName(o.product)} 수요를 높였다고 설명하며, 이를 수출 {f.growth >= 0 ? '증가' : '변화'}의 배경으로 제시합니다.
      </p>
      {article && <p className="note" style={{ marginTop: 6 }}>{article.date} · {article.country} · KOTRA 해외시장뉴스 · <a href={`#/articles?tab=${article.kind === 'general' ? 'all' : article.kind}&id=${article.id}`}>원문: {article.title} ↗</a></p>}
      <div style={{ display: 'flex', gap: 10, marginTop: 18 }}>
        <Btn kind="red" icon="file" onClick={() => onSaveReport(o)}>리포트로 저장</Btn>
        <Btn icon="star" onClick={() => onInterest(o)}>관심 목록 추가</Btn>
        <Btn icon="download" onClick={() => { setTable(true); excel() }}>표 보기·엑셀</Btn>
      </div>
    </AiBlock>
  )
}

export default function Analysis({ route }) {
  const { chats, setChats, reports, generateReport, reportStage, setProfile, notify } = useApp()
  const [activeId, setActiveId] = useState(route.query.get('chat') || chats[0]?.id)
  const [input, setInput] = useState(route.query.get('ask') || '')
  const [modal, setModal] = useState(null)
  const [rename, setRename] = useState('')
  const endRef = useRef(null)
  const active = chats.find(c => c.id === activeId) || chats[0]

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [active?.messages.length])

  const updateChat = (id, fn) => setChats(cs => cs.map(c => (c.id === id ? fn(c) : c)))
  const newChat = () => {
    const id = 'c' + Date.now().toString(36)
    setChats(cs => [{ id, title: '새 채팅', date: today(), status: 'new', messages: [] }, ...cs])
    setActiveId(id)
    setInput('')
  }
  const send = () => {
    const text = input.trim()
    if (!text) return
    let chat = active
    if (!chat) {
      const id = 'c' + Date.now().toString(36)
      chat = { id, title: '새 채팅', date: today(), status: 'new', messages: [] }
      setChats(cs => [chat, ...cs])
      setActiveId(id)
    }
    const previous = [...chat.messages].reverse().find(m => m.options)?.options || {}
    const parsed = parseQuestion(text, previous)
    const { wantsReport, preset, ...options } = parsed
    const aiMessage = wantsReport ? { role: 'ai', kind: 'condition', preset, options } : { role: 'ai', kind: 'answer', options }
    const title = chat.title === '새 채팅' ? `${options.country || '전체'} ${productName(options.product)} ${wantsReport ? '리포트' : '분석'}` : chat.title
    const status = wantsReport ? 'condition' : chat.status === 'new' ? 'answer' : chat.status
    setChats(cs => cs.map(c => (c.id === chat.id ? { ...c, title, status, date: today(), messages: [...c.messages, { role: 'user', text }, aiMessage] } : c)))
    setInput('')
  }
  const saveAsReport = options => updateChat(active.id, c => ({
    ...c, status: 'condition',
    messages: [...c.messages, { role: 'user', text: '이 분석을 리포트로 만들어줘.' }, { role: 'ai', kind: 'condition', preset: 'custom', options: { ...options, sections: ALL_SECTIONS } }],
  }))
  const addInterest = o => {
    setProfile(p => ({ ...p, products: o.product ? [...new Set([...p.products, o.product])] : p.products, countries: o.country ? [...new Set([...p.countries, o.country])] : p.countries }))
    notify(`관심 목록에 ${[o.country, o.product && productName(o.product)].filter(Boolean).join(' · ')}을(를) 추가했습니다.`)
  }
  const removeChat = () => {
    const rest = chats.filter(c => c.id !== active.id)
    setChats(rest)
    setActiveId(rest[0]?.id)
    setModal(null)
    notify('채팅방을 삭제했습니다. 생성된 리포트는 리포트 센터에 그대로 남습니다.')
  }

  const report = id => reports.find(r => r.id === id)
  const headerTag = HEADER_TAG[active?.status]

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
                <button key={c.id} type="button" className="reset" onClick={() => setActiveId(c.id)} aria-current={on}
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
              {!active?.messages.length && (
                <AiBlock>
                  <p style={{ fontSize: 17, fontWeight: 800 }}>무엇을 분석해 드릴까요?</p>
                  <p className="muted" style={{ marginTop: 6, fontSize: 14 }}>국가·품목·기간을 함께 말해 주세요. 예) “중국 메모리 수출 추이와 증가 이유를 알려줘”, “베트남 화장품 최근 6개월 리포트로 만들어줘”</p>
                </AiBlock>
              )}
              {active?.messages.map((m, i) => {
                if (m.role === 'user') return <UserBubble key={i} text={m.text} />
                if (m.kind === 'condition') {
                  return <ConditionCard key={i} message={m}
                    onChange={next => updateChat(active.id, c => ({ ...c, messages: c.messages.map((x, j) => (j === i ? next : x)) }))}
                    onGenerate={options => generateReport(active.id, options)} />
                }
                if (m.kind === 'progress') {
                  const r = report(m.reportId)
                  return <ProgressCard key={i} report={r} stage={reportStage(r)} />
                }
                if (m.kind === 'done') return <DoneCard key={i} report={report(m.reportId)} />
                if (m.kind === 'answer') return <AnswerCard key={i} message={m} onSaveReport={saveAsReport} onInterest={addInterest} />
                return <AiBlock key={i}><p>{m.text}</p></AiBlock>
              })}
              <div ref={endRef} />
              <form onSubmit={e => { e.preventDefault(); send() }} style={{ display: 'flex', gap: 10, alignItems: 'center', border: '1px solid #D5D9E0', borderRadius: 16, padding: '10px 10px 10px 18px', background: '#fff' }}>
                <input value={input} onChange={e => setInput(e.target.value)} aria-label="질문 입력" placeholder="질문하거나 “이 분석을 리포트로 만들어줘”라고 입력하세요."
                  style={{ flex: 1, fontSize: 15, border: 0, outline: 0, font: 'inherit', color: 'var(--ink)', background: 'transparent' }} />
                <Btn type="submit" kind="red" iconRight="arrowRight" disabled={!input.trim()}>전송</Btn>
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
          <Btn kind="red" size="md" disabled={!rename.trim()} onClick={() => { updateChat(active.id, c => ({ ...c, title: rename.trim() })); setModal(null) }}>저장</Btn>
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

