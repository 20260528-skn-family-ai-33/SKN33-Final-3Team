// 기사(KOTRA 해외시장뉴스 형식) 예시. 배열 순서 = 발행일 최신순(목업 목록 순서와 맞춤).
export const articleKinds = {
  general: { label: '일반기사', tone: '' },
  short: { label: '단신속보', tone: 'red' },
  major: { label: '주요이슈', tone: 'plum' },
}

export const articleTabs = [['all', '전체기사'], ['short', '단신속보'], ['major', '주요이슈']]

const SUMMARY = '현지 수요와 유통 채널, 교역 환경 변화를 정리한 기사입니다. 화면 시연용 요약이며 실제 기사 본문은 서버 연동 후 원문에서 가져옵니다.'
const KOTRA = 'KOTRA 해외시장뉴스'

const raw = [
  ['a1', 'major', '중국 배터리 소비세 정책 조정', '중국', '2026.10.08', KOTRA, ['통상·규제'], '배터리 소비세 정책 조정에 따라 통관 상품번호가 세분화됩니다. 수출 품목의 HS 코드 분류를 다시 확인해야 합니다.'],
  ['s1', 'short', '라오스 디지털 금융 서비스 확대', '라오스', '2026.10.08', '단신속보', []],
  ['g1', 'general', '미국 반도체 공급망 투자 동향', '미국', '2026.10.07', KOTRA, []],
  ['a2', 'major', '관세 관련 기사 언급량 증가', '전체', '2026.10.07', KOTRA, [], null, '3.1배'],
  ['s2', 'short', '태국 관광·기업 홍보 행사 개최', '태국', '2026.10.06', '단신속보', []],
  ['a3', 'major', '가나 중고차 수입 요건 강화', '가나', '2026.10.06', KOTRA, ['통상·규제']],
  ['s3', 'short', '중국 전자제품 시장 동향', '중국', '2026.10.06', '단신속보', []],
  ['g2', 'general', '일본 반도체 소재 시장 변화', '일본', '2026.10.05', KOTRA, []],
  ['a4', 'major', '반도체 관련 기사량 급증', '전체', '2026.10.05', KOTRA, [], null, '3.4배'],
  ['s4', 'short', '베트남 물류 서비스 확대', '베트남', '2026.10.05', '단신속보', []],
  ['a5', 'major', '주요 수출시장 통관 규정 변경', '중국', '2026.10.04', KOTRA, ['통상·규제']],
  ['s5', 'short', '일본 산업 전시회 개최', '일본', '2026.10.04', '단신속보', []],
  ['g3', 'general', '유럽 반도체 산업 지원 동향', '유럽', '2026.10.04', KOTRA, []],
  ['a6', 'major', '중국 반도체 시장 수요 변화', '중국', '2026.10.07', KOTRA, []],
  ['s6', 'short', '베트남 반도체 부품 수요 확대', '베트남', '2026.10.06', KOTRA, []],
  ['s9', 'short', '중국 메모리 반도체 시장 동향과 수요 변화', '중국', '2026.10.08', KOTRA, []],
  ['a8', 'major', '미국 메모리 수입 관세 적용 기준 점검', '미국', '2026.10.07', KOTRA, ['통상·규제']],
  ['s7', 'short', '베트남, 화장품 수입 위생허가 서류 간소화', '베트남', '2026.10.07', KOTRA, []],
  ['g6', 'general', '베트남 공급망 관련 기사', '베트남', '2026.10.07', KOTRA, []],
  ['a7', 'major', '미국 수입 승용차 관세 적용 기준 점검', '미국', '2026.10.06', KOTRA, ['통상·규제']],
  ['g5', 'general', '베트남 화장품 유통 채널 온라인 전환 가속', '베트남', '2026.10.06', KOTRA, []],
  ['g4', 'general', '일본 석유제품 수입 수요와 공급선 변화', '일본', '2026.10.05', KOTRA, []],
  ['s10', 'short', '중국 화장품 수입 위생허가 절차 변경', '중국', '2026.10.05', KOTRA, []],
  ['s8', 'short', '독일 산업용 전자부품 수입 수요 회복세', '독일', '2026.10.04', KOTRA, []],
  ['a9', 'major', '주요 교역국 통상 이슈', '전체', '2026.10.03', KOTRA, ['통상·규제']],
]

const HEADLINES = { a1: '중국 배터리 소비세 정책 조정에 따른 통관 상품번호 세분화' }

export const articles = raw.map(([id, kind, title, country, date, source, tags, summary, surge]) => ({
  id, kind, title, country, date, source, tags, surge: surge || null, headline: HEADLINES[id] || title,
  summary: summary || SUMMARY,
  url: 'https://dream.kotra.or.kr/kotranews/index.do',
}))

export const articleById = id => articles.find(a => a.id === id)

/** 키워드·기간·탭 조건으로 기사 목록을 거른다. 기간은 'YYYY-MM-DD' 문자열. */
export function searchArticles({ tab = 'all', q = '', from = '', to = '' }) {
  const norm = d => d.replaceAll('.', '-')
  return articles.filter(a =>
    (tab === 'all' || a.kind === tab) &&
    (!q || (a.title + a.country + a.tags.join(' ')).includes(q.trim())) &&
    (!from || norm(a.date) >= from) && (!to || norm(a.date) <= to),
  )
}
