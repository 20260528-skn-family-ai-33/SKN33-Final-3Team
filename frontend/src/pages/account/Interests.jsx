// BS26 관심정보(관심 국가·품목, 로그인 시작 화면)
import { useState } from 'react'
import { useApp } from '../../store'
import { Btn } from '../../components/ui'
import Icon from '../../components/Icon'
import { CountryPicker, Hs4Search } from '../../components/pickers'
import { productName, toggleValue } from '../../data/catalog'

export default function Interests() {
  const { profile, setProfile, notify } = useApp()
  const [countries, setCountries] = useState(profile.countries)
  const [products, setProducts] = useState(profile.products)
  const [home, setHome] = useState(profile.home)
  const [group, setGroup] = useState(null)
  const hasInterest = countries.length > 0 || products.length > 0
  const effectiveHome = hasInterest ? home : 'all'

  const reset = () => {
    setCountries(profile.countries); setProducts(profile.products); setHome(profile.home)
  }
  const save = async () => {
    if (!await setProfile(p => ({ ...p, countries, products, home: effectiveHome }))) return
    notify('관심정보와 시작 화면을 저장했습니다. 맞춤 조회·알림·구독 리포트에 바로 적용됩니다.')
  }
  const radio = (value, label, disabled) => (
    <label className="opt" style={{ opacity: disabled ? 0.5 : 1, cursor: disabled ? 'not-allowed' : 'pointer' }}>
      <input type="radio" className="sr-only" name="home" checked={effectiveHome === value} disabled={disabled} onChange={() => setHome(value)} />
      <span className={'rd' + (effectiveHome === value ? ' on' : '')} style={{ borderColor: effectiveHome === value ? '#FF8A9A' : '#8F89B8' }} />
      {effectiveHome === value ? <b>{label}</b> : label}
    </label>
  )

  return (
    <section className="card">
      <h2 className="t1" style={{ fontSize: 24 }}>관심정보</h2>
      <p className="muted" style={{ marginTop: 6 }}>저장된 선택값을 불러왔습니다. 여러 국가와 품목을 선택할 수 있습니다.</p>
      <div style={{ marginTop: 24 }}>
        <p style={{ fontSize: 16, fontWeight: 800 }}>관심 국가 <span className="tag red" style={{ marginLeft: 6 }}>{countries.length}개 선택</span></p>
        <CountryPicker variant="chips" value={countries} onChange={setCountries} />
      </div>
      <div style={{ marginTop: 26, paddingTop: 22, borderTop: '1px solid var(--line)' }}>
        <p style={{ fontSize: 16, fontWeight: 800 }}>관심 품목 / HS6</p>
        <Hs4Search
          group={group} onConfirm={setGroup} buttonLabel={g => `${g ? g.code + ' ' : ''}하위 품목 전체선택`}
          onSelectAll={() => setProducts(p => [...new Set([...p, ...group.items.map(([id]) => id)])])}
        />
        {group && (
          <div className="chips" style={{ marginTop: 12 }}>
            {group.items.map(([id, name]) => {
              const on = products.includes(id)
              return <button type="button" key={id} className={'chip sm' + (on ? ' on' : '')} aria-pressed={on} onClick={() => setProducts(p => toggleValue(p, id))}>{on ? '✓ ' : ''}{name} · {id}</button>
            })}
          </div>
        )}
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginTop: 12, flexWrap: 'wrap' }}>
          {products.map(id => (
            <button type="button" key={id} className="chip sm on" style={{ background: 'var(--red)', borderColor: 'var(--red)' }} onClick={() => setProducts(p => p.filter(x => x !== id))} aria-label={`${productName(id)} 삭제`}>
              {productName(id)} / {id} <Icon name="close" size={14} color="#fff" stroke={2.2} />
            </button>
          ))}
          <span className="muted">선택한 품목 {products.length}개</span>
        </div>
        <p className="note" style={{ marginTop: 8 }}>유효한 HS 4자리 선택 확정 후 활성화 · 해당 HS4 하위 HS6만 추가</p>
      </div>
      <div style={{ marginTop: 24, background: 'var(--plum)', color: '#fff', borderRadius: 16, padding: '20px 22px' }}>
        <p style={{ fontSize: 16, fontWeight: 800 }}>로그인 시작 화면</p>
        <div style={{ display: 'flex', gap: 24, marginTop: 12 }} role="radiogroup" aria-label="로그인 시작 화면">
          {radio('all', '전체 시장', false)}
          {radio('interest', '내 관심 시장', !hasInterest)}
        </div>
        <p style={{ marginTop: 10, fontSize: 13, color: '#D6D3E6' }}>저장 후 다음 로그인부터 적용 · 현재 메인 보기 전환과 별도 · 관심정보가 없으면 전체 시장으로 고정되며 내 관심 시장은 선택할 수 없습니다.</p>
      </div>
      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, marginTop: 22 }}>
        <Btn size="md" onClick={reset}>취소</Btn>
        <Btn kind="red" size="md" onClick={save}>관심정보·시작 화면 저장</Btn>
      </div>
    </section>
  )
}
