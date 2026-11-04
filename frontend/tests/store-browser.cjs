// Isolated Vite/Chromium test: API requests are synthetic; no RDS/session access.
const assert = require('node:assert/strict')
const path = require('node:path')
const { chromium } = require('playwright')

;(async () => {
  const { createServer } = await import('vite')
  const server = await createServer({ root: path.resolve(__dirname, '..'),
    server: { host: '127.0.0.1', port: 0, strictPort: false, allowedHosts: ['review-http.test'] } })
  await server.listen()
  let browser
  try {
    const port = server.httpServer.address().port
    browser = await chromium.launch({ headless: true,
      // Synthetic hostname maps to loopback; permit test-only local-network access.
      // This does not make the HTTP origin a secure context (asserted below).
      args: ['--host-resolver-rules=MAP review-http.test 127.0.0.1', '--no-proxy-server',
        '--disable-features=LocalNetworkAccessChecks'] })
    const page = await browser.newPage({ timezoneId: 'America/Los_Angeles' })
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    page.on('console', message => { if (message.type() === 'error') console.error(message.text()) })
    page.on('requestfailed', request => console.error('Failed test request', request.url(), request.failure()?.errorText))
    let count = 0
    const sent = [], generated = []
    let releaseProfile
    let releaseSettings, releaseSubscription
    let subscriptionReads = 0
    let conflict = false, savedVersion
    let previews = 0
    let revisedPeriod = false
    let interestFlow = false
    const interestPuts = []
    const conditions = { hs_items: [], country_codes: [], period: { start: '2025-12-01', end: '2025-12-31' }, compare: 'NONE' }
    await page.route('**/api/v1/**', route => {
      const pathname = new URL(route.request().url()).pathname
      if (pathname.endsWith('/users/me')) return route.fulfill({ status: 401, json: { message: 'test signed out' } })
      const savedInterests = { version: 'a'.repeat(64), country_codes: ['CN'], hs_items: [], landing_page: 'GLOBAL' }
      if (interestFlow && pathname.endsWith('/users/me/interests')) {
        if (route.request().method() === 'PUT') interestPuts.push(route.request().postDataJSON())
        return route.fulfill({ json: { interests: route.request().method() === 'PUT' ? { ...savedInterests, country_codes: ['CN', 'US'] } : savedInterests } })
      }
      if (pathname.endsWith('/users/me/interests') && route.request().method() === 'PUT') {
        return new Promise(resolve => { releaseProfile = async () => {
          await route.fulfill({ json: { interests: { version: 'new-version', landing_page: 'GLOBAL' } } }); resolve()
        } })
      }
      if (pathname.endsWith('/users/me/notification-settings')) {
        return new Promise(resolve => { releaseSettings = async () => {
          await route.fulfill({ json: {} }); resolve()
        } })
      }
      if (pathname.endsWith('/users/me/subscriptions')) {
        if (route.request().method() === 'GET') {
          subscriptionReads++
          return route.fulfill({ json: { version: 'latest', items: [{ enabled: true, period_code: 'MONTHLY', formats: ['DOCX'] }] } })
        }
        if (conflict) return route.fulfill({ status: 409, json: { code: 'VERSION_CONFLICT', message: '구독 설정이 변경되었습니다.' } })
        if (subscriptionReads) {
          savedVersion = route.request().postDataJSON().expected_version
          return route.fulfill({ json: {} })
        }
        return new Promise(resolve => { releaseSubscription = async () => {
          await route.fulfill({ json: {} }); resolve()
        } })
      }
      if (pathname.endsWith('/notifications/unread-count')) return route.fulfill({ json: { count } })
      if (pathname.endsWith('/notifications')) return route.fulfill({ json: { items: count ? [{
        user_alert_id: '1', event_type: 'REPORT', message: 'Test report', created_at: '2026-10-29',
      }] : [], page: { has_next: false } } })
      if (pathname.endsWith('/chat/sessions/1/messages')) {
        if (route.request().method() === 'POST') {
          sent.push(route.request().postDataJSON())
          return route.fulfill({ status: 202, json: { job: { job_id: '1' } } })
        }
        return route.fulfill({ json: { items: [{ message_id: '1', role: 'ASSISTANT', answer: {
          text: 'Test analysis', conditions, articles: [], warnings: [],
          tables: [{ result_id: 'result-1', columns: [{ key: 'country_code', label: '국가' }], rows: [{ country_code: 'CN' }, { country_code: 'US' }] }],
        } }], page: { has_next: false } } })
      }
      if (pathname.endsWith('/chat/sessions')) {
        return route.fulfill({ json: { items: [{ session_id: '1', title: 'Timestamp test chat',
          updated_at: '2026-11-03T00:30:00Z' }], page: { has_next: false } } })
      }
      if (pathname.endsWith('/reports') && route.request().method() === 'GET') {
        return route.fulfill({ json: { items: [], page: { has_next: false } } })
      }
      if (pathname.endsWith('/reference/regions')) return route.fulfill({ json: { items: [] } })
      if (pathname.endsWith('/reports/2/status')) return route.fulfill({ json: { job: { report_id: '2', status: 'READY' } } })
      if (pathname.endsWith('/data/availability')) return route.fulfill({ json: { trade_start_month: '2024-08', trade_end_month: '2025-12' } })
      if (pathname.endsWith('/reports/preview')) return route.fulfill({ json: {
        preview_id: 'preview-' + (++previews), conditions: { ...conditions,
          compare_period: { start: '2024-12-01', end: revisedPeriod && previews === 4 ? '2024-12-30' : '2024-12-31' } }, expires_at: new Date(Date.now() + 60000).toISOString(),
      } })
      if (pathname.endsWith('/reports')) {
        generated.push({ body: route.request().postDataJSON(), key: route.request().headers()['idempotency-key'] })
        if (['preview-1','preview-3'].includes(generated.at(-1).body.preview_id)) return route.fulfill({ status: 409, json: { code: 'PREVIEW_EXPIRED' } })
        return route.fulfill({ status: 202, json: { job: { report_id: '2' } } })
      }
      return route.fulfill({ status: 500, json: { message: 'Unexpected test request: ' + pathname } })
    })
    await page.route('**/review-harness', route => route.fulfill({ contentType: 'text/html', body: `
      <div id="root"></div><script type="module">
      import RefreshRuntime from '/@react-refresh';
      RefreshRuntime.injectIntoGlobalHook(window);
      window.$RefreshReg$ = () => {}; window.$RefreshSig$ = () => type => type;
      window.__vite_plugin_react_preamble_installed__ = true;
      await import('/tests/store-harness.jsx');</script>` }))
    await page.goto(`http://review-http.test:${port}/review-harness`)
    try { await page.waitForFunction(() => window.reviewApp && !window.reviewApp.loading) }
    catch (error) { console.error('Harness errors', errors); throw error }
    const baseline = await page.evaluate(() => ({ ...window.reviewCounts }))
    await page.evaluate(() => window.reviewApp.notify('test toast'))
    await page.waitForFunction(() => window.reviewToast.toast === 'test toast')
    assert.equal(await page.evaluate(() => window.reviewCounts.app), baseline.app)
    assert.equal(await page.evaluate(() => window.reviewCounts.notifications), baseline.notifications)
    console.log('PASS toast changes do not rerender app or notification consumers')
    count = 1
    await page.evaluate(() => window.reviewNotifications.refreshNotifications())
    await page.waitForFunction(() => window.reviewNotifications.unread === 1)
    assert.equal(await page.evaluate(() => window.reviewCounts.app), baseline.app)
    const notified = await page.evaluate(() => window.reviewCounts.notifications)
    await page.evaluate(() => window.reviewNotifications.refreshNotifications())
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
    assert.equal(await page.evaluate(() => window.reviewCounts.notifications), notified)
    console.log('PASS notification updates stay isolated and unchanged polls do not rerender')
    const profileRequest = page.waitForRequest(r => r.method() === 'PUT' && r.url().endsWith('/users/me/interests'))
    await page.evaluate(() => {
      window.reviewPendingSave = window.reviewApp.setProfile({ ...window.reviewApp.profile, name: 'late response' })
    })
    await profileRequest
    await page.evaluate(() => window.dispatchEvent(new Event('session-expired')))
    await page.waitForFunction(() => window.reviewApp.profile.name === '')
    await releaseProfile()
    await page.evaluate(() => window.reviewPendingSave)
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(resolve)))
    assert.equal(await page.evaluate(() => window.reviewApp.profile.name), '')
    console.log('PASS late profile response cannot repopulate state after session expiry')
    const settingsRequest = page.waitForRequest(r => r.method() === 'PATCH' && r.url().endsWith('/users/me/notification-settings'))
    await page.evaluate(() => {
      window.reviewPendingSave = window.reviewApp.setSettings({ all: true, surge: true, major: true, report: true })
    })
    await settingsRequest
    await page.evaluate(() => window.dispatchEvent(new Event('session-expired')))
    await releaseSettings()
    assert.equal(await page.evaluate(() => window.reviewPendingSave), false)
    assert.equal(await page.evaluate(() => window.reviewApp.settings.all), false)
    console.log('PASS late notification settings response is ignored after session expiry')
    const subscriptionRequest = page.waitForRequest(r => r.method() === 'PUT' && r.url().endsWith('/users/me/subscriptions'))
    await page.evaluate(() => {
      window.reviewPendingSave = window.reviewApp.setSubscription({ cycles: ['monthly'], formats: ['PDF'] })
    })
    await subscriptionRequest
    await page.evaluate(() => window.dispatchEvent(new Event('session-expired')))
    await releaseSubscription()
    assert.equal(await page.evaluate(() => window.reviewPendingSave), false)
    assert.equal(subscriptionReads, 0)
    assert.deepEqual(await page.evaluate(() => window.reviewApp.subscription.cycles), [])
    console.log('PASS expired subscription save cannot refresh data for a later session')
    conflict = true
    const saveMonthly = () => page.evaluate(() => window.reviewApp.setSubscription({ cycles: ['monthly'], formats: ['PDF'] }))
    assert.equal(await saveMonthly(), false)
    await page.waitForFunction(() => window.reviewApp.subscription.version === 'latest')
    assert.deepEqual(await page.evaluate(() => window.reviewApp.subscription.formats), ['DOCX'])
    conflict = false
    assert.equal(await saveMonthly(), true)
    assert.equal(savedVersion, 'latest')
    const ids = await page.evaluate(async () => {
      const { requestId } = await import('/src/api/requestId.js')
      return { secure: isSecureContext, native: typeof crypto.randomUUID,
        first: requestId(), second: requestId() }
    })
    assert.equal(ids.secure, false)
    assert.equal(ids.native, 'undefined')
    assert.match(ids.first, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
    assert.notEqual(ids.first, ids.second)
    console.log('PASS request IDs work in a real insecure HTTP browser origin')
    await page.evaluate(() => window.reviewApp.refreshChats())
    await page.waitForFunction(() => window.reviewApp.chats.length === 1)
    assert.equal(await page.evaluate(() => window.reviewApp.chats[0].updated_at), '2026-11-03T00:30:00Z')
    await page.evaluate(() => window.reviewShowAnalysis())
    await page.getByText('2026-11-02 16:30:00', { exact: true }).waitFor()
    await page.getByText('최근 대화 순 · 기기 현지 시각', { exact: true }).waitFor()
    console.log('PASS chat timestamps retain ISO values and render in the browser timezone across dates')
    await page.getByRole('button', { name: '리포트로 저장', exact: true }).waitFor()
    await page.getByLabel('질문 입력').fill('HTTP test question')
    await page.getByRole('button', { name: '전송', exact: true }).click()
    await page.waitForFunction(() => document.querySelector('[aria-label="질문 입력"]').value === '')
    assert.match(sent[0].client_message_id, /^[0-9a-f-]{36}$/)
    await page.getByRole('button', { name: '리포트로 저장', exact: true }).click()
    const year = page.getByLabel('분석 시작 연도')
    await page.waitForFunction(() => !document.querySelector('[aria-label="분석 시작 연도"]').disabled)
    assert.deepEqual(await year.locator('option').allTextContents(), ['연도','2025','2024'])
    await year.selectOption('2024')
    assert.equal(await page.getByLabel('분석 시작 월').locator('option').first().getAttribute('value'), '08')
    await year.selectOption('2025')
    await page.getByRole('button', { name: '리포트 조건 확인', exact: true }).click()
    await page.getByText('비교 기간 2024-12-01 ~ 2024-12-31', { exact:true }).waitFor()
    await page.getByRole('button', { name: '확인한 조건으로 생성', exact: true }).click()
    await page.getByText('리포트 센터에서 이 리포트 열기', { exact: true }).first().waitFor()  // 채팅 안에서 생성 상태를 보여 준다
    assert.match(generated[0].key, /^[0-9a-f-]{36}$/)
    assert.deepEqual(generated.map(r => r.body.preview_id), ['preview-1','preview-2'])
    revisedPeriod = true
    await page.getByRole('button', { name: '리포트로 저장', exact: true }).click()
    await page.getByRole('button', { name: '리포트 조건 확인', exact: true }).click()
    const submit = page.getByRole('button', { name: '확인한 조건으로 생성', exact: true })
    await submit.click()
    await page.getByText('비교 기간 2024-12-01 ~ 2024-12-30', { exact:true }).waitFor()
    assert.equal(generated.length,3)
    await submit.click()
    await page.waitForFunction(() => !document.querySelector('[role=dialog]'))
    assert.equal(generated[3].body.preview_id,'preview-4')
    console.log('PASS chat submission, preview and report creation send valid IDs over HTTP')
    interestFlow = true
    await page.getByRole('button', { name: '관심 목록 추가', exact: true }).first().click()
    const dialog = page.getByRole('dialog', { name: '관심 목록 추가' })
    await dialog.getByText('이미 등록됨', { exact: false }).waitFor()
    assert.equal(await dialog.locator('label', { hasText: '이미 등록됨' }).locator('input').isDisabled(), true)
    const alerts = []
    page.once('dialog', d => { alerts.push(d.message()); d.accept() })
    await dialog.getByRole('button', { name: '선택 항목 저장', exact: true }).click()
    await page.waitForFunction(() => true)
    assert.deepEqual(alerts, ['체크된 항목이 없습니다'])
    assert.equal(interestPuts.length, 0)
    await dialog.locator('label').filter({ hasNotText: '이미 등록됨' }).first().click()
    await dialog.getByRole('button', { name: '선택 항목 저장', exact: true }).click()
    await page.waitForFunction(() => !document.querySelector('[aria-label="관심 목록 추가"][role=dialog]'))
    assert.deepEqual(interestPuts.map(b => b.country_codes), [['CN', 'US']])
    console.log('PASS chat interest dialog disables saved items, alerts on empty selection and saves new items')
    assert.deepEqual(errors, [])
  } finally {
    if (browser) await browser.close()
    await server.close()
  }
})().catch(error => { console.error(error); process.exitCode = 1 })
