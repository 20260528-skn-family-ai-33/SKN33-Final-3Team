// 관리자 영역: 공통 틀(머리글·띠·왼쪽 메뉴)과 화면 선택
import { useApp } from '../store'
import { Link, navigate } from '../router'
import { SiteHeader, FooterSmall } from '../components/layout'
import { Btn } from '../components/ui'
import AdminScreen from './AdminScreen'
import AdminRoles from './AdminRoles'
import { isSuperAdmin } from '../auth/roles'
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

/** BS49 회원 검색: 주소의 검색어(q)로 전체 회원 목록을 거른다. */
function withMemberSearch(screen, query) {
  const q = query.get('q')
  if (q === null) return screen
  const by = query.get('by') || 'all'
  const all = adminScreens.BS40.blocks.find(b => b.type === 'table')
  const needle = q.trim().toLowerCase()
  const rows = all.rows.filter(r => !needle || (by !== 'name' && String(r.cells[0]).toLowerCase().includes(needle)) || (by !== 'id' && String(r.cells[1]).includes(q.trim())))
  const sub = `검색 조건: ${q.trim() || '전체'}`
  return {
    ...screen,
    lead: sub,
    head: { ...screen.head, sub },
    blocks: screen.blocks
      .filter(b => b.type !== 'banner')
      .map(b => (b.type === 'table' ? { ...all, rows } : b.type === 'note' && b.text.startsWith('표시') ? { ...b, text: `표시 ${rows.length}건 · 아이디(이메일)·이름 일부 일치` } : b)),
  }
}

function Forbidden() {
  return (
    <main className="page" style={{ minHeight: 480 }}>
      <div className="wrap">
        <div className="card" style={{ maxWidth: 640, margin: '0 auto', textAlign: 'center' }}>
          <p className="t2">관리자 권한이 필요한 화면입니다</p>
          <p className="muted" style={{ marginTop: 8 }}>관리자 계정으로 다시 로그인해 주세요. (체험: admin@example.com / admin1234!)</p>
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
  const { session, unread, logout } = useApp()
  const [, , slug = 'ops', id] = route.path.split('/')
  const menuKey = adminScreens[id]?.menu || slug
  const menu = ADMIN_MENU.find(m => m[0] === (menuKey === 'data' ? 'inbox' : menuKey)) || ADMIN_MENU[0]
  const isKeyword = menu[0] === 'keywords'
  const screenId = id || menu[2]
  let screen = !isKeyword && adminScreens[screenId]
  if (screen && screenId === 'BS49') screen = withMemberSearch(screen, route.query)
  const roleScreen = ['BS52', 'BS53', 'BS55', 'BS56', 'BS57'].includes(screenId)
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
            <p>{isSuperAdmin(session) ? '최고 관리자' : '운영 관리자'} · 관리자 권한으로 접속</p>
          </nav>
          {roleScreen ? <AdminRoles /> : isKeyword ? <KeywordAdmin route={route} /> : screen ? <div className="col" style={{ gap: 20, minWidth: 0 }}>{screenId === 'BS40' && isSuperAdmin(session) && <AdminRoles />}<AdminScreen key={screenId} screen={screen} query={Object.fromEntries(route.query)} /></div> : (
            <div className="card"><p className="t2">화면을 찾을 수 없습니다</p><p className="muted" style={{ marginTop: 6 }}>{screenId}</p></div>
          )}
        </div>
        <div className="wrap" style={{ marginTop: 24 }}>
          {isKeyword
            ? <p className="note">{keywordFootnote(route).map((l, i) => <span key={i}>{i > 0 && <br />}{l}</span>)}</p>
            : <p className="note">화면 상태 예시 · 실제 서비스 데이터·실행은 연결하지 않았습니다.</p>}
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
