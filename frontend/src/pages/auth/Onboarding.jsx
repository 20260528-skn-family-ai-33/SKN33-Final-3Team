// BS03~BS05 최초 관심사 등록(품목 → 국가 → 완료)
import { useEffect, useState } from 'react'
import { navigate } from '../../router'
import { useApp } from '../../store'
import { Btn, Modal, Steps } from '../../components/ui'
import { usePendingAction } from '../../api/hooks'
import { CountryPicker, InterestProducts } from '../../components/pickers'
import { productName, registerProducts } from '../../data/catalog'

function readDraft(key, version) {
  try {
    const draft = JSON.parse(sessionStorage.getItem(key))
    if (draft?.version && draft.version !== version) return null
    if (!Array.isArray(draft?.items) || !Array.isArray(draft.countries) ||
        !draft.items.every(h => /^\d{6}$/.test(h?.hs_code) && typeof h.name_ko === 'string') ||
        !draft.countries.every(c => typeof c === 'string')) return null
    registerProducts(draft.items)
    return draft
  } catch { return null }
}

export default function Onboarding({ route }) {
  const { session, profile, setProfile, setMainView, notify } = useApp()
  const step = Math.min(3, Math.max(1, Number(route.query.get('step')) || 1))
  const key = 'bs.onboarding.' + session.id
  const [version] = useState(profile.version)
  const [draft] = useState(() => readDraft(key, version))
  const welcome = draft?.welcome === true
  const [products, setProducts] = useState(() => draft?.items.map(h => h.hs_code) ?? profile.products)
  const [countries, setCountries] = useState(draft?.countries ?? profile.countries)
  const [skipOpen, setSkipOpen] = useState(false)
  const [pending, runOnce] = usePendingAction()
  useEffect(() => {
    const items = products.map(id => ({ hs_code: id, name_ko: productName(id) }))
    try { sessionStorage.setItem(key, JSON.stringify({ items, countries, welcome, version })) } catch { /* Storage may be unavailable. */ }
  }, [key, products, countries, welcome, version])
  const endDraft = () => {
    try { sessionStorage.removeItem(key) } catch { /* Optional draft storage. */ }
    if (welcome) notify('회원가입이 완료되었습니다. 환영합니다!')
  }
  const hasInterest = products.length > 0 || countries.length > 0

  const skip = () => runOnce(async () => {
    if (!await setProfile(p => ({ ...p, home: 'all' }))) return
    endDraft()
    setMainView('all')
    navigate('/')
  })
  const requestSkip = () => hasInterest ? setSkipOpen(true) : skip()
  const finish = () => runOnce(async () => {
    if (!await setProfile(p => ({ ...p, products, countries, home: hasInterest ? 'interest' : 'all' }))) return
    endDraft()
    setMainView(null)
    navigate(hasInterest ? '/?view=interest' : '/')
  })

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
                <InterestProducts products={products} setProducts={setProducts} />
                <div className="between" style={{ marginTop: 28 }}>
                  <Btn size="md" disabled={pending} onClick={requestSkip}>건너뛰기</Btn>
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
                    <Btn size="md" disabled={pending} onClick={requestSkip}>건너뛰기</Btn>
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
                    {products.length ? <ul className="chips onboarding-products">{products.map(id =>
                      <li key={id} className="tag">{productName(id)} <span className="hs">HS {id}</span></li>)}</ul> : <span>선택 안 함</span>}
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
                  <Btn kind="red" size="lg" iconRight="arrowRight" disabled={pending} onClick={finish}>완료하고 시작하기</Btn>
                </div>
                <p className="note" style={{ marginTop: 14 }}>선택한 관심사가 없으면 전체 시장 메인으로 이동합니다. 재로그인 시에는 이 과정을 생략합니다.</p>
              </>
            )}
          </div>
        </div>
      </main>
      <Modal open={skipOpen} onClose={() => !pending && setSkipOpen(false)} label="관심사 등록 건너뛰기">
        <h2 className="t2">관심사 등록을 건너뛸까요?</h2>
        <p className="muted" style={{ marginTop: 12 }}>이번 화면에서 변경한 품목과 국가는 저장되지 않습니다. 기존에 저장한 관심정보는 유지되며, 마이페이지에서 수정할 수 있습니다.</p>
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 12, marginTop: 24 }}>
          <Btn size="md" disabled={pending} onClick={() => setSkipOpen(false)}>취소</Btn>
          <Btn kind="red" size="md" disabled={pending} onClick={skip}>{pending ? '처리 중…' : '저장하지 않고 건너뛰기'}</Btn>
        </div>
      </Modal>
    </>
  )
}
