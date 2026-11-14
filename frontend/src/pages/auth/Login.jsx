// BS01 로그인
import { useState } from 'react'
import { Link, navigate } from '../../router'
import { useApp } from '../../store'
import { Btn, TextField } from '../../components/ui'
import { isEmail } from '../../utils/validate'
import { usePendingAction } from '../../api/hooks'

import { AuthFrame, CardHead } from './AuthFrame'

export default function Login() {
  const { login } = useApp()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState({})
  const [pending, runOnce] = usePendingAction()

  const submit = async e => {
    e.preventDefault()
    const next = {}
    if (!isEmail(email)) next.email = '이메일을 name@example.com 형식으로 입력해 주세요.'
    if (!password) next.password = '비밀번호를 입력해 주세요.'
    if (Object.keys(next).length) {
      setError(next)
      document.getElementById('login-' + Object.keys(next)[0])?.focus()
      return
    }
    setError({})
    await runOnce(async () => {
      const result = await login(email, password)
      if (!result.ok) return setError({ form: result.code === 'RATE_LIMITED' ? `로그인 시도가 많습니다. ${result.retryAfter || 60}초 후 다시 시도해 주세요.` : result.message || '이메일 또는 비밀번호가 일치하지 않습니다. 로그인 정보를 확인해 주세요.' })
      if (result.account.role === 'admin') navigate('/admin')
      else navigate(result.path || '/')
    })
  }

  return (
    <AuthFrame lead={<>품목·국가별 수출입 순위와 해외 시장 기사를 한곳에서 보고,<br />AI챗봇으로 리포트까지 만들어 보세요.</>}>
      <form onSubmit={submit} noValidate>
        <CardHead  title="환영합니다" desc="로그인하고 무역 분석을 시작해보세요!" />
        <div className="sp32" />
        <label className="lbl" htmlFor="login-email">이메일 (아이디)</label>
        <TextField id="login-email" type="email" autoComplete="username" placeholder="name@example.com" value={email} onChange={setEmail} invalid={!!error.email} />
        {error.email && <p className="field-err">{error.email}</p>}
        <div className="sp20" />
        <label className="lbl" htmlFor="login-password">비밀번호</label>
        <TextField id="login-password" type="password" autoComplete="current-password" placeholder="비밀번호를 입력하세요" icon="lock" value={password} onChange={setPassword} invalid={!!error.password} />
        {error.password && <p className="field-err">{error.password}</p>}
        {error.form && <p className="field-err" role="alert" style={{ marginTop: 12 }}>{error.form}</p>}
        <div className="sp24" />
        <Btn type="submit" kind="red" size="lg" block disabled={pending}>{pending ? '로그인 중…' : '로그인'}</Btn>
        <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', gap: 12, marginTop: 16, fontSize: 14 }}>
          <Link to="/find-id" style={{ color: 'var(--sub)', fontWeight: 700 }}>가입 여부 확인</Link>
          <span style={{ width: 1, height: 12, background: '#CDD1D8' }} />
          <Link to="/find-password" style={{ color: 'var(--sub)', fontWeight: 700 }}>비밀번호 찾기</Link>
          <span style={{ width: 1, height: 12, background: '#CDD1D8' }} />
          <Link to="/signup" style={{ color: 'var(--red-d)', fontWeight: 700 }}>회원가입</Link>
        </div>
        <p className="note" style={{ marginTop: 16, textAlign: 'center' }}>로그인 후 서비스를 이용할 수 있습니다.</p>
        <div style={{ marginTop: 28, background: 'var(--bg)', borderRadius: 12, padding: '14px 16px' }}>
          <p className="note">가입한 이메일의 대소문자를 정확히 입력해 주세요.</p>

        </div>
      </form>
    </AuthFrame>
  )
}
