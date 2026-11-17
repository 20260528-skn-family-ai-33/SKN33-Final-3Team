"""Server-owned condition resolution and replayable clarifications (§14.1)."""
from datetime import date, timedelta, timezone
from difflib import SequenceMatcher
import re
from uuid import uuid4
from sqlalchemy import select, func

from backend.app.agents.planner import (HS_CODE_MENTION, HS_EDITION_MENTION, ambiguous_report_source,
                                        coded_product, comparison_date_text, product_expressions)
from backend.app.agents.state import (Choice, Conditions, DataMeta, Pending, Period, TaskSlots,
                                     MAX_ASKS_PER_CONDITION, MAX_TEXT_CHARS, MARKET_INTENTS, ToolResult)
from backend.app.api import accounts, reference, trade
from backend.app.api.chat_slots import country_key, numeric_product, SlotRepair
from backend.app.api.common import ApiError, month_end, now, shift_month


def recent_article_period(received):
    end = received.replace(tzinfo=received.tzinfo or timezone.utc).astimezone(timezone(timedelta(hours=9))).date()
    month = shift_month(end, -3)
    start = month.replace(day=min(end.day, month_end(month).day)) + timedelta(days=1)
    return {'start': start, 'end': end}


def _changed_hangul_syllable(key, label):
    if len(key) != len(label) or not re.fullmatch('[가-힣]{2,}', key) or not re.fullmatch('[가-힣]{2,}', label):
        return None
    changed = [(ord(a) - ord('가'), ord(b) - ord('가')) for a, b in zip(key, label) if a != b]
    return changed[0] if len(changed) == 1 else None


def _final_consonant_differs(key, label, *, missing_only=False):
    """한글 음절 하나의 받침 차이만 확인한다."""
    if not (changed := _changed_hangul_syllable(key, label)):
        return False
    left, right = changed
    return left // 28 == right // 28 and (not missing_only or left % 28 == 0)


def country_matches(name, rows, threshold):
    key = country_key(name)
    matches = []
    for row in rows:
        if row['country_role'] != 'COUNTRY': continue
        label = country_key(row['name_ko'])
        exact = key in (label, country_key(row['country_code']))
        similarity = SequenceMatcher(None, key, label).ratio()
        contains = key in label or label in key
        close = _final_consonant_differs(key, label)
        if exact or close or contains or similarity >= threshold:
            matches.append((not exact, not close, -similarity, row['country_code'], row))
    matches.sort(key=lambda m: m[:4])
    if matches and not matches[0][0]:
        return matches[0][3], []
    return None, [m[4] for m in matches[:5]]


def _match_country_mention(mention, name, rows, threshold):
    """원문과 사전으로 확정하고, 모델의 정식 명칭은 별칭·후보 확인에 쓴다."""
    raw_code, candidates = country_matches(mention, rows, threshold)
    two_letter_code = mention.isascii() and len(mention) == 2
    if raw_code and not two_letter_code:
        return raw_code, [], None
    raw = country_key(mention)
    named_code, named_candidates = ((raw_code, candidates) if raw == country_key(name)
                                    else country_matches(name, rows, threshold))
    if two_letter_code:
        if not named_code:  # TV 등 국가명이 아닌 약어
            return None, None, None
        if raw_code == named_code:
            return raw_code, [], None
        codes = dict.fromkeys(c for c in (named_code, raw_code) if c)
        return None, [r for code in codes for r in rows if r['country_code'] == code], None
    if not candidates:
        if named_code:
            named = next(r for r in rows if r['country_code'] == named_code)
            if _changed_hangul_syllable(raw, country_key(named['name_ko'])):
                return None, [named], None  # 임계값 미달인 오타를 별칭으로 확정하지 않는다.
        return named_code, named_candidates, None

    close = [r for r in candidates if _final_consonant_differs(raw, country_key(r['name_ko']))]
    if len(candidates) == 1 and close:
        row = close[0]
        code, label = row['country_code'], row['name_ko']
        if named_code in (None, code) and _final_consonant_differs(raw, country_key(label), missing_only=True):
            return code, [], label
    aliases = [r for r in candidates if raw in country_key(r['name_ko'])]
    if not close and len(aliases) == 1 and aliases[0]['country_code'] == named_code:
        return named_code, [], None  # 러시아 → 러시아 연방처럼 사전에서 확인된 별칭
    if named_code and all(r['country_code'] != named_code for r in candidates):
        candidates = [*candidates[:4], next(r for r in rows if r['country_code'] == named_code)]
    return None, candidates, None


TRADE_PARTNER_ALIASES = {'대미': 'US'}


def _question_country_matches(question, countries, rows, threshold):
    """Bind a planner country to its source spelling and catalog entry."""
    text = question.casefold()
    matches = {(item.mention, item.name): _match_country_mention(item.mention, item.name, rows, threshold)
               for item in countries if item.mention not in TRADE_PARTNER_ALIASES and item.mention.casefold() in text}
    for alias, code in TRADE_PARTNER_ALIASES.items():
        if not re.search(r'(?<!\w)' + re.escape(alias) + r'(?=\W|$|수출|수입)', question):
            continue
        row = next((r for r in rows if r['country_code'] == code), None)
        if row is None:
            continue
        canonical = {country_key(row['name_ko']), country_key(code)}
        for item in countries:
            if (item.mention, item.name) in matches:
                continue
            if item.mention != alias and not {country_key(item.mention), country_key(item.name)} <= canonical:
                continue
            # Preserve the source spelling on the task for later snapshot verification.
            item.mention, item.name = alias, row['name_ko']
            matches[alias, item.name] = (code, [], None)
    return matches


def comparison_windows(question, current, baseline):
    from backend.app.agents.verify_claims import annual_comparison_roles

    if current and baseline and all(w.start.month == 1 and w.end.month == 12 and w.start.year == w.end.year
                                    for w in (current, baseline)):
        roles = annual_comparison_roles(question)
        if roles.kind != 'undetermined' and (roles.subject, roles.baseline) == (str(baseline.start.year), str(current.start.year)):
            return baseline, current
    return current, baseline


def _bounded_bundle(store, loaded, message):
    """조건 확인에서 쓰는 모델 묶음(HS4 후보 순서·예측 기준월). ML 단계와 같은 전용 스레드·시간 제한으로 읽는다.
    캐시가 비어 첫 로드가 늦으면 워커를 붙잡지 않고 그 하위 질문만 다시 질문하도록 안내한다."""
    from backend.app.agents import budget
    from backend.app.agents.runtime import bounded_ml
    settings = store.db.settings
    seconds = budget.seconds(settings, 'check')
    try:
        # 줄 대기도 check 예산으로 묶는다. 재시작 뒤 예열(모델 로드 약 25초) 중이면 오래 기다리지 않고 '불러오는 중'으로 안내한다
        # (ML 단계의 줄 대기 상한 API_ML_QUEUE_SECONDS는 예측을 기다리는 몫이라 여기에는 쓰지 않는다)
        bundle, _, _ = bounded_ml(lambda: loaded(settings), seconds, queue_seconds=seconds)
    except TimeoutError:
        raise SlotRepair('예측 모델을 불러오는 중입니다. 잠시 후 다시 질문해 주세요.')
    except (ImportError, OSError, ValueError):
        raise SlotRepair(message)
    return bundle


def ask(state, task, key, kind, message, options, **extra):
    count = state.ask_counts.get(key, 0)
    if kind == 'COUNTRY' and any(':country:' in k and n for k,n in state.ask_counts.items()):
        raise SlotRepair('국가를 확정하지 못했습니다. 전체 국가명 또는 국가 코드를 포함해 질문해 주세요.')
    if count >= MAX_ASKS_PER_CONDITION:
        raise SlotRepair('조건을 확인해 다시 질문해 주세요.')
    state.pending = Pending(clarification_id=uuid4().hex, condition_key=key, task_id=task.task_id,
        kind=kind, message=message, asked_at=now(), choices=[Choice(choice_id=code, label=label,
        patch=patch) for code, label, patch in options], **extra)
    state.ask_counts[key] = count + 1
    state.status = 'NEEDS_INPUT'


def check_conditions(store, state, step, *, resolve_bindings=None, confirm_report=None):
    task = next(task for task in state.plan.tasks if step in task.steps)
    try:
        _resolve(store, state, task, step, resolve_bindings, confirm_report)
    except (SlotRepair, ApiError) as exc:
        step.status = 'FAILED'
        message = exc.message if isinstance(exc, ApiError) else str(exc)
        state.results[step.step_id] = ToolResult(status='NO_DATA', error=message)
        state.skip_blocked()


# 짧은 후속 질문에서 품목 자리에 오지만 품목이 아닌 기간·지표·지시어("최근 수출 추이", "수출액은?", "상반기는?").
_NOT_PRODUCT_FOLLOWUP = re.compile(
    r'(?:수출|수입|수출입)(?:액|량|률|율)?|금액|총액|합계|규모|비중|점유율|구성비|순위|추이|흐름|증감(?:률|액)?|증가(?:율|액)?|감소(?:율|액)?'
    r'|전망|예측|원인|이유|배경|기사|뉴스|차트|그래프|표|결과|리포트|보고서|요약|설명|상세|자세히'
    r'|상반기|하반기|[1-4일이삼사]?분기|연간|월간|누적|최근|최신|올해|작년|지난해|전년|내년|금년|이번\s*달|지난\s*달|전월|당월'
    r'|이전|다음|그거|그것|이거|이것|저거|전체|국가|품목|상위|하위|[가-힣]{1,4}별',
)


# 직전 결과표 리포트에서 질문으로 바꿀 수 없는 조건(표를 만든 조건을 따른다)
PREVIOUS_TABLE_FIXED = {'countries', 'products', 'period', 'recent_months', 'comparison_requested', 'compare_period',
                        'ranking_group', 'share_basis', 'ranking_limit'}   # '상위 1개만'도 표를 바꾸는 요청이다(행을 줄이지 않는다)
PREVIOUS_TABLE_NOTE = '직전 결과표의 조건 그대로 만들었습니다. 조건을 바꾸려면 새로 조회해 주세요.'


def _product_key(expression):
    match = HS_CODE_MENTION.fullmatch(expression.strip())
    return match[1] if match else country_key(expression)


ARTICLE_INTENTS = frozenset(('CAUSE', 'LOCAL_ISSUES'))   # 금액을 조회하지 않고 기사 근거만 쓰는 하위 질문
SAME_COMPARISON_CONDITIONS = re.compile(r'(?:같은|동일한)\s*(?:비교\s*)?조건|비교\s*조건(?:은|도)?\s*그대로')


def _comparison_named(text, period):
    """비교 기간의 연도·월이나 상대 기간을 질문에서 직접 지정했는지 확인한다."""
    # "HS2022 품목"의 2022는 판본이지 비교 연도가 아니다. 위치를 유지하도록 같은 길이의 공백으로 가린다.
    text = HS_EDITION_MENTION.sub(lambda m: ' ' * len(m[0]), text)
    inherited = SAME_COMPARISON_CONDITIONS.search(text)
    year = period.start.year
    def is_target(match):
        # 월 패턴이 다른 해의 "YYYY년 N월"에서 N월만 다시 잡는 것을 막는다.
        preceding_year = re.search(r'(?<!\d)(\d{4}|\d{2})\s*년\s*$', text[:match.start()])
        if preceding_year and int(preceding_year[1]) not in (year, year % 100):
            return False
        if not inherited:
            return True
        return bool(re.match(r'\s*(?:의\s*)?(?:수출입?액|수입액|교역액|금액|실적)?\s*'
                             r'(?:(?:하고|와|과|랑|을|를|에)?\s*(?:만(?:을|를)?\s*)?(?:비교|대비|보다|비해)'
                             r'|(?:와|과)\s*의\s*차이'
                             r'|(?:을|를)?\s*기준으로(?=[^.!?。]*비교))', text[match.end():])
                    # "비교 기간은 2023년", "비교는 2023년만"처럼 날짜 앞에서 비교 대상을 소개해도 그 기간을 지정한 것이다.
                    or re.search(r'비교\s*(?:기간|대상|연도|기준)?\s*(?:은|는|을|를)?\s*$', text[:match.start()]))
    # "동기"는 낱말 첫머리이거나 "전년동기"처럼 '년' 뒤일 때만 기간이다. "전동기"·"원동기"·"발동기"는 품목명이다.
    relative = r'작년|재작년|지난\s*(?:해|달|분기|반기)|전\s*(?:년|월|분기|반기)|(?:(?<![가-힣])|(?<=년))동기'
    if any(map(is_target, re.finditer(relative, text))):
        return True
    if (period.start.month, period.end.month) == (1, 12):
        from backend.app.agents.verify_claims import annual_comparison_roles
        roles = annual_comparison_roles(text)
        if roles and roles.kind=='explicit' and roles.baseline==str(year):
            return True
        explicit_year = rf'(?<!\d)(?:{year}(?:\s*년)?|{year % 100:02d}\s*년)(?!\d|\s*년|\s*\d{{1,2}}\s*월)'
        if any(map(is_target, re.finditer(explicit_year, text))):
            return True
    for match in re.finditer(r'(?<!\d)(?:(\d{4}|\d{2})\s*년\s*)?(?:([1-4])\s*분기|([상하12])\s*반기)', text):
        if not is_target(match) or (match[1] and int(match[1]) not in (year, year % 100)):
            continue
        first = 3 * int(match[2]) - 2 if match[2] else 1 if match[3] in ('상', '1') else 7
        last = first + (2 if match[2] else 5)
        if period.end.year == year and (period.start.month, period.end.month) == (first, last):
            return True
    def month_tokens(point):
        y, m = point.year, point.month
        years = rf'(?:{y}|{y % 100:02d})'
        digits = rf'(?:{m:02d}|{m})(?!\d)'
        day = r'(?:\s*\d{1,2}\s*일)?'
        spelled = rf'(?:{years}\s*년\s*)?{digits}\s*월(?!간){day}'
        numeric = rf'{years}[./-]\s*(?:{m:02d}(?!\d)(?:[./-]\d{{1,2}}(?!\d))?|{digits}\s*월{day})'
        return spelled, numeric
    start_spelled, start_numeric = month_tokens(period.start)
    end_spelled, end_numeric = month_tokens(period.end)
    # "1~3월"은 첫 월의 단위를 생략할 수 있다.
    abbreviated = rf'(?:(?:{year}|{year % 100:02d})\s*년\s*)?(?:{period.start.month:02d}|{period.start.month})(?!\d)'
    joined = rf'(?:{start_spelled}|{start_numeric}|{abbreviated})\s*(?:[~～–-]|부터|에서)\s*(?:{end_spelled}|{end_numeric})(?:\s*(?:까지|사이))?'
    pattern = rf'(?<![\d.]){joined}'
    if period.start == period.end:
        pattern = rf'(?<![\d.])(?:{joined}|{start_spelled}|{start_numeric})'
    return any(map(is_target, re.finditer(pattern, comparison_date_text(text))))


def _code_editions(text, code):
    """문장에서 그 코드에 붙여 적은 판본: "HS2017 330488", "HS2017 기준으로 HS 330488", "330488(HS2017)"."""
    prefix = re.findall(rf'(?<![A-Za-z\d])HS\s*(2017|2022)\s*(?:(?:기준|판본|버전)\s*)?(?:(?:으로|의|에서)\s*)?'
                        rf'(?:HS\s*(?:[46](?!\d)\s*)?)?(?:코드\s*)?{code}(?![\d.,])', text, re.I)
    postfix = re.findall(rf'(?<![\d.,]){code}\s*(?:\(\s*)?HS\s*(2017|2022)(?!\d)', text, re.I)
    return set(postfix or prefix)


def _article_hs_items(store, state, task, items):
    hs = store.t('hs_code')
    try:
        mapping = store.t('hs_code_correlation')
    except ApiError as exc:
        if exc.code != 'FEATURE_NOT_READY':
            raise
        mapping = None
    converted = []
    for item in items:
        if item['hs_ver'] == 'HS2022':
            converted.append(item)
            continue
        code = item['hs_code']
        key = f'{task.task_id}:article_hs:{code}'
        if answer := task.resolved.get(key, {}).get('hs_items'):
            converted.extend(answer)
            continue
        # 대응표는 HS6 단위다. HS4는 그 아래 HS6들의 대응을 모아 HS2022 HS4로 줄인다. 하위 품목이 여러 HS4(3303·3304)로
        # 갈라지면 하나로 확정하지 않고 묻는다.
        codes = {row['new_code'][:len(code)] for row in store.rows(select(mapping.c.new_code).where(
            mapping.c.old_ver=='HS2017', mapping.c.new_ver=='HS2022',
            mapping.c.old_code.startswith(code) if len(code) == 4 else mapping.c.old_code == code))
                 } if mapping is not None else set()
        codes = codes or {code}
        rows = store.rows(select(hs.c.hs_code, hs.c.hs_ver, hs.c.name_ko).where(
            hs.c.hs_ver=='HS2022', hs.c.hs_code.in_(codes)).order_by(hs.c.hs_code))
        choices = [(row['hs_code'], row['name_ko'], {'hs_items': [
            {k: row[k] for k in ('hs_code', 'hs_ver')}]}) for row in rows]
        if len(codes)==len(choices)==1:
            converted.extend(choices[0][2]['hs_items'])
            continue
        ask(state, task, key, 'HS_ITEM', f'HS2017 {code}에 대응하는 HS2022 품목을 선택하거나 코드를 입력하세요.',
            choices[:5])
        return None
    return converted


def _uses_item_edition(task):
    """명시한 HS 판본(HS2017 포함)으로 조회하는 하위 질문인지: 추이(TREND)와 단순 금액 순위."""
    sql = next((s for s in task.steps if s.tool == 'query_trade_data'), None)
    template = sql.args.get('template') if sql else None
    return (task.intent == 'STATISTICS' and template == 'TREND'
            or task.intent == 'RANKING' and template == 'RANKING' and bool(sql.args.get('amount_only')))


def _resolve(store, state, task, step, resolve_bindings, confirm_report):
    if step.args.get('article_retry'):
        request=state.input.context.article_retry
        if request is None or task.intent!='LOCAL_ISSUES':
            raise SlotRepair('다시 검색할 기사 요청을 확인할 수 없습니다.')
        task.conditions=(Conditions.model_validate({**request.conditions.model_dump(),
                         'article_period':recent_article_period(state.input.received_at)})
                         if request.default_period else request.conditions.model_copy(deep=True))
        accounts.validate_scope(store,[h.model_dump() for h in task.conditions.hs_items],task.conditions.country_codes)
        step.status='DONE'
        return
    source_key = f'{task.task_id}:report_source'
    if (task.intent == 'REPORT' and not task.input_bindings and not task.previous_turn_result
            and state.input.context.conditions and ambiguous_report_source(state.input.question)):
        source = task.resolved.get(source_key, {}).get('report_source')
        if source is None:
            return ask(state, task, source_key, 'REPORT_SOURCE',
                '직전 결과표로 리포트를 만들까요, 아니면 같은 조건으로 다시 조회할까요?',
                [('PREVIOUS_RESULT', '직전 결과표로 리포트', {'report_source': 'PREVIOUS_RESULT'}),
                 ('REQUERY', '같은 조건으로 다시 조회', {'report_source': 'REQUERY'})])
        task.previous_turn_result = source == 'PREVIOUS_RESULT'
    slots = task.slots
    if task.previous_turn_result:
        # 직전 결과표는 그 조건 그대로 싣는다. 질문에 기간·국가 등이 함께 와도 표 조건을 바꾸지 않는다
        # (바꾸면 사본 표와 리포트 조건이 어긋난다). 바꾸려면 새로 조회하도록 적용 조건에 알린다.
        asked = slots.model_dump(include=PREVIOUS_TABLE_FIXED, exclude_none=True)
        slots = TaskSlots(trade_flow=slots.trade_flow) if slots.trade_flow == 'IMPORT' else TaskSlots()
        if asked and PREVIOUS_TABLE_NOTE not in task.applied_defaults:
            task.applied_defaults.append(PREVIOUS_TABLE_NOTE)
    # 이미 품목을 선택한 뒤 재개하는 체크포인트에도 같은 기업명 제외 규칙을 적용한다.
    slots.products = product_expressions(slots.products,article_context=task.intent in ('CAUSE','LOCAL_ISSUES'))
    report_answer=task.resolved.get(f'{task.task_id}:report_confirm',{})
    if report_answer.get('reject_input_change'): raise SlotRepair('선행 결과의 조건을 변경하려면 새 분석 질문을 입력해 주세요.')
    original=state.input.question+' '+report_answer.get('revision_text','')
    uses_item_edition = _uses_item_edition(task)
    # 결과 사본으로 만드는 리포트(같은 턴 선행 결과·직전 결과표)는 원본 판본을 그대로 쓴다.
    frozen_report = task.intent == 'REPORT' and bool(task.input_bindings or task.previous_turn_result)
    # 판본은 원문에 있는 값만 확정한다. 복합 질문의 판본은 해당 하위 질문에 적용한다.
    # 결과 사본 리포트는 리포트 문장에 적은 판본만 본다. 생략하면 연결한 원본의 판본을 쓰고("HS2017 추이와 HS2022 추이 … 첫 결과로
    # 리포트"), 원본과 다른 판본을 적으면 아래에서 거절한다.
    editions = set(HS_EDITION_MENTION.findall(task.question if frozen_report and len(state.plan.tasks) > 1 else original))
    article_edition_requested = False
    if editions and task.intent in ARTICLE_INTENTS:
        # 기사로 설명하는 질문(원인·현지 이슈)은 적은 판본 때문에 실패시키지 않고 HS2022 품목으로 조회한다. HS2017 코드는
        # 대응표로 바꾼다(기사 태그도 발행일 기준 HS2022, 2026-11-15 사용자 결정). 단일 질문도 복합 질문의 하위 질문과 같다.
        # 비중·유망시장처럼 금액만 조회하는 질문은 아래 규칙대로 판본을 지키고, 지원하지 않으면 안내한다.
        article_edition_requested = '2017' in editions
        asked_editions, editions = set(editions), set()
    elif len(state.plan.tasks) > 1 and editions:
        if len(editions) > 1 or any(HS_EDITION_MENTION.search(t.question) for t in state.plan.tasks
                                      if t.intent not in ARTICLE_INTENTS):
            # 플래너가 판본을 하위 질문에 나눠 적었으면 그 배치를 따른다. 원문 판본이 하나인데 어느 하위 질문에도
            # 없으면 플래너가 빠뜨린 것이라 원문 판본을 쓴다. 판본이 여럿인데 이 문장에 없으면 HS2022로 추측하지 않는다.
            multiple = len(editions) > 1
            editions.intersection_update(HS_EDITION_MENTION.findall(task.question))
            if multiple and not editions:
                raise SlotRepair('하위 질문의 HS 판본을 확인하지 못했습니다. 품목마다 판본을 함께 지정해 주세요.')
    if '2017' in editions and not (uses_item_edition or frozen_report):
        raise SlotRepair('HS2017은 채팅의 금액 순위와 추이에서 지원합니다.')
    if len(editions) > 1:
        raise SlotRepair('판본별 품목을 각각의 조회로 요청해 주세요.')
    hs_ver = 'HS2017' if '2017' in editions else 'HS2022'
    if (len(state.plan.tasks) == 1 and not task.resolved
            and task.intent in ('STATISTICS', 'RANKING', 'MARKET_SHARE', 'CAUSE', 'LOCAL_ISSUES', 'REPORT')
            and not task.input_bindings and not task.previous_turn_result):
        codes = list(dict.fromkeys(HS_CODE_MENTION.findall(original)))
        grounded = [p for p in slots.products or [] if _product_key(p) in country_key(original)]
        if codes and not any(not any(code in p for code in codes) for p in grounded):
            slots.products = codes
    # Customs tables already describe Korea's trade. Korea is the reporting
    # country, not a partner to resolve against the country dictionary.
    reporting_names={'한국','대한민국','우리나라','kr','kor','south korea','republic of korea'}
    countries=slots.countries
    if countries:
        countries=[item for item in countries if not (
            item.mention.strip().casefold() in reporting_names
            and item.mention.casefold() in original.casefold())]
    base = state.input.year_over_year_base or state.input.context.conditions or Conditions()
    # 짧은 새 품목 질문을 생략 슬롯으로 받았을 때 직전 품목을 재사용하지 않는다.
    if base.hs_items and not countries and task.intent in ('STATISTICS', 'RANKING', 'LOCAL_ISSUES', 'CAUSE'):
        switch = re.fullmatch(
            r'\s*(?:그럼\s*|그러면\s*)?([가-힣A-Za-z]{2,30})(?:은|는|도)\s*(?:어때|어떤가요)?[?？]?\s*'
            r'|\s*([가-힣A-Za-z]{2,30})\s+수(?:출입|출|입)(?:액|량|\s*추이)?\s*(?:알려\s*줘|보여\s*줘)?[.!?？]?\s*',
            original.strip())
        if switch:
            expression = switch[1] or switch[2]
            if not _NOT_PRODUCT_FOLLOWUP.fullmatch(expression):
                table = store.t('country')
                rows = store.rows(select(table).where(table.c.country_role=='COUNTRY'))
                reporting = country_key(expression) in {country_key(name) for name in reporting_names}
                settings = getattr(store.db, 'settings', {})
                threshold = float(settings.get('API_CHAT_COUNTRY_SIMILARITY', 0.6))
                if not reporting and country_matches(expression, rows, threshold) == (None, []):
                    slots.products = [expression]
    out = base.model_copy(deep=True)
    out.ranking_group, out.share_basis = slots.ranking_group, slots.share_basis
    out.compare_period, out.article_period, out.extra_compare_periods, out.year_comparison = None, None, [], None
    out.trade_flow = slots.trade_flow or base.trade_flow
    if task.input_bindings or task.previous_turn_result:
        if task.intent=='REPORT' and task.report_request and task.report_request.confirmed:
            out=task.report_request.conditions.model_copy(deep=True)
        elif task.previous_turn_result:
            # 직전 답변의 결과표 그대로 리포트를 만든다(설계 '이전 턴 결과 참조'). 조건은 표에 저장된 분석 조건이며
            # 다시 조회하지 않는다. 표 사본은 미리보기(confirm_report)가 같은 표를 다시 읽어 붙인다.
            from backend.app.agents.bindings import previous_result, stored_conditions
            source = previous_result(store, state, task)
            if source is None: return   # REPORT_TABLE로 어느 표인지 묻는 중
            out = stored_conditions(source[0])
        else:
            if resolve_bindings is None: raise SlotRepair('선행 결과의 대상을 확인할 수 없습니다.')
            if task.intent == 'REPORT':
                # 리포트 원본은 저장 결과의 확정 방향으로 고른다(수출 원본이 있으면 수입 원본은 빼고 알린다).
                from backend.app.agents.bindings import report_export_sources
                report_export_sources(store, state, task)
            out = resolve_bindings(store, state, task, out)
        if state.pending: return
        out.trade_flow=slots.trade_flow or out.trade_flow
    # 이어받는 비교 기간: 같은 턴 선행 결과(out)나 이전 턴 조건(Redis 문맥, base). 아래에서 첫 비교 기간만 다시 적은 후속에 쓴다.
    prior_compare, prior_extras = ((out.compare_period, out.extra_compare_periods)
                                   if task.input_bindings or task.previous_turn_result
                                   else (base.compare_period, base.extra_compare_periods))
    # 기사 분석이 이어받은 HS2017 구코드를 질문에 다시 적으면 그 판본으로 확인한 뒤 대응표 변환(_article_hs_items)을 거친다.
    inherited_2017 = {h.hs_code for h in out.hs_items if h.hs_ver == 'HS2017'}
    if slots.products is not None: out.hs_items = []
    if countries is not None: out.country_codes = []
    if task.intent in ('REPORT', *MARKET_INTENTS) and out.trade_flow == 'IMPORT':
        raise SlotRepair('이 분석은 수출만 지원합니다.')
    share_key = f'{task.task_id}:share_basis'
    out.share_basis = task.resolved.get(share_key, {}).get('share_basis', slots.share_basis)
    if task.intent == 'MARKET_SHARE' and out.share_basis == 'FOREIGN_IMPORT_MARKET':
        raise SlotRepair('해외 수입시장 점유율은 일부 HS6 품목·국가·기간에 한해 제공되며, 채팅에서는 아직 지원하지 않습니다.')
    if task.intent == 'MARKET_SHARE' and not out.share_basis:
        product = slots.ranking_group == 'HS6'
        return ask(state, task, share_key, 'SHARE_BASIS', '어떤 시장 비중을 조회할까요?',
            [(v, label, {'share_basis':v}) for v,label in [
                ('KOREA_TRADE_PARTNER','한국 교역의 품목별 비중' if product else '한국 교역의 국가별 비중'),
                ('FOREIGN_IMPORT_MARKET','해외 수입시장의 한국 비중')]])
    hs_table = store.t('hs_code')
    market_hs4 = []
    original_key = country_key(original)
    new_product = any(_product_key(expression) in original_key for expression in slots.products or [])
    for expression in slots.products or []:
        key = f'{task.task_id}:hs:{expression}'
        answer = task.resolved.get(key, {})
        if not answer:
            # 같은 계획의 다른 하위 질문에서 같은 품목 표현을 이미 확정했으면 다시 묻지 않는다(국가와 같은 규칙, 2026-11-06).
            # 예: "중국 면도기 수출 추이와 변화 원인" → 추이·원인 두 하위 질문이 "면도기"를 한 번만 묻는다
            answers = (t.resolved.get(f'{t.task_id}:hs:{other}', {})
                       for t in state.plan.tasks if t is not task
                       for other in (t.slots.products or []) if _product_key(other) == _product_key(expression))
            answer = next((v for v in answers if v.get('hs_items') and all(h['hs_ver'] == hs_ver for h in v['hs_items'])), {})
        # D-74: 유망시장 모델은 HS6 단위다. 되묻기 답이 HS4("이 HS4 전체로 분석"·4자리 입력)여도 HS6 후보로 다시 묻는다.
        hs4 = [h['hs_code'] for h in answer.get('hs_items', []) if len(h['hs_code']) == 4] \
            if task.intent in MARKET_INTENTS else []
        if 'hs_items' in answer and not hs4:
            out.hs_items.extend(answer['hs_items'])
            continue
        # 같은 답에 함께 고른 HS6는 다시 묻는 동안에도 남긴다(다음 답의 patch에 함께 넣는다)
        kept = [h for h in answer.get('hs_items', []) if len(h['hs_code']) != 4] if hs4 else []
        expr = hs4[0] if hs4 else answer.get('replace_expression', expression)
        if HS_EDITION_MENTION.search(expr):
            expr = re.sub(r'\(\s*\)', '', HS_EDITION_MENTION.sub('', expr)).strip()
        # 이름(HS 코드)도 숫자·HS 사전을 대조한다. 사용자가 이미 고른 답은 위에서 우선 적용한다.
        expr = coded_product(expr, original if not answer else expr)
        # D-22: 플래너가 질문에 없는 이전 조건을 그대로 다시 적으면 생략(이어받기)과 같다.
        # 코드가 같거나, 이전 조건을 만든 직전 질문의 표현이고 그 표현의 검색 결과에 이전 품목이 있을 때만 이어받는다.
        if not answer and base.hs_items and country_key(expr) not in original_key:
            echoed = [h for h in base.hs_items if h.hs_code == expr]
            last = state.input.context.recent[-1].question.casefold() if state.input.context.recent else ''
            if not echoed and not expr.isdigit() and expr.casefold() in last:
                found = {r['hs_code'] for r in reference._search_depth(store, expr, 6, 5)['items']}
                echoed = [h for h in base.hs_items if h.hs_code in found]
            if echoed:
                if not new_product:
                    out.hs_items.extend({'hs_code': h.hs_code, 'hs_ver': h.hs_ver} for h in echoed)
                continue
        if not answer and country_key(expr) not in original_key:
            raise SlotRepair('질문에서 품목 표현을 확인하지 못했습니다.')
        if expr.isdigit():
            named_editions = set()
            if article_edition_requested:
                # 복합 질문은 이 하위 질문 문장에 적은 판본을 먼저 쓰고(다른 하위 요청의 판본과 섞지 않는다), 없으면 원문에서
                # 이 코드에 붙은 판본만 쓴다. 단일 질문은 코드에 붙은 판본, 없으면 질문의 유일한 판본이다.
                if len(state.plan.tasks) > 1:
                    named_editions = (_code_editions(task.question, expr) or set(HS_EDITION_MENTION.findall(task.question))
                                      or _code_editions(original, expr))
                else:
                    named_editions = _code_editions(original, expr) or (asked_editions if len(asked_editions) == 1 else set())
            article_legacy = (task.intent in ARTICLE_INTENTS and not editions
                              and (named_editions=={'2017'} or (not named_editions and expr in inherited_2017)))
            ver = 'HS2017' if article_legacy else hs_ver
            numeric_product(store, original if not answer else expr, expr, set(), hs_ver=ver)
            if task.intent not in MARKET_INTENTS or len(expr) == 6:
                out.hs_items.append({'hs_code': expr, 'hs_ver': ver})
                continue
            market_hs4.append((key, expr, kept))
            continue
        else:
            found = reference._search_depth(store, expr, 6, 5, hs_ver=hs_ver)
            candidates = found['items']
            if found['match_type'] == 'EXACT' and len(candidates) == 1:
                out.hs_items.append({k: candidates[0][k] for k in ('hs_code','hs_ver')})
                continue
        return ask(state, task, key, 'HS_ITEM', f'“{expr}”에 해당하는 품목을 선택하세요.',
            [(r['hs_code'], r['name_ko'], {'hs_items': [*kept, {k:r[k] for k in ('hs_code','hs_ver')}]}) for r in candidates],
            hs_ver='HS2017' if hs_ver=='HS2017' else None)

    interests = state.input.interests
    key = f'{task.task_id}:interest'
    answer = task.resolved.get(key, {})
    if (state.input.first_question and not state.input.year_over_year_base and not out.hs_items and not out.country_codes and slots.countries is None and slots.products is None
            and (interests.hs_items or interests.country_codes) and not answer):
        options = [('GLOBAL','전체 범위',{'interest':'GLOBAL'})]
        if interests.fits_conditions(): options.insert(0, ('APPLY','관심 범위 적용',{'interest':'APPLIED'}))
        return ask(state, task, key, 'INTEREST_CONTEXT' if interests.fits_conditions() else 'INTEREST_SELECT',
                   '이번 질문에 적용할 관심 범위를 선택하세요.', options)
    if answer.get('interest') == 'APPLIED':
        out.hs_items = answer.get('hs_items', interests.hs_items)
        out.country_codes = answer.get('country_codes', interests.country_codes)
    if any(v.get('request_interest_change') for v in task.resolved.values()):
        key=f'{task.task_id}:interest_change'
        change=task.resolved.get(key)
        if not change:
            return ask(state,task,key,'INTEREST_CHANGE','이번 질문에 저장한 관심 품목·국가를 적용할까요?',
                       [('APPLY','적용',{'interest':'APPLIED'}),('DECLINE','거절',{'interest':'DECLINED'})])
        if change.get('interest')=='APPLIED':
            if not interests.fits_conditions() and 'hs_items' not in change:
                return ask(state,task,key,'INTEREST_SELECT','이번 질문에 사용할 관심 대상을 선택하세요.',
                           [('GLOBAL','전체 범위',{'interest':'GLOBAL'})])
            out.hs_items=change.get('hs_items',interests.hs_items)
            out.country_codes=change.get('country_codes',interests.country_codes)

    if countries:
        table = store.t('country')
        rows = store.rows(select(table).where(table.c.country_role=='COUNTRY'))
        # TODO(C-28): tune fuzzy matching with evaluation examples.
        settings = getattr(store.db, 'settings', {})
        threshold = float(settings.get('API_CHAT_COUNTRY_SIMILARITY', 0.6))
        # 질문에 새 국가가 있으면("미국은 왜 증가했어?") 플래너가 함께 적은 이전 국가는 이어받지 않는다.
        # 국가로 확정되는 표현만 센다. 'TV'처럼 국가가 아닌 약어는 뒤에서 빠지므로 이어받기를 막지 않는다.
        # 되묻기로 확정한 국가('미구' → 미국)도 새 국가다. 아래처럼 같은 계획의 앞 하위 질문에서 확정한 답도 쓴다.
        def confirmed(mention):
            return any(t.resolved.get(f'{t.task_id}:country:{mention}', {}).get('country_codes') for t in state.plan.tasks)
        current = _question_country_matches(original, countries, rows, threshold)
        new_country = any(match[0] or confirmed(mention) for (mention, _), match in current.items())
        for item in countries:
            key = f'{task.task_id}:country:{item.mention}'
            answer = task.resolved.get(key, {})
            if not answer:
                for previous in state.plan.tasks:
                    confirmed=previous.resolved.get(f'{previous.task_id}:country:{item.mention}', {})
                    if confirmed.get('country_codes'):
                        answer=confirmed
                        break
            if 'country_codes' in answer:
                out.country_codes.extend(answer['country_codes'])
                continue
            if (item.mention, item.name) not in current:
                # D-22: 질문에 없는 이전 조건 국가를 그대로 다시 적은 것은 생략(이어받기)과 같다.
                echoed, _ = country_matches(item.name, rows, threshold)
                if new_country and echoed in base.country_codes:
                    continue
                if echoed and echoed in base.country_codes:
                    out.country_codes.append(echoed)
                    continue
                raise SlotRepair('국가 표현을 질문에서 확인하지 못했습니다.')
            code, candidates, correction = current[item.mention, item.name]
            if 'replace_expression' in answer:  # 이전 체크포인트의 직접 입력 패치
                code, candidates = country_matches(answer['replace_expression'], rows, threshold)
                correction = None
            if code:
                out.country_codes.append(code)
                if correction:
                    note = f'“{item.mention}”을(를) {correction}({code})으로 해석했습니다.'[:MAX_TEXT_CHARS]
                    if note not in task.applied_defaults:
                        task.applied_defaults.append(note)
                continue
            if candidates is None:
                continue
            return ask(state, task, key, 'COUNTRY', f'“{item.mention}”에 해당하는 국가를 선택하거나 직접 입력해 주세요.',
                [(r['country_code'], r['name_ko'], {'country_codes':[r['country_code']]}) for r in candidates])
    if market_hs4:
        from backend.app.agents.market import hs6_by_exports, loaded
        bundle=_bounded_bundle(store, loaded, '예측 모델을 확인할 수 없습니다.')
        for key, expr, kept in market_hs4:
            ranked = hs6_by_exports(bundle, expr, out.country_codes)
            registered = {r['hs_code']: r for r in store.rows(select(hs_table).where(
                hs_table.c.hs_ver=='HS2022', hs_table.c.depth==6, hs_table.c.hs_code.startswith(expr)))}
            candidates = [registered[code] for code in ranked if code in registered][:5]
            if not candidates:
                raise SlotRepair('이 품목은 유망시장 예측 대상이 아닙니다.')
            if len(candidates)==1:
                row = candidates[0]
                out.hs_items.extend([*kept, {'hs_code':row['hs_code'], 'hs_ver':row['hs_ver']}])
                label = f'HS {expr}의 예측 대상인 {row["name_ko"]}({row["hs_code"]})을 적용했습니다.'
                label = label[:MAX_TEXT_CHARS]
                if label not in task.applied_defaults: task.applied_defaults.append(label)
                continue
            return ask(state, task, key, 'HS_ITEM', f'“{expr}”에 해당하는 품목을 선택하세요.',
                [(r['hs_code'], r['name_ko'], {'hs_items': [*kept, {k:r[k] for k in ('hs_code','hs_ver')}]}) for r in candidates])
    article = store.t('article')
    latest_article = store.c.scalar(select(func.max(article.c.published_date)).where(article.c.visibility_status=='VISIBLE'))
    bounds = trade.bounds(store) if task.intent != 'LOCAL_ISSUES' else []
    state.data_meta = DataMeta(trade_first_month=bounds[0] if bounds else None,
        trade_latest_month=bounds[-1] if bounds else None, article_latest_date=latest_article)
    share_sql = None
    if task.intent == 'MARKET_SHARE':
        share_sql = next((s for s in task.steps if s.tool == 'query_trade_data'), None)
        if share_sql and share_sql.args.get('template') == 'SHARE' and slots.ranking_group == 'HS6':
            # 분모가 한국 교역으로 확정된 품목별 비중은 LLM SQL로 조회한다(D-87).
            share_sql.args = {'template': 'LLM_SQL'}
            task.custom_query = task.custom_query or task.question[:MAX_TEXT_CHARS]
    if bounds:
        out.period = slots.period or out.period
        if slots.recent_months or not out.period:
            out.period = Period(start=shift_month(bounds[-1], 1-(slots.recent_months or 12)), end=bounds[-1])
        from backend.app.agents.multi_year import requested_years
        years = requested_years(task.question, base.year_comparison) if task.intent == 'STATISTICS' else None
        if years and len(out.country_codes) <= 1 and len(out.hs_items) <= 1:
            out.year_comparison = years
            out.period = Period(start=date(years.years[0], 1, 1), end=date(years.years[-1], 12, 1))
        comparison = slots.compare_period
        question = original if len(state.plan.tasks) == 1 else task.question
        # 세 해 이상 연도 비교(year_comparison, MULTI_YEAR)가 잡히면 그 방식이 우선이다(2026-11-17 사용자 결정).
        # 비교 기간 칸(compare_period·extra_compare_periods)은 그 밖의 여러 기간 비교에만 쓴다.
        if (not out.year_comparison and prior_compare and comparison is None and slots.extra_compare_periods is None
                and slots.comparison_requested is not False
                and SAME_COMPARISON_CONDITIONS.search(question)):
            out.compare_period, out.extra_compare_periods = prior_compare, prior_extras
        if slots.comparison_requested and slots.period and not out.year_comparison:
            out.period, comparison = comparison_windows(question, out.period, comparison)
        # 유망시장은 통계 기간을 조회하지 않는다. 공개 통계보다 오래된 기간을 물어도 여기서 끝내지 않고, 아래 MODEL_PERIOD로
        # 최신 기준월 예측을 제안한다.
        if out.year_comparison:
            for year in out.year_comparison.years:
                trade.resolve_period(store, f'{year}-01', f'{year}-12')
        elif task.intent not in MARKET_INTENTS:
            trade.resolve_period(store, out.period.start.strftime('%Y-%m'), out.period.end.strftime('%Y-%m'))
        if out.year_comparison:
            out.compare_period, out.extra_compare_periods = None, []   # 연도 비교(MULTI_YEAR)가 우선이다
        elif share_sql and share_sql.args.get('template') == 'SHARE':
            out.compare_period, out.extra_compare_periods = None, []
            note = '국가별 교역 비중은 조회 기간 기준으로만 계산했습니다.'
            if slots.comparison_requested and note not in task.applied_defaults:
                task.applied_defaults.append(note)
        elif slots.comparison_requested is False and not frozen_report:
            out.compare_period, out.extra_compare_periods = None, []
        # 배포 전 저장된 결과 기반 리포트 계획도 선행 결과의 비교 기간을 유지한다(D-90).
        elif (slots.comparison_requested or out.compare_period) and not frozen_report:
            # 조회 기간과 같은 비교 기간("2025년 기준 2025년·2024년 비교")과 중복은 뺀다. 같은 기간이 두 번이면
            # 차트 시리즈 이름이 겹친다. 여러 비교 기간을 한 표에 두는 것은 통계(TREND·LLM SQL)만 지원한다.
            periods = []
            # 첫 비교 기간은 연간 비교의 기준·대상 연도를 맞춘 값(comparison_windows)을 쓴다.
            if comparison is None and not slots.extra_compare_periods:
                comparison, extras = out.compare_period, out.extra_compare_periods
            elif (slots.extra_compare_periods is None and comparison == prior_compare
                  and not _comparison_named(original if len(state.plan.tasks) == 1 else task.question, comparison)):
                # 후속 분석·다음 턴에서 플래너가 질문에 없는 첫 비교 기간만 다시 적으면(같은 조건으로 미국도) 이어받기와
                # 같아 나머지 비교 기간도 유지한다(D-22). 질문에 비교 기간을 적으면("2023년하고만") 적은 기간만 쓴다.
                extras = prior_extras
            else:
                extras = slots.extra_compare_periods or []
            for candidate in [comparison, *extras]:
                if candidate and candidate != out.period and candidate not in periods:
                    periods.append(candidate)
            # 여러 비교 기간을 한 표에 두는 것은 통계(TREND·LLM SQL)만 지원한다. 다른 분석은 중복을 뺀 뒤 첫 기간만 쓴다.
            kept = periods if task.intent == 'STATISTICS' else periods[:1]
            # 실제로 한 비교만 안내한다. 순위 템플릿(trade.rankings)은 지정 비교 기간을 쓰지 않는다(전년·전월 대비만 계산).
            # 생성 SQL(custom_query)은 확정 비교 기간을 SQL 조건으로 쓰므로 해당하지 않는다.
            note = ('순위는 지정한 비교 기간으로 비교하지 않습니다.' if task.intent == 'RANKING' and kept and not task.custom_query
                    else '비교 기간 여러 개는 통계 조회에서만 함께 비교합니다. 첫 비교 기간만 비교했습니다.'
                    if len(kept) < len(periods) else None)
            if note and note not in task.applied_defaults:
                task.applied_defaults.append(note)
            out.compare_period, out.extra_compare_periods = None, []
            if kept:
                out.compare_period, out.extra_compare_periods = kept[0], kept[1:]
            elif not comparison:
                # 비교 기간을 말하지 않았을 때만 전년 동기를 넣는다. 말한 기간이 모두 조회 기간과 같았으면 비교하지 않는다.
                out.compare_period = Period(start=shift_month(out.period.start,-12), end=shift_month(out.period.end,-12))
    if task.intent == 'CAUSE':
        out.article_period = {'start':shift_month(out.period.start,-1), 'end':month_end(shift_month(out.period.end,1))}
    if task.intent == 'LOCAL_ISSUES':
        out.article_period = slots.article_period or recent_article_period(state.input.received_at)
    if task.intent == 'RANKING':
        key = f'{task.task_id}:ranking_group'
        out.ranking_group = task.resolved.get(key, {}).get('ranking_group', slots.ranking_group)
        if not out.ranking_group:
            return ask(state, task, key, 'RANKING_GROUP', '어떤 기준으로 순위를 볼까요?',
                       [('COUNTRY','국가별',{'ranking_group':'COUNTRY'}), ('HS6','품목별',{'ranking_group':'HS6'})])
    # 같은 대상이 여러 경로(이어받기·코드·국가명)로 들어와도 하나만 남긴다. Conditions는 중복을 거부한다.
    data = out.model_dump(warnings=False)
    data['country_codes'] = list(dict.fromkeys(data['country_codes']))
    article_edition_inherited = (task.intent in ARTICLE_INTENTS
                                 and any(item['hs_ver']=='HS2017' for item in data['hs_items']))
    if article_edition_inherited:
        items = _article_hs_items(store, state, task, data['hs_items'])
        if items is None:
            return
        data['hs_items'] = items
    data['hs_items'] = list({(h['hs_code'], h['hs_ver']): h for h in data['hs_items']}.values())
    if task.intent=='RANKING' and out.ranking_group=='COUNTRY' and uses_item_edition:
        if len({item['hs_ver'] for item in data['hs_items']})>1:
            if not editions:
                raise SlotRepair('국가별 금액 순위에는 HS 판본 하나가 필요합니다. HS2017 또는 HS2022를 지정해 주세요.')
            data['hs_items'] = [item for item in data['hs_items'] if item['hs_ver']==hs_ver]
    task.conditions = Conditions.model_validate(data)
    if task.intent in MARKET_INTENTS:
        if not out.country_codes and not out.hs_items: raise SlotRepair('유망시장을 조회할 국가 또는 품목을 입력해 주세요.')
        if task.intent == 'MARKET_TYPE' and not (out.country_codes and out.hs_items):   # D-23: 국가·품목 쌍의 유형
            raise SlotRepair('시장 유형은 국가와 품목을 함께 입력해 주세요.')
        if (slots.period or slots.recent_months) and not slots.latest_prediction_confirmed:
            key=f'{task.task_id}:model_period'
            if key in task.resolved: raise SlotRepair('과거 기준월 예측은 지원하지 않습니다.')
            from backend.app.agents.market import loaded
            bundle=_bounded_bundle(store, loaded, '예측 기준월 스냅샷을 확인할 수 없습니다.')
            anchor=str(bundle.get('anchor') or bundle.get('snapshot_month') or '')[:7]
            if not anchor: raise SlotRepair('예측 모델의 기준월을 확인할 수 없습니다.')
            if slots.recent_months or slots.period.end.strftime('%Y-%m')!=anchor:
                return ask(state,task,key,'MODEL_PERIOD',f'최신 기준월 {anchor} 예측으로 조회할까요?',
                    [('YES','최신 기준월로 조회',{'latest_prediction_confirmed':True}),('NO','거절',{'latest_prediction_confirmed':False})])
    if editions and (any(h.hs_ver != hs_ver for h in task.conditions.hs_items)
                     or (hs_ver == 'HS2017' and not task.conditions.hs_items)):
        raise SlotRepair(f'{hs_ver} 판본으로 조회할 등록 품목명이나 HS 코드를 지정해 주세요.')
    if (not (uses_item_edition or frozen_report) and task.intent not in ARTICLE_INTENTS
            and any(h.hs_ver == 'HS2017' for h in task.conditions.hs_items)):
        # 선행 결과에서 이어받은 HS2017 품목도 질문에 적은 판본과 같은 지원 범위를 안내한다(비중·유망시장·순위 화면 표).
        raise SlotRepair('HS2017은 채팅의 금액 순위와 추이에서 지원합니다.')
    hs_versions = ('HS2017', 'HS2022') if uses_item_edition or frozen_report else ('HS2022',)
    accounts.validate_scope(store, [x.model_dump() for x in task.conditions.hs_items], task.conditions.country_codes,
                            hs_versions=hs_versions)
    if task.conditions.hs_items and (article_edition_requested or article_edition_inherited):
        # 금액 하위 질문이 함께 있을 때만 판본을 금액 조회에 적용했다고 안내한다. 단일 기사 질문은 바꾼 사실만 알린다.
        note = ('지정한 HS 판본은 금액 추이·순위에만 적용하고 기사 분석 조건은 HS2022 품목으로 확정했습니다.'
                if any(t.intent not in ARTICLE_INTENTS for t in state.plan.tasks)
                else 'HS2017 품목은 기사 분석에 쓰도록 대응하는 HS2022 품목으로 확정했습니다.')
        if note not in task.applied_defaults:
            task.applied_defaults.append(note)
    if task.intent == 'REPORT':
        if confirm_report is None: raise SlotRepair('리포트 조건을 확인하지 못했습니다.')
        confirm_report(store, state, task)
        if state.pending: return
    step.status = 'DONE'
