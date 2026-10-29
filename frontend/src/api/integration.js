// Shared request rules; kept independent of rendering so URL/expiry contracts can be tested.
export function previewUsable(preview, signature, now = Date.now()) {
  return preview?.signature === signature && Date.parse(preview.expires_at) > now
}
export function previewExpired(error) {
  return error?.code === 'PREVIEW_EXPIRED' || (error?.status === 410 && error?.code === 'RESULT_EXPIRED')
}
export function reportComparisonInputs(conditions) {
  return {
    compareFrom: conditions.compare === 'CUSTOM' ? conditions.compare_period?.start.slice(0, 7) || '' : '',
    compareTo: conditions.compare === 'CUSTOM' ? conditions.compare_period?.end.slice(0, 7) || '' : '',
  }
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
export function rankLink({ scope, start, end, months, countries = [], group, product, country }) {
  const q = new URLSearchParams({ scope, period: String(months), group_by: group })
  if (start) q.set('period_start', start)
  if (end) q.set('period_end', end)
  countries.forEach(c => q.append('country_codes', c))
  if (product) q.set('product', product)
  if (country) q.set('country', country)
  return `/rank?${q}`
}
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
