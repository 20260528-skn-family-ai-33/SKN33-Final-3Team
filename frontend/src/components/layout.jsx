// 머리글·띠 배너·바닥글 등 화면 공통 틀
import { Link } from '../router'
import Icon from './Icon'

const NAV = [
  ['rank', '/rank', '수출입 순위'],
  ['articles', '/articles', '기사'],
  ['analysis', '/analysis', 'AI분석'],
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
export function SiteHeader({ active, unread = 0, bellOpen, onBell, isAdmin, admin, onLogout, children }) {
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

export function Footer({ isAdmin = false }) {
  return (
    <footer className="ft">
      <div className="wrap g">
        <div>
          <b>서비스</b>
          <p><Link to="/rank" className="ft-link">수출입 순위</Link></p>
          <p><Link to="/articles" className="ft-link">기사</Link></p>
          <p><Link to="/analysis" className="ft-link">AI분석</Link>{!isAdmin && <> · <Link to="/account/reports" className="ft-link">리포트 센터</Link></>}</p>
        </div>
        {!isAdmin && <div>
          <b>내 정보</b>
          <p><Link to="/account/interests" className="ft-link">관심정보</Link></p>
          <p><Link to="/account/settings" className="ft-link">알림설정</Link></p>
          <p><Link to="/account/notifications" className="ft-link">알림함</Link></p>
        </div>}
        <div>
          <b>데이터</b>
          <p>수출입 통계 · 관세청</p>
          <p>환율 · 한국은행 ECOS</p>
          <p>기사 · KOTRA 해외시장뉴스</p>
        </div>
        <div>
          <b>BORDERLESS SELLERS</b>
          <p>이용약관 · <span style={{ color: '#fff', fontWeight: 700 }}>개인정보처리방침</span></p>
          <p>화면의 수치·기사는 목업 예시입니다.</p>
        </div>
      </div>
      <div className="wrap cp">© 2026 Borderless Sellers</div>
    </footer>
  )
}

export function FooterSmall() {
  return (
    <footer className="ft-s">
      <div className="wrap">© 2026 Borderless Sellers · 이용약관 · <b style={{ color: '#fff' }}>개인정보처리방침</b> · 화면의 수치·기사는 목업 예시입니다.</div>
    </footer>
  )
}

/** 화면 오른쪽 아래에 떠 있는 AI 분석 바로가기 */
export function ChatFab({ ask = '' }) {
  return <Link to={ask ? `/analysis?ask=${encodeURIComponent(ask)}` : '/analysis'} className="btn btn-red btn-lg fab"><Icon name="chat" size={18} />AI 분석 채팅 열기</Link>
}
