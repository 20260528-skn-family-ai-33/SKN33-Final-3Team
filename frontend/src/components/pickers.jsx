// 관심 품목(HS4 → HS6)·관심 국가 선택 부품. 관심사 등록(BS03·BS04)과 관심정보(BS26)에서 함께 쓴다.
import { useState } from 'react'
import Icon from './Icon'
import { Btn, Check } from './ui'
import { countries, registerProducts, toggleValue } from '../data/catalog'
import { api } from '../api/client'
import { useQuery, ErrorNotice, useDebouncedValue } from '../api/hooks'
import { useApp } from '../store'

/** HS4 검색·확정 입력. 확정되면 onConfirm(group)을 부른다. */
export function Hs4Search({ group, onConfirm, buttonLabel, onSelectAll }) {
  const { run } = useApp()
  const [query, setQuery] = useState(group ? `${group.code} · ${group.name}` : '')
  const [open, setOpen] = useState(false)
  const confirmed = group && query === `${group.code} · ${group.name}`
  const q = query.trim()
  const settled = useDebouncedValue(q)
  const search = useQuery('/reference/hs-codes', { q: settled, depth: 4, limit: 5 }, settled.length >= 2 && !confirmed)
  const results = (settled === q ? search.data?.items || [] : []).map(h => ({ code: h.hs_code, name: h.name_ko }))
  return (
    <div style={{ display: 'flex', gap: 12, marginTop: 20, position: 'relative' }}>
      <div className={'input' + (q ? ' val' : '') + (open ? ' focus' : '')} style={{ flex: 1, gap: 8 }}>
        <Icon name="search" size={18} color="#5C6370" />
        <input
          value={query} placeholder="품목명 또는 HS 코드 입력 (예: 8542, 메모리)" aria-label="품목 검색"
          onChange={e => { setQuery(e.target.value); setOpen(true); if (confirmed) onConfirm(null) }}
          onFocus={() => setOpen(true)} onBlur={() => setTimeout(() => setOpen(false), 150)}
          style={{ fontWeight: confirmed ? 700 : 400 }}
        />
        {confirmed && <span className="tag plum">HS4 확정</span>}
      </div>
      <Btn kind="ink" size="md" disabled={!confirmed} onClick={onSelectAll} style={{ height: 50, borderRadius: 12 }}>{buttonLabel(group)}</Btn>
      {open && !confirmed && (
        <div role="listbox" style={{ position: 'absolute', top: 56, left: 0, right: 0, zIndex: 20, background: '#fff', border: '1px solid var(--line)', borderRadius: 12, boxShadow: '0 16px 40px rgba(21,24,30,.12)', padding: 6 }}>
          {results.map(g => (
            <button type="button" key={g.code} className="reset" role="option" aria-selected="false"
              onMouseDown={e => e.preventDefault()}
              onClick={() => run(async () => { const data = await api(`/reference/hs-codes/${g.code}/children`, { query: { hs_ver: 'HS2022' } }); registerProducts(data.items); setQuery(`${g.code} · ${g.name}`); setOpen(false); onConfirm({ ...g, items: data.items.map(h => [h.hs_code, h.name_ko]) }) })}
              style={{ display: 'flex', width: '100%', gap: 10, alignItems: 'center', padding: '10px 12px', borderRadius: 8 }}>
              <span className="hs">HS4 {g.code}</span><b>{g.name}</b><span className="note">하위 품목 보기</span>
            </button>
          ))}
          <ErrorNotice error={search.error} retry={search.reload} />
          {!results.length && !search.error && <p className="muted" style={{ padding: '10px 12px' }}>{q.length < 2 ? '품목명 또는 HS4를 두 글자 이상 입력하세요.' : search.loading ? '검색 중…' : '검색 결과가 없습니다.'}</p>}
        </div>
      )}
    </div>
  )
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
