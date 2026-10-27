// BS22 알림설정 · BS23 알림 수신 약관 동의 모달
import { useState } from 'react'
import { Link, navigate, withQuery } from '../../router'
import { useApp } from '../../store'
import { Btn, Check, Modal, Toggle } from '../../components/ui'
import { productName } from '../../data/catalog'

const ROWS = [
  ['all', '전체 알림 수신', '끄면 팝업·새 알림함 등록 중단, 기존 내역 유지'],
  ['surge', '급증 기사', '관심 범위의 품목·키워드 급증 팝업'],
  ['major', '주요 이슈', '전체 주요 무역 이슈 팝업'],
  ['report', '리포트 발행', '주간·월간·분기 구독 리포트 발행 팝업'],
]
const TERMS_VERSION = 'v1.2'

export default function NotificationSettings({ route }) {
  const { settings, setSettings, profile, notify } = useApp()
  const [draft, setDraft] = useState(settings)
  const [agree, setAgree] = useState(false)
  const termsOpen = route.query.get('terms') === 'open'
  const set = patch => setDraft(d => ({ ...d, ...patch }))
  const termsOk = draft.termsAgreed && draft.termsVersion === TERMS_VERSION

  const toggle = (key, on) => {
    if (key === 'all' && on && !termsOk) {
      setAgree(false)
      navigate(withQuery(route, { terms: 'open' }), { replace: true })
      return
    }
    set({ [key]: on })
  }
  const closeTerms = () => navigate(withQuery(route, { terms: null }), { replace: true })
  const acceptTerms = () => {
    set({ all: true, termsAgreed: true, termsVersion: TERMS_VERSION })
    closeTerms()
  }
  const save = () => {
    setSettings(draft)
    notify(draft.all ? '알림 설정을 저장했습니다.' : '알림 설정을 저장했습니다. 전체 알림 수신이 꺼져 있어 새 알림이 등록되지 않습니다.')
  }
  const interestText = `${profile.countries.join('·') || '국가 없음'} / ${profile.products.map(id => `${productName(id)}(HS ${id})`).join(', ') || '품목 없음'}`

  return (
    <section className="card">
      <h2 className="t1" style={{ fontSize: 24 }}>알림설정</h2>
      <p className="muted" style={{ marginTop: 6 }}>받고 싶은 알림과 관심 조건을 관리합니다.</p>
      <div style={{ marginTop: 10 }}>
        {ROWS.map(([key, title, desc]) => (
          <div key={key} className="between" style={{ padding: '18px 0', borderBottom: '1px solid var(--line2)', opacity: key !== 'all' && !draft.all ? 0.55 : 1 }}>
            <div>
              <p style={{ fontSize: 17, fontWeight: 800 }}>{title}</p>
              <p className="muted" style={{ marginTop: 4 }}>{desc}</p>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
              <Toggle on={draft[key]} onChange={on => toggle(key, on)} label={title} disabled={key !== 'all' && !draft.all} />
            </div>
          </div>
        ))}
      </div>
      <div className="between" style={{ marginTop: 18, border: '1px solid var(--line)', borderRadius: 14, padding: '16px 20px' }}>
        <span><span className="muted">관심정보</span> <b style={{ marginLeft: 8 }}>{interestText}</b></span>
        <Link to="/account/interests" className="more">관심정보 수정 →</Link>
      </div>
      <p className="note" style={{ marginTop: 12 }}>기본 꺼짐 또는 가입 동의 결과 적용 · 전체 켬 + 현재 약관 동의 시 수신 {termsOk ? `· 알림 약관 ${TERMS_VERSION} 동의 완료` : '· 알림 약관 미동의'}</p>
      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, marginTop: 20 }}>
        <Btn size="md" onClick={() => setDraft(settings)}>취소</Btn>
        <Btn kind="red" size="md" onClick={save}>설정 저장</Btn>
      </div>

      <Modal open={termsOpen} onClose={closeTerms} width={640} label="알림 수신 약관 동의">
        <p style={{ fontSize: 13, fontWeight: 800, color: 'var(--red-d)' }}>알림 켜기 전 확인</p>
        <h2 style={{ fontSize: 26, fontWeight: 800, marginTop: 6 }}>알림 수신 약관 동의</h2>
        <p className="muted" style={{ marginTop: 8, fontSize: 15 }}>알림을 받으려면 현재 버전의 알림 수신 약관에 동의해야 합니다.</p>
        <div style={{ marginTop: 18, border: '1px solid var(--line)', borderRadius: 14, overflow: 'hidden' }}>
          <div className="between" style={{ padding: '12px 18px', background: 'var(--bg)' }}>
            <b style={{ fontSize: 14 }}>알림 수신 약관</b><span className="tag red">현재 적용 버전 {TERMS_VERSION}</span>
          </div>
          <div style={{ padding: 18, height: 150, overflowY: 'auto', fontSize: 14, color: '#444B57', lineHeight: 1.8 }}>
            [현재 버전의 알림 수신 약관 본문]<br />
            <span className="note">수신 항목: 급증 기사 · 주요 이슈 · 리포트 발행 / 수신 방법: 서비스 내 팝업·알림함<br />약관 내용을 확인한 후 동의 여부를 선택하세요.</span>
          </div>
        </div>
        <Check checked={agree} onChange={setAgree} style={{ marginTop: 16 }}>위 알림 수신 약관을 확인했으며 동의합니다.</Check>
        <p className="note" style={{ marginTop: 8 }}>동의 전에는 알림이 켜지지 않습니다.</p>
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, marginTop: 22 }}>
          <Btn size="md" onClick={closeTerms}>거부</Btn>
          <Btn kind="red" size="md" disabled={!agree} onClick={acceptTerms}>동의하고 켜기</Btn>
        </div>
      </Modal>
    </section>
  )
}
