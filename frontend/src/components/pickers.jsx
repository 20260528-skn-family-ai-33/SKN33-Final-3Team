// 관심 품목(HS4 → HS6)·관심 국가 선택 부품. 관심사 등록(BS03·BS04)과 관심정보(BS26)에서 함께 쓴다.
import { useEffect, useId, useRef, useState } from 'react'
import Icon from './Icon'
import { Btn, Check } from './ui'
import { countries, registerProducts, toggleValue, productName, productChoiceLabel } from '../data/catalog'
import { api } from '../api/client'
import { useQuery, ErrorNotice, useDebouncedValue } from '../api/hooks'
import { useApp } from '../store'
import { mapLimited } from '../api/integration'

const HS_PREVIEW_LENGTH = 52
const compactHsName = name => name.length > HS_PREVIEW_LENGTH ? name.slice(0, HS_PREVIEW_LENGTH - 1) + '…' : name

function FullHsName({ name, code }) {
  if (name.length <= HS_PREVIEW_LENGTH) return null
  return <details className="hs-full-name">
    <summary aria-label={`HS ${code} 전체 이름`}>전체 이름</summary>
    <p>{name}</p>
  </details>
}

/** HS4 검색·확정 입력. 확정되면 onConfirm(group)을 부른다.
 * expandGroup: HS4 코드(4자리)를 정확히 넣으면 목록에 하위 HS6도 펼쳐, HS4 전체나 HS6 하나를 바로 고르게 한다(수출입 순위). */
export function Hs4Search({ group, onConfirm, buttonLabel, onSelectAll, onSelectProduct, expandGroup, hs6First }) {
  const { run } = useApp()
  const [query, setQuery] = useState(group ? `${group.code} · ${group.name}` : '')
  const [open, setOpen] = useState(false)
  const [hs4Only, setHs4Only] = useState(false)
  const selection = useRef(0)
  const inputRef = useRef(null)
  // 목록 안의 '다시 불러오기'를 누르면 그 버튼이 사라지며 포커스가 본문으로 빠지므로, 검색칸으로 되돌려 바깥 클릭 때 목록이 닫히게 한다.
  const retry = load => () => { load(); inputRef.current?.focus() }
  const confirmed = group && query === `${group.code} · ${group.name}`
  const q = query.trim()
  const searchText = hs6First ? q.replace(/^HS(?:\s*[46])?(?:\s*코드)?\s*[:\-]?\s*(\d{4}|\d{6})$/i, '$1') : q
  const searchQuery=confirmed ? '' : searchText
  const settled = useDebouncedValue(searchQuery)
  const exactProduct = !!onSelectProduct && !hs4Only && /^\d{6}$/.test(settled)
  const searchDepth = exactProduct || (hs6First && !hs4Only) ? 6 : 4
  const search = useQuery('/reference/hs-codes', { q: settled, depth: searchDepth, limit: 5 }, settled.length >= 1 && !confirmed)
  const tryProducts = !!onSelectProduct && !hs4Only && searchDepth === 4 && settled.length>=1 && !confirmed && !/^\d+$/.test(settled)
  const fallback = useQuery('/reference/hs-codes', { q: settled, depth: 6, limit: 5 }, tryProducts)
  const preferProducts = tryProducts && search.data?.match_type!=='EXACT' && !!fallback.data && (!search.data?.items.length || fallback.data.match_type === 'EXACT')
  const active = preferProducts ? fallback : search
  const found = (settled === searchText ? active.data?.items || [] : [])
    .filter(h => h.depth === (preferProducts ? 6 : searchDepth) && (!exactProduct || h.hs_code === searchText))
  const exactGroup = !hs4Only && expandGroup && onSelectProduct && /^\d{4}$/.test(q) ? found.find(h => h.hs_code === q && h.depth === 4) : null
  const children = useQuery(`/reference/hs-codes/${q}/children`, { hs_ver: 'HS2022' }, !!exactGroup && open && !confirmed)
  const results = exactGroup ? [exactGroup, ...(children.data?.items || [])] : found
  // 방향키로 고르는 중인 결과(-1이면 없음). Enter로 선택한다.
  const [cursor, setCursor] = useState(-1)
  const listId = useId()
  useEffect(() => { if (cursor >= 0) document.getElementById(`${listId}-${cursor}`)?.scrollIntoView({ block: 'nearest' }) }, [cursor, listId])
  const onKeyDown = e => {
    if (e.nativeEvent.isComposing || e.keyCode === 229 || confirmed) return
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault(); setOpen(true)
      setCursor(i => e.key === 'ArrowDown' ? Math.min(i + 1, results.length - 1) : Math.max(i - 1, 0))
    } else if (e.key === 'Enter' && cursor >= 0 && results[cursor]) {
      e.preventDefault(); const h = results[cursor]; setCursor(-1); run(() => select(h))
    } else if (e.key === 'Escape') { setOpen(false); setCursor(-1) }
  }
  const select = async h => {
    const revision = ++selection.current
    setOpen(false)
    registerProducts([h])
    if (h.depth === 6) {
      onSelectProduct(h.hs_code)
      setQuery('')
      return
    }
    const data = await api(`/reference/hs-codes/${h.hs_code}/children`, { query: { hs_ver: 'HS2022' } })
    if (revision !== selection.current) return
    registerProducts(data.items)
    setQuery(`${h.hs_code} · ${h.name_ko}`)
    onConfirm({ code: h.hs_code, name: h.name_ko, items: data.items.map(item => [item.hs_code, item.name_ko]) })
  }
  return <>
    {onSelectProduct && <div className="chips" role="group" aria-label="품목 검색 단위">
      <Btn aria-pressed={!hs4Only} kind={!hs4Only?'plum':'line'} onClick={()=>{selection.current++;setCursor(-1);setHs4Only(false);setOpen(true)}}>{hs6First ? 'HS6 세부품목 검색' : '통합 검색'}</Btn>
      <Btn aria-pressed={hs4Only} kind={hs4Only?'plum':'line'} onClick={()=>{selection.current++;setCursor(-1);setHs4Only(true);setOpen(true)}}>HS4 단위로 검색</Btn>
    </div>}
    <div className={'hs-picker' + (hs6First ? ' chat-hs-picker' : '')} onBlur={e => { if (!e.currentTarget.contains(e.relatedTarget)) setOpen(false) }}>
      <div className={'input' + (q ? ' val' : '') + (open ? ' focus' : '')}>
        <Icon name="search" size={18} color="#5C6370" />
        <input
          ref={inputRef} role="combobox" aria-expanded={open && !confirmed} aria-controls={listId} aria-autocomplete="list"
          aria-activedescendant={open && cursor >= 0 && results[cursor] ? `${listId}-${cursor}` : undefined} onKeyDown={onKeyDown}
          value={query} placeholder={onSelectProduct ? '품목명 또는 HS 코드 입력 (예: 8542, 메모리)' : '품목명 또는 HS4 코드 입력 (예: 8542)'} aria-label="품목 검색"
          onChange={e => { selection.current++; setQuery(e.target.value); setOpen(true); setCursor(-1); if (confirmed) onConfirm(null) }}
          onFocus={() => setOpen(true)}
          style={{ fontWeight: confirmed ? 700 : 400 }}
        />
        {q && <button type="button" className="reset more" aria-label="품목 검색 지우기" onClick={() => { selection.current++; setQuery(''); onConfirm(null); setOpen(false) }}>지우기</button>}
        {confirmed && <span className="tag plum">HS4 확정</span>}
      </div>
      {/* HS6 세부품목 검색에서는 HS4를 고를 수 없으므로 HS4 전체 버튼은 HS4 단위 검색에서만 보인다. */}
      {buttonLabel && (!hs6First || hs4Only) && <Btn kind="ink" size="md" disabled={!confirmed} onClick={onSelectAll} style={{ height: 50, borderRadius: 12 }}>{buttonLabel(group)}</Btn>}
      {open && !confirmed && (
        <div role="listbox" id={listId} aria-label="품목 검색 결과" style={{ position: hs6First ? 'static' : 'absolute', flexBasis: hs6First ? '100%' : undefined, top: 56, left: 0, right: 0, zIndex: 20, maxHeight: 340, overflowY: 'auto', background: '#fff', border: '1px solid var(--line)', borderRadius: 12, boxShadow: '0 16px 40px rgba(21,24,30,.12)', padding: 6 }}>
          {results.map((h, i) => (
            <button type="button" key={h.hs_code} id={`${listId}-${i}`} className="reset" role="option" aria-selected={i === cursor}
              onMouseDown={e => e.preventDefault()} onMouseEnter={() => setCursor(i)}
              onClick={() => run(() => select(h))}
              style={{ display: 'flex', width: '100%', gap: 10, alignItems: 'center', padding: '10px 12px', borderRadius: 8, background: i === cursor ? '#F1F2F5' : undefined }}>
              {!hs6First && <span className="hs">HS{h.depth} {h.hs_code}</span>}
              <b className="hs-label" title={h.name_ko}>{hs6First ? productChoiceLabel(h.hs_code,compactHsName(h.name_ko)) : compactHsName(h.name_ko)}</b>
              <span className="note">{h.depth === 6 ? (expandGroup || hs6First ? '이 품목 선택' : '관심 품목 추가') : exactGroup ? 'HS4 전체로 선택' : '하위 품목 보기'}</span>
            </button>
          ))}
          <ErrorNotice error={active.error} retry={retry(active.reload)} />
          {/* HS4 아래 하위 품목 목록을 못 불러오면 그 사실과 다시 시도를 보여 준다(HS4 전체만 남아 하위 품목이 없는 것처럼 보이지 않게). */}
          {exactGroup && <ErrorNotice error={children.error} retry={retry(children.reload)} />}
          {!results.length && !active.error && <p className="muted" style={{ padding: '10px 12px' }}>{searchText.length < 1 ? '품목명 또는 HS 코드를 한 글자 이상 입력하세요.' : settled !== searchText || active.loading ? '검색 중…' : '검색 결과가 없습니다.'}</p>}
        </div>
      )}
    </div>
  </>
}

export function InterestProducts({ products, setProducts }) {
  const [group, setGroup] = useState(null)
  const [names,setNames]=useState({})
  const [nameError,setNameError]=useState(null)
  const [nameRetry,setNameRetry]=useState(0)
  const missing=products.filter(id=>productName(id)===id).join(',')
  useEffect(()=>{
    if(!missing)return
    const controller=new AbortController()
    mapLimited(missing.split(','),4,async id=>{
      const data=await api('/reference/hs-codes',{query:{q:id,depth:id.length,limit:5},signal:controller.signal})
      return data.items.find(h=>h.hs_code===id)
    },controller.signal).then(items=>{
      if(controller.signal.aborted)return
      const found=items.filter(Boolean)
      registerProducts(found)
      setNames(previous=>({...previous,...Object.fromEntries(found.map(h=>[h.hs_code,h.name_ko]))}))
      setNameError(null)
    }).catch(error=>{if(!controller.signal.aborted)setNameError(error)})
    return ()=>controller.abort()
  },[missing,nameRetry])
  const selectedName=id=>names[id]||(productName(id)!==id?productName(id):'품목명 미확인')
  const ids = group?.items.map(([id]) => id) || []
  const allSelected = ids.length > 0 && ids.every(id => products.includes(id))
  const toggleGroup = () => setProducts(previous => allSelected
    ? previous.filter(id => !ids.includes(id)) : [...new Set([...previous, ...ids])])
  return <>
    <Hs4Search group={group} onConfirm={setGroup} buttonLabel={() => allSelected ? '하위 품목 전체해제' : '하위 품목 전체선택'}
      onSelectAll={toggleGroup} onSelectProduct={id => setProducts(previous => [...new Set([...previous, id])])} />
    {group && <FullHsName name={group.name} code={group.code} />}
    {group && <fieldset className="interest-group">
      <legend>하위 품목 선택 · HS {group.code}</legend>
      {group.items.map(([id, name]) => <div key={id}>
        <Check aria-label={`${id} ${name}`} checked={products.includes(id)} onChange={() => setProducts(p => toggleValue(p, id))}>
          <span className="hs">{id}</span> <span className="hs-label" title={name}>{compactHsName(name)}</span>
        </Check>
        <FullHsName name={name} code={id} />
      </div>)}
    </fieldset>}
    <div className="interest-summary">
      <ErrorNotice error={nameError} retry={()=>setNameRetry(v=>v+1)}/>
      <div className="between"><b>선택한 품목 {products.length}개</b>
        {!!products.length && <Btn onClick={() => setProducts([])}>선택 품목 전체 삭제</Btn>}
      </div>
      <ul className="chips">{products.map(id => <li key={id}><button type="button" className="chip sm"
        onClick={() => setProducts(p => p.filter(code => code !== id))} title={selectedName(id)} aria-label={`${id} ${selectedName(id)} 삭제`}>
        {id} · {compactHsName(selectedName(id))} ×
      </button><FullHsName name={selectedName(id)} code={id} /></li>)}</ul>
    </div>
  </>
}

/** 대륙 추가 칩 + 국가 체크 목록(관심사 등록용 격자 / 관심정보용 칩) */
export function CountryPicker({ value, onChange, variant = 'grid', includeKorea = false, showSelected = false }) {
  const [query, setQuery] = useState('')
  const regions = useQuery('/reference/regions')
  const continents = (regions.data?.items || []).filter(r => !r.parent_id)
  const countryNames = countries.map(c => c.name)
  const allCountriesSelected = countryNames.length > 0 && countryNames.every(name => value.includes(name))
  const choices=includeKorea?[{country_code:'__KOREA_TOTAL__',name:'한국(대한민국)'},...countries]:countries
  const list = choices.filter(c => !query.trim() || c.name.includes(query.trim()))
  const shown = variant === 'chips' && !query.trim()
    ? [...choices.filter(c => value.includes(c.name)), ...choices.filter(c => !value.includes(c.name)).slice(0, 3)]
    : (query.trim() ? list : choices.filter((c, index) => index < 9 || value.includes(c.name)))
  // 대륙 칩은 토글이다: 그 대륙 국가가 모두 선택돼 있으면 그 대륙 국가만 해제하고(다른 대륙·개별 선택은 유지), 아니면 모두 추가한다.
  const continentNames = region => countries.filter(c => region.country_codes.includes(c.country_code)).map(c => c.name)
  const continentOn = region => { const names = continentNames(region); return names.length > 0 && names.every(name => value.includes(name)) }
  const toggleContinent = region => {
    const names = continentNames(region)
    onChange(continentOn(region) ? value.filter(name => !names.includes(name)) : [...new Set([...value, ...names])])
  }
  return (
    <>
      <div className={'input' + (query ? ' val' : '')} style={{ marginTop: variant === 'grid' ? 20 : 12 }}>
        <input value={query} onChange={e => setQuery(e.target.value)} placeholder="국가명 검색" aria-label="국가명 검색" />
        <Icon name="search" size={18} color="#9AA0AA" />
      </div>
      <div className="chips" style={{ marginTop: variant === 'grid' ? 14 : 12 }}>
        <button type="button" className={'chip sm' + (allCountriesSelected ? ' on' : ' dash')}
          disabled={!countryNames.length} aria-pressed={allCountriesSelected}
          onClick={() => onChange(allCountriesSelected ? value.filter(name => !countryNames.includes(name)) : [...new Set([...value, ...countryNames])])}>
          전체 국가
        </button>
        {continents.map(c => {
          const on = continentOn(c)
          return <button type="button" key={c.region_id} className={'chip sm' + (on ? ' on' : ' dash')} aria-pressed={on}
            title={on ? `${c.name_ko} 국가 전체 해제` : `${c.name_ko} 국가 전체 추가`} onClick={() => toggleContinent(c)}>{on ? '✓' : '+'} {c.name_ko}</button>
        })}
      </div>
      <p className="note" style={{ marginTop: 8 }}>
        {regions.error ? '대륙 정보가 준비되지 않아 국가별 선택을 제공합니다.' : variant === 'grid' ? '대륙을 누르면 해당 대륙의 모든 국가가 추가되고, 한 번 더 누르면 그 대륙 국가만 해제됩니다.' : '대륙 추가 → 해당 대륙의 모든 국가 자동 추가 · 중복 제외 · 다시 누르면 해제'}
      </p>
      {!showSelected && !!value.length && <Btn onClick={() => onChange([])}>선택 국가 전체 삭제</Btn>}
      {variant === 'grid' ? (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 10, marginTop: 16 }} className="resp-stack">
          {shown.map(c => {
            const on = value.includes(c.name)
            return (
              <Check key={c.name} checked={on} onChange={() => onChange(toggleValue(value, c.name))} className={'li' + (on ? ' sel' : '')} style={{ padding: '13px 16px', gap: 12 }}>
                <span style={{ fontSize: 15, fontWeight: 700 }}>{c.name}</span>
              </Check>
            )
          })}
          {!shown.length && <p className="muted">검색 결과가 없습니다.</p>}
        </div>
      ) : (
        <div className="chips" style={{ marginTop: 10 }}>
          {shown.map(c => {
            const on = value.includes(c.name)
            return <button type="button" key={c.name} className={'chip sm' + (on ? ' on' : '')} aria-pressed={on} onClick={() => onChange(toggleValue(value, c.name))}>{on ? '✓ ' : ''}{c.name}</button>
          })}
        </div>
      )}
      {showSelected && <div className="interest-summary" role="region" aria-label="선택된 국가 목록">
        <div className="between"><b>선택한 국가 {value.length}개</b>
          {!!value.length && <Btn onClick={()=>onChange([])}>선택 국가 전체 삭제</Btn>}
        </div>
        {value.length ? <ul className="chips">{value.map(name=><li key={name}>
          <button type="button" className="chip sm" aria-label={`${name} 선택 삭제`}
            onClick={()=>onChange(value.filter(item=>item!==name))}>{name} ×</button>
        </li>)}</ul> : <p className="note">선택한 국가가 없습니다.</p>}
      </div>}
    </>
  )
}
