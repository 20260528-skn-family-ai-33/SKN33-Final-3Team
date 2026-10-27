// 메인 화면 데이터 조립. 기본 관심사(중국·미국·베트남 / 메모리·기초화장품)는 목업 수치를 그대로 쓰고,
// 다른 관심사로 바꾸면 같은 모양의 예시 수치를 계산해 보여 준다.
import { dashboard } from '../../data/dashboard'
import { countries as allCountries, exchangeRates, productName } from '../../data/catalog'
import { tradeRanking } from '../../data/trade'
import { articles } from '../../data/articles'
import news1 from '../../assets/news-1.jpg'
import news2 from '../../assets/news-2.jpg'
import news3 from '../../assets/news-3.jpg'
import news4 from '../../assets/news-4.jpg'

const IMAGES = [news1, news2, news3, news4]
const PIE_COLORS = ['#C8102E', '#2B2350', '#E2667A', '#6E6A9E', '#F1A9B4', '#CDD1D8']
const KIND_LABEL = { general: '전체기사', short: '단신속보', major: '주요이슈' }
const same = (a, b) => a.length === b.length && a.every(x => b.includes(x))
const f1 = v => v.toFixed(1)
const hash = s => [...s].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 997, 7)

export function articleCard(a, i) {
  return { id: a.id, kind: KIND_LABEL[a.kind], date: a.date, title: a.title, summary: '[기사 요약 한두 줄 — 실제 기사 본문 첫 문단에서 가져옵니다]', img: IMAGES[i % 4] }
}

/** 기간 칩(3·6·12개월)에 맞춰 순위 카드 수치를 늘린다. */
export function scaleCards(cards, months) {
  const k = { 3: 1, 6: 2.02, 12: 4.1 }[months] || 1
  return cards.map(c => ({ ...c, exp: f1(parseFloat(c.exp) * k), imp: f1(parseFloat(c.imp) * k) }))
}

export function buildView(view, profile) {
  if (view === 'all') return { ...dashboard.all, newsCards: dashboard.all.news }
  const base = dashboard.interest
  const { products, countries } = profile
  if (same(products, ['854232', '330499']) && same(countries, ['중국', '미국', '베트남'])) {
    return { ...base, newsCards: base.news }
  }
  // 관심사가 바뀐 경우: 같은 모양의 예시 수치를 계산한다.
  const cs = countries.length ? countries : ['중국', '미국', '베트남']
  const ps = products.length ? products : ['854232']
  const productRows = ps.map(id => {
    const rows = tradeRanking({ product: id }).filter(r => cs.includes(r.name))
    const exp = rows.reduce((s, r) => s + r.exp, 0) || 3 + (hash(id) % 40)
    const imp = rows.reduce((s, r) => s + r.imp, 0) || exp * 0.24
    return { id, exp, imp }
  }).sort((a, b) => b.exp - a.exp)
  const countryRows = cs.map(name => {
    const exp = ps.reduce((s, id) => s + (tradeRanking({ product: id }).find(r => r.name === name)?.exp || 1 + (hash(name + id) % 9)), 0)
    return { name, exp, imp: exp * 0.27 }
  }).sort((a, b) => b.exp - a.exp)
  const totalExp = productRows.reduce((s, r) => s + r.exp, 0)
  const totalImp = productRows.reduce((s, r) => s + r.imp, 0)
  const monthExp = totalExp / 3, monthImp = totalImp / 3
  const sparks = dashboard.all.rankCards.map(c => c.spark)
  const pieTop = countryRows.slice(0, 5)
  const pieSum = countryRows.reduce((s, r) => s + r.exp, 0) || 1
  const pie = pieTop.map((r, i) => [`${r.name} ${Math.round((r.exp / pieSum) * 100)}%`, PIE_COLORS[i]])
  const rest = 100 - pieTop.reduce((s, r) => s + Math.round((r.exp / pieSum) * 100), 0)
  if (rest > 0 && countryRows.length > 5) pie.push([`기타 ${rest}%`, PIE_COLORS[5]])
  const currencies = [...new Set(cs.map(n => allCountries.find(c => c.name === n)?.currency).filter(Boolean))]
  const relevant = articles.filter(a => cs.some(c => a.title.includes(c) || a.country === c))
  return {
    ...base,
    period: `관심 품목 ${ps.length} · 관심 국가 ${cs.length} · 순위 2025.10–12 · 월별 추이 2024.01–2025.12`,
    kpis: [
      { label: '12월 관심 수출', value: f1(monthExp), unit: '억 USD', rows: [['전년 동월 대비', '+11.2%', 'up'], ['전월 대비', '+4.8%', 'up']] },
      { label: '12월 관심 수입', value: f1(monthImp), unit: '억 USD', rows: [['전년 동월 대비', '+7.9%', 'up'], ['전월 대비', '−0.8%', 'down']] },
      { label: '12월 관심 무역수지', value: (monthExp - monthImp >= 0 ? '+' : '') + f1(monthExp - monthImp), unit: '억 USD', rows: [['전년 동월 대비', `+${f1((monthExp - monthImp) * 0.18)} 억 USD`, 'up'], ['전월 대비', `+${f1((monthExp - monthImp) * 0.06)} 억 USD`, 'up']] },
    ],
    fx: exchangeRates.filter(r => currencies.includes(r[0])),
    rankCards: productRows.map((r, i) => ({ rank: `${i + 1}위`, hs: r.id, name: productName(r.id), exp: f1(r.exp), imp: f1(r.imp), spark: sparks[i % sparks.length], color: '#C8102E' })),
    productTable: { ...base.productTable, rows: productRows.slice(0, 5).map((r, i) => [String(i + 1), r.id, productName(r.id), f1(r.exp), f1(r.imp)]), note: `등록 품목 ${ps.length}개 표시 · 관심 품목 내 최대 5위` },
    countryTable: {
      ...base.countryTable, sub: `${ps.map(productName).join('·')} 합산 · 관심 국가 내 · 억 USD`,
      rows: countryRows.slice(0, 5).map((r, i) => [String(i + 1), r.name, f1(r.exp), f1(r.imp)]), pie,
      pieNote: `분모: ${ps.map(productName).join('·')} 전체 수출액`,
    },
    un: cs.slice(0, 3).map(name => ({
      country: name, meta: `보고국 ${name} · 관심 품목 ${ps.length}`,
      items: ps.slice(0, 2).map(id => {
        const h = hash(name + id)
        const fixedCard = base.un.find(u => u.country === name)?.items.find(it => it.hs === id)
        return fixedCard || { name: productName(id), hs: id, rank: `공급국 ${1 + (h % 4)}위`, share: `${(8 + (h % 300) / 10).toFixed(1)}%`, change: `${((h % 30) / 10).toFixed(1)}%p`, dir: h % 2 ? 'up' : 'down', importShare: `${((h % 90) / 10).toFixed(1)}%` }
      }),
    })),
    newsCards: (relevant.length >= 4 ? relevant : articles).slice(0, 4).map(articleCard),
  }
}
