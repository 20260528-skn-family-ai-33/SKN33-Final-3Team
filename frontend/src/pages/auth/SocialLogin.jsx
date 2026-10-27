// 소셜 로그인 화면
// LG02~LG04: /login/{provider} → 실제 카카오·네이버·구글 로그인 페이지로 이동(키가 없으면 설정 안내)
// OAuthCallback: /oauth/{provider}?code=… 로 돌아왔을 때 서버에서 회원 정보를 받아 기존/신규 회원으로 나눈다.
// LG06: 취소·실패 안내
import { useEffect, useState } from 'react'
import { useApp } from '../../store'
import { Btn } from '../../components/ui'
import { PROVIDERS, completeSocialLogin, isConfigured, redirectUri, startSocialLogin } from '../../auth/oauth'
import { PlainAuthPage } from './AuthFrame'

const big = { height: 56, justifyContent: 'flex-start', padding: '0 22px' }
const CONSOLE = {
  kakao: ['카카오 Developers(developers.kakao.com) → 내 애플리케이션 → 앱 키의 REST API 키', '카카오 로그인 활성화 · Redirect URI 등록 · 동의항목(닉네임, 카카오계정 이메일)'],
  naver: ['네이버 Developers(developers.naver.com) → Application → 네이버 로그인 API 등록 → Client ID·Secret', '서비스 URL·Callback URL 등록 · 제공 정보(이름, 이메일) · 개발 중에는 등록한 테스터 아이디만 로그인'],
  google: ['Google Cloud Console → API 및 서비스 → 사용자 인증 정보 → OAuth 클라이언트 ID(웹 애플리케이션)', 'OAuth 동의 화면(테스트 사용자 추가) · 승인된 리디렉션 URI 등록'],
}

/** 주소를 hash 경로로 바꾸고(콜백 경로 제거) 라우터에 알린다. */
function leaveTo(hashPath) {
  window.history.replaceState(null, '', '/#' + hashPath)
  window.dispatchEvent(new HashChangeEvent('hashchange'))
}

export function SocialLogin({ provider }) {
  const name = PROVIDERS[provider].label
  const ready = isConfigured(provider)
  useEffect(() => {
    if (ready) startSocialLogin(provider)
  }, [ready, provider])

  if (ready) {
    return (
      <PlainAuthPage eyebrow={`소셜 로그인 · ${name}`} title={`${name} 로그인 페이지로 이동하고 있어요`}
        desc={`${name}가 제공하는 로그인·동의 화면으로 이동합니다. 로그인을 마치면 Borderless Sellers로 자동으로 돌아옵니다.`}>
        <div style={{ marginTop: 28, display: 'flex', gap: 12 }}>
          <Btn kind="red" size="md" style={big} onClick={() => startSocialLogin(provider)}>이동하지 않으면 다시 시도</Btn>
          <Btn size="md" style={big} to="/login">로그인 선택으로 돌아가기</Btn>
        </div>
      </PlainAuthPage>
    )
  }
  const [step1, step2] = CONSOLE[provider]
  return (
    <PlainAuthPage eyebrow={`소셜 로그인 · ${name}`} title={`${name} 로그인 키가 설정되지 않았어요`}
      desc={`${name} 로그인 페이지로 이동하려면 애플리케이션 키가 필요합니다. 아래 순서로 등록한 뒤 개발 서버를 다시 실행하세요.`}>
      <section className="card" style={{ marginTop: 28, background: 'var(--bg)', borderColor: 'transparent' }}>
        <p className="t2">설정 방법</p>
        <ol style={{ margin: '14px 0 0', paddingLeft: 20, fontSize: 15, lineHeight: 1.9 }}>
          <li>{step1}</li>
          <li>{step2}</li>
          <li>Redirect(Callback) URI: <code style={{ background: '#fff', padding: '2px 8px', borderRadius: 6 }}>{redirectUri(provider)}</code></li>
          <li>프로젝트 폴더의 <b>.env.example</b>을 <b>.env</b>로 복사해 <b>{PROVIDERS[provider].envName}</b>{provider !== 'kakao' && ' 와 Client Secret'} 값을 넣기</li>
          <li>터미널에서 <b>Control + C</b> 후 <b>npm run dev</b> 다시 실행</li>
        </ol>
        <p className="note" style={{ marginTop: 12 }}>Client Secret은 .env의 서버 전용 값(VITE_ 없는 이름)으로만 넣습니다. 브라우저 코드에는 포함되지 않습니다.</p>
      </section>
      <div style={{ marginTop: 20, maxWidth: 340 }}>
        <Btn size="md" style={big} to="/login">로그인 선택으로 돌아가기</Btn>
      </div>
    </PlainAuthPage>
  )
}

/** /oauth/{provider} 복귀 처리 */
export function OAuthCallback({ provider, onDone }) {
  const { socialSignIn, profile } = useApp()
  const [message, setMessage] = useState('')
  const name = PROVIDERS[provider].label
  useEffect(() => {
    let alive = true
    completeSocialLogin(provider).then(result => {
      if (!alive) return
      if (!result.ok) {
        onDone()
        leaveTo(`/login/error?provider=${provider}&reason=${result.reason}&message=${encodeURIComponent(result.message || '')}`)
        return
      }
      const signIn = socialSignIn(result.user)
      if (!signIn.ok) {
        onDone()
        leaveTo(`/login/error?provider=${provider}&reason=blocked&message=${encodeURIComponent(signIn.message)}`)
        return
      }
      if (signIn.existing) {
        setMessage('로그인했습니다. 메인으로 이동합니다.')
        onDone()
        leaveTo(signIn.account.role === 'admin' ? '/admin' : signIn.account.email === profile.email && profile.home === 'interest' ? '/?view=interest' : '/')
        return
      }
      sessionStorage.setItem('bs2.socialPending', JSON.stringify(result.user))
      onDone()
      leaveTo('/signup/social')
    })
    return () => { alive = false }
  }, [provider, socialSignIn, onDone, profile])
  return (
    <PlainAuthPage eyebrow={`소셜 로그인 · ${name}`} title={`${name} 로그인 정보를 확인하고 있어요`} desc={message || '잠시만 기다려 주세요. 인증 결과에 따라 메인 또는 가입 마무리 화면으로 이동합니다.'}>
      <div className="progress" style={{ marginTop: 28, maxWidth: 420 }}><i style={{ width: '60%' }} /></div>
    </PlainAuthPage>
  )
}

const REASON = {
  cancel: '로그인을 취소했거나 동의하지 않았습니다.',
  state: '로그인 요청 정보가 일치하지 않습니다. 보안을 위해 처음부터 다시 시도해 주세요.',
  expired: '로그인 시간이 만료되었습니다.',
  network: '로그인 서버에 연결하지 못했습니다.',
  server: '인증 서버에서 로그인 정보를 확인하지 못했습니다.',
  blocked: '이용할 수 없는 계정입니다.',
  error: '인증 과정에서 오류가 발생했습니다.',
}

export function SocialError({ route }) {
  const q = route.query
  const provider = PROVIDERS[q.get('provider')]
  const detail = q.get('message')
  return (
    <PlainAuthPage
      eyebrow={`소셜 로그인${provider ? ' · ' + provider.label : ''}`} title="로그인을 완료하지 못했어요"
      desc="인증 취소·시간 만료·검증 실패 시 다시 시도해 주세요. 서비스 이용 제한은 별도 안내하며 가입 또는 재로그인으로 우회할 수 없습니다."
    >
      {q.get('reason') && (
        <div style={{ marginTop: 20, background: 'var(--rose)', border: '1px solid #F1A9B4', color: 'var(--red-d)', borderRadius: 12, padding: '14px 18px', fontSize: 14, fontWeight: 700 }}>
          {REASON[q.get('reason')] || REASON.error}
          {detail && <p className="note" style={{ color: 'var(--red-d)', marginTop: 4 }}>{detail}</p>}
        </div>
      )}
      <div style={{ marginTop: 28, display: 'flex', gap: 12, maxWidth: 700 }}>
        <Btn kind="red" size="md" style={big} to="/login">로그인 다시 선택</Btn>
        {provider && <Btn size="md" style={big} onClick={() => startSocialLogin(q.get('provider'))}>{provider.label}로 다시 시도</Btn>}
      </div>
    </PlainAuthPage>
  )
}
