"""에이전트 tool. 설계: docs/AGENT_DESIGN.md §5."""
from backend.app.schemas.rag import RagRequest, RagResponse

NEWS_PENDING_MESSAGE = '기사 검색은 지원 예정입니다.'


def search_news(request: RagRequest) -> RagResponse | None:
    """RAG 담당자가 구현할 기사 검색 자리. None이면 tool은 NO_DATA + NEWS_PENDING_MESSAGE로 처리한다(D-43).
    TODO(C-15): RAG 모듈이 연결되면 이 함수가 RagResponse를 돌려준다."""
    return None


def article_result(state, task, step):
    from backend.app.agents.digest import bounded
    from backend.app.agents.state import ToolResult
    conditions=task.conditions
    request=RagRequest(request_id=f'{state.input.job_id}:{step.step_id}',query=task.question,use_case='chat',
        conditions={'geography':{'mode':'selected' if conditions.country_codes else 'all','codes':conditions.country_codes},
                    'products':conditions.hs_items,'period':conditions.article_period,'blocked_topics':()},limit=5)
    response=search_news(request)
    if response is None: return ToolResult(status='NO_DATA',error=NEWS_PENDING_MESSAGE)
    if response.request_id!=request.request_id or response.applied_conditions!=request.conditions:
        raise ValueError('기사 검색 결과의 요청 조건이 다릅니다.')
    if response.status=='failed': return ToolResult(status='FAILED',error='기사 검색에 실패했습니다.')
    if not response.articles: return ToolResult(status='NO_DATA',error='조건에 맞는 기사가 없습니다.')
    items=[]
    for a in response.articles:
        if not conditions.article_period.start<=a.published_date<=conditions.article_period.end:
            raise ValueError('기사 날짜가 요청 범위 밖입니다.')
        items.append({'article_id':a.article_id,'title':a.title[:100],'published_date':a.published_date.isoformat(),
            'source_code':a.source_code.value,'evidence_sufficient':bool(a.summary and a.evidence),
            'summary':[s.text[:200] for s in a.summary]})
    partial=response.status!='ok' or any(not a['evidence_sufficient'] for a in items)
    return ToolResult(status='OK',article_ids=[a['article_id'] for a in items],article_period=conditions.article_period,
        completeness='PARTIAL' if partial else 'COMPLETE',limitations=['기사 근거 또는 검색 범위가 부족합니다.'] if partial else [],
        digest=bounded({'kind':'ARTICLES','article_period':conditions.article_period.model_dump(mode='json'),
                        'rag_status':response.status,'items':items,'rows_total':len(items)}))


HS_LOOKUP_LIMIT = 10   # 표현 하나의 후보 수(HS4·HS6 각 5개)


def hs_lookup_result(store, task):
    """D-101: 품목 표현 → HS 코드, HS 코드 → 품목명. 조건을 확정하지 않고 HS2022 사전 검색 결과만 돌려준다."""
    from backend.app.agents.digest import bounded
    from backend.app.agents.state import ToolResult
    from backend.app.api.reference import _search_depth
    expressions = (task.slots.products if task.slots else None) or []
    if not expressions:
        return ToolResult(status='NO_DATA', error='품목명이나 HS 코드를 함께 입력해 주세요.')
    items, missing = [], []
    for expr in expressions:
        found = {}
        for depth in (4, 6):
            for row in _search_depth(store, expr, depth, 5)['items']:
                found.setdefault(row['hs_code'], {'query': expr, 'code': row['hs_code'], 'name': row['name_ko'],
                                                  'level': f'HS{depth}'})
        if found: items.extend(list(found.values())[:HS_LOOKUP_LIMIT])
        else: missing.append(expr)
    if not items:
        return ToolResult(status='NO_DATA', error=f'“{missing[0][:40]}”에 해당하는 HS 코드를 찾지 못했습니다.')
    return ToolResult(status='OK', completeness='PARTIAL' if missing else 'COMPLETE',
        limitations=[f'“{m[:40]}”에 해당하는 HS 코드를 찾지 못했습니다.' for m in missing][:5],
        digest=bounded({'kind': 'HS_CODES', 'hs_ver': 'HS2022', 'items': items, 'rows_total': len(items)}))
