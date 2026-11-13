// 회원가입 완료 안내. 최초 관심사 등록을 마치거나 건너뛴 직후에 한 번 보여 주고, 여기서 메인으로 들어간다.
import { useApp } from '../../store'
import { Btn } from '../../components/ui'
import Icon from '../../components/Icon'
import { AuthFrame } from './AuthFrame'

const GUIDE = [
  ['search', '수출입 순위', '국가·품목·기간별 수출입 실적과 순위를 조회해요.'],
  ['file', '기사', '무역·경제 기사와 KOTRA 단신속보·주요이슈를 모아 봐요.'],
  ['chat', 'AI 챗봇', '말로 물어보고, 답변을 그대로 리포트로 저장해요.'],
]

export default function Welcome({ route }) {
  const { session, profile } = useApp()
  const hasInterest = profile.products.length > 0 || profile.countries.length > 0
  // 관심사를 등록했으면 내 관심 메인으로, 건너뛰었으면 전체 메인으로 시작한다.
  const interest = route.query.get('view') === 'interest' && hasInterest
  return (
    <AuthFrame>
      <div style={{ textAlign: 'center' }}>
        <span className="welcome-check" aria-hidden="true"><Icon name="check" size={30} color="#fff" stroke={2.6} /></span>
        <p style={{ marginTop: 18, fontSize: 14, fontWeight: 700, color: 'var(--red-d)' }}>회원가입 완료</p>
        <h2 className="t1" style={{ marginTop: 8, fontSize: 28 }}>{session?.name ? `${session.name} 님, 환영합니다!` : '환영합니다!'}</h2>
        <p className="muted" style={{ marginTop: 10, fontSize: 15, lineHeight: 1.7 }}>
          가입이 완료되었습니다.<br />이제 Borderless의 무역 분석을 시작해 보세요.<br />
          {interest ? '등록한 관심 품목·국가 기준으로 메인 화면을 준비했어요.' : '관심 품목·국가는 마이페이지에서 언제든 등록할 수 있어요.'}
        </p>
      </div>
      <ul className="welcome-guide">
        {GUIDE.map(([icon, title, text]) => (
          <li key={title}>
            <span className="welcome-guide-icon"><Icon name={icon} size={18} stroke={1.9} /></span>
            <span><b>{title}</b><span>{text}</span></span>
          </li>
        ))}
      </ul>
      <Btn kind="red" size="lg" iconRight="arrowRight" to={interest ? '/?view=interest' : '/'} block style={{ marginTop: 24 }}>지금 시작하기</Btn>
      {!interest && <Btn size="md" to="/account/interests" block style={{ marginTop: 10 }}>관심 품목·국가 등록하기</Btn>}
    </AuthFrame>
  )
}
