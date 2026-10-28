import { useQuery } from '../../api/hooks'
import { amount, percent, mapArticle } from '../../api/adapters'
import { countryCode, countryName, productName } from '../../data/catalog'
import useMarketSeries from './useMarketSeries'
import newsImage from '../../assets/band-articles.jpg'
const colors = ['#C8102E', '#2B2350', '#9A95BF', '#DBA343', '#6C7989']
export function monthOffset(value, delta) {
  if (!value) return ''
  const [y, m] = value.split('-').map(Number)
  const date = new Date(Date.UTC(y, m - 1 + delta, 1))
  return date.toISOString().slice(0, 7)
}
export default function useDashboard(view, months, profile) {
  const scope = view === 'interest' ? 'INTERESTED' : 'GLOBAL'
  const availability = useQuery('/data/availability')
  const end = availability.data?.trade_end_month
  const range = { scope, period_start: monthOffset(end, 1 - months), period_end: end }
  const kpis = useQuery('/trade/kpis', {}, scope === 'GLOBAL')
  const country_codes = scope === 'INTERESTED' ? profile.countries.map(countryCode) : []
  const hsCodes = scope === 'INTERESTED' && profile.products.length ? profile.products : [null]
  const trends = useMarketSeries(hsCodes.map(hs_code => ({ months: 1, country_codes, hs_code, hs_ver: hs_code ? 'HS2022' : undefined })))
  const products = useQuery('/trade/rankings', { ...range, group_by: 'HS6', page_size: 5 }, !!end)
  const countries = useQuery('/trade/rankings', { ...range, group_by: 'COUNTRY', page_size: 5 }, !!end)
  const fx = useQuery('/exchange-rates', { scope })
  const news = useQuery('/articles', { scope, page_size: 4 })
  const latestRows = trends.data.map(d => d.items.at(-1)).filter(Boolean)
  const sum = key => latestRows.length && latestRows.every(r => r[key] != null) ? latestRows.reduce((s, r) => s + Number(r[key]), 0) : null
  const latest = Object.fromEntries(['export_amount_usd', 'import_amount_usd', 'previous_year_export_usd', 'previous_year_import_usd'].map(k => [k, sum(k)]))
  const exp = latest?.export_amount_usd, imp = latest?.import_amount_usd
  const period = `${products.data?.meta?.period?.start || '—'} ~ ${products.data?.meta?.period?.end || '—'} · 억 USD`
  const series = useMarketSeries((products.data?.items || []).map(r => ({ hs_code: r.hs_item.hs_code, hs_ver: 'HS2022', country_codes, period_start: range.period_start, period_end: end })))
  const ranks = (products.data?.items || []).map((r, i) => ({ rank: r.rank, hs: r.hs_item.hs_code, name: productName(r.hs_item.hs_code), exp: amount(r.export_amount_usd), imp: amount(r.import_amount_usd), values: series.data[i]?.items.map(t => Number(t.export_amount_usd)), color: colors[0] }))
  const states = [availability, kpis, trends, products, countries, fx, news, series]
  return {
    states, statMonth: end || '확인 중', title: scope === 'GLOBAL' ? '대한민국 수출입 현황' : '내 관심 시장 수출입 현황', period,
    kpis: [
      { label: '수출액', value: amount(exp), unit: '억 USD', rows: [['전년 대비', percent(latest?.previous_year_export_usd ? (exp / latest.previous_year_export_usd - 1) * 100 : null), 'flat']] },
      { label: '수입액', value: amount(imp), unit: '억 USD', rows: [['전년 대비', percent(latest?.previous_year_import_usd ? (imp / latest.previous_year_import_usd - 1) * 100 : null), 'flat']] },
      { label: '무역수지', value: exp == null || imp == null ? '—' : amount(exp - imp), unit: '억 USD', rows: [['전월 수출 증감', scope === 'GLOBAL' ? percent(kpis.data?.kpi.mom_pct) : '—', 'flat']] },
    ],
    fxTitle: scope === 'GLOBAL' ? '주요 통화 환율' : '관심 국가 환율', fxDate: fx.data?.items?.[0]?.base_date || '기준일 미제공',
    fx: (fx.data?.items || []).map(r => [r.currency_code, `${r.quote_unit} 단위`, r.rate_krw == null ? '—' : Number(r.rate_krw).toLocaleString('ko-KR'), percent(r.change_pct), r.change_pct > 0 ? 'up' : r.change_pct < 0 ? 'down' : 'flat']),
    rankTitle: '수출 상위 품목', rankSub: period, rankCards: ranks,
    productTable: { title: '품목별 수출입', sub: period, rows: ranks.map(r => [r.rank, r.hs, r.name, r.exp, r.imp]), note: '공개된 관세청 집계 기준' },
    countryTable: { title: '교역국별 수출입', sub: period, rows: (countries.data?.items || []).map(r => [r.rank, countryName(r.country_code), amount(r.export_amount_usd), amount(r.import_amount_usd)]), pieTitle: '수출액 비중', pie: (countries.data?.items || []).filter(r => r.share_pct != null).map((r, i) => [`${countryName(r.country_code)} ${Number(r.share_pct).toFixed(1)}%`, colors[i]]), pieNote: '조회 범위 전체 수출액 대비 비중 · 상위 5개국' },
    un: [], unSub: '관심 HS6·국가의 UN 데이터 공개 여부를 확인합니다.',
    newsTitle: scope === 'GLOBAL' ? '최신 해외시장 기사' : '내 관심 기사', newsSub: 'KOTRA 제공 기사', newsCards: (news.data?.items || []).map(a => ({ ...mapArticle(a), img: newsImage })),
  }
}
