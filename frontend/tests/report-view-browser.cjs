// Run: node tests/report-view-browser.cjs. All API responses are synthetic; no DB writes.
const assert = require('node:assert/strict')
const path = require('node:path')
const { chromium } = require('playwright')

;(async () => {
  const { createServer } = await import('vite')
  const server = await createServer({ root: path.resolve(__dirname, '..'),
    server: { host: '127.0.0.1', port: 0, strictPort: false } })
  await server.listen()
  let browser
  try {
    browser = await chromium.launch({ headless: true })
    for (const [mode, label, kpi, expected] of [
      ['MOM', '전월 대비', { mom_pct: 25, yoy_pct: null, previous_amount_usd: 160000000 }, '+25.0%'],
      ['CUSTOM', '비교 기간 대비', { comparison_pct: -20, yoy_pct: null }, '-20.0%'],
      ['YOY_PERIOD', '전년 동기 대비', { yoy_pct: 10 }, '+10.0%'],
      ['NONE', '비교 없음', { comparison_pct: 99, yoy_pct: 99 }, '—'],
      ['CUSTOM', '비교 기간 대비', { yoy_pct: 99 }, '—'],
    ]) {
      const page = await browser.newPage()
      const errors = []
      page.on('pageerror', e => errors.push(e.message))
      await page.route('**/api/v1/**', route => {
        const pathname = new URL(route.request().url()).pathname
        if (pathname.endsWith('/users/me')) return route.fulfill({ status: 401, json: { message: 'test' } })
        if (pathname.endsWith('/reports/1/status')) return route.fulfill({ json: { job: { status: 'READY' } } })
        if (pathname.endsWith('/reports/1')) return route.fulfill({ json: {
          report: { report_id: '1', report_type: 'RPT_ON_DEMAND', status: 'READY', title: '비교 검수',
            created_at: '2026-10-29', available_formats: [], conditions: { compare: mode,
              hs_items: [], country_codes: [], period: { start: '2025-12-01', end: '2025-12-31' } } },
          sections: [{ section_code: 'KPI', kpi: { amount_usd: 200000000, ...kpi }, tables: [] }],
        } })
        if (pathname.endsWith('/reports')) return route.fulfill({ json: { items: [], page: { has_next: false } } })
        return route.fulfill({ status: 500, json: { message: 'Unexpected test API: ' + pathname } })
      })
      await page.route('**/report-view-harness', route => route.fulfill({ contentType: 'text/html', body: `
        <div id="root"></div><script type="module">
        import RefreshRuntime from '/@react-refresh';
        RefreshRuntime.injectIntoGlobalHook(window);
        window.$RefreshReg$ = () => {}; window.$RefreshSig$ = () => type => type;
        window.__vite_plugin_react_preamble_installed__ = true;
        await import('/tests/report-view-harness.jsx');
        </script>` }))
      await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/report-view-harness`)
      try { await page.getByRole('heading', { name: '주요 지표', exact: false }).waitFor() }
      catch (error) { console.error({ errors, text: await page.locator('body').innerText() }); throw error }
      const caption = page.locator('p.muted').filter({ hasText: new RegExp('^' + label + '$') })
      assert.equal(await caption.count(), 1, `${mode}: expected label ${label}`)
      assert.equal(await caption.locator('..').locator('p').nth(1).innerText(), expected)
      const previousCard = page.locator('p.note').filter({ hasText: /^비교 기간 합계$/ }).locator('..')
      assert.equal(await previousCard.locator('p').nth(1).innerText(), mode === 'MOM' ? '1.6억 USD' : '—')
      assert.deepEqual(errors, [])
      console.log(`PASS ${mode}: ${label} ${expected}`)
      await page.close()
    }
    // 머리글의 항목 수: 주간(기사만 있는 NEWS 섹션)은 수출 분석 항목을 고르는 리포트가 아니라 개수를 쓰지 않는다.
    for (const [type, sections, expected] of [
      ['RPT_WEEKLY', [{ section_code: 'NEWS', articles: [] }], 'HS6 330499 · 국가: US'],
      ['RPT_ON_DEMAND', ['KPI', 'TOP_COUNTRIES', 'GROWTH_RISK', 'LOCAL_ISSUES'].map(section_code => ({ section_code, kpi: {}, tables: [], articles: [] })),
        '전체 항목 선택 · HS6 330499 · 국가: US'],
      ['RPT_ON_DEMAND', [{ section_code: 'KPI', kpi: {}, tables: [] }], '1개 항목 선택 · HS6 330499 · 국가: US'],
    ]) {
      const page = await browser.newPage()
      const errors = []
      page.on('pageerror', e => errors.push(e.message))
      await page.route('**/api/v1/**', route => {
        const pathname = new URL(route.request().url()).pathname
        if (pathname.endsWith('/users/me')) return route.fulfill({ status: 401, json: { message: 'test' } })
        if (pathname.endsWith('/reports/1/status')) return route.fulfill({ json: { job: { status: 'READY' } } })
        if (pathname.endsWith('/reports/1')) return route.fulfill({ json: {
          report: { report_id: '1', report_type: type, status: 'READY', title: '머리글 검수', created_at: '2026-10-29', available_formats: [],
            conditions: { compare: 'YOY_PERIOD', hs_items: [{ hs_code: '330499', hs_ver: 'HS2022' }], country_codes: ['US'],
              period: { start: '2025-12-15', end: '2025-12-21' } } },
          sections,
        } })
        if (pathname.endsWith('/reports')) return route.fulfill({ json: { items: [], page: { has_next: false } } })
        return route.fulfill({ status: 500, json: { message: 'Unexpected test API: ' + pathname } })
      })
      await page.route('**/report-view-harness', route => route.fulfill({ contentType: 'text/html', body: `
        <div id="root"></div><script type="module">
        import RefreshRuntime from '/@react-refresh';
        RefreshRuntime.injectIntoGlobalHook(window);
        window.$RefreshReg$ = () => {}; window.$RefreshSig$ = () => type => type;
        window.__vite_plugin_react_preamble_installed__ = true;
        await import('/tests/report-view-harness.jsx');
        </script>` }))
      await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/report-view-harness`)
      const title = page.getByRole('heading', { name: '머리글 검수' })
      try { await title.waitFor() }
      catch (error) { console.error({ errors, text: await page.locator('body').innerText() }); throw error }
      const line = (await title.locator('xpath=following-sibling::p[1]').innerText()).replace(/\s+/g, ' ').trim()
      assert.equal(line, expected, `${type} ${sections.length}: header line`)
      assert.deepEqual(errors, [])
      console.log(`PASS header ${type} ${sections.length}: ${line}`)
      await page.close()
    }
  } finally { if (browser) await browser.close(); await server.close() }
})().catch(error => { console.error(error); process.exitCode = 1 })
