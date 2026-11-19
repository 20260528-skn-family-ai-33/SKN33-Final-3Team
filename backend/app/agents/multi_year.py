"""Calendar-year comparisons keep each requested year and its baseline role separate."""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import re
from time import monotonic

from backend.app.agents.digest import bounded, korean_usd
from backend.app.agents.state import (ClaimFeedback, Draft, PlannerOutput, SentenceCheck, TaskConclusion,
                                      ToolResult, Verification)
from backend.app.api import trade
from backend.app.api.common import percentage
from backend.app.api.schemas import YearComparison


_YEAR = re.compile(r'(?<!\d)((?:19|20)\d\d|\d\d)\s*년')
_RANGE_JOIN = re.compile(r'\s*(?:부터|에서|[~～∼–—-])\s*')
_BARE_RANGE_START = re.compile(r'(?<!\d)((?:19|20)\d\d|\d\d)(?=\s*[~～∼–—-]\s*(?:\d{4}|\d{2})\s*년)')
_MONTHLY_AVERAGE = re.compile(r'월\s*평균|월별\s*평균|한\s*달\s*평균')
# Accept attached request verbs, but not longer nouns such as '해운/설명서'.
_FORMAT_COMMAND = (r'(?:해(?:줘|주세요|주십시오|요)?|(?:설명|정리)(?:해(?:줘|주세요|주십시오|요)?)?'
                   r'|(?:보여|알려)(?:줘|주세요|주십시오)?)(?![가-힣])')
# '글로벌/글로비스'의 '글로'는 형식이 아니다. 보조사와 붙여 쓴 부정 표현은 유지한다.
_FORMAT = re.compile(r'(?<![가-힣])(?P<medium>글|문장|텍스트|말|표|차트|그래프)(?:으로|로)'
                     rf'(?=$|[^가-힣]|[만는도]|말고|아니|하지\s*말고|{_FORMAT_COMMAND})(?:\s*만)?')
_FORMAT_NEGATION = re.compile(r'\s*(?:말고|는?\s*아니|하지\s*말고)')
_MONTHLY = re.compile(r'월\s*별|매\s*월|월마다|월\s*단위')
_YEAR_REFERENCE = re.compile(r'(?:그|세|각|이전|같은|앞서|\d+개)\s*(?:연도|년도|개년)|^\s*(?:그럼\s*)?월\s*별')
# A full calendar pivot cannot answer narrower periods, combined totals, rankings or non-amount measures.
_DIFFERENT_MEASURE = re.compile(r'\d+\s*월|분기|반기|기사|원인|리포트|보고서|순위|중량|수량|물량|단가|점유율|환율|무역수지|예측|전망|증감액만|합쳐|합산해|합산한|합친|합한')
_BASE = re.compile(r'(?:기준(?:연도|년도)?|대비)')


_PLAIN_COMPARISON = re.compile(
    r'[\s\d.,?!？·~～∼–—-]+|기준연도|기준년도|연도별|년도별|연도|년도|개년|연간|년간|년|월별|월간|'
    r'월|합계|총액|총|금액|수출액?|수입액?|기준|대비|차액|증감률|차이|비교|분석|나란히|'
    r'부터|까지|각각|각|기간|그리고|모두|전체|한국|우리나라|글|문장|텍스트|말|표|차트|그래프|'
    r'알려|보여|설명|정리|부탁|주세요|해줘|줘|싶어|해보자|해서|하고|해|으로|로|에서|만|을|를|은|는|의|과|와|이|가|에'
)


def requested_years(question, previous=None):
    if _DIFFERENT_MEASURE.search(question):
        return None
    question = _BARE_RANGE_START.sub(r"\1년", question)
    matches = list(_YEAR.finditer(question))
    average = bool(_MONTHLY_AVERAGE.search(question))
    monthly = bool(_MONTHLY.search(_MONTHLY_AVERAGE.sub("", question)))
    if not matches:
        if previous and (monthly or average) and (_YEAR_REFERENCE.search(question) or
                                                  re.match(r'^\s*(?:그럼\s*)?월\s*평균', question)):
            return previous.model_copy(update={'monthly': monthly, 'monthly_average': average})
        return None
    if not re.search(r'비교|대비|나란히|기준', question):
        return None
    anchor = next((int(m[1]) for m in matches if len(m[1]) == 4),
                  previous.years[-1] if previous else 2000)
    def expand(token):
        number = int(token)
        if len(token) == 4:
            return number
        century = (anchor // 100) * 100
        return min([century - 100 + number, century + number, century + 100 + number],
                   key=lambda year: abs(year - anchor))
    years = [expand(m[1]) for m in matches]
    selected = set(years)
    for i, (left, right) in enumerate(zip(matches, matches[1:])):
        if _RANGE_JOIN.fullmatch(question[left.end():right.start()]):
            first, last = sorted(years[i:i+2])
            selected.update(range(first, last + 1))
    distinct = sorted(selected)
    if len(distinct) < 3:
        return None
    # '기준연도는 2025년' names the following year, while '2025년 수출액을 기준으로' names the preceding year.
    named = next((years[i] for i, match in enumerate(matches)
                  if re.search(r'기준\s*(?:연도|년도)\s*(?:는|은|=|:)?\s*$', question[:match.start()])), None)
    baseline = named
    for i, match in enumerate(matches):
        following = question[match.end():matches[i+1].start() if i+1 < len(matches) else len(question)]
        if named is None and _BASE.search(following):
            baseline = years[i]
            break
    if baseline is None and previous and previous.years == distinct:
        baseline = previous.baseline_year
    return YearComparison(years=distinct, baseline_year=baseline, monthly=monthly, monthly_average=average)


def apply_requested_format(tasks, question):
    """첫 질문과 연도 비교에 같은 형식 규칙을 적용한다. 부정한 TEXT 슬롯도 해제한다."""
    for match in _FORMAT.finditer(question):
        text = match['medium'] in ('글', '문장', '텍스트', '말')
        negated = bool(_FORMAT_NEGATION.match(question, match.end()))
        if negated and not text:
            continue   # 표를 빼 달라는 것만으로 차트까지 빼지는 않는다.
        for task in tasks:
            task.slots.answer_format = 'TEXT' if text and not negated else None


def normalize_year_plan(output, question, previous_conditions):
    previous = previous_conditions.year_comparison if previous_conditions else None
    choice = requested_years(question, previous)
    if not choice or not output.tasks:
        return output
    tasks = output.tasks
    if any(t.intent not in ('STATISTICS', 'CONVERSATION') for t in tasks):
        return output
    if all(t.intent == 'CONVERSATION' for t in tasks) and not previous_conditions:
        return output
    # Only fold tasks split by year. Distinct scopes and measures still belong to separate tasks.
    countries = {(c.mention, c.name) for t in tasks for c in (t.slots.countries or [])}
    products = {p for t in tasks for p in (t.slots.products or [])}
    flows = {t.slots.trade_flow for t in tasks if t.slots.trade_flow}
    if len(countries) > 1 or len(products) > 1 or len(flows) > 1:
        return output
    if previous_conditions and (len(previous_conditions.country_codes) > 1 and not countries
                                 or len(previous_conditions.hs_items) > 1 and not products):
        return output
    apply_requested_format(tasks, question)
    # A custom plan may ask for a calculation outside the amount comparison template.
    # Fold it only if the user's words describe a calculation this template can deliver.
    if any(t.source_tasks or t.target_selections for t in tasks):
        return output
    if any(t.slots.trade_balance for t in tasks):
        return output   # 무역수지는 한 방향 금액 연도 비교로 묶지 않는다(이슈 113 서버 TREND가 맡는다)
    if any(t.custom_query for t in tasks):
        remainder = _MONTHLY_AVERAGE.sub('', question)
        for name in sorted(products | {n for pair in countries for n in pair if n}, key=len, reverse=True):
            remainder = remainder.replace(name, '')
        if _PLAIN_COMPARISON.sub('', remainder):
            return output
    task = tasks[0].model_copy(deep=True)
    task.intent, task.question, task.custom_query = 'STATISTICS', question, None
    task.source_tasks, task.target_selections = [], []
    task.slots.period = task.slots.compare_period = task.slots.recent_months = None
    task.slots.extra_compare_periods = None   # 여러 비교 기간 칸(이슈 113)도 연도 비교가 대신한다
    task.slots.comparison_requested = task.slots.amount_difference_only = None
    if any(t.slots.answer_format == 'TEXT' for t in tasks):
        task.slots.answer_format = 'TEXT'
    if countries:
        task.slots.countries = next(t.slots.countries for t in tasks if t.slots.countries)
    elif previous_conditions and '전체' not in question:
        task.slots.countries = None
    if products:
        task.slots.products = list(products)
    elif previous_conditions and '전체' not in question:
        task.slots.products = None
    if flows:
        task.slots.trade_flow = next(iter(flows))
    return PlannerOutput(tasks=[task])


def annual_rows(annual, base, *, monthly_average=False):
    totals = []
    for year, amount in annual.items():
        row = {'year_label': f'{year}년', 'amount_usd': amount}
        if monthly_average:
            row['monthly_average'] = ((amount / 12).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                                      if amount is not None else None)
        if base is not None:
            baseline = annual[base]
            row['difference_usd'] = amount - baseline if None not in (amount, baseline) else None
            row['change_pct'] = percentage(amount, baseline)
        totals.append(row)
    return totals


def run_year_comparison(store, conditions, timeout):
    from backend.app.agents.templates import Analysis, api_conditions
    comparison = conditions.year_comparison
    years = comparison.years
    source = 'import_summary' if conditions.trade_flow == 'IMPORT' else 'export_summary'
    flow = '수입액' if conditions.trade_flow == 'IMPORT' else '수출액'
    verified = set(trade.verified_months(store, source))
    hs = [h.model_dump() for h in conditions.hs_items]
    started = monotonic()
    annual, monthly = {}, {}
    for year in years:
        if monotonic() - started >= timeout:
            raise TimeoutError('SQL template time budget exhausted')
        observed = {r['trade_month']: r['amount_usd'] for r in trade.aggregate(
            store, source, date(year, 1, 1), date(year, 12, 1), hs, conditions.country_codes, 'MONTH')}
        values = [observed.get(month, Decimal(0)) if month in verified else None
                  for month in (date(year, n, 1) for n in range(1, 13))]
        monthly[year] = values
        annual[year] = sum(values, Decimal(0)) if None not in values else None

    base = comparison.baseline_year
    totals = annual_rows(annual, base, monthly_average=comparison.monthly_average)

    def column(key, label, unit=None):
        return {'key': key, 'label': label, 'type': 'number' if unit else 'string', 'unit': unit}
    if comparison.monthly:
        columns = [column('month_label', '월'), *[
            column(f'year_{year}_usd', f'{year}년 {flow}' + (' (기준)' if year == base else ''), 'USD')
            for year in years]]
        rows = [{'month_label': f'{n+1}월', **{f'year_{year}_usd': monthly[year][n] for year in years}}
                for n in range(12)]
    else:
        rows = totals
        columns = [column('year_label', '연도'), column('amount_usd', flow, 'USD')]
        if comparison.monthly_average:
            columns.append(column('monthly_average', f'월평균 {flow}', 'USD'))
        if base is not None:
            columns.extend([column('difference_usd', f'{base}년 대비 차액', 'USD'),
                            column('change_pct', f'{base}년 대비 증감률', '%')])
    meta = trade.metadata(store, conditions.period.start, conditions.period.end)
    meta.update(kind='MULTI_YEAR', year_comparison=comparison.model_dump())
    limitations = ['기간 내 미집계 월이 있습니다.'] if None in annual.values() else []
    result = ToolResult(status='OK', period=conditions.period, source='관세청', limitations=limitations,
        completeness='PARTIAL' if limitations else 'COMPLETE', digest=bounded({
            'kind':'MULTI_YEAR', 'trade_flow':conditions.trade_flow, 'source':'관세청',
            'unit':'USD', 'baseline_year':base, 'annual_totals':totals, 'monthly':comparison.monthly,
            'rows':rows, 'columns':columns, 'rows_total':len(rows)}))
    return Analysis(rows, meta, result, api_conditions(conditions), list(store.queries), columns)


def year_comparison_draft(state):
    if len(state.plan.tasks) != 1:
        return None
    task = state.plan.tasks[0]
    if not task.conditions or not task.conditions.year_comparison or task.answer_status != 'ANSWERED':
        return None
    result = next((state.results.get(s.step_id) for s in task.steps if s.tool == 'query_trade_data'), None)
    evidence = result.digest if result and result.status == 'OK' else None
    if not evidence or evidence.get('kind') != 'MULTI_YEAR':
        return None
    return render_year_draft(state, evidence['annual_totals'])


def render_year_draft(state, totals):
    task = state.plan.tasks[0]
    flow = '수출액' if task.conditions.trade_flow == 'EXPORT' else '수입액'
    amount_list = ', '.join(f"{r['year_label']} {korean_usd(r['amount_usd']) or '미집계'}" for r in totals)
    summary = f'연간 {flow}은 {amount_list}입니다.'
    base = task.conditions.year_comparison.baseline_year
    differences = ', '.join(f"{r['year_label']} {r['change_pct']:+.1f}%" for r in totals
                            if r['year_label'] != f'{base}년' and r.get('change_pct') is not None)
    explanation = []
    if task.conditions.year_comparison.monthly_average:
        amounts = ', '.join(f"{r['year_label']} {r['monthly_average']:,.2f}달러"
                            if r.get('monthly_average') is not None else f"{r['year_label']} 미집계"
                            for r in totals)
        explanation.append(summary)
        summary = f'연도별 월평균 {flow}은 {amounts}입니다.'
    if differences and base is not None:
        explanation.append(f'{base}년 {flow} 대비 각 연도의 차이는 {differences}입니다.')
    if task.conditions.year_comparison.monthly:
        explanation.append('같은 달의 금액은 월별 표에서 연도별로 비교할 수 있습니다.')
    if len(totals) != len(task.conditions.year_comparison.years) or any(len(s) > 200 for s in [summary, *explanation]):
        return None
    return Draft(conclusion=[TaskConclusion(task_id=task.task_id, sentences=[summary])],
        explanation=explanation, turn_summary=summary, title=task.question if state.input.first_question else None)


def verify_year_draft(state, snapshots):
    """Recalculate annual claims from the complete saved table, including the monthly pivot."""
    if not state.draft or not state.plan or len(state.plan.tasks) != 1:
        return None
    task = state.plan.tasks[0]
    comparison = getattr(getattr(task, 'conditions', None), 'year_comparison', None)
    if not comparison or task.answer_status != 'ANSWERED':
        return None
    step = next((s for s in task.steps if s.tool == 'query_trade_data'), None)
    snapshot = snapshots.get(step.step_id) if step else None
    if not snapshot or snapshot.get('kind') != 'MULTI_YEAR':
        return None
    saved_comparison = snapshot.get('year_comparison') or {}
    # Snapshots from before monthly averages have no monthly_average field.
    if ({'monthly_average': False, **saved_comparison} != comparison.model_dump()
            or snapshot.get('trade_flow') != task.conditions.trade_flow
            or snapshot.get('country_codes') != task.conditions.country_codes
            or snapshot.get('hs_items') != [h.model_dump() for h in task.conditions.hs_items]):
        return None
    rows = snapshot.get('rows', [])
    if comparison.monthly:
        if [r.get('month_label') for r in rows] != [f'{n}월' for n in range(1, 13)]:
            return None
        annual = {}
        for year in comparison.years:
            values = [r.get(f'year_{year}_usd') for r in rows]
            if any(v is None for v in values):
                return None
            annual[year] = sum(Decimal(str(v)) for v in values)
    else:
        if [r.get('year_label') for r in rows] != [f'{year}년' for year in comparison.years]:
            return None
        if any(r.get('amount_usd') is None for r in rows):
            return None
        annual = {year: Decimal(str(r['amount_usd'])) for year, r in zip(comparison.years, rows)}
    totals = annual_rows(annual, comparison.baseline_year, monthly_average=comparison.monthly_average)
    if comparison.monthly_average and not comparison.monthly and any(
            row.get('monthly_average') != float(total['monthly_average']) for row, total in zip(rows, totals)):
        return None
    expected = render_year_draft(state, bounded({'annual': totals})['annual'])
    if expected is None:
        return None
    actual_lines, expected_lines = state.draft.sentences(), expected.sentences()
    checks, feedback = [], []
    for i, line in enumerate(actual_lines):
        matches = i < len(expected_lines) and line == expected_lines[i]
        checks.append(SentenceCheck(index=i, numbers=re.findall(r'[-+]?\d[\d,.]*', line), basis='SNAPSHOT',
                                    status='PASS' if matches else 'MISMATCH',
                                    reason='MATCH' if matches else 'NOT_CONFIRMED'))
        if not matches:
            feedback.append(ClaimFeedback(section='conclusion' if i == 0 else 'explanation',
                                          task_id=task.task_id, index=i, passage=line, reason='NOT_CONFIRMED'))
    summary_ok = state.draft.turn_summary == expected.turn_summary
    return Verification(checks=checks, feedback=feedback,
        status='PASS' if summary_ok and not feedback and len(actual_lines) == len(expected_lines) else 'NEEDS_REVIEW',
        summary_status='PASS' if summary_ok else 'MISMATCH',
        failed_summary='' if summary_ok else state.draft.turn_summary,
        regeneration_count=state.verification.regeneration_count)
