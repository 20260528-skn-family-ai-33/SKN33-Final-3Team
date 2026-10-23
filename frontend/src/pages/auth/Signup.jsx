// BS02 회원가입 · LG05 소셜 신규 회원 추가 정보
import { useState } from 'react'
import { navigate } from '../../router'
import { useApp } from '../../store'
import { Btn, Check, TextField } from '../../components/ui'
import { PASSWORD_HINT, isEmail, isStrongPassword } from '../../utils/validate'
import PasswordRules from '../../components/PasswordRules'
import { AuthFrame, CardHead, PlainAuthPage } from './AuthFrame'

export function Signup() {
  const { accounts, signup } = useApp()
  const [form, setForm] = useState({ name: '', email: '', password: '', confirm: '' })
  const [error, setError] = useState({})
  const set = key => value => setForm(f => ({ ...f, [key]: value }))
  const passwordReady = isStrongPassword(form.password) && form.password === form.confirm

  const submit = e => {
    e.preventDefault()
    const next = {}
    if (!form.name.trim()) next.name = '이름을 입력해 주세요.'
    if (!isEmail(form.email)) next.email = '이메일 형식을 확인해 주세요.'
    else if (accounts.some(a => a.email.toLowerCase() === form.email.trim().toLowerCase())) next.email = '이미 가입된 이메일입니다.'
    if (!isStrongPassword(form.password)) next.password = PASSWORD_HINT
    if (form.password !== form.confirm) next.confirm = '비밀번호가 일치하지 않습니다.'
    setError(next)
    if (Object.keys(next).length) return
    signup(form)
    navigate('/onboarding')
  }

  const field = (key, label, props) => (
    <>
      <label className="lbl" htmlFor={'su-' + key}>{label}</label>
      <TextField id={'su-' + key} value={form[key]} onChange={set(key)} invalid={!!error[key]} {...props} />
      {error[key] && <p className="field-err">{error[key]}</p>}
    </>
  )

  return (
    <AuthFrame lead={<>가입 후 관심 품목과 국가를 고르면<br />메인 화면과 알림이 내 관심 기준으로 맞춰집니다.</>}>
      <form onSubmit={submit} noValidate>
        <CardHead eyebrow="회원가입" title="계정을 만들어 주세요" desc="Borderless Sellers 계정을 만든 뒤 관심사를 설정합니다." />
        <div className="sp32" />
        {field('name', '이름', { placeholder: '이름을 입력하세요', autoComplete: 'name' })}
        <div className="sp16" />
        {field('email', '이메일 (아이디)', { placeholder: 'name@example.com', type: 'email', autoComplete: 'email' })}
        <div className="sp16" />
        {field('password', '비밀번호', { placeholder: '영문·숫자·특수문자 포함 8자 이상', type: 'password', autoComplete: 'new-password' })}
        <PasswordRules value={form.password} showErrors={!!error.password} />
        <div className="sp16" />
        {field('confirm', '비밀번호 확인', { placeholder: '비밀번호를 다시 입력하세요', type: 'password', autoComplete: 'new-password' })}
        {form.confirm && form.password === form.confirm && !error.confirm && <p className="field-ok">✓ 비밀번호가 일치합니다</p>}
        <p className="note" style={{ marginTop: 10 }}>이름은 필수 입력입니다. 이메일 형식·중복 여부와 비밀번호 일치 여부를 확인합니다.</p>
        <div className="sp24" />
        <Btn type="submit" kind="red" size="lg" block disabled={!passwordReady}>회원가입하고 관심사 설정하기</Btn>
        {!passwordReady && <p className="note" style={{ marginTop: 8, textAlign: 'center' }}>비밀번호 조건을 모두 만족하고 비밀번호 확인이 일치해야 가입할 수 있습니다.</p>}
        <div className="sp12" />
        <Btn to="/login" size="lg" block>로그인 화면으로 돌아가기</Btn>
        <p className="note" style={{ marginTop: 16 }}>회원가입 완료 후에만 최초 관심사 등록을 진행합니다.</p>
      </form>
    </AuthFrame>
  )
}

export function SocialSignup() {
  const { signup, accounts } = useApp()
  const [pending] = useState(() => {
    try { return JSON.parse(sessionStorage.getItem('bs2.socialPending') || 'null') } catch { return null }
  })
  const [form, setForm] = useState({ name: pending?.name || '', email: pending?.email || '', terms: false, privacy: false })
  const [error, setError] = useState('')
  const ready = form.terms && form.privacy

  if (!pending) {
    return (
      <PlainAuthPage eyebrow="소셜 신규 회원" title="소셜 인증 정보가 없어요" desc="카카오·네이버·구글 로그인을 먼저 진행해 주세요. 인증 후 신규 회원이면 이 화면으로 돌아옵니다.">
        <div style={{ marginTop: 28, maxWidth: 340 }}><Btn kind="red" size="md" to="/login" style={{ height: 56, justifyContent: 'flex-start', padding: '0 22px' }}>로그인 선택으로 돌아가기</Btn></div>
      </PlainAuthPage>
    )
  }

  const provider = { kakao: '카카오', naver: '네이버', google: '구글' }[pending.provider]
  const submit = () => {
    if (!form.name.trim()) return setError('이름을 입력해 주세요.')
    if (!isEmail(form.email)) return setError('연락 이메일 형식을 확인해 주세요.')
    if (accounts.some(a => a.email.toLowerCase() === form.email.trim().toLowerCase())) return setError('이미 가입된 이메일입니다. 기존 계정으로 로그인한 뒤 연결해 주세요.')
    signup({ name: form.name, email: form.email, provider: pending.provider, providerId: pending.providerId })
    sessionStorage.removeItem('bs2.socialPending')
    navigate('/onboarding')
  }

  return (
    <PlainAuthPage eyebrow={`소셜 신규 회원 · ${provider}`} title="가입을 마무리해 주세요">
      <p className="muted" style={{ fontSize: 15, marginTop: 10 }}>{provider} 인증이 끝났습니다. 처음 오셨으니 이름과 연락 이메일을 확인하고 필수 약관에 동의해 주세요.</p>
      <div className="sp24" />
      <label className="lbl" htmlFor="ss-name">이름</label>
      <TextField id="ss-name" placeholder="제공자가 준 이름이 있으면 미리 채움 · 수정 가능" value={form.name} onChange={v => setForm(f => ({ ...f, name: v }))} />
      <div className="sp16" />
      <label className="lbl" htmlFor="ss-email">연락 이메일</label>
      <TextField id="ss-email" type="email" placeholder="제공자 미제공·미검증 시 직접 입력하고 인증" value={form.email} onChange={v => setForm(f => ({ ...f, email: v }))} />
      {!pending.email && <p className="note" style={{ marginTop: 6 }}>{provider}에서 이메일을 받지 못했습니다. 알림·리포트를 받을 이메일을 직접 입력해 주세요.</p>}
      <div className="col" style={{ gap: 12, marginTop: 22 }}>
        <Check checked={form.terms} onChange={v => setForm(f => ({ ...f, terms: v }))}>서비스 이용약관 동의 (필수)</Check>
        <Check checked={form.privacy} onChange={v => setForm(f => ({ ...f, privacy: v }))}>개인정보 수집·이용 동의 (필수)</Check>
      </div>
      {error && <p className="field-err" role="alert" style={{ marginTop: 12 }}>{error}</p>}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, marginTop: 28 }}>
        <Btn kind="red" size="md" disabled={!ready} onClick={submit} style={{ height: 56, justifyContent: 'flex-start', padding: '0 22px' }}>필수 동의 완료 → 관심 설정</Btn>
        <Btn size="md" onClick={() => { sessionStorage.removeItem('bs2.socialPending'); navigate('/login') }} style={{ height: 56, justifyContent: 'flex-start', padding: '0 22px' }}>취소</Btn>
      </div>
    </PlainAuthPage>
  )
}
