// 카카오·네이버·구글 소셜 로그인(OAuth 2.0 Authorization Code 방식)
//
// 1) 로그인 버튼 → 각 회사 로그인 페이지로 이동(authorizeUrl)
// 2) 로그인 후 {origin}/oauth/{provider}?code=...&state=... 로 돌아옴
// 3) code를 서버(POST {VITE_AUTH_API_BASE}/auth/oauth/{provider})로 보내 토큰 교환·회원 정보 조회
//    - 개발 중에는 vite.config.js의 oauthDevApi 플러그인이 이 API를 대신한다(.env의 Client Secret 사용).
//    - 배포 시에는 FastAPI 등 백엔드가 같은 주소·같은 응답 형식으로 구현한다(README 참고).
// Client Secret은 절대 프론트엔드(VITE_ 변수)에 넣지 않는다.

const env = import.meta.env

export const PROVIDERS = {
  kakao: {
    label: '카카오',
    clientId: env.VITE_KAKAO_REST_API_KEY,
    envName: 'VITE_KAKAO_REST_API_KEY',
    authorize: 'https://kauth.kakao.com/oauth/authorize',
    extra: {},
  },
  naver: {
    label: '네이버',
    clientId: env.VITE_NAVER_CLIENT_ID,
    envName: 'VITE_NAVER_CLIENT_ID',
    authorize: 'https://nid.naver.com/oauth2.0/authorize',
    extra: {},
  },
  google: {
    label: '구글',
    clientId: env.VITE_GOOGLE_CLIENT_ID,
    envName: 'VITE_GOOGLE_CLIENT_ID',
    authorize: 'https://accounts.google.com/o/oauth2/v2/auth',
    extra: { scope: 'openid email profile', prompt: 'select_account' },
  },
}

const STATE_KEY = 'bs2.oauth'
const API_BASE = (env.VITE_AUTH_API_BASE || '/api').replace(/\/$/, '')

export const redirectUri = provider => `${env.VITE_OAUTH_REDIRECT_ORIGIN || window.location.origin}/oauth/${provider}`

export const isConfigured = provider => !!PROVIDERS[provider]?.clientId

function randomState() {
  const bytes = new Uint8Array(16)
  crypto.getRandomValues(bytes)
  return Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('')
}

/** 로그인 페이지 주소를 만든다(테스트용으로 분리). */
export function authorizeUrl(provider, state) {
  const p = PROVIDERS[provider]
  const params = new URLSearchParams({ response_type: 'code', client_id: p.clientId, redirect_uri: redirectUri(provider), state, ...p.extra })
  return `${p.authorize}?${params}`
}

/** 소셜 로그인 시작: 각 회사 로그인 페이지로 이동한다. 키가 없으면 false. */
export function startSocialLogin(provider) {
  if (!isConfigured(provider)) return false
  const state = randomState()
  sessionStorage.setItem(STATE_KEY, JSON.stringify({ provider, state, at: Date.now() }))
  window.location.assign(authorizeUrl(provider, state))
  return true
}

/** 현재 주소가 소셜 로그인 복귀 주소면 provider를 돌려준다. */
export function callbackProvider() {
  const m = window.location.pathname.match(/^\/oauth\/(kakao|naver|google)\/?$/)
  return m ? m[1] : null
}

const handled = new Map()

/**
 * 복귀 주소의 code를 서버로 보내 회원 정보를 받는다.
 * 결과: { ok: true, user: { provider, providerId, email, name } } | { ok: false, reason, message }
 */
export function completeSocialLogin(provider) {
  const q = new URLSearchParams(window.location.search)
  const code = q.get('code')
  // StrictMode·새로고침으로 같은 code를 두 번 보내지 않도록 한 번만 처리한다.
  if (code && handled.has(code)) return handled.get(code)
  const run = (async () => {
    if (q.get('error')) {
      const cancelled = ['access_denied', 'consent_required', 'user_cancel'].includes(q.get('error'))
      return { ok: false, reason: cancelled ? 'cancel' : 'error', message: q.get('error_description') || q.get('error') }
    }
    let saved = null
    try { saved = JSON.parse(sessionStorage.getItem(STATE_KEY) || 'null') } catch { saved = null }
    sessionStorage.removeItem(STATE_KEY)
    if (!code) return { ok: false, reason: 'error', message: '인증 코드가 없습니다.' }
    if (!saved || saved.provider !== provider || saved.state !== q.get('state')) {
      return { ok: false, reason: 'state', message: '로그인 요청 정보가 일치하지 않습니다. 처음부터 다시 시도해 주세요.' }
    }
    if (Date.now() - saved.at > 10 * 60 * 1000) return { ok: false, reason: 'expired', message: '로그인 시간이 만료되었습니다.' }
    try {
      const res = await fetch(`${API_BASE}/auth/oauth/${provider}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ code, state: saved.state, redirectUri: redirectUri(provider) }),
      })
      const body = await res.json().catch(() => ({}))
      if (!res.ok) return { ok: false, reason: 'server', message: body.message || `로그인 서버 오류(${res.status})` }
      return { ok: true, user: { provider, ...body.user }, token: body.accessToken || null }
    } catch {
      return { ok: false, reason: 'network', message: '로그인 서버에 연결하지 못했습니다. 개발 서버(npm run dev) 또는 백엔드가 실행 중인지 확인해 주세요.' }
    }
  })()
  if (code) handled.set(code, run)
  return run
}
