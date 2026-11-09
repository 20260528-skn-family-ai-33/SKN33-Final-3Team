"""Typed, provenance-preserving evidence for verify. Never fetch or recompute data."""
from dataclasses import dataclass
from decimal import Decimal
import re

from backend.app.agents.verify_claims import NameCatalog, months_between, shift, YEAR_AGO, MONTH_AGO

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
    value: Decimal
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


def period(value):
    if not isinstance(value,dict): return None
    start,end=(str(value.get(k,''))[:7] for k in ('start','end'))
    return (start,end) if all(re.fullmatch(r'\d{4}-(?:0[1-9]|1[0-2])',p) for p in (start,end)) and start<=end else None


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
        aggregate='AVERAGE' if 'monthly_avg' in key or '월평균' in label else 'TOTAL' if key in PERIOD_AMOUNTS or re.search(r'총액|합계',label) else None
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


def build_facts(sources,catalog:NameCatalog):
    facts=[]
    for index,source in enumerate(sources):
        if source.get('_source_unavailable'): continue
        columns={c['key']:c for c in source.get('columns',[]) if isinstance(c,dict) and 'key' in c}
        current=period(source.get('period'));compare=period(source.get('compare_period'))
        prediction=source.get('kind')=='PREDICTION'
        anchor=str(source.get('anchor') or '')[:7] or None

        def walk(row,targets=frozenset(),members=frozenset(),inherited_month=None,path=()):
            if isinstance(row,list):
                for i,item in enumerate(row): walk(item,targets,members,inherited_month,path+(str(i),))
                return
            if not isinstance(row,dict): return
            names={str(row[k]) for k in ('name','label','code','product_name','country_name','country_code','hs_code','hs6') if row.get(k)}
            identities=targets|names
            declared=set()
            for name in names:
                parts=catalog.names_in(name)
                if catalog.grouped(parts): declared.update(parts)
                identities |= catalog.aliases.get(name,frozenset())
            group=members|frozenset(declared) if catalog.grouped(frozenset(declared)) else members
            identities |= group
            month=str(row.get('month') or row.get('trade_month') or inherited_month or '')[:7] or None
            for key,value in row.items():
                if isinstance(value,(dict,list)):
                    walk(value,frozenset(identities),group,month,path+(key,));continue
                if not isinstance(value,(float,int,Decimal)) or isinstance(value,bool): continue
                amount=Decimal(str(value))
                if not amount.is_finite(): continue
                column=columns.get(key,{})
                unit=column.get('unit')
                if key.endswith('_usd') or key=='exp_12m': unit='USD'
                elif key.endswith('_pct'): unit='%'
                elif key.endswith('_pp'): unit='%p'
                elif key=='rank' or key.endswith('_rank'): unit='위'
                if not unit: continue
                descriptor=metric(key,column.get('label') or '',unit,source.get('trade_flow'))
                fact_period=compare if key.startswith('compare_') else current
                fact_compare=None;fact_month=month;horizon=None;role=None
                period_value=not month or key in PERIOD_AMOUNTS or descriptor.comparison=='PERIOD'
                if prediction and key in ('exp_12m','growth_pct'):
                    fact_period=None;fact_month=None;horizon=12;role='PAST' if key=='exp_12m' else 'FUTURE'
                elif key=='compare_amount_usd':
                    fact_month=str(row.get('compare_month') or '')[:7] or None
                    if not fact_month and month and current and compare and months_between(*current)==months_between(*compare):
                        fact_month=shift(compare[0],months_between(current[0],month))
                    if not fact_month: continue
                    fact_period=(fact_month,fact_month)
                elif key in PERIOD_AMOUNTS or descriptor.comparison=='PERIOD':
                    fact_month=None
                    if descriptor.comparison=='PERIOD': fact_period=current;fact_compare=compare
                elif month: fact_period=(month,month)
                if not fact_month and fact_period and fact_period[0]==fact_period[1]: fact_month=fact_period[0]
                facts.append(Fact(index,path+(key,),descriptor,amount,unit,frozenset(identities),group,
                                  fact_period,fact_compare,fact_month,period_value,prediction,horizon,role,anchor))

        walk(source)
    return tuple(facts)
