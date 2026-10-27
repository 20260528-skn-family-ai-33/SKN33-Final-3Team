// 로그인·회원가입·계정 찾기 화면의 공통 틀(항구 사진 배경 + 가운데 카드)
const SOURCES = ['관세청 수출입 통계', 'KOTRA 해외시장뉴스', '한국은행 환율']

export function AuthFrame({ lead, showSources = !!lead, children }) {
  return (
    <main style={{ position: 'relative', isolation: 'isolate', minHeight: 860, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '56px 24px', overflow: 'hidden' }}>
      <section className="band lgbg" style={{ position: 'absolute', inset: 0, padding: 0, zIndex: -1 }} aria-hidden="true" />
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 28, width: '100%' }}>
        <div style={{ textAlign: 'center', color: '#fff', maxWidth: 640 }}>
          <p className="eyebrow">BORDERLESS SELLERS</p>
          <h1 style={{ fontSize: 36, lineHeight: 1.25, letterSpacing: -1, fontWeight: 800, color: '#fff' }}>국경 너머 시장을<br />데이터로 먼저 읽다</h1>
          {lead && <p style={{ marginTop: 10, fontSize: 15, lineHeight: 1.7, color: '#D9DEE6' }}>{lead}</p>}
          {showSources && (
            <div style={{ display: 'flex', gap: 10, marginTop: 18, justifyContent: 'center', flexWrap: 'wrap' }}>
              {SOURCES.map(s => <span key={s} className="tag" style={{ background: 'rgba(255,255,255,.14)', color: '#fff' }}>{s}</span>)}
            </div>
          )}
        </div>
        <section style={{ background: '#fff', borderRadius: 20, boxShadow: '0 30px 80px rgba(6,16,26,.35)', padding: '40px 44px', maxWidth: '100%' }}>
          <div style={{ width: 440, maxWidth: '100%' }}>{children}</div>
        </section>
      </div>
    </main>
  )
}

export function CardHead({ eyebrow, title, desc, size = 32 }) {
  return (
    <>
      <p style={{ fontSize: 14, fontWeight: 700, color: 'var(--red-d)' }}>{eyebrow}</p>
      <h2 className="t1" style={{ marginTop: 8, fontSize: size }}>{title}</h2>
      {desc && <p className="muted" style={{ marginTop: 8, fontSize: 15, lineHeight: 1.6 }}>{desc}</p>}
    </>
  )
}

/** 아이콘이 가운데 놓인 결과 안내(메일 발송 완료·회원정보 없음) */
export function ResultHead({ icon, tone = 'gray', eyebrow, title, desc }) {
  return (
    <div style={{ textAlign: 'center' }}>
      <span style={{ width: 56, height: 56, borderRadius: 28, background: tone === 'rose' ? 'var(--rose)' : '#F1F2F5', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', marginBottom: 16 }}>{icon}</span>
      <p style={{ fontSize: 14, fontWeight: 700, color: 'var(--red-d)' }}>{eyebrow}</p>
      <h2 className="t1" style={{ marginTop: 8, fontSize: 28 }}>{title}</h2>
      <p className="muted" style={{ marginTop: 10, fontSize: 15, lineHeight: 1.7 }}>{desc}</p>
    </div>
  )
}

/** 메일 미리보기 상자 */
export function MailPreview({ subject, children }) {
  return (
    <div style={{ marginTop: 22, border: '1px solid var(--line)', borderRadius: 14, overflow: 'hidden' }}>
      <div style={{ background: 'var(--bg)', padding: '10px 14px', borderBottom: '1px solid var(--line)' }}>
        <p className="note" style={{ fontWeight: 700 }}>발송 메일 미리보기</p>
        <p className="note">보낸 사람 · Borderless Sellers {'<'}no-reply@borderless.example{'>'}</p>
        <p style={{ fontSize: 13, fontWeight: 800, marginTop: 2 }}>{subject}</p>
      </div>
      <div style={{ padding: '16px 16px 18px', fontSize: 13.5, lineHeight: 1.7, color: '#2D333D' }}>{children}</div>
    </div>
  )
}

/** 소셜 로그인 연결처럼 흰 바탕의 단순 화면(LG02~LG06) */
export function PlainAuthPage({ eyebrow, title, desc, children }) {
  return (
    <main className="page white" style={{ minHeight: 760, display: 'flex', alignItems: 'flex-start', justifyContent: 'center', padding: '64px 24px' }}>
      <div style={{ width: 720, maxWidth: '100%' }}>
        <p style={{ fontSize: 14, fontWeight: 700, color: 'var(--red-d)' }}>{eyebrow}</p>
        <h2 className="t1" style={{ fontSize: 34, marginTop: 8 }}>{title}</h2>
        {desc && <p className="muted" style={{ fontSize: 15, marginTop: 10 }}>{desc}</p>}
        {children}
      </div>
    </main>
  )
}
