// Issues #121, #126: real pages with offline synthetic API responses.
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
    browser = await chromium.launch({ headless: true, channel: 'chrome', args: ['--disable-features=LocalNetworkAccessChecks'] })
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 }, reducedMotion: 'reduce' })
    page.setDefaultTimeout(10000)
    const errors = [], unexpected = [], trends = [], sent = [], reads = [], questions = [], articleSearches = []
    page.on('pageerror', e => errors.push(e.message))
    const shots = path.resolve(__dirname, '../../tmp/issue121-browser')
    fs.mkdirSync(shots, { recursive: true })
    const countries = [['US', '미국'], ['CN', '중국'], ['LA', '라오스'], ['BA', '보스니아 헤르체고비나'], ['VN', '베트남']].map(([country_code, name_ko]) => ({ country_code, name_ko }))
    const products = ['330499', '330410', '330420', '330430'].map(hs_code => ({ hs_code, hs_ver: 'HS2022', name_ko: '화장품 ' + hs_code }))
    const conditions = { hs_items: [], country_codes: [], period: { start: '2025-01-01', end: '2025-12-31' } }
    let reports = ['RPT_ON_DEMAND', 'RPT_WEEKLY', 'RPT_MONTHLY', 'RPT_QUARTERLY'].map((report_type, i) => ({ report_id: String(i + 1), report_type, title: '검수 리포트 ' + (i + 1), status: 'READY', created_at: '2026-11-10T00:00:00Z', conditions, available_formats: ['PDF', 'DOCX'] }))
    let deleteFails = true, created = 0
    const chats = [{ session_id: 'old', title: '기존 대화', updated_at: '2026-11-10T00:00:00Z' }]
    const messages = { old: [{ message_id: 'old1', role: 'USER', content_masked: '이전 질문' }] }
    const articles = countries.map((c, i) => ({ article_id: String(i + 1), title: `${c.name_ko} 디지털 금융 서비스 확대`, source_code: 'KOTRA_SHORT_NEWS', source_name: 'KOTRA 단신속보', country_codes: [c.country_code], published_date: '2026-11-10', summary: '현지 수출 동향', hs_items: [] }))
    await page.route('**/api/v1/**', async route => {
      const req = route.request(), url = new URL(req.url()), p = url.pathname.replace('/api/v1', '')
      const reply = json => route.fulfill({ json })
      if (p === '/users/me') return reply({ user: { user_id: '1', name: '검수', email: 'qa@example.org', role: 'USER', terms_consents: [] }, csrf_token: 'test' })
      if (p === '/config/options') return reply({ report_periods: ['WEEKLY', 'MONTHLY', 'QUARTERLY'].map(option_code => ({ option_code })) })
      if (p === '/users/me/interests') return reply({ interests: { country_codes: countries.map(c => c.country_code), hs_items: products, landing_page: 'GLOBAL', version: '1' } })
      if (p === '/reference/countries') return reply({ items: countries })
      if (p === '/reference/hs-codes/3304/children') return reply({ items: products })
      if (p === '/users/me/notification-settings') return reply({ settings: { notifications_enabled: true, report_popup: true } })
      if (p === '/users/me/subscriptions') return reply({ items: [], version: '1' })
      if (p === '/notifications/unread-count') return reply({ count: 0 })
      if (p === '/notifications') return reply({ items: [], page: { total: 0 } })
      if (p === '/notifications/popups') return reply({ groups: [], retry_after_seconds: 60 })
      if (p.startsWith('/terms/')) return reply({ terms: { version: 'v1', body: '테스트 약관' } })
      if (p === '/reports') return reply({ items: reports, page: { total: reports.length } })
      if (/^\/reports\/\d+$/.test(p) && req.method() === 'DELETE') {
        if (deleteFails) return route.fulfill({ status: 503, json: { message: '삭제 실패 테스트' } })
        reports = reports.filter(r => r.report_id !== p.split('/')[2])
        return route.fulfill({ status: 204 })
      }
      if (p === '/chat/sessions') {
        if (req.method() === 'POST') {
          const session = { session_id: 'new' + (++created), title: '새 대화', updated_at: '2026-11-16T00:00:00Z' }
          chats.unshift(session); messages[session.session_id] = []
          return reply({ session })
        }
        return reply({ items: chats, page: { total: chats.length } })
      }
      if (/^\/chat\/sessions\/[^/]+\/messages$/.test(p)) {
        const id = p.split('/')[3]
        if (req.method() === 'POST') {
          sent.push(id)
          const question = req.postDataJSON().question
          questions.push(question)
          if (!messages[id].length) chats.find(c => c.session_id === id).title = question.replace(/\s+/g, ' ').trim().slice(0, 20)
          const job = { job_id: 'j1', status: 'FAILED', error: { message: '오프라인 검수 종료' } }
          messages[id].push({ message_id: 'm1', role: 'USER', content_masked: req.postDataJSON().question, job })
          return reply({ job })
        }
        reads.push(id)
        return reply({ items: messages[id], page: { total: messages[id].length } })
      }
      if (p === '/articles') {
        articleSearches.push(Object.fromEntries(url.searchParams))
        return reply({ items: articles, page: { total: 5 } })
      }
      if (/^\/articles\/\d+$/.test(p)) return reply({ article: articles.find(a => a.article_id === p.split('/')[2]), body_masked: '- 현지 금융 서비스가\n확대되고 있다.\n\n' + '- 상세 내용이다.\n'.repeat(60) + 'https://example.org/' + 'long-path-'.repeat(60) })
      if (p === '/data/availability') return reply({ trade_end_month: '2025-12' })
      if (p === '/trade/trends') {
        trends.push(Object.fromEntries(url.searchParams))
        return reply({ items: Array.from({ length: 12 }, (_, i) => ({ month: `2025-${String(i + 1).padStart(2, '0')}`, export_amount_usd: 100000000, import_amount_usd: 60000000, previous_year_export_usd: 80000000, previous_year_import_usd: 50000000 })) })
      }
      if (p === '/trade/rankings' || p === '/exchange-rates' || p === '/analysis/market-shares' || p === '/un/availability') return reply({ items: [], meta: { period: conditions.period } })
      unexpected.push(`${req.method()} ${p}`)
      return route.fulfill({ status: 404, json: { message: 'Unexpected test request' } })
    })
    const base = `http://127.0.0.1:${server.httpServer.address().port}`
    await page.goto(base + '/#/')
    await page.getByRole('heading', { name: '대한민국 수출입 현황' }).waitFor()
    await page.getByText('▲+25.0%', { exact: true }).waitFor()
    assert.equal(trends[0].period_start, '2025-01')
    assert.equal(trends[0].period_end, '2025-12')
    assert.equal(await page.getByText('▲+1.2억 USD', { exact: true }).count(), 1)
    assert.equal(await page.getByRole('link', { name: '전체보기', exact: true }).count(), 0)
    assert.equal(await page.getByRole('link', { name: '기사홈', exact: true }).count(), 1)
    assert.equal(await page.getByText('순위 = 상대국 공급국 중 한국 순위').count(), 0)
    const footerWeights = await page.locator('footer .ft-link').evaluateAll(links => links.slice(0, 2).map(a => getComputedStyle(a).fontWeight))
    assert.equal(footerWeights[0], footerWeights[1])
    await page.screenshot({ path: path.join(shots, 'main-desktop.png') })
    await page.setViewportSize({ width: 390, height: 844 })
    await page.screenshot({ path: path.join(shots, 'main-mobile.png'), fullPage: true })
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1))
    await page.setViewportSize({ width: 1280, height: 900 })

    await page.goto(base + '/#/articles?tab=short&id=3')
    await page.getByText('현지 금융 서비스가 확대되고 있다.', { exact: true }).waitFor()
    const aligned = await page.locator('.article-tabs').evaluate(el => getComputedStyle(el).justifyContent)
    assert.equal(aligned, 'flex-start')
    const bodySize = await page.locator('.article-body').evaluate(el => ({ h: el.clientHeight, full: el.scrollHeight, w: el.clientWidth, scroll: el.scrollWidth }))
    assert.ok(bodySize.full > bodySize.h && bodySize.h <= 512)
    assert.ok(bodySize.scroll <= bodySize.w + 1)
    await page.screenshot({ path: path.join(shots, 'articles-desktop.png') })
    await page.setViewportSize({ width: 390, height: 844 })
    await page.screenshot({ path: path.join(shots, 'articles-mobile.png') })
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1))

    await page.goto(base + '/#/account/settings')
    await page.getByText(/라오스 외 2개/).waitFor()
    await page.getByText(/330420\) 외 1개/).waitFor()
    await page.getByRole('link', { name: '월간 구독 설정 →', exact: true }).click()
    await page.getByRole('heading', { name: '구독 설정 / 수정' }).waitFor()
    assert.equal(await page.evaluate(() => document.activeElement.closest('label').textContent.includes('월간')), true)
    assert.equal(await page.locator('input:checked').count(), 0, 'following a link must not change subscriptions')
    await page.screenshot({ path: path.join(shots, 'subscription-mobile.png') })

    await page.setViewportSize({ width: 1280, height: 900 })
    await page.goto(base + '/#/account/reports')
    await page.getByRole('heading', { name: '리포트 센터', exact: true }).waitFor()
    assert.deepEqual(await page.locator('.chips a').allTextContents(), ['채팅에서 생성된 리포트', '주간리포트', '월간리포트', '분기리포트'])
    await page.getByRole('button', { name: /검수 리포트 1 삭제/ }).click()
    await page.getByRole('button', { name: '리포트 삭제', exact: true }).click()
    await page.locator('.toast').filter({ hasText: '삭제 실패 테스트' }).waitFor()
    assert.equal(await page.getByRole('dialog', { name: '리포트 삭제 확인' }).count(), 1)
    assert.equal(reports.length, 4)
    deleteFails = false
    await page.getByRole('button', { name: '리포트 삭제', exact: true }).click()
    await page.getByText('조회할 채팅에서 생성된 리포트가 없습니다.', { exact: true }).waitFor()
    await page.reload()
    await page.getByText('조회할 채팅에서 생성된 리포트가 없습니다.', { exact: true }).waitFor()
    await page.getByRole('link', { name: '새 리포트 만들기', exact: true }).click()
    await page.getByRole('textbox', { name: '질문 입력' }).waitFor()
    assert.equal(await page.locator('.chat-user').count(), 0)
    assert.ok(!reads.includes('old'))
    const input = page.getByRole('textbox', { name: '질문 입력' })
    await input.fill('새 리포트 질문')
    await input.press('Shift+Enter')
    await input.pressSequentially('줄바꿈 확인')
    assert.equal(await input.inputValue(), '새 리포트 질문\n줄바꿈 확인')
    assert.deepEqual(sent, [], 'Shift+Enter must not submit')
    await input.dispatchEvent('keydown', { key: 'Enter', code: 'Enter', isComposing: true })
    await input.dispatchEvent('keydown', { key: 'Enter', code: 'Enter', keyCode: 229 })
    assert.deepEqual(sent, [], 'IME confirmation must not submit')
    await input.press('Enter')
    await page.getByText('오프라인 검수 종료', { exact: true }).waitFor()
    assert.deepEqual(sent, ['new1'])
    assert.deepEqual(questions, ['새 리포트 질문\n줄바꿈 확인'])
    await page.getByRole('heading', { name: '새 리포트 질문 줄바꿈 확인', exact: true }).waitFor()
    await page.locator('.chat-list').getByText('새 리포트 질문 줄바꿈 확인', { exact: true }).waitFor()
    assert.equal(await page.locator('.chat-user').innerText(), '새 리포트 질문\n줄바꿈 확인')
    assert.equal(created, 1)
    await page.waitForURL('**/#/analysis?chat=new1')
    await page.reload()
    await page.getByRole('heading', { name: '새 리포트 질문 줄바꿈 확인', exact: true }).waitFor()
    assert.equal(created, 1, 'reload must reuse the new room instead of starting another')
    await page.goto(base + '/#/articles?page=13')
    await page.getByText('KOTRA 단신속보', { exact: true }).first().waitFor()
    await page.getByPlaceholder('검색어 또는 언론사명 입력').fill('파이낸셜뉴스')
    await page.getByRole('button', { name: '기사 검색', exact: true }).click()
    await page.waitForURL('**/#/articles?q=*')
    await page.getByText(/“파이낸셜뉴스” 검색 결과/).waitFor()
    assert.equal(articleSearches.at(-1).q, '파이낸셜뉴스')
    assert.equal(articleSearches.at(-1).page, '1')
    assert.deepEqual(errors, [])
    assert.deepEqual(unexpected, [])
    console.log('PASS issues #121/#126: beta regressions, multiline chat, IME, immediate title refresh and search page reset')
  } finally {
    await browser?.close()
    await server.close()
  }
})().catch(error => { console.error(error); process.exitCode = 1 })
