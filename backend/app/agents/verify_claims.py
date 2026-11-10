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
COMPARE_LINK = r'(?:대비|보다|에\s*비(?:해|하여|하면)|(?:와|과|에)\s*비교(?:해|하여|하면|했을\s*때))'
COMPARISON = re.compile(r'(?:(?P<yoy>'+YEAR_AGO+r')\s*(?:(?:동기|동월|같은\s*달)\s*)?|'
                        r'(?P<mom>'+MONTH_AGO+r')\s*)'+COMPARE_LINK)
COMPARE_OPERATOR = re.compile(COMPARE_LINK)
EXTREME_DOWN = r'급감|급락|곤두박질'
EXTREME_UP = r'급증|급등|치솟'
DOWN_WORDS, UP_WORDS = r'감소|줄어들|줄|하락|축소|떨어|낮|내려|내린', r'증가|늘어나|늘|상승|확대|성장|올라|오르|높|올린'
CHANGE = re.compile(r'\s*(?:가|이|만큼|나|이나|의|을|를)?\s*(?:(?P<negative>안|못)\s*)?(?:(?P<down>'+DOWN_WORDS+'|'+EXTREME_DOWN+r')|(?P<up>'+UP_WORDS+'|'+EXTREME_UP+r'))')
DOWN = re.compile(EXTREME_DOWN + r'|감소|줄어|줄었|줄고|하락|축소|떨어|내려|내린')
UP = re.compile(EXTREME_UP + r'|증가|늘어|늘었|늘고|상승|확대|성장|올라|오르|올린')
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


def _contained_names(text, names, lengths):
    """Exact substring candidates; scan the smaller of dictionary and text windows."""
    if len(text) * len(lengths) >= len(names):
        return {name for name in names if name in text}
    return {part for size in lengths for i in range(len(text) - size + 1)
            if (part := text[i:i + size]) in names}


class NameCatalog:
    def __init__(self, sources, known_names=(), countries=(), *, mention_cache=None):
        self._row_bindings = {}
        fields=list(source_names(sources))
        self.countries=frozenset(countries)|{name for key,name in fields if key.startswith('country_')}
        for source in sources:
            if source.get('group')=='COUNTRY':
                self.countries |= {name for key,name in source_names(source.get('top',[])) if key in ('name','code')}
        self.names=frozenset(n for n in known_names if n and len(n)>1)|{n for _,n in fields if len(n)>1}
        self._lengths = {len(name) for name in self.names}
        # Mention spans depend on the vocabulary and country roles. Alias
        # ownership and row bindings still belong to each individual catalog.
        self._mentions = ({} if mention_cache is None else
                          mention_cache.setdefault((self.names, self.countries), {}))
        stored = {name for _, name in fields}
        products = {name for key, name in fields if key in ('product_name', 'name', 'label')} - self.countries
        candidates = self.names - stored - self.countries
        self.aliases = {}
        if candidates:
            lengths = {len(name) for name in candidates}
            owners = {}
            for product in products:
                for short in _contained_names(product, candidates, lengths):
                    if any(c.isdigit() for c in short) and not _name_pattern(short).search(product):
                        continue
                    owners[short] = None if short in owners else product
            for short, owner in owners.items():
                if owner is not None:
                    self.aliases.setdefault(owner, set()).add(short)

    def resolve_mentions(self, text):
        if text not in self._mentions:
            self._mentions[text] = self._resolve_mentions(text)
        return self._mentions[text]

    def _resolve_mentions(self, text):
        # Identify whole stored names first. A model number, date, unit or amount
        # embedded in that name must not erase the identifier before it is found.
        value=text
        possible = _contained_names(value, self.names, self._lengths)
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
        return tuple(mentions), 'unresolved' in roles.values()

    def mentions(self, text):
        return self.resolve_mentions(text)[0]

    def names_in(self,text): return frozenset(n for n,_,_ in self.mentions(text))

    def row_binding(self, name):
        """Aliases and grouped names for one stored identifier, within this catalog only."""
        if name not in self._row_bindings:
            # A group needs two distinct identifiers. Explicit HS references
            # can contribute identifiers outside the dictionary.
            possible = _contained_names(name, self.names, self._lengths)
            parts = (self.names_in(name) if len(possible) >= 2 or HS_REFERENCE.search(name)
                     else frozenset())
            members = parts if self.grouped(parts) else frozenset()
            self._row_bindings[name] = (self.aliases.get(name, ()), members)
        return self._row_bindings[name]

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
    amount_period: str | None = None
    negated_change: bool = False


@dataclass(frozen=True)
class Claim:
    quantity: Quantity | None
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


def _change_predicate(suffix):
    # In "20% 이상 감소", both the bound and the change belong to the same value.
    bound = THRESHOLD.match(suffix)
    return CHANGE.match(suffix, bound.end() if bound else 0)


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
    after=_change_predicate(direct_suffix) if quantity.unit in ('%','%p','USD') else None
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


def _scope_measure(metric,quantity,prefix,suffix,label,*,shared,multiple):
    """Bind comparison and level periods within a single numeric slot."""
    if quantity.unit == 'USD':
        delta_label = re.search(r'증감액|증가액|감소액|차액', label)
        if metric.direct_change or delta_label:
            direction = metric.direction
            if not direction and delta_label:
                direction = {'증가액': 'UP', '감소액': 'DOWN'}.get(delta_label.group())
            metric = replace(metric, family='AMOUNT_CHANGE', direction=direction,
                             direct_change=metric.direct_change or direction is not None)
        else:
            # A comparison modifies a change; a bare relative date names a level.
            level_prefix = COMPARISON.sub('', prefix)
            amount_period = 'AUTO'
            if re.search(YEAR_AGO, level_prefix):
                amount_period = 'YOY'
            elif re.search(MONTH_AGO, level_prefix):
                amount_period = 'MOM'
            elif re.search(r'비교\s*기', level_prefix):
                amount_period = 'COMPARE'
            elif re.search(r'당기|현재|이번\s*달|올해', level_prefix):
                amount_period = 'CURRENT'
            return replace(metric, family='AMOUNT', comparison=None, amount_period=amount_period)
    if quantity.unit == '%' and not metric.family and COMPARE_OPERATOR.search(prefix):
        metric = replace(metric, family='CHANGE')
    if metric.family in ('AMOUNT_CHANGE', 'CHANGE', 'SHARE_CHANGE'):
        predicate = _change_predicate(suffix)
        if predicate:
            end = predicate.end()
            negated = bool(predicate['negative'])
            for pattern in (NEGATED_THRESHOLD, NEGATED_PROPOSITION):
                match = pattern.match(suffix, end)
                if match:
                    negated = not negated
                    end = match.end()
            metric = replace(metric, negated_change=negated,
                             valid=metric.valid and not HISTORICAL_BOUND.match(suffix, end))
        markers = list(COMPARISON.finditer(prefix))
        axes = {'YOY' if match['yoy'] else 'MOM' for match in markers}
        rest = COMPARISON.sub('', prefix)
        # A date or preceding numeric operand can be bound separately by read_time.
        for operator in COMPARE_OPERATOR.finditer(rest):
            operand = rest[:operator.start()].rstrip()
            dated = any(date.end() == len(operand) for date in DATE.finditer(operand))
            if re.search(r'(?:과거|직전|지난)\s*(?:\d+\s*개월)?$', operand) and metric.prediction_role == {'FUTURE'}:
                axes.add('FORECAST')
                continue
            if operand and not dated:
                return replace(metric, valid=False)
        if len(axes) > 1:
            return replace(metric, valid=False)
        if axes:
            metric = replace(metric, comparison=axes.pop())
        elif not shared and multiple and not COMPARISON.search(prefix+suffix):
            metric = replace(metric, comparison=None)
    return metric


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


# A finite predicate asserts a direction. Nouns in "증가 여부를 확인하세요"
# and embedded questions such as "감소했는지" are not assertions.
TREND_STEMS = (r'(?P<up>증가|상승|확대|성장|늘어났|늘었|늘어나|늘|올랐|올라갔)|'
               r'(?P<down>감소|하락|축소|줄어들었|줄었|줄어들|줄|내렸|떨어졌)')
TREND_PREDICATE = re.compile(
    r'(?P<short>안\s+|못\s+)?'
    r'(?:' + TREND_STEMS + r')'
    r'(?P<negative>(?:하)?(?:지는|지도|지|진)\s*(?:않|못하?))?'
    r'(?P<form>하는\s*추세(?:를\s*보였|입|이)?|하고\s*있|세를\s*보였|세입|한\s*것으로\s*(?:나타났|집계됐|확인됐)|하였|했|합|하|았|습|었)?'
    r'(?P<ending>습니다|니다|다|고|으며|으나|지만|한\s+반면|음|함)?[,;，.!。\s]*$')
# Connective verbs describe the change leading to a reported level.
LEVEL_CHANGE = re.compile(
    r'(?P<short>안\s+|못\s+)?(?:' + TREND_STEMS + r')'
    r'(?P<negative>(?:하)?(?:지는|지도|지|진)\s*(?:않|못하?))?'
    r'(?:해(?:서)?|하여|아(?:서)?|어(?:서)?|며|면서|한(?:\s+(?:결과|후))?)?'
    r'[\s,，]*$')
TREND_BREAK = re.compile(r'(?<=반면)\s+|(?<=[고며만나])\s+|(?<=[.。])\s+|[,;，](?!\d)')
TREND_METRIC = re.compile(r'수출입|수출|수입|점유율|비중|금액|규모|총액|실적|교역액')

# A path over several observations cannot be proved by a single change cell.
TREND_PATH = re.compile(r'꾸준히|지속적으로|연속으로|갈수록|거듭할수록|매달|매월|추세|꾸준한')
ARTICLE_MEASURE = re.compile(r'수요|가격|단가')
ARTICLE_SUBJECT = re.compile(r'(?:' + ARTICLE_MEASURE.pattern + r')(?:은|는|이|가)(?=\s|$)')
# Match the causal noun phrase itself. Text before its subject can contain
# another complete assertion or the main clause's subject and comparator.
ARTICLE_ASIDE = re.compile(
    r'(?:수요|가격|단가)(?:가|이|의|는|은)?\s*'
    r'(?:둔화|감소|하락|증가|상승|변동|약화|회복)'
    r'(?:(?:된|한)?\s*(?:에도(?:\s*불구하고)?|에\s*힘입어|으로|때문에|영향으로|가운데|여파로)|했지만|됐지만)(?=\s|[,，])')
UNRESOLVED_DIRECTION = re.compile(
    r'(?P<up>증가|상승|확대|성장|늘어|늘었|' + EXTREME_UP + r')|(?P<down>감소|하락|축소|줄어|줄었|' + EXTREME_DOWN + r')'
    r'|(?P<similar>비슷한\s*수준|유사한\s*수준|보합세)')
GUIDANCE = re.compile(r'여부|했는지|하는지|했는가|(?:확인|참고|참조|살펴)\s*(?:하세요|하십시오|바랍니다|할 수)')


def _without_article_aside(text, syntax, mentions):
    # Anchor the main clause on a grounded subject, rather than enumerating
    # inflections of the article's background predicate. Never discard an
    # earlier trade assertion, number, date or comparison.
    if ARTICLE_MEASURE.search(syntax):
        for _, start, _ in mentions:
            # Keep a comparator immediately before the subject in the main clause.
            for comparison in COMPARISON.finditer(syntax, 0, start):
                if not text[comparison.end():start].strip():
                    start = comparison.start()
                    break
            background, main = syntax[:start], syntax[start:]
            if (ARTICLE_MEASURE.search(background)
                    and re.search(r'(?:로|어|아|에|해|가운데|에도|께|며|서|여|려)[,，]?\s*$', background.rstrip())
                    and not (TREND_METRIC.search(background) or COMPARISON.search(background)
                             or DATE.search(background) or quantities(background))
                    and re.match(r'\s*(?:의\s*)?(?:수출|수입|점유율|비중)', COMPARISON.sub('', main))
                    and COMPARISON.search(main) and _trend_predicate(main)):
                return text[start:]
    aside = ARTICLE_ASIDE.search(syntax)
    if not aside:
        return text
    start = aside.start()
    for _, left, right in reversed(mentions):
        if right <= start and re.fullmatch(r'의?\s*', text[right:start]):
            start = left
    return text[:start] + ' ' + text[aside.end():]


def _trend_predicate(text):
    match = TREND_PREDICATE.search(text)
    if match and not (match['form'] or match['ending'] or match['negative']):
        # A bare word such as "줄" can end a notice. A clipped trend headline
        # needs a comparator or measure to give it the meaning of an assertion.
        prefix = text[:match.start()]
        if not (COMPARE_OPERATOR.search(prefix) or TREND_METRIC.search(prefix)):
            return None
    return match


def _trend_claim(syntax, targets, time, catalog, predicate, *, understood=True):
    prefix = syntax[:predicate.start()]
    article = list(ARTICLE_SUBJECT.finditer(prefix))
    if article and not TREND_METRIC.search(prefix, article[-1].end()):
        return None
    axes = {'YOY' if m['yoy'] else 'MOM' for m in COMPARISON.finditer(prefix)}
    flow_words = set(re.findall(r'수출|수입', prefix))
    flow = {'수출': 'EXPORT', '수입': 'IMPORT'}.get(next(iter(flow_words), ''))
    share = bool(re.search(r'점유율|비중', prefix))
    # With no numeric unit, do not mistake a different measure (e.g. 수출 가격)
    # or an unknown entity for the trade amount of a matching row.
    rest = TREND_METRIC.sub('', mask_dates(COMPARISON.sub('', prefix)))
    rest = re.sub(r'소폭|큰\s*폭으로|대폭|다소|크게|조금|시장|전체|하여|에서|으로|[도의은는이가을를에와과액·,\s]', '', rest)
    valid = understood and not TREND_PATH.search(syntax) and len(axes) <= 1 and len(flow_words) <= 1 and not rest and not re.search(r'수출입|교역', prefix)
    metric = MetricClaim(family='SHARE_CHANGE' if share else 'CHANGE', comparison=next(iter(axes), None),
                         flow=flow, direction='UP' if predicate['up'] else 'DOWN' if predicate['down'] else None,
                         negated_change=bool(understood and (predicate['negative'] or predicate['short'])))
    return Claim(None, targets, 'GROUP' if catalog.grouped(targets) else 'SINGLE', metric, time, syntax, valid)


def _unresolved_direction(text):
    for marker in UNRESOLVED_DIRECTION.finditer(text):
        suffix = text[marker.end():]
        if suffix.startswith(('률', '율', '액', '분', '폭')) or GUIDANCE.search(suffix):
            continue
        return marker
    return None


SUBJECT_WORD = r'([가-힣A-Za-z0-9][가-힣A-Za-z0-9·/-]*)'
SUBJECT_QUALIFIERS = (
    re.compile(SUBJECT_WORD + r'의\s*@'),
    re.compile(r'(?:수출|수입)(?:액)?\s*중\s+' + SUBJECT_WORD),
    re.compile(SUBJECT_WORD + r'\s+기준'),
)
SCOPE_LABELS = frozenset('전체 연간 월간 기간 금액 총액 올해 전년 전월 오늘 현재'.split())


def _unknown_trade_subject(syntax, mentions):
    """Detect unbound qualifiers inside, before, or after the trade subject."""
    if not re.search(r'수출|수입', syntax):
        return False
    # Mark resolved names explicitly: spaces alone cannot distinguish a
    # missing qualifier from a known product already bound to its source row.
    marked = list(syntax)
    for _, start, _ in mentions:
        marked[start] = '@'
    scope = mask_dates(COMPARISON.sub(' ', ''.join(marked)))
    for pattern in SUBJECT_QUALIFIERS:
        if any(m[1] not in SCOPE_LABELS for m in pattern.finditer(scope)):
            return True
    for measure in re.finditer(r'수출|수입', syntax):
        end = max((b for _, _, b in mentions if b <= measure.start()), default=None)
        if end is None:
            continue
        gap = mask_dates(COMPARISON.sub('', syntax[end:measure.start()])).strip()
        # Case particles and aggregate labels do not introduce a product.
        if re.fullmatch(r'(?:으로의|로의|에서의|에 대한|의|은|는|이|가)?\s*(?:전체|총|전체의)?', gap):
            continue
        if re.fullmatch(r'[가-힣A-Za-z0-9][가-힣A-Za-z0-9 /·-]*', gap):
            return True
    return False


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
        without_aside = _without_article_aside(text, syntax, mentions)
        if without_aside != text:
            return parse(without_aside, context)
        time,last_year=read_time(syntax,context.year)
        if not DATE.search(syntax): time=context.time
        # Split coordinated assertions only when there is an independent trend
        # without a value. Numeric lists and their shared units stay intact.
        trend_cuts = []
        if TREND_BREAK.search(text) and re.search(TREND_STEMS, clean):
            boundaries = [m for m in TREND_BREAK.finditer(text)
                          if not any(a <= m.start() < b for _,a,b in mentions)]
            start = 0
            for end, after in [(m.start(), m.end()) for m in boundaries] + [(len(text), len(text))]:
                fragment = clean[start:end]
                if (_trend_predicate(fragment) or _unresolved_direction(fragment)) and not quantities(fragment):
                    trend_cuts.extend(cut for cut in (start, after) if 0 < cut < len(text))
                start = after
        if not values and not trend_cuts:
            predicate = _trend_predicate(clean)
            marker = predicate or _unresolved_direction(clean)
            claim = _trend_claim(syntax, targets, time, catalog, marker, understood=bool(predicate)) if marker else None
            if claim and not claim.metric.flow and context.metric and context.metric.family == 'AMOUNT':
                claim = replace(claim, metric=replace(claim.metric, flow=context.flow))
            claims = (claim,) if claim else ()
            return ParsedSentence(claims,(time,) if DATE.search(syntax) else (),not ambiguous_name),replace(
                context,targets=targets,year=last_year,time=time)
        flows=set(re.findall(r'수출|수입',syntax))
        common_flow=({'수출':'EXPORT','수입':'IMPORT'}[next(iter(flows))] if len(flows)==1 else context.flow)
        dates=list(DATE.finditer(syntax))
        points=[m for m in dates if m.group(1) or m.group(5)=='월']
        date_starts=[m for i,m in enumerate(points) if not i or not RANGE_JOIN.fullmatch(text[points[i-1].end():m.start()])]
        # Split only complete assertions, keeping a range and its two endpoints together.
        cuts = trend_cuts or _postposed_boundaries(text, values, mentions)
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
            # Scope period words and measure labels to this numeric slot.
            previous=next((q for q in reversed(values) if q.end<=quantity.start),None)
            next_start=next((q.start for q in values if q.start>=quantity.end),right)
            prefix=syntax[max(left,previous.end if previous else left):quantity.start]
            suffix=syntax[quantity.end:min(right,next_start)]
            label=metric_override or (syntax[:values[0].start] if each else prefix)
            metric=_scope_measure(metric,quantity,prefix,suffix,label,shared=bool(each or metric_override),
                                  multiple=len(values)>1)
            claim_time=ordered_times[variant_index] if ordered_times and not metric_list and not catalog.grouped(targets) else time
            if metric.family=='CHANGE' and previous and re.search(r'보다|대비',text[previous.end:quantity.start]):
                claim_time=replace(claim_time,comparison_reference=True)
            valid = quantity.valid and metric.valid
            if metric.family == 'CHANGE' and _unknown_trade_subject(syntax, mentions):
                valid = False
            claims.append(Claim(quantity,frozenset(bound_targets),target_scope,metric,claim_time,local,valid))
        last_metric = claims[-1].metric
        predicate = _trend_predicate(clean)
        # For a numeric change the numeric claim already carries the direction.
        # An amount alone cannot discharge a separate directional assertion.
        marker = predicate
        if not predicate:
            candidate = _unresolved_direction(clean)
            if candidate and (TREND_PATH.search(clean) or
                              (candidate.start() >= values[-1].end and not claims[-1].metric.direct_change)):
                marker = candidate
        level = claims[-1]
        separate_direction = (predicate and level.metric.family == 'AMOUNT'
                              and level.quantity.end <= predicate.start())
        if not predicate and level.metric.family == 'AMOUNT':
            # A preceding numeric change already owns the connecting verb.
            # Otherwise keep the whole prefix so axis, flow and article subject
            # are interpreted by the same rules as a direction after the level.
            previous_change = any(c.metric.family in ('CHANGE', 'SHARE_CHANGE', 'AMOUNT_CHANGE')
                                  for c in claims[:-1])
            before_level = clean[:level.quantity.start]
            candidate = None if previous_change else _unresolved_direction(before_level)
            if candidate:
                leading = LEVEL_CHANGE.search(before_level)
                if leading:
                    predicate = marker = leading
                    separate_direction = True
                elif not marker:
                    marker = candidate
        if marker and (separate_direction or not predicate or TREND_PATH.search(clean)):
            trend_syntax = syntax
            if separate_direction:
                # Preserve offsets and the comparator on either side of the level.
                # "달러로 증가" gives a level; "달러 증가" is already a delta.
                end = level.quantity.end
                link = re.match(r'(?:으로|로|이며|이고|를 기록해|를 기록한 뒤)(?=\s)', syntax[end:])
                if link:
                    end += link.end()
                trend_syntax = mask_spans(syntax, [(level.quantity.start, end)])
            trend = _trend_claim(trend_syntax, level.targets, level.time, catalog, marker,
                                 understood=bool(predicate))
            if trend:
                if not trend.metric.flow and level.metric.flow:
                    trend = replace(trend, metric=replace(trend.metric, flow=level.metric.flow))
                claims.append(trend)
        return ParsedSentence(tuple(claims),valid=not ambiguous_name),Context(targets,last_year,common_flow,time,last_metric)

    parsed, _ = parse(text)
    return replace(parsed, valid=False) if ambiguous else parsed
