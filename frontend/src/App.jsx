// 화면 라우팅과 공통 틀(머리글·바닥글)을 고른다. 경로 ↔ 화면설계서 번호는 MOCKUP_MAP.md 참고.
import { Suspense, lazy, useCallback, useEffect, useState } from 'react'
import { navigate, useRoute, withQuery } from './router'
import { useApp, useNotifications, useToast } from './store'
import { AuthHeader, Footer, FooterSmall, SiteHeader } from './components/layout'
import AlertPopover from './components/AlertPopover'
import Login from './pages/auth/Login'
import { Signup, SocialSignup } from './pages/auth/Signup'
import { OAuthCallback, SocialError, SocialLogin } from './pages/auth/SocialLogin'
import { callbackProvider } from './auth/oauth'
import { FindId, FindPassword } from './pages/auth/FindAccount'
import Onboarding from './pages/auth/Onboarding'
import Dashboard from './pages/home/Dashboard'
import Rank from './pages/market/Rank'
import Articles from './pages/market/Articles'
import Analysis from './pages/analysis/Analysis'
import ReportView from './pages/analysis/ReportView'
import AccountPage from './pages/account/AccountPage'

// 관리자 화면은 사용자가 열 때만 불러온다(번들 분리).
const AdminApp = lazy(() => import('./admin/AdminApp'))

const PUBLIC = {
  '/login': Login,
  '/signup': Signup,
  '/signup/social': SocialSignup,
  '/login/kakao': () => <SocialLogin provider="kakao" />,
  '/login/naver': () => <SocialLogin provider="naver" />,
  '/login/google': () => <SocialLogin provider="google" />,
  '/login/error': SocialError,
  '/find-id': FindId,
  '/find-password': FindPassword,
}

const SITE = {
  '/': ['', Dashboard],
  '/rank': ['rank', Rank],
  '/articles': ['articles', Articles],
  '/analysis': ['analysis', Analysis],
  '/report': ['', ReportView],
}

function UserShell({ active, route, children }) {
  const { session, logout } = useApp()
  const { unread } = useNotifications()
  const isAdmin = session?.role === 'admin'
  const open = !isAdmin && route.query.get('alerts') === 'open'
  const toggle = () => navigate(withQuery(route, { alerts: open ? null : 'open' }), { replace: true })
  return (
    <>
      <SiteHeader active={active} unread={unread} bellOpen={open} onBell={isAdmin ? () => navigate('/admin/inbox/BS33') : toggle} isAdmin={isAdmin} onLogout={logout}>
        {open && <AlertPopover onClose={toggle} />}
      </SiteHeader>
      <div className="app-body" id="main">{children}</div>
      <Footer isAdmin={isAdmin} />
    </>
  )
}

function Toast() {
  const { toast, setToast } = useToast()
  if (!toast) return null
  return (
    <div className="toast" role="status">
      {toast}
      <button type="button" aria-label="알림 닫기" onClick={() => setToast('')}>×</button>
    </div>
  )
}

export default function App() {
  const route = useRoute()
  const { session, loading, loadError, refresh } = useApp()
  const { path } = route
  // 카카오·네이버·구글 로그인에서 /oauth/{provider}?code=… 로 돌아온 경우 먼저 처리한다.
  const [oauthProvider, setOauthProvider] = useState(callbackProvider)
  const finishOAuth = useCallback(() => setOauthProvider(null), [])
  const isPublic = PUBLIC[path]
  const needsLogin = !loading && !loadError && !oauthProvider && !isPublic && !session

  const adminAccountRoute = session?.role === 'admin' && (path === '/account' || path.startsWith('/account/'))
  useEffect(() => {
    if (adminAccountRoute) navigate('/admin', { replace: true })
  }, [adminAccountRoute])

  useEffect(() => {
    if (needsLogin) navigate('/login', { replace: true })
  }, [needsLogin])

  let content
  if (loading || loadError) {
    content = <><AuthHeader /><main className="page"><div className="wrap card"><p role={loadError ? 'alert' : 'status'}>{loadError?.message || '로그인 정보를 불러오는 중…'}</p>{loadError && <button className="btn" onClick={refresh}>다시 불러오기</button>}</div></main><FooterSmall /></>
  } else if (oauthProvider) {
    content = <><AuthHeader /><div className="app-body" id="main"><OAuthCallback provider={oauthProvider} onDone={finishOAuth} /></div><FooterSmall /></>
  } else if (isPublic) {
    const Page = PUBLIC[path]
    content = <><AuthHeader /><div className="app-body" id="main"><Page route={route} /></div><FooterSmall /></>
  } else if (needsLogin || adminAccountRoute) {
    content = null
  } else if (path === '/onboarding') {
    content = <><AuthHeader /><div className="app-body" id="main"><Onboarding route={route} /></div><FooterSmall /></>
  } else if (path === '/admin' || path.startsWith('/admin/')) {
    content = <Suspense fallback={<p className="muted" style={{ padding: 40 }}>관리자 화면을 불러오는 중…</p>}><AdminApp route={route} /></Suspense>
  } else if (path.startsWith('/account')) {
    content = <UserShell route={route}><AccountPage route={route} /></UserShell>
  } else if (SITE[path]) {
    const [active, Page] = SITE[path]
    content = <UserShell active={active} route={route}><Page key={path} route={route} /></UserShell>
  } else {
    content = (
      <UserShell route={route}>
        <main className="page" style={{ minHeight: 480 }}>
          <div className="wrap card" style={{ maxWidth: 640, textAlign: 'center' }}>
            <h1 className="t1">페이지를 찾을 수 없습니다</h1>
            <p className="muted" style={{ marginTop: 8 }}>주소를 다시 확인해 주세요.</p>
            <a className="btn btn-red" href="#/" style={{ marginTop: 20 }}>메인으로</a>
          </div>
        </main>
      </UserShell>
    )
  }

  return (
    <>
      <a href="#main" className="skip-link" onClick={e => { e.preventDefault(); document.getElementById('main')?.focus() }}>본문으로 이동</a>
      {content}
      <Toast />
    </>
  )
}
