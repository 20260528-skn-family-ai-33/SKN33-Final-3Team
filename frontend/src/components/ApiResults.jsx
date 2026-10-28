import { useState } from 'react'
import { Btn } from './ui'
import { LineChart } from './charts'
import { niceTicks } from '../utils/chart'
import { Value } from '../api/hooks'
import { download } from '../api/client'
import { useApp } from '../store'

/** Render server snapshots using the existing table and chart components. */
export default function ApiResults({ table }) {
  const { run } = useApp()
  const [showTable, setShowTable] = useState(false)
  const rows = table?.rows || [], columns = table?.columns || []
  const numeric = ['amount_usd', 'export_amount_usd', 'import_amount_usd', 'previous_year_export_usd'].filter(key => rows.length && rows.every(r => typeof r[key] === 'number'))
  const chart = rows.length > 0 && rows.every(r => r.month) && numeric.length > 0
  const bars = rows.length > 0 && rows.every(r => r.country && typeof r.amount_usd === 'number')
  const maxAmount = Math.max(1, ...rows.map(r => r.amount_usd || 0))
  const compact = value => Number(value).toLocaleString('en-US', { notation: 'compact', maximumFractionDigits: 2 })
  const colors = ['#C8102E', '#2B2350', '#9A95BF', '#8A919C']
  return <div style={{ marginTop: 18, border: '1px solid var(--line)', borderRadius: 14, padding: '16px 18px', overflowX: 'auto' }}>
    <div className="between"><b>{chart ? '월별 추이 · USD' : '조회 결과'}</b><div style={{ display: 'flex', gap: 10 }}>
      {(chart || bars) && <Btn kind="lav" onClick={() => setShowTable(v => !v)}>{showTable ? '차트로 보기' : '표로 보기'}</Btn>}
      {table.result_id && <Btn icon="download" onClick={() => run(() => download(`/results/${table.result_id}/file`, '분석결과.xlsx'))}>엑셀 다운로드</Btn>}
    </div></div>
    {chart && !showTable ? <LineChart formatValue={compact} labels={rows.map(r => r.month)} yTicks={niceTicks(rows.flatMap(r => numeric.map(k => r[k])))} series={numeric.map((key, i) => ({ color: colors[i], values: rows.map(r => r[key]) }))} /> : bars && !showTable ? <>
      <div className="th" style={{ gridTemplateColumns: '1fr 2fr 1fr', marginTop: 16 }}><span>국가</span><span>수출액 (USD)</span><span className="r">비중</span></div>
      {rows.map(r => <div key={r.country} className="tr" style={{ gridTemplateColumns: '1fr 2fr 1fr' }}><b>{r.country}</b><span style={{ display: 'flex', gap: 12, alignItems: 'center' }}><span style={{ width: `${Math.max(1, r.amount_usd / maxAmount * 65)}%`, height: 12, borderRadius: 6, background: '#C8102E' }} /><b>{compact(r.amount_usd)}</b></span><span className="r">{r.share_pct == null ? '—' : `${Number(r.share_pct).toFixed(1)}%`}</span></div>)}
    </> : <>
      <div className="th" style={{ gridTemplateColumns: `repeat(${columns.length}, minmax(100px, 1fr))`, marginTop: 16 }}>{columns.map(c => <span key={c.key}>{c.label}{c.unit ? ` (${c.unit})` : ''}</span>)}</div>
      {rows.map((row, i) => <div key={i} className="tr" style={{ gridTemplateColumns: `repeat(${columns.length}, minmax(100px, 1fr))` }}>{columns.map(c => <span key={c.key}><Value value={row[c.key]} /></span>)}</div>)}
      {!rows.length && <p className="muted">조회된 결과가 없습니다.</p>}
    </>}
    {chart && !showTable && <div className="chips">{numeric.map((key, i) => <span key={key} className="lg"><i style={{ background: colors[i] }} />{columns.find(c => c.key === key)?.label || key}</span>)}</div>}
  </div>
}
