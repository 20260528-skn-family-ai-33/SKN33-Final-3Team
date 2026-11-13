// 로그인·회원가입·계정 찾기 화면의 공통 틀(배경 사진 + 가운데 카드)
export function AuthFrame({ lead, children }) {
  return (
    <main style={{ position: 'relative', isolation: 'isolate', minHeight: 860, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '56px 24px', overflow: 'hidden' }}>
      <section className="band lgbg" style={{ position: 'absolute', inset: 0, padding: 0, zIndex: -1 }} aria-hidden="true" />
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 28, width: '100%' }}>
        <div style={{ textAlign: 'center', color: '#fff', maxWidth: 640 }}>
          {/* 서비스 이름: 하늘색 + 넓은 자간, 양옆 가는 선 */}
          <p className="auth-eyebrow">BORDERLESS SELLERS</p>
          <h1 className="auth-title">국경 너머 시장을<br />데이터로 먼저 읽다</h1>
          {lead && <p className="auth-lead">{lead}</p>}
        </div>
        <section className="auth-card" style={{ background: '#fff', borderRadius: 20, boxShadow: '0 30px 80px rgba(6,16,26,.35)', padding: '40px 44px', maxWidth: '100%' }}>
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
