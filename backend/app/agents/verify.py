"""Verify immutable claims against typed facts from the same saved result.

Parsing, evidence extraction and matching are separate stages. Numeric equality
never determines how a sentence is parsed or which semantic attributes it carries.
Public Draft/SentenceCheck/Verification contracts are unchanged.
"""
from decimal import Decimal, ROUND_HALF_UP, localcontext
from dataclasses import dataclass, replace
import re

from backend.app.agents.compose import fixed_conclusion
from backend.app.agents.state import ClaimFeedback, SentenceCheck, Verification
from backend.app.agents.verify_claims import (
    ARTICLE_CITATION, NameCatalog, Claim, TimeClaim, parse_sentence, period_end_months, quantities, mask_dates, quantity_text,
    change_phrase_end, COMPARISON, DOWN, UP, TOKEN, DATE, HISTORY_DATE, _shown, _merge_compound, _target_count, _name_pattern, months_between, shift,
)
from backend.app.agents.verify_evidence import Fact, bind_targets, build_facts, compare_key, period


@dataclass(frozen=True)
class ClaimCheck:
    claim: Claim
    reason: str
    matches: tuple[tuple[int,tuple[str,...]],...] = ()
    interpretation_valid: bool = True


def _source_supports(time: TimeClaim,source):
    windows=[p for k in ('period','compare_period','article_period') if (p:=period(source.get(k)))]
    windows.extend(p for value in source.get('extra_compare_periods') or [] if (p:=period(value)))
    if source.get('anchor'): windows.append((source['anchor'][:7],source['anchor'][:7]))
    if time.years and not all(any(start[:4]<=year<=end[:4] for start,end in windows) for year in time.years): return False
    if time.months and not all(any(start<=month<=end for start,end in windows) for month in time.months): return False
    if time.bare_months and not all(any(any(int(shift(start,i)[5:])==month for i in range(min(12,months_between(start,end)+1)))
                                       for start,end in windows) for month in time.bare_months): return False
    if time.durations and not time.durations<={*(months_between(start,end)+1 for start,end in windows),
                                             *([12] if source.get('kind')=='PREDICTION' else [])}: return False
    return True


def _time_matches(claim:Claim,fact:Fact):
    time=claim.time
    periods=tuple(p for p in ((fact.compare_period,) if time.comparison_reference and fact.metric.family=='CHANGE'
                              else (fact.period,)) if p)
    months={fact.month} if fact.month else set()
    if time.comparison_reference and fact.metric.family=='CHANGE':
        months={fact.compare_period[0]} if fact.compare_period and fact.compare_period[0]==fact.compare_period[1] else set()
    if time.cumulative and (not fact.period_value or not fact.period
                            or fact.period[0] == fact.period[1]):
        return False
    if time.durations:
        lengths={months_between(*p)+1 for p in periods}
        if fact.horizon: lengths.add(fact.horizon)
        if not time.durations<=lengths: return False
    if time.period: return time.period in periods
    if time.month_window:
        return any(start[:4] == end[:4] and (int(start[5:]), int(end[5:])) == time.month_window
                   and (not time.months or all(month[:4] == start[:4] for month in time.months))
                   for start,end in periods)
    if time.years:
        if fact.prediction:
            if not fact.anchor or time.years!={fact.anchor[:4]}: return False
        elif not all((year+'-01',year+'-12') in periods for year in time.years): return False
    if time.months or time.bare_months:
        anchor_reference=fact.prediction and fact.horizon==12 and 12 in time.durations and time.anchor_reference
        if anchor_reference:
            months={fact.anchor} if fact.anchor else set()
        if not months: return False
        if not time.months<=months or not time.bare_months<={int(m[5:]) for m in months}: return False
    return True


def _comparison_matches(required,fact):
    if not required: return True
    if required=='FORECAST': return fact.prediction and fact.role=='FUTURE'
    if fact.metric.comparison==required: return True
    if fact.metric.comparison!='PERIOD' or not fact.period or not fact.compare_period: return False
    return _windows_match(required,fact.period,fact.compare_period)


def _windows_match(required,current,previous):
    if not current or not previous: return False
    start,end=current;before,after=previous
    if required=='YOY': return (before,after)==(shift(start,-12),shift(end,-12))
    if required=='MOM': return start==end and before==after==shift(start,-1)
    return False


def _level_period_matches(role, time, fact, source):
    prior = fact.metric.key.startswith('compare_')
    if role == 'AUTO':
        if not prior:
            return True  # Calendar constraints are checked by _time_matches.
        current = period(source.get('period'))
        if time.period:
            return time.period != current
        if current and time.durations and months_between(*current)+1 not in time.durations:
            return True  # _time_matches must still confirm the comparison window's length.
        dated = time.years or time.months or time.bare_months
        return bool(dated and current and not _source_supports(time, {'period': source['period']}))
    if role == 'CURRENT':
        return not prior
    if not prior:
        return False
    return role == 'COMPARE' or _windows_match(role, period(source.get('period')),
                                                compare_key(source, fact.path[-1])[1])


def mismatch(claim:Claim,fact:Fact,source):
    """Compare attributes before values. Return a diagnostic without changing meaning."""
    q,m=claim.quantity,claim.metric
    if not claim.valid or not fact.metric.valid: return 'AMBIGUOUS'
    if not _source_supports(claim.time,source): return 'SOURCE_PERIOD'
    if not claim.targets<=fact.targets: return 'TARGET'
    if claim.target_scope=='GROUP':
        if not fact.group_members or not fact.group_members<=claim.targets: return 'GROUP_SCOPE'
    elif fact.group_members: return 'INDIVIDUAL_SCOPE'
    if m.flow and fact.metric.flow and m.flow!=fact.metric.flow: return 'TRADE_FLOW'
    if m.family == 'MONTH_STATE':
        if not fact.month or fact.metric.key != 'amount_usd': return 'METRIC'
        if not _time_matches(claim, fact): return 'PERIOD'
        verified = fact.value is None if m.direction == 'MISSING' else fact.value == 0
        return None if verified else 'MONTH_STATE'
    if m.family == 'OUTLOOK' or fact.metric.family == 'OUTLOOK':
        if m.family != fact.metric.family: return 'METRIC'
        if not _time_matches(claim, fact): return 'PERIOD'
        expected = {'증가':'증가 예상', '감소':'감소 예상', '미분류':None}[m.direction]
        return None if fact.value == expected else 'OUTLOOK'
    if q is None:
        if not claim.targets and fact.targets: return 'TARGET'
        if m.flow and m.flow!=fact.metric.flow: return 'TRADE_FLOW'
        if m.family != 'SHARE_CHANGE' and (
                re.search(r'(?:^|_)(?:price|volume|quantity|weight|margin|profit|share)(?:_|$)', fact.metric.key)
                or re.search(r'단가|가격|물량|중량|수량|이익|비중|점유율', fact.metric.label)):
            return 'METRIC'
        if m.family=='SHARE_CHANGE':
            compatible=fact.metric.family=='SHARE_CHANGE' and fact.unit=='%p'
        else:
            compatible=(fact.metric.family,fact.unit) in (('CHANGE','%'),('AMOUNT_CHANGE','USD'))
        if not compatible: return 'METRIC'
        if not _time_matches(claim,fact): return 'PERIOD'
        if not _comparison_matches(m.comparison,fact): return 'COMPARISON'
        correct=fact.value>0 if m.direction=='UP' else fact.value<0
        return None if correct else 'DIRECTION'
    if not q.unit or q.unit!=fact.unit: return 'UNIT'
    expected_family=m.family
    if m.direct_change and m.family=='SHARE' and q.unit=='%': expected_family='SHARE_CHANGE'
    if expected_family and fact.metric.family!=expected_family: return 'METRIC'
    if m.aggregate == 'SUBTOTAL' and 'verified_subtotal_usd' not in fact.metric.key:
        monthly_level = fact.month and fact.metric.key == 'amount_usd' and (claim.time.months or claim.time.bare_months)
        if not monthly_level: return 'AGGREGATION'
    # 국가 순위의 total_usd는 share_pct의 분모다("총액 18억 달러를 분모로 한 비중").
    if m.aggregate=='DENOMINATOR' and not (fact.metric.key=='denominator_usd'
                                           or (fact.metric.key=='total_usd' and source.get('kind')=='RANKING')): return 'METRIC'
    if m.aggregate=='AVERAGE' and fact.metric.aggregate!='AVERAGE': return 'AVERAGE'
    if q.unit=='USD' and m.aggregate!='AVERAGE' and fact.metric.aggregate=='AVERAGE': return 'AVERAGE'
    if m.aggregate=='TOTAL':
        if fact.metric.aggregate=='AVERAGE' or 'verified_subtotal' in fact.metric.key: return 'AGGREGATION'
        if not fact.period_value and not (claim.time.months or claim.time.bare_months): return 'AGGREGATION'
    if m.amount_period and not _level_period_matches(m.amount_period,claim.time,fact,source):
        return 'AMOUNT_PERIOD'
    if not _time_matches(claim,fact): return 'PERIOD'
    if not _comparison_matches(m.comparison,fact): return 'COMPARISON'
    if fact.horizon and m.prediction_role and m.prediction_role!={fact.role}: return 'PREDICTION_HORIZON'
    if m.rank_axis and fact.prediction and m.rank_axis!=fact.metric.key: return 'RANK_AXIS'
    value,amount=q.value,fact.value
    direction_applies=m.direction and (m.direct_change or fact.metric.family in ('CHANGE','SHARE_CHANGE'))
    if m.direction and q.signed and (value<0)!=(m.direction=='DOWN'): return 'SIGN'
    if direction_applies:
        if amount==0 or (amount<0)!=(m.direction=='DOWN'): return 'DIRECTION'
        amount,value=abs(amount),abs(value)
    if m.relation!='EQ':
        threshold=value*q.scale
        comparison={'LT':amount<threshold,'GT':amount>threshold,'LE':amount<=threshold,'GE':amount>=threshold}
        return None if comparison[m.relation] else 'VALUE'
    granularity=Decimal(1).scaleb(value.as_tuple().exponent)
    with localcontext() as context:
        context.prec=max(32,len(amount.as_tuple().digits)+abs(granularity.as_tuple().exponent)+16)
        if (amount/q.scale).quantize(granularity,rounding=ROUND_HALF_UP)!=value: return 'VALUE'
    return None


@dataclass(frozen=True)
class PreparedEvidence:
    sources: list
    catalog: NameCatalog
    facts: tuple[Fact, ...]


def _prepare(sources, known_names, countries, *, mention_cache=None):
    catalog = NameCatalog(sources, known_names, countries, mention_cache=mention_cache)
    return PreparedEvidence(sources, catalog, build_facts(sources, catalog))


def inspect_sentence(sentence,sources,*,known_names=(),countries=()):
    """Internal audit API: immutable claims and the exact source cells proving them."""
    return _inspect_prepared(sentence, _prepare(sources, known_names, countries))


def _single_series(claim, facts, keep):
    """한 대상의 월별 금액 칸. 대상이 둘 이상으로 갈리면 어느 행인지 알 수 없어 비운다."""
    eligible = [f for f in facts if f.metric.key == 'amount_usd' and f.month and claim.targets <= f.targets and keep(f)]
    return eligible if len({f.stored_targets for f in eligible}) == 1 else []


def _monthly_amounts(claim, facts, sources):
    return _single_series(claim, facts, lambda f: (
        isinstance(f.value, Decimal) and not f.group_members
        and (not claim.metric.flow or not f.metric.flow or claim.metric.flow == f.metric.flow)
        and _source_supports(claim.time, sources[f.source])
        and (not claim.time.period or claim.time.period[0] <= f.month <= claim.time.period[1])))


def _direction_holds(direction, first, last):
    return last > first if direction == 'UP' else last < first if direction == 'DOWN' else False


def _period_trend_cells(claim, facts, sources):
    """기간 추세("전반적으로 증가")는 한 대상의 월별 금액에서 첫 달과 마지막 달을 비교한다."""
    if claim.metric.family != 'CHANGE' or claim.metric.negated_change:
        return ()
    months = sorted(_monthly_amounts(claim, facts, sources), key=lambda f: f.month)
    if len(months) < 2 or months[0].month == months[-1].month:
        return ()
    first, last = months[0], months[-1]
    if not _direction_holds(claim.metric.direction, first.value, last.value):
        return ()
    return ((first.source, first.path), (last.source, last.path))


def _month_change_cells(claim, facts, sources):
    """"10월에 감소했습니다": 비교 기준이 없는 특정 달의 증감은 월별 추이의 전월 대비로 판정한다."""
    if claim.metric.negated_change or claim.metric.direction not in ('UP', 'DOWN'):
        return ()
    by_month = {f.month: f for f in _monthly_amounts(replace(claim, time=TimeClaim()), facts, sources)}
    if claim.time.months:
        month, = claim.time.months
    else:
        bare, = claim.time.bare_months
        candidates = [m for m in by_month if int(m[5:7]) == bare]
        if len(candidates) != 1:
            return ()  # 여러 해에 같은 달이 있으면 연도 없는 "12월"이 어느 해인지 알 수 없다.
        month, = candidates
    current = by_month.get(month)
    if current is None:
        return ()
    previous = by_month.get(shift(month, -1))
    if previous is None or not _direction_holds(claim.metric.direction, previous.value, current.value):
        return ()
    return ((previous.source, previous.path), (current.source, current.path))


def _implied_amount_direction(checks, prepared):
    """"7월 2,100만 달러에서 12월 2,840만 달러로 늘었습니다": 같은 문장에서 확인한 두 달 금액으로 방향을 판정한다."""
    cells = {(f.source, f.path): f for f in prepared.facts}
    out = list(checks)
    for index, check in enumerate(checks):
        metric = check.claim.metric
        if (check.claim.quantity is not None or metric.family != 'CHANGE' or metric.comparison
                or metric.negated_change or metric.direction not in ('UP', 'DOWN')):
            continue
        points = {}
        for other in checks:
            if (other.claim.quantity is None or other.claim.quantity.unit != 'USD' or len(other.matches) != 1
                    or other.claim.targets != check.claim.targets):
                continue
            fact = cells.get(other.matches[0])
            if fact and fact.metric.key == 'amount_usd' and fact.month and isinstance(fact.value, Decimal):
                points.setdefault(fact.month, (fact, other.matches[0]))
        if len(points) != 2:
            continue
        # 두 달 금액이 있으면 그 비교만 근거로 쓴다. 마지막 달의 전월 대비 판정으로 대신 통과시키지 않는다.
        (first, first_cell), (last, last_cell) = (points[m] for m in sorted(points))
        if first.stored_targets == last.stored_targets and _direction_holds(metric.direction, first.value, last.value):
            out[index] = ClaimCheck(check.claim, 'MATCH', (first_cell, last_cell), check.interpretation_valid)
        else:
            out[index] = ClaimCheck(check.claim, 'NOT_CONFIRMED', (), check.interpretation_valid)
    return tuple(out)


def _matching_cells(claim, facts, sources, catalog):
    if claim.metric.family == 'OUTLOOK':
        # An implicit market is identifiable only if one saved classification is in scope.
        eligible = [f for f in facts if f.metric.family == 'OUTLOOK' and claim.targets <= f.targets
                    and _source_supports(claim.time, sources[f.source]) and _time_matches(claim, f)]
        if len(eligible) != 1:
            return ()
        fact, = eligible
        return ((fact.source, fact.path),) if mismatch(claim, fact, sources[fact.source]) is None else ()
    if claim.quantity is None and claim.metric.comparison == 'PERIOD':
        return _period_trend_cells(claim, facts, sources)
    if (claim.quantity is None and claim.metric.family == 'CHANGE' and claim.metric.comparison is None
            and len(claim.time.months) + len(claim.time.bare_months) == 1):
        return _month_change_cells(claim, facts, sources)
    if claim.metric.family == 'MONTH_STATE':
        eligible = _single_series(claim, facts, lambda f: _time_matches(claim, f))
        if not eligible:
            return ()
        if any(mismatch(claim, f, sources[f.source]) is not None for f in eligible):
            return ()
        return tuple((f.source, f.path) for f in eligible)
    if claim.targets and claim.targets <= catalog.ambiguous_countries:
        return ()  # "베트남 2,840만 달러": 베트남 기초화장품·베트남 라면 중 어느 행인지 알 수 없다.
    matches = []
    inferred_axes = set()
    check_dimensions = claim.quantity is None or (claim.targets and claim.metric.family == 'CHANGE')
    def dimensions(targets):
        named = targets - {'전체 국가', '전체 품목'}
        return bool(named - catalog.countries), bool(named & catalog.countries)
    required_dimensions = dimensions(claim.targets) if check_dimensions else None
    for fact in facts:
        if check_dimensions and dimensions(fact.stored_targets) != required_dimensions:
            continue
        reason = mismatch(claim, fact, sources[fact.source])
        if claim.quantity is None and not claim.metric.comparison and reason in (None, 'DIRECTION'):
            axes = [axis for axis in ('YOY', 'MOM') if _comparison_matches(axis, fact)]
            if not axes:
                continue
            inferred_axes.update(axes)
        if claim.metric.negated_change:
            # Invert only an evidenced numeric predicate. A missing or
            # incompatible cell proves neither the assertion nor its negation.
            if reason is None:
                return ()
            supports = reason in ('DIRECTION', 'VALUE')
        else:
            if claim.quantity is None and reason == 'DIRECTION': return ()
            supports = reason is None
        if supports:
            matches.append((fact.source, fact.path))
    return tuple(matches) if len(inferred_axes) <= 1 else ()


def _inspect_prepared(sentence, prepared):
    catalog, facts, sources = prepared.catalog, prepared.facts, prepared.sources
    parsed=parse_sentence(sentence,catalog)
    checks=[]
    for claim in parsed.claims:
        matches=_matching_cells(claim,facts,sources,catalog)
        checks.append(ClaimCheck(claim,'MATCH' if matches else 'NOT_CONFIRMED',matches,parsed.valid))
    checks=list(_implied_amount_direction(checks, prepared))
    dates_ok=all(any(_source_supports(time,source) for source in sources) for time in parsed.date_only)
    dates_ok=dates_ok and all(any(str((source.get('period') or {}).get('end') or '')[:7]==month
                                  or str((source.get('article_period') or {}).get('end') or '')[:10]==day for source in sources)
                              for month, day in period_end_months(sentence))
    return parsed.valid and dates_ok and all(c.matches for c in checks),tuple(checks)


def check_sentence(sentence,digests,*,known_names=(),countries=()):
    return _check_prepared(sentence, _prepare(digests, known_names, countries))


def _check_prepared(sentence, prepared):
    good,checks=_inspect_prepared(sentence, prepared)
    numbers=[check.claim.quantity.shown for check in checks if check.claim.quantity is not None]
    if not checks and not good:
        numbers=[q.shown for q in quantities(quantity_text(sentence,prepared.catalog))]
    return good,numbers


def evidence(digest):
    """Compatibility view; internal matching uses Fact objects, not these tuples."""
    return [(f.metric.key,f.value,f.unit,set(f.targets)) for f in build_facts([digest],NameCatalog([digest])) if f.unit not in ('OUTLOOK', 'NULL')]


def snapshot_evidence(payload):
    """Full saved rows, never paginated or bounded for LLM input. No new SQL."""
    conditions=payload.get('conditions') or {}
    meta=payload.get('meta') or {}
    scope={k:conditions[k] for k in ('period','compare_period','trade_flow') if k in conditions}
    if meta.get('extra_compare_periods'): scope['extra_compare_periods']=meta['extra_compare_periods']
    if meta.get('kind')=='PREDICTION' and meta.get('anchor'):
        anchor=str(meta['anchor'])[:7]
        scope.update(kind='PREDICTION',anchor=anchor,period={'start':anchor,'end':anchor})
    return {**scope,
            'columns':payload.get('columns',[]),'rows':payload.get('rows',[]),
            '_verification_source':'SNAPSHOT'}


def _tokens(text):
    text=_merge_compound(HISTORY_DATE.sub(' ',ARTICLE_CITATION.sub('',text)))
    return [m for m in TOKEN.finditer(text) if not _target_count(text,m)]


# TOKEN은 영문자 뒤 숫자를 읽지 않는다. "HS8516"처럼 붙여 쓴 코드는 따로 읽어 조회한 코드인지 대조한다
HS_PREFIXED = re.compile(r'(?<![A-Za-z])HS\s*-?\s*(?P<code>\d{2,10})', re.I)


def check_codes(sentence, digests):
    """HS_LOOKUP(D-101) 문장의 숫자는 조회한 HS 코드(와 품목명 속 숫자)만 허용한다.
    "HS8516"처럼 HS를 붙여 쓴 코드도 조회한 코드이거나 판본 표기(HS2022 등)여야 한다."""
    allowed, versions = set(), set()
    for digest in digests:
        versions.update(re.findall(r'\d+', str(digest.get('hs_ver', ''))))
        for item in digest.get('items', []):
            allowed.add(str(item.get('code', '')))
            allowed.update(m['value'].replace(',','') for m in _tokens(str(item.get('name', ''))))
    found=_tokens(sentence)
    prefixed=[m['code'] for m in HS_PREFIXED.finditer(sentence)]
    good=(all(m['value'].replace(',','') in allowed for m in found)
          and all(code in allowed or code in versions for code in prefixed))
    return good,([_shown(m) for m in found]+[f'HS{code}' for code in prefixed if code not in versions])[:10]


_SCALES = {'': 1, '천': 10**3, '만': 10**4, '억': 10**8, '조': 10**12}


def _amount(value, scale):
    try:
        return Decimal(value) * _SCALES.get(scale, 0) if scale in _SCALES else (value, scale)
    except ArithmeticError:
        return (value, scale)


def _fact(match):
    """대조용 숫자 표기: 값·단위 배율(만·억·조)·단위(%·달러 등)."""
    return match['value'].replace(',',''), match['scale'] or '', (match['unit'] or '').casefold()


def _date_keys(year, month=None):
    # "2025년 5월" 요약을 "5월"·"2025년"으로 줄여 써도 같은 날짜다
    return {str(year)} | ({f'{year}-{int(month):02d}', f'{int(month)}월'} if month else set())


def _dates(text):
    """문장 속 절대 날짜(연·월). 숫자 대조(_tokens)는 날짜를 지우므로 따로 읽는다.
    "최근 12개월"처럼 기간 길이는 날짜가 아니라서 대조하지 않는다(전처럼 통과)."""
    out=set()
    for m in HISTORY_DATE.finditer(ARTICLE_CITATION.sub('',text)):
        if m[1]: out|=_date_keys(m[1], m[2] or m[3])
        elif m[5]=='월': out.add(m[4]+'월')
    return out


def _turn_dates(turn):
    """이전 질문의 확정 조건 기간(시작·끝 달)도 그 대화에서 말한 날짜로 본다."""
    period=getattr(getattr(turn,'conditions',None),'period',None)
    return set().union(*(_date_keys(d.year, d.month) for d in (period.start, period.end))) if period else set()


def check_history(sentence, recent):
    """CONVERSATION(D-99) 문장의 숫자는 최근 대화의 질문·요약에 있는 값만 허용한다. 요약 숫자는 이미 verify를 통과했다(D-52).
    값만이 아니라 단위 배율·단위(12억 달러 ≠ 12%)와 날짜(연·월)까지 같아야 한다. 문장에 단위를 생략했으면 값·배율만 본다.
    날짜는 이전 질문·요약에 쓰였거나 그 질문의 확정 조건 기간의 시작·끝 달이면 허용한다."""
    history=[f'{t.question} {t.summary}' for t in recent]
    facts={_fact(m) for text in history for m in _tokens(text)}
    dates=set().union(set(),*(_dates(text) for text in history),*(_turn_dates(t) for t in recent))
    found=_tokens(sentence)
    def known(m):
        value,scale,unit=_fact(m)
        # 같은 금액의 다른 표기(28,400,000달러 = 2,840만 달러)는 같은 값이다. 단위(%·달러)는 따로 맞춘다.
        return any(_amount(v,s)==_amount(value,scale) and (not unit or u==unit) for v,s,u in facts)
    good=all(known(m) for m in found) and _dates(sentence)<=dates
    return good,[_shown(m) for m in found]


def generated_conclusion(state, task, has_evidence):
    fixed_ids = getattr(state, 'fixed_conclusion_ids', None)
    if fixed_ids is not None:
        return task.task_id not in fixed_ids
    return (has_evidence or task.intent=='CONVERSATION') and fixed_conclusion(state,task) is None


def mark_unverified(state):
    """시간이 부족해 대조하지 못했을 때(§1·§13.4): verify가 대조할 LLM 문장마다 "확인 필요"(MISMATCH)를 붙인다.
    고정 문구 결론은 건너뛴다. 증감 주장은 수치가 없어도 확인 필요이며, 안내문은 PASS다.
    확인하지 않은 숫자를 다음 턴 요약에 남기지 않도록 turn_summary는 비운다(D-77)."""
    checks,index=[],0
    catalog=NameCatalog([result.digest for result in state.results.values() if result.digest])
    feedback = []
    def add(sentence, section, task_id=None):
        text=quantity_text(sentence,catalog)
        numbers=[q.shown for q in quantities(text)]
        needs_check = bool(numbers) or any(c.quantity is None for c in parse_sentence(sentence,catalog).claims)
        if needs_check:
            feedback.append(ClaimFeedback(section=section, task_id=task_id, index=index,
                                          passage=sentence, reason="NOT_CONFIRMED"))
        checks.append(SentenceCheck(index=index,numbers=numbers,status='MISMATCH' if needs_check else 'PASS',
                                    reason='NOT_CONFIRMED' if needs_check else 'MATCH'))
    for task,conclusion in zip(state.plan.tasks,state.draft.conclusion,strict=True):
        evidence=any(s.step_id in state.results and state.results[s.step_id].digest for s in task.steps)
        generated=generated_conclusion(state,task,evidence)
        for sentence in conclusion.sentences:
            if generated: add(sentence, "conclusion", task.task_id)
            index+=1
    for sentence in state.draft.explanation:
        add(sentence, "explanation"); index+=1
    failed_summary = state.draft.turn_summary if fixed_conclusion(state,state.plan.tasks[0]) is None else ''
    if failed_summary:
        state.draft.turn_summary = ''
        feedback.append(ClaimFeedback(section='summary', task_id=state.plan.tasks[0].task_id,
                                      passage=failed_summary, reason='NOT_CONFIRMED'))
    state.verification = Verification(
        checks=checks, feedback=feedback, failed_summary=failed_summary,
        summary_status='MISMATCH' if failed_summary else None,
        regeneration_count=state.verification.regeneration_count,
        status='NEEDS_REVIEW' if feedback else 'PASS',
    )


def diagnose_claim(claim, prepared, group):
    """Only identify an attribute when an otherwise compatible cell isolates it."""
    if not claim.valid or not claim.metric.family or not claim.targets:
        return 'NOT_CONFIRMED', None, None
    if claim.metric.negated_change or claim.metric.family == 'OUTLOOK':
        return 'NOT_CONFIRMED', None, None
    candidates = []
    contradicted_rows = set()
    for fact in prepared.facts:
        if claim.targets != fact.targets or claim.metric.family != fact.metric.family:
            continue
        source = prepared.sources[fact.source]
        reason = mismatch(claim, fact, source)
        if reason in ('VALUE', 'DIRECTION') and _comparison_matches(claim.metric.comparison, fact):
            contradicted_rows.add((group[fact.source], fact.path[:-1]))
        if reason == 'COMPARISON':
            if not any(_comparison_matches(axis, fact) for axis in ('MOM', 'YOY')):
                continue
            relaxed = replace(claim, metric=replace(claim.metric, comparison=None))
        elif reason == 'DIRECTION' and fact.value != 0:
            relaxed = replace(claim, metric=replace(claim.metric, direction='UP' if fact.value>0 else 'DOWN'))
        elif reason in ('PERIOD', 'SOURCE_PERIOD'):
            if reason == 'SOURCE_PERIOD' and not (
                    source.get('anchor') or any(period(source.get(key)) for key in ('period', 'compare_period', 'article_period'))):
                continue
            if not (fact.period or fact.month or fact.anchor):
                continue
            reason = 'PERIOD'
            relaxed = replace(claim, time=TimeClaim())
        elif reason == 'VALUE':
            relaxed = None
        else:
            continue
        if relaxed is None or mismatch(relaxed, fact, source) is None:
            candidates.append((reason, group[fact.source], fact.path))
    comparison = [c for c in candidates if c[0]=='COMPARISON']
    if comparison:
        candidates = [c for c in comparison if (c[1], c[2][:-1]) in contradicted_rows]
    if len(candidates)==1:
        return candidates[0]
    return 'NOT_CONFIRMED', None, None


def _correction_span(sentence, claim, reason):
    """Locate the disputed attribute only when it is explicit in this claim."""
    if reason == 'VALUE':
        return claim.quantity_span
    if not claim.span:
        return None
    start, end = claim.span
    if reason == 'COMPARISON':
        pattern = COMPARISON
    elif reason == 'PERIOD':
        pattern = DATE
    elif reason == 'DIRECTION':
        pattern = DOWN if claim.metric.direction == 'DOWN' else UP
    else:
        return None
    matches = list(pattern.finditer(sentence, start, end))
    if len(matches) != 1:
        return None
    if reason == 'COMPARISON' and claim.quantity_span:
        # Keep the value and its predicate together so either comparison can be restated.
        left = matches[0].start()
        if left <= claim.quantity_span[0]:
            return left, change_phrase_end(sentence, claim.quantity_span[1], end)
    return matches[0].span()


def judge_draft(state,known_names=(),*,snapshots=None,countries=(),product_labels=None):
    """Judge the saved draft and supplied evidence without mutating the turn."""
    feedback = []
    confirmed_claims = []
    snapshots=snapshots or {}
    # A saved SQL task carries the resolved query window even when its compact digest omits it.
    query_periods = {}
    ranking_products = {}
    for task in state.plan.tasks:
        conditions = getattr(task, 'conditions', None)
        if conditions and conditions.period:
            for step in task.steps:
                if step.step_id not in snapshots and step.tool == 'query_trade_data':
                    query_periods[step.step_id] = conditions.period
        if conditions and task.intent == 'RANKING' and conditions.ranking_group == 'COUNTRY' and len(conditions.hs_items) == 1:
            ranking_products.update({step.step_id: conditions.hs_items[0].hs_code for step in task.steps
                                     if step.tool == 'query_trade_data'})
    def source(step_id):
        digest=state.results[step_id].digest or {}
        scope = {'hs_code': ranking_products[step_id]} if step_id in ranking_products else {}
        # 순위 행에는 국가만 있다. 조회한 단일 품목의 표시명도 근거 범위에 넣어 "화장품 수출 상위 국가"를 대조한다.
        if scope and (label := (product_labels or {}).get(scope['hs_code'])):
            scope['hs_name'] = label
        if step_id not in snapshots:
            if not digest:
                return digest
            if digest and 'period' not in digest and step_id in query_periods:
                scope['period'] = query_periods[step_id].model_dump(mode='json')
            return {**scope, **digest}
        if snapshots[step_id].get('_source_unavailable'): return snapshots[step_id]
        # 저장 행에는 없는 결과 전체 합계(순위 total_usd·비중 분모)는 같은 결과로 만든 digest 값을 근거로 더한다
        return {**scope, **snapshots[step_id],**{k:digest[k] for k in ('total_usd','denominator_usd')
                                     if k in digest and k not in snapshots[step_id]}}
    by_task = {t.task_id: tuple(s.step_id for s in t.steps if s.step_id in state.results
                                and (s.step_id in snapshots or state.results[s.step_id].digest))
               for t in state.plan.tasks}
    all_steps = tuple(step for group in by_task.values() for step in group)
    checks=[]
    text=' '.join([*(x for c in state.draft.conclusion for x in c.sentences),*state.draft.explanation,state.draft.turn_summary])
    known_names=frozenset(n for n in known_names if n and n in text)
    countries = frozenset(countries)
    # Numeric facts are extracted exactly once per step. Only grounded names
    # join the request-wide dictionary, as in the original identity pass.
    mention_cache = {}
    steps = {step: _prepare([source(step)], known_names, countries, mention_cache=mention_cache)
             for step in dict.fromkeys(all_steps)}
    names = known_names.union(n for item in steps.values() for fact in item.facts for n in fact.targets if len(n)>1)
    scopes = {}

    def prepare_scope(group):
        if group in scopes:
            return scopes[group]
        if len(group) == 1 and names <= steps[group[0]].catalog.names:
            prepared = steps[group[0]]
        else:
            sources = [steps[step].sources[0] for step in group]
            catalog = NameCatalog(sources, names, countries, mention_cache=mention_cache)
            facts = []
            for index, step in enumerate(group):
                for fact in steps[step].facts:
                    # A different scope can change alias ownership and group
                    # membership. Preserve the cell, rebind its raw identifiers.
                    targets, members = bind_targets(fact.stored_targets, catalog)
                    facts.append(replace(fact, source=index, targets=targets, group_members=members))
            prepared = PreparedEvidence(sources, catalog, tuple(facts))
        scopes[group] = prepared
        return prepared

    def review_reason(claim, prepared, group, section, sentence):
        if section == 'summary':
            for task_group in by_task.values():
                if task_group == group:
                    continue
                other = prepare_scope(task_group)
                if _matching_cells(claim, other.facts, other.sources, other.catalog):
                    return 'OUTSIDE_SUMMARY_SCOPE'
            if state.plan.tasks[0].intent == 'CONVERSATION' and claim.quantity:
                if check_history(sentence, state.input.context.recent)[0]:
                    return 'HISTORY_ONLY'
        if claim.metric.family == 'AMOUNT_CHANGE' and not any(
                f.metric.family == 'AMOUNT_CHANGE' and claim.targets <= f.targets for f in prepared.facts):
            return 'NO_DIRECT_CHANGE_CELL'
        return 'NOT_CONFIRMED'

    index=0
    def inspect(sentence,group,section,task_id=None):
        prepared = prepare_scope(group)
        digests = prepared.sources
        good,claims=_inspect_prepared(sentence,prepared)
        numbers=[c.claim.quantity.shown for c in claims if c.claim.quantity]
        if not good and not claims:
            numbers=[q.shown for q in quantities(quantity_text(sentence,prepared.catalog))]
        source=any(d.get('_verification_source')=='SNAPSHOT' for d in digests)
        unavailable=any(d.get('_source_unavailable') for d in digests)
        location = dict(section=section, task_id=task_id, index=None if section=='summary' else index)
        if not good and any(not check.interpretation_valid for check in claims):
            feedback.append(ClaimFeedback(**location, passage=sentence,
                reason='SOURCE_UNAVAILABLE' if unavailable else 'NOT_CONFIRMED'))
        elif not good:
            confirmed_spans = [c.claim.span for c in claims if c.matches and c.claim.span]
            for check in claims:
                claim = check.claim
                span = claim.span
                if check.matches:
                    # A representative supporting cell is sufficient; keep full matches in inspect_sentence.
                    source_index, cell = check.matches[0]
                    confirmed_claims.append(ClaimFeedback(**location, action='PRESERVE', reason='MATCH',
                        passage=sentence[slice(*span)] if span else claim.text, span=span,
                        step_id=group[source_index], cell=cell, targets=tuple(sorted(claim.targets))))
                    continue
                reason, step, cell = (('SOURCE_UNAVAILABLE', None, None) if unavailable
                                      else diagnose_claim(claim, prepared, group))
                if reason == 'NOT_CONFIRMED':
                    reason = review_reason(claim, prepared, group, section, sentence)
                correction = _correction_span(sentence, claim, reason)
                if correction and any(left < correction[1] and correction[0] < right
                                      for left, right in confirmed_spans):
                    correction = None
                span = correction or span
                feedback.append(ClaimFeedback(**location, passage=sentence[slice(*span)] if span else claim.text,
                    span=span, targets=tuple(sorted(claim.targets)), action='CORRECT' if correction else 'REVIEW', reason=reason, step_id=step, cell=cell))
            if all(c.matches for c in claims):
                feedback.append(ClaimFeedback(**location, passage=sentence,
                    reason='SOURCE_UNAVAILABLE' if unavailable else 'NOT_CONFIRMED'))
        if section=='summary':
            return good
        checks.append(SentenceCheck(index=index,numbers=numbers,status='PASS' if good else 'MISMATCH',
            basis='SNAPSHOT' if source else 'DIGEST',
            reason='MATCH' if good else 'SOURCE_UNAVAILABLE' if unavailable else 'NOT_CONFIRMED'))
    for task,conclusion in zip(state.plan.tasks,state.draft.conclusion,strict=True):
        if task.intent in ('CONVERSATION','HS_LOOKUP') and generated_conclusion(state,task,True):
            for sentence in conclusion.sentences:
                good,numbers=(check_history(sentence,state.input.context.recent) if task.intent=='CONVERSATION'
                              else check_codes(sentence,[source(step) for step in by_task[task.task_id]]))
                if not good:
                    feedback.append(ClaimFeedback(section="conclusion", task_id=task.task_id, index=index, passage=sentence, reason="NOT_CONFIRMED"))
                checks.append(SentenceCheck(index=index,numbers=numbers,status='PASS' if good else 'MISMATCH',
                                            reason='MATCH' if good else 'NOT_CONFIRMED'))
                index+=1
            continue
        generated=generated_conclusion(state,task,bool(by_task[task.task_id]))
        for sentence in conclusion.sentences:
            if generated: inspect(sentence,by_task[task.task_id],'conclusion',task.task_id)
            index+=1
    for sentence in state.draft.explanation:
        inspect(sentence,all_steps,'explanation'); index+=1
    primary=state.plan.tasks[0]
    summary_ok=True
    if state.draft.turn_summary and fixed_conclusion(state,primary) is None:
        if primary.intent=='CONVERSATION':
            # 이전 대화를 되짚는 답변의 요약은 본문과 같은 근거(최근 대화 이력)로 대조한다.
            summary_ok=check_history(state.draft.turn_summary,state.input.context.recent)[0]
        else:
            summary_ok=inspect(state.draft.turn_summary,by_task[primary.task_id],'summary',primary.task_id)
    return Verification(checks=checks,feedback=feedback,confirmed_claims=confirmed_claims,
        summary_status=('PASS' if summary_ok else 'MISMATCH') if state.draft.turn_summary else None,
        failed_summary='' if summary_ok else state.draft.turn_summary,regeneration_count=state.verification.regeneration_count,
        status='PASS' if summary_ok and all(c.status=='PASS' for c in checks) else 'NEEDS_REVIEW')


def verify(state,known_names=(),*,snapshots=None,countries=(),product_labels=None):
    state.verification = judge_draft(state,known_names,snapshots=snapshots,countries=countries,product_labels=product_labels)
    if state.verification.summary_status == 'MISMATCH':
        state.draft.turn_summary = ''
