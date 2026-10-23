import { test } from 'node:test'
import assert from 'node:assert/strict'
import { canChangeAdminRole, isSuperAdmin, SUPER_ADMIN_EMAIL } from '../src/auth/roles.js'
test('최고 관리자만 정상 회원의 일반 관리자 권한을 변경할 수 있다', () => {
  const root = { email: SUPER_ADMIN_EMAIL, role: 'admin' }
  const user = { email: 'member@example.com', role: 'user' }
  assert.ok(isSuperAdmin(root))
  assert.equal(isSuperAdmin({ ...user, role: 'admin' }), false)
  assert.ok(canChangeAdminRole(root, user, 'admin'))
  assert.ok(canChangeAdminRole(root, { ...user, role: 'admin' }, 'user'))
  for (const actor of [null, user, { ...user, role: 'admin' }]) assert.equal(canChangeAdminRole(actor, user, 'admin'), false)
  for (const target of [null, root, { ...user, status: 'withdrawn' }, { ...user, status: 'blocked' }]) assert.equal(canChangeAdminRole(root, target, 'admin'), false)
  assert.equal(canChangeAdminRole(root, user, 'super_admin'), false)
})
