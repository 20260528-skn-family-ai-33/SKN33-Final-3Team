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


def warm_article_index():
    """워커 시작 때 기사 색인을 미리 연다(D-104). 기다리지 않는다. 큰 색인은 처음 여는 데 수십 초가 걸려,
    미리 열지 않으면 재시작 뒤 첫 기사 질문이 검색 예산(30초)을 넘긴다. 실패해도 워커는 돈다(질문 때 다시 연다)."""
    import logging
    import threading
    def run():
        try:
            from backend.app.services.news_collections import ARTICLE_COLLECTION
            from backend.app.services.rag.store import GenerationStore, shared_client
            snapshot = GenerationStore(Settings().get('NEWS_VECTOR_ROOT'), client_factory=shared_client).snapshot()
            snapshot.collection(ARTICLE_COLLECTION).query(
                query_embeddings=[[0.0] * (snapshot.dimensions - 1) + [1.0]], n_results=1)
            snapshot.client.close()   # 빌린 수만 돌려준다. 핸들은 열린 채 남는다(D-105)
        except Exception as exc:
            logging.getLogger(__name__).warning('rag_index_warm_failed type=%s', type(exc).__name__)
    thread = threading.Thread(target=run, name='rag-index-warm', daemon=True)
    thread.start()
    return thread


def article_result(state, task, step, *, article_allowed=None, keep_evidence=None):
    """그래프 기사 단계(§14.4). article_allowed는 공개·사용자 차단 필터(기존 채팅과 같음).
    keep_evidence(items)는 인용 근거를 결과 저장소에 두고 result_id를 돌려준다(D-102). 본문 인용은 digest에 넣지 않는다."""
    from backend.app.agents.digest import bounded
    from backend.app.agents.state import ToolResult
    conditions=task.conditions
    request=RagRequest(request_id=f'{state.input.job_id}:{step.step_id}',query=task.question,use_case='chat',
        conditions={'geography':{'mode':'selected' if conditions.country_codes else 'all','codes':conditions.country_codes},
                    'products':conditions.hs_items,'period':conditions.article_period,'blocked_topics':()},limit=5)
    try:
        response=search_news(request,article_allowed=article_allowed)
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
    result_id=None
    if keep_evidence:
        from backend.app.api.chat_news import evidence_items
        result_id=keep_evidence(evidence_items(response.articles))
    return ToolResult(status='OK',result_id=result_id,article_ids=[a['article_id'] for a in items],article_period=conditions.article_period,
        completeness='PARTIAL' if partial else 'COMPLETE',limitations=[limitation] if partial else [],
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
