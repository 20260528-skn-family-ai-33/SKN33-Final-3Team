// LG07~LG10 아이디 찾기, LG11~LG13 비밀번호 찾기(임시 비밀번호 메일 발송)
import { useState } from 'react'
import { Link } from '../../router'
import { api } from '../../api/client'
import { Btn, TextField } from '../../components/ui'
import { isEmail } from '../../utils/validate'
import { AuthFrame, CardHead } from './AuthFrame'

const sp = n => <div className={'sp' + n} />

function BottomLinks({ left }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', gap: 12, marginTop: 18, paddingTop: 18, borderTop: '1px solid var(--line)', fontSize: 14 }}>
      {left}
      <span style={{ width: 1, height: 12, background: '#CDD1D8' }} />
      <Link to="/signup" style={{ color: 'var(--sub)', fontWeight: 700 }}>회원가입</Link>
    </div>
  )
}

export function FindId() {
  const [input, setInput] = useState('')
  const [error, setError] = useState('')

  const find = async e => {
    e.preventDefault()
    const n = input.trim()
    if (!n) return setError('이름을 입력해 주세요.')
    try { await api('/auth/account-recovery/candidates', { method: 'POST', body: { name: n } }) } catch (error) { setError(error.message) }
  }

  return (
    <AuthFrame>
      <form onSubmit={find} noValidate>
        <CardHead eyebrow="아이디 찾기" size={30} title="아이디를 입력해주세요" desc="회원가입 시 입력한 이름을 입력해주세요." />
        {sp(32)}
        <label className="lbl" htmlFor="fi-name">이름</label>
        <TextField id="fi-name" placeholder="예) 홍길동" value={input} onChange={v => { setInput(v); setError('') }} invalid={!!error} />
        {error && <p className="field-err">{error}</p>}
        {sp(16)}{sp(8)}
        <Btn type="submit" kind="red" size="lg" block>아이디 찾기</Btn>
        {sp(12)}
        <Btn to="/login" size="lg" block>로그인 화면으로 돌아가기</Btn>
        <BottomLinks left={<Link to="/find-password" style={{ color: 'var(--sub)', fontWeight: 700 }}>비밀번호 찾기</Link>} />
      </form>
    </AuthFrame>
  )
}

export function FindPassword() {
  const [form, setForm] = useState({ name: '', email: '' })
  const [error, setError] = useState('')

  const submit = async e => {
    e.preventDefault()
    if (!form.name.trim() || !isEmail(form.email)) return setError('이름과 이메일을 확인해 주세요.')
    try { await api('/auth/password-recovery/temporary-password', { method: 'POST', body: { ...form, action: 'ISSUE' } }) } catch (error) { setError(error.message) }
  }

  return (
    <AuthFrame>
      <form onSubmit={submit} noValidate>
        <CardHead eyebrow="비밀번호 찾기" size={30} title="임시 비밀번호를 보내 드려요" desc="가입한 이름과 이메일(아이디)을 입력해 주세요. 두 정보가 모두 확인되면 해당 이메일로 임시 비밀번호를 보내 드립니다." />
        {sp(32)}
        <label className="lbl" htmlFor="fp-name">이름</label>
        <TextField id="fp-name" placeholder="예) 홍길동" value={form.name} onChange={v => { setForm(f => ({ ...f, name: v })); setError('') }} />
        {sp(16)}
        <label className="lbl" htmlFor="fp-email">이메일 (아이디)</label>
        <TextField id="fp-email" type="email" placeholder="name@example.com" value={form.email} onChange={v => { setForm(f => ({ ...f, email: v })); setError('') }} />
        {sp(16)}
        {error ? <p className="field-err" role="alert">{error}</p> : <p className="note">카카오·네이버·구글로 가입한 계정은 비밀번호가 없습니다. 로그인 화면의 해당 소셜 버튼으로 로그인하세요.</p>}
        {sp(24)}
        <Btn type="submit" kind="red" size="lg" block>임시 비밀번호 받기</Btn>
        {sp(12)}
        <Btn to="/login" size="lg" block>로그인 화면으로 돌아가기</Btn>
        <BottomLinks left={<Link to="/find-id" style={{ color: 'var(--sub)', fontWeight: 700 }}>아이디 찾기</Link>} />
      </form>
    </AuthFrame>
  )
}
