// BS21 리포트 열람·다운로드. 저장된 조건으로 그린 결과를 보여 주며 재계산하지 않는다.
import { useEffect, useState } from 'react'
import { Link } from '../../router'
import { useApp } from '../../store'
import { Band } from '../../components/layout'
import { Btn } from '../../components/ui'
import { LineChart } from '../../components/charts'
import { niceTicks } from '../../utils/chart'
import { countryName } from '../../data/catalog'
import { reportKinds } from '../../data/seed'
import { useQuery, ErrorNotice } from '../../api/hooks'
import { api } from '../../api/client'
import { amount, percent, mapReport, mapArticle, reportComparison } from '../../api/adapters'

const range = (from, to) => from && to ? `${from} ~ ${to}` : '—'

function Section({ no, title, action, children }) {
  return (
    <div style={{ marginTop: 36 }}>
      <div className="sec-h" style={{ borderBottomWidth: 1, borderColor: 'var(--line)' }}>
        <h3 style={{ fontSize: 20, fontWeight: 800 }}><span style={{ color: 'var(--red)' }}>{String(no).padStart(2, '0')}</span> {title}</h3>
        {action}
      </div>
      <div style={{ marginTop: 16 }}>{children}</div>
    </div>
  )
}

export default function ReportView({ route }) {
  const { downloadReport, refreshReports } = useApp()
  const id = route.query.get('id')
  const [table, setTable] = useState({ trend: false, countries: false })
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
  const storedReport = content.data?.report, sections = content.data?.sections || []
  const report = storedReport && mapReport(storedReport)
  const c = report?.conditions
  const status = { QUEUED: '생성 대기', RUNNING: '생성 중', DELAYED: '재시도 대기', FAILED: '생성 실패' }

  if (!report) return <>
    <Band tone="plum" eyebrow="REPORT" title="리포트 열람" lead="저장된 결과를 열람하고 생성 당시의 PDF·DOCX 파일을 다운로드합니다. 재계산하지 않습니다." />
    <main className="page"><div className="wrap">
      <div className="card" style={{ maxWidth: 640, margin: '0 auto', textAlign: 'center' }}>
        <ErrorNotice error={error || content.error || job?.error} retry={() => { setRetry(n => n + 1); content.reload() }} />
        {!error && !content.error && <p className="t2" role="status">{status[job?.status] || '저장된 리포트를 불러오는 중…'}</p>}
        <p className="muted" style={{ marginTop: 8 }}>완료 전에는 열람·다운로드할 수 없습니다. 완료되면 리포트 센터에서 확인할 수 있습니다.</p>
        <Btn to="/account/reports" kind="red" size="md" style={{ marginTop: 20 }}>리포트 센터로</Btn>
      </div>
    </div></main>
  </>

  // 기존 화면의 표시 모델에 저장된 서버 결과만 연결한다.
  const byCode = Object.fromEntries(sections.map(section => [section.section_code, section]))
  const kpi = byCode.KPI?.kpi || {}
  const comparison = reportComparison(c, kpi)
  const monthly = byCode.KPI?.tables?.find(t => t.columns.some(col => col.key === 'month'))?.rows || []
  const countryRows = byCode.TOP_COUNTRIES?.tables?.[0]?.rows || []
  const sectionCodes = { '주요 지표(KPI)': 'KPI', '주요 수출국': 'TOP_COUNTRIES', '급성장·리스크 시장': 'GROWTH_RISK', '현지 이슈 기사': 'LOCAL_ISSUES' }
  const o = {
    country: (c.country_codes || []).map(countryName).join(' · '), product: (c.hs_items || []).map(h => h.hs_code).join(', '),
    from: c.period?.start, to: c.period?.end, compareFrom: c.compare_period?.start, compareTo: c.compare_period?.end,
    sections: Object.keys(sectionCodes).filter(label => byCode[sectionCodes[label]]),
  }
  const f = {
    total: amount(kpi.amount_usd), growth: comparison.growth, growthText: percent(comparison.growth),
    labels: monthly.map(r => r.month), current: monthly.map(r => r.amount_usd / 1e8),
    countryRows: countryRows.map(r => ({ name: r.country, exp: r.amount_usd, share: r.share_pct, growth: r.yoy_pct })),
    news: (byCode.LOCAL_ISSUES?.articles || byCode.NEWS?.articles || []).map(mapArticle),
  }
  const kind = reportKinds[report.kind]
  const newsOnly = report.kind === 'weekly'
  const has = label => !!byCode[sectionCodes[label]]
  const ticks = niceTicks(monthly.map(r => r.amount_usd), 3, true).map(value => value / 1e8)
  let no = 1

  return (
    <>
      <Band
        tone="plum" eyebrow="REPORT" title="리포트 열람" lead="저장된 결과를 열람하고 생성 당시의 PDF·DOCX 파일을 다운로드합니다. 재계산하지 않습니다."
        right={(
          <div style={{ display: 'flex', gap: 8 }} className="no-print">
            <Btn icon="download" onClick={() => downloadReport(id, 'XLSX')}>엑셀</Btn>
            <Btn kind="red" icon="download" disabled={!report.available_formats?.includes('PDF')} onClick={() => downloadReport(id, 'PDF')}>PDF</Btn>
            <Btn icon="download" disabled={!report.available_formats?.includes('DOCX')} onClick={() => downloadReport(id, 'DOCX')}>DOCX</Btn>
          </div>
        )}
      />
      <main className="page">
        <div className="wrap">
          <Btn to={`/account/reports?tab=${report.kind}`} icon="arrowLeft" className="no-print" style={{ marginBottom: 20 }}>리포트 센터</Btn>
          <article className="print-sheet" style={{ background: '#fff', borderRadius: 6, boxShadow: '0 20px 50px rgba(21,24,30,.12)', maxWidth: 1000, margin: '0 auto', overflow: 'hidden' }}>
            <div style={{ background: 'var(--plum)', color: '#fff', padding: '36px 48px 30px', position: 'relative' }}>
              <p style={{ fontSize: 13, fontWeight: 800, letterSpacing: 1, color: '#FF8A9A' }}>BORDERLESS SELLERS · {kind.eyebrow}</p>
              <h1 style={{ fontSize: 34, fontWeight: 800, marginTop: 10 }}>{report.title}</h1>
              <p style={{ marginTop: 10, color: '#D6D3E6', fontSize: 15 }}>{o.sections.length === 4 ? '전체 항목 선택' : `${o.sections.length}개 항목 선택`} · {o.product ? `HS6 ${o.product}` : '전체 품목'} · 국가: {o.country || '전체'}</p>
              <div style={{ display: 'flex', gap: 24, marginTop: 18, fontSize: 14, color: '#fff' }}>
                <span>분석 {range(o.from, o.to)}</span><span>비교 {range(o.compareFrom, o.compareTo)}</span><span>생성일 {report.created}</span>
              </div>
              <div style={{ position: 'absolute', right: 48, top: 36, width: 110, height: 6, background: '#C8102E' }} />
            </div>
            <div style={{ padding: '8px 48px 44px' }}>
              {byCode.SUMMARY && <Section no={no++} title="핵심 요약">
                <ul style={{ margin: 0, paddingLeft: 20, fontSize: 16, lineHeight: 1.9, color: '#2E333C' }}>
                  <li>{byCode.SUMMARY.text}</li>
                </ul>
              </Section>}

              {has('주요 지표(KPI)') && (
                <Section no={no++} title="주요 지표">
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 12 }}>
                    {[['기간 수출액', `${f.total}억 USD`, '#15181E'], [comparison.label, f.growthText, f.growth >= 0 ? '#C8102E' : '#1F5FD1'], ['수출국 수', `${kpi.export_country_count ?? '—'}개국`, '#15181E'], ['상위 1개국 비중', kpi.top_country_share_pct == null ? '—' : `${Number(kpi.top_country_share_pct).toFixed(1)}%`, '#15181E']].map(([l, v, c]) => (
                      <div key={l} style={{ background: 'var(--bg)', borderRadius: 14, padding: '18px 20px' }}>
                        <p className="muted">{l}</p>
                        <p style={{ fontSize: 26, fontWeight: 800, marginTop: 6, color: c }}>{v}</p>
                      </div>
                    ))}
                  </div>
                  <p className="note" style={{ marginTop: 10 }}>금액 단위: 억 USD · {comparison.label}: 서버에서 제공한 증감률만 표시</p>
                  <div style={{ marginTop: 22, border: '1px solid var(--line)', borderRadius: 14, padding: '18px 20px' }}>
                    <div className="between">
                      <b>월별 {f.labels.length}개월 수출 추이{report.kind === 'quarterly' ? ' · 분기 리포트' : ''}</b>
                      <div style={{ display: 'flex', gap: 14, alignItems: 'center' }}>
                        <span className="lg"><i style={{ background: '#C8102E' }} />분석 기간</span>
                        <Btn kind="lav" className="no-print" onClick={() => setTable(t => ({ ...t, trend: !t.trend }))}>{table.trend ? '차트로 보기' : '표로 보기'}</Btn>
                      </div>
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 10, marginTop: 14 }}>
                      {[['분석 기간 합계', `${f.total}억 USD`, '#15181E'], ['비교 기간 합계', amount(comparison.previous) + (comparison.previous == null ? '' : '억 USD'), '#15181E'], [comparison.label, f.growthText, f.growth >= 0 ? '#C8102E' : '#1F5FD1']].map(([l, v, c]) => (
                        <div key={l} style={{ border: '1px solid var(--line)', borderRadius: 12, padding: '14px 16px' }}>
                          <p className="note">{l}</p><p style={{ fontSize: 18, fontWeight: 800, color: c }}>{v}</p>
                        </div>
                      ))}
                    </div>
                    {table.trend ? (
                      <div style={{ marginTop: 14 }}>
                        <div className="th" style={{ gridTemplateColumns: '1fr 1fr 1fr' }}><span>월</span><span className="r">분석 기간</span><span className="r">비교 기간</span></div>
                        {f.labels.map((l, i) => <div key={l} className="tr" style={{ gridTemplateColumns: '1fr 1fr 1fr' }}><span className="b">{l}</span><span className="r b">{amount(monthly[i].amount_usd)}</span><span className="r s">—</span></div>)}
                      </div>
                    ) : f.labels.length ? (
                      <LineChart width={900} height={240} labels={f.labels} yTicks={ticks} series={[{ color: '#C8102E', values: f.current }]} />
                    ) : <p className="muted">조회된 월별 실적이 없습니다.</p>}
                    <p className="note">비교 기간의 월별 수출액은 현재 응답에 포함되지 않아 표시하지 않습니다. 비교 합계는 저장된 값이 있을 때 표시합니다.</p>
                  </div>
                </Section>
              )}

              {has('주요 수출국') && (
                <Section no={no++} title="주요 수출국" action={<Btn kind="lav" className="no-print" onClick={() => setTable(t => ({ ...t, countries: !t.countries }))}>{table.countries ? '막대로 보기' : '표로 보기'}</Btn>}>
                  <div className="th" style={{ gridTemplateColumns: '1fr 2fr 1fr 1fr' }}><span>국가</span><span>수출액</span><span className="r">비중</span><span className="r">전년 대비</span></div>
                  {f.countryRows.map(r => (
                    <div key={r.name} className="tr" style={{ gridTemplateColumns: '1fr 2fr 1fr 1fr' }}>
                      <span className="b">{r.name}</span>
                      {table.countries
                        ? <span className="b">{amount(r.exp)}억 USD</span>
                        : <span style={{ display: 'flex', alignItems: 'center', gap: 12 }}><span style={{ width: `${Math.min(65, Math.max(0, (r.share || 0) * .65))}%`, height: 12, borderRadius: 6, background: '#C8102E' }} /><b style={{ whiteSpace: 'nowrap' }}>{amount(r.exp)}억</b></span>}
                      <span className="r b">{r.share == null ? '—' : `${Number(r.share).toFixed(1)}%`}</span>
                      <span className="r b" style={{ color: r.growth >= 0 ? '#C8102E' : '#1F5FD1' }}>{percent(r.growth)}</span>
                    </div>
                  ))}
                  {!f.countryRows.length && <p className="muted">조회된 국가별 실적이 없습니다.</p>}
                  <p className="note" style={{ marginTop: 10 }}>국가별 전년 대비는 서버 응답이 없으면 —로 표시합니다. 조회 범위 내 수출액 상위 최대 10개국{c.country_codes?.length === 1 ? ' · 국가 1개 선택으로 1개만 표시' : ''}</p>
                </Section>
              )}

              {has('급성장·리스크 시장') && (
                <Section no={no++} title="급성장 · 리스크 시장">
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
                    <div style={{ borderRadius: 14, padding: 20, background: 'var(--rose)' }}>
                      <p style={{ fontSize: 13, fontWeight: 800, color: 'var(--red-d)' }}>성장률 상위</p>
                      <p style={{ fontSize: 22, fontWeight: 800, marginTop: 6 }}>준비 중</p>
                      <p className="note">{byCode.GROWTH_RISK?.text}</p>
                    </div>
                    <div style={{ borderRadius: 14, padding: 20, background: '#EEF3FC' }}>
                      <p style={{ fontSize: 13, fontWeight: 800, color: '#1F5FD1' }}>감소율 상위</p>
                      <p style={{ fontSize: 22, fontWeight: 800, marginTop: 6 }}>준비 중</p>
                      <p className="note">{byCode.GROWTH_RISK?.text}</p>
                    </div>
                  </div>
                  <p className="note" style={{ marginTop: 10 }}>성장·리스크 순위는 서버에서 제공된 결과가 있을 때 표시합니다.</p>
                </Section>
              )}

              {(newsOnly || o.sections.includes('현지 이슈 기사')) && (
                <Section no={no++} title="현지 이슈">
                  <p className="note" style={{ marginBottom: 10 }}>{byCode.LOCAL_ISSUES?.text || byCode.NEWS?.text}</p>
                  <div className="col" style={{ gap: 10 }}>
                    {(f.news.length ? f.news : []).map(a => (
                      <div key={a.id} className="li" style={{ padding: '16px 20px' }}>
                        <div style={{ flex: 1 }}>
                          <p style={{ fontSize: 16, fontWeight: 800 }}>{a.title}</p>
                          <p className="muted" style={{ marginTop: 4 }}>{a.summary}</p>
                          <p className="note">{a.date} · {a.country} · {a.source}</p>
                        </div>
                        <Link to={`/articles?tab=${a.kind === 'general' ? 'all' : a.kind}&id=${a.id}`} className="more">기사 보기 ↗</Link>
                      </div>
                    ))}
                    {!f.news.length && <p className="muted">해당 국가·품목·기간에 맞는 기사가 없습니다.</p>}
                  </div>
                </Section>
              )}

              <div style={{ marginTop: 36, background: 'var(--bg)', borderRadius: 14, padding: '20px 22px' }}>
                <p style={{ fontSize: 16, fontWeight: 800 }}>출처</p>
                <ul className="muted" style={{ margin: '10px 0 0', paddingLeft: 18, fontSize: 14, lineHeight: 1.9 }}>
                  {(content.data.meta?.sources || []).map(source => <li key={source.code}>{source.label || source.code}</li>)}
                  {(content.data.meta?.warnings || []).map(warning => <li key={warning}>{warning}</li>)}
                </ul>
              </div>
            </div>
          </article>
        </div>
      </main>
    </>
  )
}
