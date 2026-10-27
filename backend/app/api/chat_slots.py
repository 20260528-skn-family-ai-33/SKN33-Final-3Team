"""Validate LLM slots against server-owned reference data and published periods."""
import re
from datetime import date
from sqlalchemy import select, func
from backend.app.api import trade
from backend.app.api.common import ApiError, period, shift_month


class SlotRepair(Exception):
    pass


def resolve(store, question, previous, slots):
    conditions=dict(previous)
    warnings=[]
    country=store.t('country')
    names={r['name_ko']:r['country_code'] for r in store.rows(select(country).where(country.c.country_role=='COUNTRY'))}
    found=[]; invalid_country=False
    for item in slots.countries:
        mention=item.mention.strip()
        # Require source evidence; arbitrary two-letter abbreviations are not accepted as country names.
        if mention.isascii() and len(mention)==2:
            warnings.append('모호한 두 글자 약어는 국가 조건에서 제외했습니다.')
            continue
        if not mention or mention.casefold() not in question.casefold() or item.name not in names:
            warnings.append('확인할 수 없는 국가 표현을 제외했습니다. 국가명을 입력하세요.')
            invalid_country=True
        else: found.append(names[item.name])
    if slots.all_countries: conditions['country_codes']=[]
    elif found: conditions['country_codes']=sorted(set(found))
    elif invalid_country: raise SlotRepair('국가를 확인하지 못했습니다. 전체 국가명으로 다시 입력해 주세요.')
    conditions.setdefault('country_codes',[])
    conditions.setdefault('hs_items',[])
    if slots.all_products: conditions['hs_items']=[]
    expressions=[]
    explicit_codes=set()
    for group in re.finditer(r'HS\s*(?:코드|CODE)?\s*[:=]?\s*(\d{4}(?:\d{2})?(?!\d)(?:\s*[,/、]\s*\d{4}(?:\d{2})?(?!\d))*)',question,re.I):
        explicit_codes.update(re.findall(r'\d+',group[1]))
    for expression in slots.products:
        expression=expression.strip()
        if not expression or len(expression)>120 or expression.casefold() not in question.casefold():
            raise SlotRepair('품목 표현을 확인하지 못했습니다. 질문에 품목명을 직접 적어 주세요.')
        if expression.isdigit() and expression not in explicit_codes:
            raise SlotRepair('숫자가 연도인지 품목인지 확인이 필요합니다. 품목은 이름 또는 HS 코드로 명시해 주세요.')
        expressions.append(expression)
    article_mode=slots.intent=='LOCAL_ISSUES'
    if article_mode:
        a=store.t('article')
        latest=store.c.scalar(select(func.max(a.c.published_date)).where(a.c.visibility_status=='VISIBLE'))
        if latest is None: raise SlotRepair('조회 가능한 기사 데이터가 아직 없습니다.')
    else:
        try: latest=trade.bounds(store)[-1]
        except ApiError as exc: raise SlotRepair(exc.message) from exc

    def validated(value):
        try:
            start,end=date.fromisoformat(value['start']),date.fromisoformat(value['end'])
            if start>end or end.replace(day=1)>latest.replace(day=1): raise ValueError('period')
            if not article_mode: trade.resolve_period(store,start.strftime('%Y-%m'),end.strftime('%Y-%m'))
            return period(start.replace(day=1),end.replace(day=1))
        except (ValueError,KeyError,TypeError,ApiError) as exc:
            raise SlotRepair('요청 기간을 공개 데이터에서 확인하지 못했습니다. 조회 가능한 연월로 다시 입력해 주세요.') from exc

    if slots.period: conditions['period']=validated(slots.period.model_dump())
    elif slots.recent_months is not None or not conditions.get('period'):
        count=slots.recent_months if slots.recent_months is not None else (3 if article_mode else 12)
        if not 1<=count<=120: raise SlotRepair('조회 기간은 1~120개월로 입력해 주세요.')
        conditions['period']=validated(period(shift_month(latest,-count+1),latest))
    else: conditions['period']=validated(conditions['period'])
    if slots.compare_period:
        # Fixed statistics queries currently support the matching prior-year period only.
        comparison=validated(slots.compare_period.model_dump())
        p=conditions['period']
        expected=period(shift_month(date.fromisoformat(p['start']),-12),shift_month(date.fromisoformat(p['end']).replace(day=1),-12))
        if comparison!=expected: raise SlotRepair('현재는 같은 기간의 전년 대비를 지원합니다. 비교할 조회 기간을 다시 입력해 주세요.')
        conditions['compare_period']=comparison
    elif slots.period or slots.recent_months is not None:
        conditions['compare_period']=None
    if slots.intent=='RANKING':
        group=slots.ranking_group or conditions.get('ranking_group')
        if group not in ('COUNTRY','HS6'):
            raise SlotRepair('국가 순위와 품목 순위 중 어떤 순위를 조회할지 알려 주세요.')
        conditions['ranking_group']=group
    conditions['trade_flow']=slots.trade_flow or conditions.get('trade_flow','EXPORT')
    if slots.intent=='MARKET_SHARE' and conditions['trade_flow']=='IMPORT':
        raise SlotRepair('시장 비중은 현재 수출 기준으로 제공합니다. 수출 비중으로 다시 요청해 주세요.')
    conditions.setdefault('compare','YOY_PERIOD'); conditions.setdefault('metric','AMOUNT'); conditions.setdefault('compare_period',None)
    return conditions,warnings,expressions
