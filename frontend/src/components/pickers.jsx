// 관심 품목(HS4 → HS6)·관심 국가 선택 부품. 관심사 등록(BS03·BS04)과 관심정보(BS26)에서 함께 쓴다.
import { useRef, useState } from 'react'
import Icon from './Icon'
import { Btn, Check } from './ui'
import { countries, registerProducts, toggleValue, productName } from '../data/catalog'
import { api } from '../api/client'
import { useQuery, ErrorNotice, useDebouncedValue } from '../api/hooks'
import { useApp } from '../store'

const HS_PREVIEW_LENGTH = 52
const compactHsName = name => name.length > HS_PREVIEW_LENGTH ? name.slice(0, HS_PREVIEW_LENGTH - 1) + '…' : name

function FullHsName({ name, code }) {
  if (name.length <= HS_PREVIEW_LENGTH) return null
  return <details className="hs-full-name">
    <summary aria-label={`HS ${code} 전체 이름`}>전체 이름</summary>
    <p>{name}</p>
  </details>
}

/** HS4 검색·확정 입력. 확정되면 onConfirm(group)을 부른다. */
export function Hs4Search({ group, onConfirm, buttonLabel, onSelectAll, onSelectProduct }) {
  const { run } = useApp()
  const [query, setQuery] = useState(group ? `${group.code} · ${group.name}` : '')
  const [open, setOpen] = useState(false)
  const selection = useRef(0)
  const confirmed = group && query === `${group.code} · ${group.name}`
  const q = query.trim()
  const settled = useDebouncedValue(q)
  const exactProduct = !!onSelectProduct && /^\d{6}$/.test(settled)
  const search = useQuery('/reference/hs-codes', { q: settled, depth: exactProduct ? 6 : 4, limit: 5 }, settled.length >= 2 && !confirmed)
  const tryProducts = !!onSelectProduct && !/^\d+$/.test(settled) && !!search.data && search.data.match_type !== 'EXACT'
  const fallback = useQuery('/reference/hs-codes', { q: settled, depth: 6, limit: 5 }, tryProducts)
  const preferProducts = tryProducts && (!search.data.items.length || fallback.data?.match_type === 'EXACT')
  const active = preferProducts ? fallback : search
  const results = (settled === q ? active.data?.items || [] : []).filter(h => !exactProduct || h.hs_code === q)
  const select = async h => {
    const revision = ++selection.current
    setOpen(false)
    if (h.depth === 6) {
      registerProducts([h])
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
  return (
    <div className="hs-picker">
      <div className={'input' + (q ? ' val' : '') + (open ? ' focus' : '')}>
        <Icon name="search" size={18} color="#5C6370" />
        <input
          value={query} placeholder={onSelectProduct ? '품목명 또는 HS 코드 입력 (예: 8542, 메모리)' : '품목명 또는 HS4 코드 입력 (예: 8542)'} aria-label="품목 검색"
          onChange={e => { selection.current++; setQuery(e.target.value); setOpen(true); if (confirmed) onConfirm(null) }}
          onFocus={() => setOpen(true)} onBlur={() => setTimeout(() => setOpen(false), 150)}
          style={{ fontWeight: confirmed ? 700 : 400 }}
        />
        {q && <button type="button" className="reset more" aria-label="품목 검색 지우기" onClick={() => { selection.current++; setQuery(''); onConfirm(null); setOpen(false) }}>지우기</button>}
        {confirmed && <span className="tag plum">HS4 확정</span>}
      </div>
      <Btn kind="ink" size="md" disabled={!confirmed} onClick={onSelectAll} style={{ height: 50, borderRadius: 12 }}>{buttonLabel(group)}</Btn>
      {open && !confirmed && (
        <div role="listbox" style={{ position: 'absolute', top: 56, left: 0, right: 0, zIndex: 20, background: '#fff', border: '1px solid var(--line)', borderRadius: 12, boxShadow: '0 16px 40px rgba(21,24,30,.12)', padding: 6 }}>
          {results.map(h => (
            <button type="button" key={h.hs_code} className="reset" role="option" aria-selected="false"
              onMouseDown={e => e.preventDefault()}
              onClick={() => run(() => select(h))}
              style={{ display: 'flex', width: '100%', gap: 10, alignItems: 'center', padding: '10px 12px', borderRadius: 8 }}>
              <span className="hs">HS{h.depth} {h.hs_code}</span><b className="hs-label" title={h.name_ko}>{compactHsName(h.name_ko)}</b><span className="note">{h.depth === 6 ? '관심 품목 추가' : '하위 품목 보기'}</span>
            </button>
          ))}
          <ErrorNotice error={active.error} retry={active.reload} />
          {!results.length && !active.error && <p className="muted" style={{ padding: '10px 12px' }}>{q.length < 2 ? '품목명 또는 HS 코드를 두 글자 이상 입력하세요.' : settled !== q || active.loading ? '검색 중…' : '검색 결과가 없습니다.'}</p>}
        </div>
      )}
    </div>
  )
}

export function InterestProducts({ products, setProducts }) {
  const [group, setGroup] = useState(null)
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
      <div className="between"><b>선택한 품목 {products.length}개</b>
        {!!products.length && <Btn onClick={() => setProducts([])}>선택 품목 전체 삭제</Btn>}
      </div>
      <ul className="chips">{products.map(id => <li key={id}><button type="button" className="chip sm"
        onClick={() => setProducts(p => p.filter(code => code !== id))} title={productName(id)} aria-label={`${productName(id)} 삭제`}>
        {compactHsName(productName(id))} · {id} ×
      </button><FullHsName name={productName(id)} code={id} /></li>)}</ul>
    </div>
  </>
}

/** 대륙 추가 칩 + 국가 체크 목록(관심사 등록용 격자 / 관심정보용 칩) */
export function CountryPicker({ value, onChange, variant = 'grid' }) {
  const [query, setQuery] = useState('')
  const regions = useQuery('/reference/regions')
  const continents = (regions.data?.items || []).filter(r => !r.parent_id)
  const list = countries.filter(c => !query.trim() || c.name.includes(query.trim()))
  const shown = variant === 'chips' && !query.trim()
    ? [...countries.filter(c => value.includes(c.name)), ...countries.filter(c => !value.includes(c.name)).slice(0, 3)]
    : (query.trim() ? list : countries.slice(0, Math.max(9, value.length)))
  const addContinent = region => onChange([...new Set([...value, ...countries.filter(c => region.country_codes.includes(c.country_code)).map(c => c.name)])])
  return (
    <>
      <div className={'input' + (query ? ' val' : '')} style={{ marginTop: variant === 'grid' ? 20 : 12 }}>
        <input value={query} onChange={e => setQuery(e.target.value)} placeholder="국가명 검색" aria-label="국가명 검색" />
        <Icon name="search" size={18} color="#9AA0AA" />
      </div>
      <div className="chips" style={{ marginTop: variant === 'grid' ? 14 : 12 }}>
        {continents.map(c => <button type="button" key={c.region_id} className="chip sm dash" onClick={() => addContinent(c)}>+ {c.name_ko}</button>)}
      </div>
      <p className="note" style={{ marginTop: 8 }}>
        {regions.error ? '대륙 정보가 준비되지 않아 국가별 선택을 제공합니다.' : variant === 'grid' ? '대륙 추가 시 해당 대륙의 모든 국가가 자동 추가됩니다.' : '대륙 추가 → 해당 대륙의 모든 국가 자동 추가 · 중복 제외'}
      </p>
      {!!value.length && <Btn onClick={() => onChange([])}>선택 국가 전체 삭제</Btn>}
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
    </>
  )
}
