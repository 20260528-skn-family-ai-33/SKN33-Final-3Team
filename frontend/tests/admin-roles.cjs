const { chromium } = require('playwright')
const assert = require('node:assert/strict')
const base = process.argv[2] || 'http://127.0.0.1:4184'
;(async () => {
  const browser = await chromium.launch(process.env.CHROMIUM ? { executablePath: process.env.CHROMIUM } : {})
  try {
    const page = await browser.newPage()
    const errors = []
    page.on('pageerror', e => errors.push(String(e)))
    const go = async path => { await page.goto(`${base}/#${path}`); await page.waitForTimeout(100) }
    const login = async (email, password) => {
      await go('/login')
      await page.fill('#login-email', email)
      await page.fill('#login-password', password)
      await page.getByRole('button', { name: '로그인', exact: true }).click()
      await page.waitForTimeout(200)
    }
    const logout = async () => { await page.getByRole('button', { name: '로그아웃', exact: true }).click() }
    await login('admin@example.com', 'admin1234!')
    assert.match(page.url(), /#\/admin$/)
    assert.equal(await page.getByRole('link', { name: '마이페이지', exact: true }).count(), 0)
    await go('/account/profile')
    await page.waitForURL('**/#/admin')
    await go('/')
    assert.equal(await page.locator('header a[href^="#/account"], footer a[href^="#/account"]').count(), 0)
    await go('/admin/members/BS40')
    assert.equal(await page.locator('#admin-role-member option[value="admin@example.com"]').count(), 0)
    await page.selectOption('#admin-role-member', 'demo@example.com')
    await page.getByRole('button', { name: '관리자 권한 부여', exact: true }).click()
    await page.getByRole('dialog').getByRole('button', { name: '취소', exact: true }).click()
    assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('bs2.accounts')).find(a => a.email === 'demo@example.com').role), 'user')
    await page.getByRole('button', { name: '관리자 권한 부여', exact: true }).click()
    await page.getByRole('dialog').getByRole('button', { name: '확인', exact: true }).click()
    await page.reload()
    await page.selectOption('#admin-role-member', 'demo@example.com')
    assert.equal(await page.getByRole('button', { name: '관리자 권한 회수', exact: true }).count(), 1)
    await logout()
    await login('demo@example.com', 'demo1234!')
    assert.match(page.url(), /#\/admin$/)
    for (const path of ['/account/profile', '/account/password', '/account/notifications', '/account/reports']) {
      await go(path)
      await page.waitForURL('**/#/admin')
      assert.equal(await page.getByRole('link', { name: '마이페이지', exact: true }).count(), 0)
    }
    await go('/admin/members/BS50')
    assert.equal(await page.getByRole('link', { name: '권한 변경', exact: true }).count(), 0)
    for (const id of ['BS52','BS53','BS55','BS56','BS57']) {
      await go('/admin/members/' + id)
      assert.equal(await page.locator('#admin-role-member').count(), 0)
      assert.match(await page.locator('main').innerText(), /최고 관리자만/)
    }
    await logout()
    await login('admin@example.com', 'admin1234!')
    await go('/admin/members/BS40')
    await page.selectOption('#admin-role-member', 'demo@example.com')
    await page.getByRole('button', { name: '관리자 권한 회수', exact: true }).click()
    await page.getByRole('dialog').getByRole('button', { name: '확인', exact: true }).click()
    await logout()
    await login('demo@example.com', 'demo1234!')
    assert.equal(await page.getByRole('link', { name: '마이페이지', exact: true }).count(), 1)
    await go('/account/profile')
    assert.match(page.url(), /account\/profile/)
    await go('/admin')
    assert.match(await page.locator('main').innerText(), /관리자 권한이 필요한/)
    assert.deepEqual(errors, [])
    console.log('PASS: 최고 관리자 단일 지정, 부여·취소·회수·재로그인, 마이페이지 차단, 권한 변경 직접 접근 차단, 일반 회원 유지')
  } finally { await browser.close() }
})().catch(e => { console.error(e); process.exit(1) })
