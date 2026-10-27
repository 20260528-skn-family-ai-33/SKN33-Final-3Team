// 수출입 순위(BS08)·리포트·AI 답변에 쓰는 교역 수치. 화면 동작 확인용 합성 데이터다.
// 실제 API 연동 시 tradeRanking / monthlySeries 두 함수만 서버 응답으로 바꾸면 된다.
import { countries, productName } from './catalog.js'

// 전체 국가 합산 최근 3개월 [수출, 수입] (억 USD)
const productBase = {
  854232: [252.0, 60.6], 870323: [153.0, 25.2], 330499: [48.6, 12.3], 271019: [44.4, 85.8], 854239: [38.0, 42.0],
  854231: [32.0, 19.0], 854233: [18.0, 8.2], 850760: [26.4, 14.1], 870380: [22.8, 3.1], 271012: [17.5, 30.2],
  330410: [6.2, 2.1], 330420: [5.1, 1.7], 330491: [3.4, 1.2], 870322: [9.8, 4.4], 850780: [4.6, 3.3],
}
// 국가별 최근 3개월 [수출, 수입]
const countryBase = {
  중국: [372.0, 337.8], 미국: [334.8, 186.3], 베트남: [186.0, 73.8], 일본: [93.0, 122.4], 독일: [64.2, 88.5],
  인도: [60.0, 29.0], 프랑스: [32.0, 31.0], 캐나다: [28.0, 25.0], 인도네시아: [27.0, 23.0], 호주: [24.0, 39.0],
  태국: [21.0, 15.2], 멕시코: [20.4, 9.1], 영국: [18.2, 12.6], 브라질: [12.0, 19.0], 남아프리카공화국: [5.0, 3.0],
  칠레: [4.2, 8.8], 라오스: [0.6, 0.4], 가나: [0.9, 0.3], 뉴질랜드: [3.1, 4.2],
}
// 목업에 표시된 확정 예시(중국 교역 품목 순위)
const fixed = { 중국: { 854232: [157.2, 39.6], 330499: [29.4, 7.2], 854239: [25.8, 18.3], 870323: [21.6, 5.4] } }
const rankName = { 854239: '전자집적회로' }

const totalCountryExport = Object.values(countryBase).reduce((s, [e]) => s + e, 0)
const round1 = v => Math.round(v * 10) / 10
const monthIndex = ym => Number(ym.slice(0, 4)) * 12 + Number(ym.slice(5, 7)) - 1

export function periodMonths(from, to) {
  return Math.max(1, monthIndex(to) - monthIndex(from) + 1)
}

/** 순위 조회. country·product 중 비어 있는 쪽을 순위 대상으로 삼는다. */
export function tradeRanking({ country = '', product = '', from = '2025-10', to = '2025-12', basis = 'exp' }) {
  const scale = periodMonths(from, to) / 3
  let rows
  if (country && !product) {
    const share = (countryBase[country]?.[0] || 1) / totalCountryExport
    const source = fixed[country] || Object.fromEntries(
      Object.entries(productBase).slice(0, 6).map(([id, [e, i]], n) => [id, [e * share * (1.6 - n * 0.12), i * share * (1.4 - n * 0.1)]]),
    )
    rows = Object.entries(source).map(([id, [e, i]]) => ({ key: id, hs: id, name: rankName[id] || productName(id), exp: e, imp: i }))
  } else if (product && !country) {
    const [pe, pi] = productBase[product] || [10, 5]
    rows = Object.entries(countryBase).slice(0, 8).map(([name, [e, i]]) => ({
      key: name, name, exp: (fixed[name]?.[product]?.[0]) ?? (pe * e / totalCountryExport * 1.9), imp: (fixed[name]?.[product]?.[1]) ?? (pi * i / 1300 * 2.4),
    }))
  } else if (product && country) {
    const [e, i] = fixed[country]?.[product] || (() => {
      const [pe, pi] = productBase[product] || [10, 5]
      const share = (countryBase[country]?.[0] || 1) / totalCountryExport
      return [pe * share * 1.6, pi * share * 1.2]
    })()
    const months = periodMonths(from, to)
    return Array.from({ length: months }, (_, n) => {
      const m = monthIndex(from) + n
      const label = `${Math.floor(m / 12)}.${String((m % 12) + 1).padStart(2, '0')}`
      const w = 0.3 + 0.02 * (n % 6)
      return { key: label, name: label, exp: round1(e * w), imp: round1(i * w), rank: n + 1 }
    })
  } else {
    rows = Object.entries(productBase).slice(0, 8).map(([id, [e, i]]) => ({ key: id, hs: id, name: rankName[id] || productName(id), exp: e, imp: i }))
  }
  return rows
    .map(r => ({ ...r, exp: round1(r.exp * scale), imp: round1(r.imp * scale) }))
    .sort((a, b) => b[basis] - a[basis])
    .map((r, n) => ({ ...r, rank: n + 1 }))
}

export const rankingCountries = countries.filter(c => countryBase[c.name]).map(c => c.name)

/** 리포트·AI 답변용 월별 수출액(억 USD). 중국·메모리는 목업 수치를 그대로 쓴다. */
export function monthlySeries(country, product) {
  if ((country === '중국' || !country) && product === '854232') return { current: [48.0, 52.0, 57.2], previous: [44.0, 48.0, 50.9] }
  const base = (fixed[country]?.[product]?.[0]) ?? ((productBase[product]?.[0] || 20) * ((countryBase[country]?.[0] || 120) / totalCountryExport) * 1.6)
  const cur = [0.31, 0.33, 0.36].map(w => round1(base * w))
  const prev = cur.map((v, n) => round1(v * (0.86 + n * 0.02)))
  return { current: cur, previous: prev }
}
