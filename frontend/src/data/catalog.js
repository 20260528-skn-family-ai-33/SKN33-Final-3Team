// 품목(HS)·국가·환율 기준 데이터. 화면 시연용 예시이며 실제 API(관세청·ECOS) 연동 시 교체한다.

/** HS4 → 하위 HS6. 관심사 등록·관심정보에서 "HS4 확정 → 하위 품목 선택"에 쓴다. */
export const hs4Groups = [
  { code: '8542', name: '전자집적회로', items: [['854231', '프로세서와 컨트롤러'], ['854232', '메모리'], ['854233', '증폭기'], ['854239', '기타 전자집적회로']] },
  { code: '3304', name: '미용·화장품', items: [['330410', '입술 화장품'], ['330420', '눈 화장품'], ['330491', '파우더'], ['330499', '기초화장품']] },
  { code: '8703', name: '승용자동차', items: [['870322', '승용차(1,000~1,500cc)'], ['870323', '승용차'], ['870380', '전기 승용차']] },
  { code: '2710', name: '석유제품', items: [['271012', '경질 석유제품'], ['271019', '석유제품']] },
  { code: '8507', name: '축전지', items: [['850760', '리튬이온 축전지'], ['850780', '기타 축전지']] },
]

export const products = hs4Groups.flatMap(g => g.items.map(([id, name]) => ({ id, name, hs4: g.code })))
export const productName = id => products.find(p => p.id === id)?.name || id

// 기사 제목에서 품목을 찾을 때 쓰는 대표어
const KEYWORDS = { 854232: '메모리', 854231: '반도체', 854239: '반도체', 854233: '반도체', 330499: '화장품', 330410: '화장품', 330420: '화장품', 330491: '화장품', 870323: '승용차', 870322: '승용차', 870380: '전기차', 271019: '석유', 271012: '석유', 850760: '배터리', 850780: '배터리' }
export const productKeyword = id => KEYWORDS[id] || productName(id)

export const continents = ['아시아', '유럽', '북아메리카', '남아메리카', '아프리카', '오세아니아']

export const countries = [
  { name: '중국', continent: '아시아', currency: 'CNY' },
  { name: '미국', continent: '북아메리카', currency: 'USD' },
  { name: '베트남', continent: '아시아', currency: 'VND' },
  { name: '일본', continent: '아시아', currency: 'JPY' },
  { name: '독일', continent: '유럽', currency: 'EUR' },
  { name: '프랑스', continent: '유럽', currency: 'EUR' },
  { name: '인도', continent: '아시아', currency: 'INR' },
  { name: '캐나다', continent: '북아메리카', currency: 'CAD' },
  { name: '인도네시아', continent: '아시아', currency: 'IDR' },
  { name: '태국', continent: '아시아', currency: 'THB' },
  { name: '라오스', continent: '아시아', currency: 'LAK' },
  { name: '영국', continent: '유럽', currency: 'GBP' },
  { name: '멕시코', continent: '북아메리카', currency: 'MXN' },
  { name: '브라질', continent: '남아메리카', currency: 'BRL' },
  { name: '칠레', continent: '남아메리카', currency: 'CLP' },
  { name: '남아프리카공화국', continent: '아프리카', currency: 'ZAR' },
  { name: '가나', continent: '아프리카', currency: 'GHS' },
  { name: '호주', continent: '오세아니아', currency: 'AUD' },
  { name: '뉴질랜드', continent: '오세아니아', currency: 'NZD' },
]

/** 오늘의 환율(ECOS 형식 예시): [통화, 단위, 매매기준율, 변동, 방향] */
export const exchangeRates = [
  ['USD', '1달러', '1,380.00', '3.17 +0.23%', 'up'],
  ['EUR', '1유로', '1,510.20', '2.12 −0.14%', 'down'],
  ['CNY', '1위안', '193.20', '0.40 +0.21%', 'up'],
  ['JPY', '100엔', '925.40', '1.76 −0.19%', 'down'],
  ['VND', '100동', '5.40', '0.01 +0.19%', 'up'],
  ['INR', '1루피', '16.40', '0.02 +0.12%', 'up'],
  ['CAD', '1캐나다달러', '1,010.20', '1.10 −0.11%', 'down'],
  ['IDR', '100루피아', '8.70', '0.01 +0.12%', 'up'],
  ['GBP', '1파운드', '1,842.10', '2.30 +0.12%', 'up'],
  ['AUD', '1호주달러', '910.40', '0.40 +0.04%', 'up'],
]

export function toggleValue(list, value) {
  return list.includes(value) ? list.filter(v => v !== value) : [...list, value]
}
