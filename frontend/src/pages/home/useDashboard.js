import { useQuery } from '../../api/hooks'
import { amount, percent, mapArticle } from '../../api/adapters'
import { countryCode, countryName, productName, registerProducts } from '../../data/catalog'
import useMarketSeries from './useMarketSeries'
import { rankLink } from '../../api/integration'
const colors = ['#C8102E', '#2B2350', '#9A95BF', '#DBA343', '#6C7989']
// 영역마다 기간을 따로 둔다. 순위 카드의 3·6·12개월 버튼은 카드 영역만 바꾼다.
// 주요 지표는 기존 기본값(최근 3개월), 품목별·교역국별 표와 원그래프는 최근 12개월로 맞춘다(F-2).
export const KPI_MONTHS = 3
export const TABLE_MONTHS = 12
export function monthOffset(value, delta) {
  if (!value) return ''
  const [y, m] = value.split('-').map(Number)
  const date = new Date(Date.UTC(y, m - 1 + delta, 1))
  return date.toISOString().slice(0, 7)
}
const direction = value => value > 0 ? 'up' : value < 0 ? 'down' : 'flat'
const growth = (now, before) => before ? (now / before - 1) * 100 : null
// 금액 0은 그 기간에 실적이 없다는 뜻이므로 0 대신 —로 보여 준다(D-3).
export const money = value => value == null || Number(value) === 0 ? '—' : amount(value)
const periodText = data => `${data?.meta?.period?.start || '—'} ~ ${data?.meta?.period?.end || '—'} · 억 USD`
export default function useDashboard(view, months, profile) {
  const scope = view === 'interest' ? 'INTERESTED' : 'GLOBAL'
  const availability = useQuery('/data/availability')
  const end = availability.data?.trade_end_month
  const rangeOf = n => ({ scope, period_start: monthOffset(end, 1 - n), period_end: end })
  const kpiRange = rangeOf(KPI_MONTHS), cardRange = rangeOf(months), tableRange = rangeOf(TABLE_MONTHS)
  const country_codes = scope === 'INTERESTED' ? profile.countries.map(countryCode) : []

  // 주요 지표
  const kpis = useQuery('/trade/kpis', {}, scope === 'GLOBAL')
  const trends = useMarketSeries(end ? [{ ...kpiRange }] : [])
  const latestRows = trends.data.flatMap(d => d.items)
  const sum = key => latestRows.length && latestRows.every(r => r[key] != null) ? latestRows.reduce((s, r) => s + Number(r[key]), 0) : null
  const latest = Object.fromEntries(['export_amount_usd', 'import_amount_usd', 'previous_year_export_usd', 'previous_year_import_usd'].map(k => [k, sum(k)]))
  const exp = latest.export_amount_usd, imp = latest.import_amount_usd
  const expYoy = growth(exp, latest.previous_year_export_usd), impYoy = growth(imp, latest.previous_year_import_usd)
  const mom = scope === 'GLOBAL' ? kpis.data?.kpi.mom_pct ?? null : null
  const noTrade = exp == null || imp == null || (Number(exp) === 0 && Number(imp) === 0)

  // 수출 상위 품목 카드
  const cardProducts = useQuery('/trade/rankings', { ...cardRange, group_by: 'HS6', page_size: 5 }, !!end)
  const series = useMarketSeries((cardProducts.data?.items || []).map(r => ({ hs_code: r.hs_item.hs_code, hs_ver: 'HS2022', country_codes, period_start: cardRange.period_start, period_end: end })))

  // 품목별·교역국별 수출입(표·원그래프)
  const tableProducts = useQuery('/trade/rankings', { ...tableRange, group_by: 'HS6', page_size: 5 }, !!end)
  const countries = useQuery('/trade/rankings', { ...tableRange, group_by: 'COUNTRY', page_size: 5 }, !!end)
  const shareEnd = end ? new Date(Date.UTC(Number(end.slice(0, 4)), Number(end.slice(5, 7)), 0)).toISOString().slice(0, 10) : null
  const shares = useQuery('/analysis/market-shares', { conditions: {
    hs_items: scope === 'INTERESTED' ? profile.products.map(hs_code => ({ hs_code, hs_ver: 'HS2022' })) : [],
    country_codes, period: { start: tableRange.period_start ? `${tableRange.period_start}-01` : null, end: shareEnd }, trade_flow: 'EXPORT',
  } }, !!end, 0, 'POST')

  const fx = useQuery('/exchange-rates', { scope })
  const news = useQuery('/articles', { scope, page_size: 4 })

  // 순위 응답의 품목명을 기억해 두어 코드 대신 이름을 보여 준다.
  registerProducts([...(cardProducts.data?.items || []), ...(tableProducts.data?.items || [])].filter(r => r.hs_name).map(r => ({ hs_code: r.hs_item.hs_code, name_ko: r.hs_name })))
  const ranks = (cardProducts.data?.items || []).map((r, i) => ({ rank: r.rank, hs: r.hs_item.hs_code, name: productName(r.hs_item.hs_code), exp: money(r.export_amount_usd), imp: money(r.import_amount_usd), values: series.data[i]?.items.map(t => Number(t.export_amount_usd)), color: colors[0] }))
  const productRows = (tableProducts.data?.items || []).map(r => [r.rank, r.hs_item.hs_code, productName(r.hs_item.hs_code), money(r.export_amount_usd), money(r.import_amount_usd)])
  // 선택 국가를 채우려고 서버가 넣은 실적 없는 행은 0이 아니라 결측(—)으로 보여 준다.
  const countryRows = (countries.data?.items || []).map(r => [r.rank, countryName(r.country_code), r.has_data === false ? '—' : money(r.export_amount_usd), r.has_data === false ? '—' : money(r.import_amount_usd)])
  const link = n => (group, product, country) => rankLink({ scope, start: monthOffset(end, 1 - n), end, months: n, countries: country_codes, group, product, country })
  const tablePeriod = periodText(countries.data || tableProducts.data)
  // 영역별 오류 안내: 기준 월 조회가 실패하면 모든 영역이 비므로 각 영역에 함께 넣는다.
  const states = {
    kpi: [availability, kpis, trends], fx: [fx], cards: [availability, cardProducts, series],
    products: [availability, tableProducts], countries: [availability, countries, shares], news: [news],
  }
  return {
    states, statMonth: end || '확인 중', title: scope === 'GLOBAL' ? '대한민국 수출입 현황' : '내 관심 시장 수출입 현황',
    period: `${kpiRange.period_start || '—'} ~ ${end || '—'} · 억 USD`, kpiLink: link(KPI_MONTHS),
    kpis: [
      { label: '수출액', value: money(exp), unit: '억 USD', rows: [['전년 대비', percent(expYoy), direction(expYoy)]] },
      { label: '수입액', value: money(imp), unit: '억 USD', rows: [['전년 대비', percent(impYoy), direction(impYoy)]] },
      { label: '무역수지', value: noTrade ? '—' : amount(exp - imp), unit: '억 USD', rows: [['전월 수출 증감', percent(mom), direction(mom)]] },
    ],
    fxTitle: scope === 'GLOBAL' ? '주요 통화 환율' : '관심 국가 환율', fxDate: fx.data?.items?.[0]?.base_date || '기준일 미제공',
    fx: (fx.data?.items || []).map(r => [r.currency_code, `${r.quote_unit} 단위`, r.rate_krw == null ? '—' : Number(r.rate_krw).toLocaleString('ko-KR'), percent(r.change_pct), r.change_pct > 0 ? 'up' : r.change_pct < 0 ? 'down' : 'flat']),
    rankTitle: '수출 상위 품목', rankSub: periodText(cardProducts.data), rankCards: ranks, cardLink: link(months),
    cardsEmpty: !!cardProducts.data && !ranks.length, tableLink: link(TABLE_MONTHS),
    productTable: { title: '품목별 수출입', sub: periodText(tableProducts.data), rows: productRows, note: '공개된 관세청 집계 기준', empty: !!tableProducts.data && !productRows.length },
    countryTable: { title: '교역국별 수출입', sub: tablePeriod, rows: countryRows, empty: !!countries.data && !(countries.data.items || []).some(r => r.has_data !== false), pieTitle: '수출액 비중', pie: (shares.data?.items || []).filter(r => r.share_pct != null).map((r, i) => [`${r.label} ${Number(r.share_pct).toFixed(1)}%`, colors[i % colors.length]]), pieNote: `${tableRange.period_start || '—'} ~ ${end || '—'} · 전체 수출국 분모 · 상위 10개국+기타` },
    un: [], unSub: '관심 HS6·국가의 UN 데이터 공개 여부를 확인합니다.',
    newsTitle: scope === 'GLOBAL' ? '최신 해외시장 기사' : '내 관심 기사', newsSub: '기사별 출처와 원문을 확인하세요.', newsCards: (news.data?.items || []).map(mapArticle),
  }
}
