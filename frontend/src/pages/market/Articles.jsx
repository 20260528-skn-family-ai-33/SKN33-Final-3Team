// BS09 기사 공통 진입(검색) · BS10 전체기사 · 일반기사 · BS11 단신속보 · BS12 주요이슈(선택 기사 강조)
import { useEffect, useRef, useState } from 'react'
import { Link, navigate, withQuery } from '../../router'
import { Band, ChatFab } from '../../components/layout'
import { Btn, Chip, Pager, TextField } from '../../components/ui'
import Icon from '../../components/Icon'
import { articleKinds, articleTabs, articleParagraphs } from '../../data/articles'
import { useQuery, ErrorNotice } from '../../api/hooks'
import { mapArticle } from '../../api/adapters'
import bandArticles from '../../assets/band-articles.jpg'

const PAGE_SIZE = 5
const toDot = d => (d ? d.replaceAll('-', '.') : '')
// 기사 탭의 "AI에게 물어보기"는 사용하지 않기로 했다(2026-11-11). 다시 쓰려면 아래 버튼 주석과 함께 되살린다.
// const ask = a => `/analysis?new=1&article=${encodeURIComponent(a.id)}`
const cut = (text, n) => !text || text.length <= n ? text : `${text.slice(0, n)}…`

export default function Articles({ route }) {
  const q = route.query
  const tab = q.get('tab') || 'all'
  const keyword = q.get('q') || ''
  const from = q.get('from') || ''
  const to = q.get('to') || ''
  const selected = q.get('id')
  const [form, setForm] = useState({ keyword, from, to })
  const [error, setError] = useState('')
  // 주소가 바뀌면 검색칸과 오류 문구를 주소 값으로 다시 맞춘다.
  // - 검색 조건(탭·검색어·기간)이 바뀐 경우: 탭 클릭, 검색, 뒤로가기 등. 탭은 주소에 적힌 값 그대로 비교한다.
  // - 조건 없는 '#/articles'로 들어온 경우: 상단 메뉴 '기사'로 돌아온 것이므로 2페이지·펼친 기사에서 돌아와도 미적용 입력·오류를 비운다.
  // 기사 펼치기(id)·페이지 이동(page)만 바뀐 경우에는 입력해 둔 내용을 그대로 둔다.
  const urlSearch = JSON.stringify([q.get('tab'), keyword, from, to])
  const [formSource, setFormSource] = useState({ urlSearch, raw: route.search })
  if (formSource.urlSearch !== urlSearch || formSource.raw !== route.search) {
    if (formSource.urlSearch !== urlSearch || !route.search) { setForm({ keyword, from, to }); setError('') }
    setFormSource({ urlSearch, raw: route.search })
  }
  const clearForm = () => { setForm({ keyword: '', from: '', to: '' }); setError('') }
  // 이미 '#/articles'에 있을 때 상단 메뉴 '기사'를 다시 누르면 주소가 그대로라 위 비교로는 알 수 없다. 머리글이 보내는 신호로 비운다.
  useEffect(() => {
    const onReselect = e => { if (e.detail === '/articles') { setForm({ keyword: '', from: '', to: '' }); setError('') } }
    window.addEventListener('nav:reselect', onReselect)
    return () => window.removeEventListener('nav:reselect', onReselect)
  }, [])
  const page = Math.max(1, Number(q.get('page')) || 1)
  const listTop = useRef(null)
  const previousPage = useRef(page)
  const result = useQuery('/articles', { tab: ({ all: 'ALL', general: 'GENERAL', short: 'BREAKING', major: 'MAJOR_ISSUE' })[tab] || 'ALL', q: keyword, mode: keyword ? 'SEARCH' : 'BROWSE', start_date: from, end_date: to, page, page_size: PAGE_SIZE })
  const detail = useQuery(`/articles/${selected}`, {}, !!selected)
  const list = (result.data?.items || []).map(mapArticle)
  const pages = Math.max(1, Math.ceil((result.data?.page.total || 0) / PAGE_SIZE))
  const shown = selected && detail.data && !list.some(a => a.id === selected) ? [mapArticle(detail.data.article), ...list] : list
  const tabLabel = articleTabs.find(([k]) => k === tab)?.[1] || '전체기사'
  // 메인 등에서 기사를 골라 들어오면 검색칸 아래로 내려가 그 기사가 바로 보이게 한다.
  const selectedShown = !!selected && shown.some(a => a.id === selected)
  useEffect(() => {
    if (previousPage.current === page || result.loading) return
    previousPage.current = page
    if (!selected) listTop.current?.scrollIntoView({ block: 'start' })
  }, [page, result.loading, selected])
  useEffect(() => {
    if (!selectedShown) return undefined
    // 화면 이동 직후 맨 위로 올리는 동작이 끝난 뒤에 내려가도록 한 박자 늦춘다.
    const timer = setTimeout(() => document.getElementById(`article-${selected}`)?.scrollIntoView({ block: 'start' }), 120)
    return () => clearTimeout(timer)
  }, [selected, selectedShown])

  const search = e => {
    e.preventDefault()
    if (form.from && form.to && form.from > form.to) return setError('기간의 시작일이 종료일보다 늦습니다.')
    setError('')
    navigate(withQuery(route, { q: form.keyword.trim() || null, from: form.from || null, to: form.to || null, id: null, page: null }))
  }

  return (
    <>
      <Band
        image={bandArticles} imagePosition="center 55%"  title="기사 조회"
        lead={keyword ? '전체기사·일반기사·단신속보·주요이슈 탭에서 기사를 확인하고 검색합니다.' : '유형별 무역·경제 기사 조회'}
        right={(
          <div className="chips">
            {/* 탭을 누르면(지금 보고 있는 탭을 다시 눌러도) 검색어·발행 기간을 지우고 그 탭의 최신 기사부터 보여 준다.
                주소가 그대로인 경우(검색 전 입력만 해 둔 상태)도 있어 현재 탭은 클릭할 때 폼을 직접 비운다. */}
            {articleTabs.map(([key, label]) => <Chip key={key} on={tab === key} onClick={tab === key ? clearForm : undefined} to={withQuery(route, { tab: key, id: null, page: null, q: null, from: null, to: null })}>{label}</Chip>)}
          </div>
        )}
      />
      <main className="page" style={{ position: 'relative', paddingBottom: 120 }}>
        <div className="wrap col" style={{ gap: 20 }}>
          <form className="card" onSubmit={search} noValidate>
            <div className="article-search-fields">
              <div>
                <label className="lbl" htmlFor="ar-q">검색 (선택)</label>
                <TextField id="ar-q" placeholder="검색어 입력" icon="search" iconColor="#5C6370" value={form.keyword} onChange={v => setForm(f => ({ ...f, keyword: v }))} />
              </div>
              <div>
                <span className="lbl" id="ar-period">발행 기간 (선택)</span>
                <div className={'input period-input' + (form.from || form.to ? ' val' : '')} role="group" aria-labelledby="ar-period">
                  <input type="date" aria-label="시작일" value={form.from} onChange={e => setForm(f => ({ ...f, from: e.target.value }))} style={{ minWidth: 0 }} />
                  <span>~</span>
                  <input type="date" aria-label="종료일" value={form.to} onChange={e => setForm(f => ({ ...f, to: e.target.value }))} style={{ minWidth: 0 }} />
                </div>
              </div>
              <Btn type="submit" kind="red" size="md" style={{ height: 50, borderRadius: 12, padding: '0 30px' }}>기사 검색</Btn>
            </div>
            {error
              ? <p className="field-err" role="alert" style={{ marginTop: 10 }}>{error}</p>
              : <p className="note" style={{ marginTop: 10 }}>검색어와 기간은 모두 선택 사항입니다 · 기간 미지정 시 전체 기간 · 탭을 누르면 검색 조건 초기화</p>}
          </form>
          <div ref={listTop} className="sec-h" style={{ marginTop: 8, scrollMarginTop: 90 }}>
            <h2 className="t2">
              {keyword ? <>{tabLabel} · “{keyword}” 검색 결과 <span style={{ color: 'var(--red)' }}>{result.data?.page.total || 0}건</span></> : `${tabLabel} · 최신 기사`}
              {(from || to) && <span className="muted" style={{ marginLeft: 10, fontWeight: 500 }}>{toDot(from)} ~ {toDot(to)}</span>}
            </h2>
            <span className="muted">발행일 최신순</span>
          </div>
          {/* 목록·기사 본문 중 실패한 요청을 한 번에 모두 다시 불러온다. */}
          <ErrorNotice error={result.error || detail.error} retry={() => { if (result.error) result.reload(); if (detail.error) detail.reload() }} />
          {result.loading && <p role="status">기사를 불러오는 중…</p>}
          <div className="col" style={{ gap: 12 }}>
            {shown.map(a => {
              const on = a.id === selected
              const kind = articleKinds[a.kind]
              // 단신속보만 펼쳐서 본문을 보고, 나머지 기사는 요약 200자와 원문 링크를 바로 보여 준다.
              const short = a.kind === 'short'
              return (
                <article key={a.id} id={`article-${a.id}`} className={'li' + (on ? ' sel' : '')} style={{ color: 'var(--ink)', flexWrap: 'wrap', scrollMarginTop: 90 }}>
                  <div style={{ width: 4 }} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 8 }}>
                      <span className={'tag ' + kind.tone}>{kind.label}</span>
                      {a.tags.map(t => <span key={t} className="tag">{t}</span>)}
                      {a.surge && <span className="tag amber">급증 {a.surge}</span>}
                    </div>
                    <p style={{ fontSize: 18, fontWeight: 800 }}>{a.title}</p>
                    <p className="muted" style={{ marginTop: 6 }}>{a.country} · {a.date} · {a.source}</p>
                    {!short && a.summary && <p style={{ fontSize: 15, lineHeight: 1.7, color: '#2E333C', marginTop: 10 }}>{cut(a.summary, 200)}</p>}
                  </div>
                  {short
                    ? <Link to={withQuery(route, { id: on ? null : a.id, page: String(page) })} className="more" style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }} aria-expanded={on}>
                      {on ? '접기' : '상세 보기'} <Icon name={on ? 'chevronUp' : 'chevronDown'} size={15} />
                    </Link>
                    : <div className="article-actions" style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                      <Btn kind="ink" iconRight="external" disabled={!a.url} onClick={() => window.open(a.url, '_blank', 'noopener')}>원문 보기</Btn>
                      {/* <Btn to={ask(a)} icon="chat">AI에게 물어보기</Btn> */}
                    </div>}
                  {short && on && (
                    <div style={{ flexBasis: '100%', marginLeft: 22, paddingTop: 14, borderTop: '1px solid var(--line2)' }}>
                      {detail.loading
                        ? <p style={{ fontSize: 15, lineHeight: 1.75, color: '#2E333C' }}>본문을 불러오는 중…</p>
                        : articleParagraphs(detail.data?.body_masked || a.summary).map((line, i) => <p key={i} style={{ fontSize: 15, lineHeight: 1.75, color: '#2E333C', marginTop: i ? 10 : 0 }}>{line}</p>)}
                      {/* <div style={{ display: 'flex', gap: 10, marginTop: 14 }}>
                        <Btn to={ask(a)} icon="chat">AI에게 물어보기</Btn>
                      </div> */}
                    </div>
                  )}
                </article>
              )
            })}
            {!shown.length && !result.loading && !result.error && (
              <div className="card" style={{ textAlign: 'center', padding: '48px 24px' }}>
                <p className="t3">검색 결과가 없습니다.</p>
                <p className="muted" style={{ marginTop: 6 }}>검색어나 기간을 바꿔 다시 검색해 보세요.</p>
              </div>
            )}
          </div>
          {/* 다른 페이지로 가면 펼쳐 둔 상세를 닫아 이전 기사가 남아 보이지 않게 한다. */}
          {pages > 1 && <Pager page={page} pages={pages} onChange={n => navigate(withQuery(route, { page: String(n), id: null }))} />}
        </div>
        <ChatFab />
      </main>
    </>
  )
}
