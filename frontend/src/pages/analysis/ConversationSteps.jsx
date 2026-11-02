import { useEffect, useState } from 'react'
import { Btn, Check } from '../../components/ui'
import { Hs4Search } from '../../components/pickers'
import { countryName, productName } from '../../data/catalog'

export function Progress({job}) {
  const [time,setTime]=useState(Date.now)
  useEffect(()=>{const timer=setInterval(()=>setTime(Date.now()),1000);return ()=>clearInterval(timer)},[])
  const start=Date.parse(job.round_started_at)
  const {label,done=0,total=0}=job.progress || {}
  return <div className="chat-step"><div className="between"><b role="status">{label || (job.status==='QUEUED'?'분석 대기 중':'분석 중')}</b>
    {Number.isFinite(start) && <small>{Math.max(0, Math.floor((time-start)/1000))}초 경과</small>}</div>
    <progress aria-label="분석 진행률" max={total||1} value={total?done:undefined}/></div>
}

export function PromptActions({prompt,busy,reply}) {
  const [group,setGroup]=useState(null)
  const [formats,setFormats]=useState(prompt.report_preview?.formats || ['PDF'])
  const [countries,setCountries]=useState([])
  const [products,setProducts]=useState([])
  const toggle=(list,value)=>list.includes(value)?list.filter(v=>v!==value):[...list,value]
  const preview=prompt.report_preview
  return <div className="chat-step"><strong>{prompt.message}</strong>
    {prompt.choices?.length>0 && <div className="chips">{prompt.choices.map(c=><Btn key={c.choice_id} disabled={busy} onClick={()=>reply({choice_ids:[c.choice_id]})}>{c.label}</Btn>)}</div>}
    {prompt.kind==='HS_ITEM' && <><Hs4Search group={group} onConfirm={setGroup} buttonLabel={()=>'이 HS4 선택'} onSelectAll={()=>group && reply({hs_item:{hs_code:group.code,hs_ver:'HS2022'}})}/><div className="chips">{group?.items.map(([code,name])=><Btn key={code} disabled={busy} onClick={()=>reply({hs_item:{hs_code:code,hs_ver:'HS2022'}})}>{name} ({code})</Btn>)}</div></>}
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
