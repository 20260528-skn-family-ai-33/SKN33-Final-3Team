"""D-90/95: select downstream inputs from owned, unexpired result rows."""
import re
from pydantic import ValidationError
from sqlalchemy import select
from backend.app.agents.conditions import ask
from backend.app.agents.state import Conditions, HsProduct, MAX_COUNTRIES, MAX_HS_ITEMS, ToolResult
from backend.app.api.chat_slots import SlotRepair
from backend.app.api.common import ApiError
from backend.app.api.results import read_snapshot

PREVIOUS_TABLE_CHOICES = 5   # REPORT_TABLE 선택지 상한(INPUT_SELECTION과 같다)
TABLE_KINDS = {'TREND': '월별 추이', 'RANKING': '순위', 'SHARE': '비중', 'PREDICTION': '유망시장 예측'}
NO_PREVIOUS_TABLE = '직전 답변에 리포트로 만들 결과표가 없습니다. 조회와 리포트 생성을 한 질문에서 요청해 주세요.'
EXPIRED_PREVIOUS_TABLE = '직전 결과표의 보관 기간이 지났습니다. 같은 조건으로 다시 조회한 뒤 리포트를 요청해 주세요.'
UNKNOWN_PREVIOUS_TABLE = '직전 결과표의 종류를 확인할 수 없어 리포트로 만들 수 없습니다. 같은 조건으로 다시 조회한 뒤 리포트를 요청해 주세요.'
EXPORT_ONLY = '이 분석은 수출만 지원합니다.'   # conditions.py의 수입 거부와 같은 문구


def previous_tables(store, state):
    """같은 대화방에서 현재 질문 바로 앞 답변(ASSISTANT 메시지 하나)에 담긴 결과표. 더 앞 답변은 보지 않는다(설계 '이전 턴 결과 참조' 2)."""
    table = store.t('chat_message')
    row = store.one(select(table.c.result_json).where(
        table.c.session_id == int(state.input.session_id), table.c.role == 'ASSISTANT',
        table.c.message_id < int(state.input.job_id)).order_by(table.c.message_id.desc()).limit(1))
    answer = ((row or {}).get('result_json') or {}).get('answer') or {}
    conditions = {t.get('task_id'): t.get('conditions') or {} for t in answer.get('tasks') or []}
    # 기존 채팅 API(api/chat.py) 답변은 tasks 없이 answer.conditions 하나만 남기고, 표 종류와 관계없이 intent='STATISTICS'다.
    legacy = not answer.get('tasks')
    return [{'result_id': t['result_id'], 'task_id': t.get('task_id'), 'legacy': legacy,
             'conditions': (answer.get('conditions') if legacy else conditions.get(t.get('task_id'))) or {},
             'kind': table_kind(t.get('meta'), t.get('columns')) if legacy else (t.get('meta') or {}).get('kind'),
             'expired': bool(t.get('expired'))}
            for t in answer.get('tables') or [] if t.get('result_id')]


# 기존 채팅 API 표의 종류를 정하는 열. 순위표에도 share_pct가 있으므로 rank를 먼저 본다.
LEGACY_KINDS = (('rank', 'RANKING'), ('share_pct', 'SHARE'), ('month', 'TREND'))


def table_kind(meta, columns):
    """기존 채팅 API 표의 종류. meta.kind가 없으면 표 열 구성으로 복원한다. 모르면 None."""
    if kind := (meta or {}).get('kind'): return kind
    keys = {c.get('key') for c in columns or []}
    return next((kind for column, kind in LEGACY_KINDS if column in keys), None)


def _load(store, state, table):
    """후보 결과표의 현재 스냅샷(소유권·만료 확인). 쓸 수 없으면 None.
    기존 채팅 API 표는 비교 기간 등을 표에 저장하지 않고 답변에서 확정했으므로 답변 조건으로 채운다. 기간·품목·국가는 표 그대로다."""
    if table['expired']: return None
    try: snapshot = read_snapshot(store, state.input.user_id, table['result_id'], 'RESULT')
    except ApiError as exc:
        if exc.code in ('RESULT_EXPIRED', 'NOT_FOUND'): return None
        raise
    conditions = snapshot['conditions']
    if table['legacy']: conditions = {**table['conditions'], **conditions}
    meta = {**(snapshot.get('meta') or {}), 'kind': table['kind']}
    if table['legacy'] and meta.get('truncated') and meta.get('completeness') != 'PARTIAL':
        # 기존 채팅 API는 잘린 표를 completeness 없이 meta.truncated·rows_total로만 남긴다(api/trade.py 순위표 1,000행).
        # 리포트에는 저장된 행만 실리므로 부분 결과로 알린다. 에이전트 결과의 meta.truncated는 요약(digest) 행 상한이라
        # 표 자체는 전부 저장돼 있고, 표가 잘리면 completeness·limitations로 따로 남긴다(templates.py). 그래서 기존 표만 본다.
        total, kept = meta.get('rows_total'), len(snapshot.get('rows') or [])
        reason = (f'전체 {total:,}개 중 상위 {kept:,}개만 포함했습니다.' if isinstance(total, int) and total > kept
                  else f'결과표가 잘려 상위 {kept:,}개만 포함했습니다.')
        meta.update(completeness='PARTIAL', limitations=[*(meta.get('limitations') or []), reason])
    return {**snapshot, 'conditions': conditions, 'meta': meta}


def _names(values, limit=2):
    return ', '.join(values[:limit]) + (f' 외 {len(values) - limit}' if len(values) > limit else '')


def _table_pages(count):
    """REPORT_TABLE 선택지 쪽 나누기. 한 화면 버튼은 PREVIOUS_TABLE_CHOICES(5)개다.
    표가 더 남은 쪽은 표 4개 + '다음 목록', 마지막 쪽은 표 5개까지. 반환은 쪽마다 (시작, 끝) 범위."""
    pages, start = [], 0
    while start < count:
        end = count if count - start <= PREVIOUS_TABLE_CHOICES else start + PREVIOUS_TABLE_CHOICES - 1
        pages.append((start, end)); start = end
    return pages


def _table_labels(store, tables):
    """버튼 문구: '1. 미국 · HS 3307 · 월별 추이'. 질문 문장 대신 표를 만든 조건(국가·품목·종류)으로 구분한다."""
    codes = sorted({c for t in tables for c in t['conditions'].get('country_codes') or []})
    names = {}
    if codes:
        country = store.t('country')
        names = {r['country_code']: r['name_ko'] for r in store.rows(
            select(country.c.country_code, country.c.name_ko).where(country.c.country_code.in_(codes)))}
    bases = []
    for table in tables:
        conditions = table['conditions']
        parts = [_names([names.get(c, c) for c in conditions.get('country_codes') or []]) or '전체 국가']
        if items := [f"HS {h['hs_code']}" for h in conditions.get('hs_items') or [] if h.get('hs_code')]:
            parts.append(_names(items))
        parts.append(TABLE_KINDS.get(table['kind'], '결과표'))
        bases.append(' · '.join(parts))
    labels = []
    for index, (table, base) in enumerate(zip(tables, bases), 1):
        # 국가·품목·종류가 같은 후보는 조회 기간으로 구별한다. 기간은 잘리지 않게 앞부분을 줄인다.
        period = table['conditions'].get('period') or {}
        suffix = (f" · {period['start'][2:7].replace('-', '.')}~{period['end'][2:7].replace('-', '.')}"
                  if bases.count(base) > 1 and period.get('start') and period.get('end') else '')
        prefix = f'{index}. '
        labels.append(prefix + base[:40 - len(prefix) - len(suffix)] + suffix)
    return labels


def previous_result(store, state, task):
    """previous_turn_result 리포트의 입력 결과표를 고른다. 여러 개면 REPORT_TABLE로 묻고 None을 돌려준다.
    고른 result_id는 Task.resolved에 남겨 재개·승인 재진입에서 같은 표를 쓴다. 반환은 source_versions와 같은 (snapshot, info)."""
    key = f'{task.task_id}:previous_result'
    chosen = task.resolved.get(key, {}).get('previous_result_id')
    tables = previous_tables(store, state)
    if chosen is None:
        if not tables: raise SlotRepair(NO_PREVIOUS_TABLE)
        loaded = [(t, s) for t in tables if (s := _load(store, state, t))]
        if not loaded: raise SlotRepair(EXPIRED_PREVIOUS_TABLE)
        known = [(t, s) for t, s in loaded if t['kind']]
        if not known: raise SlotRepair(UNKNOWN_PREVIOUS_TABLE)
        # 리포트는 수출만 지원한다(D-81). 수출입을 함께 조회한 답변의 수입 표는 후보에서 뺀다.
        usable = [(t, s) for t, s in known if s['conditions'].get('trade_flow') != 'IMPORT']
        if not usable: raise SlotRepair(EXPORT_ONLY)
        if len(usable) > 1:
            # 표가 5개를 넘으면 '다음 목록'으로 나머지를 보인다. 쪽마다 따로 묻고(되묻기 횟수는 조건 키마다 센다) 답은 쪽 키에 남는다.
            pages, page = _table_pages(len(usable)), 0
            while (answer := task.resolved.get(key if page == 0 else f'{key}:page{page}', {})).get('previous_table_page'):
                page = min(answer['previous_table_page'], len(pages) - 1)   # 그사이 만료로 후보가 줄어도 마지막 쪽까지만
            chosen = answer.get('previous_result_id')
            if chosen is None:
                every = [{**t, 'conditions': s['conditions']} for t, s in usable]
                labels = _table_labels(store, every)   # 번호·기간 표시는 전체 후보 기준
                start, end = pages[page]
                options = [(f'T{i + 1}', labels[i], {'previous_result_id': every[i]['result_id']}) for i in range(start, end)]
                if page + 1 < len(pages):
                    options.append(('NEXT', f'다음 목록 ({end + 1}~{len(usable)}번)', {'previous_table_page': page + 1}))
                ask(state, task, key if page == 0 else f'{key}:page{page}', 'REPORT_TABLE',
                    '어느 결과표로 리포트를 만들까요?', options)
                return None
        else:
            chosen = usable[0][0]['result_id']
        task.resolved[key] = {'previous_result_id': chosen}
    table = next((t for t in tables if t['result_id'] == chosen), None)
    if table is None:
        raise SlotRepair('선택한 결과표가 직전 답변에 없습니다. 새 질문으로 다시 요청해 주세요.')
    snapshot = _load(store, state, table)
    if snapshot is None: raise SlotRepair(EXPIRED_PREVIOUS_TABLE)
    if not table['kind']: raise SlotRepair(UNKNOWN_PREVIOUS_TABLE)
    try: stored_conditions(snapshot)
    except ValidationError as exc:
        # 기존 채팅 API는 국가 250개·품목 100개까지 허용했다. 에이전트 조건 상한(D-24)을 넘는 표는 작업 실패 대신 안내한다.
        raise SlotRepair(f'직전 결과표의 조건(국가 {MAX_COUNTRIES}개·품목 {MAX_HS_ITEMS}개 이내)을 리포트로 옮길 수 없습니다. '
                         '범위를 줄여 다시 조회한 뒤 리포트를 요청해 주세요.') from exc
    meta = snapshot['meta']
    return snapshot, {'result_id': chosen, 'version': meta.get('input_fingerprint'),
                      'completeness': meta.get('completeness'), 'limitations': meta.get('limitations') or []}


def stored_conditions(snapshot):
    """결과표에 저장된 분석 조건. 리포트는 표를 만든 조건 그대로 쓴다(재조회 없음)."""
    stored = snapshot['conditions']
    return Conditions.model_validate({k: v for k, v in stored.items() if k in Conditions.model_fields})


def source_versions(store,state,task):
    sources=[]
    for binding in task.input_bindings:
        result=state.results.get(binding.source_step_id)
        if not result or result.status!='OK' or not result.result_id: raise SlotRepair('선행 분석 결과가 없습니다.')
        snapshot=read_snapshot(store,state.input.user_id,result.result_id,'RESULT')
        meta=snapshot['meta']
        if (meta.get('completeness')!=result.completeness or meta.get('limitations')!=result.limitations):
            raise SlotRepair('선행 분석 결과가 변경되었습니다. 새 질문으로 다시 조회하세요.')
        sources.append((snapshot,{'result_id':result.result_id,'version':meta.get('input_fingerprint'),
                       'completeness':result.completeness,'limitations':result.limitations}))
    return sources


def resolve(store,state,task,out):
    # 기준 조건은 첫 선행 결과의 저장 조건으로 한 번만 만든다. 바인딩마다 새로 만들면 앞 바인딩이 고른
    # 국가·품목이 뒤 바인딩의 저장 조건(전체 국가 등)으로 덮인다(국가 상위 2 + 품목 상위 1 → country_codes=[]).
    # 여러 선행 결과에서 같은 차원(국가·품목)을 고르면 덮어쓰지 않고 합친다(미국 상위 + 일본 상위 → 둘 다)
    chosen={'COUNTRY':[], 'HS6':[]}
    for index,(binding,(snapshot,source)) in enumerate(zip(task.input_bindings,source_versions(store,state,task),strict=True)):
        if index==0:
            stored=snapshot['conditions']
            out=stored_conditions(snapshot)
        elif any(snapshot['conditions'].get(k)!=stored.get(k) for k in ('period','trade_flow')):
            # 뒤 선행 결과의 기간·수출입이 다르면 첫 결과 기준으로 분석하고 그 사실을 적용 조건에 남긴다
            p=out.period
            label=('선행 결과의 기간·수출입 구분이 서로 달라 첫 결과 기준'
                   +(f'({p.start:%Y-%m}~{p.end:%Y-%m})' if p else '')+'으로 분석했습니다.')
            if label not in task.applied_defaults: task.applied_defaults.append(label)
        selection=binding.selection
        if not selection: continue
        key=f'{task.task_id}:input:{binding.source_step_id}'
        rule=task.resolved.get(key) or selection.model_dump(exclude_none=True)
        kind=snapshot['meta']['kind']; group=snapshot['meta'].get('group_by',snapshot['meta'].get('group'))
        dimension=rule.get('dimension'); order=rule.get('order_by'); n=rule.get('limit')
        # 금액 순위 결과에는 국가 안·품목 안 순위가 없다. 플래너가 순위 이름(country_rank)을 내도 금액 순위로 본다.
        if kind=='RANKING' and order in ('country_rank','product_rank'): order='amount_usd'
        if kind=='PREDICTION':
            supported=[('COUNTRY','product_rank'),('HS6','country_rank')]
            if group=='COUNTRY': supported=supported[1:]
            elif group=='HS6': supported=supported[:1]
        elif kind=='RANKING': supported=[(group,'amount_usd')]
        else: raise SlotRepair('선행 결과에서 대상 순위를 선택할 수 없습니다.')
        if dimension and order and (dimension,order) not in supported:
            raise SlotRepair('선행 결과의 순위 기준과 요청 대상이 다릅니다.')
        rows=snapshot['rows']
        group_key='country_code' if order=='country_rank' else 'hs6' if order=='product_rank' else None
        groups=sorted({r[group_key] for r in rows}) if group_key else []
        named_group=rule.get('group_value')
        evidence=state.input.question+' '+task.question
        explicit_count=n and re.search(rf'(?<!\d){n}(?!\d)',evidence)
        if (not (dimension and order and n) or (not explicit_count and key not in task.resolved)
                or (len(groups)>1 and not named_group)):
            options=[]
            for dim,metric in supported:
                for count in ([n] if n else [1,3,5]):
                    if dimension and dimension!=dim: continue
                    group_field='country_code' if metric=='country_rank' else 'hs6' if metric=='product_rank' else None
                    group_values=sorted({r[group_field] for r in rows}) if group_field else [None]
                    for value in group_values:
                        patch={'dimension':dim,'order_by':metric,'limit':count}
                        if value: patch['group_value']=value
                        options.append((f'{dim}_{count}_{value or "all"}',f'{value or ""} {"국가" if dim=="COUNTRY" else "품목"} 상위 {count}개'.strip(),patch))
            ask(state,task,key,'INPUT_SELECTION','선행 결과에서 사용할 대상을 선택하세요.', options[:5]); return out
        if n>(MAX_COUNTRIES if dimension=='COUNTRY' else MAX_HS_ITEMS):
            raise SlotRepair('선택한 국가 또는 품목이 조건 상한을 넘습니다.')
        if named_group and group_key: rows=[r for r in rows if r[group_key]==named_group]
        source_flow=snapshot['conditions'].get('trade_flow') or 'EXPORT'
        field=('export_amount_usd' if source_flow=='EXPORT' else 'import_amount_usd') if order=='amount_usd' else order
        def code(row): return row.get('country_code') if dimension=='COUNTRY' else row.get('hs6') or row.get('hs_code') or (row.get('hs_item') or {}).get('hs_code')
        sorted_rows=sorted((r for r in rows if code(r) and r.get(field) is not None),
            key=lambda r:(-r[field] if order=='amount_usd' else r[field],code(r)))
        codes=list(dict.fromkeys(code(r) for r in sorted_rows))[:n]
        if not codes: raise SlotRepair('요청한 대상이 선행 결과표에 없습니다.')
        if dimension=='COUNTRY':
            chosen['COUNTRY']=list(dict.fromkeys([*chosen['COUNTRY'],*codes]))
            if len(chosen['COUNTRY'])>MAX_COUNTRIES: raise SlotRepair('선택한 국가 또는 품목이 조건 상한을 넘습니다.')
            out.country_codes=chosen['COUNTRY']
        else:
            # 선행 결과 행의 판본을 따른다(예측 행의 hs_ver, 순위 행의 hs_item). 없으면 HS2022다.
            editions={code(r):r.get('hs_ver') or (r.get('hs_item') or {}).get('hs_ver') or 'HS2022' for r in reversed(sorted_rows)}
            items=[*chosen['HS6'],*((c,editions[c]) for c in codes)]
            chosen['HS6']=list(dict.fromkeys(items))
            if len(chosen['HS6'])>MAX_HS_ITEMS: raise SlotRepair('선택한 국가 또는 품목이 조건 상한을 넘습니다.')
            out.hs_items=[HsProduct(hs_code=c,hs_ver=v) for c,v in chosen['HS6']]
        if len(codes)<n:
            state.results[task.steps[0].step_id]=ToolResult(status='OK',completeness='PARTIAL',
                limitations=[f'요청한 {n}개 중 {len(codes)}개 대상만 있습니다.'])
        label=f'{order} 기준 상위 {len(codes)}개: '+', '.join(codes)
        if label not in task.applied_defaults: task.applied_defaults.append(label)
    return out
