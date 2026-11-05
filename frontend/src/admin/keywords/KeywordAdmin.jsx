// 키워드·분류·처리 이력을 서버에서 읽고 저장한다.
import { useState } from 'react'
import { navigate, withQuery } from '../../router'
import { useApp } from '../../store'
import { api, download } from '../../api/client'
import { useQuery, QueryState, ErrorNotice, Pagination, usePendingAction } from '../../api/hooks'
import { Btn, Chip, Select, TextField, Modal } from '../../components/ui'
import { AuditDetail } from '../ServerDetail'
import { AUDIT_ACTION } from '../useServerScreen'
import { kstDateTime } from '../../api/adapters'

const STATES = { UNREVIEWED: '미처리', HELD: '보류', REVIEWED: '분류함' }
const fieldLabel = f => `${f.major_name} › ${f.minor_name}`
const stack = { display: 'flex', flexWrap: 'wrap', gap: 10, marginTop: 16 }

export default function KeywordAdmin({ route }) {
  const page = route.path.split('/')[3]
  const categories = useQuery('/admin/keyword-categories')
  return <div className="col" style={{ gap: 20, minWidth: 0 }}>
    <div className="chips">
      <Chip to="/admin/keywords" on={!['fields', 'history'].includes(page)}>키워드 검수</Chip>
      <Chip to="/admin/keywords/fields" on={page === 'fields'}>분류 기준 관리</Chip>
      <Chip to="/admin/keywords/history" on={page === 'history'}>처리 이력</Chip>
    </div>
    {page === 'history' ? <History key={`${route.query.get('target')}|${route.query.get('q')}`} route={route} /> : <QueryState state={categories}>{data => page === 'fields'
      ? <Fields key={route.path} route={route} data={data} reload={categories.reload} />
      : <Keywords key={`${route.query.get('status')}|${route.query.get('q')}`} route={route} fields={data.items} />}</QueryState>}
  </div>
}

function Keywords({ route, fields }) {
  const status = route.query.get('status') || 'UNREVIEWED', page = Number(route.query.get('page')) || 1
  const [q, setQ] = useState(route.query.get('q') || ''), [error, setError] = useState(null)
  const result = useQuery('/admin/keyword-topics', { review_status: STATES[status] ? status : undefined, q: route.query.get('q'), sort: route.query.get('sort') || 'ARTICLE_COUNT_DESC', page, page_size: 20 })
  const selected = route.query.get('kw')
  const change = patch => navigate(withQuery(route, patch), { replace: true })
  return <>
    <section className="card"><h2 className="t2">키워드 검수</h2>
      <div className="chips" style={{ marginTop: 16 }}>{Object.entries({ all: '전체', ...STATES }).map(([key, label]) => <Chip key={key} on={status === key} to={`/admin/keywords?status=${key}`}>{label}</Chip>)}</div>
      <form style={stack} onSubmit={e => { e.preventDefault(); change({ q: q.trim(), page: 1, kw: null }) }}>
        <TextField aria-label="키워드 검색" placeholder="키워드 검색" value={q} onChange={setQ} /><Btn type="submit">검색</Btn>
        <Select aria-label="키워드 정렬" value={route.query.get('sort') || 'ARTICLE_COUNT_DESC'} onChange={sort => change({ sort, page: 1 })} options={[[ 'ARTICLE_COUNT_DESC', '기사 수 많은 순' ], ['KEYWORD_ASC', '가나다순'], ['UPDATED_DESC', '최근 변경순']]} />
        <Btn onClick={() => download('/admin/keyword-topics/export', '키워드.csv').catch(setError)}>CSV 내보내기</Btn>
      </form><ErrorNotice error={error} />
    </section>
    <div className="resp-stack" style={{ display: 'grid', gridTemplateColumns: 'minmax(240px, 1fr) minmax(0, 2fr)', gap: 20, alignItems: 'start' }}>
      <section className="card"><QueryState state={result}>{data => <>
        <p className="note">{data.items.length} / {data.page.total}건</p>
        {data.items.map(k => <button type="button" key={k.keyword_text} className="reset" aria-current={selected === k.keyword_text} onClick={() => change({ kw: k.keyword_text })} style={{ display: 'block', textAlign: 'left', width: '100%', padding: 14, marginTop: 8, border: '1px solid var(--line)', borderRadius: 10, background: selected === k.keyword_text ? 'var(--rose)' : 'white' }}>
          <b>{k.keyword_text}</b><p className="note">{STATES[k.review_status]} · 기사 {k.article_count}건</p><p className="note">{k.category_id ? fieldLabel(k) : '분야 미지정'}</p>
        </button>)}
        {!data.items.length && <p className="muted" style={{ margin: '20px 0' }}>조건에 맞는 키워드가 없습니다.</p>}
        <Pagination page={page} info={data.page} onChange={p => change({ page: p, kw: null })} />
      </>}</QueryState></section>
      <section className="card">{selected ? <KeywordDetail key={selected} word={selected} fields={fields} onChanged={result.reload} /> : <p className="muted">목록에서 키워드를 선택하세요.</p>}</section>
    </div>
  </>
}

function KeywordDetail({ word, fields, onChanged }) {
  const result = useQuery('/admin/keyword-topics/detail', { keyword_text: word, article_limit: 10 })
  return <QueryState state={result}>{data => <KeywordEditor key={data.keyword.version} data={data} fields={fields} reload={() => { result.reload(); onChanged() }} />}</QueryState>
}

function KeywordEditor({ data, fields, reload }) {
  const k = data.keyword, current = fields.find(f => f.category_id === k.category_id)
  const [major, setMajor] = useState(current?.major_name || ''), [category, setCategory] = useState(String(k.category_id || ''))
  const [reason, setReason] = useState(''), [error, setError] = useState(null), [action, setAction] = useState(null)
  const [busy, runOnce] = usePendingAction()
  const { notify } = useApp()
  const majors = [...new Set(fields.map(f => f.major_name))]
  const submit = () => runOnce(async () => {
    setError(null)
    try {
      await api('/admin/keyword-topics/review', { method: 'PATCH', body: { keyword_text: k.keyword_text, action, category_id: action === 'ASSIGN' ? Number(category) : null, apply_scope: 'CURRENT_LOOKUPS', expected_version: k.version, reason: reason.trim(), confirmed: true } })
      setAction(null); reload(); notify('키워드 변경과 처리 이력을 저장했습니다.')
    } catch (e) { setError(e) }
  })
  return <>
    <h2 className="t2">{k.keyword_text}</h2><p className="note">{STATES[k.review_status]} · 기사 {data.article_count}건 · {current ? fieldLabel(current) : '분야 미지정'}</p>
    <h3 className="t3" style={{ marginTop: 22 }}>기사 근거</h3>
    {data.articles.map(a => <p key={a.article_id} style={{ marginTop: 12 }}>{a.title}<br /><span className="note">{a.published_date} · {a.source_code}</span></p>)}
    {!data.articles.length && <p className="muted">연결된 기사 없음</p>}
    <label className="lbl" htmlFor="keyword-major" style={{ marginTop: 24 }}>대분류</label>
    <Select id="keyword-major" value={major} onChange={v => { setMajor(v); setCategory('') }} options={[[ '', '선택하세요' ], ...majors.map(v => [v, v])]} />
    <label className="lbl" htmlFor="keyword-minor" style={{ marginTop: 12 }}>중분류</label>
    <Select id="keyword-minor" value={category} onChange={setCategory} options={[[ '', '선택하세요' ], ...fields.filter(f => f.major_name === major).map(f => [String(f.category_id), f.minor_name])]} />
    <label className="lbl" htmlFor="keyword-reason" style={{ marginTop: 16 }}>처리 사유</label><TextField id="keyword-reason" value={reason} onChange={setReason} maxLength={1000} />
    <p className="note" style={{ marginTop: 12 }}>분류 변경은 사전 기반 현재 기사 조회에 반영됩니다. 저장된 답변·리포트는 유지됩니다.</p>
    <div style={stack}>
      <Btn kind="red" disabled={!category || !reason.trim() || busy} onClick={() => setAction('ASSIGN')}>분야 지정·변경</Btn>
      {k.status !== 'CLASSIFIED' && k.review_status !== 'HELD' && <Btn disabled={!reason.trim() || busy} onClick={() => setAction('HOLD')}>보류</Btn>}
      {(k.status === 'CLASSIFIED' || k.review_status === 'HELD') && <Btn disabled={!reason.trim() || busy} onClick={() => setAction('RELEASE')}>지정·보류 해제</Btn>}
    </div>
    <Modal open={!!action} onClose={() => !busy && setAction(null)} label="키워드 변경 확인">
      <h2 className="t2">{({ ASSIGN: '분야를 지정·변경', HOLD: '보류', RELEASE: '지정·보류를 해제' })[action]}할까요?</h2>
      <p style={{ marginTop: 12 }}>{reason}</p><ErrorNotice error={error} />
      <div style={stack}><Btn disabled={busy} onClick={() => setAction(null)}>취소</Btn><Btn kind="red" disabled={busy} onClick={submit}>확인</Btn></div>
    </Modal>
  </>
}

function Fields({ route, data, reload }) {
  const selected = data.items.find(f => f.category_id === Number(route.path.split('/')[4]))
  const [editing, setEditing] = useState(null)
  return <section className="card"><h2 className="t2">분류 기준 관리</h2>
    <p className="note">기본 대분류 {data.base_majors.length}개 · 서버에 저장된 중분류 {data.items.length}개</p>
    <div style={stack}><Btn onClick={() => setEditing({})}>새 분야 만들기</Btn>{selected && <Btn to="/admin/keywords/fields">분야 목록</Btn>}</div>
    {(selected ? [selected] : data.items).map(f => <div key={f.category_id} className="between" style={{ padding: '16px 0', borderBottom: '1px solid var(--line)' }}>
      <div><b>{fieldLabel(f)}</b><p className="note">{f.editable ? '관리자 생성' : '기본·기존 분야'}</p>{selected && <p className="note">대표 키워드: {f.representative_terms.join(', ') || '없음'}</p>}</div>
      <div style={stack}><Btn to={`/admin/keywords/fields/${f.category_id}`}>상세</Btn>{f.editable && <Btn onClick={() => setEditing(f)}>수정·삭제</Btn>}</div>
    </div>)}
    {!data.items.length && <p className="muted">분류 사전 적재 후 표시됩니다.</p>}
    {editing && <CategoryEditor key={editing.category_id || 'new'} category={editing} onClose={() => setEditing(null)} reload={reload} />}
  </section>
}

function CategoryEditor({ category, onClose, reload }) {
  const [major, setMajor] = useState(category.major_name || ''), [minor, setMinor] = useState(category.minor_name || '')
  const [reason, setReason] = useState(''), [error, setError] = useState(null), [deleting, setDeleting] = useState(false)
  const [busy, runOnce] = usePendingAction()
  const save = remove => runOnce(async () => {
    setError(null)
    try {
      await api('/admin/keyword-categories' + (category.category_id ? `/${category.category_id}` : ''), { method: remove ? 'DELETE' : category.category_id ? 'PATCH' : 'POST', body: { ...(remove ? {} : { major_name: major.trim(), minor_name: minor.trim() }), ...(category.category_id ? { expected_version: category.version } : {}), reason: reason.trim(), confirmed: true } })
      onClose(); reload()
    } catch (e) { setError(e) }
  })
  return <Modal onClose={() => !busy && onClose()} label="분야 편집">
    <h2 className="t2">{deleting ? '이 분야를 삭제할까요?' : category.category_id ? '분야 이름 수정' : '새 분야 만들기'}</h2>
    <label className="lbl" htmlFor="category-major">대분류</label><TextField id="category-major" value={major} onChange={setMajor} maxLength={40} />
    <label className="lbl" htmlFor="category-minor">중분류</label><TextField id="category-minor" value={minor} onChange={setMinor} maxLength={60} />
    <label className="lbl" htmlFor="category-reason">처리 사유</label><TextField id="category-reason" value={reason} onChange={setReason} maxLength={1000} />
    <p className="note">키워드·기사에서 사용 중인 분야는 삭제할 수 없습니다.</p><ErrorNotice error={error} />
    <div style={stack}><Btn disabled={busy} onClick={onClose}>취소</Btn><Btn kind="red" disabled={busy || !reason.trim() || !major.trim() || !minor.trim()} onClick={() => save(deleting)}>{deleting ? '삭제 확인' : '확인하고 저장'}</Btn>{category.category_id && !deleting && <Btn kind="danger" onClick={() => setDeleting(true)}>삭제</Btn>}</div>
  </Modal>
}

function History({ route }) {
  const page = Number(route.query.get('page')) || 1, target = route.query.get('target') === 'CATEGORY' ? 'CATEGORY' : 'KEYWORD'
  const result = useQuery('/admin/audit-events', { target_type: target, page, page_size: 20, q: route.query.get('q') })
  const [q, setQ] = useState(route.query.get('q') || ''), [selected, setSelected] = useState(null)
  return <section className="card"><h2 className="t2">키워드·분야 처리 이력</h2>
    <div className="chips" style={{ marginTop: 16 }}>{[['KEYWORD', '키워드'], ['CATEGORY', '분야']].map(([t, label]) => <Chip key={t} on={target === t} to={`/admin/keywords/history?target=${t}`}>{label}</Chip>)}</div>
    <form style={stack} onSubmit={e => { e.preventDefault(); navigate(withQuery(route, { q, page: 1 }), { replace: true }) }}><TextField aria-label="처리 이력 검색" value={q} onChange={setQ} placeholder="키워드·처리 사유·처리자 검색" /><Btn type="submit">검색</Btn></form>
    <QueryState state={result}>{data => <>
      {data.items.map(a => <div key={a.audit_id} className="between" style={{ padding: '16px 0', borderBottom: '1px solid var(--line)' }}><div><b>{AUDIT_ACTION[a.action] || a.action} · {a.target_id}</b><p className="note">{kstDateTime(a.created_at)} · {a.actor_email || a.actor_id}</p><p>{a.reason}</p></div><Btn onClick={() => setSelected(a)}>상세</Btn></div>)}
      {!data.items.length && <p className="muted" style={{ margin: '20px 0' }}>처리 이력이 없습니다.</p>}
      <Pagination page={page} info={data.page} onChange={p => navigate(withQuery(route, { page: p }), { replace: true })} />
    </>}</QueryState>
    {selected && <AuditDetail id={selected.audit_id} actorEmail={selected.actor_email} onClose={() => setSelected(null)} />}
  </section>
}
