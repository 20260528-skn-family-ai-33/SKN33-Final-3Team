import test from 'node:test'
import assert from 'node:assert/strict'
import { previewExpired, rankLink, rankInitial, rankBasis, countryListLabel, comparisonPeriod, mapLimited, reportComparisonInputs, reportPeriodInputs, reportConditions } from '../src/api/integration.js'

test('preview renewal only handles expired snapshots', () => {
  assert.equal(previewExpired({status:409,code:'PREVIEW_EXPIRED'}), true)
  assert.equal(previewExpired({status:410,code:'RESULT_EXPIRED'}), true)
  assert.equal(previewExpired({status:409,code:'IDEMPOTENCY_CONFLICT'}), false)
})

test('dashboard detail round trip preserves period, countries, scope and group', () => {
  const link = rankLink({scope:'INTERESTED',start:'2025-01',end:'2025-12',months:12,countries:['US','CN'],group:'COUNTRY',product:'330499'})
  const initial = rankInitial(new URLSearchParams(link.split('?')[1]))
  assert.deepEqual(initial, {country:'',product:'330499',period:'12',scope:'INTERESTED',countryCodes:['US','CN'],start:'2025-01',end:'2025-12',group:'COUNTRY',inherited:true})
  const global = rankInitial(new URLSearchParams(rankLink({scope:'GLOBAL',months:6,group:'HS6'}).split('?')[1]))
  assert.equal(global.country, '')
  assert.equal(global.period, '6')
  assert.equal(rankInitial(new URLSearchParams()).country, '중국')
})

test('dashboard import KPI opens the ranking on the import basis; export stays the default', () => {
  const query = link => new URLSearchParams(link.split('?')[1])
  assert.equal(rankBasis(query(rankLink({scope:'GLOBAL',months:3,group:'COUNTRY',flow:'IMPORT'}))), 'imp')
  assert.equal(rankBasis(query(rankLink({scope:'GLOBAL',months:3,group:'COUNTRY',flow:'EXPORT'}))), 'exp')
  assert.equal(rankBasis(query(rankLink({scope:'GLOBAL',months:3,group:'COUNTRY'}))), 'exp')
})

test('comparison label respects NONE, missing data, import and leap year', () => {
  const c = {compare:'YOY_PERIOD',period:{start:'2025-02-01',end:'2025-02-28'}}
  const rows = [{previous_year_export_usd:0}]
  assert.deepEqual(comparisonPeriod(c,rows), {start:'2024-02-01',end:'2024-02-29'})
  assert.equal(comparisonPeriod({...c,compare:'NONE'},rows), null)
  assert.equal(comparisonPeriod(c,[{previous_year_export_usd:null}]), null)
  assert.equal(comparisonPeriod({...c,trade_flow:'IMPORT'},rows), null)
  assert.deepEqual(comparisonPeriod({...c,trade_flow:'IMPORT'},[{previous_year_import_usd:1}]), {start:'2024-02-01',end:'2024-02-29'})
})

test('limited requests keep result ordering and cap outstanding work', async () => {
  let running=0, peak=0
  const result=await mapLimited([25,5,15,2,8],2,async (delay,index) => {
    running++; peak=Math.max(peak,running)
    await new Promise(resolve=>setTimeout(resolve,delay))
    running--; return index
  })
  assert.deepEqual(result,[0,1,2,3,4]); assert.equal(peak,2)
})

test('aborted or failed queries stop pending jobs', async () => {
  const controller=new AbortController(); let calls=0
  await assert.rejects(mapLimited([1,2,3],1,async () => { calls++; controller.abort() },controller.signal),{name:'AbortError'})
  assert.equal(calls,1)
  await assert.rejects(mapLimited([1,2],1,async () => { throw new Error('unavailable') }),/unavailable/)
  assert.deepEqual(await mapLimited([],4,()=>{}),[])
})

test('a failed parallel request stops the other lanes from starting queued work', async () => {
  const called=[]
  let finishSecond
  const second = new Promise(resolve => { finishSecond=resolve })
  const result = mapLimited([0,1,2,3],2,async value => {
    called.push(value)
    if (value===0) { await Promise.resolve(); throw new Error('query failed') }
    await second
  })
  await assert.rejects(result,/query failed/)
  finishSecond()
  await new Promise(resolve => setImmediate(resolve))
  assert.deepEqual(called,[0,1])
})

test('report from a 24-month YOY answer keeps automatic comparison rather than CUSTOM', () => {
  const conditions={compare:'YOY_PERIOD',period:{start:'2024-01-01',end:'2025-12-31'},compare_period:{start:'2023-01-01',end:'2024-12-31'}}
  const fields=reportComparisonInputs(conditions)
  assert.deepEqual(fields,{compareFrom:'',compareTo:''})
  const request=reportConditions({conditions,from:'2024-01',to:'2025-12',...fields})
  assert.equal(request.compare,'YOY_PERIOD')
  assert.equal(request.compare_period,undefined)
  assert.deepEqual(request.period,conditions.period)
  assert.equal(reportConditions({conditions,from:'2025-02',to:'2025-02',...fields}).compare,'YOY_PERIOD')
})

test('report custom input is explicit and NONE is preserved', () => {
  const conditions={compare:'CUSTOM',compare_period:{start:'2024-02-01',end:'2024-02-29'}}
  const fields=reportComparisonInputs(conditions)
  const request=reportConditions({conditions,from:'2025-02',to:'2025-02',...fields})
  assert.equal(request.compare,'CUSTOM')
  assert.deepEqual(request.compare_period,conditions.compare_period)
  assert.equal(reportConditions({conditions,from:'2025-02',to:'2025-02',compareFrom:'',compareTo:''}).compare,'YOY_PERIOD')
  const none=reportConditions({conditions:{compare:'NONE'},from:'2025-02',to:'2025-02',...reportComparisonInputs({compare:'NONE'})})
  assert.equal(none.compare,'NONE')
  assert.equal(none.compare_period,undefined)
})

test('국가가 많으면 순위 화면 제목·선택칸에서 첫 국가 외 N개국으로 줄인다', () => {
  assert.equal(countryListLabel([]), '')
  assert.equal(countryListLabel(['중국']), '중국')
  assert.equal(countryListLabel(['중국', '일본', '베트남']), '중국 · 일본 · 베트남')
  assert.equal(countryListLabel(['아랍에미리트 연합', '아프가니스탄', '아르메니아', '아제르바이잔']), '아랍에미리트 연합 외 3개국')
})

test('C-05: report defaults put the later period in analysis and the earlier one in comparison', () => {
  // 채팅 "2024년과 2025년 비교" → 분석 2024 / 비교 2025. 리포트 기본값은 분석 2025 / 비교 2024여야 저장된다.
  const later={compare:'CUSTOM',period:{start:'2024-01-01',end:'2024-12-31'},compare_period:{start:'2025-01-01',end:'2025-12-31'}}
  const fields=reportPeriodInputs(later)
  assert.deepEqual(fields,{from:'2025-01',to:'2025-12',compareFrom:'2024-01',compareTo:'2024-12'})
  const request=reportConditions({conditions:later,...fields})
  assert.equal(request.compare,'CUSTOM')
  assert.ok(request.compare_period.end<request.period.start)
  // 같은 해 안에서 비교 구간이 뒤인 경우도 바꾼다.
  const sameYear={compare:'CUSTOM',period:{start:'2025-01-01',end:'2025-03-31'},compare_period:{start:'2025-07-01',end:'2025-09-30'}}
  assert.deepEqual(reportPeriodInputs(sameYear),{from:'2025-07',to:'2025-09',compareFrom:'2025-01',compareTo:'2025-03'})
})

test('C-05: valid, overlapping and automatic comparisons keep a savable default', () => {
  const valid={compare:'CUSTOM',period:{start:'2025-01-01',end:'2025-12-31'},compare_period:{start:'2024-01-01',end:'2024-12-31'}}
  assert.deepEqual(reportPeriodInputs(valid),{from:'2025-01',to:'2025-12',compareFrom:'2024-01',compareTo:'2024-12'})
  // 겹치면 사용자 지정 비교를 비워 전년 같은 기간 비교를 쓴다.
  const overlap={compare:'CUSTOM',period:{start:'2025-01-01',end:'2025-06-30'},compare_period:{start:'2025-04-01',end:'2025-09-30'}}
  const fields=reportPeriodInputs(overlap)
  assert.deepEqual(fields,{from:'2025-01',to:'2025-06',compareFrom:'',compareTo:''})
  assert.equal(reportConditions({conditions:overlap,...fields}).compare,'YOY_PERIOD')
  // 한 달만 있는 경우(같은 달 비교)도 겹침으로 본다.
  const single={compare:'CUSTOM',period:{start:'2025-03-01',end:'2025-03-31'},compare_period:{start:'2025-03-01',end:'2025-03-31'}}
  assert.deepEqual(reportPeriodInputs(single),{from:'2025-03',to:'2025-03',compareFrom:'',compareTo:''})
  assert.deepEqual(reportPeriodInputs({compare:'NONE',period:{start:'2025-01-01',end:'2025-12-31'},compare_period:null}),{from:'2025-01',to:'2025-12',compareFrom:'',compareTo:''})
  assert.deepEqual(reportPeriodInputs({compare:'YOY_PERIOD',period:{start:'2025-01-01',end:'2025-12-31'},compare_period:{start:'2024-01-01',end:'2024-12-31'}}),{from:'2025-01',to:'2025-12',compareFrom:'',compareTo:''})
})
