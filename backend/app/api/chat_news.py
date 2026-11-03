"""Convert a resolved chat question into cited article cards for the chat history."""
from datetime import date, timedelta

from backend.app.agents import tools
from backend.app.api import news
from backend.app.api.common import ApiError
from backend.app.schemas.rag import RagRequest
from backend.app.scheduler.jobs.news_embed import chunk_text, format_document


def article_answer(store, uid, job_id, question, conditions, warnings, latest):
    countries = conditions['country_codes']
    start, end = (date.fromisoformat(conditions['period'][key]) for key in ('start', 'end'))
    if latest:
        start = max(start, end - timedelta(days=13))
    request = RagRequest(request_id=f'{job_id}:news', query=question, use_case='chat', limit=5,
        order_by='published_date_desc' if latest else 'relevance',
        conditions={'period': {'start': start, 'end': end}, 'products': conditions['hs_items'],
                    'geography': {'mode': 'selected' if countries else 'all', 'codes': countries},
                    'blocked_topics': []})
    response = tools.search_news(request)
    if response.status == 'failed':
        raise ApiError(503, 'ARTICLE_SEARCH_FAILED', '기사 검색에 실패했습니다. 검색 인덱스와 모델 연결을 확인해 주세요.')

    cards = []
    if response.articles:
        table = store.t('article')
        stmt = news.article_statement(store, uid, start=start, end=end, countries=countries,
                                      hs=conditions['hs_items'])
        rows = {r['article_id']: r for r in store.rows(stmt.where(
            table.c.article_id.in_([a.article_id for a in response.articles])))}
        for article in response.articles:
            row = rows.get(article.article_id)
            if not row or (row['source_code'], row['external_id'], row['published_date']) != (
                    article.source_code.value, article.external_id, article.published_date):
                continue
            # Index chunks include a metadata header. Show only the matching body from the current DB row.
            body = row['body_masked'] or ''
            metadata = {**row, 'countries': article.countries}
            chunks = {format_document(metadata, chunk): chunk for chunk in chunk_text(body)}
            evidence = {e.evidence_id: chunks.get(e.quote, e.quote if e.quote in body else None)
                        for e in article.evidence}
            sentences = []
            for s in article.summary:
                quotes = [evidence[i] for i in s.evidence_ids]
                if all(quotes):
                    sentences.append({'text': s.text, 'quotes': quotes})
            cards.append({'article_id': str(article.article_id), 'source_code': row['source_code'],
                          'title': row['title'], 'published_date': row['published_date'],
                          'source_url': str(article.source_url) if row['source_url']==str(article.source_url) else None,
                          'summary': ' '.join(s['text'] for s in sentences), 'sentences': sentences,
                          'evidence_sufficient': bool(sentences)})
    lines = [f"[{i}] {s['text']}" for i, card in enumerate(cards, 1) for s in card['sentences']]
    if latest and cards:
        text = f'발행일 기준 최신 기사 {len(cards)}건입니다.'
    elif lines:
        text = '\n'.join(lines)
    elif cards:
        text = '관련 기사는 있지만 답변을 뒷받침하는 본문 근거가 부족합니다.'
    else:
        text = '선택한 조건에서 답변에 사용할 기사를 찾지 못했습니다.'
    complete = response.status == 'ok' and len(cards) == len(response.articles) and (
        latest or all(c['evidence_sufficient'] for c in cards))
    status = ('ANSWERED' if complete else 'PARTIAL') if cards else 'NO_DATA'
    if cards and not complete:
        warnings = [*warnings, '일부 기사 또는 본문 근거만 확인됐습니다.']
    return {'intent': 'EXPLAIN', 'answer_status': status, 'text': text,
            'conditions': conditions, 'tables': [], 'charts': [], 'articles': cards,
            'rag_status': response.status, 'order_by': request.order_by,
            'article_period': request.conditions.period.model_dump(mode='json'),
            'verification': {'status': 'NEEDS_REVIEW', 'sentence_checks': None, 'regeneration_count': 0},
            'actions': [], 'report_id': None, 'warnings': warnings}
