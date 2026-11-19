// Real ranking component with synthetic APIs only.
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
    const errors = [], requests = []
    page.on('pageerror', e => errors.push(e.message))
    const item = code => ({ hs_code: code, depth: code.length, name_ko: `품목 ${code}`, hs_ver: 'HS2022' })
    await page.route('**/api/v1/**', route => {
      const url = new URL(route.request().url()), p = url.pathname.replace('/api/v1', '')
      if (p === '/users/me') return route.fulfill({ status: 401, json: {} })
      if (p === '/data/availability') return route.fulfill({ json: { trade_start_month: '2024-01', trade_end_month: '2025-12' } })
      if (p === '/reference/hs-codes') return route.fulfill({ json: { items: [item(url.searchParams.get('q'))], match_type: 'EXACT' } })
      if (p.endsWith('/children')) return route.fulfill({ json: { items: ['330410', '330499'].map(item) } })
      if (p === '/trade/rankings' || p === '/trade/trends') {
        requests.push(url)
        return route.fulfill({ json: { result_id: 'multi', items: p.endsWith('trends')
          ? [{ month: '2025-12', export_amount_usd: 3000, import_amount_usd: 2000 }]
          : [{ rank: 1, hs_item: { hs_code: '330410' }, hs_name: '품목 330410', export_amount_usd: 3000, import_amount_usd: 2000 }], page: { total: 1, page_size: 20 } } })
      }
      if (p === '/results/multi/file') return route.fulfill({ body: 'synthetic xlsx' })
      errors.push('Unexpected API: ' + p)
      return route.fulfill({ status: 500, json: {} })
    })
    await page.route('**/rank-products-harness', route => route.fulfill({ contentType: 'text/html', body: `
      <div id="root"></div><script type="module">
      import RefreshRuntime from '/@react-refresh'; RefreshRuntime.injectIntoGlobalHook(window);
      window.$RefreshReg$=()=>{}; window.$RefreshSig$=()=>type=>type;
      window.__vite_plugin_react_preamble_installed__=true;
      await import('/tests/country-picker-harness.jsx');</script>` }))
    await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/rank-products-harness#rank?country=미국&product=330499`)
    const input = page.getByRole('combobox', { name: '품목 검색' })
    const choose = async code => {
      await input.fill(code)
      await page.getByRole('option').filter({ hasText: `HS${code.length} ${code}` }).click()
    }
    const selected = () => page.getByRole('list', { name: '선택한 품목' })
    const submit = async () => {
      const req = page.waitForRequest(r => /\/trade\/(rankings|trends)/.test(r.url()))
      await page.getByRole('button', { name: '순위 조회', exact: true }).click()
      return new URL((await req).url())
    }
    await selected().getByRole('button', { name: 'HS 330499 선택 해제' }).waitFor()
    await choose('330410')
    await choose('330410') // duplicate selection must not duplicate query codes
    assert.equal(await selected().getByRole('button').count(), 2)
    const multi = await submit()
    assert.ok(multi.pathname.endsWith('/rankings'))
    assert.deepEqual(multi.searchParams.getAll('hs_codes'), ['330499', '330410'])
    assert.deepEqual(multi.searchParams.getAll('country_codes'), ['US'])
    assert.equal(multi.searchParams.get('group_by'), 'HS6')
    assert.equal(multi.searchParams.has('hs_code'), false)
    await page.getByText('조회 품목:', { exact: false }).waitFor()
    const chat = page.getByRole('link', { name: /AI챗봇 열기/ })
    assert.match(decodeURIComponent(await chat.getAttribute('href')), /HS 330499.*HS 330410/)
    // Unapplied changes must not change the displayed result's chat/export conditions.
    await choose('850440')
    assert.doesNotMatch(decodeURIComponent(await chat.getAttribute('href')), /850440/)
    const download = page.waitForEvent('download')
    await page.getByRole('button', { name: '엑셀 다운로드' }).click()
    assert.match((await download).suggestedFilename(), /HS330499 외 1개_품목순위/)
    await page.getByRole('button', { name: 'HS4 단위로 검색' }).click()
    await choose('3304')
    await selected().getByRole('button', { name: 'HS 3304 선택 해제', exact: true }).waitFor()
    assert.equal(await selected().getByRole('button').count(), 2) // other group preserved
    await page.getByRole('group', { name: '세부 품목', exact: true }).getByRole('button', { name: /330410/ }).click()
    await page.getByRole('group', { name: '세부 품목', exact: true }).getByRole('button', { name: /330499/ }).click()
    assert.equal(await selected().getByRole('button').count(), 3)
    assert.equal(await selected().getByRole('button', { name: 'HS 3304 선택 해제', exact: true }).count(), 0)
    await selected().getByRole('button', { name: 'HS 850440 선택 해제' }).click()
    await selected().getByRole('button', { name: 'HS 330410 선택 해제' }).click()
    const single = await submit()
    assert.ok(single.pathname.endsWith('/trends'))
    assert.equal(single.searchParams.get('hs_code'), '330499')
    assert.equal(single.searchParams.has('hs_codes'), false)
    await page.getByRole('button', { name: '통합 검색' }).click()
    for (let i = 0; i < 20; i++) await choose(String(850400 + i))
    await submit()
    await page.getByText('AI챗봇은 국가·품목을 20개까지 받을 수 있습니다.', { exact: false }).first().waitFor()
    assert.equal(await page.getByRole('link', { name: /AI챗봇 열기/ }).getAttribute('aria-disabled'), 'true')
    await page.setViewportSize({ width: 390, height: 844 })
    const dir = path.resolve(__dirname, '../../tmp/issue134-browser')
    fs.mkdirSync(dir, { recursive: true })
    await page.screenshot({ path: path.join(dir, 'rank-mobile.png'), fullPage: true })
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
    await page.getByRole('button', { name: '품목 선택 해제', exact: true }).click()
    assert.equal(await selected().count(), 0)
    assert.deepEqual(errors, [])
    console.log('PASS multi-product ranking/trend queries, HS4/HS6 replacement, chips, applied chat/export conditions, chat limit, mobile layout')
  } finally { if (browser) await browser.close(); await server.close() }
})().catch(error => { console.error(error); process.exitCode = 1 })
