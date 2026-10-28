import { useQuery } from '../api/hooks'
const table = (head, rows) => ({ type: 'table', head, cols: `repeat(${head.length}, minmax(100px, 1fr))`, rows: rows.map(cells => ({ cells: cells.map(c => c == null ? '—' : typeof c === 'object' ? c : String(c)) })) })
const button = (label, to) => ({ btns: [{ label, to }] })
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
  const status = ({ BS41: 'ACTIVE', BS43: 'WITHDRAWN', BS45: 'RESTRICTED' })[id]
  const paged = ['/admin/alerts', '/admin/users', '/admin/data/runs', '/admin/articles', '/admin/notification-deliveries', '/admin/audit-events'].includes(endpoint)
  const filters = paged ? { page: Number(query.get('page')) || 1, page_size: 20 } : {}
  if (endpoint === '/admin/users') { filters.q = query.get('q') || undefined; filters.status = status }
  if (endpoint === '/admin/articles') filters.q = query.get('q') || undefined
  const result = useQuery(endpoint, filters, !!endpoint)
  if (!template) return { result, screen: null }
  const items = result.data?.items || result.data?.rules || []
  const blocks = []
  // Keep the original navigation, member search, overview cards and table styling.
  for (const b of template.blocks) if (['tabs','memberSearch'].includes(b.type)) blocks.push(b)
  if (endpoint === '/admin/overview') blocks.push(...template.blocks.filter(b => b.type === 'ops'))
  else if (endpoint === '/admin/users') {
    if (!blocks.some(b => b.type === 'memberSearch')) blocks.push({ type: 'memberSearch', to: '/admin/members/BS49' })
    blocks.push(table(['아이디(이메일)', '이름', '권한', '이용 상태', '관리'], items.map(r => [r.email, r.name, r.role, r.account_status, button('상세', `/admin/members/BS48?user_id=${r.user_id}`)])))
  } else if (endpoint === '/admin/alerts') blocks.push(table(['분류', '알림 내용', '발생 시각', '관리'], items.map(r => [r.category, r.message, r.created_at, button('수집 이력', '/admin/inbox/collection')])))
  else if (endpoint === '/admin/data/runs') blocks.push(table(['실행 ID', '수집 작업', '대상 기간', '결과', '수신 건수'], items.map(r => [r.run_id, r.job_name, `${r.period_start} ~ ${r.period_end}`, r.status, r.received_count])))
  else if (endpoint === '/admin/articles') blocks.push(table(['기사 ID', '원문 분류', '노출 상태', '주제', '관리'], items.map(r => [r.article_id, r.original_category, r.visibility, r.kw_topic.join(', '), button('상세', `/admin/data/BS80?article_id=${r.article_id}`)])))
  else if (endpoint === '/admin/audit-events') blocks.push(table(['처리 시각', '작업', '대상', '사유'], items.map(r => [r.created_at, r.action, `${r.target_type} ${r.target_id}`, r.reason])))
  else if (endpoint === '/admin/operation-settings' || endpoint === '/admin/notification-rules') blocks.push(table(['설정', '활성 여부', '값'], items.map(r => [r.label_ko, r.enabled ? '활성' : '비활성', r.value_text])))
  else if (endpoint === '/admin/notification-deliveries') blocks.push(table(['알림 ID', '회원 ID', '편입 결과', '팝업 확인', '읽음'], items.map(r => [r.user_alert_id, r.user_id, r.result, r.popup_shown_at, r.read_at])))
  else if (endpoint === '/admin/article-sources') blocks.push(table(['출처', '수집 활성', '기사 수', '노출 기사 수'], items.map(r => [r.source_code, r.collection_enabled ? '활성' : '비활성', r.article_count, r.visible_count])))
  else blocks.push({ type: 'banner', tone: 'info', text: '이 기능은 서버의 저장·운영 정책 확정 후 제공됩니다.' })
  return { result, user, article, screen: { ...template, lead: template.head.title, head: { ...template.head, sub: '서버에 저장된 정보를 조회합니다.', actions: undefined }, blocks, modal: null } }
}
