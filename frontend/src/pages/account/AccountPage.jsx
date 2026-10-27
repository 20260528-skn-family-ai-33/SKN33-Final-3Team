// 마이페이지 공통 틀(띠 배너 + 왼쪽 메뉴)과 하위 화면 연결
import { Link } from '../../router'
import { useApp } from '../../store'
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
  ['reports', '리포트 센터'],
  ['settings', '알림설정'],
  ['profile', '회원정보'],
  ['interests', '관심정보'],
  ['notifications', '알림함'],
]

const PAGES = {
  reports: [ReportCenter, 'reports', '리포트 목록에서 열람하거나 생성 당시 저장한 PDF·DOCX를 다운로드합니다.'],
  subscription: [Subscription, 'reports', '리포트 종류별로 목록을 확인하고 저장된 PDF를 다시 다운로드합니다.'],
  settings: [NotificationSettings, 'settings', '받고 싶은 알림과 관심 조건을 관리합니다.'],
  profile: [Profile, 'profile', '이메일 확인 후 비밀번호를 변경하고, 동의 상태 및 회원탈퇴를 관리합니다.'],
  password: [Password, 'profile', '등록 이메일 확인 후 비밀번호를 변경합니다.'],
  interests: [Interests, 'interests', '관심 국가·품목을 수정하고 맞춤 조회와 알림에 적용합니다.'],
  notifications: [Inbox, 'notifications', '알림 유형별 탭에서 목록을 확인하고, 선택한 알림의 상세 내용을 오른쪽에서 확인합니다.'],
}

export default function AccountPage({ route }) {
  const { unread } = useApp()
  const section = route.path.split('/')[2] || 'reports'
  const [Page, menu, lead] = PAGES[section] || PAGES.reports
  const leadText = section === 'reports' && (route.query.get('tab') || 'chat') === 'chat' ? '리포트 종류별로 목록을 확인하고 저장된 PDF를 다시 다운로드합니다.' : lead
  return (
    <>
      <Band image={bandMypage} imagePosition="center 70%" eyebrow="MY PAGE" title="마이페이지" lead={leadText} />
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
