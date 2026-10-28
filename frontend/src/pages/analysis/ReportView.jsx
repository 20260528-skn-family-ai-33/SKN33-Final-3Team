// BS21: render stored server sections inside the original report sheet.
import { useEffect, useState } from 'react'
import { Link } from '../../router'
import { useApp } from '../../store'
import { Band } from '../../components/layout'
import { Btn } from '../../components/ui'
import ApiResults from '../../components/ApiResults'
import { useQuery, ErrorNotice } from '../../api/hooks'
import { api } from '../../api/client'
import { amount, percent } from '../../api/adapters'
import { countryName, productName } from '../../data/catalog'

function Section({ no, title, children }) {
  return <div style={{ marginTop: 36 }}><div className="sec-h" style={{ borderBottomWidth: 1, borderColor: 'var(--line)' }}><h3 style={{ fontSize: 20, fontWeight: 800 }}><span style={{ color: 'var(--red)' }}>{String(no).padStart(2, '0')}</span> {title}</h3></div><div style={{ marginTop: 16 }}>{children}</div></div>
}
export default function ReportView({ route }) {
  const { downloadReport, refreshReports } = useApp()
  const id = route.query.get('id')
  const [state, setState] = useState(null), [retry, setRetry] = useState(0)
  const job = state?.id === id ? state.job : null
  const error = state?.id === id ? state.error : null
  useEffect(() => {
    const controller = new AbortController(); let timer
    const poll = async () => {
      try {
        const data = await api(`/reports/${id}/status`, { signal: controller.signal })
        if (controller.signal.aborted) return
        setState({ id, job: data.job, error: null })
        if (!['READY', 'FAILED'].includes(data.job.status)) timer = setTimeout(poll, Math.max(2, data.job.retry_after_seconds || 2) * 1000)
        else await refreshReports()
      } catch (error) { if (!controller.signal.aborted) setState(previous => ({ id, job: previous?.id === id ? previous.job : null, error })) }
    }
    poll(); return () => { controller.abort(); clearTimeout(timer) }
  }, [id, retry, refreshReports])
  const content = useQuery(`/reports/${id}`, {}, job?.status === 'READY')
  const report = content.data?.report, sections = content.data?.sections || []
  const c = report?.conditions
  const status = { QUEUED: '생성 대기', RUNNING: '생성 중', DELAYED: '재시도 대기', FAILED: '생성 실패' }
  return <>
    <Band tone="plum" eyebrow="REPORT" title="리포트 열람" lead="저장된 결과를 열람하고 생성 당시의 PDF·DOCX 파일을 다운로드합니다. 재계산하지 않습니다."
      right={report && <div style={{ display: 'flex', gap: 8 }} className="no-print"><Btn icon="download" onClick={() => downloadReport(id, 'XLSX')}>엑셀</Btn>{['PDF', 'DOCX'].filter(f => report.available_formats.includes(f)).map(f => <Btn key={f} kind={f === 'PDF' ? 'red' : 'line'} icon="download" onClick={() => downloadReport(id, f)}>{f}</Btn>)}</div>} />
    <main className="page"><div className="wrap">
      <Btn to="/account/reports" icon="arrowLeft" className="no-print" style={{ marginBottom: 20 }}>리포트 센터</Btn>
      <ErrorNotice error={error || content.error} retry={() => { setRetry(n => n + 1); content.reload() }} />
      {!report && !error && !content.error && <div className="card" style={{ maxWidth: 640, margin: '0 auto', textAlign: 'center' }}><p className="t2" role="status">{status[job?.status] || '저장된 리포트를 불러오는 중…'}</p><p className="muted" style={{ marginTop: 8 }}>다른 화면으로 이동해도 서버에서 작업이 계속됩니다. 완료되면 리포트 센터에서 확인할 수 있습니다.</p><ErrorNotice error={job?.error} /></div>}
      {report && <article className="print-sheet" style={{ background: '#fff', borderRadius: 6, boxShadow: '0 20px 50px rgba(21,24,30,.12)', maxWidth: 1000, margin: '0 auto', overflow: 'hidden' }}>
        <div style={{ background: 'var(--plum)', color: '#fff', padding: '36px 48px 30px', position: 'relative' }}>
          <p style={{ fontSize: 13, fontWeight: 800, letterSpacing: 1, color: '#FF8A9A' }}>BORDERLESS SELLERS · REPORT</p>
          <h1 style={{ fontSize: 34, fontWeight: 800, marginTop: 10 }}>{report.title}</h1>
          <p style={{ marginTop: 10, color: '#D6D3E6', fontSize: 15 }}>{c.hs_items.map(h => `${productName(h.hs_code)} (${h.hs_code})`).join(' · ') || '전체 품목'} · {c.country_codes.map(countryName).join(' · ') || '전체 국가'}</p>
          <div style={{ display: 'flex', gap: 24, marginTop: 18, fontSize: 14, color: '#fff' }}><span>분석 {c.period.start} ~ {c.period.end}</span><span>생성일 {report.created_at}</span></div>
          <div style={{ position: 'absolute', right: 48, top: 36, width: 110, height: 6, background: '#C8102E' }} />
        </div>
        <div style={{ padding: '8px 48px 44px' }}>
          {sections.map((s, i) => <Section key={s.section_code} no={i + 1} title={s.title}>
            <p style={{ whiteSpace: 'pre-wrap', fontSize: 16, lineHeight: 1.9 }}>{s.text}</p>
            {s.kpi && <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 12, marginTop: 16 }}>{[['기간 수출액', `${amount(s.kpi.amount_usd)}억 USD`], ['전년 대비', percent(s.kpi.yoy_pct)], ['수출국 수', `${s.kpi.export_country_count ?? '—'}개국`], ['상위 1개국 비중', percent(s.kpi.top_country_share_pct)]].map(([label, value]) => <div key={label} style={{ background: 'var(--bg)', borderRadius: 14, padding: '18px 20px' }}><p className="muted">{label}</p><p style={{ fontSize: 26, fontWeight: 800, marginTop: 6 }}>{value}</p></div>)}</div>}
            {s.tables.filter(t => !s.kpi || t.rows.some(r => r.month)).map((t, n) => <ApiResults key={n} table={t} />)}
            <div className="col" style={{ gap: 10, marginTop: 16 }}>{s.articles.map(a => <div key={a.article_id} className="li" style={{ padding: '16px 20px' }}><div style={{ flex: 1 }}><p style={{ fontSize: 16, fontWeight: 800 }}>{a.title}</p><p className="muted" style={{ marginTop: 4 }}>{a.summary}</p><p className="note">{a.published_date} · {a.source_code}</p></div><Link to={`/articles?id=${a.article_id}`} className="more">기사 보기 ↗</Link></div>)}</div>
          </Section>)}
          <div style={{ marginTop: 36, background: 'var(--bg)', borderRadius: 14, padding: '20px 22px' }}><p style={{ fontSize: 16, fontWeight: 800 }}>출처</p><p className="muted">{content.data.meta.sources.map(s => s.label || s.code).join(' · ')}</p>{content.data.meta.warnings?.map(w => <p className="note" key={w}>{w}</p>)}</div>
        </div>
      </article>}
    </div></main>
  </>
}
