// BS24 회원정보(비밀번호 수정 진입·동의 관리·회원탈퇴) · BS24-1 회원탈퇴 사유 선택
import { useState } from 'react'
import { navigate, withQuery } from '../../router'
import { useApp } from '../../store'
import { Btn, Check, Modal, TextArea, TextField, Toggle } from '../../components/ui'
import { providerLabel } from '../../data/seed'

const CONSENTS = [
  ['service', '서비스 이용약관 동의', true],
  ['privacy', '개인정보 수집·이용 동의', true],
  ['optional', '선택 정보 수집·이용 동의', false],
]
const REASONS = ['데이터가 부족해서', '다른 사이트 사용', '구독료가 비싸서']
const OTHER = '기타 (직접 작성)'

function WithdrawModal({ open, onClose, onConfirm }) {
  const [reasons, setReasons] = useState([])
  const [other, setOther] = useState('')
  const hasOther = reasons.includes(OTHER)
  const otherMissing = hasOther && !other.trim()
  const valid = reasons.length > 0 && !otherMissing
  const toggle = r => setReasons(list => (list.includes(r) ? list.filter(x => x !== r) : [...list, r]))
  const box = on => ({ padding: '12px 14px', border: `1px solid ${on ? 'var(--red)' : 'var(--line)'}`, borderRadius: 12, background: on ? '#FBF4F5' : '#fff' })
  return (
    <Modal open={open} onClose={onClose} width={600} label="회원탈퇴 사유 선택">
      <div className="between">
        <span className="note" style={{ fontWeight: 700 }}>회원탈퇴</span>
        <Btn onClick={onClose}>닫기</Btn>
      </div>
      <h2 style={{ fontSize: 26, fontWeight: 800, marginTop: 14, color: 'var(--red)' }}>탈퇴 사유를 선택해 주세요</h2>
      <p className="muted" style={{ marginTop: 8, fontSize: 14, lineHeight: 1.6 }}>사유를 1개 이상 선택해야 탈퇴할 수 있습니다. 여러 개를 함께 선택할 수 있습니다.</p>
      <div className="col" style={{ gap: 8, marginTop: 18 }} role="group" aria-label="탈퇴 사유">
        {[...REASONS, OTHER].map(r => <Check key={r} checked={reasons.includes(r)} onChange={() => toggle(r)} style={box(reasons.includes(r))}>{r}</Check>)}
      </div>
      {hasOther && (
        <div style={{ marginTop: 10 }}>
          <TextArea rows={3} value={other} onChange={setOther} placeholder="탈퇴 사유를 직접 입력해 주세요" aria-label="기타 탈퇴 사유" invalid={otherMissing} maxLength={300} />
          <p className="note" style={{ marginTop: 6, color: 'var(--red-d)', fontWeight: 700 }}>{otherMissing ? '기타를 선택하면 사유를 입력해야 탈퇴할 수 있습니다.' : `${other.trim().length} / 300자`}</p>
        </div>
      )}
      <p className="note" style={{ marginTop: 12 }}>탈퇴하면 탈퇴일로부터 1개월 동안 보존한 뒤 완전히 삭제되며, 보존 기간에는 다시 활성화할 수 없습니다.</p>
      <div className="between" style={{ marginTop: 20 }}>
        <Btn size="md" onClick={onClose}>취소</Btn>
        <Btn kind="red" size="md" disabled={!valid} onClick={() => onConfirm({ reasons: reasons.filter(r => r !== OTHER), other: hasOther ? other.trim() : '' })}>탈퇴하기</Btn>
      </div>
      {!valid && <p className="note" style={{ marginTop: 10, textAlign: 'right' }}>{reasons.length ? '기타 사유를 입력하면 탈퇴하기 버튼이 활성화됩니다.' : '사유를 선택하면 탈퇴하기 버튼이 활성화됩니다.'}</p>}
    </Modal>
  )
}

export default function Profile({ route }) {
  const { account, session, consents, setConsents, withdraw, notify } = useApp()
  const [email, setEmail] = useState('')
  const [emailError, setEmailError] = useState('')
  const [draft, setDraft] = useState(consents)
  const [termsView, setTermsView] = useState(null)
  const withdrawOpen = route.query.get('withdraw') === 'open'
  const social = account && account.provider !== 'email'

  const verify = () => {
    if (social) return setEmailError(`${providerLabel[account.provider]} 로그인 계정은 비밀번호가 없어 변경할 수 없습니다.`)
    if (email.trim().toLowerCase() !== (session?.email || '').toLowerCase()) return setEmailError('등록된 이메일과 일치하지 않습니다. 다시 확인해 주세요.')
    sessionStorage.setItem('bs2.pwVerified', session.email)
    navigate('/account/password')
  }

  return (
    <>
      <section className="card">
        <h2 className="t2" style={{ fontSize: 22 }}>비밀번호 수정</h2>
        <p className="muted" style={{ marginTop: 6 }}>등록 이메일과 일치하면 비밀번호 변경 창을 엽니다. 불일치 시 오류를 표시합니다.</p>
        <label className="lbl" htmlFor="pf-email" style={{ marginTop: 18 }}>이메일</label>
        <div style={{ display: 'flex', gap: 10 }}>
          <TextField id="pf-email" type="email" style={{ flex: 1 }} placeholder="등록된 이메일을 입력하세요" icon="mail" value={email} onChange={v => { setEmail(v); setEmailError('') }} onEnter={verify} invalid={!!emailError} />
          <Btn kind="ink" size="md" style={{ height: 50, borderRadius: 12 }} onClick={verify}>이메일 확인</Btn>
        </div>
        {emailError && <p className="field-err" role="alert">{emailError}</p>}
      </section>

      <section className="card">
        <h2 className="t2" style={{ fontSize: 22 }}>개인정보 수집 및 약관 동의</h2>
        <div style={{ marginTop: 8 }}>
          {CONSENTS.map(([key, title, required]) => (
            <div key={key} className="between" style={{ padding: '16px 0', borderBottom: '1px solid var(--line2)' }}>
              <p style={{ fontSize: 16, fontWeight: 700 }}>{title} <span className={'tag ' + (required ? 'red' : '')}>{required ? '필수' : '선택'}</span></p>
              <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
                <button type="button" className="reset more" style={{ fontSize: 13 }} onClick={() => setTermsView(title)}>내용 보기</button>
                <span className="muted" style={{ width: 32 }}>{draft[key] ? '켜짐' : '꺼짐'}</span>
                <Toggle on={draft[key]} label={title} onChange={on => {
                  if (required && !on) return notify('필수 동의는 철회하면 서비스를 이용할 수 없습니다. 철회를 원하시면 회원탈퇴를 진행해 주세요.')
                  setDraft(d => ({ ...d, [key]: on }))
                }} />
              </div>
            </div>
          ))}
        </div>
        <div className="between" style={{ marginTop: 18 }}>
          <span className="note">동의 변경은 저장 시 반영하며, 철회 시 적용되는 이용 범위를 먼저 안내합니다.</span>
          <Btn kind="red" size="md" onClick={() => { setConsents(draft) }}>동의 설정 저장</Btn>
        </div>
      </section>

      <section className="card" style={{ borderColor: '#F6D3D9', background: '#FFFBFB' }}>
        <div className="between">
          <div>
            <h2 className="t3">회원탈퇴</h2>
            <p className="muted" style={{ marginTop: 4 }}>탈퇴 안내와 삭제 대상 확인 후 최종 확인을 진행합니다.</p>
          </div>
          <Btn kind="danger" to={withQuery(route, { withdraw: 'open' })}>회원탈퇴</Btn>
        </div>
      </section>

      <WithdrawModal open={withdrawOpen} onClose={() => navigate(withQuery(route, { withdraw: null }), { replace: true })} onConfirm={withdraw} />

      <Modal open={!!termsView} onClose={() => setTermsView(null)} width={560} label={termsView || ''}>
        <p style={{ fontSize: 13, fontWeight: 800, color: 'var(--red-d)' }}>약관 내용</p>
        <h2 style={{ fontSize: 22, fontWeight: 800, marginTop: 6 }}>{termsView}</h2>
        <div style={{ marginTop: 14, border: '1px solid var(--line)', borderRadius: 12, padding: 16, height: 180, overflowY: 'auto', fontSize: 14, lineHeight: 1.8, color: '#444B57' }}>
          일반 약관 본문과 동의 관리는 운영 정책 확정 후 제공됩니다.
        </div>
        <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 18 }}><Btn kind="ink" size="md" onClick={() => setTermsView(null)}>확인</Btn></div>
      </Modal>
    </>
  )
}
