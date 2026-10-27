// 개발용 소셜 로그인 토큰 교환(server/oauthDevApi.js) 단위 테스트 — 실제 네트워크 대신 가짜 fetch 사용
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { exchange } from '../server/oauthDevApi.js'

const env = {
  VITE_KAKAO_REST_API_KEY: 'kakao-rest', KAKAO_CLIENT_SECRET: 'kakao-secret',
  VITE_NAVER_CLIENT_ID: 'naver-id', NAVER_CLIENT_SECRET: 'naver-secret',
  VITE_GOOGLE_CLIENT_ID: 'google-id', GOOGLE_CLIENT_SECRET: 'google-secret',
}
const fakeFetch = routes => {
  const calls = []
  const fn = async (url, init = {}) => {
    calls.push({ url, init })
    const key = Object.keys(routes).find(k => url.startsWith(k))
    const [status, body] = routes[key] || [404, { error: 'not_found' }]
    return { ok: status < 400, status, text: async () => JSON.stringify(body) }
  }
  fn.calls = calls
  return fn
}

test('카카오: code → 토큰 → 사용자 정보', async () => {
  const f = fakeFetch({
    'https://kauth.kakao.com/oauth/token': [200, { access_token: 'AT' }],
    'https://kapi.kakao.com/v2/user/me': [200, { id: 123, kakao_account: { email: 'a@kakao.com', profile: { nickname: '카카오유저' } } }],
  })
  const user = await exchange('kakao', { code: 'C', redirectUri: 'http://localhost:5173/oauth/kakao' }, env, f)
  assert.deepEqual(user, { providerId: '123', email: 'a@kakao.com', name: '카카오유저' })
  const body = String(f.calls[0].init.body)
  assert.match(body, /client_id=kakao-rest/)
  assert.match(body, /client_secret=kakao-secret/)
  assert.match(body, /redirect_uri=http%3A%2F%2Flocalhost%3A5173%2Foauth%2Fkakao/)
  assert.equal(f.calls[1].init.headers.Authorization, 'Bearer AT')
})

test('네이버: Client Secret과 state로 토큰 교환', async () => {
  const f = fakeFetch({
    'https://nid.naver.com/oauth2.0/token': [200, { access_token: 'NT' }],
    'https://openapi.naver.com/v1/nid/me': [200, { resultcode: '00', response: { id: 'nv1', email: 'b@naver.com', name: '네이버유저' } }],
  })
  const user = await exchange('naver', { code: 'C', state: 'S' }, env, f)
  assert.deepEqual(user, { providerId: 'nv1', email: 'b@naver.com', name: '네이버유저' })
  assert.match(String(f.calls[0].init.body), /state=S/)
})

test('구글: userinfo로 이름·이메일', async () => {
  const f = fakeFetch({
    'https://oauth2.googleapis.com/token': [200, { access_token: 'GT', id_token: 'x' }],
    'https://openidconnect.googleapis.com/v1/userinfo': [200, { sub: 'g1', email: 'c@gmail.com', email_verified: true, name: '구글유저' }],
  })
  assert.deepEqual(await exchange('google', { code: 'C', redirectUri: 'r' }, env, f), { providerId: 'g1', email: 'c@gmail.com', name: '구글유저' })
})

test('토큰 오류와 키 누락은 메시지로 알려 준다', async () => {
  const f = fakeFetch({ 'https://kauth.kakao.com/oauth/token': [400, { error: 'invalid_grant', error_description: 'authorization code not found' }] })
  await assert.rejects(exchange('kakao', { code: 'C', redirectUri: 'r' }, env, f), /authorization code not found/)
  await assert.rejects(exchange('naver', { code: 'C' }, {}, f), /NAVER_CLIENT_SECRET/)
})
