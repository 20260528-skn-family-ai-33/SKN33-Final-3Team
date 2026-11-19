// 마이페이지 공통 틀(띠 배너 + 왼쪽 메뉴)과 하위 화면 연결
import { Link } from '../../router'
import { useNotifications } from '../../store'
import { Band } from '../../components/layout'
import bandMypage from '../../assets/band-mypage.jpg'
import ReportCenter from './ReportCenter'
import Subscription from './Subscription'
import NotificationSettings from './NotificationSettings'
import Profile from './Profile'
import Password from './Password'
import Interests from './Interests'
import Inbox from './Inbox'

const MENU = [
  ['profile', '회원정보'],   // 마이페이지 기본 화면(/account)과 같은 첫 메뉴
  ['reports', '리포트 센터'],
  ['settings', '알림설정'],
  ['interests', '관심정보'],
  ['notifications', '알림함'],
]

const PAGES = {
  reports: [ReportCenter, 'reports'],
  subscription: [Subscription, 'reports'],
  settings: [NotificationSettings, 'settings'],
  profile: [Profile, 'profile'],
  password: [Password, 'profile'],
  interests: [Interests, 'interests'],
  notifications: [Inbox, 'notifications'],
}

export default function AccountPage({ route }) {
  const { unread } = useNotifications()
  const section = route.path.split('/')[2] || 'profile'
  const [Page, menu] = PAGES[section] || PAGES.profile
  return (
    <>
      <Band image={bandMypage} imagePosition="center 70%"  title="마이페이지" />
      <main className="page">
        <div className="wrap lay">
          <nav className="side" aria-label="마이페이지 메뉴">
            {MENU.map(([key, label]) => (
              <Link key={key} to={key === 'reports' ? '/account/reports?tab=chat' : `/account/${key}`} className={menu === key ? 'on' : ''} aria-current={menu === key ? 'page' : undefined}>
                {label}{key === 'notifications' && unread > 0 && <span className="n">{unread}</span>}
              </Link>
            ))}
          </nav>
          <div className="col" style={{ gap: 20, minWidth: 0 }}>
            <Page route={route} key={section} />
          </div>
        </div>
      </main>
    </>
  )
}
