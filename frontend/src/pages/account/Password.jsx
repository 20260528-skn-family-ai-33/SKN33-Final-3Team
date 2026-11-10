// BS25 비밀번호 변경(회원정보에서 등록 이메일 확인 후 진입)
import { useEffect, useRef, useState } from 'react'
import { navigate } from '../../router'
import { useApp } from '../../store'
import { Btn, TextField } from '../../components/ui'
import Icon from '../../components/Icon'
import { PASSWORD_HINT, isStrongPassword } from '../../utils/validate'
import PasswordRules from '../../components/PasswordRules'
import { usePendingAction } from '../../api/hooks'

export default function Password() {
  const { session, changePassword, notify } = useApp()
  const [verified, setVerified] = useState(() => sessionStorage.getItem('bs2.pwVerified') === session?.email)
  const [email, setEmail] = useState('')
  const [form, setForm] = useState({ current: '', next: '', confirm: '' })
  const [error, setError] = useState({})
  const currentInput = useRef(null)
  const [saving, runOnce] = usePendingAction()
  useEffect(() => {
    const value = currentInput.current?.value
    if (value !== undefined) setForm(previous => previous.current === value ? previous : { ...previous, current: value })
  }, [verified, form.next, form.confirm])
  const set = key => v => { setForm(f => ({ ...f, [key]: v })); setError(e => ({ ...e, [key]: '' })) }

  const verify = () => {
    if (email.trim() !== (session?.email || '')) return setError({ email: '등록된 이메일과 일치하지 않습니다.' })
    sessionStorage.setItem('bs2.pwVerified', session.email)
    setVerified(true)
  }
  const save = async event => {
    event.preventDefault()
    const current = new FormData(event.currentTarget).get('current') || ''
    const next = {}
    if (!current) next.current = '현재 비밀번호를 입력해 주세요.'
    if (!isStrongPassword(form.next)) next.next = PASSWORD_HINT
    else if (form.next === current) next.next = '현재 비밀번호와 다른 비밀번호를 입력해 주세요.'
    if (form.next !== form.confirm) next.confirm = '새 비밀번호와 일치하지 않습니다.'
    if (Object.keys(next).length) return setError(next)
    await runOnce(async () => {
      setError({})
      try {
        await changePassword(current, form.next)
      } catch (err) {
        setError(err.code === 'CURRENT_PASSWORD_INVALID'
          ? { current: '현재 비밀번호가 올바르지 않습니다. 다시 입력해 주세요.' }
          : { form: err.message })
        return
      }
      sessionStorage.removeItem('bs2.pwVerified')
      notify('비밀번호를 변경했습니다. 다시 로그인해 주세요.')
      navigate('/login')
    })
  }

  if (!verified) {
    return (
      <section className="card">
        <h2 className="t1" style={{ fontSize: 24 }}>비밀번호 변경</h2>
        <p className="muted" style={{ marginTop: 6 }}>비밀번호를 바꾸기 전에 등록된 이메일을 확인합니다.</p>
        <label className="lbl" htmlFor="pw-email" style={{ marginTop: 18 }}>이메일</label>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, maxWidth: 620 }}>
          <TextField id="pw-email" style={{ flex: '1 1 200px' }} type="email" placeholder="등록된 이메일을 입력하세요" icon="mail" value={email} onChange={v => { setEmail(v); setError({}) }} onEnter={verify} invalid={!!error.email} />
          <Btn kind="ink" size="md" style={{ height: 50, borderRadius: 12 }} onClick={verify}>이메일 확인</Btn>
        </div>
        {error.email && <p className="field-err" role="alert">{error.email}</p>}
      </section>
    )
  }

  const match = form.confirm && form.next === form.confirm
  const ready = !!form.current && isStrongPassword(form.next) && form.next === form.confirm
  const field = (key, label, hint) => (
    <>
      <label className="lbl" htmlFor={'pw-' + key}>{label}</label>
      <TextField id={'pw-' + key} name={key} required type="password" icon="lock" inputRef={key === 'current' ? currentInput : undefined}
        onAnimationStart={key === 'current' ? e => set('current')(e.currentTarget.value) : undefined} value={form[key]} defaultValue={key === 'current' ? '' : undefined} onChange={set(key)} invalid={!!error[key]} autoComplete={key === 'current' ? 'current-password' : 'new-password'} placeholder={key === 'current' ? '현재 비밀번호' : key === 'next' ? '새 비밀번호' : '새 비밀번호 다시 입력'} />
      {error[key] ? <p className="field-err">{error[key]}</p> : hint}
    </>
  )
  return (
    <form className="card password-change" onSubmit={save}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <h2 className="t1" style={{ fontSize: 24 }}>비밀번호 변경</h2>
        <span className="tag ok"><Icon name="check" size={13} color="#127A4B" stroke={2.4} style={{ marginRight: 4 }} />이메일 확인 완료</span>
      </div>
      <div style={{ maxWidth: 620, marginTop: 22 }}>
        <label className="lbl" htmlFor="pw-account">변경할 계정</label>
        <input id="pw-account" className="input" name="username" autoComplete="username" readOnly value={session.email} />
        {field('current', '현재 비밀번호')}
        <div className="sp20" />
        {field('next', '변경할 비밀번호')}
        <PasswordRules value={form.next} showErrors={!!error.next} />
        <div className="sp16" />
        {field('confirm', '새 비밀번호 확인', match ? <p className="field-ok">✓ 새 비밀번호와 일치합니다</p> : form.confirm && <p className="field-err">새 비밀번호와 일치하지 않습니다.</p>)}
      </div>
      <div style={{ marginTop: 20, background: 'var(--bg)', borderRadius: 12, padding: '14px 16px' }}>
        <p className="note">현재 비밀번호를 확인하고 새 비밀번호와 확인값이 일치하면 변경합니다. 입력값은 마스킹하며, 오류 시 해당 입력란에 재입력을 안내합니다.</p>
      </div>
      {!ready && <p className="note" style={{ marginTop: 14, textAlign: 'right' }}>현재 비밀번호를 입력하고, 새 비밀번호가 조건을 모두 만족하며 확인값과 일치해야 저장할 수 있습니다.</p>}
      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, marginTop: 22 }}>
        <Btn size="md" to="/account/profile">취소</Btn>
        <Btn kind="red" size="md" type="submit" disabled={!ready || saving}>{saving ? '저장 중…' : '변경 저장'}</Btn>
      </div>
      {error.form && <p className="field-err" role="alert">{error.form}</p>}
    </form>
  )
}
