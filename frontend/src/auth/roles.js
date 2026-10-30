// UI visibility follows the server role; every mutation is authorized again by the API.
export const isAdmin = account => account?.serverRole === 'ADMIN'
export function canChangeAdminRole(actor, target, role) {
  return isAdmin(actor) && !!target && !!actor.user_id && !!target.user_id
    && String(actor.user_id) !== String(target.user_id)
    && target.account_status === 'ACTIVE' && ['user', 'admin'].includes(role)
}
