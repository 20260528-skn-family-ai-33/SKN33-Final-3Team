import { countryName } from '../data/catalog.js'
export function mapNotification(n) {
  return { ...n, id: n.user_alert_id, type: n.event_type === 'REPORT' ? 'report' : n.event_type === 'MAJOR_ISSUE' ? 'major' : 'surge', body: n.message, read: !!n.read_at, received: n.created_at, time: n.created_at?.slice(0, 10), reportId: n.target_type === 'REPORT' ? n.target_id : null, articleId: n.target_type === 'NEWS' ? n.target_id || n.issue?.articles?.[0]?.article_id : null, ratio: n.issue?.ratio ?? '—', formula: '현재 건수 ÷ 기준 평균', basis: `${n.issue?.current_count ?? '—'} / ${n.issue?.baseline_average ?? '—'}` }
}
export function mapReport(r) {
  const status = ({ READY: 'ready', QUEUED: 'generating', RUNNING: 'generating', DELAYED: 'delayed', FAILED: 'failed' })[r.status]
  return { ...r, id: r.report_id, kind: ({ RPT_WEEKLY: 'weekly', RPT_MONTHLY: 'monthly', RPT_QUARTERLY: 'quarterly' })[r.report_type] || 'chat', status, created: r.created_at || '', meta: `${r.created_at || ''} · ${r.status}`, options: r.conditions }
}
export const amount = value => value == null ? '—' : (Number(value) / 1e8).toLocaleString('ko-KR', Number(value) !== 0 && Math.abs(Number(value)) < 1e7 ? { maximumSignificantDigits: 3 } : { maximumFractionDigits: 1 })
export const percent = value => value == null ? '—' : `${Number(value) > 0 ? '+' : ''}${Number(value).toFixed(1)}%`
export function reportComparison(conditions, kpi) {
  const mode = conditions?.compare || 'YOY_PERIOD'
  const labels = { MOM: '전월 대비', YOY: '전년 동월 대비', YOY_PERIOD: '전년 동기 대비', CUSTOM: '비교 기간 대비', NONE: '비교 없음' }
  // Older saved reports have only mom_pct/yoy_pct; never substitute YOY for CUSTOM.
  const fallback = mode === 'MOM' ? kpi.mom_pct : ['YOY', 'YOY_PERIOD'].includes(mode) ? kpi.yoy_pct : null
  const growth = mode === 'NONE' ? null : Object.hasOwn(kpi, 'comparison_pct') ? kpi.comparison_pct : fallback
  return { label: labels[mode] || '비교 기간 대비', growth, previous: mode === 'NONE' ? null : kpi.previous_amount_usd }
}
const ARTICLE_SOURCE_NAMES = new Map([
  ['KOTRA_MARKET', 'KOTRA 해외시장뉴스'],
  ['KOTRA_SHORT_NEWS', 'KOTRA 단신속보'],
  ['HANKYUNG', '한국경제'],
  ['FNNEWS', '파이낸셜뉴스'],
])
export const mapArticle = a => ({ ...a, id: a.article_id, kind: a.is_major_issue ? 'major' : a.source_code === 'KOTRA_SHORT_NEWS' ? 'short' : 'general', date: a.published_date, country: (a.country_codes || []).map(countryName).join(' · '), source: ARTICLE_SOURCE_NAMES.get(a.source_code) || a.source_code || '출처 미제공', tags: (a.hs_items || []).map(h => h.name_ko || h.hs_code), url: a.source_url })
