// Issue #121 rows 49–51: real chat UI, offline API responses.
const assert = require('node:assert/strict')
const path = require('node:path')
const { chromium } = require('playwright')

;(async () => {
  const { createServer } = await import('vite')
  const server = await createServer({ root: path.resolve(__dirname, '..'), server: { host: '127.0.0.1', port: 0, proxy: {} } })
  await server.listen()
  let browser
  try {
    browser = await chromium.launch({ headless: true, channel: 'chrome', args: ['--disable-features=LocalNetworkAccessChecks'] })
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 }, reducedMotion: 'no-preference' })
    page.setDefaultTimeout(10000)
    const errors = [], unexpected = [], answers = []
    page.on('pageerror', error => errors.push(error.message))
    const hs4 = { hs_code: '3304', hs_ver: 'HS2022', depth: 4, name_ko: '화장품' }
    const hs6 = { hs_code: '330499', hs_ver: 'HS2022', depth: 6, name_ko: '기초화장품' }
    let round = 0, childrenFail = false
    const prompt = () => ({ job_id: 'j1', session_id: 's1', status: 'NEEDS_INPUT', clarification: {
      clarification_id: 'c' + (++round), kind: 'HS_ITEM', message: '화장품의 분석 범위를 선택하세요.', allow_text: true, choices: [] } })
    let job = prompt()
    await page.route('**/api/v1/**', async route => {
      const req = route.request(), url = new URL(req.url()), p = url.pathname.replace('/api/v1', '')
      const reply = json => route.fulfill({ json })
      if (p === '/users/me') return reply({ user: { user_id: '1', name: '검수', email: 'qa@example.org', role: 'USER', terms_consents: [] }, csrf_token: 'test' })
      if (p === '/config/options') return reply({})
      if (p === '/users/me/interests') return reply({ interests: { hs_items: [], country_codes: [], version: '1' } })
      if (p === '/reference/countries') return reply({ items: [] })
      if (p === '/reference/hs-codes') return reply({ items: [url.searchParams.get('depth') === '4' ? hs4 : hs6], match_type: 'EXACT' })
      if (p === '/reference/hs-codes/3304/children') return childrenFail
        ? route.fulfill({ status: 503, json: { message: '세부품목 조회 실패' } }) : reply({ items: [hs6] })
      if (p === '/users/me/notification-settings') return reply({ settings: { notifications_enabled: false } })
      if (p === '/users/me/subscriptions') return reply({ items: [], version: '1' })
      if (p === '/notifications/unread-count') return reply({ count: 0 })
      if (p === '/notifications') return reply({ items: [], page: { total: 0 } })
      if (p === '/notifications/popups') return reply({ groups: [], retry_after_seconds: 60 })
      if (p.startsWith('/terms/')) return reply({ terms: { version: 'v1', body: '테스트 약관' } })
      if (p === '/reports') return reply({ items: [], page: { total: 0 } })
      if (p === '/chat/sessions') return reply({ items: [{ session_id: 's1', title: '챗봇 검수' }], page: { total: 1 } })
      if (p === '/chat/sessions/s1/messages') return reply({ items: [{ message_id: 'm1', role: 'USER', content_masked: '화장품 리포트 만들어줘', job }], page: { total: 1 } })
      if (p === '/chat/jobs/j1') return reply({ job })
      if (p === '/chat/jobs/j1/clarifications') {
        const body = req.postDataJSON()
        answers.push(body)
        job = body.hs_item ? { ...job, clarification: { clarification_id: 'c' + (++round), kind: 'REPORT_CONFIRM',
          message: `HS ${body.hs_item.hs_code} 리포트 조건을 확인하세요.`, allow_text: true, choices: [],
          report_preview: { preview_id: 'p1', formats: ['PDF'], conditions: { hs_items: [body.hs_item], country_codes: [], period: { start: '2025-01-01', end: '2025-12-01' } } } } } : prompt()
        return reply({ job })
      }
      unexpected.push(`${req.method()} ${p}`)
      return route.fulfill({ status: 404, json: { message: 'Unexpected test request' } })
    })
    const base = `http://127.0.0.1:${server.httpServer.address().port}`
    const input = page.getByRole('textbox', { name: '질문 입력', exact: true })
    const form = page.locator('.chat-flow form')
    const animation = () => form.evaluate(el => getComputedStyle(el).animationName)
    const fixed = async () => {
      await page.waitForFunction(() => {
        const form = document.querySelector('.chat-flow form')
        return form && getComputedStyle(form).animationName === 'none'
      })
      assert.equal(await animation(), 'none')
      assert.equal(await form.evaluate(el => getComputedStyle(el).borderTopColor), 'rgb(200, 16, 46)')
    }
    await page.goto(base + '/#/analysis?chat=s1')
    await page.getByText('화장품의 분석 범위를 선택하세요.', { exact: true }).waitFor()
    await page.waitForFunction(() => document.activeElement?.getAttribute('aria-label') === '질문 입력')
    assert.equal(await animation(), 'chat-blink', 'automatic focus must not consume the user interaction')
    await input.click()
    await fixed()
    await page.getByRole('combobox', { name: '품목 검색' }).click()
    await fixed() // Blur does not restart the same clarification.
    await input.fill('다른 품목')
    await page.getByRole('button', { name: '전송', exact: true }).click()
    await form.locator('input:not(:disabled)').waitFor()
    await page.waitForFunction(() => document.querySelector('.chat-flow form')?.classList.contains('chat-wait'))
    assert.equal(await animation(), 'chat-blink', 'a new clarification gets a new attention cycle')
    await input.press('a') // Already automatically focused: typing alone must stop it.
    await fixed()

    // Default search: exact HS4 offers full scope even if fetching its children fails.
    childrenFail = true
    await page.getByRole('combobox', { name: '품목 검색' }).fill('3304')
    await page.getByRole('button', { name: '이 HS4 전체로 분석', exact: true }).click()
    await page.getByText('HS 3304 리포트 조건을 확인하세요.', { exact: true }).waitFor()
    assert.deepEqual(answers.at(-1).hs_item, { hs_code: '3304', hs_ver: 'HS2022' })

    // Explicit HS4 mode, including choosing the parent before the full-scope button.
    childrenFail = false; job = prompt()
    await page.reload()
    await page.getByRole('button', { name: 'HS4 단위로 검색', exact: true }).click()
    await page.getByRole('combobox', { name: '품목 검색' }).fill('3304')
    await page.getByRole('option').filter({ hasText: 'HS4 전체로 선택' }).click()
    await page.getByText('HS4 확정', { exact: true }).waitFor()
    await page.getByRole('button', { name: '이 HS4 전체로 분석', exact: true }).click()
    await page.getByText('HS 3304 리포트 조건을 확인하세요.', { exact: true }).waitFor()
    assert.deepEqual(answers.at(-1).hs_item, { hs_code: '3304', hs_ver: 'HS2022' })

    // HS6 selection still sends only the selected child. Keyboard focus fixes the border too.
    job = prompt()
    await page.reload()
    await page.waitForFunction(() => document.activeElement?.getAttribute('aria-label') === '질문 입력')
    await page.getByRole('combobox', { name: '품목 검색' }).fill('330499')
    await input.focus()
    await fixed()
    await page.getByRole('combobox', { name: '품목 검색' }).click()
    await page.getByRole('option').filter({ hasText: '기초화장품' }).click()
    await page.getByText('HS 330499 리포트 조건을 확인하세요.', { exact: true }).waitFor()
    assert.deepEqual(answers.at(-1).hs_item, { hs_code: '330499', hs_ver: 'HS2022' })
    assert.deepEqual(errors, [])
    assert.deepEqual(unexpected, [])
    console.log('PASS chat QA: HS4 full scope, HS6 individual selection, focus/typing stop blinking, new prompt reset')
  } finally {
    await browser?.close()
    await server.close()
  }
})().catch(error => { console.error(error); process.exitCode = 1 })
