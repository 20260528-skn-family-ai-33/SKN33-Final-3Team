"""Server-owned condition resolution and replayable clarifications (§14.1)."""
from datetime import timedelta, timezone
from difflib import SequenceMatcher
import re
from uuid import uuid4
from sqlalchemy import select, func

from backend.app.agents.planner import HS_CODE_MENTION, ambiguous_report_source, product_expressions
from backend.app.agents.state import (Choice, Conditions, DataMeta, Pending, Period,
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


def _product_key(expression):
    match = HS_CODE_MENTION.fullmatch(expression.strip())
    return match[1] if match else country_key(expression)


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
    if task.previous_turn_result:
        raise SlotRepair('이전 대화의 결과표는 아직 리포트에 바로 사용할 수 없습니다. 조회와 리포트 생성을 한 질문에서 요청해 주세요.')
    slots = task.slots
    # 이미 품목을 선택한 뒤 재개하는 체크포인트에도 같은 기업명 제외 규칙을 적용한다.
    slots.products = product_expressions(slots.products,article_context=task.intent in ('CAUSE','LOCAL_ISSUES'))
    report_answer=task.resolved.get(f'{task.task_id}:report_confirm',{})
    if report_answer.get('reject_input_change'): raise SlotRepair('선행 결과의 조건을 변경하려면 새 분석 질문을 입력해 주세요.')
    original=state.input.question+' '+report_answer.get('revision_text','')
    if len(state.plan.tasks) == 1 and not task.resolved and task.intent in ('STATISTICS', 'RANKING', 'MARKET_SHARE', 'CAUSE', 'LOCAL_ISSUES'):
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
    base = state.input.context.conditions or Conditions()
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
    out.compare_period, out.article_period = None, None
    out.trade_flow = slots.trade_flow or base.trade_flow
    if task.input_bindings:
        if task.intent=='REPORT' and task.report_request and task.report_request.confirmed:
            out=task.report_request.conditions.model_copy(deep=True)
        else:
            if resolve_bindings is None: raise SlotRepair('선행 결과의 대상을 확인할 수 없습니다.')
            out = resolve_bindings(store, state, task, out)
        if state.pending: return
        out.trade_flow=slots.trade_flow or out.trade_flow
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
            answer = next((t.resolved[f'{t.task_id}:hs:{other}']
                           for t in state.plan.tasks if t is not task and t.slots
                           for other in (t.slots.products or [])
                           if _product_key(other) == _product_key(expression)
                           and t.resolved.get(f'{t.task_id}:hs:{other}', {}).get('hs_items')), {})
        # D-74: 유망시장 모델은 HS6 단위다. 되묻기 답이 HS4("이 HS4 전체로 분석"·4자리 입력)여도 HS6 후보로 다시 묻는다.
        hs4 = [h['hs_code'] for h in answer.get('hs_items', []) if len(h['hs_code']) == 4] \
            if task.intent in MARKET_INTENTS else []
        if 'hs_items' in answer and not hs4:
            out.hs_items.extend(answer['hs_items'])
            continue
        # 같은 답에 함께 고른 HS6는 다시 묻는 동안에도 남긴다(다음 답의 patch에 함께 넣는다)
        kept = [h for h in answer.get('hs_items', []) if len(h['hs_code']) != 4] if hs4 else []
        expr = hs4[0] if hs4 else answer.get('replace_expression', expression)
        # "HS 3304"도 원문 숫자와 HS 사전을 대조하는 기존 코드 경로로 보낸다.
        if explicit_hs := HS_CODE_MENTION.fullmatch(expr.strip()):
            expr = explicit_hs[1]
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
            numeric_product(store, original if not answer else expr, expr, set())
            if task.intent not in MARKET_INTENTS or len(expr) == 6:
                out.hs_items.append({'hs_code': expr, 'hs_ver': 'HS2022'})
                continue
            market_hs4.append((key, expr, kept))
            continue
        else:
            found = reference._search_depth(store, expr, 6, 5)
            candidates = found['items']
            if found['match_type'] == 'EXACT' and len(candidates) == 1:
                out.hs_items.append({k: candidates[0][k] for k in ('hs_code','hs_ver')})
                continue
        return ask(state, task, key, 'HS_ITEM', f'“{expr}”에 해당하는 품목을 선택하세요.',
            [(r['hs_code'], r['name_ko'], {'hs_items': [*kept, {k:r[k] for k in ('hs_code','hs_ver')}]}) for r in candidates])

    interests = state.input.interests
    key = f'{task.task_id}:interest'
    answer = task.resolved.get(key, {})
    if (state.input.first_question and not out.hs_items and not out.country_codes and slots.countries is None and slots.products is None
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
        current = {(item.mention, item.name): _match_country_mention(item.mention, item.name, rows, threshold)
                   for item in countries if item.mention.casefold() in original.casefold()}
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
            if item.mention.casefold() not in original.casefold():
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
    if bounds:
        out.period = slots.period or out.period
        if slots.recent_months or not out.period:
            out.period = Period(start=shift_month(bounds[-1], 1-(slots.recent_months or 12)), end=bounds[-1])
        # 유망시장은 통계 기간을 조회하지 않는다. 공개 통계보다 오래된 기간을 물어도 여기서 끝내지 않고, 아래 MODEL_PERIOD로
        # 최신 기준월 예측을 제안한다.
        if task.intent not in MARKET_INTENTS:
            trade.resolve_period(store, out.period.start.strftime('%Y-%m'), out.period.end.strftime('%Y-%m'))
        if slots.comparison_requested:
            out.compare_period = slots.compare_period or Period(start=shift_month(out.period.start,-12),
                                                                 end=shift_month(out.period.end,-12))
    if task.intent == 'CAUSE':
        out.article_period = {'start':shift_month(out.period.start,-1), 'end':month_end(shift_month(out.period.end,1))}
    if task.intent == 'LOCAL_ISSUES':
        out.article_period = slots.article_period or recent_article_period(state.input.received_at)
    if task.intent == 'MARKET_SHARE' and slots.ranking_group == 'HS6':
        # SHARE 템플릿은 국가별 비중만 지원한다(D-87). 분모가 한국 교역으로 확정된 품목별 비중은 LLM SQL로 조회한다
        # (§11.1 custom_query). 해외 수입시장 기준은 위에서 미지원으로 끝났다. 분모가 모호하면 위에서 먼저 되묻는다.
        sql = next((s for s in task.steps if s.tool == 'query_trade_data'), None)
        if sql and sql.args.get('template') == 'SHARE':
            sql.args = {'template': 'LLM_SQL'}
            task.custom_query = task.custom_query or task.question[:MAX_TEXT_CHARS]
    if task.intent == 'RANKING':
        key = f'{task.task_id}:ranking_group'
        out.ranking_group = task.resolved.get(key, {}).get('ranking_group', slots.ranking_group)
        if not out.ranking_group:
            return ask(state, task, key, 'RANKING_GROUP', '어떤 기준으로 순위를 볼까요?',
                       [('COUNTRY','국가별',{'ranking_group':'COUNTRY'}), ('HS6','품목별',{'ranking_group':'HS6'})])
    # 같은 대상이 여러 경로(이어받기·코드·국가명)로 들어와도 하나만 남긴다. Conditions는 중복을 거부한다.
    data = out.model_dump(warnings=False)
    data['country_codes'] = list(dict.fromkeys(data['country_codes']))
    data['hs_items'] = list({(h['hs_code'], h['hs_ver']): h for h in data['hs_items']}.values())
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
    accounts.validate_scope(store, [x.model_dump() for x in task.conditions.hs_items], task.conditions.country_codes)
    if task.intent == 'REPORT':
        if confirm_report is None: raise SlotRepair('리포트 조건을 확인하지 못했습니다.')
        confirm_report(store, state, task)
        if state.pending: return
    step.status = 'DONE'
