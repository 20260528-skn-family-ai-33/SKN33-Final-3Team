import { useState } from 'react'
import { api } from '../api/client'
import { useQuery, QueryState, Value } from '../api/hooks'
import { Btn, Modal, TextField } from '../components/ui'
import { useApp } from '../store'
export default function ServerDetail({ type, id }) {
  const { session, run, notify } = useApp()
  const path = type === 'user' ? `/admin/users/${id}` : `/admin/articles/${id}`
  const result = useQuery(path)
  const [action, setAction] = useState(null), [reason, setReason] = useState(''), [busy, setBusy] = useState(false)
  const submit = async () => {
    if (busy) return
    setBusy(true)
    await run(async () => {
      await api(`${path}/${action.field}`, { method: 'PATCH', body: { [action.key]: action.value, expected_version: result.data.version, confirmed: true, reason } })
      setAction(null); setReason(''); result.reload(); notify('변경과 감사 이력 저장을 완료했습니다.')
    }); setBusy(false)
  }
  return <section className="card"><QueryState state={result}>{data => {
    const record = type === 'user' ? data.member : data.article
    return <><h2 className="t2">{type === 'user' ? '회원 상세' : '기사 내용·출처'}</h2>
      <div style={{ display: 'grid', gridTemplateColumns: '130px 1fr', gap: 12, marginTop: 20 }}>{Object.entries(type === 'user' ? { 이메일: record.email, 이름: record.name, 권한: record.role, 상태: record.account_status, '최근 로그인': record.last_login_at } : { 제목: record.title, 출처: record.source_code, 발행일: record.published_date, 노출: data.moderation.visibility }).map(([label, value]) => <div key={label} style={{ display: 'contents' }}><span className="muted">{label}</span><b><Value value={value} /></b></div>)}</div>
      {data.body_masked && <p style={{ whiteSpace: 'pre-wrap', marginTop: 20 }}>{data.body_masked}</p>}
      <div style={{ display: 'flex', gap: 10, marginTop: 24 }}>
        {type === 'user' && record.user_id !== session.user_id && record.account_status !== 'WITHDRAWN' && <Btn kind="danger" onClick={() => setAction({ field: 'status', key: 'status', value: record.account_status === 'ACTIVE' ? 'RESTRICTED' : 'ACTIVE', label: record.account_status === 'ACTIVE' ? '회원 이용 제한' : '회원 이용 제한 해제' })}>이용 상태 변경</Btn>}
        {type === 'user' && session.serverRole === 'ADMIN' && record.user_id !== session.user_id && record.account_status === 'ACTIVE' && <Btn kind="red" onClick={() => setAction({ field: 'role', key: 'role', value: record.role === 'ADMIN' ? 'USER' : 'ADMIN', label: record.role === 'ADMIN' ? '관리자 권한 회수' : '관리자 권한 부여' })}>관리자 권한 변경</Btn>}
        {type === 'article' && <Btn kind="danger" onClick={() => setAction({ field: 'visibility', key: 'visibility', value: data.moderation.visibility === 'VISIBLE' ? 'EXCLUDED' : 'VISIBLE', label: '기사 노출 상태 변경' })}>기사 노출 변경</Btn>}
      </div>
    </>
  }}</QueryState>
    <Modal open={!!action} onClose={() => setAction(null)} label="변경 확인"><h2 className="t2">{action?.label}할까요?</h2><p className="muted" style={{ marginTop: 12 }}>변경 사유와 처리 이력이 저장됩니다.</p><label className="lbl" style={{ marginTop: 16 }}>변경 사유</label><TextField value={reason} onChange={setReason} aria-label="변경 사유" /><div style={{ display: 'flex', gap: 10, marginTop: 24 }}><Btn onClick={() => setAction(null)}>취소</Btn><Btn kind="red" disabled={!reason.trim() || busy} onClick={submit}>확인</Btn></div></Modal>
  </section>
}
