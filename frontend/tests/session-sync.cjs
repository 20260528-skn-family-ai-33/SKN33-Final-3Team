const assert = require('node:assert/strict')

// 관찰 탭에는 조작을 하지 않는다. 버튼이 이미 떠 있거나 세션 조회만 일어나도 통과하지 않도록 한다.
async function expectSessionReload(tab, status, act) {
  const [, response] = await Promise.all([
    tab.waitForEvent('load', { timeout: 15000 }),
    tab.waitForResponse(r => new URL(r.url()).pathname === '/api/v1/users/me' && r.request().method() === 'GET', { timeout: 15000 }),
    act(),
  ])
  assert.equal(await tab.evaluate(() => performance.getEntriesByType('navigation')[0]?.type), 'reload')
  assert.equal(response.status(), status)
  return response
}

async function checkSessionSync(page, base, email, password, ok) {
  const actor = await page.context().newPage()
  const login = actor.getByRole('button', { name: '로그인', exact: true })
  const logout = actor.getByRole('button', { name: '로그아웃', exact: true })
  const peerLogin = page.getByRole('button', { name: '로그인', exact: true })
  const fillLogin = async () => {
    await actor.locator('#login-email').fill(email)
    await actor.locator('#login-password').fill(password)
  }
  try {
    await actor.goto(base+'/#/login')
    await fillLogin()
    const response = await expectSessionReload(page, 200, () => login.click())
    assert.equal((await response.json()).user.email, email)
    await page.getByRole('button', { name: '로그아웃', exact: true }).waitFor()
    ok('다른 탭 로그인 후 기존 탭 reload 및 현재 계정 복원')

    await expectSessionReload(page, 401, () => logout.click())
    await page.waitForURL('**/#/login')
    await peerLogin.waitFor()
    ok('다른 탭 로그아웃 후 기존 탭 reload 및 로그인 화면 전환')

    await fillLogin()
    await expectSessionReload(page, 200, () => login.click())
    await logout.waitFor()
    await page.goto(base+'/#/account/profile')
    await page.getByRole('button', { name: '로그아웃', exact: true }).waitFor()
    await actor.goto(base+'/#/account/password')
    await actor.locator('#pw-email').fill(email)
    await actor.getByRole('button', { name: '이메일 확인' }).click()
    // Chrome 자동완성은 React change 이벤트 없이 현재 비밀번호 입력값을 바꾸기도 한다.
    await actor.locator('#pw-current').evaluate((input, value) => { input.value = value }, password)
    await actor.locator('#pw-next').fill('Changed!456')
    await actor.locator('#pw-confirm').fill('Changed!456')
    await expectSessionReload(page, 401, () => actor.getByRole('button', { name: '변경 저장' }).click())
    await page.waitForURL('**/#/login')
    await peerLogin.waitFor()
    ok('다른 탭 비밀번호 변경 후 기존 탭 reload 및 재로그인 화면 전환')
  } finally {
    await actor.close()
  }
}

module.exports = { checkSessionSync }
