import { Btn } from '../../components/ui'
import { PlainAuthPage } from './AuthFrame'
const names = { kakao: '카카오', naver: '네이버', google: '구글' }
export function SocialLogin({ provider }) {
  return <PlainAuthPage eyebrow={`소셜 로그인 · ${names[provider] || ''}`} title="소셜 로그인 준비 중" desc="소셜 계정 연결 기능은 서버 설정과 운영 정책 확정 후 제공됩니다."><div style={{ marginTop: 28, maxWidth: 340 }}><Btn size="md" to="/login">로그인 선택으로 돌아가기</Btn></div></PlainAuthPage>
}
export function OAuthCallback({ provider }) { return <SocialLogin provider={provider} /> }
export function SocialError() { return <SocialLogin /> }
