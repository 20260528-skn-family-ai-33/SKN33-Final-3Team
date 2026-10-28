import { useState } from 'react'
import { useApp } from '../store'
import { Btn, Modal, Select } from '../components/ui'
import { canChangeAdminRole, isAdmin } from '../auth/roles'

export default function AdminRoles() {
  const { accounts, session, changeAdminRole } = useApp()
  const candidates = accounts.filter(a => canChangeAdminRole(session, a, 'admin'))
  const [email, setEmail] = useState('')
  const [confirm, setConfirm] = useState(null)
  const [error, setError] = useState('')
  const target = candidates.find(a => a.email === email)
  if (!isAdmin(session)) return <section className="card"><h2 className="t2">관리자 권한 관리</h2><p className="muted" style={{ marginTop: 12 }}>관리자만 관리자 권한을 부여하거나 회수할 수 있습니다.</p></section>
  const submit = () => {
    if (!changeAdminRole(confirm.email, confirm.role)) setError('권한을 변경할 수 없습니다. 대상 계정과 권한을 확인해 주세요.')
    setConfirm(null)
  }
  return <section className="card">
    <h2 className="t2">관리자 권한 관리</h2>
    <p className="note" style={{ marginTop: 8 }}>관리자는 다른 회원의 권한을 부여하거나 회수할 수 있습니다. 자기 계정은 변경할 수 없습니다.</p>
    <label className="lbl" htmlFor="admin-role-member" style={{ marginTop: 20 }}>대상 회원</label>
    <Select id="admin-role-member" value={email} onChange={value => { setEmail(value); setError('') }} options={[['', '회원을 선택하세요'], ...candidates.map(a => [a.email, `${a.name} · ${a.email} · ${a.role === 'admin' ? '관리자' : '일반 회원'}`])]} />
    {target && <div style={{ marginTop: 16 }}>
      <p className="note">현재 권한: {target.role === 'admin' ? '관리자' : '일반 회원'}</p>
      <Btn kind={target.role === 'admin' ? 'danger' : 'red'} style={{ marginTop: 10 }} onClick={() => setConfirm({ email: target.email, role: target.role === 'admin' ? 'user' : 'admin' })}>{target.role === 'admin' ? '관리자 권한 회수' : '관리자 권한 부여'}</Btn>
    </div>}
    {error && <p className="field-err" role="alert">{error}</p>}
    {confirm && <Modal open label="관리자 권한 변경 확인" onClose={() => setConfirm(null)}>
      <h2 className="t2">관리자 권한을 {confirm.role === 'admin' ? '부여' : '회수'}할까요?</h2>
      <p style={{ marginTop: 16 }}>{confirm.email}</p>
      <p className="muted" style={{ marginTop: 8 }}>{confirm.role === 'admin' ? '관리자 페이지를 사용할 수 있으며 마이페이지는 표시되지 않습니다. 다른 회원의 권한을 변경할 수 있습니다.' : '일반 회원으로 변경되며 관리자 페이지에 접근할 수 없습니다.'}</p>
      <div style={{ display: 'flex', gap: 10, marginTop: 24 }}><Btn onClick={() => setConfirm(null)}>취소</Btn><Btn kind="red" onClick={submit}>확인</Btn></div>
    </Modal>}
  </section>
}
