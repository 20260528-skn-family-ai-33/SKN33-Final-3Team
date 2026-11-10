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
    ARTICLE_CITATION, NameCatalog, Claim, TimeClaim, parse_sentence, quantities, mask_dates, quantity_text,
    TOKEN, DATE, _shown, _merge_compound, _target_count, _name_pattern, months_between, shift,
)
from backend.app.agents.verify_evidence import Fact, bind_targets, build_facts, period


@dataclass(frozen=True)
class ClaimCheck:
    claim: Claim
    reason: str
    matches: tuple[tuple[int,tuple[str,...]],...] = ()


def _source_supports(time: TimeClaim,source):
    windows=[p for k in ('period','compare_period','article_period') if (p:=period(source.get(k)))]
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
                                                period(source.get('compare_period')))


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


def _matching_cells(claim, facts, sources, catalog):
    matches = []
    inferred_axes = set()
    check_dimensions = claim.quantity is None or (claim.targets and claim.metric.family == 'CHANGE')
    for fact in facts:
        if check_dimensions:
            # A country/product detail row alone cannot establish the trend of
            # the whole country or of the product across all countries.
            if (bool(fact.stored_targets - catalog.countries) != bool(claim.targets - catalog.countries)
                    or bool(fact.stored_targets & catalog.countries) != bool(claim.targets & catalog.countries)):
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
        checks.append(ClaimCheck(claim,'MATCH' if matches else 'NOT_CONFIRMED',matches))
    dates_ok=all(any(_source_supports(time,source) for source in sources) for time in parsed.date_only)
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
    return [(f.metric.key,f.value,f.unit,set(f.targets)) for f in build_facts([digest],NameCatalog([digest]))]


def snapshot_evidence(payload):
    """Full saved rows, never paginated or bounded for LLM input. No new SQL."""
    conditions=payload.get('conditions') or {}
    meta=payload.get('meta') or {}
    scope={k:conditions[k] for k in ('period','compare_period','trade_flow') if k in conditions}
    if meta.get('kind')=='PREDICTION' and meta.get('anchor'):
        anchor=str(meta['anchor'])[:7]
        scope.update(kind='PREDICTION',anchor=anchor,period={'start':anchor,'end':anchor})
    return {**scope,
            'columns':payload.get('columns',[]),'rows':payload.get('rows',[]),
            '_verification_source':'SNAPSHOT'}


def _tokens(text):
    text=_merge_compound(DATE.sub(' ',ARTICLE_CITATION.sub('',text)))
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
    for m in DATE.finditer(ARTICLE_CITATION.sub('',text)):
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
        return any(v==value and s==scale and (not unit or u==unit) for v,s,u in facts)
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
    candidates = []
    for fact in prepared.facts:
        if claim.targets != fact.targets or claim.metric.family != fact.metric.family:
            continue
        source = prepared.sources[fact.source]
        reason = mismatch(claim, fact, source)
        if reason == 'COMPARISON':
            relaxed = replace(claim, metric=replace(claim.metric, comparison=None))
        elif reason == 'DIRECTION' and fact.value != 0:
            relaxed = replace(claim, metric=replace(claim.metric, direction='UP' if fact.value>0 else 'DOWN'))
        elif reason in ('PERIOD', 'SOURCE_PERIOD'):
            reason = 'PERIOD'
            relaxed = replace(claim, time=TimeClaim())
        elif reason == 'VALUE':
            relaxed = None
        else:
            continue
        if relaxed is None or mismatch(relaxed, fact, source) is None:
            candidates.append((reason, group[fact.source], fact.path))
    comparison = [c for c in candidates if c[0]=='COMPARISON']
    candidates = comparison or candidates
    if len(candidates)==1:
        return candidates[0]
    return 'NOT_CONFIRMED', None, None


def judge_draft(state,known_names=(),*,snapshots=None,countries=()):
    """Judge the saved draft and supplied evidence without mutating the turn."""
    feedback = []
    snapshots=snapshots or {}
    def source(step_id):
        digest=state.results[step_id].digest or {}
        if step_id not in snapshots: return digest
        if snapshots[step_id].get('_source_unavailable'): return snapshots[step_id]
        # 저장 행에는 없는 결과 전체 합계(순위 total_usd·비중 분모)는 같은 결과로 만든 digest 값을 근거로 더한다
        return {**snapshots[step_id],**{k:digest[k] for k in ('total_usd','denominator_usd')
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
        if not good:
            failures = [c.claim for c in claims if not c.matches]
            for claim in failures:
                reason, step, cell = diagnose_claim(claim, prepared, group) if not unavailable else ('SOURCE_UNAVAILABLE', None, None)
                passage = claim.text.strip()
                if passage not in sentence:
                    # Masking a citation preserves offsets but inserts spaces into the parsed clause.
                    citation_free = passage.rstrip(' .!?')
                    if citation_free and citation_free in sentence:
                        passage = citation_free
                    elif claim.quantity and claim.quantity.shown in sentence:
                        passage = claim.quantity.shown
                if claim.quantity and claim.quantity.shown in sentence and claim.quantity.shown not in passage:
                    passage = claim.quantity.shown
                feedback.append(ClaimFeedback(**location, passage=passage, reason=reason, step_id=step, cell=cell))
            if not failures:
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
        summary_ok=inspect(state.draft.turn_summary,by_task[primary.task_id],'summary',primary.task_id)
    return Verification(checks=checks,feedback=feedback,
        summary_status=('PASS' if summary_ok else 'MISMATCH') if state.draft.turn_summary else None,
        failed_summary='' if summary_ok else state.draft.turn_summary,regeneration_count=state.verification.regeneration_count,
        status='PASS' if summary_ok and all(c.status=='PASS' for c in checks) else 'NEEDS_REVIEW')


def verify(state,known_names=(),*,snapshots=None,countries=()):
    state.verification = judge_draft(state,known_names,snapshots=snapshots,countries=countries)
    if state.verification.summary_status == 'MISMATCH':
        state.draft.turn_summary = ''
