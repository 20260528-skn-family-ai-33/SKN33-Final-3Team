// BS08 수출입 순위(국가·품목·시기별)
import { useMemo, useState } from 'react'
import { Band, ChatFab } from '../../components/layout'
import { Btn, Select } from '../../components/ui'
import Icon from '../../components/Icon'
import { productName, products } from '../../data/catalog'
import { rankingCountries, tradeRanking } from '../../data/trade'
import { downloadExcel } from '../../utils/exports'

const PERIODS = [
  ['2025-10~2025-12', '2025.10–2025.12'],
  ['2025-07~2025-12', '2025.07–2025.12'],
  ['2025-01~2025-12', '2025.01–2025.12'],
  ['2025-12~2025-12', '2025.12'],
]
const PERIOD_LABEL = { '2025-10~2025-12': '최근 3개월', '2025-07~2025-12': '최근 6개월', '2025-01~2025-12': '최근 12개월', '2025-12~2025-12': '2025년 12월' }
const COLS = '60px 110px 1fr 1.4fr 110px'
const COLS_NO_HS = '60px 1fr 1.4fr 110px'

export default function Rank({ route }) {
  const initial = {
    country: route.query.get('country') ?? (route.query.get('product') ? '' : '중국'),
    product: route.query.get('product') || '',
    period: PERIODS[0][0],
  }
  const [draft, setDraft] = useState(initial)
  const [applied, setApplied] = useState(initial)
  const [basis, setBasis] = useState('exp')
  const [error, setError] = useState('')
  const [from, to] = applied.period.split('~')
  const rows = useMemo(() => tradeRanking({ ...applied, from, to, basis }), [applied, from, to, basis])
  const max = Math.max(...rows.map(r => r[basis]), 1)
  const other = basis === 'exp' ? 'imp' : 'exp'
  const label = { exp: '수출액', imp: '수입액' }

  const byMonth = applied.country && applied.product
  const title = byMonth ? `${applied.country} · ${productName(applied.product)} 월별 수출입`
    : applied.country ? `${applied.country} 교역 품목 순위`
      : `${productName(applied.product)} 교역국 순위`
  const firstCol = byMonth ? '월' : applied.country ? '품목' : '교역국'
  const showHs = !byMonth && !!applied.country
  const cols = showHs ? COLS : COLS_NO_HS

  const search = () => {
    if (!draft.country && !draft.product) return setError('국가 또는 품목 중 하나 이상 선택해 주세요.')
    setError('')
    setApplied(draft)
  }
  const exportExcel = () => downloadExcel(
    [['순위', ...(showHs ? ['HS6'] : []), firstCol, '수출액(억 USD)', '수입액(억 USD)'], ...rows.map(r => [r.rank, ...(showHs ? [r.hs] : []), r.name, r.exp, r.imp])],
    `수출입순위_${title.replaceAll(' ', '')}`,
  )

  return (
    <>
      <Band tone="lgbg" eyebrow="수출입 순위" title="국가 · 품목 · 시기별 수출입 순위" lead="국가 또는 품목 중 하나 이상 선택 · 시기 선택은 선택 사항" />
      <main className="page" style={{ position: 'relative', paddingBottom: 120 }}>
        <div className="wrap col" style={{ gap: 20 }}>
          <div className="card">
            <div style={{ display: 'flex', gap: 12 }} className="resp-stack">
              <Select prefix="국가" style={{ flex: 1 }} value={draft.country} onChange={v => setDraft(d => ({ ...d, country: v }))} options={[['', '전체 국가'], ...rankingCountries]} aria-label="국가" />
              <Select prefix="품목" style={{ flex: 1 }} value={draft.product} onChange={v => setDraft(d => ({ ...d, product: v }))} options={[['', '전체 HS6'], ...products.map(p => [p.id, `${p.id} ${p.name}`])]} aria-label="품목" />
              <Select prefix="기간" style={{ flex: 1 }} value={draft.period} onChange={v => setDraft(d => ({ ...d, period: v }))} options={PERIODS} aria-label="기간" />
              <Btn kind="red" size="md" onClick={search} style={{ height: 50, borderRadius: 12, padding: '0 28px' }}><Icon name="search" size={17} />순위 조회</Btn>
            </div>
            {error
              ? <p className="field-err" role="alert" style={{ marginTop: 12 }}>{error}</p>
              : <p className="note" style={{ marginTop: 12 }}>메인에서 진입한 최근 3개월 기준을 유지합니다. 수출 또는 수입 기준으로 순위를 전환할 수 있습니다.</p>}
          </div>
          <div className="card">
            <div className="between">
              <div>
                <h2 className="t2">{title}</h2>
                <p className="muted" style={{ marginTop: 6 }}>{PERIOD_LABEL[applied.period]} 누계 · 억 USD · 시연 데이터</p>
              </div>
              <div className="seg" role="group" aria-label="순위 기준">
                <button type="button" className={'reset' + (basis === 'exp' ? ' on' : '')} aria-pressed={basis === 'exp'} onClick={() => setBasis('exp')}>수출 기준</button>
                <button type="button" className={'reset' + (basis === 'imp' ? ' on' : '')} aria-pressed={basis === 'imp'} onClick={() => setBasis('imp')}>수입 기준</button>
              </div>
            </div>
            <div className="th" style={{ gridTemplateColumns: cols, marginTop: 18 }}>
              <span>{byMonth ? '구분' : '순위'}</span>{showHs && <span>HS6</span>}<span>{firstCol}</span><span>{label[basis]}</span><span className="r">{label[other]}</span>
            </div>
            {rows.map(r => (
              <div key={r.key} className="tr" style={{ gridTemplateColumns: cols }}>
                <span className="b" style={{ fontSize: 17 }}>{r.rank}</span>
                {showHs && <span><span className="hs">{r.hs}</span></span>}
                <span className="b" style={{ fontWeight: 700 }}>{r.name}</span>
                <span style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                  <span style={{ flex: 1, height: 10, borderRadius: 5, background: '#F1F2F5', overflow: 'hidden' }}>
                    <span style={{ display: 'block', height: 10, background: basis === 'exp' ? '#C8102E' : '#2B2350', width: `${Math.round((r[basis] / max) * 100)}%` }} />
                  </span>
                  <b style={{ width: 56, textAlign: 'right' }}>{r[basis].toFixed(1)}</b>
                </span>
                <span className="r s">{r[other].toFixed(1)}</span>
              </div>
            ))}
            <div className="between" style={{ marginTop: 18 }}>
              <span className="note">표·막대는 같은 조건 · 다운로드는 표시 단위를 보존합니다.</span>
              <Btn icon="download" onClick={exportExcel}>엑셀 다운로드</Btn>
            </div>
          </div>
        </div>
        <ChatFab />
      </main>
    </>
  )
}
