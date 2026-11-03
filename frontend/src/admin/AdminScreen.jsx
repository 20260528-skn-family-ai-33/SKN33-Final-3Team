// 관리자 화면 데이터(screens.js)를 목업과 같은 마크업으로 그린다.
import { useState } from 'react'
import { navigate } from '../router'
import { Btn, Chip, Modal } from '../components/ui'
import Icon from '../components/Icon'
import { useApp } from '../store'
import { isAdmin } from '../auth/roles'
import { useQuery, ErrorNotice } from '../api/hooks'

const BANNER = {
  info: { background: 'var(--lav)', color: 'var(--plum)' },
  warn: { background: 'var(--amber-bg)', border: '1px solid #F5D7A6', color: 'var(--amber)' },
  error: { background: 'var(--rose)', border: '1px solid #F1A9B4', color: 'var(--red-d)' },
  ok: { background: '#E6F4EC', border: '1px solid #B9E0C9', color: 'var(--ok)' },
}
const TL_TONE = { 시작: 'plum', 완료: 'ok', 경고: 'amber', 실패: 'red', 동의: 'ok', 설정: 'plum' }

const lines = text => String(text).split('\n').map((t, i, all) => <span key={i}>{t.trim()}{i < all.length - 1 && <br />}</span>)

export function AdminButton({ b }) {
  const { session } = useApp()
  if (/\/BS(52|53|55|56|57)(?:[?]|$)/.test(b.to || '') && !isAdmin(session)) return null
  return (
    <Btn kind={b.kind || 'line'} size={b.size || 'sm'} to={b.to} icon={b.icon} style={b.style ? Object.fromEntries(b.style.split(';').filter(Boolean).map(p => { const [k, v] = p.split(':'); return [k.trim().replace(/-([a-z])/g, (_, c) => c.toUpperCase()), v.trim()] })) : undefined}>
      {b.label}
    </Btn>
  )
}

function Buttons({ items }) {
  return <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>{items.map((b, i) => <AdminButton key={i} b={b} />)}</div>
}

function Cell({ c }) {
  if (typeof c === 'string') return lines(c)
  if (c.tag !== undefined) return <span className={'tag ' + c.tag}>{c.text}</span>
  if (c.btns) return <Buttons items={c.btns} />
  return null
}

function Tabs({ items, sm }) {
  return <div className="chips">{items.map(t => <Chip key={t.label} sm={sm} on={t.on} to={t.to}>{t.label}</Chip>)}</div>
}

/** 6개월 추이 미니 꺾은선(운영 현황) */
function MiniLine({ values, max, ticks, color = '#C8102E', w = 520, h = 200 }) {
  const xs = values.map((_, i) => 60 + (i * (w - 80)) / (values.length - 1))
  const y = v => h - 30 - (v / max) * (h - 50)
  return (
    <svg width="100%" viewBox={`0 0 ${w} ${h}`} aria-hidden="true">
      {ticks.map(t => (
        <g key={t}>
          <line x1="50" y1={y(t)} x2={w - 10} y2={y(t)} stroke="#ECEEF1" />
          <text x="44" y={y(t) + 4} textAnchor="end" fontSize="11" fill="#8A919C">{t.toLocaleString()}</text>
        </g>
      ))}
      <polyline points={values.map((v, i) => `${xs[i].toFixed(0)},${y(v).toFixed(0)}`).join(' ')} fill="none" stroke={color} strokeWidth="2.5" />
      {values.map((v, i) => <circle key={i} cx={xs[i]} cy={y(v)} r="3.5" fill={color} />)}
      {xs.map((x, i) => <text key={i} x={x} y={h - 8} textAnchor="middle" fontSize="11" fill="#8A919C">{i + 1}월</text>)}
    </svg>
  )
}

const OPS_TABS = [['전체', 'BS32'], ['데이터', 'BS34'], ['리포트', 'BS36'], ['회원관리', 'BS38']]
const OPS_DETAIL = { 전체: 'BS33', 데이터: 'BS35', 리포트: 'BS37', 회원관리: 'BS39' }
function OpsDashboard({ tab }) {
  const result = useQuery('/admin/alerts', { page_size: 20 })
  const alerts = (result.data?.items || []).map(a => ['데이터', a.message, a.created_at, 'red', '/admin/inbox/collection']).filter(a => tab === '전체' || a[0] === tab)
  return (
    <>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20 }} className="resp-stack">
        <section className="card">
          <div className="between">
            <div>
              <p className="t3">전체 회원</p>
              <p style={{ fontSize: 30, fontWeight: 800, color: 'var(--red)', marginTop: 4 }}>— <span className="note" style={{ fontWeight: 400 }}>집계 준비 중</span></p>
            </div>
            <Btn to="/admin/members/BS40">상세보기</Btn>
          </div>
          <MiniLine values={[]} max={1600} ticks={[0, 400, 800, 1200, 1600]} />
        </section>
        <section className="card">
          <div className="between">
            <div>
              <p className="t3">활성 구독</p>
              <p style={{ fontSize: 30, fontWeight: 800, color: 'var(--plum)', marginTop: 4 }}>— <span className="note" style={{ fontWeight: 400 }}>집계 준비 중</span></p>
            </div>
          </div>
          <MiniLine values={[]} max={500} ticks={[0, 100, 200, 300, 400, 500]} color="#2B2350" />
        </section>
      </div>
      <p className="note">회원·구독 월별 통계는 서버의 과거 스냅샷 저장 계약 확정 후 제공됩니다.</p>
      <section className="card">
        <ErrorNotice error={result.error} retry={result.reload} /><div className="between"><p className="t2">알림</p><Btn to={`/admin/inbox/${OPS_DETAIL[tab]}`}>상세보기</Btn></div>
        <div className="chips" style={{ marginTop: 14 }}>
          {OPS_TABS.map(([label, id]) => <Chip key={label} sm on={tab === label} to={`/admin/ops/${id}`}>{label}</Chip>)}
        </div>
        <div style={{ marginTop: 6 }}>
          {alerts.map(([kind, title, meta, tone, to]) => (
            <div key={title} className="between" style={{ padding: '14px 0', borderBottom: '1px solid var(--line2)' }}>
              <div style={{ display: 'flex', gap: 14, alignItems: 'flex-start' }}>
                <span className={'tag ' + tone}>{kind}</span>
                <div><p style={{ fontSize: 15, fontWeight: 700 }}>{title}</p><p className="note">{meta}</p></div>
              </div>
              <Btn to={to}>상세</Btn>
            </div>
          ))}
        </div>
        <p className="note" style={{ marginTop: 12 }}>{`조회된 알림 ${alerts.length}건`}</p>
      </section>
    </>
  )
}

/** 기사 제목·본문 검색 */
function ArticleSearch({ value, to }) {
  const [q, setQ] = useState(value || '')
  const go = () => navigate(q.trim() ? `${to}?q=${encodeURIComponent(q.trim())}` : to)
  return (
    <section className="card" style={{ padding: '16px 20px' }}>
      <form onSubmit={e => { e.preventDefault(); go() }} style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: 12, alignItems: 'end' }}>
        <div>
          <label className="lbl" htmlFor="article-q">기사 검색</label>
          <div className={'input sm' + (q ? ' val' : '')}>
            <input id="article-q" value={q} onChange={e => setQ(e.target.value)} placeholder="기사 제목 또는 본문 내용 입력" />
            <Icon name="search" color="#9AA0AA" />
          </div>
        </div>
        <Btn type="submit" kind="ink" size="md" style={{ height: 44, borderRadius: 12 }}>검색</Btn>
      </form>
      <p className="note" style={{ marginTop: 8 }}>기사 제목 또는 본문 일부로 검색합니다. 비우고 검색하면 전체 목록으로 돌아갑니다.</p>
    </section>
  )
}

/** 데이터 수집 이력 검색 + 결과 분류 */
function RunSearch({ value, status: statusValue, to }) {
  const [q, setQ] = useState(value || '')
  const [status, setStatus] = useState(statusValue || '')
  const go = (next = status) => {
    const p = new URLSearchParams()
    if (q.trim()) p.set('q', q.trim())
    if (next) p.set('status', next)
    navigate(p.toString() ? `${to}?${p}` : to)
  }
  return (
    <section className="card" style={{ padding: '16px 20px' }}>
      <form onSubmit={e => { e.preventDefault(); go() }} className="resp-stack" style={{ display: 'grid', gridTemplateColumns: '1fr 160px auto', gap: 12, alignItems: 'end' }}>
        <div>
          <label className="lbl" htmlFor="run-q">수집 이력 검색</label>
          <div className={'input sm' + (q ? ' val' : '')}>
            <input id="run-q" value={q} onChange={e => setQ(e.target.value)} placeholder="실행 ID, 수집 작업명 또는 출처 코드 입력" />
            <Icon name="search" color="#9AA0AA" />
          </div>
        </div>
        <div>
          <label className="lbl" htmlFor="run-status">결과</label>
          <div className="input sm val select">
            <select id="run-status" value={status} onChange={e => { setStatus(e.target.value); go(e.target.value) }}>
              <option value="">전체</option><option value="SUCCESS">성공</option><option value="FAILED">실패</option><option value="RUNNING">실행 중</option>
            </select>
            <Icon name="chevronDown" color="#5C6370" stroke={2} />
          </div>
        </div>
        <Btn type="submit" kind="ink" size="md" style={{ height: 44, borderRadius: 12 }}>검색</Btn>
      </form>
      <p className="note" style={{ marginTop: 8 }}>결과를 고르면 바로 걸러집니다. 검색어를 비우고 검색하면 전체 이력으로 돌아갑니다.</p>
    </section>
  )
}

/** 관리자 처리 이력 검색 + 대상 종류 + 정렬 */
function AuditSearch({ query, to }) {
  const [q, setQ] = useState(query?.q || '')
  const go = patch => {
    const p = new URLSearchParams()
    const next = { q: q.trim(), target: query?.target || '', sort: query?.sort || '', ...patch }
    for (const [k, v] of Object.entries(next)) if (v) p.set(k, v)
    navigate(p.toString() ? `${to}?${p}` : to)
  }
  const select = (id, label, key, options) => (
    <div>
      <label className="lbl" htmlFor={id}>{label}</label>
      <div className="input sm val select">
        <select id={id} value={query?.[key] || ''} onChange={e => go({ [key]: e.target.value })}>
          {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <Icon name="chevronDown" color="#5C6370" stroke={2} />
      </div>
    </div>
  )
  return (
    <section className="card" style={{ padding: '16px 20px' }}>
      <form onSubmit={e => { e.preventDefault(); go({}) }} className="resp-stack" style={{ display: 'grid', gridTemplateColumns: '1fr 130px 130px auto', gap: 12, alignItems: 'end' }}>
        <div>
          <label className="lbl" htmlFor="audit-q">처리 이력 검색</label>
          <div className={'input sm' + (q ? ' val' : '')}>
            <input id="audit-q" value={q} onChange={e => setQ(e.target.value)} placeholder="처리자 이메일, 대상 ID 또는 처리 사유 입력" />
            <Icon name="search" color="#9AA0AA" />
          </div>
        </div>
        {select('audit-target', '대상 종류', 'target', [['', '전체'], ['USER', '회원'], ['ARTICLE', '기사']])}
        {select('audit-sort', '정렬', 'sort', [['', '최신순'], ['oldest', '오래된순']])}
        <Btn type="submit" kind="ink" size="md" style={{ height: 44, borderRadius: 12 }}>검색</Btn>
      </form>
      <p className="note" style={{ marginTop: 8 }}>대상 종류와 정렬은 고르면 바로 적용됩니다. 상세를 누르면 변경 전후 값을 확인할 수 있습니다.</p>
    </section>
  )
}

/** 회원 아이디(이메일)·이름 검색 */
function MemberSearch({ value, by: byValue, to }) {
  const [q, setQ] = useState(value || '')
  const [by, setBy] = useState(byValue || 'id')
  const go = () => navigate(`${to}?q=${encodeURIComponent(q.trim())}&by=${by}`)
  return (
    <section className="card" style={{ padding: '16px 20px' }}>
      <form onSubmit={e => { e.preventDefault(); go() }} className="resp-stack" style={{ display: 'grid', gridTemplateColumns: '1fr 160px auto', gap: 12, alignItems: 'end' }}>
        <div>
          <label className="lbl" htmlFor="member-q">회원 검색</label>
          <div className={'input sm' + (q ? ' val' : '')}>
            <input id="member-q" value={q} onChange={e => setQ(e.target.value)} placeholder="아이디(이메일) 또는 이름 입력" />
            <Icon name="search" color="#9AA0AA" />
          </div>
        </div>
        <div>
          <label className="lbl" htmlFor="member-by">검색 기준</label>
          <div className="input sm val select">
            <select id="member-by" value={by} onChange={e => setBy(e.target.value)}>
              <option value="id">아이디(이메일)</option><option value="name">이름</option>
            </select>
            <Icon name="chevronDown" color="#5C6370" stroke={2} />
          </div>
        </div>
        <Btn type="submit" kind="ink" size="md" style={{ height: 44, borderRadius: 12 }}>검색</Btn>
      </form>
      <p className="note" style={{ marginTop: 8 }}>검색 기준(아이디 또는 이름)을 고른 뒤 검색합니다. 일부만 입력해도 찾을 수 있습니다.</p>
    </section>
  )
}

function Block({ b, query }) {
  switch (b.type) {
    case 'tabs': return <Tabs items={b.items} sm={b.sm} />
    case 'table': {
      const rows = b.rows
      return (
        <section className="card" style={{ padding: '18px 20px' }}>
          {b.title && <p className="t3" style={{ marginBottom: 12 }}>{b.title}</p>}
          {b.count && <p className="note" style={{ textAlign: 'right', marginBottom: 8 }}>{b.count}</p>}
          {/* 좁은 화면에서는 표만 가로로 스크롤한다. */}
          <div style={{ overflowX: 'auto' }}><div style={{ minWidth: 560 }}>
          <div className="th" style={{ gridTemplateColumns: b.cols }}>{b.head.map(h => <span key={h}>{h}</span>)}</div>
          {rows.map((r, i) => (
            <div key={i} className={'tr' + (r.sel ? ' sel' : '')} style={{ gridTemplateColumns: b.cols }}>
              {r.cells.map((c, j) => <span key={j} style={{ minWidth: 0, overflowWrap: 'anywhere' }}><Cell c={c} /></span>)}
            </div>
          ))}
          </div></div>
          {!rows.length && <p className="muted" style={{ padding: '18px 14px' }}>{b.empty || (query?.q ? '검색 결과가 없습니다. 아이디(이메일) 또는 이름을 다시 확인해 주세요.' : '표시할 항목이 없습니다.')}</p>}
        </section>
      )
    }
    case 'kv': return (
      <div style={{ display: 'grid', gridTemplateColumns: `repeat(${b.cols},1fr)`, gap: 14 }} className="resp-stack">
        {b.items.map((it, i) => (
          <div key={i} className="card" style={{ padding: '16px 20px' }}>
            <p className="note" style={{ fontWeight: 700 }}>{it.k}</p>
            <p style={{ fontSize: 17, fontWeight: 700, marginTop: 4 }}>{lines(it.v)}</p>
          </div>
        ))}
      </div>
    )
    case 'buttons': return <Buttons items={b.items} />
    case 'note': return <p className="note">{b.text}</p>
    case 'para': return <p style={{ fontSize: 15 }}>{b.text}</p>
    case 'heading': return <p className="t3">{b.text}</p>
    case 'banner': return <div style={{ borderRadius: 12, padding: '14px 18px', fontSize: 14, fontWeight: 700, ...BANNER[b.tone] }}>{b.text}</div>
    case 'label': return <p style={{ fontSize: 13, fontWeight: 800, color: 'var(--red-d)', marginTop: 6 }}>{b.text}</p>
    case 'step': {
      const bg = { on: 'var(--red)', done: 'var(--ink)' }[b.state] || '#E3E6EA'
      return (
        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          <span style={{ width: 34, height: 34, borderRadius: 17, background: bg, color: b.state ? '#fff' : '#8A919C', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontWeight: 800 }}>{b.n}</span>
          <span style={{ fontSize: 17, fontWeight: 700, color: b.state ? 'var(--ink)' : '#8A919C' }}>{b.label}</span>
        </div>
      )
    }
    case 'timeline': return (
      <div style={{ display: 'grid', gridTemplateColumns: '110px 70px 1fr', gap: 14, alignItems: 'center', padding: '12px 0', borderBottom: '1px solid var(--line2)' }}>
        <span className="muted">{b.time}</span><span className={'tag ' + (b.tone || TL_TONE[b.tag] || '')}>{b.tag}</span><span style={{ fontSize: 15 }}>{b.text}</span>
      </div>
    )
    case 'log': return (
      <div style={{ background: '#10191F', borderRadius: 12, padding: '18px 20px', fontFamily: 'ui-monospace,SFMono-Regular,Menlo,Consolas,monospace', fontSize: 12.5, lineHeight: 1.75, whiteSpace: 'pre-wrap', wordBreak: 'break-all', overflowX: 'auto' }} role="log" aria-label="실행 터미널 로그">
        {b.lines.map((l, i) => {
          let c = '#CDD6DC'
          if (l.includes(' WARN ')) c = '#EBC86E'
          if (l.includes(' ERROR ') || /^(Traceback| {2}File| {4}|requests\.)/.test(l)) c = '#F0786E'
          if (l.startsWith('$')) c = '#96E6A0'
          if (l.startsWith('Process finished')) c = '#96A0AA'
          return <div key={i} style={{ color: c }}>{l}</div>
        })}
      </div>
    )
    case 'card': return (
      <section className="card col" style={{ gap: 14 }}>
        {b.title && <p className="t2">{b.title}</p>}
        {b.blocks.map((x, i) => <Block key={i} b={x} query={query} />)}
      </section>
    )
    case 'skeleton': return <div className="col" style={{ gap: 12 }}>{Array.from({ length: b.rows }, (_, i) => <div key={i} style={{ height: 44, borderRadius: 10, background: '#ECEEF1' }} />)}</div>
    // 검색칸은 주소(검색어·필터)가 바뀌면 새로 그려, 뒤로 가기 등으로 바뀐 조건과 입력값이 어긋나지 않게 한다.
    case 'memberSearch': return <MemberSearch key={`${b.to}|${query?.q ?? ''}|${query?.by ?? ''}`} value={query?.q ?? b.value} by={query?.by} to={b.to} />
    case 'articleSearch': return <ArticleSearch key={query?.q ?? ''} value={query?.q} to={b.to} />
    case 'auditSearch': return <AuditSearch key={`${query?.q ?? ''}|${query?.target ?? ''}|${query?.sort ?? ''}`} query={query} to={location.hash.slice(1).split('?')[0]} />
    case 'runSearch': return <RunSearch key={`${query?.q ?? ''}|${query?.status ?? ''}`} value={query?.q} status={query?.status} to={location.hash.slice(1).split('?')[0]} />
    case 'ops': return <OpsDashboard tab={b.tab} />
    default: return null
  }
}

function ScreenModal({ m }) {
  return (
    <Modal onClose={() => navigate(m.close)} width={660} label={m.title} top={200}>
      <div className="between"><span className="note" style={{ fontWeight: 700 }}>{m.label}</span><Btn to={m.close}>닫기</Btn></div>
      <h2 style={{ fontSize: 26, fontWeight: 800, marginTop: 14, color: m.danger ? 'var(--red)' : 'var(--ink)' }}>{m.title}</h2>
      <div className="col" style={{ gap: 6, marginTop: 16 }}>{m.lines.map((l, i) => <p key={i} style={{ fontSize: 16 }}>{l}</p>)}</div>
      {m.inputs.map(x => (
        <div key={x.label} style={{ marginTop: 14 }}>
          <label className="lbl">{x.label}</label>
          <div className="input val"><input defaultValue={x.value} aria-label={x.label} /></div>
        </div>
      ))}
      {m.textarea && <ReasonField label={m.textarea} />}
      {m.notes.map((n, i) => <p key={i} className="note" style={{ marginTop: i ? 0 : 14 }}>{n}</p>)}
      {m.extra && <div style={{ marginTop: 16 }}><Buttons items={m.extra} /></div>}
      <div className="between" style={{ marginTop: 22 }}>{m.buttons.map((b, i) => <AdminButton key={i} b={{ ...b, size: 'md' }} />)}</div>
    </Modal>
  )
}

function ReasonField({ label }) {
  const [v, setV] = useState('')
  return (
    <>
      <label className="lbl" style={{ marginTop: 20 }}>{label}</label>
      <div className={'input area' + (v ? ' val' : '')}><textarea rows={3} value={v} onChange={e => setV(e.target.value)} placeholder="사유를 입력하세요" aria-label={label} /></div>
      <p className="note" style={{ marginTop: 8 }}>위 입력란에 사유를 작성하세요. 빈 사유는 저장하지 않습니다.</p>
    </>
  )
}

export default function AdminScreen({ screen, query }) {
  const { head, blocks, modal } = screen
  return (
    <>
      <div className="col" style={{ gap: 20, minWidth: 0 }}>
        <section className="card">
          <div className="between" style={{ alignItems: 'flex-start' }}>
            <div>
              <p className="note">{head.crumb}</p>
              <h2 className="t1" style={{ fontSize: 26, marginTop: 4 }}>{head.title}</h2>
              {head.sub && <p className="muted" style={{ marginTop: 6 }}>{head.sub}</p>}
            </div>
            {head.actions && <Buttons items={head.actions} />}
          </div>
          {head.tabs && <div style={{ marginTop: 18 }}><Tabs items={head.tabs} /></div>}
        </section>
        {blocks.map((b, i) => <Block key={i} b={b} query={query} />)}
      </div>
      {modal && <ScreenModal m={modal} />}
    </>
  )
}

