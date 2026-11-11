// BS06 메인(전체) · BS07 메인(내 관심) · BS13 알림 팝오버(머리글)
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { Link } from '../../router'
import { useApp } from '../../store'
import Icon from '../../components/Icon'
import { ChatFab } from '../../components/layout'
import { Donut, Sparkline } from '../../components/charts'
import { useQuery, ErrorNotice } from '../../api/hooks'
import { mapArticle } from '../../api/adapters'
import heroShip from '../../assets/hero-ship.jpg'
import useDashboard from './useDashboard'
import useMainView from './useMainView'
import useUnData from './useUnData'

// 메인 상단 기능 카드. 마지막 값은 누르면 이동할 화면이다(예전 '분석 · 리포트 바로가기' 띠의 링크를 옮겨 왔다).
const FEATURES = [
  ['chat', '추이·원인 질문', '“중국 메모리 수출이 왜 늘었어?”처럼 물으면 표와 근거 기사로 답해요.', 'rose', '/analysis'],
  ['bell', '급증 기사 알림', '관심 품목·국가 기사량이 평소보다 늘면 알림으로 알려줘요.', 'rose', '/account/settings'],
  ['file', '리포트 센터', '주간·월간·분기 리포트를\n열람하고 내려받아요', 'lav', '/account/reports'],
  ['calendar', '정기 리포트 구독', '주간·월간·분기 리포트를 정해진 날 자동으로 받아요.', 'lav', '/account/subscription'],
]
// 품목별·교역국별 표의 열 너비. 머리 줄(.th)과 행(.tr)이 같은 값을 써야 열이 어긋나지 않는다.
const PRODUCT_TABLE_COLS = '24px 86px 1fr 64px 60px'
const COUNTRY_TABLE_COLS = '24px 1fr 64px 60px'
const TICKER_TABS = [['all', '전체기사'], ['general', '일반기사'], ['short', '단신속보'], ['major', '주요이슈']]
const DIR_COLOR = { up: '#C8102E', down: '#1F5FD1', flat: '#5C6370' }
const ARROW = { up: '▲', down: '▼', flat: '' }
// 점유율 글자('7.3%')로 막대 너비를 정한다. 값이 없으면('—') 빈 막대, 숫자는 0~100%로 제한한다.
const shareBarWidth = share => { const pct = Number.parseFloat(share); return Number.isFinite(pct) ? `${Math.min(100, Math.max(0, pct))}%` : 0 }
const cut = (text, n) => !text || text.length <= n ? text : `${text.slice(0, n)}…`

/** 영역별 오류 안내(I-2). 한 영역에서 실패한 요청을 묶어 한 번만 알리고, 다시 불러오기는 그 영역만 한다. */
function RegionError({ label, states }) {
  const failed = [...new Set(states.filter(s => s.error))]
  if (!failed.length) return null
  const retry = () => failed.forEach(s => s.reload?.())
  const message = `${label} 정보를 불러오지 못했습니다. ${failed[0].error.message}`
  return <div><ErrorNotice error={{ ...failed[0].error, message }} retry={retry} /></div>
}

// 메인 수출입 현황의 상승 화살표(디자인 SVG). 꼬리는 투명하게 시작해 머리 쪽으로 진해진다.
// 원본 경로를 14° 시계 방향으로 돌려 완만하게(약 24°) 오르게 하고, 지표 카드 위에 얹는다. 꼬리 끝은 카드 뒤로 살짝 들어간다.
const ARROW_PATH = 'M210 639 C404 625 604 518 691 320 L623 294 L835 148 L881 395 L812 368 C660 550 478 634 210 639 Z'
function TrendArrow() {
  return (
    <svg className="trend-arrow" aria-hidden="true" width="388" height="194" viewBox="150 215 745 373"
      style={{ position: 'absolute', right: 120, bottom: 'calc(100% - 14px)', pointerEvents: 'none' }}>
      <defs>
        <linearGradient id="trend-arrow-fill" x1="208" y1="639" x2="820" y2="190" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#FFE5E5" stopOpacity="0" />
          <stop offset="0.24" stopColor="#FFCECE" stopOpacity="0.7" />
          <stop offset="0.56" stopColor="#FF8F8F" />
          <stop offset="1" stopColor="#FF2424" />
        </linearGradient>
      </defs>
      <path d={ARROW_PATH} fill="url(#trend-arrow-fill)" transform="rotate(14 545 393)" />
    </svg>
  )
}

/**
 * 점유율 카드 영역을 '카드 안 품목 두 개'까지만 보이게 할 높이를 잰다.
 * 첫 줄 카드마다 두 번째 품목(품목이 하나면 첫 품목)이 끝나는 위치를 재고, 그중 가장 낮은 곳에 카드 아래 여백을 더한다.
 * 나머지 품목과 다음 줄 카드는 스크롤로 본다. 화면 폭이 바뀌어 열 수가 달라지거나 카드 높이가 바뀌어도 다시 잰다.
 * 내용이 그 높이 안에 다 들어가면 null(제한 없음).
 */
const UN_VISIBLE_ITEMS = 2
function useUnVisibleHeight(deps) {
  const ref = useRef(null)
  const [height, setHeight] = useState(null)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return undefined
    const measure = () => {
      const cards = [...el.children]
      if (!cards.length) { setHeight(null); return }
      const top = el.getBoundingClientRect().top - el.scrollTop
      const firstRowTop = Math.min(...cards.map(c => c.offsetTop))
      const bottoms = cards.filter(c => c.offsetTop === firstRowTop).map(card => {
        const items = card.querySelectorAll('[data-un-item]')
        const last = items[Math.min(UN_VISIBLE_ITEMS, items.length) - 1]
        const cardStyle = getComputedStyle(card)
        const pad = parseFloat(cardStyle.paddingBottom) + parseFloat(cardStyle.borderBottomWidth)
        return (last || card).getBoundingClientRect().bottom - top + (last ? pad : 0)
      })
      const limit = Math.ceil(Math.max(...bottoms))
      setHeight(el.scrollHeight > limit + 1 ? limit : null)
    }
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    ;[...el.children].forEach(c => ro.observe(c))
    return () => ro.disconnect()
  }, deps) // eslint-disable-line react-hooks/exhaustive-deps
  return [ref, height]
}

function FeatureCard([icon, title, text, tone, to]) {
  const bg = tone === 'rose' ? ['#FCE8EB', '#C8102E'] : ['#ECEAF5', '#2B2350']
  return (
    <Link key={title} to={to} className="feature-card" style={{ display: 'block', background: '#fff', borderRadius: 18, padding: 24, boxShadow: '0 12px 30px rgba(0,0,0,.18)' }}>
      <span style={{ width: 48, height: 48, borderRadius: 14, background: bg[0], color: bg[1], display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}><Icon name={icon} size={24} stroke={1.8} /></span>
      <h3 style={{ margin: '16px 0 6px', fontSize: 18, fontWeight: 800, color: '#15181E' }}>{title}</h3>
      <p style={{ fontSize: 14, lineHeight: 1.6, color: '#444B57', whiteSpace: 'pre-line' }}>{text}</p>
    </Link>
  )
}

function Ticker({ interest }) {
  const [tab, setTab] = useState('all')
  const [index, setIndex] = useState(0)
  const [paused, setPaused] = useState(false)
  // 내 관심에서 단신속보·주요이슈를 관심 국가로 맞추는 규칙은 서버(news.article_statement)가 정한다.
  const result = useQuery('/articles', { scope: interest ? 'INTERESTED' : 'GLOBAL', tab: ({ all: 'ALL', general: 'GENERAL', short: 'BREAKING', major: 'MAJOR_ISSUE' })[tab], page_size: 5 })
  const items = (result.data?.items || []).map(mapArticle)
  const current = items[index % items.length]
  useEffect(() => {
    if (paused || !items.length) return undefined
    const t = setInterval(() => setIndex(i => (i + 1) % items.length), 4500)
    return () => clearInterval(t)
  }, [paused, items.length])
  const round = { width: 30, height: 30, borderRadius: 15, background: 'rgba(255,255,255,.12)', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', border: 0, cursor: 'pointer' }
  return (
    <div style={{ marginTop: 44, background: 'rgba(10,14,20,.74)', border: '1px solid rgba(255,255,255,.14)', borderRadius: 14, padding: '14px 20px', display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 16, color: '#fff' }} aria-live="polite">
      {/* 좁은 화면에서는 탭이 다음 줄로 넘어가 잘리지 않는다(각 탭 글자는 한 줄 유지). */}
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '6px 14px', fontSize: 15, whiteSpace: 'nowrap', minWidth: 0 }}>
        <strong>{interest ? '내 관심 기사' : '기사'}</strong>
        {TICKER_TABS.map(([key, label]) => (
          <button key={key} type="button" className="reset" onClick={() => { setTab(key); setIndex(0) }} style={{ color: tab === key ? '#FF8A9A' : '#D5D9E0' }} aria-pressed={tab === key}>{label}</button>
        ))}
      </div>
      <span style={{ width: 1, height: 16, background: 'rgba(255,255,255,.25)' }} />
      <span style={{ fontSize: 13, color: '#B8BEC8', whiteSpace: 'nowrap' }}>{current?.source || '기사 출처'}</span>
      {/* 제목이 길면 한 줄에 들어가는 만큼만 보여 주고 …로 줄인다. 전체 제목은 마우스를 올리면 보인다. */}
      {/* 지금 보고 있는 롤링 탭(전체기사 등)으로 기사 화면을 연다. 기사 종류로 탭을 정하면 전체기사에서 눌러도 일반기사 탭으로 갔다. */}
      <Link to={`/articles?tab=${tab}&id=${current?.id}`} title={current?.title} style={{ flex: '1 1 220px', minWidth: 0, color: '#fff', fontSize: 16, fontWeight: 700, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{current?.title || (result.error ? result.error.message : result.loading ? '기사를 불러오는 중…' : '조회된 기사가 없습니다.')}</Link>
      <div style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 13, color: '#B8BEC8', whiteSpace: 'nowrap' }}>
        <span style={{ marginRight: 6 }}>{items.length ? (index % items.length) + 1 : 0} / {items.length}</span>
        <button type="button" style={round} aria-label="이전 기사" onClick={() => setIndex(i => (i - 1 + items.length) % items.length)}><Icon name="chevronUp" size={13} color="#fff" stroke={2.2} /></button>
        <button type="button" style={round} aria-label="다음 기사" onClick={() => setIndex(i => (i + 1) % items.length)}><Icon name="chevronDown" size={13} color="#fff" stroke={2.2} /></button>
        <button type="button" style={round} aria-label={paused ? '자동 넘김 재생' : '자동 넘김 일시정지'} onClick={() => setPaused(p => !p)}>
          {paused ? <svg width="13" height="13" viewBox="0 0 24 24" fill="#fff" aria-hidden="true"><path d="M7 4v16l13-8z" /></svg> : <Icon name="pause" size={13} color="#fff" stroke={2.2} />}
        </button>
        <Link to={`/articles?tab=${tab}`} style={{ marginLeft: 10, color: '#FF8A9A' }}>전체보기</Link>
        <Link to="/articles" style={{ marginLeft: 8, color: '#D5D9E0' }}>기사홈</Link>
      </div>
    </div>
  )
}

export default function Dashboard({ route }) {
  const { profile } = useApp()
  // 전체↔내 관심 전환 버튼은 머리글(알림 왼쪽)에 있다. 여기서는 현재 보기만 읽는다.
  const { view } = useMainView(route)
  const interest = view === 'interest'
  const [months, setMonths] = useState(3)
  const market = useDashboard(view, months, profile)
  const un = useUnData(profile)
  const data = { ...market, un: un.items }
  const cards = data.rankCards
  const [unRef, unHeight] = useUnVisibleHeight([data.un])

  return (
    <>
      <section className="band" style={{ padding: '64px 0 28px' }}>
        {/* 배경 사진은 띠 전체를 덮는다. (예전 left:-30%·width:130%는 공통 img max-width:100%에 막혀 오른쪽 30%가 비었다) */}
        <img src={heroShip} alt="" style={{ objectPosition: 'center 45%' }} />
        <div className="wrap" style={{ display: 'block' }}>
          <div style={{ display: 'flex', gap: 40, alignItems: 'flex-start' }} className="resp-stack">
            <div style={{ flex: '1 1 380px', paddingTop: 24 }}>
              <p className="tag plum" style={{ marginBottom: 10 }}>{interest ? '내 관심 시장' : '전체 시장'}</p>
              <h1 style={{ fontSize: 44, lineHeight: 1.22 }}>대화로 시작하는<br />무역 분석</h1>
              <p className="lead" style={{ fontSize: 16, lineHeight: 1.7, marginTop: 18 }}>품목·국가·기간을 말로 묻고, 수치와 근거 기사를 확인한 뒤<br />그대로 PDF 리포트로 저장할 수 있습니다.</p>
            </div>
            <div style={{ flex: '1.2 1 480px', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 18, alignItems: 'start' }}>
              <div className="col" style={{ gap: 18 }}>{FEATURES.slice(0, 2).map(FeatureCard)}</div>
              <div className="col" style={{ gap: 18, marginTop: 52 }}>{FEATURES.slice(2).map(FeatureCard)}</div>
            </div>
          </div>
          <Ticker interest={interest} profile={profile} />
        </div>
      </section>

      {/* 위 여백은 상승 화살표 머리가 위쪽 사진 띠에 붙지 않게 넉넉히 둔다(화살표 끝 ↔ 영역 위 끝 약 52px). */}
      <section style={{ background: '#F6F7F9', padding: '88px 0 52px' }}>
        <div className="wrap resp-stack" style={{ display: 'flex', gap: 32 }}>
          <div style={{ flex: '2 1 600px', position: 'relative' }}>
            {/* 제목·기간 글자는 상승 화살표보다 위에 그려 화살표가 글자를 가리지 않게 한다. */}
            <div style={{ position: 'relative', zIndex: 2 }}>
              <RegionError label="주요 지표" states={data.states.kpi} />
              <p style={{ fontSize: 14, fontWeight: 700, color: '#B0102A' }}>통계 기준 월 {data.statMonth}</p>
              <h2 style={{ marginTop: 8, fontSize: 36, fontWeight: 800, letterSpacing: -1 }}>{data.title}</h2>
              <p style={{ marginTop: 10, fontSize: 14, color: '#444B57' }}>{data.period}</p>
            </div>
            {/* 화살표는 지표 카드 묶음 기준으로 놓아 화면 폭이 바뀌어도 카드와의 위치가 유지된다. 카드가 화살표 위에 그려진다. */}
            <div style={{ position: 'relative', marginTop: 24 }}>
            <TrendArrow />
            <div style={{ position: 'relative', zIndex: 1, background: '#fff', borderRadius: 16, boxShadow: '0 10px 30px rgba(21,24,30,.07)', display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(150px,1fr))' }}>
              {data.kpis.map(k => {
                // 수출액·수입액은 같은 기준의 순위 화면으로 연결한다. 무역수지는 연결할 화면이 없어 보여 주기만 한다.
                const Box = k.flow ? Link : 'div'
                const linkProps = k.flow ? { to: data.kpiLink('COUNTRY', undefined, undefined, k.flow), className: 'dash-card' } : {}
                return (
                <Box key={k.label} {...linkProps} style={{ padding: '22px 24px', borderRight: '1px solid #E3E6EA', color: '#15181E' }}>
                  <p style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 15, fontWeight: 700 }}><Icon name="flag" />{k.label}{k.flow && <Icon name="arrowRight" size={14} color="#8A919C" style={{ marginLeft: 'auto' }} />}</p>
                  <p style={{ marginTop: 10, fontSize: 28, fontWeight: 800 }}>{k.value}<span style={{ fontSize: 13, fontWeight: 500, color: '#5C6370' }}> {k.unit}</span></p>
                  <div style={{ marginTop: 10, display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '5px 12px', fontSize: 13 }}>
                    {k.rows.map(([l, v, d]) => <FragmentPair key={l} label={l} value={`${ARROW[d]}${v}`} color={DIR_COLOR[d]} />)}
                  </div>
                </Box>
                )
              })}
            </div>
            </div>
          </div>
          <div style={{ flex: '1 1 320px' }}>
            <div className="between" style={{ alignItems: 'flex-end' }}>
              <div>
                <h2 style={{ fontSize: 22, fontWeight: 800 }}>{data.fxTitle}</h2>
                <p className="muted" style={{ marginTop: 6 }}>{data.fxDate} · 직전 영업일 대비</p>
              </div>
              <span style={{ fontSize: 12, color: '#5C6370' }}>한국은행 ECOS</span>
            </div>
            <RegionError label="환율" states={data.states.fx} />
            <div role="region" aria-label={data.fxTitle} tabIndex={data.fx.length >= 5 ? 0 : undefined}
              style={{ marginTop: 14, borderTop: '2px solid #15181E', ...(data.fx.length >= 5 ? { maxHeight: 242, overflowY: 'auto', scrollbarGutter: 'stable' } : {}) }}>
              {data.fx.map(([cur, unit, rate, change, dir]) => (
                <div key={cur} style={{ display: 'grid', gridTemplateColumns: '1fr auto auto', gap: 14, alignItems: 'center', minHeight: 48, lineHeight: '22px', padding: '12px 4px', borderBottom: '1px solid #E3E6EA', fontSize: 15 }}>
                  <span><strong>{cur}</strong> <span style={{ fontSize: 12, color: '#5C6370' }}>{unit}</span></span>
                  <span style={{ fontWeight: 700 }}>{rate}</span>
                  <span style={{ minWidth: 108, textAlign: 'right', fontSize: 13, fontWeight: 700, color: DIR_COLOR[dir] }}>{ARROW[dir]}{change}</span>
                </div>
              ))}
              {!data.fx.length && !data.states.fx.some(st => st.error) && <p className="muted" style={{ padding: '13px 4px' }}>관심 국가의 환율 정보가 없습니다.</p>}
            </div>
          </div>
        </div>
      </section>

      <section style={{ background: '#fff', color: '#15181E', padding: '48px 0 52px', borderTop: '1px solid #E3E6EA', borderBottom: '1px solid #E3E6EA' }}>
        <div className="wrap">
          <div className="between" style={{ alignItems: 'flex-end', flexWrap: 'wrap' }}>
            <div>
              <h2 style={{ fontSize: 26, fontWeight: 800, color: '#15181E' }}>{data.rankTitle}</h2>
              <p style={{ marginTop: 8, fontSize: 13, color: '#5C6370' }}>{data.rankSub}</p>
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center' }}>
              {[3, 6, 12].map(m => (
                <button key={m} type="button" className="reset" aria-pressed={months === m} onClick={() => setMonths(m)}
                  style={{ height: 34, padding: '0 14px', borderRadius: 17, background: months === m ? '#15181E' : '#F1F2F5', color: months === m ? '#fff' : '#444B57', fontSize: 13, fontWeight: months === m ? 800 : 400, display: 'inline-flex', alignItems: 'center', whiteSpace: 'nowrap', flexShrink: 0 }}>{m}개월</button>
              ))}
              <Link to={data.cardLink('HS6')} className="more" style={{ marginLeft: 10, whiteSpace: 'nowrap', flexShrink: 0 }}>상세보기 →</Link>
            </div>
          </div>
          <RegionError label="수출 상위 품목" states={data.states.cards} />
          <div style={{ marginTop: 22, display: 'grid', gridTemplateColumns: `repeat(${interest ? Math.min(Math.max(cards.length, 2), 5) : 5},1fr)`, gap: 14 }} className="resp-stack">
            {cards.map(c => (
              <Link key={c.hs} to={data.cardLink('COUNTRY', c.hs)} className="dash-card" style={{ background: '#fff', border: '1px solid #E3E6EA', borderRadius: 18, padding: 18, color: '#15181E', display: 'flex', flexDirection: 'column', gap: 10, boxShadow: '0 4px 14px rgba(21,24,30,.05)' }}>
                <span style={{ display: 'flex', gap: 6 }}>
                  <span style={{ padding: '3px 8px', borderRadius: 6, background: '#FCE8EB', color: '#B0102A', fontSize: 12, fontWeight: 800 }}>{c.rank}</span>
                  <span style={{ padding: '3px 8px', borderRadius: 6, background: '#F1F2F5', color: '#444B57', fontSize: 12 }}>{c.hs}</span>
                </span>
                {/* 품목명은 항상 두 줄 높이를 차지해 카드마다 아래 수치·그래프 위치가 같아진다. */}
                <span title={c.name} style={{ fontSize: 18, fontWeight: 800, lineHeight: '24px', height: 48, display: '-webkit-box', WebkitBoxOrient: 'vertical', WebkitLineClamp: 2, overflow: 'hidden', overflowWrap: 'break-word' }}>{c.name}</span>
                <span style={{ marginTop: 'auto', display: 'grid', gridTemplateColumns: 'minmax(0,1fr) 80px', gridTemplateRows: '18px 30px 18px', columnGap: 10, alignItems: 'center' }}>
                  <span style={{ fontSize: 12, lineHeight: '18px', color: '#5C6370' }}>수출액</span>
                  <span style={{ gridColumn: 2, gridRow: '1 / 4', alignSelf: 'center', display: 'flex', justifyContent: 'flex-end' }}><Sparkline values={c.values} color={c.color} /></span>
                  <span title={c.exp} style={{ gridColumn: 1, gridRow: 2, fontSize: 22, lineHeight: '30px', fontWeight: 800, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{c.exp}</span>
                  <span title={`수입 ${c.imp}`} style={{ gridColumn: 1, gridRow: 3, fontSize: 12, lineHeight: '18px', color: '#5C6370', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>수입 {c.imp}</span>
                </span>
              </Link>
            ))}
          </div>
          {data.cardsEmpty && <p style={{ marginTop: 18, fontSize: 14, color: '#5C6370' }}>조회 기간에 집계된 수출 품목이 없습니다.</p>}
        </div>
      </section>

      <main className="wrap" style={{ paddingTop: 56, display: 'flex', flexDirection: 'column', gap: 56 }}>
        <section style={{ display: 'flex', gap: 20 }} className="resp-stack">
          <div className="card" style={{ flex: '1 1 440px' }}>
            <div className="between" style={{ alignItems: 'flex-start' }}>
              <div>
                <h2 className="t2">{data.productTable.title}</h2>
                <p className="muted" style={{ marginTop: 6 }}>{data.productTable.sub}</p>
              </div>
 <Link to={data.tableLink('HS6')} className="more">상세보기 →</Link>
            </div>
            <RegionError label="품목별 수출입" states={data.states.products} />
            <div className="th" style={{ gridTemplateColumns: PRODUCT_TABLE_COLS, marginTop: 14 }}>
              <span>순위</span><span>HS6</span><span>품목</span><span className="r">수출액</span><span className="r">수입액</span>
            </div>
            {data.productTable.rows.map(([rank, hs, name, exp, imp]) => (
              <div key={hs} className="tr row2" style={{ gridTemplateColumns: PRODUCT_TABLE_COLS }}>
                <span className="b">{rank}</span><span><span className="hs">{hs}</span></span><span className="b" title={name} style={{ fontWeight: 700 }}>{name}</span><span className="r b" title={exp}>{exp}</span><span className="r s" title={imp}>{imp}</span>
              </div>
            ))}
            {data.productTable.empty && <p className="muted" style={{ padding: '18px 4px' }}>조회 기간에 집계된 품목 실적이 없습니다.</p>}
            {data.productTable.note && <p className="note" style={{ marginTop: 12 }}>{data.productTable.note}</p>}
          </div>
          <div className="card" style={{ flex: '1.3 1 540px' }}>
            {/* 왼쪽: 교역국 표, 오른쪽: 수출액 비중. 두 제목을 같은 크기·같은 높이에 둔다. 상세보기는 카드 오른쪽 위를 유지한다. */}
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 26 }}>
              <div style={{ flex: '1 1 260px', minWidth: 0 }}>
                <h2 className="t2">{data.countryTable.title}</h2>
                <p className="muted" style={{ marginTop: 6 }}>{data.countryTable.sub}</p>
                <RegionError label="교역국별 수출입" states={data.states.countries} />
                <div style={{ marginTop: 14 }}>
                  <div className="th" style={{ gridTemplateColumns: COUNTRY_TABLE_COLS }}>
                    <span>순위</span><span>교역국</span><span className="r">수출액</span><span className="r">수입액</span>
                  </div>
                  {data.countryTable.rows.map(([rank, name, exp, imp]) => (
                    <div key={name} className="tr row2" style={{ gridTemplateColumns: COUNTRY_TABLE_COLS }}>
                      <span className="b">{rank}</span><span title={name} style={{ fontWeight: 700 }}>{name}</span><span className="r b" title={exp}>{exp}</span><span className="r s" title={imp}>{imp}</span>
                    </div>
                  ))}
                  {data.countryTable.empty && <p className="muted" style={{ padding: '18px 4px' }}>조회 기간에 집계된 교역국 실적이 없습니다. (— 는 실적 없음)</p>}
                </div>
              </div>
              <div style={{ flex: '1 1 220px', minWidth: 0, display: 'flex', flexDirection: 'column' }}>
                <div className="between" style={{ alignItems: 'flex-start', gap: 12 }}>
                  <h2 className="t2">{data.countryTable.pieTitle}</h2>
                  <Link to={data.tableLink('COUNTRY')} className="more" style={{ flexShrink: 0 }}>상세보기 →</Link>
                </div>
                {/* 원그래프는 제목 바로 아래, 국가 목록은 그 아래. 목록이 길면 목록 안에서 스크롤한다. */}
                <div style={{ marginTop: 15, display: 'flex', justifyContent: 'center' }}>
                  <Donut size={240} items={data.countryTable.pie.map(([label, color]) => [label, parseFloat(label.split(' ').pop()), color])} />
                </div>
                <ul aria-label="수출액 비중 국가 목록" tabIndex={data.countryTable.pie.length > 10 ? 0 : undefined}
                  style={{ listStyle: 'none', margin: '15px 0 0', padding: 0, display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: '20px 14px', fontSize: 13, color: '#2E333C', maxHeight: 160, overflowY: 'auto', scrollbarGutter: 'stable' }}>
                  {data.countryTable.pie.map(([label, color]) => (
                    <li key={label} style={{ display: 'flex', alignItems: 'center', gap: 7, minHeight: 22, lineHeight: '20px', overflowWrap: 'break-word' }}><span style={{ width: 10, height: 10, flexShrink: 0, borderRadius: 2, background: color }} /><span>{label}</span></li>
                  ))}
                </ul>
                <p className="muted" style={{ marginTop: 10, fontSize: 12 }}>{data.countryTable.pieNote}</p>
              </div>
            </div>
          </div>
        </section>

        <section>
          <div className="sec-h">
            <div>
              <h2 className="t1">한국의 세계시장 점유율</h2>
              <p className="muted" style={{ marginTop: 6 }}>{data.unSub}</p>
            </div>
            <Link to="/account/interests" className="more">관심정보 설정 →</Link>
          </div>
          <div style={{ marginTop: 14, display: 'flex', gap: '6px 24px', fontSize: 13, color: '#5C6370', flexWrap: 'wrap' }}>
            <span><strong style={{ color: '#15181E' }}>수출 점유율</strong> = 상대국 전체 수입 중 한국산 비중</span>
            <span>순위 = 상대국 공급국 중 한국 순위</span>
          </div>
          <div ref={unRef} role="region" aria-label="한국의 세계시장 점유율 카드" tabIndex={unHeight ? 0 : undefined}
            style={{ marginTop: 16, position: 'relative', display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 16, ...(unHeight ? { maxHeight: unHeight, overflowY: 'auto', scrollbarGutter: 'stable', paddingRight: 4 } : {}) }} className="resp-stack">
            {data.un.map(u => (
              <div key={u.country} style={{ border: '1px solid #E3E6EA', borderTop: '3px solid #C8102E', borderRadius: '4px 4px 16px 16px', padding: '16px 18px 6px' }}>
                <div className="between" style={{ alignItems: 'baseline' }}>
                  <span style={{ fontSize: 20, fontWeight: 800 }}>{u.country}</span><span className="muted" style={{ fontSize: 12 }}>{u.meta}</span>
                </div>
                {u.items.map(it => (
                  <div key={it.hs} data-un-item style={{ marginTop: 12, padding: '12px 0 10px', borderTop: '1px solid #E3E6EA', display: 'flex', flexDirection: 'column', gap: 8, color: '#15181E' }}>
                    <div className="between" style={{ fontSize: 13 }}>
                      <span style={{ fontWeight: 600 }}>{it.name} <span className="s" style={{ fontWeight: 400 }}>HS {it.hs}</span></span><span className="hs">{[it.rank, it.period && `${it.period} 기준`].filter(Boolean).join(' · ')}</span>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
                      <span className="muted" style={{ fontSize: 12 }}>수출 점유율</span>
                      <span style={{ fontSize: 24, fontWeight: 800 }}>{it.share}</span>
                      {/* 증가 ▲·감소 ▼만 화살표를 붙이고, 변화 없음(0.0%p)·비교 자료 없음은 화살표 없이 회색으로 둔다. */}
                      <span style={{ fontSize: 13, fontWeight: 600, color: DIR_COLOR[it.dir] || DIR_COLOR.flat }}>{ARROW[it.dir] ? `${ARROW[it.dir]} ` : ''}{it.change}</span>
                    </div>
                    <div style={{ height: 8, borderRadius: 4, background: '#F1F2F5', overflow: 'hidden' }}><div style={{ height: 8, background: '#C8102E', width: shareBarWidth(it.share) }} /></div>
                  </div>
                ))}
              </div>
            ))}
          </div>
<RegionError label="한국의 세계시장 점유율" states={[un]} /><p className="muted" style={{ marginTop: 10, fontSize: 12 }}>{profile.products.length ? '조회 조건에 공개된 UN 관측값만 표시합니다. 빈 결과는 미수집 상태이며 0%를 뜻하지 않습니다.' : '관심 HS6 품목을 등록하면 공개된 UN 데이터를 조회합니다.'}</p>
        </section>

        <section>
          <div className="sec-h">
            <div>
              <h2 className="t1">{data.newsTitle}</h2>
              <p className="muted" style={{ marginTop: 6 }}>{data.newsSub}</p>
            </div>
            <Link to="/articles" className="more">기사홈 →</Link>
          </div>
          <RegionError label="기사" states={data.states.news} />
          <div style={{ marginTop: 20, display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 22 }} className="resp-stack">
            {data.newsCards.map(n => {
              const found = n
              return (
                <Link key={n.title} to={found ? `/articles?tab=${found.kind}&id=${found.id}` : '/articles'} style={{ color: '#15181E', display: 'flex', flexDirection: 'column', gap: 10, border: '1px solid #E3E6EA', borderRadius: 14, padding: '18px 18px 20px' }}>
                  <span style={{ fontSize: 12, fontWeight: 800, color: '#B0102A' }}>{({ major: '주요 이슈', short: '단신속보', general: '해외시장뉴스' })[n.kind]} <span style={{ fontWeight: 500, color: '#5C6370' }}>· {n.date}</span></span>
                  <span style={{ fontSize: 16, fontWeight: 800, lineHeight: 1.45 }}>{n.title}</span>
                  <span style={{ fontSize: 13, lineHeight: 1.6, color: '#5C6370' }}>{cut(n.summary, 50)}</span>
                </Link>
              )
            })}
          </div>
        </section>
        {/* 다른 화면(기사·순위)과 같은 오른쪽 아래 고정 버튼 */}
        <ChatFab />
      </main>
      <div style={{ height: 72 }} />
    </>
  )
}

function FragmentPair({ label, value, color }) {
  return <><span className="s">{label}</span><span style={{ fontWeight: 700, color }}>{value}</span></>
}
