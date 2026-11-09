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
    let failInterestSave = false
    const user = { user_id: '1', name: '검수', email: 'Review@Example.org', role: 'USER' }
    const product = { hs_code: '330499', hs_ver: 'HS2022', depth: 6, name_ko: '화장품' }
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
        return reply({ items: url.searchParams.get('depth') === '6' ? [product] : [], match_type: 'SIMILAR' })
      }
      if (p === '/reference/hs-codes/3304/children') return reply({ items: [product] })
      if (p === '/users/me/notification-settings') return reply({ settings: { notifications_enabled: false } })
      if (p === '/terms/notification') return reply({ terms: { version: 'v1', body: '검수 약관' } })
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

    await page.goto(base + '#/onboarding')
    await page.getByRole('combobox', { name: '품목 검색' }).fill('화')
    await page.getByRole('option').filter({ hasText: '화장품' }).click()
    assert.ok(searches.includes('화'))
    await page.getByRole('button', { name: '건너뛰기', exact: true }).click()
    await page.getByRole('dialog').waitFor()
    await page.screenshot({ path: path.join(screenshots, 'skip-confirmation.png'), fullPage: true })
    await page.getByRole('button', { name: '취소', exact: true }).click()
    assert.equal(writes.filter(w => w.p === '/users/me/interests').length, 0)
    assert.ok((await page.locator('body').innerText()).includes('화장품'))
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
    await page.getByRole('status').filter({ hasText: '환영합니다' }).waitFor()
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
    await page.getByRole('heading', { name: '비밀번호 수정', exact: true }).waitFor()
    await page.goto(base + '#/account/profile')
    await page.getByRole('heading', { name: '비밀번호 수정', exact: true }).waitFor()
    await page.goto(base + '#/account/notifications')
    await page.getByText('선택한 알림이 없습니다.').waitFor()
    assert.equal(writes.filter(w => w.p.includes('/notifications/')).length, 0)
    await page.getByRole('link', { name: '주요 이슈', exact: true }).click()
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
    assert.deepEqual(errors, [])
  } finally {
    if (browser) await browser.close()
    await server.close()
  }
})().catch(error => { console.error(error); process.exitCode = 1 })
