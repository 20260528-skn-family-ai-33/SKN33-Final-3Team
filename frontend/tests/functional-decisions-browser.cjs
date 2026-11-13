// Isolated browser regressions: every API response is synthetic.
const assert = require('node:assert/strict')
const path = require('node:path')
const fs = require('node:fs')
const { chromium } = require('playwright')

;(async () => {
  const { createServer } = await import('vite')
  const server = await createServer({ root: path.resolve(__dirname, '..'), server: { host: '127.0.0.1', port: 0, strictPort: false, proxy: {} } })
  await server.listen()
  let browser
  try {
    browser = await chromium.launch({ headless: true })
    const page = await browser.newPage()
    page.setDefaultTimeout(10000)
    const errors = [], writes = [], searches = []
    const screenshots = path.resolve(__dirname, '../../tmp/red-highlight-browser')
    fs.mkdirSync(screenshots, { recursive: true })
    page.on('pageerror', error => errors.push(error.message))
    let scope = { hs_items: [], country_codes: [], landing_page: 'GLOBAL', version: 'v1' }
    let subscriptions = []
    let read = false
    let failNotificationTerms = false
    let failInterestSave = false
    const user = { user_id: '1', name: '검수', email: 'Review@Example.org', role: 'USER', terms_consents: [
      { terms_type: 'SERVICE', terms_version: 'TERMS_SERVICE_V1', acted_at: '2026-11-09' },
      { terms_type: 'PRIVACY', terms_version: 'TERMS_PRIVACY_V1', acted_at: '2026-11-09' },
    ] }
    const product = { hs_code: '010129', hs_ver: 'HS2022', depth: 6, name_ko: '말' }
    await page.route('**/api/v1/**', async route => {
      const request = route.request(), url = new URL(request.url()), p = url.pathname.replace('/api/v1', '')
      const reply = json => route.fulfill({ json })
      if (!['GET', 'HEAD'].includes(request.method())) writes.push({ p, body: request.postDataJSON() })
      if (p === '/users/me') return reply({ user, csrf_token: 'test' })
      if (p === '/auth/signup') return reply({ user, csrf_token: 'test' })
      if (p === '/users/me/interests') {
        if (request.method() === 'PUT' && failInterestSave) {
          failInterestSave = false
          return route.fulfill({ status: 503, json: { code: 'TEST_UNAVAILABLE', message: '검수용 저장 실패' } })
        }
        if (request.method() === 'PUT') scope = { ...scope, ...request.postDataJSON(), version: 'v2' }
        return reply({ interests: scope })
      }
      if (p === '/users/me/subscriptions') {
        if (request.method() === 'PUT') {
          const data = request.postDataJSON()
          subscriptions = data.period_codes.map(period_code => ({ enabled: true, period_code, formats: data.formats }))
        }
        return reply({ version: String(subscriptions.length), items: subscriptions })
      }
      if (p === '/config/options') return reply({ report_periods: ['WEEKLY', 'MONTHLY', 'QUARTERLY'].map(option_code => ({ option_code })) })
      if (p === '/reference/countries') return reply({ items: [{ country_code: 'US', name_ko: '미국', region_id: 1 }] })
      if (p === '/reference/regions') return reply({ items: [{ region_id: 1, parent_id: null, name_ko: '아메리카', country_codes: ['US'] }] })
      if (p === '/reference/hs-codes') {
        searches.push(url.searchParams.get('q'))
        const items = url.searchParams.get('depth') === '6' && url.searchParams.get('q') === '말' ? [product] : []
        return reply({ items, match_type: items.length ? 'EXACT' : 'NONE' })
      }
      if (p === '/reference/hs-codes/0101/children') return reply({ items: [product] })
      if (p === '/users/me/notification-settings') return reply({ settings: { notifications_enabled: false } })
      if (p === '/users/me/consents' && request.method() === 'POST') {
        const data = request.postDataJSON()
        return reply({ consent: { terms_type: 'NOTIFICATION', terms_version: data.terms_version, action: data.action,
          acted_at: '2026-11-10', notifications_enabled: data.action === 'AGREE' && !!data.enable_notifications } })
      }
      if (p === '/terms/notification' && failNotificationTerms) return route.fulfill({ status: 503, json: { code: 'TEST_UNAVAILABLE', message: '검수용 약관 조회 실패' } })
      if (p === '/terms/notification') return reply({ terms: { version: 'v1', body: '검수 약관' } })
      if (p === '/terms/service') return reply({ terms: { version: 'TERMS_SERVICE_V1', title: '서비스 이용약관', body: '검수 서비스 약관' } })
      if (p === '/terms/privacy') return reply({ terms: { version: 'TERMS_PRIVACY_V1', title: '개인정보 수집·이용 동의', body: '검수 개인정보 약관' } })
      if (p === '/notifications/unread-count') return reply({ count: read ? 0 : 1 })
      if (p === '/notifications') return reply({ items: [{ user_alert_id: '1', event_type: 'MAJOR_ISSUE', title: '검수 알림', message: '검수 본문', read_at: read ? '2026-11-09' : null, created_at: '2026-11-09' }], page: { has_next: false } })
      if (p.includes('/notifications/') && request.method() === 'PATCH') { read = true; return route.fulfill({ status: 204 }) }
      if (p === '/reports' || p === '/chat/sessions') return reply({ items: [], page: { has_next: false } })
      throw new Error(`Unexpected API request: ${request.method()} ${p}`)
    })
    await page.route('**/decisions-harness', route => route.fulfill({ contentType: 'text/html', body: `
      <div id="root"></div><script type="module">
      import RefreshRuntime from '/@react-refresh'; RefreshRuntime.injectIntoGlobalHook(window);
      window.$RefreshReg$=()=>{}; window.$RefreshSig$=()=>type=>type;
      window.__vite_plugin_react_preamble_installed__=true;
      await import('/tests/functional-decisions-harness.jsx');</script>` }))
    const base = `http://127.0.0.1:${server.httpServer.address().port}/decisions-harness`
    await page.goto(base + '#/login')
    await page.getByRole('link', { name: '회원가입', exact: true }).waitFor()
    assert.equal(await page.getByRole('link', { name: '회원가입', exact: true }).count(), 1)
    await page.getByText(/구분 적용 전에 가입한 계정은 이메일을 모두 소문자/).waitFor()

    await page.goto(base + '#/onboarding')
    await page.getByRole('combobox', { name: '품목 검색' }).fill('말')
    await page.getByRole('option').filter({ hasText: '말' }).click()
    assert.ok(searches.includes('말'))
    await page.getByRole('button', { name: '건너뛰기', exact: true }).click()
    await page.getByRole('dialog').waitFor()
    await page.screenshot({ path: path.join(screenshots, 'skip-confirmation.png'), fullPage: true })
    await page.getByRole('button', { name: '취소', exact: true }).click()
    assert.equal(writes.filter(w => w.p === '/users/me/interests').length, 0)
    assert.ok((await page.locator('body').innerText()).includes('말'))
    await page.getByRole('button', { name: '건너뛰기', exact: true }).click()
    await page.getByRole('button', { name: '저장하지 않고 건너뛰기' }).click()
    await page.getByText('메인 화면', { exact: true }).waitFor()
    assert.deepEqual(writes.find(w => w.p === '/users/me/interests').body.hs_items, [])
    console.log('PASS one-character search and skip confirmation preserve/cancel/discard selection')

    await page.evaluate(() => sessionStorage.setItem('bs.onboarding.1', JSON.stringify({ items: [], countries: [], welcome: true })))
    await page.goto(base + '#/onboarding?step=2')
    await page.getByText('미국', { exact: true }).click()
    assert.equal(await page.getByRole('checkbox', { name: '미국' }).isChecked(), true)
    await page.getByRole('button', { name: '건너뛰기', exact: true }).click()
    await page.getByRole('dialog').waitFor()
    await page.getByRole('button', { name: '저장하지 않고 건너뛰기' }).click()
    // 회원가입 직후에는 알림 문구 대신 가입 완료 안내 화면(#/welcome)으로 이동한다.
    await page.waitForFunction(() => location.hash === '#/welcome')
    assert.equal(await page.evaluate(() => sessionStorage.getItem('bs.onboarding.1')), null)
    console.log('PASS country selection prompts before skip and signup welcome is shown')

    scope = { ...scope, country_codes: ['US'] }
    await page.goto(base + '#/account/subscription')
    await page.reload()
    const pdf = page.getByRole('checkbox', { name: /PDF/ }), docx = page.getByRole('checkbox', { name: /DOCX/ })
    assert.equal(await pdf.isChecked(), false)
    assert.equal(await docx.isChecked(), false)
    await page.screenshot({ path: path.join(screenshots, 'subscription.png'), fullPage: true })
    await page.getByRole('button', { name: '주기 전체선택', exact: true }).click()
    assert.equal(await page.getByRole('button', { name: '저장', exact: true }).isDisabled(), true)
    await page.locator('label').filter({ has: docx }).click()
    await page.getByRole('button', { name: '주기 전체해제', exact: true }).click()
    assert.equal(await docx.isChecked(), true)
    await page.getByRole('button', { name: '주기 전체선택', exact: true }).click()
    await page.getByRole('button', { name: '저장', exact: true }).click()
    await page.waitForURL(/#\/account\/reports\?tab=weekly$/)
    assert.deepEqual(writes.find(w => w.p === '/users/me/subscriptions').body.formats, ['DOCX'])
    await page.goto(base + '#/account/subscription')
    assert.equal(await docx.isChecked(), true)
    assert.equal(await pdf.isChecked(), false)
    console.log('PASS new subscription requires explicit format; cycle controls and reload preserve chosen format')

    await page.goto(base + '#/account')
    await page.getByRole('heading', { name: '비밀번호 변경', exact: true }).waitFor()
    await page.goto(base + '#/account/profile')
    await page.getByRole('heading', { name: '비밀번호 변경', exact: true }).waitFor()
    assert.equal(await page.getByText('2026-11-09 동의', { exact: true }).count(), 2)
    // 알림 수신 동의(선택): 회원정보에서 체크하면 현재 알림 약관에 동의(알림 켜짐), 해제하면 철회(알림 꺼짐)
    const notifyRow = page.locator('.between', { hasText: '알림 수신 동의' }).filter({ hasText: '선택' })
    await notifyRow.getByText('동의 안 함').waitFor()
    await notifyRow.locator('label.opt').click()
    await notifyRow.getByText('동의함').waitFor()
    assert.deepEqual(writes.filter(w => w.p === '/users/me/consents').map(w => [w.body.action, w.body.terms_version, w.body.enable_notifications]),
      [['AGREE', 'v1', true]])
    await notifyRow.locator('label.opt').click()
    await notifyRow.getByText('동의 안 함').waitFor()
    assert.deepEqual(writes.filter(w => w.p === '/users/me/consents').map(w => [w.body.action, w.body.enable_notifications]),
      [['AGREE', true], ['REVOKE', false]])
    // 알림 약관 조회가 실패하면 '동의 안 함'이 아니라 '불러오지 못함'과 다시 불러오기를 보이고, 성공하면 동의 여부를 보인다
    failNotificationTerms = true
    await page.goto(base + '#/account/settings')
    await page.goto(base + '#/account/profile')
    await notifyRow.getByText('불러오지 못함').waitFor()
    assert.equal(await notifyRow.getByText('동의 안 함').count(), 0)
    assert.equal(await notifyRow.locator('label.opt').count(), 0)
    failNotificationTerms = false
    await page.getByRole('alert').filter({ hasText: '검수용 약관 조회 실패' }).getByRole('button', { name: '다시 불러오기' }).click()
    await notifyRow.getByText('동의 안 함').waitFor()
    await page.goto(base + '#/account/notifications')
    await page.getByText('선택한 알림이 없습니다.').waitFor()
    assert.equal(writes.filter(w => w.p.includes('/notifications/')).length, 0)
    await page.getByRole('link', { name: /^주요 이슈/ }).click()
    assert.equal(writes.filter(w => w.p.includes('/notifications/')).length, 0)
    const readResponse = page.waitForResponse(response => response.url().endsWith('/notifications/1/read'))
    await page.getByRole('button').filter({ hasText: '검수 알림' }).click()
    await readResponse
    await page.waitForFunction(() => document.body.textContent.includes('알림 상세'))
    assert.equal(read, true)
    console.log('PASS account entry opens profile and inbox reads only the selected notification')

    await page.goto(base + '#/account/profile')
    await page.getByLabel('이메일', { exact: true }).fill('review@example.org')
    await page.getByRole('button', { name: '이메일 확인', exact: true }).click()
    await page.getByRole('alert').filter({ hasText: '등록된 이메일과 일치하지 않습니다' }).waitFor()
    await page.getByLabel('이메일', { exact: true }).fill(' Review@Example.org ')
    await page.getByRole('button', { name: '이메일 확인', exact: true }).click()
    await page.waitForURL(/#\/account\/password$/)
    await page.evaluate(() => sessionStorage.removeItem('bs2.pwVerified'))
    await page.reload()
    await page.getByLabel('이메일', { exact: true }).fill('Review@example.org')
    await page.getByRole('button', { name: '이메일 확인', exact: true }).click()
    await page.getByRole('alert').filter({ hasText: '등록된 이메일과 일치하지 않습니다' }).waitFor()
    console.log('PASS both password entry routes require case-exact email')

    failInterestSave = true
    await page.goto(base + '#/onboarding?step=2')
    await page.getByRole('button', { name: '건너뛰기', exact: true }).click()
    await page.getByRole('dialog').getByText(/기존에 저장한 관심정보는 유지/).waitFor()
    await page.getByRole('button', { name: '저장하지 않고 건너뛰기' }).click()
    await page.getByRole('status').filter({ hasText: '검수용 저장 실패' }).waitFor()
    assert.equal(await page.getByRole('dialog').isVisible(), true)
    assert.equal(await page.getByRole('checkbox', { name: '미국' }).isChecked(), true)
    assert.deepEqual(await page.evaluate(() => JSON.parse(sessionStorage.getItem('bs.onboarding.1')).countries), ['미국'])
    await page.getByRole('button', { name: '취소', exact: true }).click()
    assert.equal(await page.getByRole('checkbox', { name: '미국' }).isChecked(), true)
    console.log('PASS failed skip keeps dialog, selection, and draft for retry')

    scope = { ...scope, country_codes: [], version: 'empty' }
    await page.evaluate(() => sessionStorage.removeItem('bs.onboarding.1'))
    await page.reload()
    await page.getByRole('button', { name: '건너뛰기', exact: true }).click()
    await page.getByText('메인 화면', { exact: true }).waitFor()
    assert.equal(await page.getByRole('dialog').count(), 0)
    await page.goto(base + '#/account/subscription')
    await page.getByRole('button', { name: '주기 전체선택', exact: true }).click()
    assert.equal(await page.getByRole('button', { name: '저장', exact: true }).isDisabled(), true)
    console.log('PASS empty onboarding skips directly and subscription still requires interests')

    await page.goto(base + '#/signup')
    await page.getByLabel('이름', { exact: true }).fill('신규 회원')
    await page.getByLabel('이메일 (아이디)', { exact: true }).fill('NewUser@Example.org')
    await page.getByLabel('비밀번호', { exact: true }).fill('Sample!123')
    await page.getByLabel('비밀번호 확인', { exact: true }).fill('Sample!123')
    const signupButton = page.getByRole('button', { name: '회원가입하고 관심사 설정하기', exact: true })
    assert.equal(await signupButton.isDisabled(), true)
    const agreements = page.getByRole('group', { name: '필수 약관 동의' })
    for (const label of ['[필수] 만 14세 이상입니다.', '[필수] 서비스 이용약관에 동의합니다.', '[필수] 개인정보 수집·이용에 동의합니다.']) {
      await agreements.getByText(label, { exact: true }).click()
    }
    await signupButton.click()
    await page.waitForURL(/#\/onboarding$/)
    const signupBody = writes.find(w => w.p === '/auth/signup').body
    assert.equal(signupBody.email, 'NewUser@Example.org')
    assert.equal(signupBody.age_confirmed, true)
    assert.equal(signupBody.service_terms_version, 'TERMS_SERVICE_V1')
    assert.equal(signupBody.privacy_terms_version, 'TERMS_PRIVACY_V1')
    console.log('PASS merged signup preserves case-exact email and mandatory terms consent')
    assert.deepEqual(errors, [])
  } finally {
    if (browser) await browser.close()
    await server.close()
  }
})().catch(error => { console.error(error); process.exitCode = 1 })
