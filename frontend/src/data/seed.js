// 브라우저에 처음 저장되는 체험 데이터(계정·리포트·알림·채팅). 서버 연동 시 API 응답으로 대체한다.

export const ALL_SECTIONS = ['주요 지표(KPI)', '주요 수출국', '급성장·리스크 시장', '현지 이슈 기사']

/** 체험 계정. 비밀번호는 README의 체험 계정 표와 같다. */
export const seedAccounts = [
  { email: 'demo@example.com', password: 'demo1234!', name: '체험 사용자', provider: 'email', joined: '2026.10.01', role: 'user' },
  { email: 'honggd@example.com', password: 'hong1234!', name: '홍길동', provider: 'email', joined: '2026.03.14', role: 'user' },
  { email: 'gildong.h@gmail.com', password: null, name: '홍길동', provider: 'google', joined: '2026.08.02', role: 'user' },
  { email: 'admin@example.com', password: 'admin1234!', name: '운영 관리자', provider: 'email', joined: '2026.01.02', role: 'admin' },
]

export const providerLabel = { email: '이메일', kakao: '카카오', naver: '네이버', google: '구글' }

export function maskEmail(email) {
  const [id, domain] = email.split('@')
  return `${id.slice(0, 2)}***@${domain}`
}

export const initialProfile = {
  name: '체험 사용자', email: 'demo@example.com', countries: ['중국', '미국', '베트남'], products: ['854232', '330499'], home: 'all',
}

export const initialSettings = {
  all: true, surge: true, major: true, report: true, breaking: 'all', scope: 'interest', termsAgreed: true, termsVersion: 'v1.2',
}

export const initialConsents = { service: true, privacy: true, optional: false }

export const initialSubscription = { cycles: ['weekly', 'monthly'], formats: ['PDF'] }

const opt = (country, product, extra = {}) => ({
  country, product, from: '2025-10', to: '2025-12', compareFrom: '2024-10', compareTo: '2024-12', sections: ALL_SECTIONS, ...extra,
})

export const reportKinds = {
  chat: { label: '채팅 분석', tab: '채팅에서 생성된 리포트', eyebrow: '요청형 리포트' },
  weekly: { label: '주간', tab: '주간리포트', eyebrow: '주간 구독 리포트' },
  monthly: { label: '월간', tab: '월간리포트', eyebrow: '월간 구독 리포트' },
  quarterly: { label: '분기', tab: '분기리포트', eyebrow: '분기 구독 리포트' },
}

export const initialReports = [
  { id: 'r-chat-1', kind: 'chat', title: '중국 메모리 수출입 분석', status: 'ready', created: '2026.10.08', pages: 12,
    meta: '생성일 2026.10.08 · PDF 생성 완료', note: '생성 완료 시 요청형 목록에 등록된 리포트', options: opt('중국', '854232') },
  { id: 'r-chat-2', kind: 'chat', title: '미국 화장품 수출 분석', status: 'ready', created: '2026.10.08', pages: 10,
    meta: '생성일 2026.10.08 · PDF 생성 완료', note: '생성 완료 시 요청형 목록에 등록된 리포트', options: opt('미국', '330499') },
  { id: 'r-weekly-1', kind: 'weekly', title: '중국 메모리 뉴스·이슈', status: 'ready', created: '2026.10.05', pages: 6,
    meta: '대상: 2026.09.28~10.04 · 생성: 2026.10.05 · 완료', note: '뉴스·이슈 전용 · 통계 섹션 없음', options: opt('중국', '854232', { sections: ['현지 이슈 기사'] }) },
  { id: 'r-weekly-2', kind: 'weekly', title: '미국 화장품 뉴스·이슈', status: 'delayed', created: '2026.10.05',
    meta: '대상: 2026.09.28~10.04 · 생성 예정 2026.10.05', options: opt('미국', '330499', { sections: ['현지 이슈 기사'] }) },
  { id: 'r-monthly-1', kind: 'monthly', title: '중국 메모리 월간 분석', status: 'ready', created: '2026.01.15', pages: 9,
    meta: '기준 월: 2025.12 · 생성: 2026.01.15 · 완료', note: '전월 2025.11 · 전년 동월 2024.12 비교', options: opt('중국', '854232', { from: '2025-12', to: '2025-12', compareFrom: '2024-12', compareTo: '2024-12' }) },
  { id: 'r-monthly-2', kind: 'monthly', title: '미국 화장품 월간 분석', status: 'delayed', created: '2026.01.15',
    meta: '기준 월: 2025.12 · 생성 예정 2026.01.15', options: opt('미국', '330499') },
  { id: 'r-quarter-1', kind: 'quarterly', title: '중국 메모리 수출입 분석', status: 'ready', created: '2026.07.20', pages: 14,
    meta: '대상 분기 2026년 2분기 · 생성일 2026.07.20 · 생성 완료', note: '전 분기 · 전년 동분기 비교', options: opt('중국', '854232', { from: '2026-04', to: '2026-06', compareFrom: '2025-04', compareTo: '2025-06' }) },
  { id: 'r-quarter-2', kind: 'quarterly', title: '중국 메모리 수출입 분석', status: 'ready', created: '2026.04.20', pages: 14,
    meta: '대상 분기 2026년 1분기 · 생성일 2026.04.20 · 생성 완료', note: '전 분기 · 전년 동분기 비교', options: opt('중국', '854232', { from: '2026-01', to: '2026-03', compareFrom: '2025-01', compareTo: '2025-03' }) },
]

export const notificationTypes = {
  breaking: { label: '속보', tone: 'red', tab: 'breaking' },
  surge: { label: '급증 기사', tone: 'amber', tab: 'surge' },
  major: { label: '주요 이슈', tone: 'plum', tab: 'major' },
  report: { label: '리포트 발행', tone: 'plum', tab: 'report' },
}

export const initialNotifications = [
  { id: 'n1', type: 'report', title: '월간 구독 리포트 발행 완료', time: '10.08 09:20', received: '2026.10.08 09:20', read: false,
    body: '관심정보 기준 월간 리포트가 발행되었습니다.', target: '중국 · 메모리(HS 854232)', period: '2025.12', cycle: '월간 구독', reportId: 'r-monthly-1' },
  { id: 'n2', type: 'breaking', title: '관심 국가 단신속보', time: '10.08 09:00', received: '2026.10.08 09:00', read: false,
    body: '관심 국가의 단신속보가 등록되었습니다.', articleId: 's1' },
  { id: 'n3', type: 'major', title: '주요 교역국 통상 이슈', time: '10.08 08:40', received: '2026.10.08 08:40', read: false,
    body: '주요 통상·정책 변화에 대한 알림입니다.', articleId: 'a9' },
  { id: 'n4', type: 'surge', title: '반도체 기사 언급량 급증', time: '10.07 17:10', received: '2026.10.07 17:10', read: true,
    body: '반도체 관련 기사 언급량이 급증했습니다.', ratio: '3.0배', formula: '어제 12건 ÷ 이틀 전까지 7일 평균 4건 = 3.0배', basis: '판정일 10.08: 10.07 / 09.30~10.06', articleId: 'a4' },
]

/** AI 분석 채팅방. messages의 ai 항목은 kind로 화면을 고른다(condition | progress | done | answer | text). */
export const initialChats = [
  { id: 'c1', title: '중국 반도체 수출입 분석', date: '2026.10.08', status: 'condition',
    messages: [
      { role: 'user', text: '중국의 메모리(HS 854232)를 최근 3개월 기준으로 분석해서\n관련 기사와 함께 PDF 리포트로 만들어줘.' },
      { role: 'ai', kind: 'condition', options: opt('중국', '854232'), preset: '3m' },
    ] },
  { id: 'c2', title: '미국 화장품 진출 검토', date: '2026.10.08', status: 'done', reportId: 'r-chat-2',
    messages: [
      { role: 'user', text: '미국 기초화장품(HS 330499) 수출을 최근 3개월 기준으로 분석하고 리포트로 만들어줘.' },
      { role: 'ai', kind: 'done', options: opt('미국', '330499'), reportId: 'r-chat-2' },
    ] },
  { id: 'c3', title: '베트남 시장 질문', date: '2026.10.08', status: 'answer',
    messages: [
      { role: 'user', text: '베트남 메모리 수출 추이와 증가 이유를 알려줘.' },
      { role: 'ai', kind: 'answer', options: opt('베트남', '854232') },
    ] },
]
