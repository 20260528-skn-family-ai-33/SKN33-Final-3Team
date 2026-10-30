"""Build a saved PDF context from real data, without changing the legacy DOCX document.

Trade totals use the report's saved HS/country scope. One monthly/country aggregate
per item supplies all charts and comparisons; only verified months can become zero.
Unconnected editorial/AI fields remain null, never copied from template samples.
"""
from collections import defaultdict
from datetime import date, datetime
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from sqlalchemy import select, func

from backend.app.api import news, trade
from backend.app.api.common import clean, month_end, percentage, require, shift_month


KINDS = {'RPT_MONTHLY': '월간', 'RPT_WEEKLY': '주간', 'RPT_QUARTERLY': '분기', 'RPT_ON_DEMAND': '요청형'}
SOURCES = {'KOTRA_MARKET': 'KOTRA 해외시장뉴스', 'KOTRA_SHORT_NEWS': 'KOTRA 단신속보',
           'HANKYUNG': '한국경제', 'FNNEWS': '파이낸셜뉴스'}


def months(start, end):
    cursor = start
    while cursor <= end:
        yield cursor
        cursor = shift_month(cursor, 1)


def quarter_label(value):
    return f'{value.year}년 {(value.month - 1) // 3 + 1}분기'


def safe_url(value):
    try:
        parsed = urlsplit(value or '')
        return value if parsed.scheme in ('http', 'https') and parsed.netloc else None
    except ValueError:
        return None


def build_context(store, uid, record, document):
    kind = record['report_type']
    require(kind in KINDS, '리포트 형식을 확인하세요.')
    saved = record['conditions_json']
    start, end = (date.fromisoformat(saved['period'][k]) for k in ('start', 'end'))
    names = {r['country_code']: r['name_ko'] for r in store.rows(select(store.t('country')))}
    countries = [{'country_code': c, 'name': names.get(c, c)} for c in saved.get('country_codes', [])]
    hs = saved.get('hs_items', [])
    table = store.t('hs_code')
    labels = {(r['hs_code'], r['hs_ver']): r['name_ko'] for r in store.rows(
        select(table).where(table.c.hs_code.in_([h['hs_code'] for h in hs])))} if hs else {}
    items = []
    for item in hs:
        name = labels.get((item['hs_code'], item['hs_ver']), item['hs_code'])
        short = name if len(name) <= 24 else name[:23] + '…'
        items.append({**item, 'name': name, 'label': short, 'display': f"{short} (HS {item['hs_code']})"})
    if not items:
        items = [{'hs_code': '전체', 'hs_ver': 'HS2022', 'name': '전체 품목',
                  'label': '전체 품목', 'display': '전체 품목'}]

    label = KINDS[kind]
    period_label = (f'{end.year}년 {end.month}월' if kind == 'RPT_MONTHLY' else
                    quarter_label(end) if kind == 'RPT_QUARTERLY' else f'{start} ~ {end}')
    title = '관심 범위 주간 뉴스' if kind == 'RPT_WEEKLY' else f'관심 범위 {label} 수출 동향'
    scope = '선택 국가 내 집계' if countries else '전체 국가 집계'
    context = {
        'report': {'report_id': str(record['report_id']), 'report_type': kind,
                   'kind_label': f'정기 리포트 · {label}', 'title': title, 'period_label': period_label,
                   'cover_eyebrow': f'정기 리포트 · {label}',
                   'cover_lead': f"{len(hs)}개 관심 품목" if hs else '전체 품목',
                   'footer_label': f'{title} · {period_label}', 'is_sample': False,
                   'published_at': datetime.fromisoformat(document['generated_at'].replace('Z', '+00:00'))
                       .astimezone(ZoneInfo('Asia/Seoul')).isoformat()},
        'subscription': {'label': f'{label} 구독 · {scope}'},
        'conditions': {'hs_items': items, 'countries': countries},
        'meta': {'data_basis': f'생성 시점 저장 데이터 · {scope}',
                 'publish_note': '공개 검증 완료 기간 기준',
                 'publication_policy': '공개 검증 완료 기간의 구독 리포트 생성 · 실패 시 워커의 재시도 설정 적용',
                 'latest_available_month': document['meta'].get('latest_available_month')},
        'verification': None, 'article_limit': None,
        'article_policy_note': f"조건에 맞는 최신 기사 최대 {20 if kind == 'RPT_WEEKLY' else 5}건 · 본문 발췌 (AI 요약 미연결)",
        'articles': [], 'sources': [],
    }
    context['report']['cover_lead'] += ' · ' + scope
    selected_articles = [a for block in document['sections'] for a in block.get('articles', [])]
    for i, a in enumerate(selected_articles, 1):
        context['articles'].append({
            'no': i, 'title': a['title'], 'summary': a['summary'],
            'published_date': a['published_date'], 'source_label': SOURCES.get(a['source_code'], a['source_code']),
            'source_url': safe_url(a.get('source_url')), 'office': None,
            'country': ', '.join(names.get(c, c) for c in a.get('country_codes', [])) or '국가 미분류',
            'item_label': ', '.join(h['hs_code'] for h in a.get('hs_items', [])) or '품목 미분류'})

    if kind == 'RPT_ON_DEMAND':
        from backend.app.services.report_ondemand_context import populate
        context['report']['kind_label'] = '요청형 리포트'
        if not hs:
            context['conditions']['hs_items'] = []
        else:
            for item in context['conditions']['hs_items']:
                item['display'] = f"{item['name']} · HS {item['hs_code']}"
        populate(store, saved, context, names, start, end)
    elif kind == 'RPT_WEEKLY':
        _weekly(store, uid, saved, context, names, start, end)
    else:
        _trade(store, saved, context, names, start, end, kind)
    return clean(context)


def _trade(store, saved, ctx, names, start, end, kind):
    end_month = end.replace(day=1)
    span = 1 if kind == 'RPT_MONTHLY' else 3
    require(start == shift_month(end_month, 1 - span) and end == month_end(end_month)
            and (span == 1 or end.month in (3, 6, 9, 12)),
            '월간은 한 달, 분기는 완결된 달력 분기여야 합니다.', 'INVALID_REPORT_PERIOD', 422)
    trade.resolve_period(store, start.strftime('%Y-%m'), end.strftime('%Y-%m'))
    available = set(trade.bounds(store))
    first = shift_month(end_month, -24 if span == 1 else -23)
    requested_countries = saved.get('country_codes', [])
    ctx.update(summary={'highlight': None, 'sentences': [], 'check_points': [
        {'target': '집계 범위', 'content': '선택한 국가·품목 범위의 수출 집계입니다. 국가 비중의 분모도 같은 범위입니다.'},
        {'target': '미제공 항목', 'content': '미검증 과거 기간은 —로 표시합니다. AI 원인 분석·요약 및 문장 검증은 미연결입니다.'}]},
        items=[], interest_overview=[], one_off_note=None)
    scope = ', '.join(c['name'] for c in ctx['conditions']['countries']) or '전체 국가'
    ctx['sources'] = [{'label': '수출 통계', 'provider': '관세청',
                       'scope': f'{scope} · HS2022 · 검증 완료 월만 집계'},
                      {'label': '기사', 'provider': ', '.join(dict.fromkeys(a['source_label'] for a in ctx['articles'])) or '관련 기사 없음',
                       'scope': ctx['article_policy_note']}]
    if span == 1:
        ctx['conditions'].update(target_month=end.strftime('%Y-%m'),
            prev_month=shift_month(end_month, -1).strftime('%Y-%m'),
            prev_year_month=shift_month(end_month, -12).strftime('%Y-%m'))
    else:
        ctx['conditions'].update(year=end.year, prev_year=end.year - 1,
            quarter_label=f'{end.month // 3}분기', months_label=f'{start.month}~{end.month}월',
            prev_year_quarter_label=quarter_label(shift_month(end_month, -12)),
            prev_quarter_label=quarter_label(shift_month(end_month, -3)))

    overview = {c['country_code']: {'name': c['name'], 'values': []} for c in ctx['conditions']['countries']}
    for item in ctx['conditions']['hs_items']:
        hs = [{'hs_code': item['hs_code'], 'hs_ver': item['hs_ver']}] if item['hs_code'] != '전체' else []
        rows = trade.aggregate(store, 'export_summary', first, end_month, hs, requested_countries, 'MONTH_COUNTRY')
        values = {(r['trade_month'], r['country_code']): r['amount_usd'] for r in rows if r['trade_month'] in available}
        codes = sorted({c for _, c in values} | set(requested_countries))

        def amount(at, count=1, country=None):
            window = list(months(shift_month(at, 1 - count), at))
            if any(m not in available for m in window):
                return None
            return sum(values.get((m, c), 0) for m in window for c in ([country] if country else codes))

        current = amount(end_month, span)
        previous = amount(shift_month(end_month, -12), span)
        prior = amount(shift_month(end_month, -span), span)
        active = [c for c in codes if amount(end_month, span, c) > 0]
        active.sort(key=lambda c: (-amount(end_month, span, c), c))
        old_count = sum(amount(shift_month(end_month, -12), span, c) > 0 for c in codes) if previous is not None else None

        def country_metrics(country_codes):
            def total(at):
                parts = [amount(at, span, code) for code in country_codes]
                return None if any(x is None for x in parts) else sum(parts)
            value, last, year = total(end_month), total(shift_month(end_month, -span)), total(shift_month(end_month, -12))
            return {'amount_usd': value, 'share_pct': value * 100 / current if current else None,
                    'mom_pct': percentage(value, last), 'yoy_pct': percentage(value, year),
                    'is_new_mom': last == 0 and value > 0, 'is_new_yoy': year == 0 and value > 0}

        top = [{'rank': i, 'name': names.get(c, c), 'is_interest': c in requested_countries,
                **country_metrics([c])} for i, c in enumerate(active[:10], 1)]
        other_codes = [c for c in codes if c not in active[:10]]
        result = {**item, 'amount_usd': current, 'prev_amount_usd': previous,
                  'mom_pct': percentage(current, prior), 'qoq_pct': percentage(current, prior),
                  'yoy_pct': percentage(current, previous), 'country_count': len(active),
                  'country_count_diff': len(active) - old_count if old_count is not None else None,
                  'top_country': {'name': top[0]['name'] if top else '수출 실적 없음',
                                  'share_pct': top[0]['share_pct'] if top else None},
                  'top_countries': top, 'others': country_metrics(other_codes) if other_codes else None}
        if span == 1:
            window = list(months(shift_month(end_month, -12), end_month))
            result['trend'] = {'range_label': f'{window[0]:%Y.%m} ~ {end_month:%Y.%m}',
                               'labels': [f'{m:%y.%m}' for m in window],
                               'current': [amount(m) for m in window],
                               'previous': [amount(shift_month(m, -12)) for m in window]}
            result['country_trend'] = [{**c, 'values': [amount(m, country=c['country_code']) for m in window]}
                                       for c in ctx['conditions']['countries']]
        else:
            quarters = [shift_month(end_month, -3 * n) for n in reversed(range(8))]
            window = list(months(start, end_month))
            result['quarters'] = {'labels': [f'{m:%y}.{m.month // 3}Q' for m in quarters],
                                  'values': [amount(m, 3) for m in quarters],
                                  'previous_index': 3,
                                  'current_from': next(i for i, m in enumerate(quarters) if m.year == end.year)}
            result['months'] = [{'label': f'{m.month}월', 'amount_usd': amount(m),
                                 'prev_amount_usd': amount(shift_month(m, -12)),
                                 'yoy_pct': percentage(amount(m), amount(shift_month(m, -12)))} for m in window]
            result['country_quarters'] = [{**c, 'values': [amount(m, 3, c['country_code']) for m in quarters]}
                                          for c in ctx['conditions']['countries']]
            result['country_months'] = {'labels': [f'{m.month}월' for m in window], 'series': [
                {**c, 'values': [amount(m, country=c['country_code']) for m in window]} for c in ctx['conditions']['countries']]}
            result['top5_compare'] = [{'name': names.get(c, c), 'current': amount(end_month, 3, c),
                                      'previous': amount(shift_month(end_month, -12), 3, c)} for c in active[:5]]
        for code, row in overview.items():
            value = amount(end_month, span, code)
            row['values'].append({'amount_usd': value, 'yoy_pct': percentage(value, amount(shift_month(end_month, -12), span, code))})
        ctx['items'].append(result)
    ctx['interest_overview'] = list(overview.values())


def _weekly(store, uid, saved, ctx, names, start, end):
    # Count all matching articles, not just the bounded preview cards in the document.
    stmt = news.article_statement(store, uid, start=start, end=end,
                                  hs=saved.get('hs_items', []), countries=saved.get('country_codes', []))
    matched = stmt.order_by(None).subquery()
    counts = store.rows(select(matched.c.source_code, func.count().label('count')).group_by(matched.c.source_code))
    total = sum(r['count'] for r in counts)
    short = sum(r['count'] for r in counts if r['source_code'] == 'KOTRA_SHORT_NEWS')
    source_label = ', '.join(SOURCES.get(r['source_code'], r['source_code']) for r in counts) or '관련 기사 없음'
    ctx['meta']['data_basis'] = source_label + ' · 공개 기사 · 사용자 차단 키워드 적용'
    ctx['conditions'].update(week_label=f'{start} ~ {end}', compare_label='키워드 8주 비교 집계 미연결')
    ctx['summary'] = {'counts': {'total': total, 'market': total - short, 'short': short, 'spike_keywords': None},
                      'market_label': '일반 기사', 'total_note': f"전체 {total}건 · 본문 카드는 최신 최대 20건",
                      'spike_ratio': None, 'issue_spike_ratio': None, 'sentences': [], 'issues': [], 'schedule': [],
                      'issues_note': '주요 이슈 선정·AI 요약은 아직 연결되지 않았습니다.'}
    ctx['keyword_spikes'] = []
    ctx['keyword_note'] = '급증 키워드·직전 8주 비교 집계는 아직 연결되지 않았습니다.'
    ctx['news_source_label'] = source_label
    ctx['publish_note'] = '구독 스케줄러의 지난주 월~일 발행 조건 및 워커 재시도 설정 적용'
    tags = store.t('article_tag')
    country_stmt = select(tags.c.country_code, matched.c.source_code, func.count(func.distinct(matched.c.article_id)).label('count'))\
        .select_from(matched.join(tags, tags.c.article_id == matched.c.article_id)).where(tags.c.country_code.is_not(None))
    if saved.get('country_codes'):
        country_stmt = country_stmt.where(tags.c.country_code.in_(saved['country_codes']))
    by_country = defaultdict(lambda: {'market': 0, 'short': 0})
    for row in store.rows(country_stmt.group_by(tags.c.country_code, matched.c.source_code)):
        by_country[row['country_code']]['short' if row['source_code'] == 'KOTRA_SHORT_NEWS' else 'market'] += row['count']
    ctx['country_counts'] = [{'country_code': c, 'name': names.get(c, c),
                              'is_interest': c in saved.get('country_codes', []), **values}
                             for c, values in sorted(by_country.items())]
    ctx['country_count_note'] = '국가 태그별 집계입니다. 다국가 기사는 각 국가에 포함되며, 국가 미분류 기사는 이 표에서 제외됩니다.'
    shorts = store.rows(stmt.where(store.t('article').c.source_code == 'KOTRA_SHORT_NEWS').limit(5))
    ctx['short_news'] = {'total': short, 'items': [
        {'published_date': a['published_date'], 'country': ', '.join(names.get(c, c) for c in a['country_codes']) or '국가 미분류',
         'content': a['title']} for a in news.article_views(store, shorts, uid)]}
