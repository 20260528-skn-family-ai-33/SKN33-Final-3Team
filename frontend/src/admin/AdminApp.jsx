// 관리자 영역: 공통 틀(머리글·띠·왼쪽 메뉴)과 화면 선택
import { useApp, useNotifications } from '../store'
import { Link, navigate } from '../router'
import { SiteHeader, FooterSmall } from '../components/layout'
import { Btn } from '../components/ui'
import AdminScreen from './AdminScreen'
import { useServerScreen } from './useServerScreen'
import ServerDetail from './ServerDetail'
import { ErrorNotice, Pagination } from '../api/hooks'
import { adminScreens } from './screens'
import KeywordAdmin from './keywords/KeywordAdmin'
import { keywordFootnote, keywordLead } from './keywords/keywordData'

const ADMIN_MENU = [
  ['ops', '운영 현황', 'BS32'],
  ['inbox', '알림함', 'BS33'],
  ['members', '회원·권한', 'BS40'],
  ['keywords', '키워드·분류 체계', 'BS89'],
  ['history', '처리 이력·운영 설정', 'BS156'],
]

function Forbidden() {
  return (
    <main className="page" style={{ minHeight: 480 }}>
      <div className="wrap">
        <div className="card" style={{ maxWidth: 640, margin: '0 auto', textAlign: 'center' }}>
          <p className="t2">관리자 권한이 필요한 화면입니다</p>
          <p className="muted" style={{ marginTop: 8 }}>관리자 계정으로 다시 로그인해 주세요.</p>
          <div style={{ display: 'flex', gap: 10, justifyContent: 'center', marginTop: 20 }}>
            <Btn size="md" to="/">메인으로</Btn>
            <Btn kind="red" size="md" to="/login">다시 로그인</Btn>
          </div>
        </div>
      </div>
    </main>
  )
}

export default function AdminApp({ route }) {
  const { session, logout } = useApp()
  const { unread } = useNotifications()
  const [, , slug = 'ops', id] = route.path.split('/')
  const menuKey = adminScreens[id]?.menu || slug
  const menu = ADMIN_MENU.find(m => m[0] === (menuKey === 'data' ? 'inbox' : menuKey)) || ADMIN_MENU[0]
  const isKeyword = menu[0] === 'keywords'
  const screenId = id || menu[2]
  const binding = useServerScreen(!isKeyword ? adminScreens[screenId] : null, screenId, route.query)
  const screen = binding.screen
  const lead = isKeyword ? keywordLead(route) : screen?.lead || '관리자 업무 화면입니다.'

  let body
  if (session?.role !== 'admin') body = <Forbidden />
  else {
    body = (
      <main className="page">
        <div className="wrap lay">
          <nav className="side" aria-label="관리자 메뉴">
            {ADMIN_MENU.map(([key, label, first]) => (
              <Link key={key} to={key === 'keywords' ? '/admin/keywords' : `/admin/${key}/${first}`} className={menu[0] === key ? 'on' : ''} aria-current={menu[0] === key ? 'page' : undefined}>{label}</Link>
            ))}
            <p>관리자 · 관리자 권한으로 접속</p>
          </nav>
          {isKeyword ? <KeywordAdmin route={route} /> : screen ? <div className="col" style={{ gap: 20, minWidth: 0 }}>
            <ErrorNotice error={binding.result.error} retry={binding.result.reload} />
            {binding.result.loading && <p role="status">불러오는 중…</p>}
            {binding.user && <ServerDetail type="user" id={binding.user} key={binding.user} />}
            {binding.article && <ServerDetail type="article" id={binding.article} key={binding.article} />}
            <AdminScreen key={screenId} screen={screen} query={Object.fromEntries(route.query)} />
            {binding.result.data?.page && <Pagination page={Number(route.query.get('page')) || 1} info={binding.result.data.page} onChange={page => { const q = new URLSearchParams(route.query); q.set('page', page); navigate(`${route.path}?${q}`) }} />}
          </div> : <div className="card"><p className="t2">화면을 찾을 수 없습니다</p></div>}

        </div>
        <div className="wrap" style={{ marginTop: 24 }}>
          {isKeyword
            ? <p className="note">{keywordFootnote(route).map((l, i) => <span key={i}>{i > 0 && <br />}{l}</span>)}</p>
            : <p className="note">서버 응답 기준 · 준비 중인 기능은 변경을 실행하지 않습니다.</p>}
        </div>
      </main>
    )
  }

  return (
    <>
      <SiteHeader admin unread={unread} onBell={() => navigate('/admin/inbox/BS33')} onLogout={session ? logout : undefined} />
      <section className="band ink">
        <div className="wrap">
          <div>
            <p className="eyebrow">ADMIN</p>
            <h1>관리자 페이지</h1>
            <p className="lead">{lead}</p>
          </div>
          <div />
        </div>
      </section>
      <div className="app-body" id="main">{body}</div>
      <FooterSmall />
    </>
  )
}
