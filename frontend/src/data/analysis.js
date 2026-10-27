// AI 분석 시연 로직: 질문에서 국가·품목·기간을 읽고, 답변·리포트 본문에 쓸 수치를 만든다.
// 실제 서비스에서는 이 파일 대신 분석 API(LLM + 통계 조회) 응답을 사용한다.
import { countries, productKeyword, productName, products } from './catalog.js'
import { articles } from './articles.js'
import { monthlySeries, tradeRanking } from './trade.js'
import { ALL_SECTIONS } from './seed.js'

export const PERIOD_PRESETS = [
  ['1m', '최근 1개월', ['2025-12', '2025-12']],
  ['3m', '최근 3개월', ['2025-10', '2025-12']],
  ['6m', '최근 6개월', ['2025-07', '2025-12']],
  ['12m', '최근 12개월', ['2025-01', '2025-12']],
  ['ytd', '올해', ['2026-01', '2026-09']],
  ['last', '작년', ['2025-01', '2025-12']],
]

const ALIASES = { 반도체: '854232', 메모리: '854232', 디램: '854232', 화장품: '330499', 자동차: '870323', 승용차: '870323', 전기차: '870380', 석유: '271019', 배터리: '850760', 축전지: '850760' }

const shift = (ym, years) => `${Number(ym.slice(0, 4)) + years}${ym.slice(4)}`
export const dot = ym => ym.replace('-', '.')
export const range = (a, b) => (a === b ? dot(a) : `${dot(a)}~${a.slice(0, 4) === b.slice(0, 4) ? b.slice(5) : dot(b)}`)

/** 자연어 질문 → { country, product, preset, from, to, compareFrom, compareTo, wantsReport } */
export function parseQuestion(text, previous = {}) {
  const country = text.includes('전체 국가') ? '' : countries.find(c => text.includes(c.name))?.name ?? previous.country ?? ''
  const code = text.match(/\b(\d{6})\b/)?.[1]
  const alias = Object.keys(ALIASES).find(k => text.includes(k))
  const product = text.includes('전체 품목') ? '' : ((code && products.some(p => p.id === code) ? code : null) || products.find(p => text.includes(p.name))?.id || (alias && ALIASES[alias]) || previous.product) ?? '854232'
  const presetKey = text.includes('12개월') ? '12m' : text.includes('6개월') ? '6m' : text.includes('1개월') ? '1m' : text.includes('올해') ? 'ytd' : text.includes('작년') ? 'last' : null
  const explicitPeriod = text.match(/\b(\d{4}-(?:0[1-9]|1[0-2]))~(\d{4}-(?:0[1-9]|1[0-2]))\b/)
  const validPeriod = explicitPeriod && explicitPeriod[1] <= explicitPeriod[2]
  const [from, to] = validPeriod ? explicitPeriod.slice(1)
    : !presetKey && previous.from && previous.to ? [previous.from, previous.to]
      : (PERIOD_PRESETS.find(p => p[0] === (presetKey || previous.preset)) || PERIOD_PRESETS[1])[2]
  return {
    country, product, preset: validPeriod ? 'custom' : presetKey || previous.preset || '3m', from, to, compareFrom: shift(from, -1), compareTo: shift(to, -1),
    sections: ALL_SECTIONS, wantsReport: /리포트|보고서|PDF|pdf/.test(text),
  }
}

export function presetRange(key) {
  const [, , [from, to]] = PERIOD_PRESETS.find(p => p[0] === key)
  return { preset: key, from, to, compareFrom: shift(from, -1), compareTo: shift(to, -1) }
}

const monthLabels = (from, to) => {
  const start = Number(from.slice(0, 4)) * 12 + Number(from.slice(5)) - 1
  const end = Number(to.slice(0, 4)) * 12 + Number(to.slice(5)) - 1
  return Array.from({ length: Math.min(12, end - start + 1) }, (_, i) => `${((start + i) % 12) + 1}월`)
}
const pct = (a, b) => (b ? ((a / b - 1) * 100) : 0)
const signed = v => `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`

/** 질문 답변·리포트 공통 수치 */
export function figures(options) {
  const { country, product, from, to } = options
  const labels = monthLabels(from, to)
  const base = monthlySeries(country, product)
  const n = labels.length
  const current = n === 3 ? base.current : labels.map((_, i) => +(base.current[i % 3] * (0.92 + (i % 5) * 0.03)).toFixed(1))
  const previous = n === 3 ? base.previous : labels.map((_, i) => +(base.previous[i % 3] * (0.92 + (i % 5) * 0.03)).toFixed(1))
  const sum = a => a.reduce((s, v) => s + v, 0)
  const total = +sum(current).toFixed(1)
  const prevTotal = +sum(previous).toFixed(1)
  const growth = pct(total, prevTotal)
  const imp = +(total * 0.252).toFixed(1)
  const countryRows = country
    ? [{ name: country, exp: total, share: 100, growth }]
    : tradeRanking({ product, from, to }).slice(0, 5).map((r, i, all) => ({ name: r.name, exp: r.exp, share: Math.round((r.exp / all.reduce((s, x) => s + x.exp, 0)) * 100), growth: 12 - i * 4.5 }))
  const keyword = productKeyword(product)
  const news = articles.filter(a => (!country || a.country === country) && (a.title.includes(keyword) || a.title.includes('반도체'))).slice(0, 3)
  return {
    labels, current, previous, total, prevTotal, growth, growthText: signed(growth), imp,
    flow: `${current[0]} → ${current[current.length - 1]}`, countryRows, news,
    title: `${country || '전체 국가'} ${productName(product)}`,
  }
}

export function reportParagraphs(report) {
  const o = report.options
  const f = figures(o)
  const lines = [
    `${report.title} · Borderless Sellers ${report.kind === 'chat' ? '요청형' : '정기'} 리포트 (화면 시연용 예시 수치)`,
    `대상: ${o.country || '전체 국가'} / ${productName(o.product)}${o.product ? ` (HS ${o.product})` : ''}`,
    `분석 기간 ${range(o.from, o.to)} · 비교 기간 ${range(o.compareFrom, o.compareTo)} · 생성일 ${report.created}`,
    `01 핵심 요약: 선택 기간 ${o.country || '전체'}향 ${productName(o.product)} 수출액은 ${f.total}억 USD로 전년 동기 대비 ${f.growthText}입니다.`,
  ]
  if (o.sections.includes('주요 지표(KPI)')) lines.push(`02 주요 지표: 기간 수출액 ${f.total}억 USD · 전년 대비 ${f.growthText} · 수입액 ${f.imp}억 USD · 월별 ${f.labels.map((l, i) => `${l} ${f.current[i]}`).join(', ')}`)
  if (o.sections.includes('주요 수출국')) lines.push(`03 주요 수출국: ${f.countryRows.map(r => `${r.name} ${r.exp}억(${r.share}%)`).join(', ')}`)
  if (o.sections.includes('급성장·리스크 시장')) lines.push(`04 급성장·리스크 시장: 성장률 상위 ${f.countryRows[0]?.name} ${f.growthText} · 감소율 상위 해당 국가 없음`)
  if (o.sections.includes('현지 이슈 기사')) lines.push(`05 현지 이슈: ${f.news.map(a => `${a.title}(${a.date})`).join(' / ') || '해당 기간 기사 없음'}`)
  lines.push('출처: 관세청 수출입 통계 · KOTRA 해외시장뉴스 · 한국은행 ECOS (시연 데이터)')
  return lines
}
