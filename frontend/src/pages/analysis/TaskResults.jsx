import { useState } from 'react'
import { api } from '../../api/client'
import { BarChart, Donut, LineChart } from '../../components/charts'
import ApiResults from '../../components/ApiResults'
import { niceTicks } from '../../utils/chart'
import { countryName, productName } from '../../data/catalog'
import { useApp } from '../../store'

const colors=['#a71b39','#342954','#4b7578','#967348','#6e6198']
const badges={PARTIAL:'일부 결과',NO_DATA:'결과 없음',OUT_OF_SCOPE:'범위 밖'}

function Chart({initial,selector}) {
  const [chart,setChart]=useState(initial)
  const [busy,setBusy]=useState(false)
  const [country,setCountry]=useState(selector?.country_codes[0]||'')
  const [hs,setHs]=useState(selector?.hs_items[0]?.hs_code||'')
  const {run}=useApp()
  const change=async(c,h)=>{
    setBusy(true)
    const product=selector.hs_items.find(p=>p.hs_code===h)
    const result=await run(()=>api(`/results/${initial.result_id}/chart`,{query:{country_code:c||undefined,hs_code:product?.hs_code,hs_ver:product?.hs_ver}}))
    if(result){setChart(result.chart);setCountry(c);setHs(h)}
    setBusy(false)
  }
  const series=chart.series.map((s,i)=>({...s,color:colors[i%colors.length]}))
  const props={labels:chart.labels,fullLabels:chart.full_labels,series,label:chart.title,formatValue:n=>n.toLocaleString('ko-KR')}
  return <figure style={{margin:'14px 0'}}><figcaption><b>{chart.title}</b> · {chart.unit} {({QUARTER:'· 분기별',YEAR:'· 연도별'})[chart.granularity]}</figcaption>
    {selector && <div className="chips">{selector.country_codes.length>0 && <select aria-label="차트 국가" disabled={busy} value={country} onChange={e=>change(e.target.value,hs)}>{selector.country_codes.map(c=><option key={c} value={c}>{countryName(c)}</option>)}</select>}{selector.hs_items.length>0 && <select aria-label="차트 품목" disabled={busy} value={hs} onChange={e=>change(country,e.target.value)}>{selector.hs_items.map(h=><option key={h.hs_code} value={h.hs_code}>{productName(h.hs_code)}</option>)}</select>}</div>}
    {chart.type==='line'?<LineChart {...props} yTicks={niceTicks(series.flatMap(s=>s.values.filter(v=>v!=null)))}/>:chart.type==='donut'?<><Donut items={chart.labels.map((l,i)=>[l,series[0].values[i]||0,colors[i%colors.length]])}/><p>{chart.labels.map((l,i)=>l+' '+(series[0].values[i]??'—')+'%').join(' · ')}</p></>:<BarChart {...props}/>}
    {series.length>1 && <p className="note">{series.map(s=>s.name).join(' · ')}</p>}
  </figure>
}

export default function TaskResults({answer}) {
  const conclusionCount=tasks=>tasks.reduce((count,task)=>count+task.conclusion.length,0)
  const mismatches=new Map((answer.verification?.sentence_checks||[]).filter(c=>c.status==='MISMATCH').map(c=>[c.index,c]))
  const sentence=(s,i)=><p key={i}>{s} {mismatches.has(i)&&<span className="tag amber" title={mismatches.get(i).reason==='SOURCE_UNAVAILABLE'?'저장된 조회 결과가 만료되어 원본을 확인할 수 없습니다.':'수치·대상·기간·단위를 검증 근거와 일치한다고 확인하지 못했습니다.'}>확인 필요{mismatches.get(i).reason==='SOURCE_UNAVAILABLE'?' · 원본 확인 불가':''}</span>}</p>
  const sections=answer.tasks.map((t,index)=>{
    const start=conclusionCount(answer.tasks.slice(0,index))
    return <section key={t.task_id} className="chat-result"><h3>{index+1}. {t.question} {badges[t.answer_status]&&<span className="tag amber">{badges[t.answer_status]}</span>}</h3>
      {t.conclusion.map((s,i)=>sentence(s,start+i))}
      {answer.charts?.filter(c=>c.task_id===t.task_id).map(c=><Chart key={c.chart_id} initial={c} selector={answer.chart_selectors?.find(x=>x.task_id===c.task_id&&x.result_id===c.result_id)}/>)}
      {answer.tables.filter(x=>x.task_id===t.task_id).map(x=><ApiResults key={x.result_id} table={x} tableOnly/>)}</section>
  })
  const offset=conclusionCount(answer.tasks)
  return <>{sections}{answer.text.split('\n').slice(offset).map((s,i)=>sentence(s,offset+i))}</>
}
