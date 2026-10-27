// 알림·순위 회귀 검사: 빌드·preview 후 node tests/notification-rank.cjs [URL]
const { chromium } = require('playwright')
const assert = require('node:assert/strict')
const fs = require('node:fs/promises')
const base = process.argv[2] || 'http://127.0.0.1:4173'
;(async () => {
  const browser = await chromium.launch(process.env.CHROMIUM ? { executablePath: process.env.CHROMIUM } : {})
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
    const errors = []
    page.on('pageerror', e => errors.push(e.message))
    const go = async path => { await page.goto(`${base}/#${path}`); await page.waitForTimeout(150) }
    const login = async (email, password) => {
      await go('/login')
      await page.fill('#login-email', email)
      await page.fill('#login-password', password)
      await page.getByRole('button', { name: '로그인', exact: true }).click()
      await page.waitForURL(url => !url.hash.includes('/login'))
    }
    const downloadRows = async () => {
      const pending = page.waitForEvent('download')
      await page.getByRole('button', { name: '엑셀 다운로드', exact: true }).click()
      const file = await pending
      const xml = await fs.readFile(await file.path(), 'utf8')
      return page.evaluate(xml => Array.from(new DOMParser().parseFromString(xml, 'application/xml').getElementsByTagName('Row'), row => Array.from(row.getElementsByTagName('Data'), d => d.textContent)), xml)
    }
    await login('demo@example.com', 'demo1234!')
    await page.evaluate(() => {
      const ns = JSON.parse(localStorage.getItem('bs2.notifications'))
      ns[0].read = true
      ns.unshift({ id: 'n2', type: 'breaking', title: '이전 속보', read: false })
      localStorage.setItem('bs2.notifications', JSON.stringify(ns))
      const settings = JSON.parse(localStorage.getItem('bs2.settings'))
      localStorage.setItem('bs2.settings', JSON.stringify({ ...settings, report: false, breaking: 'all', scope: 'linked' }))
    })
    await page.reload()
    await page.locator('button.bell').click()
    assert.doesNotMatch(await page.locator('.alert-pop').innerText(), /속보/)
    const saved = await page.evaluate(() => ({ ns: JSON.parse(localStorage.getItem('bs2.notifications')), settings: JSON.parse(localStorage.getItem('bs2.settings')) }))
    assert.equal(saved.ns.some(n => n.type === 'breaking'), false)
    assert.equal(saved.ns.find(n => n.id === 'n1').read, true)
    assert.equal(saved.settings.report, false)
    assert.equal('scope' in saved.settings || 'breaking' in saved.settings, false)
    await go('/account/settings?terms=open')
    assert.doesNotMatch(await page.locator('main').innerText(), /속보|관심 연결|알림 범위/)
    assert.doesNotMatch(await page.getByRole('dialog').innerText(), /속보/)
    await go('/account/notifications?tab=breaking&id=n2')
    assert.deepEqual(await page.locator('main .chips').first().locator('a').allTextContents(), ['전체', '급증 기사', '주요 이슈', '리포트 발행'])
    assert.doesNotMatch(await page.locator('main').innerText(), /이전 속보/)
    await go('/articles?tab=short')
    assert.ok(await page.getByRole('link', { name: '단신속보', exact: true }).count())
    console.log('PASS 이전 알림·설정 정리, 약관·탭, 단신속보 기사 유지')

    for (const [country, product, period] of [
      ['미국', '330499', '2025-01~2025-12'],
      ['인도네시아', '330499', '2025-01~2025-12'],
      ['인도', '330499', '2025-01~2025-12'],
      ['', '330499', '2025-07~2025-12'],
      ['미국', '', '2025-12~2025-12'],
      ['인도네시아', '', '2025-12~2025-12'],
    ]) {
      await go('/rank')
      await page.getByLabel('국가', { exact: true }).selectOption(country)
      await page.getByLabel('품목', { exact: true }).selectOption(product)
      await page.getByLabel('기간', { exact: true }).selectOption(period)
      await page.getByRole('button', { name: '순위 조회', exact: true }).click()
      if (country && product) {
        assert.match(await page.locator('main').innerText(), /최근 12개월 월별/)
        const rows = await downloadRows()
        assert.deepEqual(rows[0], ['월', '수출액(억 USD)', '수입액(억 USD)'])
        assert.equal(rows[1][0], '2025.01')
        assert.equal(rows.at(-1)[0], '2025.12')
        assert.equal(rows.length, 13)
      } else {
        const rows = await downloadRows()
        assert.equal(rows[0][0], '순위')
        assert.equal(rows[1][0], '1')
      }
      // 조회하지 않은 입력값 대신 실제 표시 중인 조건을 전달합니다.
      await page.getByLabel('국가', { exact: true }).selectOption('중국')
      await page.getByRole('link', { name: 'AI 분석 채팅 열기', exact: true }).click()
      await page.getByLabel('질문 입력').waitFor()
      const question = await page.getByLabel('질문 입력').inputValue()
      assert.ok(question.includes(country || '전체 국가'))
      assert.ok(question.includes(product || '전체 품목'))
      assert.ok(question.includes(period))
      await page.getByRole('button', { name: '전송', exact: true }).click()
      await page.waitForTimeout(100)
      const options = await page.evaluate(() => JSON.parse(localStorage.getItem('bs2.chats'))[0].messages.at(-1).options)
      assert.deepEqual([options.country, options.product, options.from, options.to], [country, product, ...period.split('~')])
    }
    console.log('PASS 월별·순위 엑셀, 조회 조건 전달과 AI 해석(전체 국가·전체 품목 포함)')
    await page.getByRole('button', { name: '로그아웃', exact: true }).click()
    await login('admin@example.com', 'admin1234!')
    await go('/admin/members/BS63')
    await page.getByText('회원이 직접 등록한 관심만 대조합니다.', { exact: true }).waitFor()
    assert.doesNotMatch(await page.locator('body').innerText(), /상위 5개|연결 국가/)
    assert.deepEqual(errors, [])
    console.log('PASS 관리자 관심 범위, 브라우저 오류 0건')
  } finally { await browser.close() }
})().catch(error => { console.error(error); process.exitCode = 1 })
