// BS08 수출입 순위(국가·품목·시기별)
import { useEffect, useRef, useState } from 'react'
import { Band, ChatFab } from '../../components/layout'
import { Btn, Pager, Select } from '../../components/ui'
import Icon from '../../components/Icon'
import { productName, countries, countryCode, countryName, registerProducts } from '../../data/catalog'
import { useApp } from '../../store'
import { useQuery, ErrorNotice } from '../../api/hooks'
import { download } from '../../api/client'
import { Hs4Search } from '../../components/pickers'
import { monthOffset } from '../home/useDashboard'
import { rankInitial, rankBasis, countryListLabel } from '../../api/integration'
import { amount } from '../../api/adapters'

const COLS = '60px 110px 1fr 1.4fr 110px'
const COLS_NO_HS = '60px 1fr 1.4fr 110px'
// 실적 없음(서버가 채운 빈 행, 금액 0)은 0.0이 아니라 —로 보여 준다(D-3). 엑셀은 원본 값 그대로다.
const noRecord = (row, value) => value == null || row.has_data === false || Number(value) === 0
// 억 USD 값 표시. 메인 대시보드와 같은 규칙: 0.1억 미만은 유효숫자 1자리(0.02), 그 이상은 소수 첫째 자리까지(D-3).
const eok = value => value == null ? '—' : amount(value * 1e8)
const compactList = (items, limit) => items.slice(0, limit).join('-') + (items.length > limit ? ` 외 ${items.length - limit}개` : '')
const selectedCodes = selection => selection.country ? [countryCode(selection.country)] : selection.countryCodes

export default function Rank({ route }) {
  // Re-entering a detail link on the same route must apply its new conditions.
  const key = new URLSearchParams(route.query)
  key.delete('alerts')
  return <RankResults key={key.toString()} route={route} />
}

function RankResults({ route }) {
  const { run, profile } = useApp()
  const available = useQuery('/data/availability')
  const end = available.data?.trade_end_month
  const PERIODS = [...[3, 6, 12, 1].map(n => [String(n), n === 1 ? '최근 공개 월' : `최근 ${n}개월`]), ['custom', '연·월 직접 선택']]
  const [group, setGroup] = useState(null)
  const [pickerKey, setPickerKey] = useState(0)
  const [page, setPage] = useState(1)
  const initial = rankInitial(route.query)
  if (initial.countryCodes.length && !route.query.get('country')) initial.country = ''
  if (initial.start && initial.end) initial.period = 'custom'
  const [draft, setDraft] = useState(initial)
  const [applied, setApplied] = useState(initial)
  const [basis, setBasis] = useState(() => rankBasis(route.query))
  const [error, setError] = useState('')
  const countryCodes = selectedCodes(applied)
  const countryNames = countryCodes.map(countryName)
  const countryLabel = countryListLabel(countryNames)
  const countryFull = countryNames.join(' · ')
  const byMonth = !applied.group && countryCodes.length === 1 && applied.product
  const rankingGroup = applied.group || (countryCodes.length === 1 ? 'HS6' : 'COUNTRY')
  const query = { scope: applied.scope, period_start: applied.start || monthOffset(end, 1 - Number(applied.period)), period_end: applied.end || end, country_codes: countryCodes, hs_code: applied.product || undefined, hs_ver: applied.product ? 'HS2022' : undefined }
  const result = useQuery(byMonth ? '/trade/trends' : '/trade/rankings', byMonth ? query : { ...query, group_by: rankingGroup, sort_flow: basis === 'exp' ? 'EXPORT' : 'IMPORT', page, page_size: 20 }, !!end)
  // 관심 범위(INTERESTED)로 조회하면 서버가 국가·품목을 고르지 않은 부분을 최신 관심 국가·관심 품목으로 좁힌다.
  // 제목·파일명·AI 질문은 서버가 실제로 적용한 조건(응답의 conditions)을 쓰고, 응답 전에는 내 관심정보로 대신한다.
  const appliedScope = applied.scope === 'INTERESTED' ? result.data?.conditions : null
  const interestCountries = applied.scope === 'INTERESTED' && !countryCodes.length ? appliedScope?.country_codes?.map(countryName) || profile.countries || [] : []
  // 관심 범위에서 품목(HS4 등)을 지정해도 서버는 그 아래 '관심 HS6'만 집계하므로, 응답이 오면 서버 목록을 그대로 쓴다.
  // 응답 전에는 지정 품목이 있으면 그것을, 없으면 내 관심 품목을 임시로 쓴다(AI 버튼은 응답 전 비활성).
  const interestProducts = applied.scope === 'INTERESTED' ? appliedScope?.hs_items?.map(h => h.hs_code) || (applied.product ? [] : profile.products || []) : []
  const scopeCountry = countryLabel || (interestCountries.length === 1 ? interestCountries[0] : interestCountries.length ? '관심 국가' : '전체 국가')
  const scopeProduct = interestProducts.length === 1 ? productName(interestProducts[0]) : interestProducts.length ? '관심 품목' : applied.product ? productName(applied.product) : '전체 품목'
  registerProducts((result.data?.items || []).filter(r => r.hs_name).map(r => ({ hs_code: r.hs_item.hs_code, name_ko: r.hs_name })))
  const pageInfo = result.data?.page
  // 기사 화면처럼 페이지를 넘기면 새 목록을 다 불러온 뒤 결과 표 맨 위로 올라간다(처음 열 때는 그대로 둔다).
  // 요청이 실패하면 결과 표 대신 조회 카드의 오류 안내·다시 불러오기 버튼이 보이게 그쪽으로 옮긴다.
  const resultTop = useRef(null), errorTop = useRef(null), previousPage = useRef(page)
  useEffect(() => {
    if (previousPage.current === page || result.loading) return
    previousPage.current = page
    if (result.error) errorTop.current?.scrollIntoView({ block: 'center' })
    else resultTop.current?.scrollIntoView({ block: 'start' })
  }, [page, result.loading, result.error])
  // 전체 쪽수는 마지막으로 성공한 응답 기준으로 기억한다. 다음 페이지를 불러오는 중이거나 요청이 실패해도
  // 조회 조건·정렬 기준이 같으면 페이지 버튼을 그대로 두어 방금 본 페이지로 돌아갈 수 있게 한다.
  const pagesKey = JSON.stringify([byMonth, query, rankingGroup, basis])
  const freshTotal = pageInfo?.page_size ? Number(pageInfo.total ?? pageInfo.total_count ?? 0) : null
  const freshPages = freshTotal == null ? null : Math.ceil(freshTotal / pageInfo.page_size)
  const [knownPages, setKnownPages] = useState({ key: null, pages: 0, total: null })
  if (freshPages != null && (knownPages.key !== pagesKey || knownPages.pages !== freshPages || knownPages.total !== freshTotal)) setKnownPages({ key: pagesKey, pages: freshPages, total: freshTotal })
  const known = knownPages.key === pagesKey
  const pages = freshPages ?? (known ? knownPages.pages : 0)
  const total = freshTotal ?? (known ? knownPages.total : null)
  // 조회 사이에 결과가 줄어 지금 페이지가 마지막 페이지보다 뒤가 되면(예: 41건 → 7건) 마지막 유효 페이지를 다시 조회한다.
  if (freshPages != null && !result.loading && page > Math.max(freshPages, 1)) setPage(Math.max(freshPages, 1))
  const rows = (result.data?.items || []).map((r, i) => ({ key: r.month || r.country_code || r.hs_item?.hs_code, rank: r.has_data === false ? '—' : r.rank || i + 1, hs: r.hs_item?.hs_code, name: r.month || (r.country_code ? countryName(r.country_code) : productName(r.hs_item?.hs_code)), exp: noRecord(r, r.export_amount_usd) ? null : Number(r.export_amount_usd) / 1e8, imp: noRecord(r, r.import_amount_usd) ? null : Number(r.import_amount_usd) / 1e8 }))
  const pageEmpty = !!result.data && !result.loading && !rows.some(r => r.exp != null || r.imp != null)
  // 순위는 큰 금액부터 정렬되므로 1페이지에 실적이 없으면 전체 결과에도 없다. 다른 페이지는 1페이지 판단을 그대로 쓴다.
  const resultKey = JSON.stringify([byMonth, query, rankingGroup, basis])
  const [firstPage, setFirstPage] = useState({ key: null, empty: false })
  if ((byMonth || page === 1) && result.data && !result.loading && (firstPage.key !== resultKey || firstPage.empty !== pageEmpty)) setFirstPage({ key: resultKey, empty: pageEmpty })
  const empty = byMonth || page === 1 ? pageEmpty : firstPage.key === resultKey && firstPage.empty
  const max = Math.max(...rows.map(r => r[basis]), 1)
  const other = basis === 'exp' ? 'imp' : 'exp'
  const label = { exp: '수출액', imp: '수입액' }

  const title = byMonth ? `${countryLabel} · ${scopeProduct} 월별 수출입`
    : rankingGroup === 'HS6' ? `${scopeCountry} 교역 품목 순위`
      : `${scopeProduct} 교역국 순위`
  const firstCol = byMonth ? '월' : rankingGroup === 'HS6' ? '품목' : '교역국'
  const showHs = !byMonth && rankingGroup === 'HS6'
  const cols = showHs ? COLS : COLS_NO_HS

  const search = () => {
    // 응답을 기다리는 동안 다시 누르면 같은 요청이 겹치므로 무시한다(연속 클릭 시 1회 조회).
    if (result.loading) return
    if (!draft.inherited && !selectedCodes(draft).length && !draft.product) return setError('국가 또는 품목 중 하나 이상 선택해 주세요.')
    if (draft.period === 'custom' && (!draft.start || !draft.end)) return setError('시작 월과 끝 월을 모두 골라 주세요.')
    if (draft.period === 'custom' && draft.start > draft.end) return setError('시작 월이 끝 월보다 늦습니다. 기간을 다시 골라 주세요.')
    const first = available.data?.trade_start_month
    if (draft.period === 'custom' && ((first && draft.start < first) || (end && draft.end > end))) return setError(first ? `조회할 수 있는 기간은 ${first} ~ ${end}입니다. 기간을 다시 골라 주세요.` : `${end}까지 조회할 수 있습니다. 기간을 다시 골라 주세요.`)
    setError('')
    // 조건이 그대로여도 '순위 조회'를 누르면 다시 불러온다(다른 탭에서 바꾼 관심정보 등 최신 서버 값 반영).
    setPage(1); setApplied(draft); result.reload()
  }
  // 검색에서 HS4를 고르면 'HS4 전체' + 하위 HS6 칩, 고르기 전에는 지금 선택한 품목만 칩으로 보여 준다.
  const productOptions = group ? [[group.code, `${group.code} 전체`], ...group.items.map(([id, name]) => [id, `${id} ${name}`])]
    : draft.product ? [[draft.product, `${draft.product} ${productName(draft.product)}`]] : []
  // 파일 이름: 국가_기간_내용.xlsx (파일명에 쓸 수 없는 글자는 뺀다)
  // 품목명은 길어서 코드로 적는다. 예) 미국_2025-10~2025-12_HS870332_월별수출입.xlsx
  const fileKind = byMonth ? '월별수출입' : rankingGroup === 'HS6' ? '품목순위' : '교역국순위'
  const productLabel = interestProducts.length ? compactList(interestProducts.map(code => 'HS' + code), 1) : applied.product ? `HS${applied.product}` : '전체품목'
  // AI 질문에는 '관심 국가·관심 품목' 같은 묶음 말 대신 실제 국가 이름과 HS 코드를 적어, 채팅이 같은 조건으로 분석하게 한다.
  const askCountry = countryFull || (interestCountries.length ? interestCountries.join(', ') : '전체 국가')
  // AI 질문에는 조회 종류와 수출·수입 기준도 넣어, 채팅이 화면과 같은 순위를 분석하게 한다.
  const askKind = byMonth ? '월별 수출입 추이' : `${basis === 'exp' ? '수출' : '수입'} 기준 ${rankingGroup === 'HS6' ? '교역 품목 순위' : '교역국 순위'}`
  // 지금 AI챗봇은 질문에서 국가·품목을 각각 20개까지만 읽는다(backend/app/api/intent.py IntentSlots).
  // 그보다 많으면 일부만 분석되므로 넘기지 않고 이유를 안내한다. 결과 ID로 조건을 넘기는 방식은 에이전트 재설계(PR #51, D-64)에서 다룬다.
  const CHAT_MAX_ITEMS = 20
  // 대시보드에서 넘어오면 관심 국가가 주소의 country_codes로 직접 오므로 그 개수도 함께 센다.
  const overChatLimit = interestProducts.length > CHAT_MAX_ITEMS || Math.max(countryCodes.length, interestCountries.length) > CHAT_MAX_ITEMS
  const chatBlocked = !result.data || result.loading || !!result.error || overChatLimit
  const chatReason = overChatLimit ? `AI챗봇은 국가·품목을 ${CHAT_MAX_ITEMS}개까지 받을 수 있습니다. 품목이나 국가를 골라 조회한 뒤 열어 주세요.` : '순위 조회가 끝나면 이 조건으로 AI챗봇을 열 수 있습니다.'
  const askProduct = interestProducts.length ? interestProducts.map(code => `${productName(code)}(HS ${code})`).join(', ') : applied.product ? `${productName(applied.product)}(HS ${applied.product})` : '전체 품목'
  const fileCountry = compactList(countryNames.length ? countryNames : interestCountries, 1) || '전체국가'
  const fileName = `${fileCountry}_${query.period_start}~${query.period_end}_${productLabel}_${fileKind}`.replace(/[\\/:*?"<>|]/g, '') + '.xlsx'
  const exportExcel = () => run(() => download(`/results/${result.data.result_id}/file`, fileName))

  return (
    <>
      <Band tone="lgbg"  title="국가 · 품목 · 시기별 수출입 순위" lead="국가 / 품목 / 시기를 선택하여 순위 조회" />
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
              <CountrySearch value={selectedCodes(draft)} replaceInitial={Boolean(draft.country)} onChange={countryCodes => setDraft(d => ({ ...d, country: '', countryCodes, group: null, scope: 'GLOBAL', inherited: false }))} />
              <Select prefix="기간" style={{ flex: 1 }} value={draft.period} onChange={v => setDraft(d => v === 'custom' ? { ...d, period: v, start: d.start || monthOffset(end, -2), end: d.end || end } : { ...d, period: v, start: "", end: "" })} options={PERIODS} aria-label="기간" />
              <Btn kind="red" size="md" onClick={search} disabled={result.loading} style={{ height: 50, borderRadius: 12, padding: '0 28px' }}><Icon name="search" size={17} />순위 조회</Btn>
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
            {/* 실패한 요청(공개 기간·순위)을 모두 다시 불러온다. 공개 기간이 복구되면 순위 조회도 다시 시작된다. */}
            <div ref={errorTop}><ErrorNotice error={available.error || result.error} retry={() => { if (available.error) available.reload(); if (result.error) result.reload() }} /></div>
            {error
              ? <p className="field-err" role="alert" style={{ marginTop: 12 }}>{error}</p>
              : <p className="note" style={{ marginTop: 12 }}>{query.period_start || "—"} ~ {query.period_end || "—"} 조회 조건을 적용합니다. 수출 또는 수입 기준으로 순위를 전환할 수 있습니다. 품목은 이름이나 HS 코드(4·6자리)로 검색해 고른 뒤 세부 품목 칩에서 HS6로 좁힐 수 있습니다.</p>}
          </div>
          <div ref={resultTop} className="card" style={{ scrollMarginTop: 90 }}>
            <div className="between">
              <div>
                <h2 className="t2" title={countryLabel !== countryFull ? countryFull : undefined}>{title}</h2>
                <p className="muted" style={{ marginTop: 6 }}>{query.period_start} ~ {query.period_end} {byMonth ? '월별' : '누계'} · 억 USD · 관세청{!byMonth && total != null && ` · 총 ${total.toLocaleString('ko-KR')}건`}</p>
              </div>
              <div className="seg" role="group" aria-label="순위 기준">
                <button type="button" className={'reset' + (basis === 'exp' ? ' on' : '')} aria-pressed={basis === 'exp'} onClick={() => { setBasis('exp'); setPage(1) }}>수출 기준</button>
                <button type="button" className={'reset' + (basis === 'imp' ? ' on' : '')} aria-pressed={basis === 'imp'} onClick={() => { setBasis('imp'); setPage(1) }}>수입 기준</button>
              </div>
            </div>
            {/* 좁은 화면에서는 표만 가로로 스크롤한다. */}
            <div style={{ overflowX: 'auto', marginTop: 18 }}><div style={{ minWidth: 520 }}>
            <div className="th" style={{ gridTemplateColumns: cols }}>
              <span>{byMonth ? '구분' : '순위'}</span>{showHs && <span>품목코드(HS6)</span>}<span>{firstCol}</span><span>{label[basis]} (억 USD)</span><span className="r">{label[other]} (억 USD)</span>
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
                  <b style={{ width: 64, flex: 'none', textAlign: 'right' }}>{eok(r[basis])}</b>
                </span>
                <span className="r s">{eok(r[other])}</span>
              </div>
            ))}
            </div></div>
            {empty && <p className="muted" role="status" style={{ padding: '22px 4px', textAlign: 'center' }}>조회 조건에 맞는 수출입 실적이 없습니다. 국가·품목·기간을 바꿔 다시 조회해 주세요.</p>}
            <div className="between" style={{ marginTop: 18 }}>
              <span className="note">표·막대: 억 USD · 엑셀: 원본 USD 단위{result.data?.meta?.truncated && ` · 엑셀에는 전체 ${Number(result.data.meta.rows_total).toLocaleString()}행 중 상위 1,000행만 담깁니다`}</span>
              <Btn icon="download" disabled={!result.data?.result_id || result.loading || empty} onClick={exportExcel}>엑셀 다운로드</Btn>
            </div>
            {/* 기사 화면과 같은 번호형 페이지 이동(처음·←·1~5·→·끝) */}
            {!byMonth && pages > 1 && <Pager page={page} pages={pages} onChange={setPage} />}
            {overChatLimit && <p className="note" role="status" style={{ marginTop: 10 }}>{chatReason}</p>}
          </div>
        </div>
        <ChatFab disabled={chatBlocked} reason={chatReason} ask={`${askCountry} · ${askProduct} · ${query.period_start} ~ ${query.period_end} ${askKind}를 분석해줘`} />
      </main>
    </>
  )
}

/** 국가 검색 선택: 국가명을 입력하면 목록이 좁혀진다. */
function CountrySearch({ value, replaceInitial, onChange }) {
  const options = [['', '전체 국가'], ...countries.map(c => [c.country_code, c.name])]
  const all = countries.length > 0 && countries.every(c => value.includes(c.country_code))
  const names = value.map(countryName)
  const fullLabel = names.join(' · ')
  const label = all ? `전체 국가 (${value.length}개)` : countryListLabel(names)
  const [text, setText] = useState(null)
  const typing = text !== null
  const shown = typing ? options.filter(([v, l]) => !v || l.includes(text.trim())) : options
  const matches = typing ? options.filter(([, l]) => text.trim() && l.includes(text.trim())) : []
  // 방향키로 고르는 중인 선택지(-1이면 없음). 입력이 바뀌면 처음부터 다시 고른다.
  const [active, setActive] = useState(-1)
  const pick = code => {
    if (!code) onChange(all ? [] : countries.map(c => c.country_code))
    else if (value.includes(code)) onChange(value.filter(c => c !== code))
    else onChange(replaceInitial ? [code] : [...value, code])
    setText(null)
    setActive(-1)
  }
  useEffect(() => { if (active >= 0) document.getElementById(`country-option-${active}`)?.scrollIntoView({ block: 'nearest' }) }, [active])
  const onKeyDown = e => {
    if (e.nativeEvent.isComposing || e.keyCode === 229) return
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      if (!typing) setText('')
      setActive(i => e.key === 'ArrowDown' ? Math.min(i + 1, shown.length - 1) : Math.max(i - 1, 0))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      if (active >= 0 && shown[active]) pick(shown[active][0])
      else if (matches.length) pick(matches[0][0])
      else setText(null)
    } else if (e.key === 'Escape') { setText(null); setActive(-1) }
  }
  return (
    // 포커스가 검색칸과 목록을 모두 벗어났을 때만 목록을 닫는다(Tab으로 선택지에 들어가도 열린 채 유지).
    <div style={{ flex: 1, position: 'relative', minWidth: 0 }} onBlur={e => { if (!e.currentTarget.contains(e.relatedTarget)) setText(null) }}>
      <div className={'input val' + (typing ? ' focus' : '')} style={{ gap: 8 }}>
        <span className="select-prefix">국가</span>
        <input value={typing ? text : label} title={!typing && label !== fullLabel ? fullLabel : undefined} aria-label="국가" placeholder="국가명 입력"
          role="combobox" aria-expanded={typing} aria-controls="country-listbox" aria-autocomplete="list"
          aria-activedescendant={typing && active >= 0 ? `country-option-${active}` : undefined}
          onFocus={() => setText('')} onClick={() => !typing && setText('')}
          onChange={e => { setText(e.target.value); setActive(-1) }}
          onKeyDown={onKeyDown} />
        <Icon name="search" size={16} color="#9AA0AA" />
      </div>
      {typing && (
        <div role="listbox" id="country-listbox" aria-label="국가 목록" aria-multiselectable="true" style={{ position: 'absolute', top: 56, left: 0, right: 0, zIndex: 20, maxHeight: 280, overflowY: 'auto', background: '#fff', border: '1px solid var(--line)', borderRadius: 12, boxShadow: '0 16px 40px rgba(21,24,30,.12)', padding: 6 }}>
          {shown.map(([v, l], i) => (
            <button type="button" key={v || 'all'} id={`country-option-${i}`} className="reset" role="option" aria-selected={v ? value.includes(v) : all}
              onMouseDown={e => e.preventDefault()} onClick={() => pick(v)} onMouseEnter={() => setActive(i)}
              style={{ display: 'block', width: '100%', textAlign: 'left', padding: '10px 12px', borderRadius: 8, background: i === active ? '#F1F2F5' : undefined }}>{l}{(v ? value.includes(v) : all) && <span aria-hidden="true"> ✓</span>}</button>
          ))}
          {typing && text.trim() && !matches.length && <p className="muted" style={{ padding: '10px 12px' }}>검색 결과가 없습니다.</p>}
        </div>
      )}
    </div>
  )
}
