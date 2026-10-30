// Real local login; inject failures/delays only for the UI regression under test.
const { chromium } = require('playwright')
const assert = require('node:assert/strict')
const fs = require('node:fs'), path = require('node:path')
async function main() {
  const base = 'http://127.0.0.1:5173'
  const browser = await chromium.launch({ channel: 'chrome', headless: true })
  try {
  const context = await browser.newContext()
  const response = await context.request.post(base + '/api/v1/auth/signup', { data: {
    name: 'PR 검수', email: `review-${Date.now()}@example.org`, password: 'ReviewLocal!123', password_confirm: 'ReviewLocal!123',
  } })
  assert.equal(response.status(), 201)
  const { csrf_token: csrf } = await response.json()
  const sessions = []
  for (const title of ['검수 채팅 A', '검수 채팅 B']) {
    const created = await context.request.post(base + '/api/v1/chat/sessions', { data: {}, headers: { 'X-CSRF-Token': csrf } })
    assert.equal(created.status(), 201)
    const id = (await created.json()).session.session_id
    const renamed = await context.request.patch(base + `/api/v1/chat/sessions/${id}`, { data: { title }, headers: { 'X-CSRF-Token': csrf } })
    assert.ok(renamed.ok()); sessions.push(id)
  }
  const results = []
  const check = async (name, run) => {
    const page = await context.newPage(); page.setDefaultTimeout(12000)
    const errors = []; page.on('pageerror', error => errors.push(error.message))
    try { await run(page); assert.deepEqual(errors, []); results.push({ name, passed: true }); console.log('PASS ' + name) }
    catch (error) { results.push({ name, passed: false, error: error.message }); console.error('FAIL ' + name + ': ' + error.message) }
    finally { await page.close() }
  }
  const unavailable = route => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ code: 'TEST_UNAVAILABLE', message: '검수용 일시 오류' }) })
  try {
    await check('chat read retry does not lock the send button', async page => {
      let failing = true
      await page.route(`**/api/v1/chat/sessions/${sessions[0]}/messages*`, route => {
        if (route.request().method() === 'GET' && failing) return unavailable(route)
        return route.continue()
      })
      await page.goto(base + '/#/analysis?chat=' + sessions[0])
      await page.getByRole('alert').filter({ hasText: '검수용 일시 오류' }).waitFor()
      failing = false
      const restored = page.waitForResponse(r => r.url().includes(`/chat/sessions/${sessions[0]}/messages`) && r.status() === 200)
      await page.getByRole('button', { name: '다시 불러오기', exact: true }).click(); await restored
      await page.getByRole('alert').filter({ hasText: '검수용 일시 오류' }).waitFor({ state: 'hidden' })
      await page.getByLabel('질문 입력').fill('새 질문')
      assert.equal(await page.getByRole('button', { name: '전송', exact: true }).isEnabled(), true)
    })
    await check('failed send retries reuse a key only within the same chat', async page => {
      const sent = []
      await page.route('**/api/v1/chat/sessions/*/messages*', route => {
        if (route.request().method() === 'POST') { sent.push(route.request().postDataJSON()); return unavailable(route) }
        return route.continue()
      })
      await page.goto(base + '/#/analysis?chat=' + sessions[0])
      await page.getByLabel('질문 입력').fill('동일한 질문')
      const submit = async () => {
        const done = page.waitForResponse(r => r.request().method() === 'POST' && r.url().includes('/messages'))
        await page.getByRole('button', { name: '전송', exact: true }).click(); await done
        await page.getByRole('status').filter({ hasText: '검수용 일시 오류' }).waitFor()
      }
      await submit(); await submit()
      await page.getByRole('button').filter({ hasText: '검수 채팅 B' }).click()
      await page.getByRole('heading', { name: '검수 채팅 B', exact: true }).waitFor()
      await submit()
      assert.equal(sent[0].client_message_id, sent[1].client_message_id)
      assert.notEqual(sent[1].client_message_id, sent[2].client_message_id)
    })
    await check('report switch waits for the selected report to become READY', async page => {
      let nextStatus = 0, earlyReads = 0
      const conditions = { hs_items: [], country_codes: [], period: { start: '2025-12-01', end: '2025-12-31' } }
      await page.route(/\/api\/v1\/reports\/90000[12](\/status)?$/,  async route => {
        const url = new URL(route.request().url()), second = url.pathname.includes('900002')
        if (url.pathname.endsWith('/status')) {
          if (second) { nextStatus++; await new Promise(resolve => setTimeout(resolve, 250)) }
          return route.fulfill({ json: { job: { status: second && nextStatus === 1 ? 'QUEUED' : 'READY' } } })
        }
        if (second && nextStatus < 2) { earlyReads++; return unavailable(route) }
        return route.fulfill({ json: { report: { title: second ? '두 번째 검수 리포트' : '첫 번째 검수 리포트', conditions, available_formats: [], created_at: '2026-10-28' }, sections: [], meta: { sources: [] } } })
      })
      await page.goto(base + '/#/report?id=900001')
      await page.getByRole('heading', { name: '첫 번째 검수 리포트', exact: true }).waitFor()
      await page.evaluate(() => { location.hash = '/report?id=900002' })
      await page.getByRole('heading', { name: '두 번째 검수 리포트', exact: true }).waitFor()
      assert.equal(earlyReads, 0)
    })
  } finally {
    const out = path.resolve(__dirname, '../../tmp/pr-review'); fs.mkdirSync(out, { recursive: true })
    fs.writeFileSync(path.join(out, 'browser-regressions.json'), JSON.stringify(results, null, 2))
  }
  if (results.some(r => !r.passed)) process.exitCode = 1
  } finally { await browser.close() }
}
main().catch(error => { console.error(error); process.exitCode = 1 })
