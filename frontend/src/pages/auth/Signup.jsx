// BS02 회원가입
import { useState } from 'react'
import { navigate } from '../../router'
import { useApp } from '../../store'
import { Btn, Check, Modal, TextField } from '../../components/ui'
import { PASSWORD_HINT, isEmail, isStrongPassword } from '../../utils/validate'
import PasswordRules from '../../components/PasswordRules'
import TermsBody from '../../components/TermsBody'
import { ErrorNotice, useQuery, usePendingAction } from '../../api/hooks'
import { AuthFrame, CardHead } from './AuthFrame'

const NO_AGREEMENT = { age: false, service: false, privacy: false }

export function Signup() {
  const { accounts, signup, notify } = useApp()
  const [form, setForm] = useState({ name: '', email: '', password: '', confirm: '' })
  const [error, setError] = useState({})
  const [pending, runOnce] = usePendingAction()
  const serviceTerms = useQuery('/terms/service')
  const privacyTerms = useQuery('/terms/privacy')
  const [agreed, setAgreed] = useState(NO_AGREEMENT)
  const [viewing, setViewing] = useState(null)
  const set = key => value => {
    setForm(f => ({ ...f, [key]: value }))
    setError(previous => ({ ...previous, [key]: undefined, form: undefined }))
  }
  const passwordReady = isStrongPassword(form.password) && form.password === form.confirm
  const serviceVersion = serviceTerms.data?.terms.version
  const privacyVersion = privacyTerms.data?.terms.version
  const agreementReady = agreed.age && agreed.service && agreed.privacy && !!serviceVersion && !!privacyVersion
  const ready = passwordReady && agreementReady
  const termsError = serviceTerms.error || privacyTerms.error
  const reloadTerms = () => { serviceTerms.reload(); privacyTerms.reload() }
  const agreement = (key, label, terms) => (
    <div className="between" style={{ gap: 12 }}>
      <Check checked={agreed[key]} onChange={value => setAgreed(current => ({ ...current, [key]: value }))}>{label}</Check>
      {terms && <button type="button" className="reset more" style={{ fontSize: 13, flexShrink: 0 }} disabled={!terms.data} onClick={() => setViewing(terms.data.terms)}>내용 보기</button>}
    </div>
  )

  const submit = async e => {
    e.preventDefault()
    const next = {}
    if (!form.name.trim()) next.name = '이름을 입력해 주세요.'
    else if ([...form.name].length > 120) next.name = '이름은 120자 이하로 입력해 주세요.'
    if (!isEmail(form.email)) next.email = '이메일을 name@example.com 형식으로 입력해 주세요.'
    else if (accounts.some(a => a.email === form.email.trim())) next.email = '이미 가입된 이메일입니다.'
    if (!isStrongPassword(form.password)) next.password = PASSWORD_HINT
    if (form.password !== form.confirm) next.confirm = '비밀번호가 일치하지 않습니다.'
    setError(next)
    if (Object.keys(next).length) {
      document.getElementById('su-' + Object.keys(next)[0])?.focus()
      return
    }
    await runOnce(async () => {
      const result = await signup({ ...form, ageConfirmed: agreed.age, serviceTermsVersion: serviceVersion, privacyTermsVersion: privacyVersion })
      if (result.ok) {
        try {
          sessionStorage.setItem('bs.onboarding.' + result.account.id, JSON.stringify({ items: [], countries: [], welcome: true }))
        } catch { notify('회원가입이 완료되었습니다. 환영합니다!') }
        return navigate('/onboarding')
      }
      const fieldError = (key, message) => {
        setError({ [key]: message })
        document.getElementById('su-' + key)?.focus()
      }
      if (result.code === 'TERMS_VERSION_CHANGED') {
        setAgreed(NO_AGREEMENT)
        reloadTerms()
      }
      if (result.code === 'PASSWORD_POLICY_VIOLATION') return fieldError('password', PASSWORD_HINT)
      if (['EMAIL_ALREADY_EXISTS', 'RESERVED_ACCOUNT'].includes(result.code)) return fieldError('email', result.message)
      if (Array.isArray(result.details) && result.details.some(detail => detail.field === 'email')) {
        return fieldError('email', '이메일을 name@example.com 형식으로 입력해 주세요.')
      }
      setError({ form: result.message })
    })
  }

  const field = (key, label, props, message = error[key]) => (
    <>
      <label className="lbl" htmlFor={'su-' + key}>{label}</label>
      <TextField id={'su-' + key} value={form[key]} onChange={set(key)} invalid={!!message} aria-describedby={message ? 'su-error-' + key : undefined} {...props} />
      {message && <p className="field-err" id={'su-error-' + key}>{message}</p>}
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
        {field('password', '비밀번호', { placeholder: '영문·숫자·특수문자 포함 8~72자', type: 'password', autoComplete: 'new-password' },
          form.password.length > 72 ? '비밀번호는 72자 이하로 입력해 주세요.' : error.password)}
        <PasswordRules value={form.password} showErrors={!!error.password} />
        <div className="sp16" />
        {field('confirm', '비밀번호 확인', { placeholder: '비밀번호를 다시 입력하세요', type: 'password', autoComplete: 'new-password' })}
        {form.confirm && form.password === form.confirm && !error.confirm && <p className="field-ok">✓ 비밀번호가 일치합니다</p>}
        <p className="note" style={{ marginTop: 10 }}>이름은 필수 입력입니다. 이메일은 대소문자를 구분하며 형식·중복 여부와 비밀번호 일치 여부를 확인합니다.</p>
        <div className="col" role="group" aria-label="필수 약관 동의" style={{ gap: 10, marginTop: 22 }}>
          {agreement('age', '[필수] 만 14세 이상입니다.')}
          {agreement('service', '[필수] 서비스 이용약관에 동의합니다.', serviceTerms)}
          {agreement('privacy', '[필수] 개인정보 수집·이용에 동의합니다.', privacyTerms)}
        </div>
        <ErrorNotice error={termsError} retry={reloadTerms} />
        <div className="sp24" />
        {error.form && <p className="field-err" role="alert">{error.form}</p>}
        <Btn type="submit" kind="red" size="lg" block disabled={!ready || pending}>{pending ? '가입 중…' : '회원가입하고 관심사 설정하기'}</Btn>
        {!passwordReady && <p className="note" style={{ marginTop: 8, textAlign: 'center' }}>비밀번호 조건을 모두 만족하고 비밀번호 확인이 일치해야 가입할 수 있습니다.</p>}
        {passwordReady && !agreementReady && <p className="note" style={{ marginTop: 8, textAlign: 'center' }}>필수 항목에 모두 동의해야 가입할 수 있습니다.</p>}
        <div className="sp12" />
        <Btn to="/login" size="lg" block>로그인 화면으로 돌아가기</Btn>
        <p className="note" style={{ marginTop: 16 }}>회원가입 완료 후에만 최초 관심사 등록을 진행합니다.</p>
      </form>
      <Modal open={!!viewing} onClose={() => setViewing(null)} width={680} label={viewing?.title || '약관 내용'}>
        <h2 style={{ fontSize: 22, fontWeight: 800 }}>{viewing?.title}</h2>
        <div style={{ marginTop: 14, border: '1px solid var(--line)', borderRadius: 12, padding: 16, maxHeight: '55vh', overflowY: 'auto' }}>
          <TermsBody body={viewing?.body} />
        </div>
        <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 18 }}><Btn kind="ink" size="md" onClick={() => setViewing(null)}>확인</Btn></div>
      </Modal>
    </AuthFrame>
  )
}
