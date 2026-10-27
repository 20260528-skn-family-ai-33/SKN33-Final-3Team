// BS89~BS96 키워드·분류 체계: 미분류 키워드(분야 지정·보류·새 분야·분류함 수정) / 분류 기준 관리 / 키워드 처리 이력
import { useMemo, useState } from 'react'
import { Link, navigate, withQuery } from '../../router'
import { useApp, useStored } from '../../store'
import { Btn, Check, Chip, Radio, Select, TextField } from '../../components/ui'
import Icon from '../../components/Icon'
import { HISTORY_TYPES, MAJORS, STATUS, initialFields, initialHistory, initialKeywords } from './keywordData'

const SCOPE = { new: '새로 들어오는 기사부터 적용', existing: '이미 저장된 기사에도 바로 적용', none: '기사 분야 변경 없음' }
const now = () => {
  const d = new Date(), p = n => String(n).padStart(2, '0')
  return { at: `${d.getFullYear()}.${p(d.getMonth() + 1)}.${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`, time: `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}` }
}
const card = sel => (sel
  ? { padding: '14px 16px', borderRadius: 12, background: 'var(--rose)', boxShadow: 'inset 3px 0 0 var(--red)' }
  : { padding: '14px 16px', borderRadius: 12, background: '#fff', border: '1px solid var(--line)' })

function useKeywordStore() {
  const [state, setState] = useStored('bs2.keywords', { keywords: initialKeywords, fields: initialFields, history: initialHistory })
  return [state, setState]
}

function SubTabs({ active }) {
  return (
    <div className="chips">
      <Chip to="/admin/keywords" on={active === 'list'}>미분류 키워드</Chip>
      <Chip to="/admin/keywords/fields" on={active === 'fields'}>분류 기준 관리</Chip>
      <Chip to="/admin/keywords/history" on={active === 'history'}>처리 이력</Chip>
    </div>
  )
}

function ArticleRow({ a }) {
  return (
    <div style={{ padding: '12px 14px', border: '1px solid var(--line)', borderRadius: 12 }}>
      <p style={{ fontSize: 15, fontWeight: 700 }}>{a[0]}</p>
      <p className="note">{a[1]} · {a[2]} · {a[3] || '해외시장뉴스'}</p>
    </div>
  )
}

/* ---------------- BS89 · BS90 · BS91 미분류 키워드 ---------------- */
function KeywordList({ route, store, setStore }) {
  const { notify } = useApp()
  const { keywords, fields } = store
  const status = route.query.get('status') || 'todo'
  const [q, setQ] = useState('')
  const [size, setSize] = useState('all')
  const [sort, setSort] = useState('count')
  const [onlyCandidate, setOnlyCandidate] = useState(false)
  const fieldOf = id => fields.find(f => f.id === id)
  const label = id => { const f = fieldOf(id); return f ? `${f.major} › ${f.minor}` : '' }
  const counts = { todo: 0, hold: 0, done: 0 }
  keywords.forEach(k => { counts[k.status] += 1 })
  const list = keywords
    .filter(k => k.status === status && k.name.includes(q.trim()) && (size === 'all' || k.count >= Number(size)) && (!onlyCandidate || k.candidate))
    .sort((a, b) => (sort === 'count' ? b.count - a.count : sort === 'recent' ? b.last.localeCompare(a.last) : a.name.localeCompare(b.name)))
  const selected = list.find(k => k.id === route.query.get('kw')) || list.find(k => k.id === 'k3') || list[0]
  return (
    <>
      <section className="card">
        <div className="between" style={{ alignItems: 'flex-start' }}>
          <div>
            <h2 className="t1" style={{ fontSize: 24 }}>미분류 키워드</h2>
            <p className="muted" style={{ marginTop: 6 }}>실제 규모: 미분류 23,232종 · 후보 분야 있음 8,291종 (2026.10.19 기준)</p>
            <p className="note">미처리 숫자는 관리자가 분야를 지정하거나 보류한 키워드만큼만 줄어듭니다. 목록을 열어 보기만 해서는 줄지 않습니다.</p>
          </div>
          <div className="chips">
            {Object.entries(STATUS).map(([key, name]) => <Chip key={key} sm on={status === key} to={`/admin/keywords?status=${key}`}>{name} {counts[key]}</Chip>)}
          </div>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1.6fr 1fr 1fr auto', gap: 12, alignItems: 'end', marginTop: 18 }} className="resp-stack">
          <div><label className="lbl" htmlFor="kw-q">키워드 검색</label><TextField id="kw-q" sm placeholder="키워드 입력" icon="search" value={q} onChange={setQ} /></div>
          <div><span className="lbl">기사 수</span><Select sm value={size} onChange={setSize} options={[['all', '전체'], ['10', '10건 이상'], ['100', '100건 이상']]} aria-label="기사 수" /></div>
          <div><span className="lbl">정렬</span><Select sm value={sort} onChange={setSort} options={[['count', '기사 수 많은 순'], ['recent', '최근 등장 순'], ['name', '가나다순']]} aria-label="정렬" /></div>
          <Check checked={onlyCandidate} onChange={setOnlyCandidate} style={{ height: 44, fontSize: 14 }}>후보 분야 있는 것만</Check>
        </div>
      </section>
      <div style={{ display: 'grid', gridTemplateColumns: '360px 1fr', gap: 20, alignItems: 'stretch' }} className="resp-stack">
        <section className="card col" style={{ gap: 8, padding: 18 }}>
          <div className="between" style={{ marginBottom: 4 }}><b>{STATUS[status]} · {list.length}개</b><span className="note">{{ count: '기사 수 많은 순', recent: '최근 등장 순', name: '가나다순' }[sort]}</span></div>
          {list.map(k => (
            <button key={k.id} type="button" className="reset" style={card(k.id === selected?.id)} aria-current={k.id === selected?.id} onClick={() => navigate(withQuery(route, { kw: k.id, modal: null }), { replace: true })}>
              <div className="between">
                <b style={{ fontSize: 16 }}>{k.name}{k.fresh && <span className="tag amber" style={{ marginLeft: 4 }}>신규</span>}</b>
                <span className="muted">기사 {k.count}건</span>
              </div>
              <p className="note" style={{ marginTop: 4 }}>최근 {k.last} · {k.status === 'done' ? label(k.field) : k.candidate ? `후보 ${fieldOf(k.candidate)?.minor}` : '후보 없음'}</p>
            </button>
          ))}
          {!list.length && <p className="muted" style={{ padding: 8 }}>조건에 맞는 키워드가 없습니다.</p>}
          <div style={{ flex: 1 }} />
          <p className="note" style={{ marginTop: 10 }}>처리 상태·기사 수·후보 조건을 함께 적용합니다.</p>
        </section>
        {selected
          ? <KeywordDetail key={selected.id} kw={selected} route={route} store={store} setStore={setStore} notify={notify} />
          : <section className="card" style={{ padding: '26px 28px' }}><p className="muted">선택한 키워드가 없습니다.</p></section>}
      </div>
    </>
  )
}

function KeywordDetail({ kw, route, store, setStore, notify }) {
  const { fields } = store
  const current = fields.find(f => f.id === (kw.status === 'done' ? kw.field : kw.candidate))
  const [major, setMajor] = useState(current?.major || (kw.id === 'k4' ? '경제·시장' : ''))
  const [fieldId, setFieldId] = useState(current?.id || '')
  const [scope, setScope] = useState('new')
  const [all, setAll] = useState(false)
  const creating = route.query.get('modal') === 'new-field'
  const [newMode, setNewMode] = useState('minor')
  const [newMajor, setNewMajor] = useState('')
  const [newMinor, setNewMinor] = useState('')
  const minors = fields.filter(f => f.major === major)
  const chosen = fields.find(f => f.id === fieldId)
  const isDone = kw.status === 'done'
  const majors = [...MAJORS, ...new Set(fields.map(f => f.major).filter(m => !MAJORS.includes(m)))]

  const record = (type, patch, entry) => {
    const t = now()
    setStore(s => ({
      ...s,
      keywords: s.keywords.map(k => (k.id === kw.id ? { ...k, ...patch, fresh: false } : k)),
      history: [{ id: 'h' + Date.now(), keywordId: kw.id, keyword: kw.name, type, at: t.at, count: kw.count, reason: '', ...entry,
        log: entry.log || [[t.time, '저장', `${kw.name} ${HISTORY_TYPES[type][0]} 저장`], ...(entry.scope === 'existing' ? [[t.time, '완료', `기존 기사 ${kw.count}건 분야 반영 · 분야 필터·키워드 집계 갱신`]] : [[t.time, '기록', '새로 들어오는 기사부터 적용 · 저장된 기사는 다음 재처리 때 반영']])] }, ...s.history],
    }))
  }
  const fieldLabel = f => `${f.major} › ${f.minor}`
  const save = () => {
    if (!chosen) return notify('대분류와 중분류를 먼저 고르세요.')
    if (isDone) {
      if (chosen.id === kw.field) return notify('현재와 같은 분야입니다.')
      const before = fields.find(f => f.id === kw.field)
      record('edit', { field: chosen.id, assignedAt: now().at }, { before: before ? fieldLabel(before) : '-', beforeNote: '분류함', after: fieldLabel(chosen), afterNote: '분류함 유지', scope })
      notify(`'${kw.name}'의 분야를 ${chosen.minor}(으)로 수정했습니다.`)
    } else {
      record('assign', { status: 'done', field: chosen.id, assignedAt: now().at }, { before: STATUS[kw.status], beforeNote: kw.candidate ? `후보 ${fields.find(f => f.id === kw.candidate)?.minor}` : '후보 분야 없음', after: fieldLabel(chosen), afterNote: '분류함으로 이동', scope })
      notify(`'${kw.name}'을(를) ${fieldLabel(chosen)}(으)로 지정했습니다. 분류함으로 이동했습니다.`)
    }
    navigate(withQuery(route, { kw: null, modal: null }), { replace: true })
  }
  const hold = () => {
    record('hold', { status: 'hold' }, { before: '미처리', beforeNote: kw.candidate ? `후보 ${fields.find(f => f.id === kw.candidate)?.minor}` : '후보 분야 없음', after: '보류', afterNote: '분야 변경 없음', scope: 'none' })
    notify(`'${kw.name}'을(를) 보류했습니다.`)
    navigate(withQuery(route, { kw: null }), { replace: true })
  }
  const revert = () => {
    const before = fields.find(f => f.id === kw.field)
    record(isDone ? 'unassign' : 'hold', { status: 'todo', field: null }, isDone
      ? { before: before ? fieldLabel(before) : '-', beforeNote: '분류함', after: '미처리', afterNote: '미처리 탭으로 되돌림', scope }
      : { before: '보류', beforeNote: '', after: '미처리', afterNote: '보류 해제', scope: 'none' })
    notify(`'${kw.name}'을(를) 미처리로 되돌렸습니다.`)
    navigate(withQuery(route, { kw: null }), { replace: true })
  }
  const createField = () => {
    const m = newMode === 'major' ? newMajor.trim() : major
    const name = newMinor.trim()
    if (!m || !name) return notify(newMode === 'major' ? '새 대분류와 중분류 이름을 입력해 주세요.' : '대분류를 고르고 새 중분류 이름을 입력해 주세요.')
    if (fields.some(f => f.major === m && f.minor === name)) return notify('같은 이름의 분야가 이미 있습니다.')
    const f = { id: 'f-' + Date.now().toString(36), major: m, minor: name, base: false, reps: [], repCount: 0, createdAt: now().at }
    setStore(s => ({ ...s, fields: [...s.fields, f] }))
    setMajor(m); setFieldId(f.id); setNewMinor(''); setNewMajor('')
    navigate(withQuery(route, { modal: null }), { replace: true })
    notify(`새 분야 '${m} › ${name}'을(를) 만들고 선택했습니다.`)
  }
  const openNew = () => navigate(withQuery(route, { kw: kw.id, modal: 'new-field' }), { replace: true })
  const newChip = <button type="button" className="chip sm dash" style={{ color: 'var(--plum)' }} onClick={openNew}>+ 맞는 분야가 없으면 새 분야 만들기</button>

  return (
    <section className="card" style={{ padding: '26px 28px' }}>
      <p style={{ fontSize: 13, fontWeight: 800, color: 'var(--red-d)' }}>키워드 상세 · {STATUS[kw.status]}</p>
      <h2 style={{ fontSize: 30, fontWeight: 800, marginTop: 6 }}>{kw.name}</h2>
      <p className="muted" style={{ marginTop: 4 }}>기사 {kw.count}건 · 최근 등장 {kw.last} · 처음 등장 {kw.first}</p>
      {isDone && current && (
        <div style={{ marginTop: 16, background: 'var(--lav)', borderRadius: 12, padding: '14px 16px' }}>
          <b style={{ color: 'var(--plum)' }}>현재 분야 · {fieldLabel(current)}</b>
          <p className="note">관리자 · {kw.assignedAt} · 분야 지정</p>
        </div>
      )}
      {!isDone && kw.candidate && (
        <div style={{ marginTop: 16, background: 'var(--amber-bg)', border: '1px solid #F5D7A6', borderRadius: 12, padding: '14px 16px' }}>
          <b style={{ color: 'var(--amber)' }}>후보 분야 · {fieldLabel(fields.find(f => f.id === kw.candidate))}</b>
          <p className="note" style={{ color: '#8A5A12' }}>{kw.reason}</p>
        </div>
      )}
      <div className="between" style={{ marginTop: 22 }}>
        <b style={{ fontSize: 16 }}>최근 기사</b>
        <button type="button" className="reset more" style={{ fontSize: 13 }} onClick={() => setAll(v => !v)}>{all ? '접기' : `최근 10건 모두 보기 →`}</button>
      </div>
      <div className="col" style={{ gap: 8, marginTop: 10 }}>
        {(all ? kw.articles : kw.articles.slice(0, 3)).map(a => <ArticleRow key={a[0]} a={a} />)}
        {all && <p className="note">이 키워드가 나온 기사는 시연 데이터에 {kw.articles.length}건만 있습니다. 실제로는 기사 DB에서 최근 10건을 조회합니다.</p>}
      </div>
      <b style={{ fontSize: 16, display: 'block', marginTop: 20 }}>같이 자주 나온 키워드</b>
      <div className="chips" style={{ marginTop: 10 }}>{kw.co.map(c => <span key={c} className="tag">{c}</span>)}</div>
      <div style={{ height: 1, background: 'var(--line)', margin: '22px 0 4px' }} />
      <b style={{ fontSize: 18, display: 'block', marginTop: 14 }}>{isDone ? '분야 수정' : '분야 지정'}</b>
      <p style={{ fontSize: 13, fontWeight: 700, color: 'var(--sub)', marginTop: 14 }}>대분류 ({majors.length})</p>
      <div className="chips" style={{ marginTop: 8 }}>
        {majors.map(m => <button key={m} type="button" className={'chip sm red' + (major === m ? ' on' : '')} aria-pressed={major === m} onClick={() => { setMajor(m); setFieldId('') }}>{m}</button>)}
        {!creating && newChip}
      </div>
      <p style={{ fontSize: 13, fontWeight: 700, color: 'var(--sub)', marginTop: 14 }}>중분류</p>
      <div className="chips" style={{ marginTop: 8 }}>
        {minors.map(f => <button key={f.id} type="button" className={'chip sm red' + (fieldId === f.id ? ' on' : '')} aria-pressed={fieldId === f.id} onClick={() => setFieldId(f.id)}>{f.minor}</button>)}
        {!major && <span className="muted">대분류를 먼저 고르세요.</span>}
      </div>
      {creating ? (
        <div style={{ marginTop: 16, border: '1px solid #F1A9B4', borderRadius: 16, padding: '20px 22px', background: '#FFFBFB' }}>
          <b style={{ fontSize: 16 }}>새 분야 만들기</b>
          <div style={{ display: 'flex', gap: 22, marginTop: 12 }} role="radiogroup" aria-label="새 분야 종류">
            <Radio name="new-mode" checked={newMode === 'minor'} onChange={() => setNewMode('minor')}>선택한 대분류 아래에 중분류 추가</Radio>
            <Radio name="new-mode" checked={newMode === 'major'} onChange={() => setNewMode('major')}>새 대분류와 중분류 함께 만들기</Radio>
          </div>
          {newMode === 'minor'
            ? <p className="muted" style={{ marginTop: 12 }}>대분류: <b style={{ color: 'var(--ink)' }}>{major || '위에서 대분류를 고르세요'}</b></p>
            : <><label className="lbl" style={{ marginTop: 12 }}>새 대분류 이름</label><TextField value={newMajor} onChange={setNewMajor} placeholder="예) 사회·문화" /></>}
          <label className="lbl" style={{ marginTop: 12 }}>새 중분류 이름</label>
          <TextField value={newMinor} onChange={setNewMinor} placeholder="예) 이민·비자" onEnter={createField} />
          <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
            <Btn kind="red" onClick={createField}>분야 만들고 선택</Btn>
            <Btn onClick={() => navigate(withQuery(route, { modal: null }), { replace: true })}>취소</Btn>
          </div>
          <p className="note" style={{ marginTop: 12 }}>새 분야는 이름만 만들어 분류 틀에 추가됩니다. 다른 키워드에도 고를 수 있고 기사 화면 분야 필터에도 나타납니다. 대표 키워드가 없으므로 이 분야에는 관리자가 직접 지정한 키워드만 들어갑니다. 대표 키워드는 분류 틀 관리에서 나중에 추가할 수 있게 확장합니다.</p>
        </div>
      ) : <div style={{ marginTop: 14 }}>{newChip}</div>}
      <p style={{ fontSize: 13, fontWeight: 700, color: 'var(--sub)', marginTop: 18 }}>기사 반영 범위</p>
      <div className="col" style={{ gap: 10, marginTop: 10 }} role="radiogroup" aria-label="기사 반영 범위">
        <Radio name="scope" checked={scope === 'new'} onChange={() => setScope('new')}>새로 들어오는 기사부터 적용 <span className="note">(저장된 기사는 다음 재처리 때 반영)</span></Radio>
        <Radio name="scope" checked={scope === 'existing'} onChange={() => setScope('existing')}>이미 저장된 기사에도 바로 적용</Radio>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 20, flexWrap: 'wrap' }}>
        <Btn kind="red" size="md" onClick={save} disabled={!chosen}>{isDone ? '분야 수정 저장' : '분야 지정 저장'}</Btn>
        {isDone || kw.status === 'hold' ? <Btn size="md" onClick={revert}>미처리로 되돌리기</Btn> : <Btn size="md" onClick={hold}>보류</Btn>}
        <span className="muted" style={{ marginLeft: 6 }}>선택: <b style={{ color: 'var(--ink)' }}>{chosen ? fieldLabel(chosen) : '아직 고르지 않음'}</b></span>
      </div>
      <p className="note" style={{ marginTop: 12 }}>저장하면 분류 기준 테이블에 반영되고 CSV는 내보내기로 갱신합니다. 기사 반영 범위는 저장할 때마다 관리자가 고릅니다.</p>
    </section>
  )
}

/* ---------------- BS92 분류 기준 관리 ---------------- */
const FIELD_COLS = '96px 136px 84px 1fr 126px 80px 56px'

function FieldTable({ route, store }) {
  const { fields, keywords } = store
  const [q, setQ] = useState('')
  const [major, setMajor] = useState('')
  const [kind, setKind] = useState(route.query.get('kind') || 'all')
  const [onlyAssigned, setOnlyAssigned] = useState(false)
  const assigned = id => keywords.filter(k => k.status === 'done' && k.field === id).length
  const rows = fields
    .filter(f => (!major || f.major === major) && (kind === 'all' || (kind === 'base' ? f.base : !f.base)) && (!onlyAssigned || assigned(f.id) > 0))
    .filter(f => !q.trim() || (f.major + f.minor + f.reps.join(' ')).includes(q.trim()))
    .sort((a, b) => MAJORS.indexOf(a.major) - MAJORS.indexOf(b.major))
  const baseCount = fields.filter(f => f.base).length
  const majorsCount = new Set(fields.map(f => f.major)).size
  return (
    <section className="card">
      <div className="between" style={{ alignItems: 'flex-start' }}>
        <div>
          <h2 className="t1" style={{ fontSize: 24 }}>분류 틀 관리</h2>
          <p className="note" style={{ marginTop: 6 }}>대분류 {majorsCount}개 · 중분류 {fields.length}개 · 기본 분야는 기존 분류 규칙에서 옮겨온 목록입니다.</p>
        </div>
        <div className="chips">
          <Chip sm on={kind === 'all'} onClick={() => setKind('all')}>전체 {fields.length}</Chip>
          <Chip sm on={kind === 'base'} onClick={() => setKind('base')}>기본 분야 {baseCount}</Chip>
          <Chip sm on={kind === 'custom'} onClick={() => setKind('custom')}>관리자가 만든 분야 {fields.length - baseCount}</Chip>
        </div>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1.6fr 1fr 1fr auto', gap: 12, alignItems: 'end', marginTop: 18 }} className="resp-stack">
        <div><label className="lbl" htmlFor="fd-q">분야·키워드 검색</label><TextField id="fd-q" sm placeholder="대분류·중분류·대표 키워드 입력" icon="search" value={q} onChange={setQ} /></div>
        <div><span className="lbl">대분류</span><Select sm value={major} onChange={setMajor} options={[['', '전체'], ...[...new Set(fields.map(f => f.major))]]} aria-label="대분류" /></div>
        <div><span className="lbl">구분</span><Select sm value={kind} onChange={setKind} options={[['all', '전체'], ['base', '기본'], ['custom', '관리자 생성']]} aria-label="구분" /></div>
        <Check checked={onlyAssigned} onChange={setOnlyAssigned} style={{ height: 44, fontSize: 14 }}>지정 키워드 있는 것만</Check>
      </div>
      <div className="th" style={{ gridTemplateColumns: FIELD_COLS, marginTop: 20 }}>
        <span>대분류</span><span>중분류</span><span>구분</span><span>대표 키워드</span><span>자동 분류</span><span>관리자 지정 키워드</span><span />
      </div>
      {rows.map(f => (
        <div key={f.id} className="tr" style={{ gridTemplateColumns: FIELD_COLS, background: f.base ? undefined : '#FBF4F5', fontSize: 14 }}>
          <span className="s">{f.major}</span><b>{f.minor}</b>
          <span><span className={'tag ' + (f.base ? '' : 'amber')}>{f.base ? '기본' : '관리자 생성'}</span></span>
          <span className="s">{f.reps.length ? `${f.reps.slice(0, 3).join(', ')} 외 ${f.repCount - 3}개` : '없음'}</span>
          <span className="s">{f.base ? '일치 자동 · 포함 후보' : '수동 지정만'}</span>
          <span style={{ fontWeight: assigned(f.id) ? 800 : 400 }}>{assigned(f.id)}개</span>
          <span><Btn to={`/admin/keywords/fields/${f.id}`}>상세</Btn></span>
        </div>
      ))}
      {!rows.length && <p className="muted" style={{ padding: '18px 14px' }}>조건에 맞는 분야가 없습니다.</p>}
    </section>
  )
}

/* ---------------- BS93 · BS94 · BS95 분야 상세 ---------------- */
function FieldDetail({ route, store, setStore, id }) {
  const { notify } = useApp()
  const f = store.fields.find(x => x.id === id)
  const mode = route.query.get('modal')
  const target = store.keywords.find(k => k.id === route.query.get('kw'))
  const [name, setName] = useState(f?.minor || '')
  const [scope, setScope] = useState('new')
  if (!f) {
    return (
      <section className="card"><p className="t2">분야를 찾을 수 없습니다</p><p className="muted" style={{ marginTop: 6 }}>삭제되었거나 아직 만들지 않은 분야입니다.</p>
        <Btn to="/admin/keywords/fields" icon="arrowLeft" style={{ marginTop: 14 }}>목록으로</Btn></section>
    )
  }
  const assigned = store.keywords.filter(k => k.status === 'done' && k.field === f.id)
  const setMode = (m, kw) => navigate(withQuery(route, { modal: m, kw: kw || null }), { replace: true })
  const rename = () => {
    if (!name.trim()) return notify('중분류 이름을 입력해 주세요.')
    setStore(s => ({ ...s, fields: s.fields.map(x => (x.id === f.id ? { ...x, minor: name.trim() } : x)) }))
    setMode(null)
    notify('분야 이름을 바꿨습니다. 지정된 키워드와 기사에도 새 이름이 반영됩니다.')
  }
  const remove = () => {
    if (assigned.length) return notify('지정된 키워드가 있는 분야는 삭제할 수 없습니다. 먼저 지정을 해제해 주세요.')
    setStore(s => ({ ...s, fields: s.fields.filter(x => x.id !== f.id) }))
    notify(`'${f.major} › ${f.minor}' 분야를 삭제했습니다.`)
    navigate('/admin/keywords/fields')
  }
  const unassign = () => {
    const t = now()
    setStore(s => ({
      ...s,
      keywords: s.keywords.map(k => (k.id === target.id ? { ...k, status: 'todo', field: null } : k)),
      history: [{ id: 'h' + Date.now(), keywordId: target.id, keyword: target.name, type: 'unassign', at: t.at, count: target.count, reason: '',
        before: `${f.major} › ${f.minor}`, beforeNote: '분류함', after: '미처리', afterNote: '미처리 탭으로 되돌림', scope,
        log: [[t.time, '저장', `${target.name} 지정 해제 · 분류 기준 테이블에서 삭제`], [t.time, scope === 'existing' ? '완료' : '기록', scope === 'existing' ? `저장된 기사 ${target.count}건에서 분야 삭제` : '새로 들어오는 기사부터 적용']] }, ...s.history],
    }))
    setMode(null)
    notify(`'${target.name}'의 지정을 해제했습니다. 미처리 탭으로 돌아갔습니다.`)
  }
  return (
    <section className="card" style={{ padding: '26px 28px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <Btn to="/admin/keywords/fields" icon="arrowLeft">목록으로</Btn><span className="note">분류 틀 관리 › 분야 상세</span>
      </div>
      <div className="between" style={{ marginTop: 20, alignItems: 'flex-start' }}>
        <div>
          <p style={{ fontSize: 13, fontWeight: 800, color: 'var(--red-d)' }}>분야 상세 · {f.base ? '기본 분야' : '관리자가 만든 분야'}</p>
          <h2 style={{ fontSize: 30, fontWeight: 800, marginTop: 6 }}>{f.major} › {f.minor}</h2>
          <p className="muted" style={{ marginTop: 4 }}>{f.base ? '기본 분야 · 기존 분류 규칙(category_rules.py)에서 옮겨옴' : `관리자가 만든 분야 · 관리자 · ${f.createdAt}`}</p>
        </div>
        {!f.base && (
          <div style={{ display: 'flex', gap: 8 }}>
            <Btn icon="edit" onClick={() => setMode('rename')}>이름 수정</Btn>
            <Btn kind="danger" icon="trash" onClick={remove}>분야 삭제</Btn>
          </div>
        )}
      </div>
      {mode === 'rename' && !f.base && (
        <div style={{ marginTop: 20, border: '1px solid #F1A9B4', background: '#FFFBFB', borderRadius: 16, padding: '18px 20px' }}>
          <label className="lbl" htmlFor="fd-name">중분류 이름</label>
          <div style={{ display: 'flex', gap: 8 }}>
            <TextField id="fd-name" style={{ flex: 1 }} value={name} onChange={setName} onEnter={rename} />
            <Btn kind="red" size="md" style={{ height: 50, borderRadius: 12 }} onClick={rename}>이름 저장</Btn>
            <Btn size="md" style={{ height: 50, borderRadius: 12 }} onClick={() => { setName(f.minor); setMode(null) }}>취소</Btn>
          </div>
          <p className="note" style={{ marginTop: 8 }}>이름을 바꾸면 이 분야로 지정된 키워드와 기사에 새 이름이 함께 반영됩니다.</p>
        </div>
      )}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginTop: 20 }} className="resp-stack">
        <div className="card" style={{ padding: '20px 22px' }}>
          <div className="between">
            <b style={{ fontSize: 16 }}>관리자 지정 키워드 · {assigned.length}개</b>
            <Link to="/admin/keywords" className="more" style={{ fontSize: 13 }}>미분류 키워드 화면 →</Link>
          </div>
          {assigned.length ? (
            <div className="chips" style={{ marginTop: 12 }}>
              {assigned.map(k => (
                <button key={k.id} type="button" className="chip sm" style={{ background: 'var(--rose)', borderColor: '#F1A9B4', color: 'var(--red-d)' }} onClick={() => setMode('unassign', k.id)} aria-label={`${k.name} 지정 해제`}>
                  {k.name} <Icon name="close" size={13} stroke={2.2} />
                </button>
              ))}
            </div>
          ) : <p className="muted" style={{ marginTop: 12 }}>관리자가 이 분야로 지정한 키워드가 아직 없습니다.</p>}
          {mode === 'unassign' && target && (
            <div style={{ marginTop: 14, background: 'var(--amber-bg)', border: '1px solid #F5D7A6', borderRadius: 14, padding: '16px 18px' }}>
              <b style={{ color: 'var(--amber)' }}>'{target.name}'의 지정을 해제할까요?</b>
              <p className="note" style={{ color: '#8A5A12', marginTop: 4 }}>이 분야에서 빠지고 미분류 키워드의 미처리 탭으로 돌아갑니다.</p>
              <p style={{ fontSize: 13, fontWeight: 700, color: 'var(--sub)', marginTop: 12 }}>기사 반영 범위</p>
              <div className="col" style={{ gap: 8, marginTop: 8 }}>
                <Radio name="un-scope" checked={scope === 'new'} onChange={() => setScope('new')} style={{ fontSize: 14 }}>새로 들어오는 기사부터 적용 (저장된 기사는 다음 재처리 때 반영)</Radio>
                <Radio name="un-scope" checked={scope === 'existing'} onChange={() => setScope('existing')} style={{ fontSize: 14 }}>이미 저장된 기사에서도 바로 이 분야 지우기</Radio>
              </div>
              <div style={{ display: 'flex', gap: 8, marginTop: 14 }}>
                <Btn kind="red" onClick={unassign}>지정 해제</Btn><Btn onClick={() => setMode(null)}>취소</Btn>
              </div>
            </div>
          )}
          <p className="note" style={{ marginTop: 12 }}>관리자가 미분류 키워드 화면에서 이 분야로 지정한 키워드입니다. 앞으로 이 키워드가 나오는 기사에는 항상 이 분야가 붙습니다. × 를 누르면 지정을 해제합니다.</p>
        </div>
        <div className="card" style={{ padding: '20px 22px' }}>
          <b style={{ fontSize: 16 }}>대표 키워드 · {f.repCount}개</b>
          {f.reps.length ? (
            <>
              <div className="chips" style={{ marginTop: 12, gap: 6 }}>{f.reps.map(r => <span key={r} className="tag plum">{r}</span>)}{f.reps.length < f.repCount && <span className="tag">외 {f.repCount - f.reps.length}개</span>}</div>
              <p className="note" style={{ marginTop: 12 }}>새 키워드가 이 중 하나와 똑같으면 자동으로 이 분야가 붙고, 포함만 하면 후보 분야로 미처리에 올라옵니다.</p>
            </>
          ) : (
            <>
              <div style={{ marginTop: 12, background: 'var(--bg)', borderRadius: 12, padding: '14px 16px', fontSize: 14, color: '#444B57' }}>대표 키워드가 없습니다. 이 분야에는 관리자가 직접 지정한 키워드만 들어가고, 새 키워드는 자동으로 분류되지 않습니다.</div>
              <div style={{ marginTop: 14, border: '1px dashed #C3C8D0', borderRadius: 12, padding: '14px 16px', opacity: 0.85 }}>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}><b style={{ fontSize: 14 }}>대표 키워드 추가</b><span className="tag">확장 예정 (C)</span></div>
                <p className="note">쉼표로 구분해 입력</p>
                <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                  <TextField sm style={{ flex: 1 }} placeholder="예: 비자, 이민, 체류" disabled />
                  <Btn disabled style={{ height: 44 }}>추가</Btn>
                </div>
                <p className="note" style={{ marginTop: 8 }}>지금은 사용하지 않습니다. 확장하면 입력한 말과 똑같은 새 키워드가 자동으로 이 분야에 들어갑니다.</p>
              </div>
            </>
          )}
        </div>
      </div>
    </section>
  )
}

/* ---------------- BS96 키워드 처리 이력 ---------------- */
function History({ route, store }) {
  const { history, keywords } = store
  const [q, setQ] = useState('')
  const type = route.query.get('type') || 'all'
  const [period, setPeriod] = useState('30')
  const counts = useMemo(() => {
    const c = { all: history.length }
    history.forEach(h => { c[h.type] = (c[h.type] || 0) + 1 })
    return c
  }, [history])
  const list = history.filter(h => (type === 'all' || h.type === type) && h.keyword.includes(q.trim()))
  const sel = list.find(h => h.id === route.query.get('id')) || list.find(h => h.keyword === '할랄인증제도') || list[0]
  const kw = sel && keywords.find(k => k.id === sel.keywordId)
  const [tLabel, tTone] = sel ? HISTORY_TYPES[sel.type] : []
  const scopeLine = h => (h.scope === 'existing' ? '기존 기사 ' + h.count + '건 포함' : h.scope === 'new' ? '새 기사부터' : '변경 없음')
  return (
    <>
      <section className="card">
        <div className="between" style={{ alignItems: 'flex-start' }}>
          <div>
            <h2 className="t1" style={{ fontSize: 24 }}>키워드 처리 이력</h2>
            <p className="muted" style={{ marginTop: 6 }}>미분류 키워드를 어떻게 처리했는지 시간순으로 확인합니다. 분야 지정·보류·분야 수정·지정 해제만 기록합니다.</p>
            <p className="note">이력은 수정·삭제할 수 없습니다. 목록을 열어 보기만 해서는 기록이 생기지 않습니다.</p>
          </div>
          <div className="chips">
            <Chip sm on={type === 'all'} to={withQuery(route, { type: null, id: null })}>전체 {counts.all}</Chip>
            {Object.entries(HISTORY_TYPES).map(([key, [name]]) => <Chip key={key} sm on={type === key} to={withQuery(route, { type: key, id: null })}>{name} {counts[key] || 0}</Chip>)}
          </div>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1.6fr 1fr 1fr', gap: 12, alignItems: 'end', marginTop: 18 }} className="resp-stack">
          <div><label className="lbl" htmlFor="hs-q">키워드 검색</label><TextField id="hs-q" sm placeholder="키워드 입력" value={q} onChange={setQ} /></div>
          <div><span className="lbl">처리 유형</span><Select sm value={type} onChange={v => navigate(withQuery(route, { type: v === 'all' ? null : v, id: null }), { replace: true })} options={[['all', '전체'], ...Object.entries(HISTORY_TYPES).map(([k, [n]]) => [k, n])]} aria-label="처리 유형" /></div>
          <div><span className="lbl">기간</span><Select sm value={period} onChange={setPeriod} options={[['7', '최근 7일'], ['30', '최근 30일'], ['90', '최근 90일']]} aria-label="기간" /></div>
        </div>
      </section>
      <div style={{ display: 'grid', gridTemplateColumns: '360px 1fr', gap: 20, alignItems: 'start' }} className="resp-stack">
        <section className="card col" style={{ gap: 8, padding: 18 }}>
          <div className="between" style={{ marginBottom: 4 }}><b>처리 기록 · {list.length}건</b><span className="note">최신순</span></div>
          {list.map(h => {
            const [n, tone] = HISTORY_TYPES[h.type]
            return (
              <button key={h.id} type="button" className="reset" style={card(h.id === sel?.id)} aria-current={h.id === sel?.id} onClick={() => navigate(withQuery(route, { id: h.id }), { replace: true })}>
                <div className="between"><b style={{ fontSize: 16 }}>{h.keyword}</b><span className={'tag ' + tone}>{n}</span></div>
                <p className="note" style={{ marginTop: 4 }}>{h.before} → {h.after}</p>
                <p className="note">{h.at} · 관리자 · {scopeLine(h)}</p>
              </button>
            )
          })}
          {!list.length && <p className="muted" style={{ padding: 8 }}>처리 기록이 없습니다.</p>}
          <p className="note" style={{ marginTop: 10 }}>선택한 기록의 변경 전후·반영 범위·결과를 오른쪽에서 확인합니다.</p>
        </section>
        {sel && (
          <section className="card" style={{ padding: '26px 28px' }}>
            <p style={{ fontSize: 13, fontWeight: 800, color: 'var(--red-d)' }}>처리 상세 · {tLabel}</p>
            <div className="between" style={{ marginTop: 6 }}>
              <h2 style={{ fontSize: 30, fontWeight: 800 }}>{sel.keyword}</h2>
              <span className={'tag ' + (sel.scope === 'existing' ? 'ok' : tTone)}>{sel.scope === 'existing' ? '반영 완료' : '기록됨'}</span>
            </div>
            <p className="muted" style={{ marginTop: 4 }}>{sel.at} · 관리자 · 기사 {sel.count}건{kw ? ` · 처음 등장 ${kw.first}` : ''}</p>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginTop: 18 }}>
              {[['변경 전', sel.before, sel.beforeNote], ['변경 후', sel.after, sel.afterNote],
                ['기사 반영 범위', SCOPE[sel.scope], sel.scope === 'existing' ? `대상 기사 ${sel.count}건 + 앞으로 들어오는 기사` : sel.scope === 'new' ? '저장된 기사는 다음 재처리 때 반영' : '기사 분야는 바뀌지 않음'],
                ['처리 결과', sel.scope === 'existing' ? `${sel.count}건 중 ${sel.count}건 반영 완료` : '기록 완료', sel.scope === 'existing' ? `실패 0건 · ${sel.at}` : sel.at]].map(([l, v, n]) => (
                <div key={l} style={{ padding: '14px 16px', border: '1px solid var(--line)', borderRadius: 12 }}>
                  <p className="note" style={{ fontWeight: 700 }}>{l}</p>
                  <p style={{ fontSize: 15, fontWeight: 700, marginTop: 4 }}>{v}</p>
                  {n && <p className="note">{n}</p>}
                </div>
              ))}
            </div>
            <b style={{ fontSize: 16, display: 'block', marginTop: 22 }}>판단 근거</b>
            <div className="col" style={{ gap: 8, marginTop: 10 }}>
              {kw?.articles.slice(0, 1).map(a => <ArticleRow key={a[0]} a={a} />)}
              {kw && (
                <div style={{ background: 'var(--bg)', borderRadius: 12, padding: '12px 14px' }}>
                  <p className="note" style={{ fontWeight: 700 }}>같이 자주 나온 키워드</p>
                  <div className="chips" style={{ marginTop: 6 }}>{kw.co.map(c => <span key={c} className="tag">{c}</span>)}</div>
                </div>
              )}
              <div style={{ background: 'var(--amber-bg)', border: '1px solid #F5D7A6', borderRadius: 12, padding: '12px 14px' }}>
                <p className="note" style={{ color: '#8A5A12', fontWeight: 700 }}>처리 사유</p>
                <p style={{ fontSize: 14, color: '#8A5A12' }}>{sel.reason || '입력한 처리 사유가 없습니다.'}</p>
              </div>
            </div>
            <b style={{ fontSize: 16, display: 'block', marginTop: 22 }}>반영 진행 기록</b>
            <div style={{ marginTop: 6 }}>
              {sel.log.map(([t, tag, text], i) => (
                <div key={i} style={{ display: 'grid', gridTemplateColumns: '110px 70px 1fr', gap: 12, alignItems: 'center', padding: '10px 0', borderBottom: '1px solid var(--line2)' }}>
                  <span className="muted">{t}</span><span className={'tag ' + ({ 저장: 'ok', 완료: 'ok', 시작: 'plum' }[tag] || '')}>{tag}</span><span style={{ fontSize: 14 }}>{text}</span>
                </div>
              ))}
            </div>
            <div style={{ display: 'flex', gap: 10, marginTop: 20 }}>
              {kw && <Btn kind="red" to={`/admin/keywords?status=${kw.status}&kw=${kw.id}`}>{STATUS[kw.status]}에서 키워드 보기</Btn>}
              <Btn to="/admin/keywords">미분류 키워드로</Btn>
            </div>
            <p className="note" style={{ marginTop: 12 }}>
              같은 처리의 요약(처리자·시각·대상·결과)은 왼쪽 메뉴의 <Link to="/admin/history/BS156" style={{ fontWeight: 700 }}>처리 이력·운영 설정</Link>에도 남습니다. 이 탭은 키워드 처리 내용만 자세히 보여 줍니다.
            </p>
          </section>
        )}
      </div>
    </>
  )
}

export default function KeywordAdmin({ route }) {
  const [store, setStore] = useKeywordStore()
  const parts = route.path.split('/')
  const page = parts[3] || 'list'
  const fieldId = parts[4]
  let body
  if (page === 'fields' && fieldId) body = <FieldDetail key={fieldId} route={route} store={store} setStore={setStore} id={fieldId} />
  else if (page === 'fields') body = <FieldTable route={route} store={store} />
  else if (page === 'history') body = <History route={route} store={store} />
  else body = <KeywordList route={route} store={store} setStore={setStore} />
  return (
    <div className="col" style={{ gap: 20, minWidth: 0 }}>
      <SubTabs active={page === 'fields' ? 'fields' : page === 'history' ? 'history' : 'list'} />
      {body}
    </div>
  )
}
