"""Convert a resolved chat question into cited article cards for the chat history."""
from datetime import date
import logging

from pydantic import HttpUrl, TypeAdapter, ValidationError

from backend.app.agents import tools
from backend.app.api import news
from backend.app.schemas.rag import RagRequest

_URL = TypeAdapter(HttpUrl)


def matching_url(current, indexed):
    try:
        url = _URL.validate_python(current)
        return str(url) if url == _URL.validate_python(indexed) else None
    except ValidationError:
        return None


def article_answer(store, uid, job_id, question, conditions, warnings, latest):
    countries = conditions['country_codes']
    start, end = (date.fromisoformat(conditions['period'][key]) for key in ('start', 'end'))
    request = RagRequest(request_id=f'{job_id}:news', query=question, use_case='chat', limit=5,
        order_by='published_date_desc' if latest else 'relevance',
        conditions={'period': {'start': start, 'end': end}, 'products': conditions['hs_items'],
                    'geography': {'mode': 'selected' if countries else 'all', 'codes': countries},
                    'blocked_topics': []})
    if latest:
        start = date.fromordinal(max(start.toordinal(), end.toordinal() - 13))
    period = {'start': start.isoformat(), 'end': end.isoformat()}
    base = {'intent': 'EXPLAIN', 'conditions': conditions, 'tables': [], 'charts': [],
            'order_by': request.order_by, 'article_period': period,
            'verification': {'status': 'NEEDS_REVIEW', 'sentence_checks': None, 'regeneration_count': 0},
            'actions': [], 'report_id': None}

    # Apply current visibility and user blocks before spending the candidate and
    # summary budgets. Country/product tags are not evidence of body relevance.
    table = store.t('article')
    stmt = news.article_statement(store, uid, start=start, end=end)
    eligible = {(r['article_id'], r['source_code'], r['external_id'], r['published_date'])
                for r in store.rows(stmt.with_only_columns(table.c.article_id, table.c.source_code,
                                                          table.c.external_id, table.c.published_date))}
    def article_allowed(article):
        return (article.article_id, article.source_code.value, article.external_id,
                article.published_date) in eligible

    try:
        response = tools.search_news(request, article_allowed=article_allowed)
    except Exception as exc:
        logging.getLogger(__name__).warning('article_search_unavailable type=%s', type(exc).__name__)
        response = None
    if response is None or response.status == 'failed':
        message = '기사 검색에 실패했습니다. 잠시 후 다시 시도해 주세요.'
        return {**base, 'answer_status': 'NO_DATA', 'text': message, 'articles': [],
                'rag_status': 'failed', 'warnings': [*warnings, message]}

    cards = []
    evidence_complete = True
    if response.articles:
        rows = {r['article_id']: r for r in store.rows(stmt.where(
            table.c.article_id.in_([a.article_id for a in response.articles])))}
        for article in response.articles:
            row = rows.get(article.article_id)
            if not row or (row['source_code'], row['external_id'], row['published_date']) != (
                    article.source_code.value, article.external_id, article.published_date):
                continue
            # v26 returns original body quotes, independent of mutable index headers.
            body = row['body_masked'] or ''
            evidence = {e.evidence_id: e.quote if e.quote in body else None for e in article.evidence}
            sentences = []
            for s in article.summary:
                quotes = [evidence.get(i) for i in s.evidence_ids]
                if quotes and all(quotes):
                    sentences.append({'text': s.text, 'quotes': quotes,
                                      'unverified_question_parts': list(s.unverified_question_parts)})
            evidence_complete &= len(sentences) == len(article.summary)
            cards.append({'article_id': str(article.article_id), 'source_code': row['source_code'],
                          'title': row['title'], 'published_date': row['published_date'],
                          'source_url': matching_url(row['source_url'], article.source_url),
                          'summary': ' '.join(s['text'] for s in sentences), 'sentences': sentences,
                          'evidence_sufficient': bool(sentences), 'evidence_role': article.evidence_role,
                          'reference_limitation': article.reference_limitation})
    lines = [f"[{i}] {s['text']}" for i, card in enumerate(cards, 1) for s in card['sentences']]
    list_only = response.answer_format == 'list'
    if lines:
        text = '\n'.join(lines)
    elif list_only and cards:
        text = f'조건에 맞는 기사 {len(cards)}건입니다.'
    elif cards:
        text = '관련 기사는 있지만 답변을 뒷받침하는 본문 근거가 부족합니다.'
    else:
        text = '선택한 조건에서 답변에 사용할 기사를 찾지 못했습니다.'
    if latest and response.latest_scope_description:
        text = response.latest_scope_description + '\n' + text
    complete = response.status == 'ok' and len(cards) == len(response.articles) and (
        list_only or (evidence_complete and all(c['evidence_sufficient'] for c in cards)))
    status = ('ANSWERED' if complete else 'PARTIAL') if cards else 'NO_DATA'
    if list_only and any(c['evidence_role'] == 'unclassified' for c in cards):
        warnings = [*warnings, '본문 관련성을 검수하지 않은 목록이며 답변 근거로 확정하지 않았습니다.']
    if any(f.stage == 'timeout' for f in response.failures):
        warnings = [*warnings, '기사 검색 시간이 끝나 확인을 마친 결과만 표시합니다.']
    elif cards and not complete:
        warnings = [*warnings, '일부 기사 또는 본문 근거만 확인됐습니다.']
    return {**base, 'answer_status': status, 'text': text, 'articles': cards,
            'rag_status': response.status, 'answer_format': response.answer_format,
            'latest_scope': response.latest_scope,
            'latest_scope_description': response.latest_scope_description,
            'unverified_question_parts': list(response.unverified_question_parts), 'warnings': warnings}
