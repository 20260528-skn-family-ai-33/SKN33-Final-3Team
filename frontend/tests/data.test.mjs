import { test } from 'node:test'
import assert from 'node:assert/strict'
import { amount, percent, reportEok, mapReport, mapArticle, mapNotification, mapPopupGroup, notificationTarget, popupItemTag, reportComparison } from '../src/api/adapters.js'
import { registerCountries, countryCode, countryName, registerProducts, productName } from '../src/data/catalog.js'
import { isStrongPassword } from '../src/utils/validate.js'
test('미수집 값과 실제 0을 구분하고 USD를 억 USD로 표시한다', () => {
  assert.equal(amount(null), '—'); assert.equal(amount(0), '0'); assert.equal(amount(15720000000), '157.2')
  assert.equal(amount(2345678), '0.02'); assert.equal(amount(51234), '0.0005'); assert.equal(amount(1230), '0.00001')
  assert.equal(percent(null), '—'); assert.equal(percent(0), '0.0%'); assert.equal(percent(-5.3), '-5.3%')
})

test('리포트 억 USD는 소수 2자리, 0.1억 미만은 유효숫자 1자리로 PDF와 같게 쓴다', () => {
  assert.equal(reportEok(null), '—'); assert.equal(reportEok(0), '0.00'); assert.equal(reportEok(1234.5), '1,234.50')
  assert.equal(reportEok(0.12), '0.12'); assert.equal(reportEok(0.0345), '0.03'); assert.equal(reportEok(0.000512), '0.0005'); assert.equal(reportEok(-0.004), '-0.004')
})

test('리포트 비교 방식과 저장된 비교값을 유지하고 예전 문서도 읽는다', () => {
  assert.equal(reportComparison({ compare: 'MOM' }, { mom_pct: 25, yoy_pct: 80 }).growth, 25)
  assert.equal(reportComparison({ compare: 'YOY_PERIOD' }, { yoy_pct: 80 }).growth, 80)
  assert.equal(reportComparison({ compare: 'CUSTOM' }, { comparison_pct: -20 }).growth, -20)
  assert.equal(reportComparison({ compare: 'CUSTOM' }, { yoy_pct: 80 }).growth, null)
  assert.equal(reportComparison({ compare: 'NONE' }, { comparison_pct: 99 }).growth, null)
  assert.equal(reportComparison({ compare: 'MOM' }, { comparison_pct: null, mom_pct: 25 }).growth, null)
  assert.equal(reportComparison({ compare: 'MOM' }, { comparison_pct: 0, previous_amount_usd: 0 }).growth, 0)
  assert.equal(reportComparison({ compare: 'MOM' }, { previous_amount_usd: 0 }).previous, 0)
})
test('서버 국가 코드와 화면 국가명은 왕복 변환된다', () => {
  registerCountries([{ country_code: 'US', name_ko: '미국' }, { country_code: 'CN', name_ko: '중국' }])
  assert.equal(countryCode('미국'), 'US'); assert.equal(countryName('US'), '미국')
  assert.equal(countryName('XX'), 'XX')
  registerProducts([{ hs_code: '330499', name_ko: '기초화장품' }])
  assert.equal(productName('330499'), '기초화장품')
})
test('큰 ID를 숫자로 바꾸지 않고 서버 리포트 상태를 유지한다', () => {
  const id = '9007199254740997'
  for (const [status, expected] of [['READY','ready'], ['FAILED','failed'], ['RUNNING','generating'], ['DELAYED','delayed']]) {
    const r = mapReport({ report_id: id, report_type: 'RPT_MONTHLY', status })
    assert.equal(r.id, id); assert.equal(r.kind, 'monthly'); assert.equal(r.status, expected)
  }
})
test('기사·알림의 실제 연결 대상과 읽음 상태를 보존한다', () => {
  const a = mapArticle({ article_id: '123', country_codes: ['US'], source_code: 'KOTRA_SHORT_NEWS', hs_items: [{ hs_code: '330499' }] })
  assert.equal(a.country, '미국'); assert.equal(a.kind, 'short'); assert.deepEqual(a.tags, ['330499'])
  const n = mapNotification({ user_alert_id: '45', event_type: 'REPORT', target_type: 'REPORT', target_id: '9007199254740997', read_at: null })
  assert.equal(n.read, false); assert.equal(n.reportId, '9007199254740997')
  const article = mapNotification({ user_alert_id: '46', event_type: 'MAJOR_ISSUE', target_type: 'ARTICLE', target_id: '7', issue: { articles: [{ article_id: '8' }] } })
  assert.equal(article.type, 'major'); assert.equal(article.articleId, '7')
  const aggregate = mapNotification({ user_alert_id: '47', event_type: 'MAJOR_ISSUE', target_type: 'ANALYSIS', target_id: '3', issue: { articles: [] } })
  assert.equal(aggregate.articleId, null)
  const group = mapPopupGroup({ delivery_id: 'd1', event_type: 'MAJOR_ISSUE', notifications: [{ user_alert_id: '46', event_type: 'MAJOR_ISSUE', title: '주요 이슈: 관세', read_at: null }] })
  assert.equal(group.deliveryId, 'd1'); assert.equal(group.type, 'major'); assert.equal(group.items[0].read, false)
  assert.deepEqual(popupItemTag({ event_type: 'REPORT', title: '분기 리포트가 발행되었습니다.' }), { label: '분기', tone: 'ok' })
  assert.deepEqual(popupItemTag({ event_type: 'MAJOR_ISSUE', issue: { reason: 'WEEKLY_NEWS_SPIKE' } }), { label: '기사 급증', tone: 'amber' })
  assert.equal(popupItemTag({ event_type: 'REPORT', title: '리포트가 발행되었습니다.' }), null)
  assert.equal(notificationTarget({ id: '1', reportId: '9' }), '/report?id=9')
  assert.equal(notificationTarget({ id: '1', articleId: '7', reportId: null }), '/articles?id=7')
  assert.equal(notificationTarget({ id: '1', articleId: null, reportId: null }), '/account/notifications?id=1')
})
test('기사 출처는 기사 유형과 독립적으로 실제 제공자를 표시한다', () => {
  for (const [source_code, expected] of [
    ['KOTRA_MARKET', 'KOTRA 해외시장뉴스'], ['KOTRA_SHORT_NEWS', 'KOTRA 단신속보'],
    ['HANKYUNG', '한국경제'], ['FNNEWS', '파이낸셜뉴스'],
  ]) {
    for (const is_major_issue of [false, true]) {
      const a = mapArticle({ article_id: '123', source_code, is_major_issue, source_url: 'https://example.com/article' })
      assert.equal(a.source, expected)
      assert.equal(a.kind, is_major_issue ? 'major' : source_code === 'KOTRA_SHORT_NEWS' ? 'short' : 'general')
      assert.equal(a.url, 'https://example.com/article')
    }
  }
})

test('미등록 또는 누락된 기사 출처를 다른 언론사로 대체하지 않는다', () => {
  assert.equal(mapArticle({ source_code: 'NEW_PROVIDER' }).source, 'NEW_PROVIDER')
  for (const source_code of [null, undefined, '']) {
    assert.equal(mapArticle({ source_code }).source, '출처 미제공')
  }
})

test('회원가입 비밀번호 규칙', () => {
  assert.equal(isStrongPassword('Original!123'), true)
  for (const bad of ['12345678', 'abcdefgh', 'abcd1234', 'a1!', 'abcd 123!']) assert.equal(isStrongPassword(bad), false)
})


test('trade chart uses a common Korean USD unit without changing amounts', async()=>{
 const {chartDisplay}=await import('../src/utils/chart.js')
 const display=chartDisplay([100000000,234567890], 'USD')
 assert.equal(display.unit,'억USD');assert.equal(display.format(234567890),'2.35')
})
test('same-metric country comparisons share a chart; ML and different units stay separate', async()=>{
 const {comparisonCharts}=await import('../src/pages/analysis/comparisonCharts.js')
 const period={start:'2026-01-01',end:'2026-02-01'}
 const tasks=['US','CN'].map((code,i)=>({task_id:'t'+i,intent:'STATISTICS',conditions:{country_codes:[code],hs_items:[],period,trade_flow:'EXPORT'}}))
 const charts=tasks.map((task,i)=>({chart_id:task.task_id,task_id:task.task_id,type:'line',title:'수출액',source:'관세청',unit:'USD',labels:i?['2026-02']:['2026-01'],series:[{name:'수출액',values:[i+1]}]}))
 const combined=comparisonCharts(charts,tasks)
 assert.equal(combined.length,1);assert.deepEqual(combined[0].series.map(s=>s.values),[[1,null],[null,2]])
 assert.equal(charts[0].series.length,1)
 assert.equal(comparisonCharts([charts[0],{...charts[1],unit:'%'}],tasks).length,2)
 assert.equal(comparisonCharts(charts,tasks.map(t=>({...t,intent:'PROMISING_MARKET'}))).length,2)
})


test('comparison table produces one aligned country chart and preserves zero/null',async()=>{
 const {comparisonChart,comparisonGroups}=await import('../src/pages/analysis/comparisonGroups.js')
 const conditions={period:{start:'2026-01-01',end:'2026-02-01'},trade_flow:'EXPORT',country_codes:['CN','JP']}
 const group=comparisonGroups({tasks:[{task_id:'t1',intent:'STATISTICS',conditions}],tables:[{task_id:'t1',result_id:'r',meta:{kind:'TREND'},rows:[{country_code:'CN'}]}]})
 assert.equal(group.length,1)
 const chart=comparisonChart({columns:[{key:'cn',type:'number',label:'중국 수출액'},{key:'jp',type:'number',label:'일본 수출액'}],rows:[{hs_code:'854232',name:'메모리',month:'2026-01',cn:10,jp:0},{hs_code:'854232',name:'메모리',month:'2026-02',cn:20,jp:null}]},conditions)
 assert.equal(chart.series.length,2)
 assert.deepEqual(chart.series.find(s=>s.name==='일본 수출액').values,[0,null])
 assert.deepEqual(chart.labels,['2026-01','2026-02'])
})


test('cause and text answers comparing two countries also use the per-period comparison table',async()=>{
 const {comparisonGroups}=await import('../src/pages/analysis/comparisonGroups.js')
 const conditions={period:{start:'2026-01-01',end:'2026-02-01'},trade_flow:'EXPORT',country_codes:['CN','US']}
 for(const task of [{intent:'CAUSE'},{intent:'STATISTICS',answer_format:'TEXT'}]){
  const groups=comparisonGroups({tasks:[{task_id:'t1',...task,conditions}],tables:[{task_id:'t1',result_id:'r',meta:{kind:'TREND'},rows:[{country_code:'CN'},{country_code:'US'}]}]})
  assert.equal(groups.length,1)
 }
 assert.equal(comparisonGroups({tasks:[{task_id:'t1',intent:'RANKING',conditions}],tables:[{task_id:'t1',result_id:'r',meta:{kind:'TREND'},rows:[{country_code:'CN'},{country_code:'US'}]}]}).length,0)
})


test('duplicate current-period results with different previous-period settings form one comparison',async()=>{
 const {comparisonGroups}=await import('../src/pages/analysis/comparisonGroups.js')
 const period={start:'2026-01-01',end:'2026-02-28'}
 const tasks=[['CN'],['JP'],['CN','JP']].map((codes,i)=>({task_id:'t'+i,intent:'STATISTICS',conditions:{period:i===2?{end:period.end,start:period.start}:period,trade_flow:'EXPORT',metric:'AMOUNT',country_codes:codes,hs_items:[],compare_period:i===2?{start:'2025-01-01',end:'2025-02-28'}:null}}))
 const tables=tasks.map((t,i)=>({task_id:t.task_id,result_id:'r'+i,meta:{kind:'TREND'},rows:[{country_code:t.conditions.country_codes[0]}]}))
 const groups=comparisonGroups({tasks,tables})
 assert.equal(groups.length,1);assert.equal(groups[0].tables.length,3)
})


test('results over the comparison API limit stay separate instead of failing as one group',async()=>{
 const {comparisonGroups}=await import('../src/pages/analysis/comparisonGroups.js')
 const period={start:'2026-01-01',end:'2026-02-28'}
 const codes=n=>Array.from({length:n},(_,i)=>'C'+String(i).padStart(2,'0'))
 const tasks=[codes(11),codes(21).slice(11)].map((c,i)=>({task_id:'t'+i,intent:'STATISTICS',conditions:{period,trade_flow:'EXPORT',country_codes:c,hs_items:[]}}))
 const tables=tasks.map((t,i)=>({task_id:t.task_id,result_id:'r'+i,meta:{kind:'TREND'},rows:t.conditions.country_codes.map(country_code=>({country_code}))}))
 assert.equal(comparisonGroups({tasks,tables}).length,0)
 assert.equal(comparisonGroups({tasks:tasks.slice(0,1),tables:tables.slice(0,1)}).length,1)
})


test('job completion received after the message read displays its saved answer once',async()=>{
 const {completedMessages}=await import('../src/pages/analysis/completedMessages.js')
 const rows=[{message_id:'1',role:'USER',job:{job_id:'1',status:'RUNNING'}}]
 const answer={text:'저장된 최종 답변'}
 const job={job_id:'1',status:'SUCCEEDED',answer}
 const completed=completedMessages(rows,job)
 assert.equal(rows.length,1);assert.equal(completed.length,2);assert.equal(completed[1].answer,answer)
 assert.equal(completedMessages(completed,job),completed)
 assert.equal(completedMessages(rows,{...job,status:'RUNNING'}),rows)
 const other=[...rows,{role:'USER',message_id:'2',job:{job_id:'2'}}]
 assert.equal(completedMessages(other,job)[1].role,'ASSISTANT')
 assert.equal(completedMessages(other,job)[2].message_id,'2')
})
