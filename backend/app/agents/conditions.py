"""Server-owned condition resolution and replayable clarifications (§14.1)."""
from datetime import timedelta, timezone
from difflib import SequenceMatcher
from uuid import uuid4
from sqlalchemy import select, func

from backend.app.agents.state import (Choice, Conditions, DataMeta, Pending, Period,
                                     MAX_ASKS_PER_CONDITION, MAX_TEXT_CHARS, ToolResult)
from backend.app.api import accounts, reference, trade
from backend.app.api.chat_slots import country_key, numeric_product, SlotRepair
from backend.app.api.common import ApiError, month_end, now, shift_month


def country_matches(name, rows, threshold):
    key = country_key(name)
    matches = []
    for row in rows:
        if row['country_role'] != 'COUNTRY': continue
        label = country_key(row['name_ko'])
        exact = key in (label, country_key(row['country_code']))
        similarity = SequenceMatcher(None, key, label).ratio()
        contains = key in label or label in key
        if exact or contains or similarity >= threshold:
            matches.append((not exact, -similarity, row['country_code'], row, contains))
    matches.sort(key=lambda m: m[:3])
    if matches and not matches[0][0]:
        return matches[0][2], []
    return None, [m[3] for m in matches[:5]]


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


def _resolve(store, state, task, step, resolve_bindings, confirm_report):
    slots = task.slots
    report_answer=task.resolved.get(f'{task.task_id}:report_confirm',{})
    if report_answer.get('reject_input_change'): raise SlotRepair('선행 결과의 조건을 변경하려면 새 분석 질문을 입력해 주세요.')
    original=state.input.question+' '+report_answer.get('revision_text','')
    # Customs tables already describe Korea's trade. Korea is the reporting
    # country, not a partner to resolve against the country dictionary.
    reporting_names={'한국','대한민국','우리나라','kr','kor','south korea','republic of korea'}
    countries=slots.countries
    if countries:
        countries=[item for item in countries if not (
            item.mention.strip().casefold() in reporting_names
            and item.mention.casefold() in original.casefold())]
    base = state.input.context.conditions or Conditions()
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
    if task.intent in ('REPORT', 'PROMISING_MARKET') and out.trade_flow == 'IMPORT':
        raise SlotRepair('이 분석은 수출만 지원합니다.')
    share_key = f'{task.task_id}:share_basis'
    out.share_basis = task.resolved.get(share_key, {}).get('share_basis', slots.share_basis)
    if task.intent == 'MARKET_SHARE' and out.share_basis == 'FOREIGN_IMPORT_MARKET':
        raise SlotRepair('해외 수입시장에서 한국 제품의 점유율은 아직 지원하지 않습니다.')
    hs_table = store.t('hs_code')
    market_hs4 = []
    for expression in slots.products or []:
        key = f'{task.task_id}:hs:{expression}'
        answer = task.resolved.get(key, {})
        # D-74: 유망시장 모델은 HS6 단위다. 되묻기 답이 HS4("이 HS4 전체로 분석"·4자리 입력)여도 HS6 후보로 다시 묻는다.
        hs4 = [h['hs_code'] for h in answer.get('hs_items', []) if len(h['hs_code']) == 4] \
            if task.intent == 'PROMISING_MARKET' else []
        if 'hs_items' in answer and not hs4:
            out.hs_items.extend(answer['hs_items'])
            continue
        # 같은 답에 함께 고른 HS6는 다시 묻는 동안에도 남긴다(다음 답의 patch에 함께 넣는다)
        kept = [h for h in answer.get('hs_items', []) if len(h['hs_code']) != 4] if hs4 else []
        expr = hs4[0] if hs4 else answer.get('replace_expression', expression)
        # D-22: 플래너가 질문에 없는 이전 조건을 그대로 다시 적으면 생략(이어받기)과 같다.
        # 코드가 같거나, 이전 조건을 만든 직전 질문의 표현이고 그 표현의 검색 결과에 이전 품목이 있을 때만 이어받는다.
        if not answer and base.hs_items and expr.casefold() not in original.casefold():
            echoed = [h for h in base.hs_items if h.hs_code == expr]
            last = state.input.context.recent[-1].question.casefold() if state.input.context.recent else ''
            if not echoed and not expr.isdigit() and expr.casefold() in last:
                found = {r['hs_code'] for r in reference._search_depth(store, expr, 6, 5)['items']}
                echoed = [h for h in base.hs_items if h.hs_code in found]
            if echoed:
                out.hs_items.extend({'hs_code': h.hs_code, 'hs_ver': h.hs_ver} for h in echoed)
                continue
        if not answer and expr.casefold() not in original.casefold():
            raise SlotRepair('질문에서 품목 표현을 확인하지 못했습니다.')
        if expr.isdigit():
            numeric_product(store, original if not answer else expr, expr, set())
            if task.intent != 'PROMISING_MARKET' or len(expr) == 6:
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
        # 되묻기로 확정한 국가('미구' → 미국)도 새 국가다.
        new_country = any(item.mention.casefold() in original.casefold()
                          and (country_matches(item.name, rows, threshold)[0]
                               or task.resolved.get(f'{task.task_id}:country:{item.mention}', {}).get('country_codes'))
                          for item in countries)
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
            if item.mention.isascii() and len(item.mention)==2:
                code = next((r['country_code'] for r in rows
                             if country_key(r['country_code']) == country_key(item.mention)), None)
                named_code, _ = country_matches(answer.get('replace_expression', item.name), rows, threshold)
                if code and named_code == code:
                    out.country_codes.append(code)
                    continue
                if not named_code:  # 국가명이 아닌 약어(TV 등)는 국가 조건이 아니다
                    continue
                # 코드와 국가명이 어긋나거나(UK→영국=GB) 없는 코드면 빼지 말고 국가명·입력 코드 후보로 되묻는다.
                return ask(state, task, key, 'COUNTRY', f'“{item.mention}”에 해당하는 국가를 선택하거나 직접 입력해 주세요.',
                    [(r['country_code'], r['name_ko'], {'country_codes':[r['country_code']]})
                     for c in dict.fromkeys(filter(None, (named_code, code))) for r in rows if r['country_code'] == c])
            code, candidates = country_matches(answer.get('replace_expression', item.name), rows, threshold)
            if code:
                out.country_codes.append(code)
                continue
            return ask(state, task, key, 'COUNTRY', f'“{item.mention}”에 해당하는 국가를 선택하거나 직접 입력해 주세요.',
                [(r['country_code'], r['name_ko'], {'country_codes':[r['country_code']]}) for r in candidates])
    if market_hs4:
        from backend.app.agents.market import hs6_by_exports, loaded
        try: bundle,_,_=loaded(store.db.settings)
        except (ImportError,OSError,ValueError): raise SlotRepair('예측 모델을 확인할 수 없습니다.')
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
        trade.resolve_period(store, out.period.start.strftime('%Y-%m'), out.period.end.strftime('%Y-%m'))
        if slots.comparison_requested:
            out.compare_period = slots.compare_period or Period(start=shift_month(out.period.start,-12),
                                                                 end=shift_month(out.period.end,-12))
    if task.intent == 'CAUSE':
        out.article_period = {'start':shift_month(out.period.start,-1), 'end':month_end(shift_month(out.period.end,1))}
    if task.intent == 'LOCAL_ISSUES':
        received=state.input.received_at
        end=received.replace(tzinfo=received.tzinfo or timezone.utc).astimezone(timezone(timedelta(hours=9))).date()
        month = shift_month(end,-3)
        start = month.replace(day=min(end.day, month_end(month).day)) + timedelta(days=1)
        out.article_period = slots.article_period or {'start':start, 'end':end}
    if task.intent == 'MARKET_SHARE' and not out.share_basis:
        return ask(state, task, share_key, 'SHARE_BASIS', '어떤 시장 비중을 조회할까요?',
            [(v, label, {'share_basis':v}) for v,label in [('KOREA_TRADE_PARTNER','한국 교역의 국가별 비중'),
                                                           ('FOREIGN_IMPORT_MARKET','해외 수입시장의 한국 비중')]])
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
    if task.intent=='PROMISING_MARKET':
        if not out.country_codes and not out.hs_items: raise SlotRepair('유망시장을 조회할 국가 또는 품목을 입력해 주세요.')
        if (slots.period or slots.recent_months) and not slots.latest_prediction_confirmed:
            key=f'{task.task_id}:model_period'
            if key in task.resolved: raise SlotRepair('과거 기준월 예측은 지원하지 않습니다.')
            from backend.app.agents.market import loaded
            try: bundle,_,_=loaded(store.db.settings)
            except (ImportError,OSError,ValueError): raise SlotRepair('예측 기준월 스냅샷을 확인할 수 없습니다.')
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
