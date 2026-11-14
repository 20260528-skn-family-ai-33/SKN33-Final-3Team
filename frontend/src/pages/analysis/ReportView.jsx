// BS21 리포트 열람·다운로드. 저장된 조건으로 그린 결과를 보여 주며 재계산하지 않는다.
import { useEffect, useState } from 'react'
import { Link } from '../../router'
import { useApp } from '../../store'
import { Band } from '../../components/layout'
import { Btn } from '../../components/ui'
import { LineChart } from '../../components/charts'
import ApiResults from '../../components/ApiResults'
import { niceTicks } from '../../utils/chart'
// 리포트 화면의 금액(억 USD)·비율은 PDF 리포트와 같이 소수 2자리로 쓴다(2026-11-06 사용자 결정)
// 단, 0.1억 미만 금액은 PDF와 같이 유효숫자 1자리로 보여 준다(0.03억·0.0005억).
const two = value => Number(value).toLocaleString('ko-KR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
// 실적 금액 0은 그 기간에 실적이 없다는 뜻이라 —로 쓴다(메인·순위와 같은 규칙). 그래프 눈금의 0은 reportEok로 그대로 둔다.
const amount = value => value == null || Number(value) === 0 ? '—' : reportEok(Number(value) / 1e8)
const withUnit = (value, unit) => { const text = amount(value); return text === '—' ? text : text + unit }
const percent = value => value == null ? '—' : `${Number(value) > 0 ? '+' : ''}${two(value)}%`
const share = value => value == null ? '—' : `${two(value)}%`
import { countryName } from '../../data/catalog'
import { reportKinds } from '../../data/seed'
import { useQuery, ErrorNotice } from '../../api/hooks'
import { api } from '../../api/client'
import { mapReport, mapArticle, reportComparison, kstDateTimeText, reportEok } from '../../api/adapters'
import { promisingView } from './promising'

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

const SERIES_COLORS = ['#1e293a', '#2E6DB4', '#16877C', '#8E5BB5', '#E07B24']

/** 월별 추이 결과표를 국가·품목별 선으로 나눈다(PDF 결과 기반 모드와 같은 기준: 시리즈 5개·월 60개 이하). 아니면 null. */
function trendChart(table) {
  // 월별 금액(amount_usd)만 쓴다. 기간 합계(total_usd)나 비교 기준월(compare_month)로 선을 만들거나 나누지 않는다.
  const keys = table.columns.map(c => c.key), amount = 'amount_usd'
  if (table.kind !== 'TREND' || !keys.includes(amount) || !keys.includes('month')) return null
  const names = ['country_name', 'name'].filter(k => keys.includes(k))
  const labels = [...new Set(table.rows.map(r => r.month).filter(Boolean))].sort()
  // 선은 국가 코드·HS 코드·판본으로 나눈다(PDF와 같은 기준). 이름이 같은 품목(0409·040900 '천연꿀')을 한 선으로 합치지 않는다.
  const groups = new Map()
  for (const r of table.rows) {
    const codes = [r.country_code || r.country_name, r.hs_code || r.name, r.hs_ver]   // 코드가 없는 예전 결과는 이름으로 대신
    const key = JSON.stringify(codes)
    if (!groups.has(key)) groups.set(key, { codes, name: names.map(k => r[k]).filter(Boolean).join(' · ') || '수출액', values: {} })
    groups.get(key).values[r.month] = r[amount]
  }
  if (!labels.length || labels.length > 60 || groups.size > SERIES_COLORS.length) return null
  const list = [...groups.values()], base = list.map(g => g.name)
  // 이름이 겹치는 선만 HS 코드를 붙이고, 그래도 겹치면 판본·국가 코드까지 붙인다.
  const label = detail => list.map((g, i) => base.filter(n => n === base[i]).length === 1 ? base[i]
    : `${base[i]} (${detail.map(k => g.codes[k] && (k === 1 ? `HS ${g.codes[k]}` : g.codes[k])).filter(Boolean).join(' · ')})`)
  let shown = label([1])
  if (new Set(shown).size !== shown.length) shown = label([1, 2, 0])
  const series = list.map((g, i) => ({ name: shown[i], color: SERIES_COLORS[i], values: labels.map(m => g.values[m] == null ? null : g.values[m] / 1e8) }))
  return { labels, series, ticks: niceTicks(table.rows.map(r => r[amount]), 3, true).map(v => v / 1e8) }
}

const RESULT_ROW_LIMIT = 200   // PDF 결과 기반 모드(report_ondemand_context.RESULT_ROW_LIMIT)와 같은 값

/** 승인한 채팅 결과로 만든 리포트(D-90). 결과표 사본을 그대로 보여 주고, 결과표에 없는 섹션은 저장된 안내('해당 분석 결과에 포함되지 않음')를 쓴다.
 *  원본 결과는 만료될 수 있으므로 결과 API(페이지·엑셀)를 부르지 않고 이 리포트의 엑셀 파일로 내려받는다. */
function ResultSections({ sections, onDownload }) {
  return sections.map((section, index) => (
    <Section key={section.section_code} no={index + 1} title={section.title}>
      <p className="note" style={{ whiteSpace: 'pre-line' }}>{section.text}</p>
      {(section.tables || []).map(table => {
        const rows = table.rows || []
        const chart = trendChart({ ...table, rows })   // 그래프는 PDF와 같이 결과표 전체 행으로 그린다
        // 표는 PDF와 같은 상한(RESULT_ROW_LIMIT행)까지만 그리고, 나머지는 이 리포트의 엑셀로 안내한다.
        const total = table.total_rows ?? rows.length
        const shown = rows.slice(0, RESULT_ROW_LIMIT)
        return (
          <div key={table.result_id || table.kind}>
            {chart && <div style={{ marginTop: 14 }}>
              <p className="muted">월별 수출액 · 단위 억 USD</p>
              <LineChart width={900} height={240} labels={chart.labels} yTicks={chart.ticks} formatValue={reportEok} series={chart.series} />
              <div className="chips">{chart.series.map(s => <span key={s.name} className="lg"><i style={{ background: s.color }} />{s.name}</span>)}</div>
            </div>}
            <ApiResults table={{ ...table, rows: shown, total_rows: shown.length, result_id: undefined }} tableOnly fixedColumns onDownload={onDownload} />
            <p className="note" style={{ marginTop: 8 }}>{`전체 ${total.toLocaleString()}행` + (total > shown.length ? ` 중 ${shown.length.toLocaleString()}행 표시 · 전체는 엑셀 파일` : '')}</p>
          </div>
        )
      })}
    </Section>
  ))
}

const PROMISING_COLS = '110px 1fr 1.6fr 100px 150px'
const CHANGE_COLS = '1.3fr 1fr 1fr 1fr 1.1fr'
const UP = '#1e293a', DOWN = '#C0392B'   // PDF(report_templates.UP·DOWN)와 같은 색
const signedEok = value => (value > 0 ? '+' : value < 0 ? '−' : '') + reportEok(Math.abs(Number(value)) / 1e8)

/** 유망시장 위쪽의 실제 수출 증감(PDF와 같은 기준): 성장률 상위·감소율 상위 표와 국가별 증감액 막대. 비교 기간이 없으면 안내만 한다. */
function CountryChanges({ changes }) {
  if (!changes) return null   // 이 기능 전에 만든 리포트(저장 문서에 changes 없음)는 예전처럼 ML 박스만 보인다
  if (changes.status !== 'READY') {
    const text = changes?.status === 'NO_COMPARE' ? '비교 기간을 선택하면 국가별 증감을 표시합니다.' : '비교 기간 통계가 아직 준비되지 않았습니다.'
    return <><p style={{ fontSize: 15, fontWeight: 800 }}>실제 수출 증감</p><p className="muted" style={{ marginTop: 6 }}>{text}</p></>
  }
  // 비교 기간 날짜는 표시하지 않는다(#95). 열·제목은 '비교 기간'·'분석 기간'으로 구분한다
  const peak = Math.max(1, ...changes.bars.map(r => Math.abs(r.change_usd)))
  const table = (title, items) => (
    <div style={{ minWidth: 0 }}>
      <p style={{ fontSize: 15, fontWeight: 800, marginBottom: 8 }}>{title} {items.length}개국 <span className="muted" style={{ fontSize: 13, fontWeight: 400 }}>비교 기간 → 분석 기간</span></p>
      {items.length ? <>
        <div className="th" style={{ gridTemplateColumns: CHANGE_COLS }}><span>국가</span><span className="r">비교 기간 수출액</span><span className="r">분석 기간 수출액</span><span className="r">증감액</span><span className="r">증감률</span></div>
        {items.map(r => (
          <div key={r.country_code} className="tr" style={{ gridTemplateColumns: CHANGE_COLS }}>
            <span className="b">{r.country}</span><span className="r">{amount(r.previous_usd)}</span><span className="r">{amount(r.amount_usd)}</span>
            <span className="r">{signedEok(r.change_usd)}</span>
            <span className="r b" style={{ color: r.change_pct > 0 ? UP : DOWN }}>{r.change_pct > 0 ? '▲' : '▼'} {Math.abs(r.change_pct).toFixed(2)}%</span>
          </div>
        ))}
      </> : <p className="muted">해당하는 국가가 없습니다.</p>}
    </div>
  )
  return <>
    <div className="resp-stack" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 24 }}>
      {table('성장률 상위', changes.growth)}{table('감소율 상위', changes.decline)}
    </div>
    {changes.bars.length > 0 && <div style={{ marginTop: 34 }}>
      <p style={{ fontSize: 15, fontWeight: 800, marginBottom: 10 }}>국가별 증감액 <span className="muted" style={{ fontSize: 13, fontWeight: 400 }}>비교 기간 → 분석 기간 · 억 달러</span></p>
      {changes.bars.map(r => {
        const width = `${Math.max(Math.abs(r.change_usd) / peak * 45, 0.5)}%`, up = r.change_usd > 0
        return (
          <div key={r.country_code} aria-label={`${r.country} ${signedEok(r.change_usd)}`} style={{ display: 'flex', alignItems: 'center', height: 26, fontSize: 13 }}>
            <div style={{ flex: 1, display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: 6 }}>
              {up ? <span>{r.country}</span> : <><b>{signedEok(r.change_usd)}</b><span style={{ width, height: 12, borderRadius: 2, background: DOWN }} /></>}
            </div>
            <div style={{ width: 1, alignSelf: 'stretch', background: '#2B2E33' }} />
            <div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: 6 }}>
              {up ? <><span style={{ width, height: 12, borderRadius: 2, background: UP }} /><b>{signedEok(r.change_usd)}</b></> : <span>{r.country}</span>}
            </div>
          </div>
        )
      })}
    </div>}
  </>
}
const OUTLOOK_COLOR = { '증가 예상': UP, '감소 예상': DOWN }
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
        {/* 대기 안내는 아직 만드는 중일 때만, 실패면 재요청 안내, 완료(READY)된 뒤 본문을 불러오는 동안에는 추가 안내 없이 둔다. */}
        {!error && !content.error && job?.status === 'FAILED' && <p className="muted" style={{ marginTop: 8 }}>리포트를 만들지 못했습니다. AI챗봇에서 같은 조건으로 다시 요청해 주세요.</p>}
        {!error && !content.error && ['QUEUED', 'RUNNING', 'DELAYED'].includes(job?.status) && <p className="muted" style={{ marginTop: 8 }}>완료 전에는 열람·다운로드할 수 없습니다. 완료되면 리포트 센터에서 확인할 수 있습니다.</p>}
        <Btn to="/account/reports" kind="red" size="md" style={{ marginTop: 20 }}>리포트 센터로</Btn>
      </div>
    </div></main>
  </>

  // 기존 화면의 표시 모델에 저장된 서버 결과만 연결한다.
  const byCode = Object.fromEntries(sections.map(section => [section.section_code, section]))
  const kpi = byCode.KPI?.kpi || {}
  const comparison = reportComparison(c, kpi)
  const monthly = byCode.KPI?.tables?.find(t => t.columns.some(col => col.key === 'month'))?.rows || []
  const scopeComparison=byCode.KPI?.tables?.find(t=>t.meta?.kind==='REPORT_SCOPE_COMPARISON')
  const scopeSeries=scopeComparison?.columns.filter(col=>col.key!=='month')||[]
  const scopeColors=SERIES_COLORS
  const countryRows = byCode.TOP_COUNTRIES?.tables?.[0]?.rows || []
  const sectionCodes = { '주요 지표(KPI)': 'KPI', '주요 수출국': 'TOP_COUNTRIES', '유망시장(증가·감소 예상)': 'GROWTH_RISK', '현지 이슈 기사': 'LOCAL_ISSUES' }
  const o = {
    country: [...(c.include_korea_total?['한국(대한민국) 전체']:[]),...(c.country_codes || []).map(countryName)].join(' · '), product: (c.hs_items || []).map(h => h.hs_code).join(', '),
    from: c.period?.start, to: c.period?.end,
    sections: Object.keys(sectionCodes).filter(label => byCode[sectionCodes[label]]),
  }
  const f = {
    total: withUnit(kpi.amount_usd, '억 USD'), growth: comparison.growth, growthText: percent(comparison.growth),
    labels: monthly.map(r => r.month), current: monthly.map(r => r.amount_usd / 1e8),
    countryRows: countryRows.map(r => ({ name: r.country, exp: r.amount_usd, share: r.share_pct, growth: r.yoy_pct })),
    news: (byCode.LOCAL_ISSUES?.articles || byCode.NEWS?.articles || []).map(mapArticle),
  }
  const kind = reportKinds[report.kind]
  const newsOnly = report.kind === 'weekly'
  const frozen = content.data.meta?.freshness === 'FROZEN'   // 승인한 채팅 결과로 만든 리포트(D-90)
  const has = label => !!byCode[sectionCodes[label]]
  const promising = promisingView(byCode.GROWTH_RISK)
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
            <div style={{ background: '#1e293a', color: '#fff', padding: '36px 48px 30px', position: 'relative' }}>
              <p style={{ fontSize: 13, fontWeight: 800, letterSpacing: 1, color: '#d6dde9' }}>BORDERLESS SELLERS · {kind.eyebrow}</p>
              <h1 style={{ fontSize: 34, fontWeight: 800, marginTop: 10 }}>{report.title}</h1>
              <p style={{ marginTop: 10, color: '#d6dde9', fontSize: 15 }}>{/* 주간은 기사만 싣는 리포트라 수출 분석 항목 수를 쓰지 않는다 */}
                {frozen ? '승인한 분석 결과로 작성 · ' : !newsOnly && (o.sections.length === 4 ? '전체 항목 선택 · ' : `${o.sections.length}개 항목 선택 · `)}{o.product ? `HS6 ${o.product}` : '전체 품목'} · 국가: {o.country || '전체'}</p>
              <div style={{ display: 'flex', gap: 24, marginTop: 18, fontSize: 14, color: '#fff' }}>
                <span>분석 {range(o.from, o.to)}</span><span>생성일 {kstDateTimeText(report.created)}</span>
              </div>
              <div style={{ position: 'absolute', right: 48, top: 36, width: 110, height: 6, background: '#a6b6d0' }} />
            </div>
            <div style={{ padding: '8px 48px 44px' }}>
              {frozen && <ResultSections sections={sections} onDownload={() => downloadReport(id, 'XLSX')} />}
              {!frozen && <>
              {byCode.SUMMARY && <Section no={no++} title="핵심 요약">
                <ul style={{ margin: 0, paddingLeft: 20, fontSize: 16, lineHeight: 1.9, color: '#2E333C' }}>
                  <li>{byCode.SUMMARY.text}</li>
                </ul>
              </Section>}

              {has('주요 지표(KPI)') && (
                <Section no={no++} title="주요 지표">
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(150px,1fr))', gap: 12 }}>
                    {[['기간 수출액', f.total, '#15181E'], [comparison.label, f.growthText, f.growth >= 0 ? UP : DOWN], ['수출국 수', `${kpi.export_country_count ?? '—'}개국`, '#15181E'], ['상위 1개국 비중', share(kpi.top_country_share_pct), '#15181E']].map(([l, v, c]) => (
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
                        <span className="lg"><i style={{ background: '#1e293a' }} />분석 기간</span>
                        <Btn kind="lav" className="no-print" onClick={() => setTable(t => ({ ...t, trend: !t.trend }))}>{table.trend ? '차트로 보기' : '표로 보기'}</Btn>
                      </div>
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 10, marginTop: 14 }}>
                      {[['분석 기간 합계', f.total, '#15181E'], ['비교 기간 합계', withUnit(comparison.previous, '억 USD'), '#15181E'], [comparison.label, f.growthText, f.growth >= 0 ? UP : DOWN]].map(([l, v, c]) => (
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
                      <LineChart width={900} height={240} labels={f.labels} yTicks={ticks} formatValue={reportEok} series={[{ color: '#1e293a', areaColor: '#a6b6d0', values: f.current }]} />
                    ) : <p className="muted">조회된 월별 실적이 없습니다.</p>}
                    <p className="note">비교 기간의 월별 수출액은 현재 응답에 포함되지 않아 표시하지 않습니다. 비교 합계는 저장된 값이 있을 때 표시합니다.</p>
                  </div>
                </Section>
              )}

              {scopeComparison && <Section no={no++} title="한국 전체 · 교역 상대국 비교">
                <p className="note">한국 전체에는 개별 상대국 수출액이 포함되므로 비교 대상 간 금액을 합산하지 않습니다. 단위: 억 USD</p>
                <div className="chips">{scopeSeries.map((s,i)=><span key={s.key} style={{color:scopeColors[i%scopeColors.length]}}>● {s.label}</span>)}</div>
                <LineChart formatValue={reportEok} labels={scopeComparison.rows.map(r=>r.month)} yTicks={niceTicks(scopeComparison.rows.flatMap(r=>scopeSeries.map(s=>r[s.key]/1e8)))}
                  series={scopeSeries.map((s,i)=>({color:scopeColors[i%scopeColors.length],values:scopeComparison.rows.map(r=>r[s.key]/1e8)}))}/>
                <ApiResults table={scopeComparison} tableOnly/>
              </Section>}
              {has('주요 수출국') && (
                <Section no={no++} title="주요 수출국" action={<Btn kind="lav" className="no-print" onClick={() => setTable(t => ({ ...t, countries: !t.countries }))}>{table.countries ? '막대로 보기' : '표로 보기'}</Btn>}>
                  <div className="th" style={{ gridTemplateColumns: '1fr 2fr 1fr 1fr' }}><span>국가</span><span>수출액</span><span className="r">비중</span><span className="r">전년 대비</span></div>
                  {f.countryRows.map(r => (
                    <div key={r.name} className="tr" style={{ gridTemplateColumns: '1fr 2fr 1fr 1fr' }}>
                      <span className="b">{r.name}</span>
                      {table.countries
                        ? <span className="b">{withUnit(r.exp, '억 USD')}</span>
                        : <span style={{ display: 'flex', alignItems: 'center', gap: 12 }}><span style={{ width: `${Math.min(65, Math.max(0, (r.share || 0) * .65))}%`, height: 12, borderRadius: 6, background: '#1e293a' }} /><b style={{ whiteSpace: 'nowrap' }}>{withUnit(r.exp, '억')}</b></span>}
                      <span className="r b">{share(r.share)}</span>
                      <span className="r b" style={{ color: r.growth >= 0 ? UP : DOWN }}>{percent(r.growth)}</span>
                    </div>
                  ))}
                  {!f.countryRows.length && <p className="muted">조회된 국가별 실적이 없습니다.</p>}
                  <p className="note" style={{ marginTop: 10 }}>국가별 전년 대비는 서버 응답이 없으면 —로 표시합니다. 조회 범위 내 수출액 상위 최대 10개국{c.country_codes?.length === 1 ? ' · 국가 1개 선택으로 1개만 표시' : ''}</p>
                </Section>
              )}

              {has('유망시장(증가·감소 예상)') && (
                <Section no={no++} title="유망시장(증가·감소 예상)">
                  <CountryChanges changes={byCode.GROWTH_RISK?.changes} />
                  <div style={{ marginTop: 64, padding: '18px 20px', borderRadius: 14, background: '#d6dde9' }}>
                  <p style={{ fontSize: 15, fontWeight: 800, marginBottom: 8 }}>ML 유망시장 분석 <span className="muted" style={{ fontSize: 13, fontWeight: 400 }}>{promising.anchor ? `${promising.anchor} 기준 · ` : ''}모델 예측 순위 · 증가·감소 예상</span></p>
                  <p className="note" style={{ marginBottom: 10 }}>{byCode.GROWTH_RISK?.text}</p>
                  {promising.rows.length > 0 && <>
                    <div className="th" style={{ gridTemplateColumns: PROMISING_COLS }}><span>{promising.rankLabel}</span><span>국가</span><span>품목</span><span>증감 예상</span><span className="r">최근 12개월 수출액</span></div>
                    {promising.rows.map(r => (
                      <div key={`${r.country_code}-${r.hs6}`} className="tr" style={{ gridTemplateColumns: PROMISING_COLS }}>
                        <span className="b">{r.rank ?? '—'}{r.size ? <span className="s" style={{ fontWeight: 400 }}> / {Number(r.size).toLocaleString('ko-KR')}</span> : null}</span>
                        <span className="b">{r.country_name || countryName(r.country_code)}</span>
                        <span>{r.product_name || r.name || r.hs6} <span className="hs">{r.hs6}</span></span>
                        <span className="b" style={{ color: OUTLOOK_COLOR[r.growth_outlook] || '#5C6370' }}>{r.growth_outlook || '—'}</span>
                        <span className="r b">{withUnit(r.exp_12m, '억 USD')}</span>
                      </div>
                    ))}
                    <p className="note" style={{ marginTop: 10 }}>{promising.anchor ? `${promising.anchor} 기준 · ` : ''}순위 옆 숫자는 전체 예측 대상 수입니다. 예측은 참고용이며 실제 결과와 다를 수 있습니다.</p>
                  </>}
                  </div>
                  {byCode.GROWTH_RISK?.changes?.status === 'READY' && <p className="note" style={{ marginTop: 10 }}>성장률 = (분석 기간 − 비교 기간) ÷ 비교 기간 × 100 · 성장률 순위는 분석 기간, 감소율 순위는 비교 기간 수출액 {(Number(byCode.GROWTH_RISK.changes.min_usd || 1e7) / 1e8).toFixed(1)}억 달러 이상 국가만</p>}
                </Section>
              )}

              {(newsOnly || o.sections.includes('현지 이슈 기사')) && (
                <Section no={no++} title="현지 이슈">
                  <p className="note" style={{ marginBottom: 10, whiteSpace: 'pre-line' }}>{byCode.LOCAL_ISSUES?.text || byCode.NEWS?.text}</p>
                  <div className="col" style={{ gap: 10 }}>
                    {(f.news.length ? f.news : []).map(a => (
                      <div key={a.id} className="li" style={{ padding: '16px 20px' }}>
                        <div style={{ flex: 1 }}>
                          <p style={{ fontSize: 16, fontWeight: 800 }}>{a.title}</p>
                          <p className="muted" style={{ marginTop: 4 }}>{a.summary}</p>
                          <p className="note">{a.date} · {a.country} · {a.source}</p>
                        </div>
                        <Link to={`/articles?tab=${a.kind}&id=${a.id}`} className="more">기사 보기 ↗</Link>
                      </div>
                    ))}
                    {!f.news.length && <p className="muted">해당 국가·품목·기간에 맞는 기사가 없습니다.</p>}
                  </div>
                </Section>
              )}
              </>}

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
