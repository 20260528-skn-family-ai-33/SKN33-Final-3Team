// shortcut: 브라우저 시연용 권한이다. 서버 연동 시 유일 최고 관리자와 변경 권한을 서버에서도 검증해야 한다.
export const SUPER_ADMIN_EMAIL = 'admin@example.com'
export const isSuperAdmin = account => account?.email === SUPER_ADMIN_EMAIL && account?.role === 'admin'
export function canChangeAdminRole(actor, target, role) {
  return isSuperAdmin(actor) && !!target && target.email !== SUPER_ADMIN_EMAIL
    && (!target.status || target.status === 'active' || target.status === 'normal')
    && ['user', 'admin'].includes(role)
}
