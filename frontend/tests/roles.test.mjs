import { test } from 'node:test'
import assert from 'node:assert/strict'
import { canChangeAdminRole, isAdmin } from '../src/auth/roles.js'
test('이메일이 아닌 서버 ADMIN 권한으로 관리 UI를 구분한다', () => {
  const root = { user_id: '1', email: 'admin@example.com', serverRole: 'ADMIN', account_status: 'ACTIVE' }
  const member = { user_id: '2', email: 'member@example.com', serverRole: 'USER', account_status: 'ACTIVE' }
  assert.equal(isAdmin({ email: 'admin@example.com', role: 'admin' }), false)
  assert.ok(isAdmin(root)); assert.ok(canChangeAdminRole(root, member, 'admin'))
  assert.equal(canChangeAdminRole(member, root, 'user'), false)
  assert.equal(canChangeAdminRole(root, root, 'user'), false)
  assert.equal(canChangeAdminRole(root, { ...member, account_status: 'RESTRICTED' }, 'admin'), false)
})
