/** Bridge the race between the message-list read and the final job-status read. */
export function completedMessages(rows,job) {
  if(job?.status!=='SUCCEEDED'||!job.answer)return rows
  const user=rows.findIndex(m=>m.role==='USER'&&m.job?.job_id===job.job_id)
  if(user<0)return rows
  const next=rows.findIndex((m,i)=>i>user&&m.role==='USER')
  const end=next<0?rows.length:next
  if(rows.slice(user+1,end).some(m=>m.role==='ASSISTANT'&&m.answer))return rows
  const answer={message_id:`completed-${job.job_id}`,role:'ASSISTANT',answer:job.answer,content_masked:job.answer.text||''}
  return [...rows.slice(0,end),answer,...rows.slice(end)]
}
