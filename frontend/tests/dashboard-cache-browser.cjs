// Real dashboard, synthetic APIs only. Test timing is deliberately controlled.
const assert = require('node:assert/strict')
const path = require('node:path')
const { chromium } = require('playwright')
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))
async function until(check) {
  for (let attempt = 0; attempt < 100; attempt++) {
    if (check()) return
    await delay(50)
  }
  assert.fail('Timed out waiting for expected API request')
}

;(async () => {
  const { createServer } = await import('vite')
  const server = await createServer({ root: path.resolve(__dirname, '..'), server: { host: '127.0.0.1', port: 0, proxy: {} } })
  await server.listen()
  let browser
  try {
    browser = await chromium.launch({ headless: true, channel: 'chrome' })
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' })
    page.setDefaultTimeout(10000)
    await page.clock.install()
    const errors = [], calls = [], shareCalls = [], pendingGraphs = [], pendingRanks = [], pendingTrade = [], pendingShares = []
    let holdGraphs = true, holdSix = false, failGraph = false, failAnnual = false, account = '1', version = 'v1', interestsVersion = '1'
    let availabilityMode = 'ok', availabilityCalls = 0, holdTrade = false, amountMultiplier = 1, holdShares = false
    page.on('pageerror', error => errors.push(error.message))
    await page.route('**/api/v1/**', async route => {
      const req = route.request(), url = new URL(req.url()), p = url.pathname.replace('/api/v1', ''), q = url.searchParams
      const reply = json => route.fulfill({ json })
      if (p === '/users/me') return reply({ user: { user_id: account, name: '테스트', email: `test${account}@example.org`, role: 'USER' }, csrf_token: 'test' })
      if (p === '/users/me/interests') {
        if (req.method() === 'PUT') interestsVersion = String(Number(interestsVersion) + 1)
        return reply({ interests: { hs_items: [], country_codes: interestsVersion === '1' ? ['US'] : ['CN'], landing_page: 'GLOBAL', version: interestsVersion } })
      }
      if (p === '/reference/countries') return reply({ items: [{ country_code: 'US', name_ko: '미국' }, { country_code: 'CN', name_ko: '중국' }] })
      if (p === '/config/options') return reply({ report_periods: [] })
      if (p === '/users/me/notification-settings') return reply({ settings: { notifications_enabled: false } })
      if (p === '/users/me/subscriptions') return reply({ items: [] })
      if (p === '/notifications/unread-count') return reply({ count: 0 })
      if (p === '/data/availability') {
        availabilityCalls++
        if (availabilityMode === 'network') return route.abort('failed')
        if (availabilityMode !== 'ok') return route.fulfill({ status: Number(availabilityMode), headers: { 'Retry-After': '30' }, json: { message: '기준월 오류 테스트' } })
        return reply({ trade_end_month: '2025-12', meta: { data_version: version } })
      }
      if (p === '/trade/rankings' || p === '/trade/trends') {
        calls.push({ p, q: Object.fromEntries(q) })
        if (holdTrade) await new Promise(resolve => pendingTrade.push(resolve))
        const start = q.get('period_start'), months = start === '2025-10' ? 3 : start === '2025-07' ? 6 : 12
        const meta = { period: { start, end: '2025-12' }, data_version: version }
        if (p.endsWith('rankings')) {
          if (holdSix && months === 6) await new Promise(resolve => pendingRanks.push(resolve))
          await delay(100)
          if (failAnnual && months === 12 && q.get('group_by') === 'HS6') return route.fulfill({ status: 503, json: { message: '공유 순위 오류 테스트' } })
          return reply({ items: Array.from({ length: 5 }, (_, i) => ({ rank: i + 1, hs_item: { hs_code: `330${String(months).padStart(2, '0')}${i}` }, hs_name: `${months}개월 품목 ${i}`, country_code: i ? 'CN' : 'US', export_amount_usd: months * 100000000 * amountMultiplier, import_amount_usd: 100000000 })), meta,
            conditions: { hs_items: [], country_codes: q.get('scope') === 'INTERESTED' ? [interestsVersion === '1' ? 'US' : 'CN'] : [] } })
        }
        if (q.get('view') === 'SPARKLINE') {
          // Real scope_filters rejects an explicit HS when only countries were saved.
          if (q.get('scope') === 'INTERESTED') return route.fulfill({ status: 400, json: { message: '등록한 관심 품목 범위 밖입니다.' } })
          if (holdGraphs) await new Promise(resolve => pendingGraphs.push(resolve))
          if (failGraph) return route.fulfill({ status: 503, json: { message: '그래프 오류 테스트' } })
          await delay(100)
        }
        return reply({ items: [{ month: start, export_amount_usd: 1, import_amount_usd: 1, previous_year_export_usd: 1, previous_year_import_usd: 1 }], meta })
      }
      if (p === '/analysis/market-shares') {
        const responseVersion = version
        shareCalls.push({ body: req.postDataJSON(), version: responseVersion })
        if (holdShares) await new Promise(resolve => pendingShares.push(resolve))
        return reply({ items: [{ label: '미국', country_code: 'US', share_pct: responseVersion === 'v1' ? 40 : responseVersion === 'v2' ? 60 : 70 }] })
      }
      if (['/exchange-rates', '/articles', '/notifications', '/reports', '/chat/sessions'].includes(p)) return reply({ items: [], page: { has_next: false } })
      throw new Error(`Unexpected API ${p}`)
    })
    await page.route('**/dashboard-harness', route => route.fulfill({ contentType: 'text/html', body: `
      <div id="root"></div><script type="module">
      import RefreshRuntime from '/@react-refresh'; RefreshRuntime.injectIntoGlobalHook(window);
      window.$RefreshReg$=()=>{}; window.$RefreshSig$=()=>type=>type;
      window.__vite_plugin_react_preamble_installed__=true;
      await import('/tests/dashboard-cache-harness.jsx');</script>` }))
    const base = `http://127.0.0.1:${server.httpServer.address().port}/dashboard-harness`
    const section = page.locator('.zone-exports'), cards = section.locator('.rank-card')
    const choose = months => section.getByRole('button', { name: `${months}개월`, exact: true }).click()
    const ready = async months => {
      await cards.first().getByText(`${months}개월 품목 0`, { exact: true }).waitFor()
      await section.locator('[aria-label="수출 추이 그래프를 불러오는 중입니다"]').first().waitFor({ state: 'hidden' })
      await section.locator('[aria-busy="true"]').first().waitFor({ state: 'hidden' })
      assert.equal(await cards.count(), 5)
      assert.equal(await section.getByRole('alert').count(), 0)
    }
    const tradeCount = () => calls.length
    await page.goto(base)
    await cards.first().getByText('3개월 품목 0', { exact: true }).waitFor()
    assert.equal(await section.getByRole('status', { name: '수출 추이 그래프를 불러오는 중입니다' }).count(), 5)
    holdGraphs = false; pendingGraphs.splice(0).forEach(resolve => resolve())
    await ready(3)
    console.log('PASS cards show amounts while graphs are still pending')
    for (const months of [6, 12]) { await choose(months); await ready(months) }
    const before = tradeCount()
    const sharesBefore = shareCalls.length
    for (const months of [3, 6, 12, 3]) { await choose(months); await ready(months) }
    await delay(150)
    assert.equal(tradeCount(), before)
    assert.equal(shareCalls.length, sharesBefore, 'Card period changes must not refetch the annual pie')
    assert.equal(calls.filter(c => c.p.endsWith('rankings') && c.q.group_by === 'HS6' && c.q.period_start === '2025-01').length, 1)
    console.log('PASS 3/6/12 return visits issue zero trade requests; 12-month table and card share one ranking')

    const regions = page.locator('.zone-overview, .zone-exports, .zone-tables')
    // Compare the data independently of the small refresh status announcement.
    const regionData = () => regions.evaluateAll(nodes => nodes.map(node => {
      const copy = node.cloneNode(true)
      copy.querySelectorAll('[role="status"]').forEach(status => status.remove())
      return copy.textContent
    }))
    const unchanged = await regionData()
    for (const mode of ['503', '429', 'network']) {
      availabilityMode = mode
      const beforePoll = availabilityCalls
      await page.clock.fastForward(60100)
      await until(() => availabilityCalls > beforePoll)
      await delay(100)
      assert.deepEqual(await regionData(), unchanged)
      assert.equal(await regions.locator('[role="alert"]').count(), 0)
      assert.equal(tradeCount(), before, 'A failed version poll must not discard trade caches')
    }
    availabilityMode = 'ok'
    const beforeRecovery = availabilityCalls
    await page.clock.fastForward(60100)
    await until(() => availabilityCalls > beforeRecovery)
    await delay(100)
    assert.equal(tradeCount(), before, 'Recovering the same version must not refetch trade data')
    assert.equal(shareCalls.length, sharesBefore, 'Failed polls and same-version recovery must preserve the pie')
    console.log('PASS 503, 429, network failure and same-version recovery preserve all four main regions')

    const layout = () => page.locator('.zone-overview h2, .zone-exports h2, .rank-card, .zone-tables .th, .zone-tables .tr').evaluateAll(nodes => nodes.map(node => {
      const rect = node.getBoundingClientRect()
      return { x: rect.x + window.scrollX, y: rect.y + window.scrollY, width: rect.width, height: rect.height }
    }))
    const beforeRefreshLayout = await layout()
    holdTrade = true
    await page.clock.fastForward(300100)
    await until(() => pendingTrade.length >= 4)
    await delay(100)
    assert.deepEqual(await regionData(), unchanged)
    assert.equal(await regions.locator('.skel').count(), 0)
    assert.deepEqual(await layout(), beforeRefreshLayout, 'Automatic refresh must not move headings, cards or table rows')
    assert.equal(await regions.getByRole('status').count(), 0, 'Automatic refresh must not insert live announcements')
    assert.ok(await regions.locator('[aria-busy="true"]').count() > 0)
    holdTrade = false; amountMultiplier = 2
    pendingTrade.splice(0).forEach(resolve => resolve())
    await cards.first().locator('[title="6"]').waitFor()
    await ready(3)
    assert.deepEqual(await layout(), beforeRefreshLayout, 'Completing automatic refresh must also preserve the layout')
    assert.ok(tradeCount() > before)
    console.log('PASS five-minute refresh preserves layout without live announcements and clears busy state')

    holdSix = true
    await choose(6)
    await until(() => pendingRanks.length > 0)
    assert.ok(pendingRanks.length > 0)
    await choose(3); await ready(3)
    holdSix = false; pendingRanks.splice(0).forEach(resolve => resolve())
    await delay(200)
    await ready(3)
    console.log('PASS a late response cannot replace the selected period')

    const beforeScope = tradeCount()
    await page.evaluate(() => { location.hash = '/?view=interest' })
    await until(() => tradeCount() > beforeScope)
    await ready(3)
    assert.ok(tradeCount() > beforeScope)
    const beforeProfile = tradeCount()
    failGraph = true
    await page.getByRole('button', { name: '관심 변경 테스트' }).click()
    await section.getByRole('alert').waitFor()
    assert.equal(await cards.count(), 5)
    assert.ok(tradeCount() > beforeProfile)
    failGraph = false
    const ranksBeforeRetry = calls.filter(c => c.p.endsWith('rankings')).length
    await section.getByRole('button', { name: '다시 불러오기' }).click()
    await ready(3)
    assert.equal(calls.filter(c => c.p.endsWith('rankings')).length, ranksBeforeRetry)
    assert.ok(calls.some(c => c.q.view === 'SPARKLINE' && c.q.scope !== 'INTERESTED' && c.q.country_codes === 'CN'))
    console.log('PASS scope/profile invalidate results and failed graphs retry without hiding cards or refetching ranks')

    const beforeVersion = tradeCount(), sharesBeforeVersion = shareCalls.length
    const pie = page.locator('.zone-tables')
    await pie.getByText('미국 40.0%', { exact: true }).waitFor()
    holdShares = true
    version = 'v2'
    await page.clock.fastForward(60100)
    await until(() => tradeCount() > beforeVersion)
    await ready(3)
    await until(() => pendingShares.length > 0)
    assert.ok(tradeCount() > beforeVersion)
    assert.equal(shareCalls.length, sharesBeforeVersion + 1)
    assert.deepEqual(shareCalls.at(-1).body, shareCalls.at(-2).body, 'Version is a local refresh key, not an unsupported API field')
    assert.equal(await pie.getByText('미국 40.0%', { exact: true }).count(), 0, 'Do not show the old pie beside a new-version table')
    // An older version response arriving late cannot replace the current pie.
    holdShares = false
    version = 'v3'
    await page.clock.fastForward(60100)
    await pie.getByText('미국 70.0%', { exact: true }).waitFor()
    pendingShares.splice(0).forEach(resolve => resolve())
    await delay(200)
    assert.equal(await pie.getByText('미국 60.0%', { exact: true }).count(), 0)
    await pie.getByText('미국 70.0%', { exact: true }).waitFor()
    console.log('PASS same-month data corrections refresh the pie and ignore a late older-version response')
    account = '2'
    const beforeAccount = tradeCount()
    await page.getByRole('button', { name: '계정 갱신 테스트' }).click()
    await until(() => tradeCount() > beforeAccount)
    await ready(3)
    assert.ok(tradeCount() > beforeAccount)
    assert.deepEqual(errors, [])
    console.log('PASS batch version and account changes invalidate cached data; no browser errors')

    failAnnual = true
    await page.reload()
    await ready(3)
    await choose(12)
    await section.getByRole('alert').waitFor()
    const tableError = page.locator('.zone-tables [role="alert"]').filter({ hasText: '공유 순위 오류 테스트' })
    await tableError.waitFor()
    failAnnual = false
    const annualCalls = () => calls.filter(c => c.p.endsWith('rankings') && c.q.group_by === 'HS6' && c.q.period_start === '2025-01').length
    const beforeSharedRetry = annualCalls()
    await tableError.getByRole('button', { name: '다시 불러오기' }).click()
    await ready(12)
    assert.equal(await tableError.count(), 0)
    assert.equal(annualCalls(), beforeSharedRetry + 1)
    await page.clock.fastForward(300100)
    await until(() => annualCalls() > beforeSharedRetry + 1)
    await ready(12)
    assert.equal(await tableError.count(), 0)
    assert.equal(annualCalls(), beforeSharedRetry + 2)
    await section.getByRole('status').filter({ hasText: '수출 상위 품목 정보를 갱신 중입니다.' }).waitFor({ state: 'hidden' })
    console.log('PASS retrying a shared ranking clears errors in both the cards and table')

    // A failed background refresh keeps the same-condition data and exposes retry.
    failAnnual = true
    await page.clock.fastForward(300100)
    await section.getByRole('alert').waitFor()
    await tableError.waitFor()
    assert.equal(await cards.count(), 5)
    assert.equal(await regions.locator('.skel').count(), 0)
    failAnnual = false; holdTrade = true
    await tableError.getByRole('button', { name: '다시 불러오기' }).click()
    await until(() => pendingTrade.length > 0)
    assert.equal(await cards.count(), 5)
    assert.equal(await regions.locator('.skel').count(), 0)
    const cardRefresh = section.getByRole('status').filter({ hasText: '수출 상위 품목 정보를 갱신 중입니다.' })
    const tableRefresh = page.locator('.zone-tables').getByRole('status').filter({ hasText: '품목별 수출입 정보를 갱신 중입니다.' })
    await cardRefresh.waitFor()
    await tableRefresh.waitFor()
    assert.equal(await section.getByRole('alert').count(), 0)
    holdTrade = false; amountMultiplier = 3
    pendingTrade.splice(0).forEach(resolve => resolve())
    await cards.first().locator('[title="36"]').waitFor()
    await ready(12)
    await cardRefresh.waitFor({ state: 'hidden' })
    await tableRefresh.waitFor({ state: 'hidden' })
    console.log('PASS manual retry preserves data and announces progress in both shared regions until completion')

    const afterRetryLayout = await layout()
    holdTrade = true
    await page.clock.fastForward(300100)
    await until(() => pendingTrade.length >= 4)
    assert.equal(await regions.getByRole('status').count(), 0, 'The next automatic refresh must not inherit the manual announcement')
    assert.deepEqual(await layout(), afterRetryLayout)
    holdTrade = false
    pendingTrade.splice(0).forEach(resolve => resolve())
    await ready(12)
    console.log('PASS automatic refresh after a manual retry remains silent and does not shift content')

    availabilityMode = '503'
    const beforeInitialFailure = tradeCount()
    await page.reload()
    await section.getByRole('alert').waitFor()
    assert.equal(await cards.count(), 0)
    assert.equal(tradeCount(), beforeInitialFailure)
    availabilityMode = 'ok'
    await section.getByRole('button', { name: '다시 불러오기' }).click()
    await ready(3)
    assert.deepEqual(errors, [])
    console.log('PASS first availability failure remains visible and can be retried')
  } finally { await browser?.close(); await server.close() }
})().catch(error => { console.error(error); process.exitCode = 1 })
