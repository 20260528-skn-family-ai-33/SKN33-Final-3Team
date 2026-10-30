import { test } from 'node:test'
import assert from 'node:assert/strict'
import { amount, percent, mapReport, mapArticle, mapNotification, reportComparison } from '../src/api/adapters.js'
import { registerCountries, countryCode, countryName, registerProducts, productName } from '../src/data/catalog.js'
import { isStrongPassword } from '../src/utils/validate.js'
test('미수집 값과 실제 0을 구분하고 USD를 억 USD로 표시한다', () => {
  assert.equal(amount(null), '—'); assert.equal(amount(0), '0'); assert.equal(amount(15720000000), '157.2')
  assert.equal(percent(null), '—'); assert.equal(percent(0), '0.0%'); assert.equal(percent(-5.3), '-5.3%')
})

test('리포트 비교 방식과 저장된 비교값을 유지하고 예전 문서도 읽는다', () => {
  assert.equal(reportComparison({ compare: 'MOM' }, { mom_pct: 25, yoy_pct: 80 }).growth, 25)
  assert.equal(reportComparison({ compare: 'YOY_PERIOD' }, { yoy_pct: 80 }).growth, 80)
  assert.equal(reportComparison({ compare: 'CUSTOM' }, { comparison_pct: -20 }).growth, -20)
  assert.equal(reportComparison({ compare: 'CUSTOM' }, { yoy_pct: 80 }).growth, null)
  assert.equal(reportComparison({ compare: 'NONE' }, { comparison_pct: 99 }).growth, null)
  assert.equal(reportComparison({ compare: 'MOM' }, { comparison_pct: null, mom_pct: 25 }).growth, null)
  assert.equal(reportComparison({ compare: 'MOM' }, { comparison_pct: 0, previous_amount_usd: 0 }).growth, 0)
  assert.equal(reportComparison({ compare: 'MOM' }, { previous_amount_usd: 0 }).previous, 0)
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
test('기사 출처는 기사 유형과 독립적으로 실제 제공자를 표시한다', () => {
  for (const [source_code, expected] of [
    ['KOTRA_MARKET', 'KOTRA 해외시장뉴스'], ['KOTRA_SHORT_NEWS', 'KOTRA 단신속보'],
    ['HANKYUNG', '한국경제'], ['FNNEWS', '파이낸셜뉴스'],
  ]) {
    for (const is_major_issue of [false, true]) {
      const a = mapArticle({ article_id: '123', source_code, is_major_issue, source_url: 'https://example.com/article' })
      assert.equal(a.source, expected)
      assert.equal(a.kind, is_major_issue ? 'major' : source_code === 'KOTRA_SHORT_NEWS' ? 'short' : 'general')
      assert.equal(a.url, 'https://example.com/article')
    }
  }
})

test('미등록 또는 누락된 기사 출처를 다른 언론사로 대체하지 않는다', () => {
  assert.equal(mapArticle({ source_code: 'NEW_PROVIDER' }).source, 'NEW_PROVIDER')
  for (const source_code of [null, undefined, '']) {
    assert.equal(mapArticle({ source_code }).source, '출처 미제공')
  }
})

test('회원가입 비밀번호 규칙', () => {
  assert.equal(isStrongPassword('Original!123'), true)
  for (const bad of ['12345678', 'abcdefgh', 'abcd1234', 'a1!', 'abcd 123!']) assert.equal(isStrongPassword(bad), false)
})
