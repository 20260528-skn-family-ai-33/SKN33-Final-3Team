// Display only: keep snapshot keys and values unchanged for charts and downloads.
const labels={country_name:'국가',country_code:'국가 코드',hs_code:'HS 코드',name:'품목',month:'기준월',trade_month:'기준월',amount_usd:'수출액',export_amount_usd:'수출액',import_amount_usd:'수입액',compare_amount_usd:'비교 기간 금액',total_usd:'기간 합계',compare_total_usd:'비교 기간 합계',change_pct:'증감률',monthly_avg_usd:'월평균 금액',compare_monthly_avg_usd:'비교 기간 월평균',verified_months:'집계된 개월 수',requested_months:'조회 개월 수',compare_verified_months:'비교 집계 개월 수',compare_requested_months:'비교 조회 개월 수',verified_subtotal_usd:'집계된 금액',compare_verified_subtotal_usd:'비교 집계 금액'}
export function resultColumns(columns,rows) {
  const titles={...labels,name:rows.some(r=>r.hs_code)?'품목':rows.some(r=>r.country_code)?'국가':'명칭'}
  const hidden=new Set(['hs_ver'])
  if(!rows.some(r=>r.hs_item)) hidden.add('hs_item')
  if(rows.some(r=>r.country_name)) hidden.add('country_code')
  if(rows.some(r=>'hs_code' in r)&&!rows.some(r=>r.hs_code)){hidden.add('hs_code');hidden.add('name')}
  if(rows.some(r=>'month' in r&&'verified_months' in r)){
    for(const key of ['total_usd','compare_total_usd','change_pct','monthly_avg_usd','compare_monthly_avg_usd','verified_months','requested_months','compare_verified_months','compare_requested_months','verified_subtotal_usd','compare_verified_subtotal_usd']) hidden.add(key)
    if(!rows.some(r=>r.compare_amount_usd!=null)) hidden.add('compare_amount_usd')
  }
  return columns.filter(c=>!hidden.has(c.key)).map(c=>({...c,label:(!c.label||c.label===c.key)?titles[c.key]||c.key:c.label}))
}
