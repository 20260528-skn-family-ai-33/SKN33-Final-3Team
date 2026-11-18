"""Monthly facts come from the same unrounded observations as the result table."""
import re

from backend.app.agents.verify_articles import ARTICLE_CITATION
from backend.app.agents.digest import korean_usd, number
from backend.app.agents.state import MAX_EXPLANATION_SENTENCES, MAX_TEXT_EXPLANATION_SENTENCES, is_text_task
from backend.app.api.common import percentage

_TREND = re.compile(r'추이|추세|월별|최저|최고|최소|최대|전년|작년|지난해|동월|연도별|비교')
# Signed/quantified statements anchored to the monthly table belong to its numerical summary.
_EXTREME = re.compile(r'최저|최고|최소|최대|가장\s*(?:낮|높|크|작|많|적)|사이|범위|[~∼]|달러에서')
_MONTH_DIRECTION = re.compile(r'(?:\d+월|매월|매달|모든 달|전년 동월).{0,30}(?:증가|감소|상승|하락|늘|줄)'
                              r'|(?:증가|감소|상승|하락)한 (?:달|월)')


def monthly_facts(rows):
    observed = [r for r in rows if r.get('amount_usd') is not None]
    if not observed:
        return {}
    # Keep the first tied month and its multiplicity, even when the Writer digest trims its rows.
    low = min(observed, key=lambda r: r['amount_usd'])
    high = max(observed, key=lambda r: r['amount_usd'])
    facts = {'min_month': low['month'], 'min_usd': low['amount_usd'],
             'max_month': high['month'], 'max_usd': high['amount_usd'],
             'min_count': sum(r['amount_usd'] == low['amount_usd'] for r in observed),
             'max_count': sum(r['amount_usd'] == high['amount_usd'] for r in observed)}
    paired = [r for r in observed if r.get('compare_amount_usd') is not None
              and r.get('compare_month') == f"{int(r['month'][:4])-1:04d}{r['month'][4:]}"]
    if paired:
        increased = [r['month'] for r in paired if r['amount_usd'] > r['compare_amount_usd']]
        facts.update(yoy_months=len(paired), increased_count=len(increased), increased_months=increased)
    return facts


def _date(month):
    return f'{month[:4]}년 {int(month[5:])}월'


def _period_label(window):
    start, end = window.start, window.end
    if start.year == end.year and start.month == 1 and end.month == 12:
        return f'{start.year}년'
    return f'{start.year}년 {start.month}월~{end.year}년 {end.month}월'


def monthly_sentences(task, target, facts):
    """Render one verified series. An incomplete comparison keeps its existing missing-data description."""
    cond = task.conditions
    if not facts or not cond or not cond.period or target.get('total_usd') is None:
        return ()
    flow = '무역수지' if task.slots.trade_balance else '수입액' if cond.trade_flow == 'IMPORT' else '수출액'
    topic = '는' if flow == '무역수지' else '은'
    subject = ' '.join(filter(None, ('한국의', '대' + target['country_name'] if target.get('country_code') else None,
                                    target.get('name') if target.get('hs_code') else None, flow)))
    total = korean_usd(target['total_usd'])
    period = _period_label(cond.period)
    if cond.compare_period:
        baseline = target.get('compare_total_usd')
        if baseline is None or facts.get('yoy_months') != target.get('requested_months'):
            return ()
        change = percentage(target['total_usd'], baseline)
        if change is None:
            change_text = '기준 금액이 0달러여서 증감률을 계산할 수 없습니다'
        elif change == 0:
            change_text = '두 기간 금액이 같습니다'
        else:
            change_text = f"전년보다 {number(abs(change), 1):g}% {'증가' if change > 0 else '감소'}했습니다"
        totals = f'{period} {subject}{topic} {total}, {_period_label(cond.compare_period)}은 {korean_usd(baseline)}이며 {change_text}.'
        count = facts['increased_count']
        items = facts.get('increased_months', [])
        if count == 0:
            more = '전년 동월보다 금액이 증가한 달은 없습니다.'
        else:
            one_year = cond.period.start.year == cond.period.end.year
            labels = ', '.join(f'{int(m[5:])}월' if one_year else _date(m) for m in items)
            more = (f'전년 동월보다 금액이 증가한 달은 {labels}입니다.'
                    if count == len(items) and len(labels) < 160 else f'전년 동월보다 금액이 증가한 달은 {count}개월입니다.')
        return (totals, more)
    min_label = '공동 최저' if facts['min_count'] > 1 else '최저'
    max_label = '공동 최고' if facts['max_count'] > 1 else '최고'
    if facts['min_usd'] == facts['max_usd']:
        extrema = f"모든 달의 {flow}{'가' if flow == '무역수지' else '이'} {number(facts['min_usd']):,}달러로 같습니다."
    else:
        extrema = (f"월별 {flow}의 {min_label}는 {_date(facts['min_month'])} {number(facts['min_usd']):,}달러, "
                   f"{max_label}는 {_date(facts['max_month'])} {number(facts['max_usd']):,}달러입니다.")
    return (f'{period} {subject}{topic} {total}입니다.', extrema)


def task_series(state, task):
    if (task.intent not in ('STATISTICS', 'CAUSE') or task.answer_status not in ('ANSWERED', 'PARTIAL')
            or not task.conditions or not task.conditions.period or getattr(task.slots, 'amount_difference_only', False)
            or len(task.conditions.country_codes) > 1 or len(task.conditions.hs_items) > 1):
        return ()
    results = [state.results[s.step_id] for s in task.steps if s.tool == 'query_trade_data' and s.step_id in state.results]
    if len(results) != 1 or results[0].status != 'OK':
        return ()
    digest = results[0].digest or {}
    targets = digest.get('targets') or []
    if digest.get('kind') != 'TREND' or len(targets) != 1:
        return ()
    target = targets[0]
    if target.get('requested_months', 0) < 2:
        return ()
    facts = target.get('monthly_facts')
    # Earlier result snapshots can still have a complete monthly series in their digest.
    if facts is None and len(target.get('monthly', ())) == target['requested_months']:
        facts = monthly_facts(target['monthly'])
    return monthly_sentences(task, target, facts)


def snapshot_sentences(task, source):
    """Rebuild the rendered facts independently from the saved table for numerical verification."""
    rows = source.get('rows') or []
    if source.get('kind') != 'TREND' or not rows or source.get('_verification_source') != 'SNAPSHOT':
        return ()
    cond = task.conditions
    if not cond or not cond.period or source.get('trade_flow') != cond.trade_flow:
        return ()
    start, end = cond.period.start, cond.period.end
    count = 12 * (end.year - start.year) + end.month - start.month + 1
    if count != len(rows):
        return ()
    month_keys = {r.get('month') for r in rows}
    subject_keys = {(r.get('country_code'), r.get('hs_code'), r.get('hs_ver')) for r in rows}
    expected_subject = (next(iter(cond.country_codes), None),
                        cond.hs_items[0].hs_code if cond.hs_items else None,
                        cond.hs_items[0].hs_ver if cond.hs_items else None)
    if (subject_keys != {expected_subject} or len(month_keys) != count or None in month_keys
            or min(month_keys) != start.strftime('%Y-%m') or max(month_keys) != end.strftime('%Y-%m')
            or any(r.get('amount_usd') is None for r in rows)):
        return ()
    previous = [r.get('compare_amount_usd') for r in rows]
    target = dict(rows[0], requested_months=count, total_usd=sum(r['amount_usd'] for r in rows),
                  compare_total_usd=sum(previous) if None not in previous else None)
    return monthly_sentences(task, target, monthly_facts(sorted(rows, key=lambda r: r['month'])))


_PLAIN_WORDS = re.compile(
    r'(?:\s+|[.,!?？·]|\d{4}\s*년(?:도)?|전년도?|작년|지난해|동월|대비|비교|연도별|기간|'
    r'수출액?|수입액?|추이|추세|월별|분석|우리나라|대한민국|한국|기준|각각|대상|'
    r'알려|보여|주세요|주십시오|줘|싶어|하고|해|한|의|을|를|은|는|이|가|과|와|에|으로|로|대)'
)


def fixed_monthly_sentences(state, task):
    # A request for another metric, a specific pair of months, narrative detail, etc.
    # must still go through the normal Writer. Only remove names actually bound to this query.
    if task.intent != 'STATISTICS' or not _TREND.search(task.question) or is_text_task(task):
        return ()
    sentences = task_series(state, task)
    if not sentences:
        return ()
    names = set(task.slots.products or ())
    for mention in task.slots.countries or ():
        names.update((mention.name, mention.mention))
    for step in task.steps:
        result = state.results.get(step.step_id)
        if step.tool == 'query_trade_data' and result and result.digest:
            target = result.digest['targets'][0]
            names.update(filter(None, (target.get('name'), target.get('country_name'), target.get('hs_code'))))
    question = task.question
    for name in sorted(names, key=len, reverse=True):
        if name:
            question = question.replace(name, ' ')
    return sentences if not _PLAIN_WORDS.sub('', question) else ()


def _uncited_monthly_claim(line):
    # Verification binds each cited clause to its article and checks any following SQL claim.
    if ARTICLE_CITATION.search(line):
        return False
    amount = re.search(r'달러|USD|월별', line) or ('월' in line and re.search(r'수출|수입', line))
    trade = re.search(r'수출|수입|금액|달러|전년 동월', line)
    months_only = re.match(r'^\s*(?:\d{1,2}월(?:에만|에는|만|은|이|에)?[,·와과\s]*)+(?:증가|감소|상승|하락)', line)
    return bool((_EXTREME.search(line) and amount) or (_MONTH_DIRECTION.search(line) and (trade or months_only)))


def reconcile_monthly_claims(state, draft):
    """Retain article findings, replacing Writer restatements of monthly statistics across shared sections."""
    series = {task.task_id: task_series(state, task) for task in state.plan.tasks}
    shared = set(filter(None, series.values()))
    if not shared:
        return
    for task, conclusion in zip(state.plan.tasks, draft.conclusion, strict=True):
        sentences = series[task.task_id]
        if not sentences or task.intent != 'CAUSE':
            continue
        conclusion.sentences = list(dict.fromkeys(
            sentences[1] if _uncited_monthly_claim(line) else line
            for line in conclusion.sentences))
    # Shared text has no task identity. For a single scoped series, its monthly answer is already in the conclusion.
    primary = state.plan.tasks[0]
    fixed = fixed_monthly_sentences(state, primary)
    if fixed and not ARTICLE_CITATION.search(draft.turn_summary):
        draft.turn_summary = fixed[1]
    plain = all(t.intent == 'CAUSE' or fixed_monthly_sentences(state, t) for t in state.plan.tasks)
    if len(shared) == 1 and all(series.values()) and plain:
        sentences, = shared
        draft.explanation = [line for line in draft.explanation if not _uncited_monthly_claim(line)]
        if not fixed and _uncited_monthly_claim(draft.turn_summary):
            draft.turn_summary = sentences[1]
        if (len(state.plan.tasks) == 1 and primary.intent == 'CAUSE' and _TREND.search(primary.question)
                and sentences[1] not in draft.conclusion[0].sentences):
            limit = MAX_TEXT_EXPLANATION_SENTENCES if is_text_task(primary) else MAX_EXPLANATION_SENTENCES
            draft.explanation = [sentences[1], *draft.explanation][:limit]
