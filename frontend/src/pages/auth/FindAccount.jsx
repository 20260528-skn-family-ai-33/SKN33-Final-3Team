// LG07~LG10 아이디 찾기, LG11~LG13 비밀번호 찾기(임시 비밀번호 메일 발송)
import { useState } from 'react'
import { Link, navigate } from '../../router'
import { useApp } from '../../store'
import { Btn, Radio, TextField } from '../../components/ui'
import Icon from '../../components/Icon'
import { maskEmail, providerLabel } from '../../data/seed'
import { isEmail } from '../../utils/validate'
import { AuthFrame, CardHead, MailPreview, ResultHead } from './AuthFrame'

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

export function FindId({ route }) {
  const { accounts } = useApp()
  const step = route.query.get('step') || 'input'
  const name = route.query.get('name') || '홍길동'
  const matches = accounts.filter(a => a.name === name && a.status !== 'withdrawn')
  const [input, setInput] = useState('')
  const [error, setError] = useState('')
  const [pick, setPick] = useState(0)

  const find = e => {
    e.preventDefault()
    const n = input.trim()
    if (!n) return setError('이름을 입력해 주세요.')
    const found = accounts.some(a => a.name === n && a.status !== 'withdrawn')
    navigate(`/find-id?step=${found ? 'select' : 'none'}&name=${encodeURIComponent(n)}`)
  }

  if (step === 'select' && matches.length) {
    return (
      <AuthFrame>
        <CardHead eyebrow="아이디 찾기" size={30} title="가입된 계정을 찾았어요!" desc={`'${name}' 이름으로 가입된 계정입니다. 아이디를 선택하세요.`} />
        {sp(32)}
        <div className="col" style={{ gap: 10 }} role="radiogroup" aria-label="가입 계정">
          {matches.map((a, i) => (
            <Radio key={a.email} name="find-id" checked={pick === i} onChange={() => setPick(i)}
              style={{ gap: 12, padding: '14px 16px', borderRadius: 12, border: pick === i ? '1.5px solid var(--red)' : '1px solid var(--line)', background: pick === i ? '#FBF4F5' : '#fff' }}>
              <div style={{ flex: 1 }}>
                <p style={{ fontSize: 16, fontWeight: 700 }}>{maskEmail(a.email)}</p>
                <p className="note">가입 {a.joined}</p>
              </div>
              <span className={'tag ' + (a.provider === 'email' ? 'plum' : '')}>{providerLabel[a.provider]}</span>
            </Radio>
          ))}
        </div>
        <p className="note" style={{ marginTop: 16 }}>선택한 이메일로 정보를 보내드릴 예정이에요.</p>
        {sp(24)}
        <Btn kind="red" size="lg" block onClick={() => navigate(`/find-id?step=sent&name=${encodeURIComponent(name)}&pick=${pick}`)}>계정 선택</Btn>
        {sp(12)}
        <Btn to="/find-id" size="lg" block>다시 찾기</Btn>
      </AuthFrame>
    )
  }

  if (step === 'sent' && matches.length) {
    const a = matches[Number(route.query.get('pick')) || 0] || matches[0]
    return (
      <AuthFrame>
        <ResultHead icon={<Icon name="mail" size={26} color="#C8102E" stroke={1.8} />} tone="rose" eyebrow="아이디 찾기" title="메일을 전송했습니다"
          desc={<>선택하신 {maskEmail(a.email)}으로<br />메일을 보내드렸어요!</>} />
        <MailPreview subject="[Borderless Sellers] 가입하신 아이디 안내">
          <p>{a.name}님, 요청하신 아이디를 안내해 드립니다.</p>
          <p style={{ marginTop: 10, padding: '10px 12px', background: 'var(--bg)', borderRadius: 8 }}>아이디(이메일): <b>{a.email}</b><br />로그인 수단: {providerLabel[a.provider]}</p>
          <p style={{ marginTop: 10 }}>비밀번호가 기억나지 않으면 로그인 화면의 비밀번호 찾기를 이용해 주세요. 본인이 요청하지 않았다면 이 메일을 무시하셔도 됩니다.</p>
        </MailPreview>
        {sp(24)}
        <Btn to="/login" kind="red" size="lg" block>로그인하기</Btn>
        {sp(12)}
        <Btn to="/find-password" size="lg" block>비밀번호 찾기</Btn>
      </AuthFrame>
    )
  }

  if (step === 'none' || ((step === 'select' || step === 'sent') && !matches.length)) {
    return (
      <AuthFrame>
        <ResultHead icon={<Icon name="userX" size={26} color="#5C6370" stroke={1.8} />} eyebrow="아이디 찾기"
          title={<>해당 이름으로 가입된<br />회원정보가 없습니다</>} desc={<>입력하신 이름을 다시 확인하거나<br />회원가입을 해 주세요.</>} />
        {sp(32)}
        <Btn to="/signup" kind="red" size="lg" block>회원가입</Btn>
        {sp(12)}
        <Btn to="/find-id" size="lg" block>다시 찾기</Btn>
        {sp(12)}
        <Btn to="/login" size="lg" block>로그인 화면으로 돌아가기</Btn>
      </AuthFrame>
    )
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

function tempPassword() {
  const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789'
  const pick = s => s[Math.floor(Math.random() * s.length)]
  return pick('ABCDEFGHJKLMNPQRSTUVWXYZ') + pick('abcdefghijkmnopqrstuvwxyz') + pick('23456789') + pick('#!@$%') + Array.from({ length: 4 }, () => pick(chars)).join('')
}

export function FindPassword({ route }) {
  const { accounts, setAccounts } = useApp()
  const step = route.query.get('step') || 'input'
  const [form, setForm] = useState({ name: '', email: '' })
  const [error, setError] = useState('')
  const [sent, setSent] = useState(null)

  const submit = e => {
    e.preventDefault()
    if (!form.name.trim() || !isEmail(form.email)) return setError('이름과 이메일(아이디)을 정확히 입력해 주세요.')
    const account = accounts.find(a => a.name === form.name.trim() && a.email.toLowerCase() === form.email.trim().toLowerCase() && a.status !== 'withdrawn')
    if (!account) return navigate('/find-password?step=none')
    if (!account.password) return setError(`${providerLabel[account.provider]}로 가입한 계정은 비밀번호가 없습니다. 로그인 화면의 ${providerLabel[account.provider]} 버튼으로 로그인하세요.`)
    const temp = tempPassword()
    setAccounts(list => list.map(a => (a.email === account.email ? { ...a, password: temp, tempPassword: true } : a)))
    setSent({ name: account.name, email: account.email, temp })
    navigate('/find-password?step=sent')
  }

  if (step === 'sent') {
    const info = sent || { name: '홍길동', email: 'honggd@example.com', temp: 'Bs7#k2Qm' }
    const resend = () => {
      if (!sent) return
      const temp = tempPassword()
      setAccounts(list => list.map(a => (a.email === sent.email ? { ...a, password: temp, tempPassword: true } : a)))
      setSent({ ...sent, temp })
    }
    return (
      <AuthFrame>
        <ResultHead icon={<Icon name="mail" size={26} color="#C8102E" stroke={1.8} />} tone="rose" eyebrow="비밀번호 찾기" title="임시 비밀번호를 보냈어요"
          desc={<>{maskEmail(info.email)}으로 임시 비밀번호를 보냈습니다.<br />로그인한 뒤 마이페이지에서 비밀번호를 꼭 바꿔 주세요.</>} />
        <MailPreview subject="[Borderless Sellers] 임시 비밀번호 안내">
          <p>{info.name}님, 요청하신 임시 비밀번호를 보내 드립니다.</p>
          <p style={{ marginTop: 10, padding: '10px 12px', background: 'var(--bg)', borderRadius: 8 }}>
            임시 비밀번호: <b style={{ fontFamily: 'ui-monospace,Menlo,Consolas,monospace', letterSpacing: 1 }}>{info.temp}</b> {!sent && <span className="note">(예시)</span>}
          </p>
          <p style={{ marginTop: 10 }}><b>임시 비밀번호로 로그인한 뒤, 마이페이지 › 비밀번호 변경에서 새 비밀번호로 바꿔 주세요.</b> 임시 비밀번호는 보안을 위해 계속 사용하지 않는 것이 좋습니다.</p>
          <div style={{ marginTop: 12 }}><Btn to="/account/password" kind="ink">마이페이지에서 비밀번호 변경하기</Btn></div>
          <p className="note" style={{ marginTop: 10 }}>본인이 요청하지 않았다면 바로 로그인해 비밀번호를 변경하고 고객센터로 알려 주세요.</p>
        </MailPreview>
        {sp(24)}
        <Btn to="/login" kind="red" size="lg" block>로그인하기</Btn>
        {sp(12)}
        <Btn size="lg" block onClick={resend}>다시 보내기</Btn>
      </AuthFrame>
    )
  }

  if (step === 'none') {
    return (
      <AuthFrame>
        <ResultHead icon={<Icon name="userX" size={26} color="#5C6370" stroke={1.8} />} eyebrow="비밀번호 찾기"
          title={<>해당 이메일로 가입된<br />회원정보가 없습니다</>} desc={<>입력하신 이름과 이메일을 다시 확인하거나<br />회원가입을 해 주세요.</>} />
        {sp(32)}
        <Btn to="/signup" kind="red" size="lg" block>회원가입</Btn>
        {sp(12)}
        <Btn to="/find-password" size="lg" block>다시 입력하기</Btn>
        {sp(12)}
        <Btn to="/find-id" size="lg" block>아이디 찾기</Btn>
      </AuthFrame>
    )
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
