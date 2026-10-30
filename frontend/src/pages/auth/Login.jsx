// BS01 로그인
import { useState } from 'react'
import { Link, navigate } from '../../router'
import { useApp } from '../../store'
import { Btn, TextField } from '../../components/ui'
import { isEmail } from '../../utils/validate'

import { AuthFrame, CardHead } from './AuthFrame'

const SOCIAL = [
  ['kakao', 'K', '카카오', { background: '#FEE500', color: '#191919' }],
  ['naver', 'N', '네이버', { background: '#03C75A', color: '#fff' }],
  ['google', 'G', '구글', { background: '#fff', border: '1px solid #D5D9E0', color: '#3C4043' }],
]

export default function Login() {
  const { login } = useApp()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState({})

  const submit = async e => {
    e.preventDefault()
    const next = {}
    if (!isEmail(email)) next.email = '이메일 형식을 확인해 주세요.'
    if (!password) next.password = '비밀번호를 입력해 주세요.'
    if (Object.keys(next).length) return setError(next)
    const result = await login(email, password)
    if (!result.ok) return setError({ form: result.message || '이메일 또는 비밀번호가 일치하지 않습니다. 로그인 정보를 확인해 주세요.' })
    // 재로그인: 관심사 등록을 건너뛰고 저장된 메인 설정으로 이동한다.
    if (result.account.role === 'admin') navigate('/admin')
    else navigate(result.path || '/')
  }

  return (
    <AuthFrame lead={<>품목·국가별 수출입 순위와 해외 시장 기사를 한곳에서 보고,<br />AI 분석으로 리포트까지 만들어 보세요.</>}>
      <form onSubmit={submit} noValidate>
        <CardHead eyebrow="로그인" title="다시 오신 걸 환영해요" desc="이메일 또는 소셜 계정으로 로그인하고 무역 분석을 시작하세요." />
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
        <Btn type="submit" kind="red" size="lg" block>로그인</Btn>
        <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', gap: 12, marginTop: 16, fontSize: 14 }}>
          <Link to="/find-id" style={{ color: 'var(--sub)', fontWeight: 700 }}>아이디 찾기</Link>
          <span style={{ width: 1, height: 12, background: '#CDD1D8' }} />
          <Link to="/find-password" style={{ color: 'var(--sub)', fontWeight: 700 }}>비밀번호 찾기</Link>
        </div>
        <div style={{ marginTop: 22 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <span style={{ flex: 1, height: 1, background: 'var(--line)' }} />
            <span className="note" style={{ fontWeight: 700 }}>또는 소셜 계정으로 로그인</span>
            <span style={{ flex: 1, height: 1, background: 'var(--line)' }} />
          </div>
          <div style={{ display: 'flex', justifyContent: 'center', gap: 26, marginTop: 16 }}>
            {SOCIAL.map(([key, letter, label, style]) => (
              <button key={key} type="button" className="reset" aria-label={`${label}로 로그인`} onClick={() => navigate(`/login/${key}`)}
                style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6, color: 'var(--ink)' }}>
                <span style={{ width: 52, height: 52, borderRadius: 26, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontSize: 19, fontWeight: 800, ...style }}>{letter}</span>
                <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--sub)' }}>{label}</span>
              </button>
            ))}
          </div>
        </div>
        <p className="note" style={{ marginTop: 16 }}>로그인 후 서비스를 이용할 수 있습니다.</p>
        <p style={{ marginTop: 10 }}>
          <Link to="/signup" style={{ fontSize: 14, fontWeight: 700, color: 'var(--red-d)', textDecoration: 'underline', textUnderlineOffset: 3 }}>아직 계정이 없으신가요?</Link>
        </p>
        <div style={{ marginTop: 28, background: 'var(--bg)', borderRadius: 12, padding: '14px 16px' }}>
          <p className="note">가입한 이메일 계정으로 로그인하세요. 관리자 계정은 운영 담당자가 발급합니다.</p>
        </div>
      </form>
    </AuthFrame>
  )
}
