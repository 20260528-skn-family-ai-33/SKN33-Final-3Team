// BS21 리포트 열람·다운로드. 저장된 조건으로 그린 결과를 보여 주며 재계산하지 않는다.
import { useEffect, useState } from 'react'
import { Link } from '../../router'
import { useApp } from '../../store'
import { Band } from '../../components/layout'
import { Btn } from '../../components/ui'
import { LineChart } from '../../components/charts'
import { niceTicks } from '../../utils/chart'
import { productName } from '../../data/catalog'
import { reportKinds } from '../../data/seed'
import { figures, range, reportParagraphs } from '../../data/analysis'
import { downloadDocx, downloadExcel } from '../../utils/exports'

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
  const { reports } = useApp()
  const report = reports.find(r => r.id === route.query.get('id'))
  const [table, setTable] = useState({ trend: false, countries: false })
  const print = route.query.get('print') === '1'
  useEffect(() => {
    if (!print || report?.status !== 'ready') return undefined
    const t = setTimeout(() => window.print(), 400)
    return () => clearTimeout(t)
  }, [print, report])

  if (!report || report.status !== 'ready') {
    return (
      <>
        <Band tone="plum" eyebrow="REPORT" title="리포트 열람" lead="저장된 결과를 열람하고 생성 당시의 PDF·DOCX 파일을 다운로드합니다. 재계산하지 않습니다." />
        <main className="page">
          <div className="wrap">
            <div className="card" style={{ maxWidth: 640, margin: '0 auto', textAlign: 'center' }}>
              <p className="t2">{report ? '아직 생성이 완료되지 않은 리포트입니다' : '리포트를 찾을 수 없습니다'}</p>
              <p className="muted" style={{ marginTop: 8 }}>{report ? '생성 지연 · 자동 재시도 후에도 완료되지 않았습니다. 완료 전에는 열람·다운로드할 수 없습니다.' : '삭제되었거나 주소가 잘못되었습니다.'}</p>
              <Btn to="/account/reports" kind="red" size="md" style={{ marginTop: 20 }}>리포트 센터로</Btn>
            </div>
          </div>
        </main>
      </>
    )
  }

  const o = report.options
  const f = figures(o)
  const kind = reportKinds[report.kind]
  const newsOnly = report.kind === 'weekly'
  const has = s => !newsOnly && o.sections.includes(s)
  const year = Number(o.from.slice(0, 4))
  const ticks = niceTicks([...f.current, ...f.previous], 3, true)
  let no = 1
  const excel = () => downloadExcel(
    [['구분', '기간', '수출액(억 USD)'], ...f.labels.map((l, i) => ['분석', l, f.current[i]]), ...f.labels.map((l, i) => ['비교', l, f.previous[i]])],
    `${report.title}_데이터`,
  )

  return (
    <>
      <Band
        tone="plum" eyebrow="REPORT" title="리포트 열람" lead="저장된 결과를 열람하고 생성 당시의 PDF·DOCX 파일을 다운로드합니다. 재계산하지 않습니다."
        right={(
          <div style={{ display: 'flex', gap: 8 }} className="no-print">
            <Btn icon="download" onClick={excel}>엑셀</Btn>
            <Btn kind="red" icon="download" onClick={() => window.print()}>PDF</Btn>
            <Btn icon="download" onClick={() => downloadDocx(report.title, reportParagraphs(report))}>DOCX</Btn>
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
              <p style={{ marginTop: 10, color: '#D6D3E6', fontSize: 15 }}>{o.sections.length === 4 ? '전체 항목 선택' : `${o.sections.length}개 항목 선택`} · HS6 {o.product} · 국가: {o.country || '전체'}</p>
              <div style={{ display: 'flex', gap: 24, marginTop: 18, fontSize: 14, color: '#fff' }}>
                <span>분석 {range(o.from, o.to)}</span><span>비교 {range(o.compareFrom, o.compareTo)}</span><span>생성일 {report.created}</span>
              </div>
              <div style={{ position: 'absolute', right: 48, top: 36, width: 110, height: 6, background: '#C8102E' }} />
            </div>
            <div style={{ padding: '8px 48px 44px' }}>
              <Section no={no++} title="핵심 요약">
                <ul style={{ margin: 0, paddingLeft: 20, fontSize: 16, lineHeight: 1.9, color: '#2E333C' }}>
                  <li>선택 기간 {o.country || '전체'}향 {productName(o.product)} 수출액은 <b>{f.total}억 USD</b>로, 전년 동기 대비 <b style={{ color: f.growth >= 0 ? '#C8102E' : '#1F5FD1' }}>{Math.abs(f.growth).toFixed(1)}% {f.growth >= 0 ? '증가' : '감소'}</b>했습니다.</li>
                  <li>{o.country ? `선택 국가가 ${o.country} 1개국이므로 조회 범위의 수출국 수는 1개이며 상위 1개국 비중은 100%입니다.` : `상위 5개국이 조회 범위 수출액의 대부분을 차지합니다.`}</li>
                  <li>수출 실적과 관련 현지 기사 근거를 함께 확인할 수 있습니다.</li>
                </ul>
              </Section>

              {has('주요 지표(KPI)') && (
                <Section no={no++} title="주요 지표">
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 12 }}>
                    {[['기간 수출액', `${f.total}억 USD`, '#15181E'], ['전년 대비', f.growthText, f.growth >= 0 ? '#C8102E' : '#1F5FD1'], ['수출국 수', `${f.countryRows.length}개국`, '#15181E'], ['상위 1개국 비중', `${f.countryRows[0]?.share ?? 0}%`, '#15181E']].map(([l, v, c]) => (
                      <div key={l} style={{ background: 'var(--bg)', borderRadius: 14, padding: '18px 20px' }}>
                        <p className="muted">{l}</p>
                        <p style={{ fontSize: 26, fontWeight: 800, marginTop: 6, color: c }}>{v}</p>
                      </div>
                    ))}
                  </div>
                  <p className="note" style={{ marginTop: 10 }}>추가 실적: 수입액 {f.imp}억 USD · 금액 단위: USD · 비교: 전년 같은 기간</p>
                  <div style={{ marginTop: 22, border: '1px solid var(--line)', borderRadius: 14, padding: '18px 20px' }}>
                    <div className="between">
                      <b>월별 {f.labels.length}개월 수출 추이{report.kind === 'quarterly' ? ' · 분기 리포트' : ''}</b>
                      <div style={{ display: 'flex', gap: 14, alignItems: 'center' }}>
                        <span className="lg"><i style={{ background: '#C8102E' }} />{year}년</span>
                        <span className="lg"><i style={{ background: '#9A95BF' }} />{year - 1}년</span>
                        <Btn kind="lav" className="no-print" onClick={() => setTable(t => ({ ...t, trend: !t.trend }))}>{table.trend ? '차트로 보기' : '표로 보기'}</Btn>
                      </div>
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 10, marginTop: 14 }}>
                      {[[`${year}년 기간 합계`, `${f.total}억 USD`, '#15181E'], [`${year - 1}년 기간 합계`, `${f.prevTotal}억 USD`, '#15181E'], ['전년 동기 대비', f.growthText, f.growth >= 0 ? '#C8102E' : '#1F5FD1']].map(([l, v, c]) => (
                        <div key={l} style={{ border: '1px solid var(--line)', borderRadius: 12, padding: '14px 16px' }}>
                          <p className="note">{l}</p><p style={{ fontSize: 18, fontWeight: 800, color: c }}>{v}</p>
                        </div>
                      ))}
                    </div>
                    {table.trend ? (
                      <div style={{ marginTop: 14 }}>
                        <div className="th" style={{ gridTemplateColumns: '1fr 1fr 1fr' }}><span>월</span><span className="r">{year}년</span><span className="r">{year - 1}년</span></div>
                        {f.labels.map((l, i) => <div key={l} className="tr" style={{ gridTemplateColumns: '1fr 1fr 1fr' }}><span className="b">{l}</span><span className="r b">{f.current[i].toFixed(1)}</span><span className="r s">{f.previous[i].toFixed(1)}</span></div>)}
                      </div>
                    ) : (
                      <LineChart width={900} height={240} labels={f.labels} yTicks={ticks} series={[{ color: '#C8102E', values: f.current }, { color: '#9A95BF', values: f.previous }]} />
                    )}
                    <p className="note">월별 합계 = 기간 합계 · ({f.total} ÷ {f.prevTotal} − 1) × 100 = {f.growthText} (소수 첫째 자리 반올림)</p>
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
                        ? <span className="b">{r.exp.toFixed(1)}억 USD</span>
                        : <span style={{ display: 'flex', alignItems: 'center', gap: 12 }}><span style={{ width: `${Math.max(8, r.share)}%`, height: 12, borderRadius: 6, background: '#C8102E' }} /><b>{r.exp.toFixed(1)}억</b></span>}
                      <span className="r b">{r.share}%</span>
                      <span className="r b" style={{ color: r.growth >= 0 ? '#C8102E' : '#1F5FD1' }}>{r.growth >= 0 ? '+' : ''}{r.growth.toFixed(1)}%</span>
                    </div>
                  ))}
                  <p className="note" style={{ marginTop: 10 }}>조회 범위 내 수출액 상위 최대 10개국{o.country ? ' · 국가 1개 선택으로 1개만 표시' : ''}</p>
                </Section>
              )}

              {has('급성장·리스크 시장') && (
                <Section no={no++} title="급성장 · 리스크 시장">
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
                    <div style={{ borderRadius: 14, padding: 20, background: 'var(--rose)' }}>
                      <p style={{ fontSize: 13, fontWeight: 800, color: 'var(--red-d)' }}>성장률 상위</p>
                      <p style={{ fontSize: 22, fontWeight: 800, marginTop: 6 }}>{f.countryRows[0]?.name} <span style={{ color: '#C8102E' }}>{f.growthText}</span></p>
                      <p className="note">일회성 가능: 해당 없음 (예시)</p>
                    </div>
                    <div style={{ borderRadius: 14, padding: 20, background: '#EEF3FC' }}>
                      <p style={{ fontSize: 13, fontWeight: 800, color: '#1F5FD1' }}>감소율 상위</p>
                      <p style={{ fontSize: 22, fontWeight: 800, marginTop: 6 }}>해당 국가 없음</p>
                      <p className="note">선택 범위에서 감소한 국가가 없습니다.</p>
                    </div>
                  </div>
                  <p className="note" style={{ marginTop: 10 }}>성장률 순위는 기간 합계 1천만 USD 이상 국가만 포함합니다. 1개월 리포트는 100만 USD 이상입니다. 성장률·감소율 순으로 정렬하며, 일회성 가능으로 판정된 국가에는 별도 배지를 표시합니다.</p>
                </Section>
              )}

              {(newsOnly || o.sections.includes('현지 이슈 기사')) && (
                <Section no={no++} title="현지 이슈">
                  <div className="col" style={{ gap: 10 }}>
                    {(f.news.length ? f.news : []).map(a => (
                      <div key={a.id} className="li" style={{ padding: '16px 20px' }}>
                        <div style={{ flex: 1 }}>
                          <p style={{ fontSize: 16, fontWeight: 800 }}>{a.title}</p>
                          <p className="muted" style={{ marginTop: 4 }}>{a.summary}</p>
                          <p className="note">{a.date} · {a.country} · KOTRA 해외시장뉴스</p>
                        </div>
                        <Link to={`/articles?tab=${a.kind === 'general' ? 'all' : a.kind}&id=${a.id}`} className="more">원문 보기 ↗</Link>
                      </div>
                    ))}
                    {!f.news.length && <p className="muted">해당 국가·품목·기간에 맞는 기사가 없습니다.</p>}
                  </div>
                </Section>
              )}

              <div style={{ marginTop: 36, background: 'var(--bg)', borderRadius: 14, padding: '20px 22px' }}>
                <p style={{ fontSize: 16, fontWeight: 800 }}>출처</p>
                <ul className="muted" style={{ margin: '10px 0 0', paddingLeft: 18, fontSize: 14, lineHeight: 1.9 }}>
                  <li>통계: 관세청 / 분석 {range(o.from, o.to)} · 비교 {range(o.compareFrom, o.compareTo)} / USD 단위 / 조회 기준일 {report.created}</li>
                  <li>기사: KOTRA 해외시장뉴스·단신속보 / 기사별 발행일·원문 URL</li>
                  <li>환율: 한국은행 ECOS / 사용한 환율 기준일 (환율 사용 시)</li>
                </ul>
              </div>
            </div>
          </article>
        </div>
      </main>
    </>
  )
}
