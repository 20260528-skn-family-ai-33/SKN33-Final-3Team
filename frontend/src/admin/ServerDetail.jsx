import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { useQuery, QueryState, Value } from '../api/hooks'
import { Btn, Modal, TextField } from '../components/ui'
import { useApp } from '../store'
import { AUDIT_ACTION, AUDIT_TARGET } from './useServerScreen'
export default function ServerDetail({ type, id, onChanged }) {
  const { session, run, notify } = useApp()
  const path = type === 'user' ? `/admin/users/${id}` : `/admin/articles/${id}`
  const result = useQuery(path)
  const [action, setAction] = useState(null), [reason, setReason] = useState(''), [busy, setBusy] = useState(false)
  // 같은 메뉴 안 이동은 스크롤을 유지하므로, 목록 아래에서 상세를 열어도 상세가 보이게 맨 위로 올린다.
  useEffect(() => { window.scrollTo(0, 0) }, [id])
  const submit = async () => {
    if (busy) return
    setBusy(true)
    await run(async () => {
      await api(`${path}/${action.field}`, { method: 'PATCH', body: { [action.key]: action.value, expected_version: result.data.version, confirmed: true, reason } })
      setAction(null); setReason(''); result.reload(); onChanged?.(); notify('변경과 감사 이력 저장을 완료했습니다.')
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
        {type === 'user' && record.user_id === session.user_id && <p className="note">내 계정은 이용 상태와 관리자 권한을 바꿀 수 없습니다. 다른 관리자 계정에서 변경해 주세요.</p>}
        {type === 'article' && <Btn kind="danger" onClick={() => setAction({ field: 'visibility', key: 'visibility', value: data.moderation.visibility === 'VISIBLE' ? 'EXCLUDED' : 'VISIBLE', label: '기사 노출 상태 변경' })}>기사 노출 변경</Btn>}
      </div>
    </>
  }}</QueryState>
    <Modal open={!!action} onClose={() => setAction(null)} label="변경 확인"><h2 className="t2">{action?.label}할까요?</h2><p className="muted" style={{ marginTop: 12 }}>변경 사유와 처리 이력이 저장됩니다.</p><label className="lbl" style={{ marginTop: 16 }}>변경 사유</label><TextField value={reason} onChange={setReason} aria-label="변경 사유" /><div style={{ display: 'flex', gap: 10, marginTop: 24 }}><Btn onClick={() => setAction(null)}>취소</Btn><Btn kind="red" disabled={!reason.trim() || busy} onClick={submit}>확인</Btn></div></Modal>
  </section>
}

const FIELD_LABEL = { role: '권한', account_status: '이용 상태', visibility: '노출 상태' }
const VALUE_LABEL = { USER: '일반 회원', ADMIN: '관리자', ACTIVE: '정상', RESTRICTED: '이용 제한', WITHDRAWN: '탈퇴', VISIBLE: '노출', EXCLUDED: '노출 제외' }
const shown = v => v == null ? '—' : VALUE_LABEL[v] || String(v)

/** 관리자 처리 이력 상세: 목록 위에 모달로 열고, 바뀐 값만 변경 전·후로 보여 준다. */
export function AuditDetail({ id, actorEmail, onClose }) {
  const result = useQuery(`/admin/audit-events/${id}`)
  return <Modal onClose={onClose} label="처리 이력 상세" width={640}>
    <QueryState state={result}>{({ audit: a }) => {
      const before = a.before || {}, after = a.after || {}
      const changed = Object.keys(after).filter(k => JSON.stringify(before[k]) !== JSON.stringify(after[k]))
      return <>
        <h2 className="t2">처리 이력 상세</h2>
        <div style={{ display: 'grid', gridTemplateColumns: '110px 1fr', gap: 10, marginTop: 18 }}>
          {Object.entries({ '처리 시각': a.created_at ? new Date(a.created_at).toLocaleString('sv-SE') : null, 처리자: actorEmail || `회원 ${a.actor_id}`, 작업: AUDIT_ACTION[a.action] || a.action, 대상: `${AUDIT_TARGET[a.target_type] || a.target_type} ${a.target_id}`, '처리 사유': a.reason, 결과: a.result === 'SUCCEEDED' ? '완료' : a.result, '영향 범위': a.impact })
            .map(([label, value]) => <div key={label} style={{ display: 'contents' }}><span className="muted">{label}</span><b style={{ overflowWrap: 'anywhere' }}><Value value={value} /></b></div>)}
        </div>
        <p className="t3" style={{ marginTop: 22 }}>변경 내용</p>
        <div className="th" style={{ gridTemplateColumns: '1fr 1fr 1fr', marginTop: 10 }}><span>항목</span><span>변경 전</span><span>변경 후</span></div>
        {changed.map(k => <div key={k} className="tr" style={{ gridTemplateColumns: '1fr 1fr 1fr' }}><span>{FIELD_LABEL[k] || k}</span><span>{shown(before[k])}</span><b>{shown(after[k])}</b></div>)}
        {!changed.length && <p className="muted" style={{ padding: '12px 4px' }}>바뀐 값이 기록되지 않았습니다.</p>}
        {/* 이력은 기록이라 여기서 바꾸지 않는다. 지금 상태를 보거나 다시 바꾸려면 대상 화면으로 간다. */}
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, marginTop: 22 }}>
          {a.target_type === 'ARTICLE' && <Btn kind="red" to={`/admin/data/BS80?article_id=${a.target_id}`}>이 기사 관리로 가기</Btn>}
          {a.target_type === 'USER' && <Btn kind="red" to={`/admin/members/BS48?user_id=${a.target_id}`}>이 회원 상세로 가기</Btn>}
          <Btn onClick={onClose}>닫기</Btn>
        </div>
      </>
    }}</QueryState>
  </Modal>
}
