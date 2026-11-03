import { useEffect, useId, useState } from 'react'
import Icon from '../../components/Icon'
import { Btn, Check } from '../../components/ui'
import { Hs4Search } from '../../components/pickers'
import { countryName, productName } from '../../data/catalog'

export function Progress({job}) {
  const [time,setTime]=useState(Date.now)
  const [open,setOpen]=useState(false)
  const panelId=useId()
  const active=['QUEUED','RUNNING','WAITING_REPORT'].includes(job.status)
  useEffect(()=>{if(!active)return;const timer=setInterval(()=>setTime(Date.now()),1000);return ()=>clearInterval(timer)},[active])
  const start=Date.parse(job.round_started_at)
  const {label,plan=[]}=job.progress || {}
  const total=plan.length || job.progress?.total || 0
  const done=plan.length?plan.filter(s=>['DONE','FAILED','SKIPPED'].includes(s.status)).length:job.progress?.done||0
  const percent=total?Math.max(0,Math.min(100,Math.round(100*done/total))):0
  const statuses={PENDING:'예정',RUNNING:'진행 중',WAITING:'대기 중',DONE:'완료',FAILED:'실패',SKIPPED:'건너뜀'}
  return <div className="chat-step"><div className="between"><b>분석 계획</b><div className="chips"><span>{percent}%{total>0 && ` · ${done}/${total}단계`}</span><Btn aria-label={open?'분석 계획 접기':'분석 계획 펼치기'} aria-expanded={open} aria-controls={panelId} onClick={()=>setOpen(v=>!v)}><Icon name={open?'chevronUp':'chevronDown'}/></Btn></div></div>
    <div id={panelId} hidden={!open} className="chat-plan-details">
    <div className="chat-progress" role="progressbar" aria-label="분석 진행률" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}><span style={{width:`${percent}%`}}/></div>
    <div className="between"><span role="status">{total?label:job.status==='QUEUED'?'분석 대기 중':'질문을 읽고 계획을 작성하고 있습니다.'}</span>
      {active && Number.isFinite(start) && <small>{Math.max(0, Math.floor((time-start)/1000))}초 경과</small>}</div>
    {plan.length>0 && <ol className="chat-plan">{plan.map((step,i)=>{
      const current=(active||job.status==='NEEDS_INPUT') && (step.status==='RUNNING'||step.status==='WAITING')
      return <li key={step.id} aria-current={current?'step':undefined}>
        {step.question && step.question!==plan[i-1]?.question && <small>{step.question}</small>}
        <div>{current?<strong>{step.label}</strong>:<span>{step.label}</span>} <span className="note">{statuses[step.status]}</span></div>
      </li>
    })}</ol>}</div></div>
}

export function PromptActions({prompt,busy,reply,onDirectInput}) {
  const [group,setGroup]=useState(null)
  const [hs4Only,setHs4Only]=useState(false)
  const [formats,setFormats]=useState(prompt.report_preview?.formats || ['PDF'])
  const [countries,setCountries]=useState([])
  const [products,setProducts]=useState([])
  const toggle=(list,value)=>list.includes(value)?list.filter(v=>v!==value):[...list,value]
  const preview=prompt.report_preview
  return <div className="chat-step"><strong>{prompt.message}</strong>
    {prompt.choices?.length>0 && <div className="chips">{prompt.choices.map(c=><Btn key={c.choice_id} disabled={busy} onClick={()=>reply({choice_ids:[c.choice_id]})}>{c.label}</Btn>)}</div>}
    {prompt.kind==='COUNTRY' && <div><Btn disabled={busy} onClick={onDirectInput}>목록에 없는 국가 직접 입력</Btn><p className="note">전체 국가명 또는 국가 코드로 입력하세요. 예: 중국, CN</p></div>}
    {prompt.kind==='HS_ITEM' && <><div className="chips" role="group" aria-label="분석 품목 단위">
      <Btn disabled={busy} aria-pressed={hs4Only} kind={hs4Only?'plum':'line'} onClick={()=>setHs4Only(true)}>HS4 전체로 분석</Btn>
      <Btn disabled={busy} aria-pressed={!hs4Only} kind={!hs4Only?'plum':'line'} onClick={()=>setHs4Only(false)}>HS6 세부품목 선택</Btn>
    </div><p className="note">{hs4Only?'HS4 분류를 검색한 뒤 해당 분류 전체로 분석할 수 있습니다.':'HS4 분류를 검색한 뒤 아래에서 HS6 세부품목을 선택하세요.'}</p>
    <Hs4Search group={group} onConfirm={setGroup} buttonLabel={()=>'이 HS4 전체로 분석'} onSelectAll={()=>!busy && group && reply({hs_item:{hs_code:group.code,hs_ver:'HS2022'}})}/>{!hs4Only && <div className="chips">{group?.items.map(([code,name])=><Btn key={code} disabled={busy} onClick={()=>reply({hs_item:{hs_code:code,hs_ver:'HS2022'}})}>{name} ({code})</Btn>)}</div>}</>}
    {prompt.kind==='INTEREST_SELECT' && <><p>국가 {countries.length}/20 · 품목 {products.length}/5</p><div className="chips">
      {prompt.interests?.country_codes?.map(c=><Btn key={c} aria-pressed={countries.includes(c)} kind={countries.includes(c)?'plum':'line'} disabled={busy||(!countries.includes(c)&&countries.length>=20)} onClick={()=>setCountries(toggle(countries,c))}>{countryName(c)}</Btn>)}
      {prompt.interests?.hs_items?.map(h=><Btn key={h.hs_ver+h.hs_code} aria-pressed={products.includes(h)} kind={products.includes(h)?'plum':'line'} disabled={busy||(!products.includes(h)&&products.length>=5)} onClick={()=>setProducts(toggle(products,h))}>{productName(h.hs_code)} ({h.hs_code})</Btn>)}</div>
      <Btn kind="red" disabled={busy||(!countries.length&&!products.length)} onClick={()=>reply({interest_selection:{country_codes:countries,hs_items:products}})}>선택 적용</Btn></>}
    {preview && <><p>{preview.conditions.period.start} ~ {preview.conditions.period.end} · {preview.conditions.country_codes?.map(countryName).join(', ')||'전체 국가'}{preview.conditions.hs_items?.map(h=>' · HS '+h.hs_code)}</p>
      {preview.conditions.compare_period && <p>비교 기간 {preview.conditions.compare_period.start} ~ {preview.conditions.compare_period.end}</p>}
      {preview.adjustments?.map(a=><p key={a}>{a}</p>)}
      <div className="chips">{['PDF','DOCX'].map(f=><Check key={f} checked={formats.includes(f)} onChange={()=>setFormats(toggle(formats,f))}>{f}</Check>)}</div>
      <div className="chips">{[true,false].map(on=><Btn key={String(on)} kind={on?'red':'line'} disabled={busy||(on&&!formats.length)} onClick={()=>reply({report_confirmation:{preview_id:preview.preview_id,formats:formats.length?formats:['PDF'],confirm:on}})}>{on?'확인하고 생성':'생성 취소'}</Btn>)}</div></>}
    {prompt.allow_text && <p className="note">아래 입력창에 직접 답할 수 있습니다.</p>}</div>
}
