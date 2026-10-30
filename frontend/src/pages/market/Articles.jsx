// BS09 기사 공통 진입(검색) · BS10 전체기사 · BS11 단신속보 · BS12 주요이슈(선택 기사 강조)
import { useState } from 'react'
import { Link, navigate, withQuery } from '../../router'
import { Band, ChatFab } from '../../components/layout'
import { Btn, Chip, Pager, TextField } from '../../components/ui'
import Icon from '../../components/Icon'
import { articleKinds, articleTabs } from '../../data/articles'
import { useQuery, ErrorNotice } from '../../api/hooks'
import { mapArticle } from '../../api/adapters'
import bandArticles from '../../assets/band-articles.jpg'

const PAGE_SIZE = 5
const toDot = d => (d ? d.replaceAll('-', '.') : '')

export default function Articles({ route }) {
  const q = route.query
  const tab = q.get('tab') || 'all'
  const keyword = q.get('q') || ''
  const from = q.get('from') || ''
  const to = q.get('to') || ''
  const selected = q.get('id')
  const [form, setForm] = useState({ keyword, from, to })
  const [error, setError] = useState('')
  const page = Math.max(1, Number(q.get('page')) || 1)
  const result = useQuery('/articles', { tab: ({ all: 'ALL', short: 'BREAKING', major: 'MAJOR_ISSUE' })[tab] || 'ALL', q: keyword, mode: keyword ? 'SEARCH' : 'BROWSE', start_date: from, end_date: to, page, page_size: PAGE_SIZE })
  const detail = useQuery(`/articles/${selected}`, {}, !!selected)
  const list = (result.data?.items || []).map(mapArticle)
  const pages = Math.max(1, Math.ceil((result.data?.page.total || 0) / PAGE_SIZE))
  const shown = selected && detail.data && !list.some(a => a.id === selected) ? [mapArticle(detail.data.article), ...list] : list
  const tabLabel = articleTabs.find(([k]) => k === tab)?.[1] || '전체기사'

  const search = e => {
    e.preventDefault()
    if (!form.keyword.trim()) return setError('키워드를 입력해 주세요. 기간은 선택 사항입니다.')
    if (form.from && form.to && form.from > form.to) return setError('기간의 시작일이 종료일보다 늦습니다.')
    setError('')
    navigate(withQuery(route, { q: form.keyword.trim(), from: form.from, to: form.to, id: null, page: null }))
  }

  return (
    <>
      <Band
        image={bandArticles} imagePosition="center 55%" eyebrow="무역·경제 뉴스" title="기사"
        lead={keyword ? '전체기사·단신속보·주요이슈 탭에서 기사를 확인하고 검색합니다.' : '통합 기사를 유형별로 확인합니다. 메인에서 선택한 기사를 바로 표시합니다.'}
        right={(
          <div className="chips">
            {articleTabs.map(([key, label]) => <Chip key={key} on={tab === key} to={withQuery(route, { tab: key, id: null, page: null })}>{label}</Chip>)}
          </div>
        )}
      />
      <main className="page" style={{ position: 'relative', paddingBottom: 120 }}>
        <div className="wrap col" style={{ gap: 20 }}>
          <form className="card" onSubmit={search} noValidate>
            <div style={{ display: 'grid', gridTemplateColumns: '1.6fr 1fr auto', gap: 14, alignItems: 'end' }} className="resp-stack">
              <div>
                <label className="lbl" htmlFor="ar-q">키워드{keyword ? ' *' : ''}</label>
                <TextField id="ar-q" placeholder="검색어 입력" icon="search" iconColor="#5C6370" value={form.keyword} onChange={v => setForm(f => ({ ...f, keyword: v }))} invalid={!!error && !form.keyword.trim()} />
              </div>
              <div>
                <span className="lbl" id="ar-period">발행 기간 (선택)</span>
                <div className={'input' + (form.from || form.to ? ' val' : '')} role="group" aria-labelledby="ar-period" style={{ gap: 6 }}>
                  <input type="date" aria-label="시작일" value={form.from} onChange={e => setForm(f => ({ ...f, from: e.target.value }))} style={{ minWidth: 0 }} />
                  <span>~</span>
                  <input type="date" aria-label="종료일" value={form.to} onChange={e => setForm(f => ({ ...f, to: e.target.value }))} style={{ minWidth: 0 }} />
                </div>
              </div>
              <Btn type="submit" kind="red" size="md" style={{ height: 50, borderRadius: 12, padding: '0 30px' }}>기사 검색</Btn>
            </div>
            {error
              ? <p className="field-err" role="alert" style={{ marginTop: 10 }}>{error}</p>
              : <p className="note" style={{ marginTop: 10 }}>{keyword ? '키워드 필수 · 기간 미지정 시 전체 기간 검색 · 탭 전환 시 검색 조건 유지' : '메인 직접 진입 시 검색어 입력 없이 조회 · 이후 키워드·기간 검색 가능'}</p>}
          </form>
          <div className="sec-h" style={{ marginTop: 8 }}>
            <h2 className="t2">
              {keyword ? <>{tabLabel} · “{keyword}” 검색 결과 <span style={{ color: 'var(--red)' }}>{result.data?.page.total || 0}건</span></> : `${tabLabel} · 최신 기사`}
              {(from || to) && <span className="muted" style={{ marginLeft: 10, fontWeight: 500 }}>{toDot(from)} ~ {toDot(to)}</span>}
            </h2>
            <span className="muted">발행일 최신순</span>
          </div>
          <ErrorNotice error={result.error || detail.error} retry={result.reload} />
          {result.loading && <p role="status">기사를 불러오는 중…</p>}
          <div className="col" style={{ gap: 12 }}>
            {shown.map(a => {
              const on = a.id === selected
              const kind = articleKinds[a.kind]
              return (
                <article key={a.id} className={'li' + (on ? ' sel' : '')} style={{ color: 'var(--ink)', flexWrap: 'wrap' }}>
                  <div style={{ width: 4 }} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
                      <span className={'tag ' + kind.tone}>{kind.label}</span>
                      {a.tags.map(t => <span key={t} className="tag">{t}</span>)}
                      {a.surge && <span className="tag amber">급증 {a.surge}</span>}
                      {on && <span className="tag" style={{ background: 'var(--ink)', color: '#fff' }}>선택한 기사</span>}
                    </div>
                    <p style={{ fontSize: 18, fontWeight: 800 }}>{a.title}</p>
                    <p className="muted" style={{ marginTop: 6 }}>{a.country} · {a.date} · {a.source}</p>
                  </div>
                  <Link to={withQuery(route, { id: on ? null : a.id, page: String(page) })} className="more" style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }} aria-expanded={on}>
                    {on ? '접기' : '상세 보기'} <Icon name={on ? 'chevronUp' : 'arrowRight'} size={15} />
                  </Link>
                  {on && (
                    <div style={{ flexBasis: '100%', marginLeft: 22, paddingTop: 14, borderTop: '1px solid var(--line2)' }}>
                      <p style={{ fontSize: 15, lineHeight: 1.75, color: '#2E333C' }}>{detail.loading ? '본문을 불러오는 중…' : detail.data?.body_masked || a.summary}</p>
                      <div style={{ display: 'flex', gap: 10, marginTop: 14 }}>
                        <Btn kind="ink" iconRight="external" onClick={() => window.open(a.url, '_blank', 'noopener')}>기사 원문 보기</Btn>
                        <Btn to={`/analysis?ask=${encodeURIComponent(`${a.title} 관련 시장 영향을 알려줘`)}`} icon="chat">AI에게 물어보기</Btn>
                      </div>
                    </div>
                  )}
                </article>
              )
            })}
            {!shown.length && !result.loading && !result.error && (
              <div className="card" style={{ textAlign: 'center', padding: '48px 24px' }}>
                <p className="t3">검색 결과가 없습니다.</p>
                <p className="muted" style={{ marginTop: 6 }}>키워드나 기간을 바꿔 다시 검색해 보세요.</p>
              </div>
            )}
          </div>
          {pages > 1 && <Pager page={page} pages={pages} onChange={n => navigate(withQuery(route, { page: String(n) }))} />}
          <p className="note">{selected ? '선택 기사 강조 표시 · 기사 ID로 해당 결과 위치 유지' : '선택 기사 없음 · 전체 목록 진입'}</p>
        </div>
        <ChatFab />
      </main>
    </>
  )
}
