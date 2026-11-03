// BS08 수출입 순위(국가·품목·시기별)
import { useState } from 'react'
import { Band, ChatFab } from '../../components/layout'
import { Btn, Select } from '../../components/ui'
import Icon from '../../components/Icon'
import { productName, countries, countryCode, countryName, registerProducts } from '../../data/catalog'
import { useApp } from '../../store'
import { useQuery, ErrorNotice, Pagination } from '../../api/hooks'
import { download } from '../../api/client'
import { Hs4Search } from '../../components/pickers'
import { monthOffset } from '../home/useDashboard'
import { rankInitial } from '../../api/integration'

const COLS = '60px 110px 1fr 1.4fr 110px'
const COLS_NO_HS = '60px 1fr 1.4fr 110px'

export default function Rank({ route }) {
  // Re-entering a detail link on the same route must apply its new conditions.
  const key = new URLSearchParams(route.query)
  key.delete('alerts')
  return <RankResults key={key.toString()} route={route} />
}

function RankResults({ route }) {
  const { run } = useApp()
  const available = useQuery('/data/availability')
  const end = available.data?.trade_end_month
  const PERIODS = [...[3, 6, 12, 1].map(n => [String(n), n === 1 ? '최근 공개 월' : `최근 ${n}개월`]), ['custom', '연·월 직접 선택']]
  const rankingCountries = countries.map(c => [c.name, c.name])
  const [group, setGroup] = useState(null)
  const [pickerKey, setPickerKey] = useState(0)
  const [page, setPage] = useState(1)
  const initial = rankInitial(route.query)
  const inheritedCountry = initial.countryCodes.length ? '__inherited__' : ''
  if (!initial.country && inheritedCountry) initial.country = inheritedCountry
  if (inheritedCountry) rankingCountries.unshift([inheritedCountry, initial.countryCodes.map(countryName).join(' · ')])
  if (initial.start && initial.end) initial.period = 'custom'
  const [draft, setDraft] = useState(initial)
  const [applied, setApplied] = useState(initial)
  const [basis, setBasis] = useState('exp')
  const [error, setError] = useState('')
  const countryCodes = applied.country === '__inherited__' ? applied.countryCodes : applied.country ? [countryCode(applied.country)] : []
  const countryLabel = countryCodes.map(countryName).join(' · ')
  const byMonth = !applied.group && countryCodes.length === 1 && applied.product
  const rankingGroup = applied.group || (countryCodes.length ? 'HS6' : 'COUNTRY')
  const query = { scope: applied.scope, period_start: applied.start || monthOffset(end, 1 - Number(applied.period)), period_end: applied.end || end, country_codes: countryCodes, hs_code: applied.product || undefined, hs_ver: applied.product ? 'HS2022' : undefined }
  const result = useQuery(byMonth ? '/trade/trends' : '/trade/rankings', byMonth ? query : { ...query, group_by: rankingGroup, sort_flow: basis === 'exp' ? 'EXPORT' : 'IMPORT', page, page_size: 20 }, !!end)
  registerProducts((result.data?.items || []).filter(r => r.hs_name).map(r => ({ hs_code: r.hs_item.hs_code, name_ko: r.hs_name })))
  const rows = (result.data?.items || []).map((r, i) => ({ key: r.month || r.country_code || r.hs_item?.hs_code, rank: r.rank || i + 1, hs: r.hs_item?.hs_code, name: r.month || (r.country_code ? countryName(r.country_code) : productName(r.hs_item?.hs_code)), exp: r.export_amount_usd == null || r.has_data === false ? null : Number(r.export_amount_usd) / 1e8, imp: r.import_amount_usd == null || r.has_data === false ? null : Number(r.import_amount_usd) / 1e8 }))
  const pageEmpty = !!result.data && !result.loading && !rows.some(r => r.exp != null || r.imp != null)
  // 순위는 큰 금액부터 정렬되므로 1페이지에 실적이 없으면 전체 결과에도 없다. 다른 페이지는 1페이지 판단을 그대로 쓴다.
  const resultKey = JSON.stringify([byMonth, query, rankingGroup, basis])
  const [firstPage, setFirstPage] = useState({ key: null, empty: false })
  if ((byMonth || page === 1) && result.data && !result.loading && (firstPage.key !== resultKey || firstPage.empty !== pageEmpty)) setFirstPage({ key: resultKey, empty: pageEmpty })
  const empty = byMonth || page === 1 ? pageEmpty : firstPage.key === resultKey && firstPage.empty
  const max = Math.max(...rows.map(r => r[basis]), 1)
  const other = basis === 'exp' ? 'imp' : 'exp'
  const label = { exp: '수출액', imp: '수입액' }

  const title = byMonth ? `${countryLabel} · ${productName(applied.product)} 월별 수출입`
    : rankingGroup === 'HS6' ? `${countryLabel || '전체 국가'} 교역 품목 순위`
      : `${productName(applied.product)} 교역국 순위`
  const firstCol = byMonth ? '월' : rankingGroup === 'HS6' ? '품목' : '교역국'
  const showHs = !byMonth && rankingGroup === 'HS6'
  const cols = showHs ? COLS : COLS_NO_HS

  const search = () => {
    if (!draft.inherited && !draft.country && !draft.product) return setError('국가 또는 품목 중 하나 이상 선택해 주세요.')
    if (draft.period === 'custom' && (!draft.start || !draft.end)) return setError('시작 월과 끝 월을 모두 골라 주세요.')
    if (draft.period === 'custom' && draft.start > draft.end) return setError('시작 월이 끝 월보다 늦습니다. 기간을 다시 골라 주세요.')
    const first = available.data?.trade_start_month
    if (draft.period === 'custom' && ((first && draft.start < first) || (end && draft.end > end))) return setError(`조회할 수 있는 기간은 ${first} ~ ${end}입니다. 기간을 다시 골라 주세요.`)
    setError('')
    setPage(1); setApplied(draft)
  }
  // 검색에서 HS4를 고르면 'HS4 전체' + 하위 HS6 칩, 고르기 전에는 지금 선택한 품목만 칩으로 보여 준다.
  const productOptions = group ? [[group.code, `${group.code} 전체`], ...group.items.map(([id, name]) => [id, `${id} ${name}`])]
    : draft.product ? [[draft.product, `${draft.product} ${productName(draft.product)}`]] : []
  // 파일 이름: 국가_기간_내용.xlsx (파일명에 쓸 수 없는 글자는 뺀다)
  // 품목명은 길어서 코드로 적는다. 예) 미국_2025-10~2025-12_HS870332_월별수출입.xlsx
  const fileKind = byMonth ? '월별수출입' : rankingGroup === 'HS6' ? '품목순위' : '교역국순위'
  const fileName = `${countryLabel || '전체국가'}_${query.period_start}~${query.period_end}_${applied.product ? `HS${applied.product}` : '전체품목'}_${fileKind}`.replace(/[\\/:*?"<>|]/g, '').replace(/\s+/g, '') + '.xlsx'
  const exportExcel = () => run(() => download(`/results/${result.data.result_id}/file`, fileName))

  return (
    <>
      <Band tone="lgbg" eyebrow="수출입 순위" title="국가 · 품목 · 시기별 수출입 순위" lead="국가 또는 품목 중 하나 이상 선택 · 시기 선택은 선택 사항" />
      <main className="page" style={{ position: 'relative', paddingBottom: 120 }}>
        <div className="wrap col" style={{ gap: 20 }}>
          <div className="card">
            {/* 관심사 등록과 같은 순서: ① 품목 검색(HS4·HS6 코드 또는 이름) → ② 세부 품목 칩 → ③ 국가·기간 → 순위 조회 */}
            <p className="lbl" style={{ margin: 0 }}>품목 (선택)</p>
            {/* HS4를 고르면 HS4 전체가 조건이 되고 아래 칩에서 HS6로 좁힐 수 있다. HS6(6자리 코드·품목명·HS4 아래 목록)를 고르면 그 HS6가 바로 조건이 된다. */}
            <Hs4Search key={pickerKey} group={group} expandGroup onConfirm={g => {
              setGroup(g)
              if (g) registerProducts([{ hs_code: g.code, name_ko: g.name }])
              setDraft(d => ({ ...d, product: g ? g.code : '', group: null, scope: 'GLOBAL', inherited: false }))
            }} onSelectProduct={id => {
              setGroup(null)
              setDraft(d => ({ ...d, product: id, group: null, scope: 'GLOBAL', inherited: false }))
            }} />
            {(group || draft.product) && (
              <div style={{ marginTop: 14, padding: '12px 14px', borderRadius: 12, background: '#F7F8FA' }}>
                <div className="between" style={{ gap: 10 }}>
                  <span className="lbl" style={{ margin: 0 }}>세부 품목 (하나 선택)</span>
                  <Btn icon="close" onClick={() => { setGroup(null); setPickerKey(k => k + 1); setDraft(d => ({ ...d, product: '', group: null, scope: 'GLOBAL', inherited: false })) }}>품목 선택 해제</Btn>
                </div>
                <div className="chips" role="group" aria-label="세부 품목" style={{ marginTop: 10 }}>
                  {productOptions.map(([id, name]) => {
                    const on = draft.product === id
                    return <button type="button" key={id} className={'chip sm' + (on ? ' on' : '')} aria-pressed={on} onClick={() => setDraft(d => ({ ...d, product: id, group: null, scope: 'GLOBAL', inherited: false }))}>{on ? '✓ ' : ''}{name}</button>
                  })}
                </div>
              </div>
            )}
            <div style={{ display: 'flex', gap: 12, marginTop: 16 }} className="resp-stack">
              <CountrySearch value={draft.country} options={[['', '전체 국가'], ...rankingCountries]} onChange={v => setDraft(d => ({ ...d, country: v, group: null, scope: "GLOBAL", inherited: false }))} />
              <Select prefix="기간" style={{ flex: 1 }} value={draft.period} onChange={v => setDraft(d => v === 'custom' ? { ...d, period: v, start: d.start || monthOffset(end, -2), end: d.end || end } : { ...d, period: v, start: "", end: "" })} options={PERIODS} aria-label="기간" />
              <Btn kind="red" size="md" onClick={search} style={{ height: 50, borderRadius: 12, padding: '0 28px' }}><Icon name="search" size={17} />순위 조회</Btn>
            </div>
            {draft.period === 'custom' && (
              <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginTop: 12, flexWrap: 'wrap' }}>
                <span className="lbl" style={{ margin: 0 }}>기간 직접 선택</span>
                <div className="input val sm"><input type="month" aria-label="시작 월" value={draft.start} min={available.data?.trade_start_month} max={end} onChange={e => setDraft(d => ({ ...d, start: e.target.value }))} /></div>
                <span>~</span>
                <div className="input val sm"><input type="month" aria-label="끝 월" value={draft.end} min={available.data?.trade_start_month} max={end} onChange={e => setDraft(d => ({ ...d, end: e.target.value }))} /></div>
                {end && <span className="note">조회 가능: {available.data?.trade_start_month} ~ {end}</span>}
              </div>
            )}
            <ErrorNotice error={available.error || result.error} retry={result.reload} />
            {error
              ? <p className="field-err" role="alert" style={{ marginTop: 12 }}>{error}</p>
              : <p className="note" style={{ marginTop: 12 }}>{query.period_start || "—"} ~ {query.period_end || "—"} 조회 조건을 적용합니다. 수출 또는 수입 기준으로 순위를 전환할 수 있습니다. 품목은 이름이나 HS 코드(4·6자리)로 검색해 고른 뒤 세부 품목 칩에서 HS6로 좁힐 수 있습니다.</p>}
          </div>
          <div className="card">
            <div className="between">
              <div>
                <h2 className="t2">{title}</h2>
                <p className="muted" style={{ marginTop: 6 }}>{query.period_start} ~ {query.period_end} {byMonth ? '월별' : '누계'} · 억 USD · 관세청</p>
              </div>
              <div className="seg" role="group" aria-label="순위 기준">
                <button type="button" className={'reset' + (basis === 'exp' ? ' on' : '')} aria-pressed={basis === 'exp'} onClick={() => setBasis('exp')}>수출 기준</button>
                <button type="button" className={'reset' + (basis === 'imp' ? ' on' : '')} aria-pressed={basis === 'imp'} onClick={() => setBasis('imp')}>수입 기준</button>
              </div>
            </div>
            {/* 좁은 화면에서는 표만 가로로 스크롤한다. */}
            <div style={{ overflowX: 'auto', marginTop: 18 }}><div style={{ minWidth: 520 }}>
            <div className="th" style={{ gridTemplateColumns: cols }}>
              <span>{byMonth ? '구분' : '순위'}</span>{showHs && <span>HS6</span>}<span>{firstCol}</span><span>{label[basis]} (억 USD)</span><span className="r">{label[other]} (억 USD)</span>
            </div>
            {result.loading && <p className="muted" role="status" style={{ padding: '22px 4px', textAlign: 'center' }}>순위를 불러오는 중입니다…</p>}
            {!result.loading && rows.map(r => (
              <div key={r.key} className="tr" style={{ gridTemplateColumns: cols }}>
                <span className="b" style={{ fontSize: 17 }}>{r.rank}</span>
                {showHs && <span><span className="hs">{r.hs}</span></span>}
                <span className="b" style={{ fontWeight: 700 }}>{r.name}</span>
                <span style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                  <span style={{ flex: 1, height: 10, borderRadius: 5, background: '#F1F2F5', overflow: 'hidden' }}>
                    <span style={{ display: 'block', height: 10, background: basis === 'exp' ? '#C8102E' : '#2B2350', width: `${Math.round((r[basis] / max) * 100)}%` }} />
                  </span>
                  <b style={{ width: 56, textAlign: 'right' }}>{r[basis] == null ? '—' : r[basis].toFixed(1)}</b>
                </span>
                <span className="r s">{r[other] == null ? '—' : r[other].toFixed(1)}</span>
              </div>
            ))}
            </div></div>
            {empty && <p className="muted" role="status" style={{ padding: '22px 4px', textAlign: 'center' }}>조회 조건에 맞는 수출입 실적이 없습니다. 국가·품목·기간을 바꿔 다시 조회해 주세요.</p>}
            <div className="between" style={{ marginTop: 18 }}>
              <span className="note">표·막대: 억 USD · 엑셀: 원본 USD 단위</span>
              <Btn icon="download" disabled={!result.data?.result_id || result.loading || empty} onClick={exportExcel}>엑셀 다운로드</Btn>
            </div>
            {!byMonth && <Pagination page={page} info={result.data?.page} onChange={setPage} />}
          </div>
        </div>
        <ChatFab ask={`${countryLabel || '전체 국가'} · ${applied.product ? `${productName(applied.product)}(HS ${applied.product})` : '전체 품목'} · ${query.period_start} ~ ${query.period_end} 수출입을 분석해줘`} />
      </main>
    </>
  )
}

/** 국가 검색 선택: 국가명을 입력하면 목록이 좁혀진다. */
function CountrySearch({ value, options, onChange }) {
  const label = (options.find(([v]) => v === value) || options[0])[1]
  const [text, setText] = useState(null)
  const typing = text !== null
  // '전체 국가'(값 '')는 검색어와 상관없이 맨 위에 두어 언제든 국가 조건을 풀 수 있게 한다.
  const shown = typing ? options.filter(([v, l]) => !v || l.includes(text.trim())) : options
  const matches = shown.filter(([v]) => v)
  const pick = v => { onChange(v); setText(null) }
  return (
    <div style={{ flex: 1, position: 'relative', minWidth: 0 }}>
      <div className={'input val' + (typing ? ' focus' : '')} style={{ gap: 8 }}>
        <span className="select-prefix">국가</span>
        <input value={typing ? text : label} aria-label="국가" placeholder="국가명 입력"
          onFocus={() => setText('')} onClick={() => !typing && setText('')} onBlur={() => setTimeout(() => setText(null), 150)}
          onChange={e => setText(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); pick(text.trim() && matches.length ? matches[0][0] : '') } if (e.key === 'Escape') setText(null) }} />
        <Icon name="search" size={16} color="#9AA0AA" />
      </div>
      {typing && (
        <div role="listbox" aria-label="국가 목록" style={{ position: 'absolute', top: 56, left: 0, right: 0, zIndex: 20, maxHeight: 280, overflowY: 'auto', background: '#fff', border: '1px solid var(--line)', borderRadius: 12, boxShadow: '0 16px 40px rgba(21,24,30,.12)', padding: 6 }}>
          {shown.map(([v, l]) => (
            <button type="button" key={v || 'all'} className="reset" role="option" aria-selected={v === value}
              onMouseDown={e => e.preventDefault()} onClick={() => pick(v)}
              style={{ display: 'block', width: '100%', textAlign: 'left', padding: '10px 12px', borderRadius: 8, fontWeight: v === value ? 800 : 500 }}>{l}</button>
          ))}
          {typing && text.trim() && !matches.length && <p className="muted" style={{ padding: '10px 12px' }}>검색 결과가 없습니다.</p>}
        </div>
      )}
    </div>
  )
}
