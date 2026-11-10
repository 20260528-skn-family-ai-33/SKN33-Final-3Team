import { useRef, useState } from 'react'
import { Btn, Pager } from './ui'
import { LineChart } from './charts'
import { niceTicks } from '../utils/chart'
import { Value } from '../api/hooks'
import { api, download } from '../api/client'
import { useApp } from '../store'
import { resultColumns } from './resultColumns'

const PAGE_SIZE = 20

/** Render server snapshots using the existing table and chart components. */
// fixedColumns: 저장된 리포트처럼 서버가 열과 순서를 확정한 표. 채팅 화면의 숨김 규칙을 다시 적용하지 않는다.
export default function ApiResults({ table, tableOnly = false, fixedColumns = false, onDownload }) {
  const { run } = useApp()
  const [showTable, setShowTable] = useState(false)
  // 응답에는 첫 페이지(20행)만 온다. 나머지는 저장 결과 API에서 페이지로 읽는다(차트는 전체 값을 따로 그린다).
  // 읽은 페이지는 결과 ID와 함께 둔다. 같은 자리에 다른 결과가 오면 첫 페이지부터 보인다.
  const [loaded, setLoaded] = useState({ id: null, page: 1, rows: null })
  const latest = useRef(0)
  const rows = table?.rows || [], total = table?.total_rows ?? rows.length
  const pages = table?.result_id && total > rows.length ? Math.ceil(total / PAGE_SIZE) : 1
  const paged = loaded.id === table?.result_id ? loaded : { page: 1, rows: null }
  const shown = paged.rows || rows
  // 여러 페이지면 서버가 전체 행으로 정한 열을 그대로 쓴다(페이지마다 열이 바뀌지 않게). 라벨만 붙인다.
  const columns = resultColumns(table?.columns || [], rows, { labelsOnly: fixedColumns || pages > 1, meta: table?.meta })
  const go = page => run(async () => {
    const request = ++latest.current, id = table.result_id
    const data = page === 1 ? { table } : await api(`/results/${id}`, { query: { page, page_size: PAGE_SIZE } })
    if (request === latest.current) setLoaded({ id, page, rows: page === 1 ? null : data.table.rows })  // 늦게 온 이전 요청은 버린다
  })
  const numeric = ['amount_usd', 'export_amount_usd', 'import_amount_usd', 'previous_year_export_usd'].filter(key => rows.length && rows.every(r => typeof r[key] === 'number'))
  const chart = !tableOnly && rows.length > 0 && rows.every(r => r.month) && numeric.length > 0
  const bars = !tableOnly && rows.length > 0 && rows.every(r => r.country && typeof r.amount_usd === 'number')
  const maxAmount = Math.max(1, ...rows.map(r => r.amount_usd || 0))
  const compact = value => Number(value).toLocaleString('en-US', { notation: 'compact', maximumFractionDigits: 2 })
  const colors = ['#C8102E', '#2B2350', '#9A95BF', '#8A919C']
  return <div className="api-results" style={{ border: '1px solid var(--line)', borderRadius: 14, padding: '16px 18px', overflowX: 'auto' }}>
    <div className="between"><b>{chart ? '월별 추이 · USD' : '조회 결과'}</b><div style={{ display: 'flex', gap: 10 }}>
      {(chart || bars) && <Btn kind="lav" onClick={() => setShowTable(v => !v)}>{showTable ? '차트로 보기' : '표로 보기'}</Btn>}
      {(table.result_id || onDownload) && <Btn icon="download" onClick={() => run(() => onDownload ? onDownload() : download(`/results/${table.result_id}/file`))}>엑셀 다운로드</Btn>}
    </div></div>
    {chart && !showTable ? <LineChart formatValue={compact} labels={rows.map(r => r.month)} yTicks={niceTicks(rows.flatMap(r => numeric.map(k => r[k])))} series={numeric.map((key, i) => ({ color: colors[i], values: rows.map(r => r[key]) }))} /> : bars && !showTable ? <>
      <div className="th" style={{ gridTemplateColumns: '1fr 2fr 1fr', marginTop: 16 }}><span>국가</span><span>수출액 (USD)</span><span className="r">비중</span></div>
      {rows.map(r => <div key={r.country} className="tr" style={{ gridTemplateColumns: '1fr 2fr 1fr' }}><b>{r.country}</b><span style={{ display: 'flex', gap: 12, alignItems: 'center' }}><span style={{ width: `${Math.max(1, r.amount_usd / maxAmount * 65)}%`, height: 12, borderRadius: 6, background: '#C8102E' }} /><b>{compact(r.amount_usd)}</b></span><span className="r">{r.share_pct == null ? '—' : `${Number(r.share_pct).toFixed(1)}%`}</span></div>)}
    </> : <div style={{overflowX:'auto',marginTop:16}}><div style={{minWidth:columns.length*140}}>
      <div className="th" style={{ gridTemplateColumns: `repeat(${columns.length}, minmax(100px, 1fr))` }}>{columns.map(c => <span key={c.key} style={{overflowWrap:'anywhere'}}>{c.label}{c.unit ? ` (${c.unit})` : ''}</span>)}</div>
      {shown.map((row, i) => <div key={i} className="tr" style={{ gridTemplateColumns: `repeat(${columns.length}, minmax(100px, 1fr))` }}>{columns.map(c => <span key={c.key}><Value value={row[c.key]} /></span>)}</div>)}
      {!rows.length && <p className="muted">조회된 결과가 없습니다.</p>}
    </div>{pages > 1 && <><p className="muted" style={{ marginTop: 10 }}>전체 {total.toLocaleString()}행 중 {((paged.page - 1) * PAGE_SIZE + 1).toLocaleString()}~{Math.min(total, paged.page * PAGE_SIZE).toLocaleString()}행</p>
      <Pager page={paged.page} pages={pages} onChange={go} /></>}</div>}
    {chart && !showTable && <div className="chips">{numeric.map((key, i) => <span key={key} className="lg"><i style={{ background: colors[i] }} />{columns.find(c => c.key === key)?.label || key}</span>)}</div>}
  </div>
}
