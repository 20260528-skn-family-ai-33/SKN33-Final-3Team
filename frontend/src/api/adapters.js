import { countryName, productName } from '../data/catalog.js'
// 화면 시각은 모두 한국 시각(KST)이다(PR #56). 다운로드 파일명도 KST다.
const timeFormatter = new Intl.DateTimeFormat('ko-KR', { timeZone: 'Asia/Seoul', dateStyle: 'medium', timeStyle: 'short' })
const kstFormatter = new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' })
export function kstDateTime(iso) {
  const date = iso && new Date(iso)
  return date && Number.isFinite(date.getTime()) ? kstFormatter.format(date) : null
}
export function displayTime(iso) {
  const date = iso && new Date(iso)
  return date && Number.isFinite(date.getTime()) ? timeFormatter.format(date) : '시각 미확인'
}
/** yyyy-mm-dd hh:mm:ss 한국 시각. 값이 없거나 잘못되면 '시각 미확인'. */
export function kstDateTimeText(iso) {
  return kstDateTime(iso) || '시각 미확인'
}
export function monthEndDate(value) {
  const match = /^(\d{4})-(0[1-9]|1[0-2])(?:-\d{2})?$/.exec(value || '')
  if (!match) return value
  const end = new Date(Date.UTC(Number(match[1]), Number(match[2]), 0))
  return end.toISOString().slice(0, 10)
}
export function reportProduct(code, name = productName(code)) {
  if (!name || name === code) return 'HS ' + code
  return `${name.length > 32 ? name.slice(0,31) + '…' : name} (HS ${code})`
}
export function reportScope(c, names = {}) {
  if (!c) return '조건 미확인'
  const countries = [...(c.include_korea_total?['한국(대한민국) 전체']:[]),...(c.country_codes||[]).map(countryName)].join(', ') || '전체 국가'
  const products = c.hs_items?.map(h => reportProduct(h.hs_code, names[h.hs_code])).join(', ') || '전체 품목'
  return `${countries} · ${products}`
}
export function mapNotification(n) {
  return { ...n, id: n.user_alert_id, type: n.event_type === 'REPORT' ? 'report' : n.event_type === 'MAJOR_ISSUE' ? 'major' : 'surge', body: n.message, read: !!n.read_at, received: displayTime(n.created_at), time: displayTime(n.created_at), reportId: n.target_type === 'REPORT' ? n.target_id : null, articleId: n.target_type === 'ARTICLE' ? n.target_id : n.issue?.articles?.[0]?.article_id ?? null, ratio: n.issue?.ratio ?? '—', formula: '현재 건수 ÷ 기준 평균', basis: `${n.issue?.current_count ?? '—'} / ${n.issue?.baseline_average ?? '—'}` }
}
/** 알림을 눌렀을 때 열 화면(FR-305-03). 기사·리포트가 없으면 알림 상세로 간다. */
export function notificationTarget(n) {
  if (n.reportId) return `/report?id=${n.reportId}`
  return n.articleId ? `/articles?id=${n.articleId}` : `/account/notifications?id=${n.id}`
}
const REPORT_CYCLE_TONES = { 주간: 'amber', 월간: 'plum', 분기: 'ok' }
const ISSUE_REASON_TAGS = { WEEKLY_NEWS_SPIKE: ['기사 급증', 'amber'], KOTRA_TRADE_REGULATION: ['통상·규제', 'plum'] }
/** 팝업에서 한 건을 구분할 표시. 리포트는 주기, 주요 이슈는 판정 이유로 나눈다. 구분할 값이 없으면 null. */
export function popupItemTag(n) {
  if (n.event_type === 'REPORT') {
    const cycle = /^(주간|월간|분기)/.exec(n.title || '')?.[1]
    return cycle ? { label: cycle, tone: REPORT_CYCLE_TONES[cycle] } : null
  }
  const issue = ISSUE_REASON_TAGS[n.issue?.reason]
  return issue ? { label: issue[0], tone: issue[1] } : null
}
export function mapPopupGroup(group) {
  const items = group.notifications.map(mapNotification)
  return { deliveryId: group.delivery_id, type: items[0]?.type, items }
}
export function mapReport(r) {
  const status = ({ READY: 'ready', QUEUED: 'generating', RUNNING: 'generating', DELAYED: 'delayed', FAILED: 'failed' })[r.status]
  const label = { READY: '발행 완료', QUEUED: '생성 대기', RUNNING: '생성 중', DELAYED: '재시도 대기', FAILED: '생성 실패' }[r.status] || '상태 미확인'
  return { ...r, title: r.conditions ? `${reportScope(r.conditions, r.hs_names)} · ${r.title}` : r.title, id: r.report_id, kind: ({ RPT_WEEKLY: 'weekly', RPT_MONTHLY: 'monthly', RPT_QUARTERLY: 'quarterly' })[r.report_type] || 'chat', status, created: r.created_at || '', meta: `${kstDateTimeText(r.created_at)} · ${label}`, options: r.conditions }
}
// 억 USD 표시. 0.1억 이상은 소수 첫째 자리까지, 0.1억 미만은 유효숫자 1자리(0.02·0.0005)로 보여 준다(D-3).
export const amount = value => value == null ? '—' : (Number(value) / 1e8).toLocaleString('ko-KR', Number(value) !== 0 && Math.abs(Number(value)) < 1e7 ? { maximumSignificantDigits: 1 } : { maximumFractionDigits: 1 })
// 리포트(웹·PDF 공통)의 억 USD 숫자. 기본 소수 2자리, 0.1억 미만은 유효숫자 1자리(0.03·0.0005)로 늘려 0.00으로 뭉개지지 않게 한다.
// 반올림은 PDF(report_templates._round_eok)와 같다: 값의 최단 십진 표기를 기준으로 0.5는 0에서 먼 쪽으로 올린다.
// 브라우저 Intl은 엔진마다 경계값(0.00015 등) 처리가 다를 수 있어 직접 반올림한다.
export function reportEok(value) {
  if (value == null || value === '' || Number.isNaN(Number(value))) return '—'
  const v = Number(value)
  const [mantissa, exp] = Math.abs(v).toExponential().split('e')   // 최단 십진 표기: 1.5e-4
  const digits = mantissa.replace('.', ''), e = Number(exp)
  const places = v && Math.abs(v) < 0.1 ? Math.max(2, -e) : 2
  const keep = e + 1 + places   // 정수로 남길 자릿수
  let n = keep <= 0 ? 0n : BigInt(digits.slice(0, keep).padEnd(keep, '0'))
  if (keep >= 0 && keep < digits.length && digits[keep] >= '5') n += 1n
  const text = n.toString().padStart(places + 1, '0')
  const whole = text.slice(0, -places).replace(/\B(?=(\d{3})+(?!\d))/g, ','), frac = text.slice(-places)
  const body = places === 2 ? `${whole}.${frac}` : `${whole}.${frac}`.replace(/\.?0+$/, '')
  return (v < 0 && n > 0n ? '-' : '') + body
}
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
