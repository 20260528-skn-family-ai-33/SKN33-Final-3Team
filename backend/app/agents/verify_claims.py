"""Parse quantitative prose into immutable claims before looking up any numeric facts.

This module knows names and grammar, but never selects a meaning because a value
happens to exist in the result. It is private to verify; Draft/state stay unchanged.
"""
from dataclasses import dataclass, replace
from decimal import Decimal
from functools import lru_cache
import re

NUMBER = r'(?:(?<![\d.A-Za-z])|(?<=[A-Za-z]{3}))(?P<value>[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)'
CURRENCY = r'(?:[가-힣]+\s*)?(?:달러|파운드|프랑|페소|리라|디나르|리얄|크로나|크로네|루피|루블|바트|랜드|동|유로|위안|엔|원)'
MONEY_UNIT = CURRENCY+r'|[A-Z]{3}(?![A-Za-z])|US\$|[$€£¥₩]'
UNIT = r'%p|%|'+MONEY_UNIT+r'|배|위|개|건|kg'
TOKEN = re.compile(NUMBER+r'\s*(?P<scale>[만억조])?\s*(?P<unit>'+UNIT+r')?', re.I)
DATE = re.compile(r'(?<!\d)(\d{4})\s*(?:년\s*(?:(\d{1,2})\s*월)?|-(\d{2})(?:-\d{2})?)|(\d{1,3})\s*(개월|월)')
COMPOUND = re.compile(r'(?<![\d.])(?P<sign>[+-]?)(?P<eok>\d[\d,]*)\s*억\s*(?P<man>\d[\d,]*)\s*만')
SCALE = {None:1,'만':10**4,'억':10**8,'조':10**12}
CHANGE_KEYS = frozenset({'change_pct','yoy_pct','mom_pct','growth_pct'})
YEAR_AGO = r'(?:전년(?:도)?|작년|지난\s*해)'
MONTH_AGO = r'(?:전월|지난\s*달)'
COMPARISON = re.compile(r'(?P<yoy>'+YEAR_AGO+r')\s*(?:동기\s*)?(?:대비|보다)|(?P<mom>'+MONTH_AGO+r')\s*(?:대비|보다)')
DOWN_WORDS, UP_WORDS = r'감소|줄|하락|축소|떨어|낮|내려|내린', r'증가|늘|상승|확대|성장|올라|오르|높|올린'
CHANGE = re.compile(r'\s*(?:가|이|만큼|나|이나|의|을|를)?\s*(?:(?P<down>'+DOWN_WORDS+r')|(?P<up>'+UP_WORDS+r'))')
DOWN = re.compile(r'감소|줄어|줄었|줄고|하락|축소|떨어|내려|내린')
UP = re.compile(r'증가|늘어|늘었|늘고|상승|확대|성장|올라|오르|올린')
DIRECTION = re.compile(r'과거|지난|직전|최근|앞선|향후|앞으로|미래|다가올|다음|예측|예상|전망')
PAST_WORDS = frozenset({'과거','지난','직전','최근','앞선'})
RANGE_JOIN = re.compile(r'\s*(?:부터|[~～–])\s*')
SEPARATOR = re.compile(r'(?<!\d),|,(?!\d)|[;，]|이며|이고|하고|으며|(?:(?<=달러)|(?<=USD)|(?<=[%위배]))로(?=\s)|(?:(?<=%)|(?<=%p)|(?<=[율률중]))\s*(?:와|과|및|그리고)\s*')
METRIC = re.compile(r'(?:수출|수입)\s*(?:점유율|비중|증감률|증가율|감소율|변동률)|월평균|총액|합계|점유율|비중|증감률|증가율|감소율|변동률|수출액|수입액|교역액|금액|수출입액|순위')
NAME_EDGE = r'(?<![가-힣A-Za-z0-9])'
COUNTRY_EDGE = r'(?:(?<![가-힣A-Za-z0-9])|(?<=(?<![가-힣])대))'
COUNT_BEFORE = re.compile(r'(?:상위|하위)\s*$')
COUNT_AFTER = re.compile(r'\s*(?:곳|개국|국|개?\s*(?:국가|품목|시장))')
HS_REFERENCE = re.compile(r'(?<![A-Za-z0-9])HS\s*(?:코드\s*)?(\d{2,}[A-Za-z0-9]*(?:[-._][A-Za-z0-9]+)*)', re.I)
MODEL_END = r'(?![A-Za-z0-9]|[-._]\w)'
NAME_JOIN = re.compile(r'\s*(?:과|와|및|·|,)\s*')
THRESHOLD = re.compile(r'\s*(?:을|를|은|는|이|가|의|에)?\s*(?:(?:다소|소폭|조금|크게)\s*)?'
                       r'(미만|초과|이상|이하|넘(?=었|는|은|어|지|진|습니다|는다)|미달)')
THRESHOLD_RELATIONS = {'미만': 'LT', '초과': 'GT', '이상': 'GE', '이하': 'LE', '넘': 'GT', '미달': 'LT'}
NEGATIVE_COPULA = r'(?:아니|아(?=닌|닙))'  # Leave the modifier in "아닌 적" for the scope check.
NEGATED_THRESHOLD = re.compile(r'\s*(?:(?:하|이)?(?:지(?:는|도)?|진)\s*(?:않|못)'
                               r'|(?:이|가|은|는)?\s*' + NEGATIVE_COPULA + ')')
NEGATED_RELATIONS = {'LT': 'GE', 'GT': 'LE', 'GE': 'LT', 'LE': 'GT'}
BOUND_MODIFIER = r'\s*(?:한|했던|하는|인|이었던|은|았던|었던|는|닌)\s*'
NEGATED_PROPOSITION = re.compile(BOUND_MODIFIER + r'(?:것(?:이|은|도)?|건|게)\s*' + NEGATIVE_COPULA)
HISTORICAL_BOUND = re.compile(BOUND_MODIFIER + '적')
RELATIVE_COPULA = r'(?:이었던|였던|인)'
POSTPOSED_TARGET = re.compile(RELATIVE_COPULA + r'(?:\s+(?:국가|품목)(?:는|은)?)?')


@lru_cache(maxsize=4096)
def _name_token(name):
    suffix = r'(?![A-Za-z0-9])' if name.isascii() else ''
    if any(c.isdigit() for c in name):
        suffix = MODEL_END
    return re.compile(re.escape(name) + suffix)


@lru_cache(maxsize=4096)
def _name_pattern(name, country=False):
    return re.compile((COUNTRY_EDGE if country else NAME_EDGE) + _name_token(name).pattern)


def _overlaps(start, end, spans):
    return any(start < b and a < end for a,b in spans)


def shift(month, offset):
    index=int(month[:4])*12+int(month[5:7])-1+offset
    return f'{index//12:04d}-{index%12+1:02d}'


def months_between(start, end):
    return (int(end[:4])-int(start[:4]))*12+int(end[5:7])-int(start[5:7])


def _target_count(text, match):
    if match['scale'] or match['unit'] not in (None,'개'): return False
    return bool(COUNT_BEFORE.search(text,0,match.start()) or COUNT_AFTER.match(text,match.end()))


def _shown(match):
    text=match.group().strip()
    raw=match['value'].replace(',','')
    if match['scale']=='만' and raw.lstrip('+-').isdigit() and abs(int(raw))>=10**4:
        eok,man=divmod(abs(int(raw)),10**4)
        text=('-' if raw.startswith('-') else '')+f'{eok:,}억'+(f' {man:,}만' if man else '')+text[match.end('scale')-match.start():]
    return text[:40]


def _merge_compound(text):
    return COMPOUND.sub(lambda m:f'{m["sign"]}{int(m["eok"].replace(",",""))*10**4+int(m["man"].replace(",",""))}만',text)


def normalize_unit(unit):
    raw=re.sub(r'\s+','',unit or '').lower()
    return {'달러':'USD','미국달러':'USD','미달러':'USD','usd':'USD','us$':'USD','$':'USD',
            '원':'KRW','krw':'KRW','₩':'KRW','유로':'EUR','eur':'EUR','€':'EUR',
            '엔':'JPY','jpy':'JPY','¥':'JPY','위안':'CNY','cny':'CNY','£':'GBP','파운드':'GBP',
            '영국파운드':'GBP','호주달러':'AUD','캐나다달러':'CAD','스위스프랑':'CHF',
            '개':'COUNT','건':'COUNT','kg':'KG','%p':'%p'}.get(raw,raw.upper() or None)


@dataclass(frozen=True)
class Quantity:
    start: int
    end: int
    value: Decimal
    scale: int
    unit: str | None
    explicit_unit: bool
    signed: bool
    shown: str
    valid: bool = True


def quantities(text):
    """Recognize the whole quantity, including units before or in parentheses.

    Dates/citations/names must already be masked with spaces by the caller. All
    quantities survive even if the unit is conflicting or unsupported.
    """
    found=[]
    for match in TOKEN.finditer(text):
        if _target_count(text,match): continue
        start,end=match.span()
        units=[normalize_unit(match['unit'])] if match['unit'] else []
        prefix=re.search(r'(?<![A-Za-z])(?P<unit>'+MONEY_UNIT+r')\s*[(（]?\s*$',text[:start],re.I)
        if prefix and prefix.start()>=(found[-1].end if found else 0):
            start=prefix.start(); units.append(normalize_unit(prefix['unit']))
        suffix=re.match(r'\s*[(（]\s*(?P<unit>'+UNIT+r')\s*[)）]',text[end:],re.I)
        if suffix:
            end+=suffix.end(); units.append(normalize_unit(suffix['unit']))
        units=set(units)
        scale=SCALE[match['scale']]
        unit=next(iter(units)) if len(units)==1 else ('USD' if not units and scale>1 else None)
        shown=text[start:end].strip() if prefix or suffix else _shown(match)
        found.append(Quantity(start,end,Decimal(match['value'].replace(',','')),scale,unit,bool(units),
                              match['value'].startswith(('+','-')),shown[:40],len(units)<=1))
    return tuple(found)


def mask_spans(text, spans):
    chars=list(text)
    for start,end in spans: chars[start:end]=' '*(end-start)
    return ''.join(chars)


def mask_dates(text):
    return DATE.sub(lambda m:' '*len(m.group()),text)


def source_names(value):
    if isinstance(value,list):
        for item in value: yield from source_names(item)
    elif isinstance(value,dict):
        for key,item in value.items():
            if key in ('columns','conditions','meta'): continue
            if key in ('country_name','country_code','product_name','name','label','code','hs_code','hs6') and item:
                yield key,str(item)
            elif isinstance(item,(dict,list)): yield from source_names(item)


@lru_cache(maxsize=4096)
def _quantity_shaped_name(name):
    if not any(c.isdigit() for c in name) or name.isdigit():
        return False
    normalized = _merge_compound(name)
    values = quantities(normalized)
    return (len(values) == 1 and values[0].start == 0 and values[0].end == len(normalized)
            and (values[0].explicit_unit or values[0].scale > 1))


def _quantity_name_role(text, start, end):
    """Use the grammatical slot, independently of which value the snapshot contains."""
    before = text[:start].rstrip(" (（'\"“‘")
    after = text[end:].lstrip(" )）'\"”’")
    if re.search(r'(?:품목|제품|모델)(?:명|코드)?\s*(?:은|는|이|가)?\s*[:：]?\s*$', before):
        return 'target'
    if re.search(r'(?:수출|수입|금액|총액|합계|가격|매출|비중|점유율|증감률)(?:액)?\s*'
                 r'(?:은|는|이|가|으로|로)?\s*[:：]?\s*(?:(?:각각|모두|총|약)\s*)?$', before):
        return 'value'
    if re.match(r'\s*의\s*(?:수출액|수입액|금액|규모)(?:을|를)\s*(?:기록|달성|보[였인])', after):
        return 'value'
    if re.match(r'\s*(?:의\s*)?(?:수출|수입|점유율|비중|증감률|월평균|금액|순위|품목|제품|모델)', after):
        if quantities(after) or re.search(r'확인|조회|참조|품목|제품|모델', after):
            return 'target'
        return 'unresolved'
    if re.search(RELATIVE_COPULA + r'\s*$', before):
        return 'target'
    if re.match(r'(?:은|는|이(?=\s)|가(?=\s))', after):
        if quantities(after) or re.search(r'품목|제품|모델|조회', after):
            return 'target'
        if re.search(r'(?:수출액|수입액|금액|총액|규모)(?:입니다|이다|이었|에\s*해당)', after):
            return 'value'
        return 'unresolved'
    if re.match(RELATIVE_COPULA + r'\s', after) or re.match(r'(?:을|를)?\s*(?:초과|미만|이상|이하|기록)', after):
        return 'value'
    if re.search(r'[은는]\s*$', before) and re.match(r'\s*(?:입니다|이다|였습니다)', after):
        return 'value'
    return 'unresolved'


def _quantity_name_roles(text, hits, countries):
    """Resolve a complete currency-like name and its coordinated neighbors once."""
    dual = {hit for hit in hits if _quantity_shaped_name(hit[0]) and
            not any(a <= hit[1] and hit[2] <= b and (a,b) != hit[1:] for _,a,b in hits)}
    if not dual:
        return {}
    syntax = mask_spans(text, [(a,b) for name,a,b in hits if name in countries])
    roles = {hit: _quantity_name_role(syntax, *hit[1:]) for hit in dual}
    # In "각각 USD200과 USD100", either, both or neither amount can be in the
    # product dictionary. An unambiguous neighboring amount supplies the role too.
    anchors = {hit[1:]: roles.get(hit, 'target') for hit in hits}
    for q in quantities(syntax):
        if not _overlaps(q.start, q.end, anchors):
            anchors[q.start, q.end] = 'value'
    ordered = sorted(anchors)
    for indexes in (ordered, list(reversed(ordered))):
        for first, second in zip(indexes, indexes[1:]):
            left, right = sorted((first, second))
            if anchors[second] == 'unresolved' and NAME_JOIN.fullmatch(text[left[1]:right[0]]):
                anchors[second] = anchors[first]
    return {hit: anchors[hit[1:]] for hit in dual}


class NameCatalog:
    def __init__(self, sources, known_names=(), countries=()):
        fields=list(source_names(sources))
        self.countries=frozenset(countries)|{name for key,name in fields if key.startswith('country_')}
        for source in sources:
            if source.get('group')=='COUNTRY':
                self.countries |= {name for key,name in source_names(source.get('top',[])) if key in ('name','code')}
        self.names=frozenset(n for n in known_names if n and len(n)>1)|{n for _,n in fields if len(n)>1}
        stored = {name for _, name in fields}
        products = {name for key, name in fields if key in ('product_name', 'name', 'label')} - self.countries
        candidates = self.names - stored - self.countries
        self.aliases = {}
        for short in candidates:
            owners = [name for name in products if short in name
                      and (not any(c.isdigit() for c in short) or _name_pattern(short).search(name))]
            if len(owners) == 1:
                self.aliases.setdefault(owners[0], set()).add(short)

    def resolve_mentions(self, text):
        # Identify whole stored names first. A model number, date, unit or amount
        # embedded in that name must not erase the identifier before it is found.
        value=text
        possible={name for name in self.names if name in value}
        hits={(name,m.start(),m.end()) for name in possible for m in _name_pattern(name,name in self.countries).finditer(value)}
        pending=list(hits)
        for _,_,end in pending:
            join=re.compile(r'(?:과|와|및)\s*').match(value,end)
            if not join: continue
            for name in possible:
                start=join.end(); after=start+len(name)
                if not _name_token(name).match(value, start): continue
                hit=(name,start,after)
                if hit not in hits: hits.add(hit);pending.append(hit)
        explicit_codes = set()
        for match in HS_REFERENCE.finditer(text):
            code = match[1]
            if match.group().upper() in ('HS2017', 'HS2022'):
                continue  # Edition labels used by the trade data.
            if len(code) < 4 and code not in self.names:
                continue  # For example, "HS10 기준" specifies a classification level.
            if match.group() in self.names and code not in self.names:
                continue  # A full stored product name already identifies this span.
            start, end = match.span()
            hits = {hit for hit in hits if not (start <= hit[1] and hit[2] <= end)}
            explicit_codes.add((code, start, end))
        hits.update(explicit_codes)
        # A longer identifier owns its interior, even if it contains a currency code.
        roles = _quantity_name_roles(text, hits, self.countries)
        value_spans = [hit[1:] for hit, role in roles.items() if role != 'target']
        if value_spans:
            hits = {hit for hit in hits if not _overlaps(*hit[1:], value_spans)}
        protected = [(a,b) for name,a,b in hits if (name,a,b) in explicit_codes or
                     (name not in self.countries and not name.isdigit())]
        outside = mask_spans(value, protected)
        blocked = [m.span() for m in DATE.finditer(outside)]
        outside_values = quantities(mask_dates(outside))
        each = re.search(r'각각|모두', outside)
        for q in outside_values:
            shared_unit = each and each.end() <= q.start and outside_values[-1].unit is not None
            if q.explicit_unit or q.scale > 1 or shared_unit:
                blocked.append((q.start, q.end))
        hits = {h for h in hits if not _overlaps(h[1],h[2],blocked)}
        # A coordinated label names several members; it is not an alias of either member.
        composite=set()
        for name,start,end in hits:
            contained=[h for h in hits if h[0]!=name and start<=h[1] and h[2]<=end]
            remainder=mask_spans(value,[(a,b) for _,a,b in contained])[start:end]
            if len({n for n,_,_ in contained})>1 and re.fullmatch(r'[\s과와및,·ㆍ]+',remainder): composite.add(name)
        hits={h for h in hits if h[0] not in composite}
        mentions = sorted((h for h in hits if not any(h[0]!=other[0] and other[1]<=h[1] and h[2]<=other[2] for other in hits)),key=lambda h:h[1])
        return mentions, 'unresolved' in roles.values()

    def mentions(self, text):
        return self.resolve_mentions(text)[0]

    def names_in(self,text): return frozenset(n for n,_,_ in self.mentions(text))

    def merge(self,inherited,mentioned):
        country=mentioned & self.countries; product=mentioned-self.countries
        return frozenset((country or inherited & self.countries)|(product or inherited-self.countries))

    def grouped(self,names):
        return len(names & self.countries)>1 or len(names-self.countries)>1


def prepare_sentence(sentence, catalog, *, mentions=None):
    """Normalize amounts/citations only outside recognized identifier spans."""
    if mentions is None:
        mentions = catalog.mentions(sentence)
    spans=[(a,b) for _,a,b in mentions]
    text=re.sub(r'\[\d+\]',lambda m:m.group() if _overlaps(*m.span(),spans) else ' '*len(m.group()),sentence)
    return COMPOUND.sub(lambda m:m.group() if _overlaps(*m.span(),spans) else _merge_compound(m.group()),text)


def quantity_text(sentence, catalog):
    text=prepare_sentence(sentence,catalog)
    return mask_dates(mask_spans(text,[(a,b) for _,a,b in catalog.mentions(text)]))


@dataclass(frozen=True)
class TimeClaim:
    years: frozenset[str] = frozenset()
    months: frozenset[str] = frozenset()
    bare_months: frozenset[int] = frozenset()
    durations: frozenset[int] = frozenset()
    period: tuple[str,str] | None = None
    anchor_reference: bool = False
    comparison_reference: bool = False


@dataclass(frozen=True)
class MetricClaim:
    family: str | None = None
    aggregate: str | None = None
    comparison: str | None = None
    flow: str | None = None
    direction: str | None = None
    direct_change: bool = False
    prediction_role: frozenset[str] = frozenset()
    rank_axis: str | None = None
    valid: bool = True
    relation: str = 'EQ'


@dataclass(frozen=True)
class Claim:
    quantity: Quantity
    targets: frozenset[str]
    target_scope: str
    metric: MetricClaim
    time: TimeClaim
    text: str
    valid: bool = True


@dataclass(frozen=True)
class ParsedSentence:
    claims: tuple[Claim,...]
    date_only: tuple[TimeClaim,...] = ()
    valid: bool = True


@dataclass(frozen=True)
class Context:
    targets: frozenset[str] = frozenset()
    year: str | None = None
    flow: str | None = None
    time: TimeClaim = TimeClaim()
    metric: MetricClaim | None = None


def read_time(text, inherited_year=None):
    dates=list(DATE.finditer(text)); years=set();months=set();bare=set();durations=set()
    current_year=inherited_year
    for date in dates:
        year,m1,m2,count,kind=date.groups()
        if year:
            current_year=year
            if m1 or m2: months.add(f'{year}-{int(m1 or m2):02d}')
            else: years.add(year)
        elif kind=='개월': durations.add(int(count))
        elif current_year: months.add(f'{current_year}-{int(count):02d}')
        else: bare.add(int(count))
    period=None
    if len(dates)==2 and RANGE_JOIN.fullmatch(text[dates[0].end():dates[1].start()]):
        first,last=dates
        if first.group(1) and last.group(1) and not (first.group(2) or first.group(3) or last.group(2) or last.group(3)):
            period=(first.group(1)+'-01',last.group(1)+'-12')
        elif first.group(1) and (first.group(2) or first.group(3)):
            end_month=last.group(2) or last.group(3) or (last.group(4) if last.group(5)=='월' else None)
            if end_month: period=(f'{first.group(1)}-{int(first.group(2) or first.group(3)):02d}',f'{last.group(1) or first.group(1)}-{int(end_month):02d}')
    comparison_reference=any(re.match(r'\s*(?:대비|보다)',text[d.end():]) for d in dates)
    return TimeClaim(frozenset(years),frozenset(months),frozenset(bare),frozenset(durations),period,'기준' in text,
                     comparison_reference),current_year


def _threshold_relation(suffix):
    threshold = THRESHOLD.match(suffix)
    if not threshold:
        unclear = re.match(r'\s*(?:을|를|에|보다)\s*(?:[가-힣]+\s+){1,2}(?:초과|미만|이상|이하|미달)', suffix)
        return 'EQ', not unclear
    relation = THRESHOLD_RELATIONS[threshold[1]]
    end = threshold.end()
    # Each negation applies to the predicate immediately before it.
    for negation in (NEGATED_THRESHOLD, NEGATED_PROPOSITION):
        match = negation.match(suffix, end)
        if match:
            relation = NEGATED_RELATIONS[relation]
            end = match.end()
    # A single observation cannot establish a claim about past observations.
    return relation, not HISTORICAL_BOUND.match(suffix, end)


def metric_claim(text,quantity,*,direct_suffix='',flow=None):
    share=bool(re.search(r'비중|점유율',text));change=bool(re.search(r'증감률|증가율|감소율|변동률',text))
    family='SHARE' if share else 'CHANGE' if change else None
    aggregate='AVERAGE' if '월평균' in text else 'TOTAL' if re.search(r'총액|합계',text) else None
    if aggregate: family='AMOUNT'
    if re.search(r'수출액|수입액|교역액|금액',text) and quantity.unit=='USD': family='AMOUNT'
    comparison=None
    relation=COMPARISON.search(text)
    if relation: comparison='YOY' if relation['yoy'] else 'MOM'
    elif change:
        if re.search(YEAR_AGO,text): comparison='YOY'
        elif re.search(MONTH_AGO,text): comparison='MOM'
    flows={'EXPORT' for _ in re.finditer('수출',text)}|{'IMPORT' for _ in re.finditer('수입',text)}
    direction=None;direct=False
    after=CHANGE.match(direct_suffix) if quantity.unit in ('%','%p','USD') else None
    if after: direction='DOWN' if after['down'] else 'UP';direct=True
    elif quantity.unit in ('%','%p'):
        down,up=bool(DOWN.search(text)),bool(UP.search(text))
        if down!=up: direction='DOWN' if down else 'UP'
    if direction and quantity.unit=='%' and not share: family='CHANGE'
    if direct and quantity.unit=='USD': family='AMOUNT_CHANGE'
    if quantity.unit=='USD' and re.search(r'증감액|증가액|감소액|차액',text): family='AMOUNT_CHANGE'
    if quantity.unit=='%p' and share: family='SHARE_CHANGE'
    temporal=re.sub(r'(?:과거|지난|직전|최근|앞선)\s*(?:대비|기준월)\s*','',text)
    roles=frozenset('PAST' if word in PAST_WORDS else 'FUTURE' for word in DIRECTION.findall(temporal))
    rank_axis=None
    for label,axis in (('품목','country_rank'),('국가','product_rank')):
        if re.search(label+r'(?:별|\s*(?:중|내|안))?\s*순위',text): rank_axis=axis
    relation, bound_valid = _threshold_relation(direct_suffix)
    valid = not (share and change) and len(flows) < 2 and bound_valid
    return MetricClaim(family,aggregate,comparison,next(iter(flows)) if len(flows)==1 else flow,
                       direction,direct,roles,rank_axis,valid,relation)


def _introduces_target(quantity, link):
    link = link.strip()
    return bool(POSTPOSED_TARGET.fullmatch(link)) or (quantity.unit == '위' and link in ('', '는'))


def _postposed_boundaries(text, values, mentions):
    """End the first assertion *after* its subject, keeping the next predicate intact."""
    cuts = []
    for first, second in zip(values, values[1:]):
        between = [(a, b) for _, a, b in mentions if first.end <= a and b <= second.start]
        if not between:
            continue
        if not _introduces_target(first, text[first.end:between[0][0]]):
            continue
        # Keep the whole relative-clause target, including "미국의 반도체".
        end = between[0][1]
        for start, next_end in between[1:]:
            if not re.fullmatch(r'\s*(?:의\s*)?', text[end:start]):
                break
            end = next_end
        join = re.match(r'\s*(?:과|와|및|그리고|[,;，·]|이고|이며)\s*', text[end:second.start])
        cuts.append(end + join.end() if join else end)
    return cuts


def _listed_targets(text, mentions, targets, catalog):
    """Keep a common country/product, or read explicit pairs in their written order."""
    countries = targets & catalog.countries
    products = targets - countries
    if len(countries) > 1 and len(products) > 1:
        pairs = []
        current = set()
        end = 0
        for name, start, after in mentions:
            if current and NAME_JOIN.fullmatch(text[end:start]):
                pairs.append(current)
                current = set()
            if current and not re.fullmatch(r'\s*(?:의\s*)?', text[end:start]):
                return []
            current.add(name)
            end = after
        pairs.append(current)
        if any(len(pair & countries) != 1 or len(pair & products) != 1 for pair in pairs):
            return []
        return pairs
    varied = countries if len(countries) > 1 else products
    ordered = list(dict.fromkeys(name for name, _, _ in mentions if name in varied))
    return [(targets - varied) | {name} for name in ordered] if set(ordered) == varied else []


def parse_sentence(sentence,catalog):
    original_mentions, ambiguous = catalog.resolve_mentions(sentence)
    text = prepare_sentence(sentence, catalog, mentions=original_mentions)

    def parse(text,context=Context()):
        mentions, ambiguous_name = catalog.resolve_mentions(text)
        names=frozenset(n for n,_,_ in mentions)
        targets=catalog.merge(context.targets,names)
        syntax=mask_spans(text,[(a,b) for _,a,b in mentions])
        clean=mask_dates(syntax)
        values=quantities(clean)
        time,last_year=read_time(syntax,context.year)
        if not DATE.search(syntax): time=context.time
        # A numberless fragment may supply context, but never erases a target/date assertion.
        if not values: return ParsedSentence((),(time,) if DATE.search(syntax) else (),not ambiguous_name),replace(context,targets=targets,year=last_year,time=time)
        flows=set(re.findall(r'수출|수입',syntax))
        common_flow=({'수출':'EXPORT','수입':'IMPORT'}[next(iter(flows))] if len(flows)==1 else context.flow)
        dates=list(DATE.finditer(syntax))
        points=[m for m in dates if m.group(1) or m.group(5)=='월']
        date_starts=[m for i,m in enumerate(points) if not i or not RANGE_JOIN.fullmatch(text[points[i-1].end():m.start()])]
        # Split only complete assertions, keeping a range and its two endpoints together.
        cuts = _postposed_boundaries(text, values, mentions)
        if not cuts and len(date_starts)>1 and not time.period:
            cuts=[m.start() for m in date_starts[1:] if any(q.end<=m.start() for q in values)]
        if not cuts:
            for _,start,_ in mentions:
                previous=next((q for q in reversed(values) if q.end<=start),None)
                if previous and not _introduces_target(previous, text[previous.end:start]) and any(q.start>start for q in values):
                    cuts.append(start)
        if not cuts:
            # Prediction direction and ordinary punctuation can introduce a new assertion.
            for boundary in SEPARATOR.finditer(syntax):
                if any(q.end<=boundary.start() for q in values) and any(q.start>=boundary.end() for q in values):
                    # Values of an upfront '각각' list share the prefix and must stay together.
                    if not re.search(r'각각|모두',syntax[:values[0].start]): cuts.append(boundary.end())
            for boundary in re.finditer(r'(?:됐고|었고|았고|였고|(?<=달러)고)\s*(?='+DIRECTION.pattern+r')',syntax):
                if any(q.end<=boundary.start() for q in values): cuts.append(boundary.end())
        if cuts:
            cuts=sorted({0,*cuts,len(text)})
            parts=[text[a:b] for a,b in zip(cuts,cuts[1:])]
            # Context comes from the preceding assertion, not all later names/dates.
            collected=[];date_only=[];valid=not ambiguous_name
            for part in parts:
                parsed,context=parse(part,context)
                collected.extend(parsed.claims);date_only.extend(parsed.date_only);valid &= parsed.valid
            return ParsedSentence(tuple(collected),tuple(date_only),valid),context

        each=re.search(r'각각|모두',syntax[:values[0].start])
        if each and values[-1].unit:
            # Bind a shared unit before deriving amount/change or inheriting a metric.
            values = tuple(replace(q, unit=values[-1].unit) if q.unit is None else q for q in values)
        metric_mentions=list(METRIC.finditer(syntax))
        prefix_metrics=[m for m in metric_mentions if m.end()<=values[0].start]
        # '수출액의 점유율' has one metric; a coordinated list needs explicit separators.
        metric_list=(each and len(prefix_metrics)>1 and all(re.fullmatch(r'\s*(?:과|와|및|,|·)\s*',text[a.end():b.start()])
                     for a,b in zip(prefix_metrics,prefix_metrics[1:])))
        variants=[]
        ordered_times=[]
        if each and len(points)>1 and not time.period:
            inherited_year=context.year
            for point in points:
                scoped,inherited_year=read_time(point.group(),inherited_year)
                ordered_times.append(scoped)
        if metric_list:
            if len(prefix_metrics)!=len(values): return ParsedSentence((),valid=False),context
            for quantity,mention in zip(values,prefix_metrics):
                variants.append((quantity,targets,'GROUP' if catalog.grouped(targets) else 'SINGLE',mention.group()))
        elif ordered_times and not catalog.grouped(targets):
            if len(values) not in (1,len(ordered_times)): return ParsedSentence((),valid=False),context
            variants=[(values[0] if len(values)==1 else values[i],targets,'SINGLE',None) for i in range(len(ordered_times))]
        elif each and catalog.grouped(targets):
            ordered = _listed_targets(text, mentions, targets, catalog)
            if not ordered or len(values) not in (1, len(ordered)):
                return ParsedSentence((),valid=False),context
            for i, member in enumerate(ordered):
                variants.append((values[0] if len(values)==1 else values[i], member, 'EACH', None))
        else:
            variants=[(q,targets,'GROUP' if catalog.grouped(targets) else 'SINGLE',None) for q in values]

        claims=[]
        for variant_index,(quantity,bound_targets,target_scope,metric_override) in enumerate(variants):
            left=max((m.end() for m in SEPARATOR.finditer(syntax,0,quantity.start)),default=0)
            right=next((m.start() for m in SEPARATOR.finditer(syntax,quantity.end)),len(text))
            local=text[left:right]
            metric_local=syntax[left:right]
            if metric_override:
                # Preserve common comparison/flow, but never another listed metric's direction.
                relation=COMPARISON.search(syntax[:values[0].start])
                local=metric_override+(' '+relation.group() if relation and re.search('률|율',metric_override) and not re.search('점유율|비중',metric_override) else '')
                metric_local=local
            metric=metric_claim(metric_local,quantity,direct_suffix=syntax[quantity.end:right],flow=common_flow)
            if not metric.family and context.metric:
                compatible={'%':{'SHARE','CHANGE','SHARE_CHANGE'},'%p':{'SHARE_CHANGE'},'USD':{'AMOUNT','AMOUNT_CHANGE'}}
                if context.metric.family in compatible.get(quantity.unit,set()):
                    metric=replace(metric,family=context.metric.family,aggregate=context.metric.aggregate,
                                   comparison=metric.comparison or context.metric.comparison)
            if metric_override and metric_override in ('수출액','수입액'):
                metric=replace(metric,flow='EXPORT' if metric_override=='수출액' else 'IMPORT',family='AMOUNT')
            if quantity.unit=='USD' and not metric.family:
                metric=replace(metric,family='AMOUNT')
            claim_time=ordered_times[variant_index] if ordered_times and not metric_list and not catalog.grouped(targets) else time
            previous=next((q for q in reversed(values) if q.end<=quantity.start),None)
            if metric.family=='CHANGE' and previous and re.search(r'보다|대비',text[previous.end:quantity.start]):
                claim_time=replace(claim_time,comparison_reference=True)
            claims.append(Claim(quantity,frozenset(bound_targets),target_scope,metric,claim_time,local,quantity.valid and metric.valid))
        return ParsedSentence(tuple(claims),valid=not ambiguous_name),Context(targets,last_year,common_flow,time,claims[-1].metric)

    parsed, _ = parse(text)
    return replace(parsed, valid=False) if ambiguous else parsed
