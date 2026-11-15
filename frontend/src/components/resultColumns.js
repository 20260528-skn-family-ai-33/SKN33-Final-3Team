// Display only: keep snapshot keys and values unchanged for charts and downloads.
const labels={country_name:'국가',country_code:'국가 코드',hs_code:'HS 코드',name:'품목',month:'기준월',trade_month:'기준월',amount_usd:'수출액',export_amount_usd:'수출액',import_amount_usd:'수입액',compare_amount_usd:'비교 기간 금액',compare_month:'비교 기준월',total_usd:'기간 합계',compare_total_usd:'비교 기간 합계',change_pct:'증감률',monthly_avg_usd:'월평균 금액',compare_monthly_avg_usd:'비교 기간 월평균',verified_months:'집계된 개월 수',requested_months:'조회 개월 수',compare_verified_months:'비교 집계 개월 수',compare_requested_months:'비교 조회 개월 수',verified_subtotal_usd:'집계된 금액',compare_verified_subtotal_usd:'비교 집계 금액'}
export function resultColumns(columns,rows,{ labelsOnly=false, meta }={}) {
  if (meta?.kind === 'LLM_SQL') return columns
  if (meta?.kind === 'PREDICTION') {
    const byKey = new Map(columns.map(column => [column.key, column]))
    const ranks = {
      COUNTRY: [['country_rank', '품목 순위']],
      HS6: [['product_rank', '국가 순위']],
      PAIR: [['country_rank', '국가 내 품목 순위'], ['product_rank', '품목 내 국가 순위']],
    }
    const fields = [
      ...(ranks[meta.group_by] || ranks.PAIR),
      byKey.has('country_name') ? ['country_name', '국가'] : ['country_code', '국가 코드'],
      ['hs6', 'HS 코드'],
      [byKey.has('product_name') || meta.group_by === 'HS6' ? 'product_name' : 'name', '품목명'],
      ['growth_outlook', '증감 예상'],   // 모델 B 방향. 모델 A 예측 증감률(growth_pct)은 순위용이라 보이지 않는다
      ['exp_12m', '최근 12개월 수출액', 'USD'], ['anchor', '예측 기준월'],
    ]
    return fields.filter(([key]) => byKey.has(key)).map(([key, label, unit]) => ({
      ...byKey.get(key), label, unit: unit ?? byKey.get(key).unit,
    }))
  }
  const titles={...labels,name:rows.some(r=>r.hs_code)?'품목':rows.some(r=>r.country_code)?'국가':'명칭'}
  // 추이 표는 서버가 전체 행으로 고른 열을 사용한다. 길이가 다른 단일 비교에도 합계·월평균이 필요하다.
  if(labelsOnly||meta?.kind==='TREND'||meta?.extra_compare_periods?.length) return columns.map(c=>({...c,label:(!c.label||c.label===c.key)?titles[c.key]||c.key:c.label}))
  const hidden=new Set(['RANKING','COUNTRY_COMPARISON'].includes(meta?.kind)?[]:['hs_ver'])
  if(!rows.some(r=>r.hs_item)) hidden.add('hs_item')
  if(rows.some(r=>r.country_name)) hidden.add('country_code')
  if(rows.some(r=>'hs_code' in r)&&!rows.some(r=>r.hs_code)){hidden.add('hs_code');hidden.add('name')}
  return columns.filter(c=>!hidden.has(c.key)).map(c=>({...c,label:(!c.label||c.label===c.key)?titles[c.key]||c.key:c.label}))
}
