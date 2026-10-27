// 개발용 소셜 로그인 토큰 교환 API (Vite 개발·미리보기 서버에서만 동작)
//   POST /api/auth/oauth/{kakao|naver|google}  body: { code, state, redirectUri }
//   → 200 { user: { providerId, email, name } }   /   4xx·5xx { message }
// Client Secret은 이 파일(서버 쪽)에서만 읽는다. 백엔드(FastAPI 등)를 만들면 같은 주소·응답으로 옮기면 된다.

const form = obj => new URLSearchParams(Object.fromEntries(Object.entries(obj).filter(([, v]) => v !== undefined && v !== '')))

async function readJson(res, what) {
  const text = await res.text()
  let body = {}
  try { body = JSON.parse(text) } catch { body = { raw: text } }
  if (!res.ok || body.error) {
    const detail = body.error_description || body.error || body.msg || body.message || text.slice(0, 200)
    throw Object.assign(new Error(`${what} 실패: ${detail}`), { status: res.status >= 400 ? res.status : 400 })
  }
  return body
}

/** provider별 code → 회원 정보. fetchImpl은 테스트에서 바꿔 끼운다. */
export async function exchange(provider, { code, state, redirectUri }, env, fetchImpl = fetch) {
  if (!code) throw Object.assign(new Error('code가 없습니다.'), { status: 400 })
  if (provider === 'kakao') {
    if (!env.VITE_KAKAO_REST_API_KEY) throw Object.assign(new Error('.env에 VITE_KAKAO_REST_API_KEY가 없습니다.'), { status: 500 })
    const token = await readJson(await fetchImpl('https://kauth.kakao.com/oauth/token', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded;charset=utf-8' },
      body: form({ grant_type: 'authorization_code', client_id: env.VITE_KAKAO_REST_API_KEY, client_secret: env.KAKAO_CLIENT_SECRET, redirect_uri: redirectUri, code }),
    }), '카카오 토큰 발급')
    const me = await readJson(await fetchImpl('https://kapi.kakao.com/v2/user/me', { headers: { Authorization: `Bearer ${token.access_token}` } }), '카카오 사용자 정보 조회')
    const account = me.kakao_account || {}
    return { providerId: String(me.id), email: account.email || '', name: account.profile?.nickname || me.properties?.nickname || '' }
  }
  if (provider === 'naver') {
    if (!env.VITE_NAVER_CLIENT_ID || !env.NAVER_CLIENT_SECRET) throw Object.assign(new Error('.env에 VITE_NAVER_CLIENT_ID 또는 NAVER_CLIENT_SECRET이 없습니다.'), { status: 500 })
    const token = await readJson(await fetchImpl('https://nid.naver.com/oauth2.0/token', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded;charset=utf-8' },
      body: form({ grant_type: 'authorization_code', client_id: env.VITE_NAVER_CLIENT_ID, client_secret: env.NAVER_CLIENT_SECRET, code, state }),
    }), '네이버 토큰 발급')
    const me = await readJson(await fetchImpl('https://openapi.naver.com/v1/nid/me', { headers: { Authorization: `Bearer ${token.access_token}` } }), '네이버 사용자 정보 조회')
    const r = me.response || {}
    return { providerId: String(r.id), email: r.email || '', name: r.name || r.nickname || '' }
  }
  if (provider === 'google') {
    if (!env.VITE_GOOGLE_CLIENT_ID || !env.GOOGLE_CLIENT_SECRET) throw Object.assign(new Error('.env에 VITE_GOOGLE_CLIENT_ID 또는 GOOGLE_CLIENT_SECRET이 없습니다.'), { status: 500 })
    const token = await readJson(await fetchImpl('https://oauth2.googleapis.com/token', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: form({ grant_type: 'authorization_code', client_id: env.VITE_GOOGLE_CLIENT_ID, client_secret: env.GOOGLE_CLIENT_SECRET, redirect_uri: redirectUri, code }),
    }), '구글 토큰 발급')
    const me = await readJson(await fetchImpl('https://openidconnect.googleapis.com/v1/userinfo', { headers: { Authorization: `Bearer ${token.access_token}` } }), '구글 사용자 정보 조회')
    return { providerId: String(me.sub), email: me.email_verified === false ? '' : me.email || '', name: me.name || '' }
  }
  throw Object.assign(new Error('지원하지 않는 로그인 방식입니다.'), { status: 404 })
}

function middleware(env) {
  return async (req, res, next) => {
    const m = req.url?.match(/^\/api\/auth\/oauth\/([a-z]+)\/?(\?.*)?$/)
    if (!m) return next()
    const send = (status, body) => {
      res.statusCode = status
      res.setHeader('Content-Type', 'application/json; charset=utf-8')
      res.end(JSON.stringify(body))
    }
    if (req.method !== 'POST') return send(405, { message: 'POST만 지원합니다.' })
    let raw = ''
    for await (const chunk of req) raw += chunk
    try {
      const body = JSON.parse(raw || '{}')
      const user = await exchange(m[1], body, env)
      send(200, { user })
    } catch (e) {
      const message = e.message === 'fetch failed' ? '로그인 제공자 서버에 연결하지 못했습니다. 인터넷 연결을 확인해 주세요.' : e.message
      console.warn(`[oauth] ${m[1]}: ${message}`)
      send(e.status || 502, { message })
    }
  }
}

/** Vite 플러그인: npm run dev / npm run preview 에서 /api/auth/oauth/* 를 처리한다. */
export function oauthDevApi(env) {
  return {
    name: 'borderless-oauth-dev-api',
    configureServer(server) { server.middlewares.use(middleware(env)) },
    configurePreviewServer(server) { server.middlewares.use(middleware(env)) },
  }
}
