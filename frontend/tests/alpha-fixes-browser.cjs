// Offline UI regressions: all API requests are intercepted with synthetic data.
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
    browser = await chromium.launch({ headless: true, channel: 'chrome', args: ['--disable-features=LocalNetworkAccessChecks'] })
    const page = await browser.newPage({ viewport: { width: 1280, height: 800 }, reducedMotion: 'reduce' })
    page.setDefaultTimeout(10000)
    const errors = []
    const screenshots = path.resolve(__dirname, '../../tmp/issue94-browser')
    fs.mkdirSync(screenshots, { recursive: true })
    page.on('pageerror', e => { errors.push(e.message); console.error(e.message) })
    page.on('console', m => { if (m.type() === 'error') console.error(m.text()) })
    const answer = { text: '첫 번째 답변입니다. '.repeat(100), tables: [{
      columns: [{ key: 'country_code', label: '국가' }, { key: 'amount_usd', label: '수출액', unit: 'USD' }, { key: 'share_pct', label: '비중', unit: '%' }],
      rows: [{ country_code: 'US', amount_usd: 1000, share_pct: 0.0123 }], total_rows: 1,
    }], articles: [], warnings: [] }
    const rows = [
      { message_id: '1', role: 'USER', content_masked: '첫 질문', job: { job_id: '1', status: 'SUCCEEDED' } },
      { message_id: '2', role: 'ASSISTANT', content_masked: answer.text, answer },
    ]
    let next = 3
    let statusMode = '503', finishStatus, statusCalls = 0, messageReads = 0
    const statusUnavailable = { status: 503, json: { message: '작업 상태 조회 일시 실패 테스트' } }
    const recoveredJob = { job_id: '90', status: 'SUCCEEDED', answer: { ...answer, text: '상태 재조회로 복구된 답변' } }
    const loadedJobs = new Set(['1']), prematureStreams = []
    const user = { user_id: '1', name: '검수', email: 'Review@Example.org', role: 'USER', terms_consents: [] }
    await page.route('**/api/v1/**', async route => {
      const req = route.request(), url = new URL(req.url()), p = url.pathname.replace('/api/v1', '')
      const reply = json => route.fulfill({ json })
      if (p === '/users/me') return reply({ user, csrf_token: 'test' })
      if (p === '/config/options') return reply({ report_periods: [] })
      if (p.endsWith('/answer-stream')) {
        const id = p.split('/')[3]
        if (!loadedJobs.has(id)) prematureStreams.push(id)
        return route.fulfill({ contentType: 'text/event-stream', body: 'event: done\ndata: {}\n\n' })
      }
      if (p === '/users/me/interests') return reply({ interests: { country_codes: [], hs_items: [], landing_page: 'GLOBAL' } })
      if (p === '/users/me/notification-settings') return reply({ settings: { notifications_enabled: false } })
      if (p === '/users/me/subscriptions') return reply({ items: [], version: '1' })
      if (p === '/notifications/unread-count') return reply({ count: 0 })
      if (p.startsWith('/terms/')) return reply({ terms: { version: 'TERMS_NOTIFICATION_V1', body: '알림 수신 약관 본문' } })
      if (p === '/chat/sessions') return reply({ items: [
        { session_id: '1', title: '테스트 채팅', updated_at: '2026-11-10T00:00:00Z' },
        { session_id: '2', title: '다른 채팅', updated_at: '2026-11-10T00:00:00Z' },
      ], page: { has_next: false } })
      if (p === '/chat/sessions/2/messages') return reply({ items: [
        { message_id: '201', role: 'USER', content_masked: '다른 방 질문' },
        { message_id: '202', role: 'ASSISTANT', content_masked: '다른 방 답변' },
      ], page: { has_next: false } })
      if (p === '/chat/jobs/90') {
        statusCalls++
        const response = statusMode === 'hold' ? await new Promise(resolve => { finishStatus = resolve })
          : statusMode === '503' ? statusUnavailable : { json: { job: recoveredJob } }
        return route.fulfill(response)
      }
      if (p === '/chat/jobs/90/clarifications' && req.method() === 'POST') return reply({ job: { job_id: '90', status: 'RUNNING' } })
      if (p === '/chat/sessions/1/messages') {
        if (req.method() === 'POST') {
          const id = String(next++)
          const failed = req.postDataJSON().question === '리포트 요청'
          const job = { job_id: id, status: failed ? 'FAILED' : 'SUCCEEDED', error: failed ? { message: '리포트 생성 실패 테스트' } : null }
          rows.push({ message_id: id, role: 'USER', content_masked: req.postDataJSON().question, job })
          if (!failed) rows.push({ message_id: String(next++), role: 'ASSISTANT', answer: { ...answer, text: req.postDataJSON().question === '다음 질문' ? '후속 답변' : '중간 답변' } })
          return reply({ job })
        }
        messageReads++
        await new Promise(resolve => setTimeout(resolve, 150))
        rows.forEach(row => { if (row.job) loadedJobs.add(row.job.job_id) })
        return reply({ items: rows, page: { has_next: false } })
      }
      if (p === '/articles') {
        const n = Number(url.searchParams.get('page') || 1)
        return reply({ items: Array.from({ length: 5 }, (_, i) => ({ article_id: String((n - 1) * 5 + i + 1),
          title: `페이지 ${n} 기사 ${i + 1}`, summary: '기사 요약입니다. '.repeat(60), source_code: 'KOTRA',
          published_date: '2026-11-10', article_type: 'GENERAL', tags: [] })), page: { total: 15 } })
      }
      if (p === '/reports/99/status') return reply({ job: { report_id: '99', status: 'FAILED', error: { message: '저장 리포트 실패 테스트' } } })
      if (p === '/reports' || p === '/notifications' || p.startsWith('/reference/')) return reply({ items: [], page: { has_next: false } })
      throw new Error(`Unexpected request: ${req.method()} ${p}`)
    })
    // Render actual components and styles without unrelated dashboard requests.
    await page.route('**/alpha-harness', route => route.fulfill({ contentType: 'text/html', body: `
      <div id="root"></div><script type="module">
      import RefreshRuntime from '/@react-refresh'; RefreshRuntime.injectIntoGlobalHook(window);
      window.$RefreshReg$=()=>{}; window.$RefreshSig$=()=>type=>type;
      window.__vite_plugin_react_preamble_installed__=true;
      await import('/tests/alpha-fixes-harness.jsx');</script>` }))
    const base = `http://127.0.0.1:${server.httpServer.address().port}/alpha-harness`
    await page.goto(base + '#/analysis?chat=1')
    await page.getByText('첫 질문', { exact: true }).waitFor()
    for (const text of ['후속 질문', '리포트 요청', '다음 질문']) {
      await page.getByLabel('질문 입력').fill(text)
      await page.getByRole('button', { name: '전송', exact: true }).click()
      await page.getByText(text, { exact: true }).waitFor()
    }
    await page.getByText('후속 답변', { exact: true }).waitFor()
    const boxes = await page.locator('.chat-flow > div').evaluateAll(elements => elements.map(el => ({ text: el.textContent.slice(0, 45), top: el.getBoundingClientRect().top, bottom: el.getBoundingClientRect().bottom })))
    for (let i = 1; i < boxes.length; i++) assert.ok(boxes[i].top >= boxes[i - 1].bottom, `Chat overlap: ${JSON.stringify(boxes.slice(i - 1, i + 1))}`)
    assert.equal(await page.locator('.chat-user').count(), 4)
    assert.deepEqual(prematureStreams, [], 'A new answer must never stream into the previous answer card')
    const failedTurn = page.locator('.chat-turn').filter({ has: page.getByText('리포트 요청', { exact: true }) })
    assert.equal(await failedTurn.getByRole('alert').count(), 1)
    assert.equal(await page.getByText('리포트 생성 실패 테스트', { exact: true }).count(), 1)
    console.log('PASS consecutive messages do not overlap')
    await page.evaluate(() => sessionStorage.setItem('analysis-reports:1', JSON.stringify({ 1: [{ report_id: '99', after: 'old-message', status: 'QUEUED' }] })))
    await page.reload()
    await page.getByText('저장 리포트 실패 테스트', { exact: true }).waitFor()
    await page.getByText('첫 질문', { exact: true }).waitFor()
    assert.ok(await page.evaluate(() => document.getElementById('report-99').compareDocumentPosition(document.querySelector('.chat-user')) & Node.DOCUMENT_POSITION_FOLLOWING))
    await page.getByRole('button', { name: '실패 안내 닫기' }).click()
    assert.equal(await page.locator('#report-99').count(), 0)
    await page.reload()
    await page.getByText('첫 질문', { exact: true }).waitFor()
    assert.equal(await page.locator('#report-99').count(), 0)
    console.log('PASS failed report stays before later turns and can be dismissed persistently')

    rows.push({ message_id: '90', role: 'USER', content_masked: '상태 조회 대기 질문', job: { job_id: '90', status: 'RUNNING' } })
    statusMode = 'hold'
    const pendingStatus = page.waitForRequest('**/api/v1/chat/jobs/90')
    await page.reload()
    await pendingStatus
    // History must render before the status response, including previously completed answers.
    await page.getByText('첫 질문', { exact: true }).waitFor()
    await page.getByText('후속 답변', { exact: true }).waitFor()
    await page.getByText('상태 조회 대기 질문', { exact: true }).waitFor()
    finishStatus(statusUnavailable)
    await page.getByText('작업 상태 조회 일시 실패 테스트', { exact: true }).waitFor()
    assert.equal(await page.getByLabel('질문 입력').isDisabled(), true)
    const readsBeforeRetry = messageReads, callsBeforeRetry = statusCalls
    statusMode = '503'
    const repeatedFailure = page.waitForResponse('**/api/v1/chat/jobs/90')
    await page.getByRole('button', { name: '작업 상태 다시 조회', exact: true }).click()
    await repeatedFailure
    await page.getByText('작업 상태 조회 일시 실패 테스트', { exact: true }).waitFor()
    assert.equal(messageReads, readsBeforeRetry, 'Status retry must not refetch the message list')
    assert.equal(statusCalls, callsBeforeRetry + 1)
    assert.equal(await page.getByText('후속 답변', { exact: true }).count(), 1)
    statusMode = 'complete'
    await page.getByRole('button', { name: '작업 상태 다시 조회', exact: true }).click()
    await page.getByText('상태 재조회로 복구된 답변', { exact: true }).waitFor()
    assert.equal(await page.getByText('작업 상태 조회 일시 실패 테스트', { exact: true }).count(), 0)
    assert.equal(await page.getByLabel('질문 입력').isDisabled(), false)
    assert.equal(messageReads, readsBeforeRetry)
    assert.deepEqual(prematureStreams, [])
    console.log('PASS slow/503 status responses preserve history, and status-only retry recovers the answer')

    rows.at(-1).job = { job_id: '90', status: 'NEEDS_INPUT', clarification: {
      clarification_id: 'confirm-90', kind: 'CHOICE', message: '분석 조건을 확인해 주세요', allow_text: false,
      choices: [{ choice_id: 'yes', label: '이 조건으로 분석' }],
    } }
    statusMode = '503'
    await page.reload()
    await page.getByText('작업 상태 조회 일시 실패 테스트', { exact: true }).waitFor()
    const statusAfterClarification = page.waitForResponse('**/api/v1/chat/jobs/90')
    await page.getByRole('button', { name: '이 조건으로 분석', exact: true }).click()
    await statusAfterClarification
    await page.getByText('작업 상태 조회 일시 실패 테스트', { exact: true }).waitFor()
    assert.equal(await page.getByText('첫 질문', { exact: true }).count(), 1)
    assert.equal(await page.getByText('후속 답변', { exact: true }).count(), 1)
    assert.equal(await page.getByRole('button', { name: '이 조건으로 분석', exact: true }).count(), 0)
    assert.equal(await page.getByLabel('질문 입력').isDisabled(), true)
    rows.at(-1).job = { job_id: '90', status: 'RUNNING' }
    console.log('PASS stale clarification snapshots do not reopen an already accepted prompt after a 503')

    statusMode = 'hold'
    const lateStatus = page.waitForRequest('**/api/v1/chat/jobs/90')
    await page.reload()
    await lateStatus
    await page.getByText('상태 조회 대기 질문', { exact: true }).waitFor()
    await page.getByRole('button', { name: /다른 채팅/ }).click()
    await page.getByText('다른 방 답변', { exact: true }).waitFor()
    finishStatus({ json: { job: recoveredJob } })
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
    assert.equal(await page.getByText('상태 재조회로 복구된 답변', { exact: true }).count(), 0)
    assert.equal(await page.getByRole('button', { name: '작업 상태 다시 조회', exact: true }).count(), 0)
    assert.equal(await page.getByText('다른 방 답변', { exact: true }).count(), 1)
    rows.pop()
    console.log('PASS late status response cannot overwrite a different chat')

    await page.goto(base + '#/articles')
    await page.getByText('페이지 1 기사 1', { exact: true }).waitFor()
    await page.getByRole('button', { name: '2', exact: true }).click()
    await page.getByText('페이지 2 기사 1', { exact: true }).waitFor()
    await page.waitForFunction(() => document.querySelector('.sec-h').getBoundingClientRect().top >= 0)
    assert.ok((await page.locator('.sec-h').boundingBox()).y < 150)
    await page.getByRole('button', { name: '1', exact: true }).click()
    await page.getByText('페이지 1 기사 1', { exact: true }).waitFor()
    await page.waitForFunction(() => document.querySelector('.sec-h').getBoundingClientRect().top >= 0)
    console.log('PASS article pagination returns to the list heading in both directions')

    await page.goto(base + '#/account/profile')
    await page.getByRole('heading', { name: '비밀번호 변경', exact: true }).waitFor()
    await page.getByLabel('이메일', { exact: true }).fill(user.email)
    await page.getByRole('button', { name: '이메일 확인', exact: true }).click()
    await page.getByLabel('변경할 계정').waitFor()
    const gap = await page.evaluate(() => document.querySelector('label[for="pw-current"]').getBoundingClientRect().top - document.querySelector('#pw-account').getBoundingClientRect().bottom)
    assert.ok(gap >= 20)
    console.log('PASS password title and account/current-password spacing')

    await page.goto(base + '#/account/settings?terms=open')
    await page.getByText('알림 수신 약관 본문', { exact: true }).waitFor()
    assert.equal(await page.getByText(/TERMS_NOTIFICATION_V1/).count(), 0)
    console.log('PASS internal notification terms version is hidden')

    for (const width of [375, 640, 1024]) {
      await page.setViewportSize({ width, height: 800 })
      for (const route of ['/analysis?chat=1', '/articles', '/account/password']) {
        await page.goto(base + '#' + route)
        if (route === '/articles') {
          await page.getByText('페이지 1 기사 5', { exact: true }).waitFor()
        } else if (route === '/account/password') {
          await page.getByLabel('변경할 계정').waitFor()
        } else {
          await page.getByText('후속 답변', { exact: true }).waitFor()
        }
        await page.evaluate(() => document.fonts.ready)
        const layout = await page.evaluate(() => ({
          pageWidth: document.documentElement.scrollWidth,
          wordBreak: getComputedStyle(document.body).wordBreak,
          brokenButtons: [...document.querySelectorAll('.btn,.chip')].filter(el => el.getBoundingClientRect().width && getComputedStyle(el).whiteSpace !== 'nowrap').map(el => el.textContent),
        }))
        assert.ok(layout.pageWidth <= width, `${route}: page width ${layout.pageWidth}px exceeds viewport ${width}px`)
        assert.equal(layout.wordBreak, 'keep-all')
        assert.deepEqual(layout.brokenButtons, [])
      }
    }
    // Verify actual line boxes: a word moves intact, while an overlong URL can still fit.
    const wraps = await page.evaluate(() => {
      const el = document.createElement('p'); el.style.cssText = 'width:100px;font-size:16px;line-height:24px';
      el.textContent = '가독성 대한민국 가독성'; document.body.append(el)
      const range = document.createRange(); range.setStart(el.firstChild, 4); range.setEnd(el.firstChild, 8)
      const wordLines = range.getClientRects().length
      el.textContent = 'https://example.org/' + 'a'.repeat(150)
      const fits = el.scrollWidth <= el.clientWidth
      el.remove(); return { wordLines, fits }
    })
    assert.deepEqual(wraps, { wordLines: 1, fits: true })
    await page.setViewportSize({ width: 375, height: 800 })
    await page.goto(base + '#/analysis?chat=1')
    await page.getByText('후속 답변', { exact: true }).waitFor()
    await page.getByText('다음 질문', { exact: true }).scrollIntoViewIfNeeded()
    await page.screenshot({ path: path.join(screenshots, 'chat-mobile.png') })
    await page.setViewportSize({ width: 1280, height: 800 })
    await page.goto(base + '#/account/password')
    await page.getByLabel('변경할 계정').waitFor()
    await page.screenshot({ path: path.join(screenshots, 'password-desktop.png'), fullPage: true })
    assert.deepEqual(errors, [])
    console.log('PASS responsive buttons and global word wrapping at 375/640/1024px')
  } finally {
    if (browser) await browser.close()
    await server.close()
  }
})().catch(error => { console.error(error); process.exitCode = 1 })
