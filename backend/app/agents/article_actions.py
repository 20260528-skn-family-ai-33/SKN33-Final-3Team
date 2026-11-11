"""Run the article tab's explicit actions without inferring intent from a title."""
from time import monotonic

from openai import OpenAI, OpenAIError

from backend.app.agents import tools
from backend.app.agents.state import Conditions, Plan, Step, Task, TaskSlots, ToolResult
from backend.app.api import news
from backend.app.api.chat_news import eligible_filter
from backend.app.api.common import mask_text
from backend.app.schemas.rag import ArticleEvidence, RagRequest
from backend.app.scheduler.jobs.news_embed import chunk_text
from backend.app.services.rag.documents import ArticleDocument
from backend.app.services.rag.gateway import ModelGateway
from backend.app.services.rag.retrieval import Candidate
from backend.app.services.rag.runtime import ExecutionContext
from backend.app.services.rag.service import grounded_sentences


def action_plan(action):
    tasks = []
    for mode, question in [('summary', '선택한 기사 내용을 요약해 줘.'), ('related', '유사한 다른 기사를 찾아줘.')]:
        if action.mode not in (mode, 'both'):
            continue
        task_id = f't{len(tasks) + 1}'
        check_id = f'{task_id}.check'
        tasks.append(Task(task_id=task_id, intent='LOCAL_ISSUES', question=question,
                          slots=TaskSlots(answer_format='TEXT' if mode == 'summary' else None),
                          steps=[Step(step_id=check_id, tool='check_conditions'),
                                 Step(step_id=f'{task_id}.news', tool='search_articles',
                                      args={'article_mode': mode}, depends_on=[check_id])]))
    return Plan(tasks=tasks)


def selected_row(store, uid, article_id):
    article = store.t('article')
    return store.one(news.article_statement(store, uid).where(article.c.article_id == int(article_id)))


def summarize(row, request, settings, timeout):
    body = mask_text(row['body_masked'] or '')
    if not body.strip():
        return None
    document = ArticleDocument(article_id=row['article_id'], source_code=row['source_code'],
        external_id=row['external_id'], title=row['title'], published_date=row['published_date'],
        source_url=row['source_url'] or '', body=body)
    chunks = {f'{document.article_id}:{i}': text for i, text in enumerate(chunk_text(body, overlap=0))}
    candidate = Candidate(document, 0, document.version, chunks)
    context = ExecutionContext(monotonic() + timeout)
    with OpenAI(api_key=settings.get('OPENAI_API_KEY'), timeout=context.remaining(), max_retries=0) as api:
        payload = ModelGateway(api).summarize(request, candidate, context)
    evidence, summary = grounded_sentences(payload, candidate, context)
    if not summary:
        return None
    return ArticleEvidence(article_id=document.article_id, source_code=document.source_code,
        external_id=document.external_id, title=document.title, published_date=document.published_date,
        source_url=document.source_url, countries=(), body_status='available', evidence_role='direct_evidence',
        evidence=evidence, summary=summary)


def run_action(services, state, task, step, keep, timeout):
    mode = step.args['article_mode']
    action = state.input.article_action
    started = monotonic()
    with services.read_store() as store:
        row = selected_row(store, state.input.user_id, action.article_id)
        if row is None:
            return ToolResult(status='NO_DATA', error='선택한 기사를 볼 수 없습니다.')
        if mode == 'related':
            period = task.conditions.article_period
            _, allowed = eligible_filter(store, state.input.user_id, period.start, period.end)
    if mode == 'related':
        excerpt = mask_text(row['body_masked'] or '')[:500].strip()
        query = f'「{row["title"]}」와 같은 사건이나 주제를 다룬 기사\n{excerpt}'
        # 선택 기사 동작은 채팅 "다시 검색" 이어받기(article_retry)에 저장하지 않는다. 기사 화면에서 다시 요청하게 안내한다.
        return tools.article_result(state, task, step, started=started, query=query,
            article_allowed=lambda article: article.article_id != row['article_id'] and allowed(article),
            keep_evidence=keep, retry_hint='잠시 후 기사 화면에서 유사 기사 검색을 다시 요청해 주세요.')

    published = row['published_date']
    task.conditions = Conditions(article_period={'start': published, 'end': published})
    request = RagRequest(request_id=f'{state.input.job_id}:{step.step_id}',
        query=f'「{row["title"]}」 기사의 본문에 나온 핵심 내용을 요약해 주세요.', use_case='chat',
        conditions={'period': task.conditions.article_period, 'geography': {'mode': 'all'},
                    'products': (), 'blocked_topics': ()})
    remaining = timeout - (monotonic() - started)
    if remaining <= 0:
        return ToolResult(status='FAILED', error='기사 요약 시간이 초과되었습니다.')
    try:
        article = summarize(row, request, services.settings, remaining)
    except OpenAIError:
        return ToolResult(status='FAILED', error='기사 요약에 실패했습니다. 잠시 후 다시 시도해 주세요.')
    if article is None:
        return ToolResult(status='NO_DATA', error='선택한 기사의 요약 근거를 확인하지 못했습니다.')
    return tools.article_evidence_result(task.conditions, [article], 'ok', keep, '기사 요약 근거가 부족합니다.')
