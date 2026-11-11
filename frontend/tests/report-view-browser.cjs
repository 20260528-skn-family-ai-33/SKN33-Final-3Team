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
    // 리포트 열람 화면의 비율은 PDF와 같이 소수 2자리(AGENT_DESIGN 2026-11 결정, report_templates.REPORT_DIGITS)
    for (const [mode, label, kpi, expected] of [
      ['MOM', '전월 대비', { mom_pct: 25, yoy_pct: null, previous_amount_usd: 160000000 }, '+25.00%'],
      ['CUSTOM', '비교 기간 대비', { comparison_pct: -20, yoy_pct: null }, '-20.00%'],
      ['YOY_PERIOD', '전년 동기 대비', { yoy_pct: 10 }, '+10.00%'],
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
      assert.equal(await previousCard.locator('p').nth(1).innerText(), mode === 'MOM' ? '1.60억 USD' : '—')
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
    // 승인한 채팅 결과로 만든 리포트(meta.freshness FROZEN, D-90): 결과표를 그대로 보이고 없는 섹션은 '포함되지 않음'.
    // 집계 카드의 '—'·'준비 중'을 그리지 않고, 만료될 수 있는 원본 결과 API(/results)를 부르지 않는다.
    {
      const trend = { result_id: 'result_t', kind: 'TREND', total_rows: 4,
        // 서버가 확정한 열 순서 그대로: 기간 합계(total_usd)가 월별 금액보다 앞에 있고, 같은 길이 비교라 compare_month가 월마다 다르다
        columns: [{ key: 'country_name', label: '국가', type: 'string' }, { key: 'hs_ver', label: 'HS 판본', type: 'string' },
          { key: 'total_usd', label: '기간 합계', type: 'number', unit: 'USD' }, { key: 'monthly_avg_usd', label: '월평균 금액', type: 'number', unit: 'USD' },
          { key: 'month', label: '기준월', type: 'string' }, { key: 'amount_usd', label: '수출액', type: 'number', unit: 'USD' },
          { key: 'compare_month', label: '비교 기준월', type: 'string' }, { key: 'compare_amount_usd', label: '비교 기간 금액', type: 'number', unit: 'USD' }],
        rows: [['중국', '2025-10', 221000000, 443000000], ['미국', '2025-10', 121000000, 243000000], ['중국', '2025-11', 222000000, 443000000], ['미국', '2025-11', 122000000, 243000000]]
          .map(([country_name, month, amount_usd, total_usd]) => ({ country_name, hs_ver: 'HS2022', total_usd, monthly_avg_usd: total_usd / 2, month, amount_usd,
            compare_month: month.replace('2025', '2024'), compare_amount_usd: 100000000 })) }
      // 250행 결과표: 웹도 PDF처럼 200행까지만 그리고 나머지는 엑셀로 안내한다
      const many = { result_id: 'result_m', kind: 'RANKING', total_rows: 250,
        columns: [{ key: 'rank', label: '순위', type: 'number' }, { key: 'name', label: '품목', type: 'string' }],
        rows: Array.from({ length: 250 }, (_, i) => ({ rank: i + 1, name: `ITEM-${i + 1}` })) }
      const ranking = { result_id: 'result_r', kind: 'RANKING', total_rows: 2,
        columns: [{ key: 'rank', label: '순위', type: 'number' }, { key: 'country_name', label: '국가', type: 'string' }, { key: 'export_usd', label: '수출액', type: 'number', unit: 'USD' }],
        rows: [{ rank: 1, country_name: '중국', export_usd: 6660000 }, { rank: 2, country_name: '미국', export_usd: 3660000 }] }
      // 이름이 같은 품목(0409·040900 모두 '천연꿀')은 HS 코드로 선을 나눈다
      const honey = { result_id: 'result_h', kind: 'TREND', total_rows: 4,
        columns: [{ key: 'name', label: '품목', type: 'string' }, { key: 'month', label: '기준월', type: 'string' }, { key: 'amount_usd', label: '수출액', type: 'number', unit: 'USD' }],
        rows: [['0409', '2025-10', 300000000], ['0409', '2025-11', 310000000], ['040900', '2025-10', 50000000], ['040900', '2025-11', 60000000]]
          .map(([hs_code, month, amount_usd]) => ({ country_code: 'US', hs_code, hs_ver: 'HS2022', name: '천연꿀', month, amount_usd })) }
      const missing = '해당 분석 결과에 포함되지 않음'
      const sections = [
        { section_code: 'SUMMARY', title: '핵심 요약', text: '승인한 분석 결과표를 기준으로 작성했습니다.', tables: [], articles: [] },
        { section_code: 'KPI', title: '주요 지표 및 월별 수출 추이', text: '승인한 분석 결과입니다.', kpi: null, tables: [trend, honey], articles: [] },
        { section_code: 'TOP_COUNTRIES', title: '주요 수출국', text: '승인한 분석 결과입니다.', kpi: null, tables: [ranking, many], articles: [] },
        { section_code: 'GROWTH_RISK', title: '수출 증가 감소 주요 시장', text: missing, kpi: null, tables: [], articles: [] },
        { section_code: 'LOCAL_ISSUES', title: '현지 이슈', text: missing, kpi: null, tables: [], articles: [] }]
      const page = await browser.newPage()
      const errors = [], requested = []
      page.on('pageerror', e => errors.push(e.message))
      await page.route('**/api/v1/**', route => {
        const pathname = new URL(route.request().url()).pathname
        requested.push(pathname)
        if (pathname.endsWith('/users/me')) return route.fulfill({ status: 401, json: { message: 'test' } })
        if (pathname.endsWith('/reports/1/status')) return route.fulfill({ json: { job: { status: 'READY' } } })
        if (pathname.endsWith('/reports/1')) return route.fulfill({ json: {
          report: { report_id: '1', report_type: 'RPT_ON_DEMAND', status: 'READY', title: '결과 기반 검수', created_at: '2026-11-09', available_formats: ['PDF'],
            conditions: { compare: 'NONE', hs_items: [{ hs_code: '330499', hs_ver: 'HS2022' }], country_codes: ['US', 'CN'], period: { start: '2025-10-01', end: '2025-12-31' } } },
          sections, meta: { sources: [], warnings: ['2025-12 일부 통계 미공개'], freshness: 'FROZEN' } } })
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
      const title = page.getByRole('heading', { name: '결과 기반 검수' })
      try { await title.waitFor() }
      catch (error) { console.error({ errors, text: await page.locator('body').innerText() }); throw error }
      const text = (await page.locator('article').innerText()).replace(/\s+/g, ' ')
      const header = (await title.locator('xpath=following-sibling::p[1]').innerText()).replace(/\s+/g, ' ').trim()
      assert.equal(header, '승인한 분석 결과로 작성 · HS6 330499 · 국가: US · CN')
      assert.equal(text.split(missing).length - 1, 2, '결과표에 없는 섹션 두 곳에 포함되지 않음 안내')
      for (const absent of ['—억 USD', '준비 중', '데이터 준비 중', '전체 항목 선택']) assert.ok(!text.includes(absent), `결과 기반 리포트에 '${absent}'가 보이면 안 된다`)
      for (const shown of ['221,000,000', '122,000,000', '443,000,000', 'HS2022', '2024-10', '6,660,000', '3,660,000', '2025-12 일부 통계 미공개']) assert.ok(text.includes(shown), `결과표 값 ${shown}`)
      // 월별 추이는 국가별 선 두 개(섞어 한 줄로 그리지 않는다)
      const legend = await page.locator('article .chips .lg').allInnerTexts()
      assert.deepEqual(legend.filter(t => ['중국', '미국'].includes(t.trim())).sort(), ['미국', '중국'])
      assert.ok(!legend.some(t => t.includes('2024-')), '비교 기준월로 선을 나누지 않는다: ' + legend.join(', '))
      // 선의 값은 월별 금액(억 USD)이다. 기간 합계(4.43·2.43)를 월마다 그리지 않는다
      assert.ok(legend.includes('천연꿀 (HS 0409)') && legend.includes('천연꿀 (HS 040900)'), '이름이 같은 품목은 HS 코드로 구분: ' + legend.join(', '))
      const points = (await page.locator('article svg circle title').allTextContents()).map(t => t.replace(/\s+/g, ' ').trim())
      assert.deepEqual(points.filter(t => t.startsWith('천연꿀')).sort(), ['천연꿀 (HS 040900) · 2025-10: 0.50', '천연꿀 (HS 040900) · 2025-11: 0.60', '천연꿀 (HS 0409) · 2025-10: 3.00', '천연꿀 (HS 0409) · 2025-11: 3.10'].sort())
      assert.deepEqual(points.filter(t => /^(미국|중국) · /.test(t)).sort(), ['미국 · 2025-10: 1.21', '미국 · 2025-11: 1.22', '중국 · 2025-10: 2.21', '중국 · 2025-11: 2.22'])
      // 저장된 결과표는 서버가 확정한 열을 그대로 보인다(채팅 화면의 숨김 규칙을 적용하지 않음)
      for (const head of ['HS 판본', '기간 합계', '월평균 금액', '비교 기준월', '비교 기간 금액']) assert.ok(text.includes(head), '결과표 열 ' + head)
      assert.ok(!requested.some(p => p.includes('/results/')), '원본 결과 API를 부르지 않는다: ' + requested.join(', '))
      assert.deepEqual(errors, [])
      assert.ok(text.includes('전체 250행 중 200행 표시 · 전체는 엑셀 파일'), '200행 상한 안내')
      assert.ok(text.includes('ITEM-200') && !text.includes('ITEM-201'), '201번째 행부터는 그리지 않는다')
      assert.ok(text.includes('전체 2행'), '상한 아래 표는 전체 행 수만 안내')
      console.log('PASS result-based report view')
      await page.close()
    }
  } finally { if (browser) await browser.close(); await server.close() }
})().catch(error => { console.error(error); process.exitCode = 1 })
