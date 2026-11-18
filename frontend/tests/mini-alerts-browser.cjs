// The real notification store, popover and inbox; no live API requests.
const assert = require('node:assert/strict')
const path = require('node:path')
const fs = require('node:fs')
const { chromium } = require('playwright')

;(async () => {
  const { createServer } = await import('vite')
  const server = await createServer({ root: path.resolve(__dirname, '..'), server: { host: '127.0.0.1', port: 0, proxy: {} } })
  await server.listen()
  let browser
  try {
    browser = await chromium.launch({ headless: true, args: ['--disable-features=LocalNetworkAccessChecks'], ...(process.env.CHROMIUM ? { executablePath: process.env.CHROMIUM } : {}) })
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
    page.setDefaultTimeout(10000)
    const errors = [], mutations = []
    page.on('pageerror', e => errors.push(e.message))
    let account = '1'
    const notice = id => ({ user_alert_id: id, event_type: 'MAJOR_ISSUE', title: `알림 ${id}`, message: `본문 ${id}`, created_at: '2026-11-18T01:00:00Z', read_at: null })
    const notices = ['1', '2', '3', '4'].map(notice)
    await page.route('**/api/v1/**', route => {
      const req = route.request(), p = new URL(req.url()).pathname.replace('/api/v1', '')
      if (req.method() !== 'GET') mutations.push([req.method(), p])
      const reply = json => route.fulfill({ json })
      if (p === '/auth/login') account = '2'
      if (p === '/users/me' || p === '/auth/login') return reply({ user: { user_id: account, role: 'USER', name: '테스트', email: `user${account}@example.test` }, csrf_token: 'synthetic' })
      if (p === '/users/me/interests') return reply({ interests: { hs_items: [], country_codes: [], landing_page: 'GLOBAL' } })
      if (p === '/users/me/notification-settings') return reply({ settings: { notifications_enabled: true } })
      if (p === '/users/me/subscriptions') return reply({ items: [], version: '1' })
      if (p === '/config/options') return reply({ report_periods: [] })
      if (p === '/notifications') return reply({ items: notices, page: { has_next: false } })
      if (p === '/notifications/unread-count') return reply({ count: notices.filter(n => !n.read_at).length })
      if (/^\/notifications\/\d+\/read$/.test(p)) {
        notices.find(n => n.user_alert_id === p.split('/')[2]).read_at = '2026-11-18T02:00:00Z'
        return route.fulfill({ status: 204 })
      }
      if (['/reference/countries', '/reports', '/chat/sessions'].includes(p)) return reply({ items: [] })
      errors.push('Unexpected API: ' + p)
      return route.fulfill({ status: 500, json: {} })
    })
    await page.route('**/mini-alerts-harness', route => route.fulfill({ contentType: 'text/html', body: `
      <div id="root"></div><script type="module">
      import RefreshRuntime from '/@react-refresh'; RefreshRuntime.injectIntoGlobalHook(window);
      window.$RefreshReg$=()=>{}; window.$RefreshSig$=()=>type=>type;
      window.__vite_plugin_react_preamble_installed__=true;
      await import('/tests/mini-alerts-harness.jsx');</script>` }))
    await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/mini-alerts-harness`)
    const open = () => page.locator('button.bell').click()
    const dialog = page.getByRole('dialog', { name: '최근 알림' })
    const remove = id => dialog.getByRole('button', { name: `알림 ${id} 미니 알림 삭제` })
    await open()
    const before = await page.getByTestId('original').innerText()
    await remove('1').click()
    await remove('1').waitFor({ state: 'detached' })
    assert.equal(await dialog.locator('.item').count(), 3) // next item fills the vacancy
    assert.equal(await page.getByTestId('original').innerText(), before)
    assert.equal(await page.getByTestId('unread').innerText(), '4')
    assert.deepEqual(mutations, [])
    await page.locator('.pop-dismiss').click({ position: { x: 5, y: 500 } })
    await open()
    assert.equal(await remove('1').count(), 0)
    await page.reload()
    await open()
    assert.equal(await remove('1').count(), 0)
    await dialog.getByRole('button', { name: '모두 삭제' }).click()
    await dialog.getByText('새 알림이 없습니다.').waitFor()
    assert.equal(await page.getByTestId('original').innerText(), before)
    assert.deepEqual(mutations, [])
    notices.unshift(notice('5'))
    await page.locator('.pop-dismiss').click({ position: { x: 5, y: 500 } })
    await page.getByRole('button', { name: '목록 갱신' }).click()
    await open()
    await remove('5').waitFor()
    assert.equal(await dialog.locator('.item').count(), 1)
    await dialog.getByRole('button', { name: '알림함 전체 보기' }).click()
    for (const id of ['1', '2', '3', '4', '5']) await page.getByText(`알림 ${id}`, { exact: true }).waitFor()
    assert.equal(await page.getByTestId('unread').innerText(), '5')
    await page.getByRole('button', { name: '계정 변경' }).click()
    await open()
    await remove('1').waitFor() // same IDs in another account must not inherit hidden state
    const dir = path.resolve(__dirname, '../../tmp/issue134-browser')
    fs.mkdirSync(dir, { recursive: true })
    await page.screenshot({ path: path.join(dir, 'alerts-desktop.png') })
    await page.setViewportSize({ width: 390, height: 844 })
    await page.screenshot({ path: path.join(dir, 'alerts-mobile.png') })
    const box = await dialog.boundingBox()
    assert.ok(box.x >= 0 && box.x + box.width <= 390 && box.y >= 0 && box.y + box.height <= 844)
    await dialog.getByRole('button', { name: '모두 읽음' }).click()
    await page.getByTestId('unread').filter({ hasText: /^0$/ }).waitFor()
    assert.equal(mutations.filter(([method]) => method === 'PATCH').length, 5)
    await dialog.locator('.item').first().click()
    await page.getByText('알림 상세', { exact: true }).waitFor()
    assert.ok(page.url().includes('id=5'))
    assert.equal(mutations.some(([method]) => method === 'DELETE'), false)
    assert.deepEqual(errors, [])
    console.log('PASS individual/all popover dismissal, original/unread preservation, refresh/reopen, new notifications, account isolation, inbox/detail/read-all, mobile layout')
  } finally { if (browser) await browser.close(); await server.close() }
})().catch(error => { console.error(error); process.exitCode = 1 })
