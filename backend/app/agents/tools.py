"""에이전트 tool. 설계: docs/AGENT_DESIGN.md §5, §13.4, §14.4."""
import time

from openai import OpenAI

from backend.app.api.config import Settings
from backend.app.schemas.rag import RagRequest, RagResponse
from backend.app.services.rag.factory import create_service
from backend.app.services.rag.runtime import ExecutionContext, RagDeadlineExceeded

CHAT_SEARCH_SECONDS = 30
CHAT_FINISH_RESERVE_SECONDS = 1


def search_news(request: RagRequest, *, article_allowed=None) -> RagResponse:
    """챗봇의 기사 검색 요청을 공통 RAG 서비스에 전달한다."""
    request = RagRequest.model_validate(request)
    regular_chat = request.use_case == 'chat' and request.order_by == 'relevance'
    context = ExecutionContext(time.monotonic() + (CHAT_SEARCH_SECONDS if regular_chat else 15),
                               finish_reserve=CHAT_FINISH_RESERVE_SECONDS if regular_chat else 0)
    settings = Settings()
    root = settings.get('NEWS_VECTOR_ROOT')
    with OpenAI(api_key=settings.get('OPENAI_API_KEY'), timeout=context.remaining(), max_retries=0) as api:
        return create_service(api, root=root, article_allowed=article_allowed).run(request, context)


def article_result(state, task, step):
    from backend.app.agents.digest import bounded
    from backend.app.agents.state import ToolResult
    conditions=task.conditions
    request=RagRequest(request_id=f'{state.input.job_id}:{step.step_id}',query=task.question,use_case='chat',
        conditions={'geography':{'mode':'selected' if conditions.country_codes else 'all','codes':conditions.country_codes},
                    'products':conditions.hs_items,'period':conditions.article_period,'blocked_topics':()},limit=5)
    try:
        response=search_news(request)
    except RagDeadlineExceeded:
        return ToolResult(status='NO_DATA',error='기사 검색 시간이 끝나 검수를 통과한 기사가 없습니다.')
    if response.request_id!=request.request_id or response.applied_conditions!=request.conditions:
        raise ValueError('기사 검색 결과의 요청 조건이 다릅니다.')
    timed_out=any(f.stage=='timeout' for f in response.failures)
    if response.status=='failed':
        if timed_out:
            return ToolResult(status='NO_DATA',error='기사 검색 시간이 끝나 검수를 통과한 기사가 없습니다.')
        return ToolResult(status='FAILED',error='기사 검색에 실패했습니다.')
    if not response.articles: return ToolResult(status='NO_DATA',error='조건에 맞는 기사가 없습니다.')
    items=[]
    for a in response.articles:
        if not conditions.article_period.start<=a.published_date<=conditions.article_period.end:
            raise ValueError('기사 날짜가 요청 범위 밖입니다.')
        items.append({'article_id':a.article_id,'title':a.title[:100],'published_date':a.published_date.isoformat(),
            'source_code':a.source_code.value,'evidence_sufficient':bool(a.summary and a.evidence),
            'summary':[s.text[:200] for s in a.summary]})
    partial=response.status!='ok' or any(not a['evidence_sufficient'] for a in items)
    limitation=('기사 검색 시간이 끝나 확인을 마친 결과만 표시합니다.' if timed_out
                else '기사 근거 또는 검색 범위가 부족합니다.')
    return ToolResult(status='OK',article_ids=[a['article_id'] for a in items],article_period=conditions.article_period,
        completeness='PARTIAL' if partial else 'COMPLETE',limitations=[limitation] if partial else [],
        digest=bounded({'kind':'ARTICLES','article_period':conditions.article_period.model_dump(mode='json'),
                        'rag_status':response.status,'items':items,'rows_total':len(items)}))
