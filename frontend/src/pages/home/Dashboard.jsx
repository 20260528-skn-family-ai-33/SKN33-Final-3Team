// BS06 메인(전체) · BS07 메인(내 관심) · BS13 알림 팝오버(머리글)
import { useEffect, useState } from 'react'
import { Link, navigate } from '../../router'
import { useApp } from '../../store'
import Icon from '../../components/Icon'
import { Donut, Sparkline } from '../../components/charts'
import { useQuery, ErrorNotice } from '../../api/hooks'
import { mapArticle } from '../../api/adapters'
import heroShip from '../../assets/hero-ship.jpg'
import useDashboard from './useDashboard'
import useUnData from './useUnData'

const FEATURES = [
  ['chat', '추이·원인 질문', '“중국 메모리 수출이 왜 늘었어?”처럼 물으면 표와 근거 기사로 답해요.', 'rose'],
  ['bell', '급증 기사 알림', '관심 품목·국가 기사량이 평소보다 늘면 알림으로 알려줘요.', 'rose'],
  ['file', 'PDF 리포트 생성', '대화 내용과 조건을 그대로 리포트로 만들어 저장해요.', 'lav'],
  ['calendar', '정기 리포트 구독', '주간·월간·분기 리포트를 정해진 날 자동으로 받아요.', 'lav'],
]
const SHORTCUTS = [
  ['sparkle', 'AI 분석', '대화로 묻고\n근거 기사와 함께 답을 받아요', '/analysis'],
  ['file', '리포트 센터', '주간·월간·분기 리포트를\n열람하고 내려받아요', '/account/reports'],
  ['star', '관심정보 설정', '관심 품목(HS6)과 국가를\n등록·수정해요', '/account/interests'],
  ['bell', '알림 설정', '리포트 발행·주요이슈·급증 기사\n알림을 관리해요', '/account/settings'],
]
const TICKER_TABS = [['all', '전체기사'], ['short', '단신속보'], ['major', '주요이슈']]
const DIR_COLOR = { up: '#C8102E', down: '#1F5FD1', flat: '#5C6370' }

function FeatureCard([icon, title, text, tone]) {
  const bg = tone === 'rose' ? ['#FCE8EB', '#C8102E'] : ['#ECEAF5', '#2B2350']
  return (
    <div key={title} style={{ background: '#fff', borderRadius: 18, padding: 24, boxShadow: '0 12px 30px rgba(0,0,0,.18)' }}>
      <span style={{ width: 48, height: 48, borderRadius: 14, background: bg[0], color: bg[1], display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}><Icon name={icon} size={24} stroke={1.8} /></span>
      <h3 style={{ margin: '16px 0 6px', fontSize: 18, fontWeight: 800, color: '#15181E' }}>{title}</h3>
      <p style={{ fontSize: 14, lineHeight: 1.6, color: '#444B57' }}>{text}</p>
    </div>
  )
}

function Ticker({ interest }) {
  const [tab, setTab] = useState('all')
  const [index, setIndex] = useState(0)
  const [paused, setPaused] = useState(false)
  const result = useQuery('/articles', { scope: interest ? 'INTERESTED' : 'GLOBAL', tab: ({ all: 'ALL', short: 'BREAKING', major: 'MAJOR_ISSUE' })[tab], page_size: 5 })
  const items = (result.data?.items || []).map(mapArticle)
  const current = items[index % items.length]
  useEffect(() => {
    if (paused || !items.length) return undefined
    const t = setInterval(() => setIndex(i => (i + 1) % items.length), 4500)
    return () => clearInterval(t)
  }, [paused, items.length])
  const round = { width: 30, height: 30, borderRadius: 15, background: 'rgba(255,255,255,.12)', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', border: 0, cursor: 'pointer' }
  return (
    <div style={{ marginTop: 44, background: 'rgba(10,14,20,.74)', border: '1px solid rgba(255,255,255,.14)', borderRadius: 14, padding: '14px 20px', display: 'flex', alignItems: 'center', gap: 16, color: '#fff' }} aria-live="polite">
      <div style={{ display: 'flex', alignItems: 'center', gap: 14, fontSize: 15 }}>
        <strong>{interest ? '내 관심 기사' : '기사'}</strong>
        {TICKER_TABS.map(([key, label]) => (
          <button key={key} type="button" className="reset" onClick={() => { setTab(key); setIndex(0) }} style={{ color: tab === key ? '#FF8A9A' : '#D5D9E0' }} aria-pressed={tab === key}>{label}</button>
        ))}
      </div>
      <span style={{ width: 1, height: 16, background: 'rgba(255,255,255,.25)' }} />
      <span style={{ fontSize: 13, color: '#B8BEC8' }}>{current?.source || '기사 출처'}</span>
      <Link to={`/articles?tab=${current?.kind === 'general' ? 'all' : current?.kind}&id=${current?.id}`} style={{ flex: 1, color: '#fff', fontSize: 16, fontWeight: 700 }}>{current?.title || (result.error ? result.error.message : result.loading ? '기사를 불러오는 중…' : '조회된 기사가 없습니다.')}</Link>
      <div style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 13, color: '#B8BEC8' }}>
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
  const { profile, mainView, setMainView, notify } = useApp()
  const hasInterest = profile.products.length > 0 || profile.countries.length > 0
  const requested = route.query.get('view') || mainView || profile.home
  const view = requested === 'interest' && hasInterest ? 'interest' : 'all'
  const interest = view === 'interest'
  const [months, setMonths] = useState(3)
  const market = useDashboard(view, months, profile)
  const un = useUnData(profile)
  const data = { ...market, un: un.items }
  const cards = data.rankCards

  const switchView = next => {
    if (next === 'interest' && !hasInterest) {
      notify('관심정보가 없어 전체 시장만 볼 수 있습니다. 관심정보를 먼저 등록해 주세요.')
      return
    }
    setMainView(next)
    navigate(next === 'interest' ? '/?view=interest' : '/?view=all', { replace: true })
  }

  return (
    <>
      <section className="band" style={{ padding: '64px 0 28px' }}>
        <img src={heroShip} alt="" style={{ left: '-30%', width: '130%', objectPosition: 'center 45%' }} />
        <div className="wrap" style={{ display: 'block' }}>
          <div style={{ display: 'flex', gap: 40, alignItems: 'flex-start' }} className="resp-stack">
            <div style={{ flex: '1 1 380px', paddingTop: 24 }}>
              <p className="eyebrow">AI 분석</p>
              <h1 style={{ fontSize: 44, lineHeight: 1.22 }}>대화로 시작하는<br />무역 분석</h1>
              <p className="lead" style={{ fontSize: 16, lineHeight: 1.7, marginTop: 18 }}>품목·국가·기간을 말로 묻고, 수치와 근거 기사를 확인한 뒤<br />그대로 PDF 리포트로 저장할 수 있습니다.</p>
              <Link to="/analysis" className="btn btn-red btn-lg" style={{ marginTop: 28 }}>AI 분석 채팅 열기 <Icon name="arrowRight" size={18} /></Link>
            </div>
            <div style={{ flex: '1.2 1 480px', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 18, alignItems: 'start' }}>
              <div className="col" style={{ gap: 18 }}>{FEATURES.slice(0, 2).map(FeatureCard)}</div>
              <div className="col" style={{ gap: 18, marginTop: 52 }}>{FEATURES.slice(2).map(FeatureCard)}</div>
            </div>
          </div>
          <Ticker interest={interest} profile={profile} />
        </div>
      </section>

      <section style={{ background: '#2B2350', padding: '48px 0 52px' }}>
        <div className="wrap">
          <h2 style={{ textAlign: 'center', color: '#fff', fontSize: 26, fontWeight: 800, marginBottom: 26 }}>분석 · 리포트 바로가기</h2>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 16 }} className="resp-stack">
            {SHORTCUTS.map(([icon, title, text, to]) => (
              <Link key={title} to={to} style={{ border: '1px solid rgba(255,255,255,.22)', borderRadius: 16, padding: '28px 20px 26px', textAlign: 'center', color: '#fff', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10, background: 'rgba(255,255,255,.04)' }}>
                <Icon name={icon} size={44} color="#fff" stroke={1.3} />
                <span style={{ fontSize: 18, fontWeight: 800 }}>{title}</span>
                <span style={{ fontSize: 14, lineHeight: 1.6, color: '#D6D3E6', whiteSpace: 'pre-line' }}>{text}</span>
              </Link>
            ))}
          </div>
        </div>
      </section>

      <section style={{ background: '#F6F7F9', padding: '48px 0 52px' }}>
        <div className="wrap resp-stack" style={{ display: 'flex', gap: 32 }}>
          <div style={{ flex: '2 1 600px', position: 'relative' }}>
            <svg aria-hidden="true" width="300" height="150" viewBox="0 0 560 280" style={{ position: 'absolute', right: 0, top: -6 }}>
              <path d="M0 250 C 90 220, 150 170, 200 190 C 250 210, 270 240, 320 220 C 380 195, 420 120, 470 60" fill="none" stroke="#C8102E" strokeWidth="30" opacity=".9" />
              <path d="M432 74 L 512 0 L 516 110 Z" fill="#C8102E" opacity=".9" />
            </svg>
            {data.states.map((s, i) => <ErrorNotice key={i} error={s.error} retry={s.reload} />)}
            <p style={{ fontSize: 14, fontWeight: 700, color: '#B0102A' }}>통계 기준 월 {data.statMonth}</p>
            <h2 style={{ marginTop: 8, fontSize: 36, fontWeight: 800, letterSpacing: -1 }}>{data.title}</h2>
            <p style={{ marginTop: 10, fontSize: 14, color: '#444B57' }}>{data.period}</p>
            <div className="seg" style={{ marginTop: 18 }} role="group" aria-label="메인 보기 전환">
              <button type="button" className={'reset seg-btn' + (!interest ? ' on' : '')} aria-pressed={!interest} onClick={() => switchView('all')}>전체</button>
              <button type="button" className={'reset seg-btn' + (interest ? ' on' : '')} aria-pressed={interest} onClick={() => switchView('interest')}>내 관심</button>
            </div>
            <div style={{ marginTop: 24, background: '#fff', borderRadius: 16, boxShadow: '0 10px 30px rgba(21,24,30,.07)', display: 'grid', gridTemplateColumns: 'repeat(3,1fr)' }}>
              {data.kpis.map(k => (
                <div key={k.label} style={{ padding: '22px 24px', borderRight: '1px solid #E3E6EA' }}>
                  <p style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 15, fontWeight: 700 }}><Icon name="flag" />{k.label}</p>
                  <p style={{ marginTop: 10, fontSize: 28, fontWeight: 800 }}>{k.value}<span style={{ fontSize: 13, fontWeight: 500, color: '#5C6370' }}> {k.unit}</span></p>
                  <div style={{ marginTop: 10, display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '5px 12px', fontSize: 13 }}>
                    {k.rows.map(([l, v, d]) => <FragmentPair key={l} label={l} value={v} color={DIR_COLOR[d]} />)}
                  </div>
                </div>
              ))}
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
            <div style={{ marginTop: 14, borderTop: '2px solid #15181E' }}>
              {data.fx.map(([cur, unit, rate, change, dir]) => (
                <div key={cur} style={{ display: 'grid', gridTemplateColumns: '1fr auto auto', gap: 14, alignItems: 'center', padding: '13px 4px', borderBottom: '1px solid #E3E6EA', fontSize: 15 }}>
                  <span><strong>{cur}</strong> <span style={{ fontSize: 12, color: '#5C6370' }}>{unit}</span></span>
                  <span style={{ fontWeight: 700 }}>{rate}</span>
                  <span style={{ minWidth: 108, textAlign: 'right', fontSize: 13, fontWeight: 700, color: DIR_COLOR[dir] }}>{dir === 'up' ? '▲' : '▼'}{change}</span>
                </div>
              ))}
              {!data.fx.length && <p className="muted" style={{ padding: '13px 4px' }}>관심 국가의 환율 정보가 없습니다.</p>}
            </div>
          </div>
        </div>
      </section>

      <section style={{ background: '#15181E', color: '#ECEEF1', padding: '48px 0 52px' }}>
        <div className="wrap">
          <div className="between" style={{ alignItems: 'flex-end' }}>
            <div>
              <h2 style={{ fontSize: 26, fontWeight: 800, color: '#fff' }}>{data.rankTitle}</h2>
              <p style={{ marginTop: 8, fontSize: 13, color: '#A3AAB6' }}>{data.rankSub.replace('최근 3개월', `최근 ${months}개월`)}</p>
            </div>
            <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
              {[3, 6, 12].map(m => (
                <button key={m} type="button" className="reset" aria-pressed={months === m} onClick={() => setMonths(m)}
                  style={{ height: 34, padding: '0 14px', borderRadius: 17, background: months === m ? '#fff' : '#262A33', color: months === m ? '#15181E' : '#C9CED6', fontSize: 13, fontWeight: months === m ? 800 : 400, display: 'inline-flex', alignItems: 'center' }}>{m}개월</button>
              ))}
              <Link to="/rank" style={{ marginLeft: 10, fontSize: 14, fontWeight: 700, color: '#FF8A9A' }}>상세보기 →</Link>
            </div>
          </div>
          <div style={{ marginTop: 22, display: 'grid', gridTemplateColumns: `repeat(${interest ? Math.min(Math.max(cards.length, 2), 5) : 5},1fr)`, gap: 14 }} className="resp-stack">
            {cards.map(c => (
              <Link key={c.hs} to={`/rank?product=${c.hs}`} style={{ background: '#1F232B', border: '1px solid #2C313B', borderRadius: 18, padding: 18, color: '#ECEEF1', display: 'flex', flexDirection: 'column', gap: 10 }}>
                <span style={{ display: 'flex', gap: 6 }}>
                  <span style={{ padding: '3px 8px', borderRadius: 6, background: '#3A3016', color: '#F2C14E', fontSize: 12, fontWeight: 800 }}>{c.rank}</span>
                  <span style={{ padding: '3px 8px', borderRadius: 6, background: '#2C313B', color: '#C9CED6', fontSize: 12 }}>{c.hs}</span>
                </span>
                <span style={{ fontSize: 18, fontWeight: 800 }}>{c.name}</span>
                <span style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end' }}>
                  <span>
                    <span style={{ display: 'block', fontSize: 12, color: '#A3AAB6' }}>수출액</span>
                    <span style={{ display: 'block', fontSize: 22, fontWeight: 800 }}>{c.exp}</span>
                    <span style={{ display: 'block', fontSize: 12, color: '#A3AAB6' }}>수입 {c.imp}</span>
                  </span>
                  <Sparkline values={c.values} color={c.color} />
                </span>
              </Link>
            ))}
          </div>
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
              <Link to="/rank" className="more">상세보기 →</Link>
            </div>
            <div className="th" style={{ gridTemplateColumns: '22px 86px 1fr 64px 60px', marginTop: 14 }}>
              <span>순위</span><span>HS6</span><span>품목</span><span className="r">수출액</span><span className="r">수입액</span>
            </div>
            {data.productTable.rows.map(([rank, hs, name, exp, imp]) => (
              <div key={hs} className="tr" style={{ gridTemplateColumns: '22px 86px 1fr 64px 60px' }}>
                <span className="b">{rank}</span><span><span className="hs">{hs}</span></span><span className="b" style={{ fontWeight: 700 }}>{name}</span><span className="r b">{exp}</span><span className="r s">{imp}</span>
              </div>
            ))}
            {data.productTable.note && <p className="note" style={{ marginTop: 12 }}>{data.productTable.note}</p>}
          </div>
          <div className="card" style={{ flex: '1.3 1 540px' }}>
            <div className="between" style={{ alignItems: 'flex-start' }}>
              <div>
                <h2 className="t2">{data.countryTable.title}</h2>
                <p className="muted" style={{ marginTop: 6 }}>{data.countryTable.sub}</p>
              </div>
              <Link to="/rank" className="more">상세보기 →</Link>
            </div>
            <div style={{ display: 'flex', gap: 26, marginTop: 14 }}>
              <div style={{ flex: '1 1 260px' }}>
                <div className="th" style={{ gridTemplateColumns: '22px 1fr 64px 60px' }}>
                  <span>순위</span><span>교역국</span><span className="r">수출액</span><span className="r">수입액</span>
                </div>
                {data.countryTable.rows.map(([rank, name, exp, imp]) => (
                  <div key={name} className="tr" style={{ gridTemplateColumns: '22px 1fr 64px 60px' }}>
                    <span className="b">{rank}</span><span style={{ fontWeight: 700 }}>{name}</span><span className="r b">{exp}</span><span className="r s">{imp}</span>
                  </div>
                ))}
              </div>
              <div style={{ flex: '1 1 200px', display: 'flex', flexDirection: 'column', justifyContent: 'center', gap: 14 }}>
                <p style={{ fontSize: 14, fontWeight: 800 }}>{data.countryTable.pieTitle}</p>
                <div style={{ display: 'flex', alignItems: 'center', gap: 18 }}>
                  <Donut items={data.countryTable.pie.map(([label, color]) => [label, parseFloat(label.split(' ').pop()), color])} />
                  <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 6, fontSize: 13, color: '#2E333C' }}>
                    {data.countryTable.pie.map(([label, color]) => (
                      <li key={label} style={{ display: 'flex', alignItems: 'center', gap: 7 }}><span style={{ width: 10, height: 10, borderRadius: 2, background: color }} />{label}</li>
                    ))}
                  </ul>
                </div>
                <p className="muted" style={{ fontSize: 12 }}>{data.countryTable.pieNote}</p>
              </div>
            </div>
          </div>
        </section>

        <section>
          <div className="sec-h">
            <div>
              <h2 className="t1">UN data</h2>
              <p className="muted" style={{ marginTop: 6 }}>{data.unSub}</p>
            </div>
            <Link to="/account/interests" className="more">관심정보 설정 →</Link>
          </div>
          <div style={{ marginTop: 14, display: 'flex', gap: '6px 24px', fontSize: 13, color: '#5C6370', flexWrap: 'wrap' }}>
            <span><strong style={{ color: '#15181E' }}>수출 점유율</strong> = 상대국 전체 수입 중 한국산 비중</span>
            <span><strong style={{ color: '#15181E' }}>수입 점유율</strong> = 상대국 전체 수출 중 한국행 비중</span>
            <span>순위 = 상대국 공급국 중 한국 순위</span>
          </div>
          <div style={{ marginTop: 16, display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 16 }} className="resp-stack">
            {data.un.map(u => (
              <div key={u.country} style={{ border: '1px solid #E3E6EA', borderTop: '3px solid #C8102E', borderRadius: '4px 4px 16px 16px', padding: '16px 18px 6px' }}>
                <div className="between" style={{ alignItems: 'baseline' }}>
                  <span style={{ fontSize: 20, fontWeight: 800 }}>{u.country}</span><span className="muted" style={{ fontSize: 12 }}>{u.meta}</span>
                </div>
                {u.items.map(it => (
                  <div key={it.hs} style={{ marginTop: 12, padding: '12px 0 10px', borderTop: '1px solid #E3E6EA', display: 'flex', flexDirection: 'column', gap: 8 }}>
                    <div className="between" style={{ fontSize: 13 }}>
                      <span style={{ fontWeight: 600 }}>{it.name} <span className="s" style={{ fontWeight: 400 }}>HS {it.hs}</span></span><span className="hs">{it.rank} · {it.period}</span>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
                      <span className="muted" style={{ fontSize: 12 }}>수출 점유율</span>
                      <span style={{ fontSize: 24, fontWeight: 800 }}>{it.share}</span>
                      <span style={{ fontSize: 13, fontWeight: 600, color: DIR_COLOR[it.dir] }}>{it.dir === 'up' ? '▲' : '▼'} {it.change}</span>
                    </div>
                    <div style={{ height: 8, borderRadius: 4, background: '#F1F2F5', overflow: 'hidden' }}><div style={{ height: 8, background: '#C8102E', width: it.share }} /></div>
                    <div className="muted" style={{ fontSize: 12 }}>수입 점유율 <strong style={{ color: '#15181E' }}>{it.importShare}</strong> · 상대국 수출 중 한국행</div>
                  </div>
                ))}
              </div>
            ))}
          </div>
<ErrorNotice error={un.error} /><p className="muted" style={{ marginTop: 10, fontSize: 12 }}>{profile.products.length ? '조회 조건에 공개된 UN 관측값만 표시합니다. 빈 결과는 미수집 상태이며 0%를 뜻하지 않습니다.' : '관심 HS6 품목을 등록하면 공개된 UN 데이터를 조회합니다.'}</p>
        </section>

        <section>
          <div className="sec-h">
            <div>
              <h2 className="t1">{data.newsTitle}</h2>
              <p className="muted" style={{ marginTop: 6 }}>{data.newsSub}</p>
            </div>
            <Link to="/articles" className="more">기사홈 →</Link>
          </div>
          <div style={{ marginTop: 20, display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 22 }} className="resp-stack">
            {data.newsCards.map(n => {
              const found = n
              return (
                <Link key={n.title} to={found ? `/articles?tab=${found.kind === 'general' ? 'all' : found.kind}&id=${found.id}` : '/articles'} style={{ color: '#15181E', display: 'flex', flexDirection: 'column', gap: 10 }}>
                  <img src={n.img} alt="" style={{ width: '100%', height: 170, borderRadius: 14, objectFit: 'cover', display: 'block' }} />
                  <span style={{ fontSize: 12, fontWeight: 800, color: '#B0102A' }}>{({ major: '주요 이슈', short: '단신속보', general: '해외시장뉴스' })[n.kind]} <span style={{ fontWeight: 500, color: '#5C6370' }}>· {n.date}</span></span>
                  <span style={{ fontSize: 16, fontWeight: 800, lineHeight: 1.45 }}>{n.title}</span>
                  <span style={{ fontSize: 13, lineHeight: 1.6, color: '#5C6370' }}>{n.summary}</span>
                </Link>
              )
            })}
          </div>
        </section>
      </main>
      <div style={{ height: 72 }} />
    </>
  )
}

function FragmentPair({ label, value, color }) {
  return <><span className="s">{label}</span><span style={{ fontWeight: 700, color }}>{value}</span></>
}
