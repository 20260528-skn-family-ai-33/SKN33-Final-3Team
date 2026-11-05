import { countryName, productName } from '../../data/catalog.js'

/** Combine comparable ordinary trade series; keep ML and distinct metrics apart. */
export function comparisonCharts(charts,tasks) {
  const output=[],groups=new Map()
  for(const chart of charts){
    const task=tasks.find(t=>t.task_id===chart.task_id),c=task?.conditions
    if(chart.type!=='line'||task?.intent!=='STATISTICS'||!c||chart.source==='ML 모델'){
      output.push(chart);continue
    }
    const key=JSON.stringify([chart.title,chart.unit,chart.source,chart.granularity,c.period,c.compare_period,c.trade_flow])
    const target=[...(c.country_codes||[]).map(countryName),...(c.hs_items||[]).map(h=>productName(h.hs_code))].join(' · ')
    const series=chart.series.map(s=>({...s,name:target?`${target} · ${s.name}`:s.name}))
    const previous=groups.get(key)
    if(previous && previous.series.length+series.length<=5 && !series.some(s=>previous.series.some(p=>p.name===s.name))){
      const labels=[...new Set([...previous.labels,...chart.labels])].sort()
      if(labels.length>60){output.push(chart);continue}
      previous.series=previous.series.map(s=>({...s,values:labels.map(l=>s.values[previous.labels.indexOf(l)]??null)}))
        .concat(series.map(s=>({...s,values:labels.map(l=>s.values[chart.labels.indexOf(l)]??null)})))
      previous.labels=labels;previous.full_labels=labels
    }else{
      const copy={...chart,series,labels:[...chart.labels]}
      output.push(copy);groups.set(key,copy)
    }
  }
  return output
}
