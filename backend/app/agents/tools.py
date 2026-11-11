"""에이전트 tool. 설계: docs/AGENT_DESIGN.md §5, §13.4, §14.4."""
import time

from openai import OpenAI

from backend.app.api.config import Settings
from backend.app.schemas.rag import RagRequest, RagResponse
from backend.app.services.rag.factory import create_service
from backend.app.services.rag.runtime import ExecutionContext, RagDeadlineExceeded

CHAT_SEARCH_SECONDS = 30
CHAT_FINISH_RESERVE_SECONDS = 1


def search_news(request: RagRequest, *, article_allowed=None, started=None) -> RagResponse:
    """챗봇의 기사 검색 요청을 공통 RAG 서비스에 전달한다.
    started(time.monotonic): 검색 예산을 이 시각부터 센다. 앞에서 공개·차단 필터를 조회했으면 그 시간도 예산에 넣는다."""
    request = RagRequest.model_validate(request)
    regular_chat = request.use_case == 'chat' and request.order_by == 'relevance'
    reserve = CHAT_FINISH_RESERVE_SECONDS if regular_chat else 0
    deadline = (time.monotonic() if started is None else started) + (CHAT_SEARCH_SECONDS if regular_chat else 15)
    if deadline - time.monotonic() <= reserve:
        raise RagDeadlineExceeded('global_deadline_exceeded')   # 필터 조회만으로 예산을 다 썼다
    context = ExecutionContext(deadline, finish_reserve=reserve)
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
            try:
                snapshot.collection(ARTICLE_COLLECTION).query(
                    query_embeddings=[[0.0] * (snapshot.dimensions - 1) + [1.0]], n_results=1)
            finally:
                # 빌린 수만 돌려준다. 핸들은 열린 채 남는다(D-105). 시험 검색이 실패해도 돌려줘야 새 세대가 열린 뒤 닫힌다
                snapshot.client.close()
        except Exception as exc:
            logging.getLogger(__name__).warning('rag_index_warm_failed type=%s', type(exc).__name__)
    thread = threading.Thread(target=run, name='rag-index-warm', daemon=True)
    thread.start()
    return thread


def article_query(task, countries, products, *, question=None):
    conditions=task.conditions
    partners=', '.join(countries.get(code,code) for code in conditions.country_codes) or '전체 상대국'
    items=', '.join(products.get((h.hs_code,h.hs_ver),h.hs_code) for h in conditions.hs_items) or '전체 품목'
    trade_context=task.intent=='CAUSE' or any(s.args.get('trade_context') for s in task.steps)
    if trade_context:
        direction=(f'한국이 {partners}에 수출' if conditions.trade_flow=='EXPORT'
                   else f'{partners}에서 한국으로 수입')
        scope=f'{direction}하는 {items}'
    else:
        scope=f'기사의 분석 대상 품목: {items}'
        if conditions.country_codes:
            scope+=f'; 대상 국가: {partners}'
    if trade_context and conditions.period:
        scope+=f' ({conditions.period.start:%Y-%m}~{conditions.period.end:%Y-%m})'
    return f'{scope}\n{question or task.question}'


CHAT_RETRY_HINT = '잠시 후 다시검색을 요청해 주세요.'


def article_result(state, task, step, *, article_allowed=None, keep_evidence=None, started=None, query=None,
                   retry_hint=CHAT_RETRY_HINT):
    """그래프 기사 단계(§14.4). article_allowed는 공개·사용자 차단 필터(기존 채팅과 같음).
    keep_evidence(items)는 인용 근거를 결과 저장소에 두고 result_id를 돌려준다(D-102). 본문 인용은 digest에 넣지 않는다."""
    from backend.app.agents.state import ToolResult
    conditions=task.conditions
    request=RagRequest(request_id=f'{state.input.job_id}:{step.step_id}',query=query or task.question,use_case='chat',
        conditions={'geography':{'mode':'selected' if conditions.country_codes else 'all','codes':conditions.country_codes},
                    'products':conditions.hs_items,'period':conditions.article_period,'blocked_topics':()},limit=5)
    try:
        response=search_news(request,article_allowed=article_allowed,started=started)
    except RagDeadlineExceeded as exc:
        message=(f'기사 검색용 질의 처리 시간이 초과되었습니다. {retry_hint}'
                 if str(exc)=='query_embedding_timeout'
                 else f'기사 검색 또는 근거 검수 시간이 초과되었습니다. {retry_hint}')
        return ToolResult(status='NO_DATA',error=message)
    if response.request_id!=request.request_id or response.applied_conditions!=request.conditions:
        raise ValueError('기사 검색 결과의 요청 조건이 다릅니다.')
    timed_out=any(f.stage=='timeout' for f in response.failures)
    if response.failures:
        import logging
        logging.getLogger(__name__).warning('article_search_result job_id=%s status=%s articles=%s failures=%s',
            state.input.job_id,response.status,len(response.articles),
            [(failure.stage,failure.code) for failure in response.failures])
    if response.status=='failed':
        if timed_out:
            return ToolResult(status='NO_DATA',error=f'기사 검색 또는 근거 검수 시간이 초과되었습니다. {retry_hint}')
        return ToolResult(status='FAILED',error='기사 검색에 실패했습니다.')
    if not response.articles:
        message=(f'기사 검색 또는 근거 검수 시간이 초과되었습니다. {retry_hint}' if timed_out
                 else '질문을 뒷받침하는 기사 근거를 확인하지 못했습니다.' if response.status=='insufficient_evidence'
                 else '조건에 맞는 기사가 없습니다.')
        return ToolResult(status='NO_DATA',error=message)
    limitation=('기사 검색 시간이 끝나 확인을 마친 결과만 표시합니다.' if timed_out
                else '기사 근거 또는 검색 범위가 부족합니다.')
    return article_evidence_result(conditions, response.articles, response.status, keep_evidence, limitation)


def article_evidence_result(conditions, articles, status, keep_evidence, limitation):
    from backend.app.agents.digest import bounded
    from backend.app.agents.state import ToolResult
    items=[]
    for a in articles:
        if not conditions.article_period.start<=a.published_date<=conditions.article_period.end:
            raise ValueError('기사 날짜가 요청 범위 밖입니다.')
        items.append({'article_id':a.article_id,'title':a.title[:100],'published_date':a.published_date.isoformat(),
            'source_code':a.source_code.value,'evidence_sufficient':bool(a.summary and a.evidence),
            'summary':[s.text[:200] for s in a.summary]})
    partial=status!='ok' or any(not a['evidence_sufficient'] for a in items)
    result_id=None
    if keep_evidence:
        from backend.app.api.chat_news import evidence_items
        result_id=keep_evidence(evidence_items(articles))
    return ToolResult(status='OK',result_id=result_id,article_ids=[a['article_id'] for a in items],article_period=conditions.article_period,
        completeness='PARTIAL' if partial else 'COMPLETE',limitations=[limitation] if partial else [],
        digest=bounded({'kind':'ARTICLES','article_period':conditions.article_period.model_dump(mode='json'),
                        'rag_status':status,'items':items,'rows_total':len(items)}))


HS_LOOKUP_LIMIT = 10   # 표현 하나의 후보 수(HS4·HS6 각 5개)


def hs_lookup_result(store, task):
    """D-101: 품목 표현 → HS 코드, HS 코드 → 품목명. 조건을 확정하지 않고 HS2022 사전 검색 결과만 돌려준다."""
    from backend.app.agents.digest import bounded
    from backend.app.agents.planner import HS_CODE_MENTION
    from backend.app.agents.state import ToolResult
    from backend.app.api.reference import _search_depth
    expressions = (task.slots.products if task.slots else None) or []
    if not expressions:
        return ToolResult(status='NO_DATA', error='품목명이나 HS 코드를 함께 입력해 주세요.')
    items, missing = [], []
    for expr in expressions:
        if match := HS_CODE_MENTION.fullmatch(expr.strip()):
            expr = match[1]
        found = {}
        for depth in (len(expr),) if expr.isdigit() and len(expr) in (4, 6) else (4, 6):
            for row in _search_depth(store, expr, depth, 5)['items']:
                if expr.isdigit() and row['hs_code'] != expr:
                    continue
                found.setdefault(row['hs_code'], {'query': expr, 'code': row['hs_code'], 'name': row['name_ko'],
                                                  'level': f'HS{depth}'})
        if found: items.extend(list(found.values())[:HS_LOOKUP_LIMIT])
        else: missing.append(expr)
    if not items:
        return ToolResult(status='NO_DATA', error=f'“{missing[0][:40]}”에 해당하는 HS 코드를 찾지 못했습니다.')
    return ToolResult(status='OK', completeness='PARTIAL' if missing else 'COMPLETE',
        limitations=[f'“{m[:40]}”에 해당하는 HS 코드를 찾지 못했습니다.' for m in missing][:5],
        digest=bounded({'kind': 'HS_CODES', 'hs_ver': 'HS2022', 'items': items, 'rows_total': len(items)}))
