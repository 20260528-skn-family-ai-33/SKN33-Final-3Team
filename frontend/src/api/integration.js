// Shared request rules; kept independent of rendering so URL/expiry contracts can be tested.
export function previewExpired(error) {
  return error?.code === 'PREVIEW_EXPIRED' || (error?.status === 410 && error?.code === 'RESULT_EXPIRED')
}
export function reportComparisonInputs(conditions) {
  return {
    compareFrom: conditions.compare === 'CUSTOM' ? conditions.compare_period?.start.slice(0, 7) || '' : '',
    compareTo: conditions.compare === 'CUSTOM' ? conditions.compare_period?.end.slice(0, 7) || '' : '',
  }
}
/**
 * 채팅 결과를 리포트 조건 화면의 기본값으로 옮긴다(C-05).
 * 채팅은 "2024년과 2025년 비교"처럼 비교 기간이 분석 기간보다 뒤인 조건도 그대로 돌려주지만,
 * 리포트의 사용자 지정 비교는 비교 기간이 분석 기간보다 앞서야 한다(reports.resolve_comparison).
 * - 비교 기간이 분석 기간보다 완전히 뒤면 둘을 바꿔 최신 기간을 분석, 이전 기간을 비교로 둔다.
 * - 두 기간이 겹치면 사용자 지정 비교를 비워 기본 비교(전년 같은 기간)를 쓴다.
 */
export function reportPeriodInputs(conditions) {
  const from = conditions.period.start.slice(0, 7), to = conditions.period.end.slice(0, 7)
  const { compareFrom, compareTo } = reportComparisonInputs(conditions)
  if (!compareFrom || !compareTo || compareTo < from) return { from, to, compareFrom, compareTo }
  if (compareFrom > to) return { from: compareFrom, to: compareTo, compareFrom: from, compareTo: to }
  return { from, to, compareFrom: '', compareTo: '' }
}
export function reportConditions(options) {
  const monthEnd = value => {
    const [year, month] = value.split('-').map(Number)
    return `${value}-${new Date(Date.UTC(year, month, 0)).getUTCDate()}`
  }
  const custom = options.compareFrom || options.compareTo
  const mode = options.conditions.compare === 'CUSTOM' ? 'YOY_PERIOD' : options.conditions.compare || 'YOY_PERIOD'
  const conditions = { ...options.conditions, period: { start: `${options.from}-01`, end: monthEnd(options.to) }, compare: custom ? 'CUSTOM' : mode }
  if (options.compareFrom && options.compareTo) conditions.compare_period = { start: `${options.compareFrom}-01`, end: monthEnd(options.compareTo) }
  else delete conditions.compare_period
  return conditions
}
export async function mapLimited(items, limit, load, signal) {
  const results = new Array(items.length)
  let cursor = 0, stopped = false
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (!stopped && cursor < items.length) {
      try {
        if (signal?.aborted) throw new DOMException('Aborted', 'AbortError')
        const index = cursor++
        results[index] = await load(items[index], index)
      } catch (error) { stopped = true; throw error }
    }
  }))
  return results
}
export function comparisonPeriod(conditions, rows) {
  if (conditions?.compare === 'NONE') return null
  if (conditions?.compare_period) return conditions.compare_period
  const key = conditions?.trade_flow === 'IMPORT' ? 'previous_year_import_usd' : 'previous_year_export_usd'
  if (!rows?.length || !rows.every(r => typeof r[key] === 'number') || !conditions?.period) return null
  const shift = value => `${Number(value.slice(0, 4)) - 1}${value.slice(4, 7)}`
  const start = shift(conditions.period.start), end = shift(conditions.period.end)
  const last = new Date(Date.UTC(Number(end.slice(0, 4)), Number(end.slice(5, 7)), 0)).getUTCDate()
  return { start: `${start}-01`, end: `${end}-${last}` }
}
// flow: 'IMPORT'이면 순위 화면이 수입 기준으로 열린다. 없으면 기존처럼 수출 기준이다.
export function rankLink({ scope, start, end, months, countries = [], group, product, country, flow }) {
  const q = new URLSearchParams({ scope, period: String(months), group_by: group })
  if (start) q.set('period_start', start)
  if (end) q.set('period_end', end)
  countries.forEach(c => q.append('country_codes', c))
  if (product) q.set('product', product)
  if (country) q.set('country', country)
  if (flow === 'IMPORT') q.set('sort_flow', 'IMPORT')
  return `/rank?${q}`
}
/** 국가 이름 목록을 제목·선택칸에 쓸 짧은 글로 만든다. 3개까지는 ' · '로 잇고, 그보다 많으면 '첫 국가 외 N개국'으로 줄인다.
 *  (관심 국가를 대륙 단위로 고르면 수십 개국이 한 줄에 이어져 제목이 화면을 덮던 문제) */
export const countryListLabel = (names, max = 3, unit = '개국', separator = ' · ') =>
  names.length <= max ? names.join(separator) : `${names[0]} 외 ${names.length - 1}${unit}`
/** 순위 화면의 처음 정렬 기준. 링크에 sort_flow=IMPORT가 있으면 수입 기준('imp'), 아니면 수출 기준('exp'). */
export const rankBasis = query => query.get('sort_flow') === 'IMPORT' ? 'imp' : 'exp'
export function rankInitial(query) {
  const inherited = query.has('scope')
  return {
    country: query.get('country') ?? (inherited || query.get('product') ? '' : '중국'),
    product: query.get('product') || '',
    period: ['1', '3', '6', '12'].includes(query.get('period')) ? query.get('period') : '3',
    scope: query.get('scope') === 'INTERESTED' ? 'INTERESTED' : 'GLOBAL',
    countryCodes: query.getAll('country_codes'),
    start: query.get('period_start') || '', end: query.get('period_end') || '',
    group: ['HS6', 'COUNTRY'].includes(query.get('group_by')) ? query.get('group_by') : null,
    inherited,
  }
}
