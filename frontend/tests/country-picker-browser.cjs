// Run: node tests/country-picker-browser.cjs. All API responses are synthetic; no DB writes.
// 국가 선택: 선택한 국가를 칩으로 보이고, 대륙 칩을 다시 누르면 그 대륙 국가만 해제한다(전체 삭제와 별개).
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
    const page = await browser.newPage()
    const errors = []
    page.on('pageerror', e => errors.push(e.message))
    await page.route('**/api/v1/**', route => {
      const pathname = new URL(route.request().url()).pathname
      if (pathname.endsWith('/reference/regions')) return route.fulfill({ json: { items: [
        { region_id: 1, parent_id: null, name_ko: '오세아니아', country_codes: ['AU', 'NZ'] },
        { region_id: 2, parent_id: null, name_ko: '아시아', country_codes: ['JP'] }] } })
      if (pathname.endsWith('/users/me')) return route.fulfill({ status: 401, json: { message: 'test' } })
      return route.fulfill({ status: 500, json: { message: 'Unexpected test API: ' + pathname } })
    })
    await page.route('**/country-picker-harness', route => route.fulfill({ contentType: 'text/html', body: `
      <div id="root"></div><script type="module">
      import RefreshRuntime from '/@react-refresh';
      RefreshRuntime.injectIntoGlobalHook(window);
      window.$RefreshReg$ = () => {}; window.$RefreshSig$ = () => type => type;
      window.__vite_plugin_react_preamble_installed__ = true;
      await import('/tests/country-picker-harness.jsx');
      </script>` }))
    await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/country-picker-harness`)
    const oceania = page.getByRole('button', { name: /오세아니아/ })
    await oceania.waitFor()
    const value = async () => (await page.getByTestId('value').innerText()).split(',').filter(Boolean).sort()
    const chips = async () => (await page.locator('.interest-summary li button').allInnerTexts()).map(t => t.replace(' ×', '').trim()).sort()

    // 선택한 국가는 아래 칩으로 보인다
    assert.deepEqual(await chips(), ['미국'])
    // 대륙을 누르면 그 대륙 국가 전체 추가, 칩이 선택 상태로 바뀐다
    await oceania.click()
    assert.deepEqual(await value(), ['뉴질랜드', '미국', '호주'])
    assert.equal(await oceania.getAttribute('aria-pressed'), 'true')
    assert.deepEqual(await chips(), ['뉴질랜드', '미국', '호주'])
    // 다른 대륙도 추가한 뒤 오세아니아를 다시 누르면 오세아니아 국가만 해제(미국·일본은 유지)
    await page.getByRole('button', { name: /아시아/ }).filter({ hasNotText: '오세아니아' }).click()
    await oceania.click()
    assert.deepEqual(await value(), ['미국', '일본'])
    assert.equal(await oceania.getAttribute('aria-pressed'), 'false')
    // 일부만 선택된 대륙을 누르면 빠진 국가를 채운다(해제하지 않음)
    await page.getByRole('button', { name: '호주 선택 삭제' }).waitFor({ state: 'detached' })
    await page.getByText('호주', { exact: true }).click()
    assert.deepEqual(await value(), ['미국', '일본', '호주'])
    await oceania.click()
    assert.deepEqual(await value(), ['뉴질랜드', '미국', '일본', '호주'])
    // 선택 칩을 누르면 그 국가만 삭제
    await page.getByRole('button', { name: '일본 선택 삭제' }).click()
    assert.deepEqual(await value(), ['뉴질랜드', '미국', '호주'])
    // 선택 국가 전체 삭제는 대륙과 상관없이 모두 비운다
    await page.locator('.interest-summary').getByRole('button', { name: '선택 국가 전체 삭제' }).click()
    assert.deepEqual(await value(), [])
    assert.deepEqual(errors, [])
    console.log('PASS country picker: selected chips, continent toggle, clear all')
  } finally { if (browser) await browser.close(); await server.close() }
})().catch(error => { console.error(error); process.exitCode = 1 })
