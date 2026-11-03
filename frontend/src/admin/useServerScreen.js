import { useQuery } from '../api/hooks'
const table = (head, rows, cols) => ({ type: 'table', head, cols: cols || `repeat(${head.length}, minmax(100px, 1fr))`, rows: rows.map(cells => ({ cells: cells.map(c => c == null ? '—' : typeof c === 'object' ? c : String(c)) })) })
const button = (label, to) => ({ btns: [{ label, to }] })
// 서버 시각(UTC)을 이 기기 시간대의 yyyy-mm-dd hh:mm:ss로 보여 준다.
export const AUDIT_ACTION = { USER_ROLE: '회원 권한 변경', USER_STATUS: '회원 이용 상태 변경', ARTICLE_VISIBILITY: '기사 노출 변경' }
export const AUDIT_TARGET = { USER: '회원', ARTICLE: '기사' }
const RUN_STATUS = { SUCCESS: ['ok', '성공'], FAILED: ['red', '실패'], RUNNING: ['', '실행 중'] }
const localTime = value => value ? new Date(value).toLocaleString('sv-SE') : null
export function useServerScreen(template, id, query) {
  const user = query.get('user_id'), article = query.get('article_id')
  const endpoint = ['BS32','BS34','BS36','BS38'].includes(id) ? '/admin/overview'
    : ['BS33','BS35','BS37','BS39'].includes(id) ? '/admin/alerts'
    : template?.menu === 'members' ? '/admin/users'
    : ['collection','BS64','BS65','BS66','BS67','BS71','BS72'].includes(id) ? '/admin/data/runs'
    : ['BS77','BS78','BS79','BS80','BS81','BS82','BS83','BS84','BS85','BS86','BS87'].includes(id) ? '/admin/articles'
    : id === 'BS88' ? '/admin/article-sources'
    : ['BS149','BS150','BS151','BS152'].includes(id) ? '/admin/notification-deliveries'
    : ['BS156','BS157','BS158'].includes(id) ? '/admin/audit-events'
    : ['BS159','BS160','BS161','BS163','BS165'].includes(id) ? '/admin/operation-settings'
    : id === 'BS155' ? '/admin/notification-rules' : null
  const status = ({ BS41: 'ACTIVE', BS42: 'WITHDRAWN', BS43: 'RESTRICTED' })[id]
  const paged = ['/admin/alerts', '/admin/users', '/admin/data/runs', '/admin/articles', '/admin/notification-deliveries', '/admin/audit-events'].includes(endpoint)
  const filters = paged ? { page: Number(query.get('page')) || 1, page_size: 20 } : {}
  if (endpoint === '/admin/users') { filters.q = query.get('q') || undefined; filters.by = query.get('q') ? query.get('by') || 'id' : undefined; filters.status = status }
  if (endpoint === '/admin/articles') filters.q = query.get('q') || undefined
  if (endpoint === '/admin/audit-events') { filters.q = query.get('q') || undefined; filters.target_type = query.get('target') || undefined; filters.sort = query.get('sort') || undefined }
  if (endpoint === '/admin/data/runs') { filters.q = query.get('q') || undefined; filters.status = query.get('status') || undefined }
  if (endpoint === '/admin/alerts') filters.category = ({ BS35: 'DATA', BS37: 'REPORT', BS39: 'MEMBER' })[id] || 'ALL'
  // 회원·기사 상세를 열면 그 한 건만 보여 주고 목록은 불러오지 않는다.
  const detailOpen = (endpoint === '/admin/users' && !!user) || (endpoint === '/admin/articles' && !!article)
  // 페이지만 바뀌거나 다시 불러올 때는 이전 행을 유지하고, 탭·분류·검색 조건이 바뀌면 이전 행을 바로 비운다.
  const { page: _page, ...group } = filters
  const result = useQuery(endpoint, filters, !!endpoint && !detailOpen, 0, 'GET', JSON.stringify([endpoint, group]))
  if (!template) return { result, screen: null }
  const items = result.data?.items || result.data?.rules || []
  const blocks = []
  // Keep the original navigation, member search, overview cards and table styling.
  // 상태 탭(정상·탈퇴·이용 제한)에서 검색하면 그 탭에 머물러 상태 조건을 유지한다.
  for (const b of template.blocks) if (['tabs','memberSearch'].includes(b.type)) blocks.push(b.type === 'memberSearch' && status ? { ...b, to: `/admin/members/${id}` } : b)
  if (endpoint === '/admin/overview') blocks.push(...template.blocks.filter(b => b.type === 'ops'))
  else if (endpoint === '/admin/users' && user) blocks.splice(0, blocks.length, { type: 'buttons', items: [{ label: '회원 목록으로', to: '/admin/members/BS40' }] })
  else if (endpoint === '/admin/users') {
    if (!blocks.some(b => b.type === 'memberSearch')) blocks.push({ type: 'memberSearch', to: '/admin/members/BS49' })
    blocks.push(table(['아이디(이메일)', '이름', '권한', '이용 상태', '관리'], items.map(r => [r.email, r.name, r.role, r.account_status, button('상세', `/admin/members/BS48?user_id=${r.user_id}`)]), '1.8fr 1fr 0.8fr 0.9fr 0.8fr'))
  } else if (endpoint === '/admin/alerts') blocks.push(table(['분류', '알림 내용', '발생 시각', '관리'], items.map(r => [r.category, r.message, localTime(r.created_at),
    r.target_type === 'BATCH' ? button('수집 이력', `/admin/inbox/collection?q=${encodeURIComponent(r.target_id)}`) : null]), '0.7fr 2fr 1.2fr 0.8fr'))
  else if (endpoint === '/admin/data/runs') {
    blocks.push({ type: 'runSearch' })
    blocks.push({ ...table(['실행 ID', '수집 작업', '대상 기간', '결과', '수신 건수'], items.map(r => [r.run_id, `${r.source_code} · ${r.job_name}`, `${r.period_start} ~ ${r.period_end}`, RUN_STATUS[r.status] ? { tag: RUN_STATUS[r.status][0], text: RUN_STATUS[r.status][1] } : r.status, r.received_count]), '0.6fr 1.6fr 1.7fr 0.7fr 0.7fr'), empty: query.get('q') || query.get('status') ? '조건에 맞는 수집 이력이 없습니다. 검색어나 결과 분류를 바꿔 보세요.' : '' })
  }
  else if (endpoint === '/admin/articles' && article) blocks.push({ type: 'buttons', items: [{ label: '기사 목록으로', to: '/admin/data/BS77' }] })
  else if (endpoint === '/admin/articles') {
    blocks.push({ type: 'articleSearch', to: '/admin/data/BS77' })
    blocks.push({ ...table(['기사 ID', '원문 분류', '노출 상태', '주제', '관리'], items.map(r => [r.article_id, r.original_category, r.visibility, r.kw_topic.join(', '), button('상세', `/admin/data/BS80?article_id=${r.article_id}`)])), empty: query.get('q') ? '검색 결과가 없습니다. 기사 제목·본문 검색어를 바꿔 보세요.' : '' })
  }
  else if (endpoint === '/admin/audit-events') {
    // 상세는 목록 위에 모달로 연다. 지금 검색·정렬·페이지 조건은 그대로 둔다.
    const detailTo = auditId => { const q = new URLSearchParams(query); q.set('audit', auditId); return `/admin/history/${id}?${q}` }
    blocks.push({ type: 'auditSearch' })
    blocks.push({ ...table(['처리 시각', '처리자', '작업', '대상', '처리 사유', '관리'], items.map(r => [localTime(r.created_at), r.actor_email || `회원 ${r.actor_id}`, AUDIT_ACTION[r.action] || r.action, `${AUDIT_TARGET[r.target_type] || r.target_type} ${r.target_id}`, r.reason, button('상세', detailTo(r.audit_id))]), '1.1fr 1.3fr 1fr 0.8fr 1.4fr 0.6fr'), empty: query.get('q') || query.get('target') ? '조건에 맞는 처리 이력이 없습니다. 검색어나 대상 종류를 바꿔 보세요.' : '' })
  }
  else if (endpoint === '/admin/operation-settings' || endpoint === '/admin/notification-rules') blocks.push(table(['설정', '활성 여부', '값'], items.map(r => [r.label_ko, r.enabled ? '활성' : '비활성', r.value_text])))
  else if (endpoint === '/admin/notification-deliveries') blocks.push(table(['알림 ID', '회원 ID', '편입 결과', '팝업 확인', '읽음'], items.map(r => [r.user_alert_id, r.user_id, r.result, r.popup_shown_at, r.read_at])))
  else if (endpoint === '/admin/article-sources') blocks.push(table(['출처', '수집 활성', '기사 수', '노출 기사 수'], items.map(r => [r.source_code, r.collection_enabled ? '활성' : '비활성', r.article_count, r.visible_count])))
  else blocks.push({ type: 'banner', tone: 'info', text: '이 기능은 서버의 저장·운영 정책 확정 후 제공됩니다.' })
  // 페이지로 나뉜 목록은 표 위에 '이번 페이지 건수 / 전체 건수'를 적는다.
  const total = result.data?.page?.total
  if (paged && total != null) for (const b of blocks) if (b.type === 'table') b.count = `${items.length} / ${total}건`
  return { result, user, article, audit: endpoint === '/admin/audit-events' ? query.get('audit') : null, screen: { ...template, lead: template.head.title, head: { ...template.head, sub: '서버에 저장된 정보를 조회합니다.', actions: undefined }, blocks, modal: null } }
}
