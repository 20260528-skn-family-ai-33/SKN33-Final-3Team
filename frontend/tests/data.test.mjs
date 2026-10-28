import { test } from 'node:test'
import assert from 'node:assert/strict'
import { amount, percent, mapReport, mapArticle, mapNotification } from '../src/api/adapters.js'
import { registerCountries, countryCode, countryName, registerProducts, productName } from '../src/data/catalog.js'
import { isStrongPassword } from '../src/utils/validate.js'
test('미수집 값과 실제 0을 구분하고 USD를 억 USD로 표시한다', () => {
  assert.equal(amount(null), '—'); assert.equal(amount(0), '0'); assert.equal(amount(15720000000), '157.2')
  assert.equal(percent(null), '—'); assert.equal(percent(0), '0.0%'); assert.equal(percent(-5.3), '-5.3%')
})
test('서버 국가 코드와 화면 국가명은 왕복 변환된다', () => {
  registerCountries([{ country_code: 'US', name_ko: '미국' }, { country_code: 'CN', name_ko: '중국' }])
  assert.equal(countryCode('미국'), 'US'); assert.equal(countryName('US'), '미국')
  assert.equal(countryName('XX'), 'XX')
  registerProducts([{ hs_code: '330499', name_ko: '기초화장품' }])
  assert.equal(productName('330499'), '기초화장품')
})
test('큰 ID를 숫자로 바꾸지 않고 서버 리포트 상태를 유지한다', () => {
  const id = '9007199254740997'
  for (const [status, expected] of [['READY','ready'], ['FAILED','failed'], ['RUNNING','generating'], ['DELAYED','delayed']]) {
    const r = mapReport({ report_id: id, report_type: 'RPT_MONTHLY', status })
    assert.equal(r.id, id); assert.equal(r.kind, 'monthly'); assert.equal(r.status, expected)
  }
})
test('기사·알림의 실제 연결 대상과 읽음 상태를 보존한다', () => {
  const a = mapArticle({ article_id: '123', country_codes: ['US'], source_code: 'KOTRA_SHORT_NEWS', hs_items: [{ hs_code: '330499' }] })
  assert.equal(a.country, '미국'); assert.equal(a.kind, 'short'); assert.deepEqual(a.tags, ['330499'])
  const n = mapNotification({ user_alert_id: '45', event_type: 'REPORT', target_type: 'REPORT', target_id: '9007199254740997', read_at: null })
  assert.equal(n.read, false); assert.equal(n.reportId, '9007199254740997')
})
test('회원가입 비밀번호 규칙', () => {
  assert.equal(isStrongPassword('Original!123'), true)
  for (const bad of ['12345678', 'abcdefgh', 'abcd1234', 'a1!', 'abcd 123!']) assert.equal(isStrongPassword(bad), false)
})
