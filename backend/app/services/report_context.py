"""Build a saved PDF context from real data, without changing the legacy DOCX document.

Trade totals use the report's saved HS/country scope. One monthly/country aggregate
per item supplies all charts and comparisons; only verified months can become zero.
Unconnected editorial/AI fields remain null, never copied from template samples.
"""
from collections import defaultdict
from datetime import date, datetime
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_, select

from backend.app.api import news, trade
from backend.app.api.common import clean, month_end, percentage, require, shift_month
from backend.app.scheduler.jobs.major_issue_detect import (MARKET, MINIMUM_RATIO, SHORT_NEWS, keyword_counts, keyword_issues,
                                                           product_counts, product_issues, spike, trade_regulation_issues, windows)
from backend.app.services.article_sources import KEYWORD_DAILY_SOURCES, PRODUCT_DAILY_SOURCES, SHORT_NEWS_SOURCES


KINDS = {'RPT_MONTHLY': '월간', 'RPT_WEEKLY': '주간', 'RPT_QUARTERLY': '분기', 'RPT_ON_DEMAND': '요청형'}
SOURCES = {MARKET: 'KOTRA 해외시장뉴스', **dict.fromkeys(SHORT_NEWS_SOURCES, 'KOTRA 단신속보'),
           'HANKYUNG': '한국경제', 'FNNEWS': '파이낸셜뉴스'}
WEEKLY_ISSUE_LIMIT = 10  # 주간 리포트에 싣는 급증 키워드·주요 이슈 최대 건수


def canonical_source(code):
    """옛 단신 코드는 현행 코드로 본다. 출처 목록에서 같은 매체가 두 번 나오지 않게 한다."""
    return SHORT_NEWS if code in SHORT_NEWS_SOURCES else code


def source_entries(codes):
    """리포트 출처 목록(웹 상세·DOCX·PDF 공통). 같은 매체는 한 번만, 코드 순으로 싣는다."""
    unique = sorted({canonical_source(code) for code in codes})
    return [{'code': code, 'label': SOURCES.get(code, code), 'url': None} for code in unique]


def source_label(codes):
    return ', '.join(s['label'] for s in source_entries(codes)) or '관련 기사 없음'


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
        content = record.get('content_json') or {}
        if content.get('source_tables'):  # 승인한 채팅 결과로 만든 리포트: 결과표 사본만 쓰고 다시 조회하지 않는다(D-90)
            from backend.app.services.report_ondemand_context import populate_results
            populate_results(saved, context, start, end, content)
        else:
            populate(store, {**saved,'country_codes':[]} if saved.get('include_korea_total') else saved, context, names, start, end)
        if saved.get('include_korea_total'):
            context['conditions']['countries'].insert(0,{'country_code':'__KOREA_TOTAL__','name':'한국(대한민국) 전체'})
            table=next((t for section in document['sections'] for t in section.get('tables',[])
                        if t.get('meta',{}).get('kind')=='REPORT_SCOPE_COMPARISON'),None)
            if table:
                columns=[col for col in table['columns'] if col['key']!='month']
                context['scope_comparison']={'labels':[r['month'] for r in table['rows']],
                    'series':[{'name':col['label'],'values':[r[col['key']] for r in table['rows']]} for col in columns],
                    'rows':[{'month':r['month'],'name':col['label'],'amount_usd':r[col['key']]}
                            for r in table['rows'] for col in columns]}
        if note := document.get('meta',{}).get('monthly_average_note'):
            context['summary']['monthly_average_note']=note
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


def weekly_highlights(ctx):
    """PDF 양식의 급증 키워드·주요 이슈를 웹 화면과 DOCX에도 같은 값으로 보여 줄 문장(FR-109-07)."""
    summary = ctx['summary']
    if summary['spike_ratio'] is None:
        keywords = ctx['keyword_note']
    else:
        keywords = ', '.join(f"{r['keyword']}({r['country']}) ×{r['ratio']:.1f}" for r in ctx['keyword_spikes']) or '기준을 넘은 키워드가 없습니다.'
    if summary['issues']:
        issues = ''.join(f"\n· {r['title']} ({r['basis']})" for r in summary['issues'])
        if summary['spike_gap_note']:
            issues += f"\n· {summary['spike_gap_note']}"
    else:
        issues = ' ' + summary['issues_note']
    return f'급증 키워드: {keywords}\n주요 이슈:{issues}'


def _keyword_spikes(store, uid, saved, names, start, end):
    """구독 국가(없으면 전체)의 급증 키워드를 표시 순서대로 (집계 키 (키워드, 국가 코드 또는 None), 표시 행) 쌍으로 돌려줍니다.
    수집이 끝나지 않은 기간이 있으면 0건과 구분해 None을 돌려줍니다(FR-906-05)."""
    from backend.app.api.subscriptions import sources_ready
    recent, baseline = windows(end)
    if not sources_ready(store, (MARKET, SHORT_NEWS), baseline[0], recent[1]):
        return None
    countries = saved.get('country_codes') or ()
    blocked = set(news.blocked_topics(store, uid))
    # 차단한 기사에 함께 붙은 다른 키워드도 세지 않도록 두 기간 모두 회원 기준으로 센다.
    now_counts, old_counts = keyword_counts(store, *recent, countries, blocked), keyword_counts(store, *baseline, countries, blocked)
    # 동의어는 대표어를 차단해도 함께 빠진다.
    topics = store.t('keyword_topic')
    synonyms = {r['keyword_text'] for r in store.rows(select(topics.c.keyword_text).where(topics.c.canonical_text.in_(blocked)))} if blocked else set()
    result = []
    for (keyword, country), count in now_counts.items():
        average, ratio = spike(count, old_counts.get((keyword, country), 0))
        if count > 0 and ratio >= MINIMUM_RATIO and keyword not in blocked and keyword not in synonyms:
            result.append(((keyword, country), {'keyword': keyword, 'country': names.get(country, country) if country else '전체',
                                                'this_week': count, 'weekly_avg_8w': average, 'ratio': ratio}))
    return sorted(result, key=lambda pair: (-pair[1]['ratio'], pair[1]['keyword'], pair[1]['country']))


def _issue_candidates(store, start, end):
    """이번 주 주요 이슈 후보. 판정 작업이 아직 돌지 않았어도 빠지지 않게 마지막 날 기준을 직접 계산하고, 그 뒤에 저장된 판정을 붙입니다."""
    from backend.app.api.subscriptions import sources_ready
    baseline_start = windows(end)[1][0]
    keyword_ready = sources_ready(store, (MARKET, SHORT_NEWS), baseline_start, end)
    product_ready = sources_ready(store, (MARKET,), baseline_start, end)
    live = [*(keyword_issues(store, end) if keyword_ready else ()), *(product_issues(store, end) if product_ready else ()),
            *trade_regulation_issues(store, start, end)]
    e = store.t('alert_event')
    rows = [{'article_id': article_id, 'payload_json': payload} for _, article_id, payload in live] + store.rows(
        select(e).where(e.c.event_type == 'MAJOR_ISSUE', e.c.created_at >= datetime(start.year, start.month, start.day)).order_by(e.c.event_id.desc()))
    # 같은 이슈는 판정 기준일이 가장 늦은 건을 쓴다(뒤늦은 재처리가 저장 순서를 뒤집어도 같다). 같은 날은 직접 계산한 값이 앞선다.
    rows.sort(key=lambda row: row['payload_json'].get('metric_date') or '', reverse=True)
    return rows, keyword_ready, product_ready


def _day_label(day):
    return f'{day.month}월 {day.day}일 기준'


def _spike_gap_note(keyword_ready, product_ready, end):
    """판단하지 못한 급증 범위 안내. 미판정을 0건으로 보이지 않게 합니다(FR-906-05). 어느 날 기준인지 함께 밝힙니다."""
    if keyword_ready and product_ready:
        return None
    if product_ready:
        return f'{_day_label(end)} 단신 수집이 완료되지 않아 키워드 급증은 판단하지 않았습니다.'
    return f'{_day_label(end)} 시장뉴스 수집이 완료되지 않아 급증 이슈는 판단하지 않았습니다.'


def _spike_ready(store, target, metric, cache):
    """그 판정일 기준 최근 7일과 직전 8주의 필수 출처 수집이 모두 끝났는지. 키워드는 시장뉴스·단신, 품목은 시장뉴스가 필요합니다."""
    from backend.app.api.subscriptions import sources_ready
    sources = (MARKET, SHORT_NEWS) if target.get('keyword_text') else (MARKET,)
    if (sources, metric) not in cache:
        cache[sources, metric] = sources_ready(store, sources, windows(metric)[1][0], metric)
    return cache[sources, metric]


def _current_spike(store, payload, cache, blocked=()):
    """저장된 급증 판정을 그 기준일의 지금 공개 기사 수(회원이 차단한 기사 제외)로 다시 계산합니다. 기준 미달이면 None입니다."""
    metric = date.fromisoformat(payload['metric_date'])
    target = payload.get('target') or {}
    if target.get('keyword_text'):
        kind, counter, key = 'keyword', lambda s, e: keyword_counts(store, s, e, blocked=blocked), (target['keyword_text'], None)
    elif target.get('hs_code'):
        kind, counter, key = 'product', lambda s, e: product_counts(store, s, e, blocked), (target['hs_code'], target.get('hs_ver'))
    else:
        return None
    if (kind, metric) not in cache:
        recent, baseline = windows(metric)
        cache[kind, metric] = counter(*recent), counter(*baseline)
    recent_counts, baseline_counts = cache[kind, metric]
    count = recent_counts.get(key, 0)
    average, ratio = spike(count, baseline_counts.get(key, 0))
    if count <= 0 or ratio < MINIMUM_RATIO:
        return None
    return {**payload, 'recent_week_count': count, 'baseline_weekly': average, 'ratio': ratio}


def _issue_key(payload):
    """같은 이슈 판별 기준. 통상·규제는 기사, 급증은 키워드·품목으로 구분합니다(제목이 같아도 다른 기사는 합치지 않습니다).
    대표어(kw_topic)는 나중에 지정될 수 있어 키에 넣지 않습니다. 회원 차단 판정에는 계속 씁니다."""
    target = payload.get('target') or {}
    if payload.get('reason') == 'WEEKLY_NEWS_SPIKE':
        if target.get('keyword_text'):
            return ('SPIKE', 'KEYWORD', target['keyword_text'])
        return ('SPIKE', 'PRODUCT', target.get('hs_code'), target.get('hs_ver'))
    return ('REGULATION', tuple(payload.get('article_ids') or ()))


def _counted_sources(store, blocked, metric, targets):
    """급증 배수(최근 7일 ÷ 직전 8주)에 실제로 들어간 기사의 출처. 최근 7일만이 아니라 비교값을 정한 직전 8주 기사도 포함합니다.
    targets는 [(판정 대상, 국가 코드 또는 None)]이고 대상은 저장 판정의 target 형식({'keyword_text'} 또는 {'hs_code', 'hs_ver'})입니다.
    keyword_counts·product_counts와 같은 출처·국가·공개 상태·회원 차단 조건으로 찾습니다(옛 단신 코드는 source_entries가 합칩니다)."""
    if not targets:
        return set()
    a, t = store.t('article'), store.t('article_tag')
    matches = []
    for target, country in targets:
        if target.get('keyword_text'):
            match = and_(a.c.source_code.in_(KEYWORD_DAILY_SOURCES),
                         a.c.article_id.in_(select(t.c.article_id).where(t.c.keyword_text == target['keyword_text'])))
        else:  # 품목 집계는 HS6 태그 기사를 위 단계 HS4에도 세므로 앞자리로 찾는다(근거 기사 조회와 같은 기준)
            match = and_(a.c.source_code.in_(PRODUCT_DAILY_SOURCES), a.c.article_id.in_(select(t.c.article_id).where(
                t.c.hs_code.startswith(target['hs_code']), t.c.hs_ver == target.get('hs_ver'))))
        if country:
            match = and_(match, a.c.article_id.in_(select(t.c.article_id).where(t.c.country_code == country)))
        matches.append(match)
    stmt = select(a.c.source_code).distinct().where(
        a.c.visibility_status == 'VISIBLE', a.c.published_date >= windows(metric)[1][0], a.c.published_date <= metric, or_(*matches))
    if blocked:
        stmt = stmt.where(~news.blocked_article(store, blocked))
    return set(store.c.execute(stmt).scalars())


def _major_issues(store, uid, saved, names, start, end, used_sources=None):
    """이번 주 주요 이슈(FR-107-02)와 키워드·품목 판정 가능 여부. 같은 이슈는 기준일이 가장 늦고 지금도 기준을 넘는 1건만 씁니다."""
    from backend.app.api.notifications import event_allowed, event_ids, evidence_statement, has_evidence
    tags = store.t('article_tag')
    result, seen, cache, ready = [], set(), {}, {}
    blocked = set(news.blocked_topics(store, uid))
    candidates, keyword_ready, product_ready = _issue_candidates(store, start, end)
    for row in candidates:
        p = row['payload_json']
        metric = p.get('metric_date')
        if not metric or not start <= date.fromisoformat(metric) <= end:
            continue
        spiked = p.get('reason') == 'WEEKLY_NEWS_SPIKE'
        # 판정 뒤 그 판정 기간의 수집이 실패로 바뀌었으면 저장된 판정을 쓰지 않는다(FR-906-05).
        if spiked and not _spike_ready(store, p.get('target') or {}, date.fromisoformat(metric), ready):
            continue
        # 알림 전달·알림함과 같은 기준: 급증은 판정 범위 전체에서 이 회원이 열 수 있는 근거 기사를 찾는다.
        if not event_allowed(store, uid, row):
            continue
        key = _issue_key(p)
        if key in seen:
            continue
        if spiked:
            p = _current_spike(store, p, cache, blocked)  # 판정 뒤 기사를 숨겼거나 회원이 차단했으면 저장값을 믿지 않는다
            if p is None:
                continue
        seen.add(key)
        ids = event_ids(row)
        related = bool(has_evidence(store, uid, p, ids, saved.get('hs_items', []), saved.get('country_codes', [])))
        country = [] if spiked or not ids else store.c.execute(select(tags.c.country_code).where(
            tags.c.article_id.in_(ids), tags.c.country_code.is_not(None)).distinct()).scalars().all()
        result.append(({'kind': 'SPIKE' if spiked else 'REGULATION',
                        'title': f"‘{p.get('label')}’ 기사 급증" if spiked else p.get('label'),
                        'country': ', '.join(names.get(c, c) for c in country) or '전체',
                        'basis': (f"{_day_label(date.fromisoformat(metric))} · 7일 {p['recent_week_count']}건 · 주평균 {p['baseline_weekly']:.1f}건 · ×{p['ratio']:.1f}" if spiked else 'KOTRA 통상·규제 분류'),
                        'related': '관심 범위' if related else '—'}, p, ids))
    # 표시 건수 제한에 밀리지 않게 급증을 앞에 둔다. 출처는 제한 뒤 실제로 싣는 이슈에서만 모은다.
    result.sort(key=lambda r: r[0]['kind'] != 'SPIKE')
    shown = result[:WEEKLY_ISSUE_LIMIT]
    if used_sources is not None:
        a = store.t('article')
        for _, p, ids in shown:
            if p.get('reason') == 'WEEKLY_NEWS_SPIKE':  # 보여 주는 배수의 최근 7일·직전 8주에 들어간 기사(전체 국가 집계)
                used_sources.update(_counted_sources(store, blocked, date.fromisoformat(p['metric_date']), [(p.get('target') or {}, None)]))
            elif (evidence := evidence_statement(store, uid, p, ids)) is not None:  # 통상·규제는 근거 기사
                used_sources.update(store.c.execute(evidence.order_by(None).with_only_columns(a.c.source_code).distinct()).scalars())
    return [entry for entry, _, _ in shown], keyword_ready, product_ready


def _weekly(store, uid, saved, ctx, names, start, end):
    # Count all matching articles, not just the bounded preview cards in the document.
    stmt = news.article_statement(store, uid, start=start, end=end,
                                  hs=saved.get('hs_items', []), countries=saved.get('country_codes', []))
    matched = stmt.order_by(None).subquery()
    counts = store.rows(select(matched.c.source_code, func.count().label('count')).group_by(matched.c.source_code))
    total = sum(r['count'] for r in counts)
    short = sum(r['count'] for r in counts if r['source_code'] in SHORT_NEWS_SOURCES)
    ranked = _keyword_spikes(store, uid, saved, names, start, end)
    spikes = None if ranked is None else [row for _, row in ranked]
    shown_spikes = (ranked or [])[:WEEKLY_ISSUE_LIMIT]
    # 출처는 본문 카드가 아니라 기사 수 요약·표에 싣는 급증 키워드·주요 이슈에 실제로 쓴 기사 기준으로 모은다(웹·DOCX·PDF 공통).
    blocked = set(news.blocked_topics(store, uid))
    used = {r['source_code'] for r in counts} | _counted_sources(
        store, blocked, end, [({'keyword_text': keyword}, country) for (keyword, country), _ in shown_spikes])
    issues, keyword_ready, product_ready = _major_issues(store, uid, saved, names, start, end, used)
    ctx['source_codes'] = [s['code'] for s in source_entries(used)]
    news_sources = source_label(used)
    ctx['meta']['data_basis'] = news_sources + ' · 공개 기사 · 사용자 차단 키워드 적용'
    gap_note = _spike_gap_note(keyword_ready, product_ready, end)
    ctx['conditions'].update(week_label=f'{start} ~ {end}', compare_label='직전 8주 주평균' if spikes is not None else '키워드 8주 비교 집계 미완료')
    ctx['summary'] = {'counts': {'total': total, 'market': total - short, 'short': short, 'spike_keywords': None if spikes is None else len(spikes)},
                      'market_label': '일반 기사', 'total_note': f"전체 {total}건 · 본문 카드는 최신 최대 20건",
                      'spike_ratio': None if spikes is None else MINIMUM_RATIO,
                      # 어떤 급증도 판단하지 못했으면 기준 문구를 빼서 통상·규제만 판단했음을 드러낸다.
                      'issue_spike_ratio': MINIMUM_RATIO if keyword_ready or product_ready else None, 'sentences': [],
                      'issues': issues, 'schedule': [], 'spike_gap_note': gap_note,
                      'issues_note': f'{gap_note} 확인한 범위에는 주요 이슈가 없습니다.' if gap_note else '이번 주 기준에 해당하는 주요 이슈가 없습니다.'}
    ctx['keyword_spikes'] = [row for _, row in shown_spikes]  # 요약 카드의 총수는 제한 전 건수를 쓴다
    ctx['keyword_note'] = ('기사 수집 완료 기록이 없는 기간이 있어 직전 8주와 비교하지 않았습니다.' if spikes is None
                           else f'최근 7일 ÷ 직전 8주 주평균 {MINIMUM_RATIO}배 이상 · 한 기사는 키워드마다 1건 · 8주 주평균이 0이면 1로 계산')
    ctx['news_source_label'] = news_sources
    ctx['publish_note'] = '구독 스케줄러의 지난주 월~일 발행 조건 및 워커 재시도 설정 적용'
    tags = store.t('article_tag')
    country_stmt = select(tags.c.country_code, matched.c.source_code, func.count(func.distinct(matched.c.article_id)).label('count'))\
        .select_from(matched.join(tags, tags.c.article_id == matched.c.article_id)).where(tags.c.country_code.is_not(None))
    if saved.get('country_codes'):
        country_stmt = country_stmt.where(tags.c.country_code.in_(saved['country_codes']))
    by_country = defaultdict(lambda: {'market': 0, 'short': 0})
    for row in store.rows(country_stmt.group_by(tags.c.country_code, matched.c.source_code)):
        by_country[row['country_code']]['short' if row['source_code'] in SHORT_NEWS_SOURCES else 'market'] += row['count']
    ctx['country_counts'] = [{'country_code': c, 'name': names.get(c, c),
                              'is_interest': c in saved.get('country_codes', []), **values}
                             for c, values in sorted(by_country.items())]
    ctx['country_count_note'] = '국가 태그별 집계입니다. 다국가 기사는 각 국가에 포함되며, 국가 미분류 기사는 이 표에서 제외됩니다.'
    shorts = store.rows(stmt.where(store.t('article').c.source_code.in_(SHORT_NEWS_SOURCES)).limit(5))
    ctx['short_news'] = {'total': short, 'items': [
        {'published_date': a['published_date'], 'country': ', '.join(names.get(c, c) for c in a['country_codes']) or '국가 미분류',
         'content': a['title']} for a in news.article_views(store, shorts, uid)]}
