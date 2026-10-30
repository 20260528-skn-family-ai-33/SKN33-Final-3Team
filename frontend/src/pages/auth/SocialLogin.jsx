// 기존 소셜 로그인 안내·오류 화면을 유지한다. 인증은 백엔드 연결 후 활성화한다.
import { useEffect } from 'react'
import { Btn } from '../../components/ui'
import { PROVIDERS } from '../../auth/oauth'
import { PlainAuthPage } from './AuthFrame'

const big = { height: 56, justifyContent: 'flex-start', padding: '0 22px' }

export function SocialLogin({ provider }) {
  const name = PROVIDERS[provider]?.label || '소셜'
  return (
    <PlainAuthPage eyebrow={`소셜 로그인 · ${name}`} title={`${name} 로그인 준비 중`}
      desc={`${name} 계정 연결 기능은 서버 연동이 완료된 후 제공됩니다.`}>
      <section className="card" style={{ marginTop: 28, background: 'var(--bg)', borderColor: 'transparent' }}>
        <p className="t2">로그인 안내</p>
        <p style={{ marginTop: 14, fontSize: 15, lineHeight: 1.9 }}>현재는 이메일로 가입하고 로그인할 수 있습니다.</p>
      </section>
      <div style={{ marginTop: 20, maxWidth: 340 }}>
        <Btn size="md" style={big} to="/login">로그인 선택으로 돌아가기</Btn>
      </div>
    </PlainAuthPage>
  )
}

export function OAuthCallback({ provider, onDone }) {
  useEffect(() => {
    window.history.replaceState(null, '', `/#/login/error?provider=${provider}&reason=pending`)
    onDone()
    window.dispatchEvent(new HashChangeEvent('hashchange'))
  }, [provider, onDone])
  return <SocialError route={{ query: new URLSearchParams({ provider, reason: 'pending' }) }} />
}

const REASON = {
  cancel: '로그인을 취소했거나 동의하지 않았습니다.',
  state: '로그인 요청 정보가 일치하지 않습니다. 보안을 위해 처음부터 다시 시도해 주세요.',
  expired: '로그인 시간이 만료되었습니다.',
  network: '로그인 서버에 연결하지 못했습니다.',
  server: '인증 서버에서 로그인 정보를 확인하지 못했습니다.',
  blocked: '이용할 수 없는 계정입니다.',
  error: '인증 과정에서 오류가 발생했습니다.',
  pending: '소셜 로그인은 서버 연동 준비 중입니다. 이메일 로그인을 이용해 주세요.',
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
        {provider && <Btn size="md" style={big} to={`/login/${q.get('provider')}`}>{provider.label}로 다시 시도</Btn>}
      </div>
    </PlainAuthPage>
  )
}
