// 리포트의 유망시장(증가·감소 예상) 섹션 표시 모델
const RANK_LABELS = { COUNTRY: '국가 안 순위', HS6: '품목 안 순위', PAIR: '국가 안 순위' }
/** 유망시장(증가·감소 예상) 표. PDF(report_ondemand_context.promising_view)와 같은 순위 기준을 쓴다. */
export function promisingView(section) {
  const table = section?.tables?.[0]
  const rows = table?.rows || []
  let group = table?.meta?.group_by
  if (!RANK_LABELS[group]) {   // 채팅 결과를 복사한 표에는 meta가 없다. 행 모양으로 정한다
    const countries = new Set(rows.map(r => r.country_code)), products = new Set(rows.map(r => r.hs6))
    group = countries.size === 1 && products.size > 1 ? 'COUNTRY' : products.size === 1 && countries.size > 1 ? 'HS6' : 'PAIR'
  }
  const [rank, size] = group === 'HS6' ? ['product_rank', 'product_size'] : ['country_rank', 'country_size']
  return { rankLabel: RANK_LABELS[group], anchor: rows[0]?.anchor?.slice(0, 7) || null, rows: rows.map(r => ({ ...r, rank: r[rank], size: r[size] })) }
}
