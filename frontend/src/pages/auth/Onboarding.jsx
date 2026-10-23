// BS03~BS05 최초 관심사 등록(품목 → 국가 → 완료)
import { useState } from 'react'
import { navigate } from '../../router'
import { useApp } from '../../store'
import { Btn, Check, Steps } from '../../components/ui'
import { CountryPicker, Hs4Search } from '../../components/pickers'
import { hs4Groups, productName, toggleValue } from '../../data/catalog'

export default function Onboarding({ route }) {
  const { profile, setProfile, setMainView } = useApp()
  const step = Math.min(3, Math.max(1, Number(route.query.get('step')) || 1))
  const [products, setProducts] = useState(profile.products)
  const [countries, setCountries] = useState(profile.countries)
  const [group, setGroup] = useState(hs4Groups[0])
  const hasInterest = products.length > 0 || countries.length > 0

  const skip = () => {
    setProfile(p => ({ ...p, home: 'all' }))
    setMainView('all')
    navigate('/')
  }
  const finish = () => {
    setProfile(p => ({ ...p, products, countries, home: hasInterest ? 'interest' : 'all' }))
    setMainView(null)
    navigate(hasInterest ? '/?view=interest' : '/')
  }

  return (
    <>
      <section className="band lgbg" style={{ padding: '44px 0 92px', textAlign: 'center' }}>
        <div className="wrap" style={{ display: 'block' }}>
          <p className="eyebrow">STEP {step} / 3</p>
          <h1>관심사 등록</h1>
          <p className="lead">관심 품목과 국가를 선택하면 맞춤 무역 정보를 보여드립니다.</p>
        </div>
      </section>
      <main className="page" style={{ paddingTop: 0 }}>
        <div className="wrap" style={{ maxWidth: 880, marginTop: -60, position: 'relative', zIndex: 1 }}>
          <div className="card flat" style={{ padding: '30px 40px 36px' }}>
            <Steps items={['품목 선택', '국가 선택', '완료']} current={step - 1} />
            <div style={{ height: 1, background: 'var(--line)', margin: '28px 0' }} />

            {step === 1 && (
              <>
                <h2 className="t2">관심 품목을 선택해 주세요</h2>
                <p className="muted" style={{ marginTop: 6 }}>선택 사항 · 품목명 또는 HS6 코드로 검색하고 추가할 수 있습니다.</p>
                <Hs4Search
                  group={group} onConfirm={setGroup} buttonLabel={() => '하위 품목 전체선택'}
                  onSelectAll={() => setProducts(p => [...new Set([...p, ...group.items.map(([id]) => id)])])}
                />
                <p className="note" style={{ marginTop: 8 }}>유효한 HS 4자리 선택 확정 후 활성화 · 미확정 상태에서는 비활성화</p>
                {group && (
                  <div className="col" style={{ gap: 10, marginTop: 18 }}>
                    {group.items.map(([id, name]) => {
                      const on = products.includes(id)
                      return (
                        <Check key={id} checked={on} onChange={() => setProducts(p => toggleValue(p, id))} className={'li' + (on ? ' sel' : '')} style={{ padding: '14px 18px' }}>
                          <span className="hs">{id}</span><span style={{ fontSize: 16, fontWeight: 700 }}>{name}</span>
                        </Check>
                      )
                    })}
                  </div>
                )}
                {products.length > 0 && (
                  <div style={{ marginTop: 18, padding: '14px 18px', borderRadius: 12, background: 'var(--rose)', fontSize: 14, color: 'var(--red-d)', fontWeight: 700 }}>
                    선택한 품목 {products.length}개 · {products.map(id => `${productName(id)} / HS ${id}`).join(', ')}
                  </div>
                )}
                <div className="between" style={{ marginTop: 28 }}>
                  <Btn size="md" onClick={skip}>건너뛰기</Btn>
                  <Btn kind="red" size="md" iconRight="arrowRight" to="/onboarding?step=2">다음: 국가 선택</Btn>
                </div>
                <p className="note" style={{ marginTop: 14 }}>건너뛰면 전체 시장이 기본 메인으로 표시됩니다. 관심정보는 나중에 수정할 수 있습니다.</p>
              </>
            )}

            {step === 2 && (
              <>
                <h2 className="t2">관심 국가를 선택해 주세요</h2>
                <p className="muted" style={{ marginTop: 6 }}>선택 사항 · 여러 국가를 선택할 수 있습니다.</p>
                <CountryPicker value={countries} onChange={setCountries} />
                <p className="note" style={{ marginTop: 12 }}>대륙·개별 국가를 함께 선택해도 중복 국가는 한 번만 추가합니다. 이전 버튼으로 돌아가도 품목·국가의 선택값을 유지합니다.</p>
                <div className="between" style={{ marginTop: 26 }}>
                  <div style={{ display: 'flex', gap: 10 }}>
                    <Btn size="md" icon="arrowLeft" to="/onboarding?step=1">이전</Btn>
                    <Btn size="md" onClick={skip}>건너뛰기</Btn>
                  </div>
                  <Btn kind="red" size="md" iconRight="arrowRight" to="/onboarding?step=3">다음: 완료 확인</Btn>
                </div>
              </>
            )}

            {step === 3 && (
              <>
                <div style={{ textAlign: 'center' }}>
                  <span style={{ width: 64, height: 64, borderRadius: 32, background: 'var(--red)', display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}>
                    <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M5 12l5 5 9-10" /></svg>
                  </span>
                  <h2 className="t1" style={{ marginTop: 18 }}>선택한 관심사를 확인해 주세요</h2>
                  <p className="muted" style={{ marginTop: 8, fontSize: 15 }}>설정을 완료하면 관심사가 반영된 메인으로 이동합니다.</p>
                </div>
                <div style={{ marginTop: 28, border: '1px solid var(--line)', borderRadius: 16, overflow: 'hidden' }}>
                  <div className="tr" style={{ gridTemplateColumns: '140px 1fr', padding: '18px 22px' }}>
                    <span className="s">관심 품목</span>
                    <span className="b">{products.length ? products.map(id => <span key={id} style={{ marginRight: 12 }}>{productName(id)} <span className="hs">HS {id}</span></span>) : '선택 안 함'}</span>
                  </div>
                  <div className="tr" style={{ gridTemplateColumns: '140px 1fr', padding: '18px 22px' }}>
                    <span className="s">관심 국가</span><span className="b">{countries.length ? countries.join(' · ') : '선택 안 함'}</span>
                  </div>
                  <div className="tr" style={{ gridTemplateColumns: '140px 1fr', padding: '18px 22px', border: 0 }}>
                    <span className="s">기본 메인</span><span><span className={'tag ' + (hasInterest ? 'red' : '')}>{hasInterest ? '내 관심' : '전체'}</span></span>
                  </div>
                </div>
                <ul style={{ margin: '18px 0 0', paddingLeft: 18 }} className="note">
                  <li>이전: 국가 선택으로 돌아가기 / 선택값 유지</li>
                  <li>등록 내용은 마이페이지 관심정보에서 수정할 수 있습니다.</li>
                  <li>메인 우측 상단 화면 설정에서 전체 ↔ 내 관심을 전환할 수 있습니다.</li>
                </ul>
                <div className="between" style={{ marginTop: 26 }}>
                  <Btn size="md" icon="arrowLeft" to="/onboarding?step=2">이전</Btn>
                  <Btn kind="red" size="lg" iconRight="arrowRight" onClick={finish}>완료하고 시작하기</Btn>
                </div>
                <p className="note" style={{ marginTop: 14 }}>선택한 관심사가 없으면 전체 시장 메인으로 이동합니다. 재로그인 시에는 이 과정을 생략합니다.</p>
              </>
            )}
          </div>
        </div>
      </main>
    </>
  )
}
