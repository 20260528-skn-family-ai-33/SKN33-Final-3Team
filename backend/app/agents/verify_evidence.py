"""Typed, provenance-preserving evidence for verify. Never fetch or recompute data."""
from dataclasses import dataclass, replace
from decimal import Decimal
import re

from backend.app.agents.verify_claims import IDENTITY_FIELDS, NameCatalog, months_between, shift, YEAR_AGO, MONTH_AGO

PERIOD_AMOUNTS=frozenset({'total_usd','monthly_avg_usd','verified_subtotal_usd',
                         'compare_total_usd','compare_monthly_avg_usd','compare_verified_subtotal_usd'})


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    family: str | None
    flow: str | None
    aggregate: str | None
    comparison: str | None
    valid: bool = True


@dataclass(frozen=True)
class Fact:
    source: int
    path: tuple[str,...]
    metric: Metric
    value: Decimal | str | None
    unit: str
    targets: frozenset[str]
    group_members: frozenset[str]
    period: tuple[str,str] | None
    compare_period: tuple[str,str] | None
    month: str | None
    period_value: bool
    prediction: bool
    horizon: int | None
    role: str | None
    anchor: str | None
    stored_targets: frozenset[str]
    hs_ver: str | None = None
    annual_sql_sum: bool = False
    period_difference: bool = False


EXTRA_COMPARE_KEY=re.compile(r'compare([2-9])_(\w+)|change([2-9])_pct')


def compare_key(source, key):
    """(검사에 쓸 열 이름, 그 열의 비교 기간). 추가 비교 기간 열(compare2_total_usd·change2_pct)은 첫 비교 기간 열과
    같은 이름으로 검사하고 기간만 extra_compare_periods의 해당 기간으로 바꾼다.
    생성 SQL(LLM_SQL)은 별칭만으로 그 열이 어느 기간을 집계했는지 알 수 없으므로 비교 기간을 붙이지 않는다.
    (compare2_total_usd 별칭으로 2025년 금액을 내도 2024년 값으로 검증되면 안 된다.)
    예외는 조회·비교 연도 합계를 모두 SQL 식으로 증명한 연간 비교 결과의 change_pct 한 칸이다(_annual_rate_proven)."""
    unproved=source.get('kind')=='LLM_SQL' and not (key=='change_pct' and _annual_rate_proven(source))
    compare=None if unproved else period(source.get('compare_period'))
    match=EXTRA_COMPARE_KEY.fullmatch(key)
    if not match: return key, compare
    if compare is None: return ('compare_'+match[2] if match[1] else 'change_pct'), None
    extras=source.get('extra_compare_periods') or []
    n=int(match[1] or match[3])
    if n-2>=len(extras) or not (window:=period(extras[n-2])): return key, compare
    return ('compare_'+match[2] if match[1] else 'change_pct'), window


def _annual_rate_proven(source):
    """연간 비교 생성 SQL이 한 행에 조회 연도와 비교 연도 합계를 모두 SQL 식으로 증명했는지(snapshot_evidence의
    _annual_sums). 그때만 같은 행의 change_pct를 두 기간의 변화율로 본다. 다른 별칭·월 표시는 기간 근거가 아니다."""
    current, compare = period(source.get('period')), period(source.get('compare_period'))
    years = {cell.get('year') for cell in (source.get('_annual_sums') or {}).values() if isinstance(cell, dict)}
    return (source.get('kind') == 'LLM_SQL' and bool(current and compare) and len(source.get('rows') or []) == 1
            and years >= {current[0][:4], compare[0][:4]})


def period(value):
    if not isinstance(value,dict): return None
    start,end=(str(value.get(k,''))[:7] for k in ('start','end'))
    return (start,end) if all(re.fullmatch(r'\d{4}-(?:0[1-9]|1[0-2])',p) for p in (start,end)) and start<=end else None


def _share_point_metric(words, label):
    """Only saved column metadata can identify the measure and its comparison axis."""
    named_share = 'share' in words
    label_words = set(re.findall(r'[a-z]+', label.lower()))
    labeled_share = 'share' in label_words or bool(re.search(r'점유율|비중', label))
    axis_words = words | label_words
    yoy = 'yoy' in axis_words or bool(re.search(YEAR_AGO, label))
    mom = 'mom' in axis_words or bool(re.search(MONTH_AGO, label))
    delta = bool(axis_words & {'change', 'delta', 'diff', 'difference', 'comparison'})
    delta |= bool(re.search(r'변화|변동|증감|차이|상승|하락|증가|감소', label))
    if not (named_share or labeled_share) or not (delta or yoy or mom):
        return None
    conflicting_metric = (not labeled_share and bool(re.search(r'성장률|수익률|증감률', label))
                          or not named_share and bool(words & {'growth', 'margin', 'yield'}))
    other_axis = bool(axis_words & {'qoq', 'wow'} or re.search(r'전분기|직전\s*분기|전주|지난\s*주', label))
    valid = not (yoy and mom or conflicting_metric or other_axis)
    return 'SHARE_CHANGE', 'YOY' if yoy else 'MOM' if mom else 'PERIOD', valid


def metric(key,label,unit,default_flow):
    words=set(key.lower().split('_'))
    key_flows=({'EXPORT'} if 'export' in words or key=='exp_12m' else set())|({'IMPORT'} if 'import' in words else set())
    label_flows=({'EXPORT'} if '수출' in label else set())|({'IMPORT'} if '수입' in label else set())
    flows=key_flows or label_flows
    valid=len(flows)<2 and not (key_flows and label_flows and key_flows!=label_flows)
    flow=next(iter(flows)) if len(flows)==1 else default_flow
    family=aggregate=comparison=None
    if unit=='USD':
        family='AMOUNT_CHANGE' if words & {'change','delta','difference','diff'} or re.search(r'증감액|증가액|감소액|차액',label) else 'AMOUNT'
        if family=='AMOUNT_CHANGE':
            comparison='YOY' if 'yoy' in words else 'MOM' if 'mom' in words else 'PERIOD'
        aggregate='AVERAGE' if 'monthly_avg' in key or '월평균' in label else 'TOTAL' if key in PERIOD_AMOUNTS or re.search(r'총액|합계',label) else None
    elif unit=='%p' and (points := _share_point_metric(words,label)):
        family,comparison,points_valid=points
        valid=valid and points_valid
    elif unit in ('%','%p'):
        def classify(share,change,yoy,mom):
            if yoy and mom: return None,None
            if share and change: return 'SHARE_CHANGE',None
            if share and (yoy or mom): return None,None
            if share: return 'SHARE',None
            if yoy: return 'CHANGE','YOY'
            if mom: return 'CHANGE','MOM'
            if change: return 'CHANGE','PERIOD'
            return None,None
        by_key=classify('share' in words,bool(words & {'change','growth','comparison'}),'yoy' in words,'mom' in words)
        by_label=classify(bool(re.search(r'비중|점유율',label)),bool(re.search(r'증감률|증가율|감소율|변동률|변화',label)),
                          bool(re.search(YEAR_AGO,label)),bool(re.search(MONTH_AGO,label)))
        if key in ('share_pct','yoy_pct','mom_pct','change_pct','growth_pct'): family,comparison=by_key
        elif by_key[0] and by_label[0] and by_key[0]!=by_label[0]: valid=False
        else:
            family,comparison=by_key if by_key[0] else by_label
            # Generic comparison aliases keep their actual current/comparison windows.
            if by_key==('CHANGE','PERIOD'): comparison='PERIOD'
            elif by_key[1] and by_label[1] and by_label[1] not in (by_key[1],'PERIOD'): valid=False
    elif unit=='위': family='RANK'
    elif unit=='COUNT': family='COUNT'
    elif unit=='KG': family='QUANTITY'
    elif unit=='배': family='RATIO'
    return Metric(key,label,family,flow,aggregate,comparison,valid)


def bind_targets(stored, catalog):
    targets = set(stored)
    members = set()
    for name in stored:
        aliases, group = catalog.row_binding(name)
        targets.update(aliases, group)
        members.update(group)
    return frozenset(targets), frozenset(members)


def build_facts(sources,catalog:NameCatalog):
    facts=[]
    for index,source in enumerate(sources):
        if source.get('_source_unavailable'): continue
        columns={c['key']:c for c in source.get('columns',[]) if isinstance(c,dict) and 'key' in c}
        current=period(source.get('period'));compare=period(source.get('compare_period'))
        prediction=source.get('kind')=='PREDICTION'
        anchor=str(source.get('anchor') or '')[:7] or None

        def walk(row,stored=frozenset(),inherited_month=None,path=(),inherited_edition=None):
            if isinstance(row,list):
                for i,item in enumerate(row): walk(item,stored,inherited_month,path+(str(i),),inherited_edition)
                return
            if not isinstance(row,dict): return
            names={str(row[key]) for key in IDENTITY_FIELDS if row.get(key)}
            hs_item = row.get('hs_item')
            hs_item = hs_item if isinstance(hs_item, dict) else {}
            if hs_item.get('hs_code'): names.add(hs_item['hs_code'])
            edition = row.get('hs_ver') or hs_item.get('hs_ver')
            if edition not in ('HS2017', 'HS2022'): edition = inherited_edition
            stored = stored.union(names)
            identities, group = bind_targets(stored, catalog)
            month=str(row.get('month') or row.get('trade_month') or inherited_month or '')[:7] or None
            row_period=current
            if source.get('kind')=='LLM_SQL':
                # 생성 SQL의 월·연도 표시와 열 이름은 SQL이 만든 값이라 기간 근거가 아니다('2024-01' AS month로 2025년 금액을
                # 낼 수 있다). 조회 기간 전체를 그대로 집계했다고 증명된 결과(sql.period_covered)만 조회 기간을 붙인다.
                # 연간 비교 결과도 SQL 식으로 증명한 칸(연도별 합계·차액, 그 행의 change_pct)만 기간을 받는다(칸 단위).
                month=None
                if source.get('period_verified') is not True:
                    row_period=None
            for key,value in row.items():
                if key in ('_annual_sums', '_annual_differences', '_annual_candidates', '_annual_nationwide'): continue
                if isinstance(value,(dict,list)):
                    walk(value,stored,month,path+(key,),edition);continue
                if value is None and key == 'amount_usd' and month:
                    descriptor = metric(key, '', 'USD', source.get('trade_flow'))
                    facts.append(Fact(index, path+(key,), descriptor, None, 'NULL', identities, group,
                                      (month,month), None, month, False, prediction, None, None, anchor, stored, edition))
                    continue
                if prediction and key == 'growth_outlook' and value in ('증가 예상', '감소 예상', None):
                    row_anchor = str(row.get('anchor') or anchor or '')[:7]
                    if anchor and row_anchor != anchor:
                        continue
                    descriptor = Metric(key, '시장 예상 방향', 'OUTLOOK', source.get('trade_flow'), None, None)
                    facts.append(Fact(index, path+(key,), descriptor, value, 'OUTLOOK', identities, group,
                                      None, None, row_anchor or None, False, True, 12, 'FUTURE', row_anchor, stored, edition))
                    continue
                if not isinstance(value,(float,int,Decimal)) or isinstance(value,bool): continue
                # 추가 비교 기간 열(compare2_·change2_pct)은 첫 비교 기간 열 이름(logic)으로 검사하고 기간(window)만 바꾼다.
                logic,window=compare_key(source,key)
                amount=Decimal(str(value))
                if not amount.is_finite(): continue
                column=columns.get(key,{})
                unit=column.get('unit')
                if logic.endswith('_usd') or logic=='exp_12m': unit='USD'
                elif logic.endswith('_pct'): unit='%'
                elif logic.endswith('_pp'): unit='%p'
                elif logic=='rank' or logic.endswith('_rank'): unit='위'
                if not unit or (unit=='%p' and column.get('unit') not in (None,'','%p')): continue
                descriptor=metric(logic,column.get('label') or '',unit,source.get('trade_flow'))
                if unit=='%p' and month and not re.fullmatch(r'\d{4}-(?:0[1-9]|1[0-2])',month):
                    continue
                # 조회·비교 연도 합계를 SQL 식으로 증명한 행의 change_pct는 두 기간의 변화율이다(_annual_rate_proven).
                cell_period=(current if key=='change_pct' and path[:1]==('rows',) and len(path)==2 and not stored
                             and _annual_rate_proven(source) else row_period)
                fact_period=window if logic.startswith('compare_') else cell_period
                fact_compare=None;fact_month=month;horizon=None;role=None
                period_value=not month or logic in PERIOD_AMOUNTS or descriptor.comparison=='PERIOD'
                trend_difference = (key == 'period_difference_usd' and source.get('kind') == 'TREND'
                                    and source.get('_verification_source') == 'SNAPSHOT'
                                    and path[:1] == ('rows',) and len(path) == 2)
                if prediction and logic in ('exp_12m','growth_pct'):
                    fact_period=None;fact_month=None;horizon=12;role='PAST' if logic=='exp_12m' else 'FUTURE'
                elif logic=='compare_amount_usd' and window is None and source.get('kind')=='LLM_SQL':
                    continue   # 생성 SQL의 비교 월(compare_month)은 SQL이 낸 값이라 그 금액의 기간 근거가 되지 않는다.
                elif logic=='compare_amount_usd':
                    fact_month=str(row.get(key.replace('amount_usd','month')) or '')[:7] or None
                    if not fact_month and month and current and window and months_between(*current)==months_between(*window):
                        fact_month=shift(window[0],months_between(current[0],month))
                    if not fact_month: continue
                    fact_period=(fact_month,fact_month)
                elif trend_difference:
                    fact_month, fact_period, fact_compare = None, current, compare
                elif month and (descriptor.family=='AMOUNT_CHANGE' or
                                (unit=='%p' and descriptor.family=='SHARE_CHANGE' and descriptor.comparison=='PERIOD')):
                    fact_period=(month,month)
                    paired=None
                    if current and window and current[0]<=month<=current[1] and months_between(*current)==months_between(*window):
                        paired=shift(window[0],months_between(current[0],month))
                    if unit=='%p' and row.get('compare_month') is not None:
                        # A row-level comparison month must agree with the saved windows, if both exist.
                        recorded=str(row['compare_month'])[:7]
                        consistent=not (current and window) or recorded==paired
                        paired=recorded if consistent and re.fullmatch(r'\d{4}-(?:0[1-9]|1[0-2])',recorded) else None
                    if paired: fact_compare=(paired,paired)
                elif logic in PERIOD_AMOUNTS or descriptor.comparison=='PERIOD':
                    fact_month=None
                    if descriptor.comparison=='PERIOD': fact_period=cell_period;fact_compare=window
                elif month: fact_period=(month,month)
                if not fact_month and fact_period and fact_period[0]==fact_period[1]: fact_month=fact_period[0]
                root_cell = path[:1] == ('rows',) and len(path) == 2
                cell = source.get('_annual_sums', {}).get(key) if root_cell else None
                if cell and unit == 'USD' and not stored:
                    year = cell['year']
                    years = set(re.findall(r'(?<!\d)\d{4}(?!\d)', key + ' ' + descriptor.label))
                    if ((years and years != {year}) or descriptor.flow != cell['flow']
                            or descriptor.family != 'AMOUNT' or descriptor.aggregate == 'AVERAGE'):
                        continue
                    scoped = frozenset(cell[k] for k in ('country_code','country_name','hs_code','hs_name') if cell[k])
                    targets, members = bind_targets(scoped, catalog)
                    window = (year + '-01', year + '-12')
                    facts.append(Fact(index,path+(key,),descriptor,amount,unit,targets,members,
                                      window,None,None,True,False,None,None,None,scoped, annual_sql_sum=True))
                    continue
                difference = source.get('_annual_differences', {}).get(key) if root_cell else None
                if difference and unit == 'USD' and not stored:
                    left, right = difference['left_year'], difference['right_year']
                    years = set(re.findall(r'(?<!\d)\d{4}(?!\d)', key + ' ' + descriptor.label))
                    ordered = re.search(r'(\d{4})년?\s*[-−]\s*(\d{4})년?', descriptor.label)
                    if ((years and years != {left, right}) or
                            (ordered and ordered.groups() != (left, right)) or
                            not descriptor.valid or
                            descriptor.flow != difference['flow']):
                        continue
                    scoped = frozenset(difference[k] for k in ('country_code','country_name','hs_code','hs_name')
                                        if difference[k])
                    targets, members = bind_targets(scoped, catalog)
                    descriptor = replace(descriptor, family='AMOUNT_CHANGE', aggregate=None, comparison='PERIOD')
                    facts.append(Fact(index,path+(key,),descriptor,amount,unit,targets,members,
                                      (left+'-01',left+'-12'),(right+'-01',right+'-12'),None,
                                      True,False,None,None,None,scoped,period_difference=True))
                    continue
                if (root_cell and not stored and not month and unit == 'USD' and descriptor.aggregate != 'AVERAGE' and
                        descriptor.family in ('AMOUNT', 'AMOUNT_CHANGE') and
                        (key in source.get('_annual_candidates', ()) or
                         (source.get('_annual_candidates') is not None and
                          re.search(r'(?<!\d)\d{4}(?!\d)', key + ' ' + descriptor.label)))):
                    # A saved annual SQL cell has no year until its own SQL
                    # projection and source filters prove one. Never inherit
                    # the request's current year for an unproved alias.
                    continue
                facts.append(Fact(index,path+(key,),descriptor,amount,unit,frozenset(identities),group,
                                  fact_period,fact_compare,fact_month,period_value,prediction,horizon,role,anchor,stored,edition,
                                  period_difference=trend_difference))

        walk(source)
    return tuple(facts)
