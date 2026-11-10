// 머리글·띠 배너·바닥글 등 화면 공통 틀
import { Link } from '../router'
import Icon from './Icon'

const CONTACT_EMAIL = 'BorderlessSellers1205@gmail.com'
const NAV = [
  ['rank', '/rank', '수출입 순위'],
  ['articles', '/articles', '기사'],
  ['analysis', '/analysis', 'AI챗봇'],
]

export function Logo() {
  return <Link to="/" className="logo"><Icon name="logo" />BORDERLESS</Link>
}

/** 로그인 전 화면(로그인·회원가입·찾기·관심사 등록)의 머리글 */
export function AuthHeader() {
  return (
    <header className="hd">
      <div className="wrap">
        <Logo />
        <div className="hd-r"><span className="muted">무역 데이터 분석 · 리포트 플랫폼</span></div>
      </div>
    </header>
  )
}

/** 서비스 머리글. admin=true면 관리자 화면용(관리자 버튼만 표시) */
/** beforeBell: 알림 버튼 왼쪽에 둘 화면별 조작(메인의 전체↔내 관심 전환) */
export function SiteHeader({ active, unread = 0, bellOpen, onBell, isAdmin, admin, onLogout, beforeBell, userName, children }) {
  return (
    <header className="hd">
      <div className="wrap">
        <Logo />
        <nav className="nav" aria-label="주요 메뉴">
          {NAV.map(([key, to, label]) => (
            <Link key={key} to={to} className={active === key ? 'on' : ''} aria-current={active === key ? 'page' : undefined}>{label}</Link>
          ))}
        </nav>
        <div className="hd-r">
          {beforeBell}
          {/* 로그인한 회원 이름. 이름이 없는 계정(예전 회원)은 표시하지 않는다. */}
          {userName && <span className="hd-user" title={`${userName} 님`}><b>{userName}</b> 님</span>}
          <button
            type="button" className={'bell' + (bellOpen ? ' open' : '')} onClick={onBell}
            aria-label={`알림 ${unread}건`} aria-expanded={!!bellOpen} aria-haspopup="dialog"
          >
            <Icon name="bell" size={22} stroke={1.8} />
            {unread > 0 && <b>{unread}</b>}
          </button>
          {!(admin || isAdmin) && <Link className="pill-my" to="/account/profile">마이페이지</Link>}
          {(admin || isAdmin) && <Link className="pill-admin" to="/admin">관리자</Link>}
          {onLogout && (
            <button type="button" className="pill-logout" onClick={onLogout} aria-label="로그아웃">
              <Icon name="logout" size={15} stroke={2} />로그아웃
            </button>
          )}
        </div>
      </div>
      {children}
    </header>
  )
}

/** 페이지 상단 띠 배너. image가 있으면 사진 + 어두운 그라데이션, tone='plum'|'ink'|'lgbg' */
export function Band({ eyebrow, title, lead, image, imagePosition, tone, right, style }) {
  return (
    <section className={'band' + (tone ? ' ' + tone : '')} style={style}>
      {image && <img src={image} alt="" style={{ objectPosition: imagePosition }} />}
      <div className="wrap">
        <div>
          {eyebrow && <p className="eyebrow">{eyebrow}</p>}
          <h1>{title}</h1>
          {lead && <p className="lead">{lead}</p>}
        </div>
        <div>{right}</div>
      </div>
    </section>
  )
}

// 푸터 데이터 출처: 출처 기관 사이트를 새 탭으로 연다.
function SourceLink({ href, children }) {
  return <a href={href} target="_blank" rel="noopener noreferrer" className="ft-link">{children}</a>
}
export function Footer() {
  return (
    <footer className="ft">
      <div className="wrap g ft-info">
        <div>
          <b>BORDERLESS SELLERS</b>
          <p>수출입 통계와 해외시장 기사, AI 분석을 한곳에서 보는 무역 데이터 서비스입니다.</p>
          <p>통계·기사·AI 분석 결과는 참고 자료이며, 수출·거래·투자 권고가 아닙니다.</p>
        </div>
        <div>
          <b>데이터 출처</b>
          <p>수출입 통계 : <SourceLink href="https://tradedata.go.kr">관세청</SourceLink></p>
          <p>국가별 점유율 : <SourceLink href="https://comtradeplus.un.org">UN Comtrade</SourceLink></p>
          <p>환율 : <SourceLink href="https://ecos.bok.or.kr">한국은행 ECOS</SourceLink></p>
          <p>기사 : <SourceLink href="https://dream.kotra.or.kr/kotranews/index.do">KOTRA</SourceLink> · <SourceLink href="https://www.hankyung.com">한국경제</SourceLink> · <SourceLink href="https://www.fnnews.com">파이낸셜뉴스</SourceLink></p>
        </div>
        <div>
          <b>약관 및 문의</b>
          <p><Link to="/terms/service" className="ft-link">이용약관</Link></p>
          <p><Link to="/terms/privacy-policy" className="ft-link" style={{ color: '#fff', fontWeight: 700 }}>개인정보처리방침</Link></p>
          <p><a href={`mailto:${CONTACT_EMAIL}`} className="ft-link">{CONTACT_EMAIL}</a></p>
        </div>
      </div>
      <div className="wrap cp">© 2026 Borderless Sellers. All rights reserved.</div>
    </footer>
  )
}

export function FooterSmall() {
  return (
    <footer className="ft-s">
      <div className="wrap">© 2026 Borderless Sellers · <Link to="/terms/service" style={{ color: 'inherit' }}>이용약관</Link> · <Link to="/terms/privacy-policy" style={{ color: '#fff', fontWeight: 700 }}>개인정보처리방침</Link></div>
    </footer>
  )
}

/** 화면 오른쪽 아래에 떠 있는 AI챗봇 바로가기 */
// disabled면 이유(reason)를 툴팁으로 보여 주고 이동하지 않는다(예: 조회 결과가 확정되기 전).
export function ChatFab({ ask = '', disabled = false, reason = '' }) {
  if (disabled) return <span className="btn btn-dis btn-lg fab" role="link" aria-disabled="true" title={reason} aria-label={`AI챗봇 열기 (${reason})`}><Icon name="chat" size={18} />AI챗봇 열기</span>
  return <Link to={ask ? `/analysis?ask=${encodeURIComponent(ask)}` : '/analysis'} className="btn btn-red btn-lg fab"><Icon name="chat" size={18} />AI챗봇 열기</Link>
}
