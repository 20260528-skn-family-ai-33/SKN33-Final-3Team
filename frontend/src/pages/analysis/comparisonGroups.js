/** Group only equivalent monthly trade amounts; independent analyses stay separate. */
export function comparisonGroups(answer) {
  const groups=new Map()
  for(const table of answer.tables){
    const task=answer.tasks.find(t=>t.task_id===table.task_id),c=task?.conditions
    if(task?.intent!=='STATISTICS'||!c||table.meta?.kind!=='TREND'||table.expired||!table.result_id)continue
    const key=JSON.stringify([c.period?.start,c.period?.end,c.trade_flow||'EXPORT',c.metric||'AMOUNT'])
    if(!groups.has(key))groups.set(key,{task_id:task.task_id,conditions:c,tables:[]})
    groups.get(key).tables.push(table)
  }
  return [...groups.values()].filter(group=>{
    const codes=new Set(group.tables.flatMap(t=>t.rows.map(r=>r.country_code).filter(Boolean)))
    const tasks=answer.tasks.filter(t=>group.tables.some(table=>table.task_id===t.task_id))
    tasks.forEach(t=>t.conditions.country_codes?.forEach(c=>codes.add(c)))
    const products=new Set(tasks.flatMap(t=>t.conditions.hs_items?.map(h=>h.hs_ver+h.hs_code)||[]))
    return group.tables.length>1||codes.size>1||products.size>1
  })
}

export function comparisonChart(table,conditions) {
  const months=[...new Set(table.rows.map(r=>r.month))].sort()
  const periodKey=(month,unit)=>unit==='MONTH'?month:unit==='YEAR'?month.slice(0,4):`${month.slice(0,4)}-Q${Math.floor((Number(month.slice(5,7))-1)/3)+1}`
  let granularity='MONTH',labels=months
  for(const unit of ['MONTH','QUARTER','YEAR']){
    granularity=unit;labels=[...new Set(months.map(m=>periodKey(m,unit)))]
    if(labels.length<=60)break
  }
  const products=new Map()
  table.rows.forEach(row=>{if(!products.has(row.hs_code))products.set(row.hs_code,[]);products.get(row.hs_code).push(row)})
  const columns=table.columns.filter(c=>c.type==='number')
  const series=[]
  for(const rows of products.values())for(const col of columns){
    const lookup=new Map(rows.map(r=>[r.month,r[col.key]]))
    const values=labels.map(label=>{
      const data=months.filter(m=>periodKey(m,granularity)===label).map(m=>lookup.get(m)??null)
      return data.some(v=>v===null)?null:data.reduce((sum,v)=>sum+v,0)
    })
    series.push({name:products.size>1?`${rows[0].name} · ${col.label}`:col.label,values})
  }
  series.sort((a,b)=>b.values.reduce((s,v)=>s+(v||0),0)-a.values.reduce((s,v)=>s+(v||0),0))
  return {type:'line',title:(conditions.trade_flow==='IMPORT'?'수입액':'수출액')+' 비교',unit:'USD',source:'관세청',
    granularity,labels,full_labels:labels,period:conditions.period,series:series.slice(0,5),truncated:series.length>5}
}
