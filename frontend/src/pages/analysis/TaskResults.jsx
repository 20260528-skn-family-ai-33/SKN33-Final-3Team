import { useState } from 'react'
import { api, download } from '../../api/client'
import { useQuery, ErrorNotice } from '../../api/hooks'
import { BarChart, Donut, LineChart } from '../../components/charts'
import ApiResults from '../../components/ApiResults'
import { niceTicks, chartDisplay } from '../../utils/chart'
import { countryName, productName } from '../../data/catalog'
import { useApp } from '../../store'
import { comparisonCharts } from './comparisonCharts'
import { comparisonGroups, comparisonChart } from './comparisonGroups'

const colors=['#a71b39','#342954','#4b7578','#967348','#6e6198']
const badges={PARTIAL:'일부 결과',NO_DATA:'결과 없음',OUT_OF_SCOPE:'범위 밖'}

function Chart({initial,selector,table}) {
  const [selected,setSelected]=useState(null)
  const chart=selected && selected.resultId===initial.result_id ? selected.chart : initial
  const [busy,setBusy]=useState(false)
  const [country,setCountry]=useState(selector?.country_codes[0]||'')
  // 품목은 코드와 판본으로 고른다. 같은 숫자가 두 판본에 있으면(개정 전후 다른 품목) 코드만으로는 구분되지 않는다.
  const itemKey=h=>h?`${h.hs_code}:${h.hs_ver}`:''
  const [hs,setHs]=useState(itemKey(selector?.hs_items[0]))
  const {run}=useApp()
  const pairMode=selector?.group_by==='PAIR'
  // PAIR 결과는 최대 100쌍이다. 답변에 첫 페이지만 있으면 저장된 전체 조합을 읽는다.
  const needsPairs=pairMode && !table?.expired && (!table || table.total_rows>table.rows.length)
  const pairQuery=useQuery(`/results/${initial.result_id}`,{page_size:100},needsPairs)
  const pairs=pairMode ? (pairQuery.data?.table?.rows || table?.rows || []) : []  // API-41: {table:{rows}}
  const pairKey=(c,h)=>`${c}:${h}`
  const change=async(c,h)=>{
    setBusy(true)
    const product=selector.hs_items.find(p=>itemKey(p)===h)
    const result=await run(()=>api(`/results/${initial.result_id}/chart`,{query:{country_code:c||undefined,hs_code:product?.hs_code,hs_ver:product?.hs_ver}}))
    if(result){setSelected({resultId:initial.result_id,chart:result.chart});setCountry(c);setHs(h)}
    setBusy(false)
  }
  const series=chart.series.map((s,i)=>({...s,color:colors[i%colors.length]}))
  const display=chartDisplay(series.flatMap(s=>s.values),chart.unit)
  const props={labels:chart.labels,fullLabels:chart.full_labels,series,label:chart.title,formatValue:display.format}
  return <figure style={{margin:'14px 0'}}><figcaption><b>{chart.title}</b> · {display.unit} {({QUARTER:'· 분기별',YEAR:'· 연도별'})[chart.granularity]}</figcaption>
    {pairMode ? <div className="chips">
      {pairs.length>0 && <select aria-label="차트 국가·품목" disabled={busy} value={pairKey(country,hs)} onChange={e=>{
        const pair=pairs.find(p=>pairKey(p.country_code,itemKey({hs_code:p.hs6,hs_ver:p.hs_ver||'HS2022'}))===e.target.value)
        if(pair) change(pair.country_code,itemKey({hs_code:pair.hs6,hs_ver:pair.hs_ver||'HS2022'}))
      }}>{pairs.map(p=>{const key=pairKey(p.country_code,itemKey({hs_code:p.hs6,hs_ver:p.hs_ver||'HS2022'}))
        return <option key={key} value={key}>{countryName(p.country_code)} · {productName(p.hs6)}</option>})}</select>}
      {pairQuery.loading && <span role="status">차트 대상 불러오는 중…</span>}
      <ErrorNotice error={pairQuery.error} retry={pairQuery.reload}/>
    </div> : selector && <div className="chips">{selector.country_codes.length>0 && <select aria-label="차트 국가" disabled={busy} value={country} onChange={e=>change(e.target.value,hs)}>{selector.country_codes.map(c=><option key={c} value={c}>{countryName(c)}</option>)}</select>}{selector.hs_items.length>0 && <select aria-label="차트 품목" disabled={busy} value={hs} onChange={e=>change(country,e.target.value)}>{selector.hs_items.map(h=><option key={itemKey(h)} value={itemKey(h)}>{productName(h.hs_code)}{selector.hs_items.some(o=>o!==h&&o.hs_code===h.hs_code)?` (${h.hs_ver})`:''}</option>)}</select>}</div>}
    {chart.type==='line'?<LineChart {...props} yTicks={niceTicks(series.flatMap(s=>s.values.filter(v=>v!=null)))}/>:chart.type==='donut'?<><Donut items={chart.labels.map((l,i)=>[l,series[0].values[i]||0,colors[i%colors.length]])}/><p>{chart.labels.map((l,i)=>l+' '+(series[0].values[i]??'—')+'%').join(' · ')}</p></>:<BarChart {...props}/>}
    {series.length>1 && <ul aria-label="그래프 범례" style={{display:'flex',flexWrap:'wrap',gap:'8px 18px',listStyle:'none',padding:0,margin:'12px 0 0'}}>
      {series.map((s,i)=><li key={i} style={{display:'flex',alignItems:'center',gap:8,fontSize:13}}>
        <span aria-hidden="true" style={{display:'inline-block',flexShrink:0,width:24,height:4,borderRadius:2,background:s.color}}/>
        <span>{s.name}</span>
      </li>)}
    </ul>}
  </figure>
}

function ComparisonResult({group,fallback,charts,selectors,tasks}) {
  const result_ids=group.tables.map(t=>t.result_id)
  const result=useQuery('/results/comparison',{result_ids},true,0,'POST')
  if(result.loading)return <section aria-label="통합 비교 결과"><h3>비교 결과</h3>
    {fallback && <Chart initial={fallback}/>}
    <p role="status">비교 표를 구성하고 있습니다.</p>
  </section>
  // 통합 비교가 실패해도(국가 20개 초과 등) 각 결과는 유효하다. 원래 차트와 표를 하위 질문별 제목 아래 그대로 보여 준다.
  if(result.error)return <section aria-label="통합 비교 결과"><h3>비교 결과</h3>
    <ErrorNotice error={result.error} retry={result.reload}/>
    {[...new Set(group.tables.map(t=>t.task_id))].map(taskId=>{
      const index=tasks.findIndex(t=>t.task_id===taskId),own=group.tables.filter(t=>t.task_id===taskId)
      return <div key={taskId}>{group.tables.length>own.length && <h4>{index+1}. {tasks[index]?.question}</h4>}
        {charts.filter(c=>own.some(t=>t.result_id===c.result_id)).map(c=><Chart key={c.result_id+':'+c.chart_id} initial={c}
          selector={selectors?.find(x=>x.task_id===c.task_id&&x.result_id===c.result_id)} table={own.find(t=>t.result_id===c.result_id)}/>)}
        {own.map(t=><ApiResults key={t.result_id} table={t} tableOnly/>)}</div>
    })}
  </section>
  const table=result.data.table,chart=comparisonChart(table,group.conditions)
  return <section aria-label="통합 비교 결과"><h3>비교 결과</h3>
    {table.rows.length>0 && <Chart initial={chart}/>}
    {chart.truncated && <p className="note">그래프에는 금액 상위 5개 대상만 표시합니다. 전체 비교 결과는 표에서 확인하세요.</p>}
    <ApiResults table={table} tableOnly onDownload={()=>download('/results/comparison',undefined,{method:'POST',body:{result_ids,format:'xlsx'}})}/>
  </section>
}

export default function TaskResults({answer,hideText=false}) {
  const charts=comparisonCharts(answer.charts||[],answer.tasks)
  const groups=comparisonGroups(answer)
  const groupedTables=new Set(groups.flatMap(g=>g.tables.map(t=>t.result_id)))
  const groupedTasks=new Set(groups.flatMap(g=>g.tables.map(t=>t.task_id)))
  const conclusionCount=tasks=>tasks.reduce((count,task)=>count+task.conclusion.length,0)
  const mismatches=new Map((answer.verification?.sentence_checks||[]).filter(c=>c.status==='MISMATCH').map(c=>[c.index,c]))
  const sentence=(s,i)=><p key={i}>{s} {mismatches.has(i)&&<span className="tag amber" title={mismatches.get(i).reason==='SOURCE_UNAVAILABLE'?'저장된 조회 결과가 만료되어 원본을 확인할 수 없습니다.':'수치·대상·기간·단위를 검증 근거와 일치한다고 확인하지 못했습니다.'}>확인 필요{mismatches.get(i).reason==='SOURCE_UNAVAILABLE'?' · 원본 확인 불가':''}</span>}</p>
  const sections=answer.tasks.map((t,index)=>{
    const start=conclusionCount(answer.tasks.slice(0,index))
    return <section key={t.task_id} className="chat-result"><h3>{index+1}. {t.question} {badges[t.answer_status]&&<span className="tag amber">{badges[t.answer_status]}</span>}</h3>
      {!hideText && t.conclusion.map((s,i)=>sentence(s,start+i))}
      {charts.filter(c=>c.task_id===t.task_id&&!groupedTasks.has(c.task_id)).map(c=><Chart key={c.result_id+':'+c.chart_id} initial={c} selector={answer.chart_selectors?.find(x=>x.task_id===c.task_id&&x.result_id===c.result_id)} table={answer.tables.find(x=>x.result_id===c.result_id)}/>)}
      {groups.filter(g=>g.task_id===t.task_id).map(g=><ComparisonResult key={g.tables.map(table=>table.result_id).join(',')} group={g}
        charts={answer.charts||[]} selectors={answer.chart_selectors} tasks={answer.tasks}  // 오류 시 원본 차트(합치기 전)를 보여 준다
        fallback={charts.filter(chart=>g.tables.some(table=>table.task_id===chart.task_id)).sort((a,b)=>(b.type==='line'?100:0)+b.series.length-(a.type==='line'?100:0)-a.series.length)[0]}/>)}
      {answer.tables.filter(x=>x.task_id===t.task_id&&!groupedTables.has(x.result_id)).map(x=><ApiResults key={x.result_id} table={x} tableOnly/>)}</section>
  })
  const offset=conclusionCount(answer.tasks)
  return <>{sections}{!hideText && answer.text.split('\n').slice(offset).map((s,i)=>sentence(s,offset+i))}</>
}
