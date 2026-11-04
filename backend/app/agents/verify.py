"""Match generated claims to the same bounded evidence used by compose."""
import re
from functools import lru_cache
from decimal import Decimal, ROUND_HALF_UP,localcontext
from backend.app.agents.compose import fixed_conclusion
from backend.app.agents.state import SentenceCheck, Verification

# 앞이 영문이면 이름의 일부다('HS6'의 6, 'Q3'의 3).
TOKEN = re.compile(r'(?<![\d.A-Za-z])(?P<value>[+-]?\d[\d,]*(?:\.\d+)?)\s*(?P<scale>[만억조])?\s*(?P<unit>%p|%|달러|USD|배|위|개|건|kg)?', re.I)
DATE = re.compile(r'(?<!\d)(\d{4})\s*(?:년\s*(?:(\d{1,2})\s*월)?|-(\d{2})(?:-\d{2})?)|(\d{1,3})\s*(개월|월)')
SCALE = {None:1,'만':10**4,'억':10**8,'조':10**12}
PERIOD_AMOUNTS = {'total_usd','monthly_avg_usd','verified_subtotal_usd',
                  'compare_total_usd','compare_monthly_avg_usd','compare_verified_subtotal_usd'}
# 저장 행에는 월마다 같은 기간 값이 붙어 있다. change_pct도 두 기간 전체를 비교한 값이라 월에 묶지 않는다.
PERIOD_KEYS = PERIOD_AMOUNTS | {'change_pct'}
CHANGE_KEYS = {'change_pct','yoy_pct','mom_pct','growth_pct'}
DIRECTION = re.compile(r'과거|지난|직전|최근|앞선|향후|앞으로|미래|다가올|다음|예측|예상|전망')
PAST_WORDS = {'과거','지난','직전','최근','앞선'}
# "1억 2,346만 달러"는 금액 하나다. 두 숫자로 나누지 않도록 만 단위 한 숫자(12346만)로 바꿔 대조한다.
COMPOUND = re.compile(r'(?<![\d.])(?P<sign>[+-]?)(?P<eok>\d[\d,]*)\s*억\s*(?P<man>\d[\d,]*)\s*만')
# 값 바로 뒤의 증감 표현. "5.2% 감소"·"5.2%의 감소"·"5.2% 낮은"은 -5.2%다. "~로 감소"는 바뀐 뒤의 값이라 방향으로 보지 않는다.
DOWN_WORDS, UP_WORDS = r'감소|줄|하락|축소|떨어|낮|내려|내린', r'증가|늘|상승|확대|성장|올라|오르|높|올린'
CHANGE = re.compile(r'\s*(?:가|이|만큼|나|이나|의|을|를)?\s*(?:(?P<down>'+DOWN_WORDS+r')|(?P<up>'+UP_WORDS+r'))')
# 같은 주장 안의 증감 단어("감소율은 5.2%", "감소하여 5.2%"). 비율 값에만 쓰고, 한 방향만 있을 때만 본다.
# 값과 떨어진 곳에서는 활용형까지 본다. '높'·'늘'만으로는 '가장 높은'·'오늘'도 방향으로 읽는다.
DOWN = re.compile(r'감소|줄어|줄었|줄고|하락|축소|떨어|내려|내린')
UP = re.compile(r'증가|늘어|늘었|늘고|상승|확대|성장|올라|오르|올린')
# 한 문장 안의 주장 경계. 월평균·총액·비중 같은 지표 표현은 그 금액이 있는 주장에서만 본다.
# 값 뒤의 '로'("총 1,410만 달러로 월평균 117.5만 달러")도 주장을 나눈다.
SEPARATOR = re.compile(r'(?<!\d),|,(?!\d)|[;，]|이며|이고|하고|으며|(?:(?<=달러)|(?<=USD)|(?<=[%위배]))로(?=\s)')
# 이름은 앞이 글자·숫자가 아닐 때만 찾는다('1위'의 '위'). 영문 이름(국가 코드)은 뒤도 본다('USD'의 'US').
# 국가명만 '대중국'·'대미국'처럼 낱말 첫머리의 '대(對)' 뒤에서도 찾는다. 품목명에 넓히면 '대리석'·'대형'에 걸린다.
NAME_EDGE = r'(?<![가-힣A-Za-z0-9])'
COUNTRY_EDGE = r'(?:(?<![가-힣A-Za-z0-9])|(?<=(?<![가-힣])대))'
# "상위 2개국", "3곳", "2개 품목"은 조회 대상 수이지 통계 수치가 아니다. TOKEN이 "개"까지 먹으므로 뒤는 "국"부터 본다.
# 단위가 없거나 "개"인 값만 대상 수로 본다. "상위 10%"·"상위 3.5억 달러"는 그대로 검증한다.
COUNT_BEFORE = re.compile(r'(?:상위|하위)\s*$')
COUNT_AFTER = re.compile(r'\s*(?:곳|개국|국|개?\s*(?:국가|품목|시장))')


@lru_cache(maxsize=4096)
def _name_pattern(name, country=False):
    return re.compile((COUNTRY_EDGE if country else NAME_EDGE)+re.escape(name)+(r'(?![A-Za-z0-9])' if name.isascii() else ''))


def _target_count(text, match):
    if match['scale'] or match['unit'] not in (None, '개'): return False
    return bool(COUNT_BEFORE.search(text,0,match.start()) or COUNT_AFTER.match(text,match.end()))


def _shown(match):
    """재생성 안내에 넘길 숫자 표기. 합친 억·만 금액(12346만)은 LLM이 쓴 모양(1억 2,346만)으로 되돌린다."""
    text=match.group().strip()
    raw=match['value'].replace(',','')
    if match['scale']=='만' and raw.lstrip('+-').isdigit() and abs(int(raw))>=10**4:
        eok,man=divmod(abs(int(raw)),10**4)
        text=('-' if raw.startswith('-') else '')+f'{eok:,}억'+(f' {man:,}만' if man else '')+text[match.end('scale')-match.start():]
    return text[:40]


def _merge_compound(text):
    def merged(m):
        total=int(m['eok'].replace(',',''))*10**4+int(m['man'].replace(',',''))
        return f'{m["sign"]}{total}만'
    return COMPOUND.sub(merged,text)


def _clause(text, start, end):
    left=max((m.end() for m in SEPARATOR.finditer(text,0,start)),default=0)
    right=next((m.start() for m in SEPARATOR.finditer(text,end)),len(text))
    return text[left:right]


def _shift(month, offset):
    index=int(month[:4])*12+int(month[5:7])-1+offset
    return f'{index//12}-{index%12+1:02d}'


def _months_between(start, end):
    return (int(end[:4])-int(start[:4]))*12+int(end[5:7])-int(start[5:7])


def _compare_month(digest, row):
    """compare_month가 없는 예전 결과는 기간 차이로 비교 월을 구한다(두 기간 길이가 같을 때만)."""
    if row.get('compare_month'): return str(row['compare_month'])
    period,compare=digest.get('period') or {},digest.get('compare_period') or {}
    month=str(row.get('month') or row.get('trade_month') or '')[:7]
    bounds=[str(x.get(k,''))[:7] for x in (period,compare) for k in ('start','end')]
    if not month or not all(bounds) or _months_between(*bounds[:2])!=_months_between(*bounds[2:]): return None
    return _shift(bounds[2],_months_between(bounds[0],month))


def _period_scope(digest, key):
    """기간 값의 근거: 해당 기간 표시. change_pct는 두 기간을 비교한 값이라 양쪽 기간을 모두 붙인다."""
    names=['compare_period' if key.startswith('compare_') else 'period']
    if key=='change_pct': names.append('compare_period')
    scope=set()
    for name in names:
        period=digest.get(name) or {}
        start,end=(str(period.get(k,''))[:7] for k in ('start','end'))
        if start and end: scope.add(f'period:{start}:{end}')
        if start and start==end: scope.add(start)
    return scope


def evidence(digest):
    """Keep row identities and monthly identities attached to each numeric cell."""
    columns={c['key']:c.get('unit') for c in digest.get('columns', [])}
    def walk(row, identities):
        if isinstance(row,list):
            for item in row: yield from walk(item, identities)
        if not isinstance(row,dict): return
        ids=identities | {str(row[k]) for k in ('name','label','code','product_name','country_name','country_code','hs_code','hs6','month','trade_month') if row.get(k)}
        for key,value in row.items():
            if isinstance(value,(dict,list)): yield from walk(value,ids)
            elif isinstance(value,(float,int)) and not isinstance(value,bool):
                unit=columns.get(key)
                if key.endswith('_usd') or key=='exp_12m': unit='USD'
                elif key.endswith('_pct'): unit='%'
                elif key.endswith('_pp'): unit='%p'
                elif key=='rank' or key.endswith('_rank'): unit='위'
                if digest.get('kind')=='PREDICTION' and key in ('exp_12m','growth_pct'):
                    yield key,Decimal(str(value)),unit,ids|{'duration:12'}
                elif key=='compare_amount_usd':
                    if not (month:=_compare_month(digest,row)): continue
                    yield key,Decimal(str(value)),unit,(ids-{str(row.get('month')),str(row.get('trade_month'))})|{month}
                elif unit and (key in PERIOD_KEYS or not any(
                        re.fullmatch(r'\d{4}-\d{2}',identity) for identity in ids)):
                    scoped=(ids-{str(row.get('month')),str(row.get('trade_month'))})|_period_scope(digest,key)
                    yield key,Decimal(str(value)),unit,scoped
                elif unit: yield key,Decimal(str(value)),unit,ids
    return list(walk(digest,{'prediction'} if digest.get('kind')=='PREDICTION' else set()))


def check_sentence(sentence, digests, *, known_names=(), countries=()):
    """문장의 수치를 근거와 대조한다. 월·방향이 여럿인 문장은 주장 단위로 나눠 각각 검사한다."""
    return _check_claim(sentence,digests,known_names,require_month=False,inherited_names=(),countries=frozenset(countries))


def _check_claim(sentence, digests, known_names, *, require_month, inherited_names, countries=frozenset()):
    _require_month,_inherited_names=require_month,inherited_names
    text=_merge_compound(re.sub(r'\[\d+\]', '',sentence))
    dates=list(DATE.finditer(text))
    def names_in(value):
        value=DATE.sub(' ',value)
        # 1글자 이름('위'·'조'·'배')은 단위·조사와 구분할 수 없어 이름으로 보지 않는다.
        names={name for name in known_names if len(name)>1 and name in value and _name_pattern(name,name in countries).search(value)}
        return {name for name in names if not any(name!=other and name in other for other in names)}
    def check_clauses(clauses, require_month):
        inherited=_inherited_names
        checks=[]
        for clause in clauses:
            inherited=names_in(clause) or inherited
            checks.append(_check_claim(clause,digests,known_names,countries=countries,
                                       require_month=require_month,inherited_names=inherited))
        return all(good for good,_ in checks),[n for _,nums in checks for n in nums]
    if any(d.get('kind')=='PREDICTION' for d in digests):
        clauses=re.split(r'(?<!\d),|,(?!\d)|[;，]|(?:이고|이며|하고|됐고|었고|았고|였고|(?<=달러)고|(?<=%)고)\s+(?='+DIRECTION.pattern+r')',text)
        if len(clauses)>1 and all(TOKEN.search(DATE.sub(' ',c)) for c in clauses):
            return check_clauses(clauses,_require_month)
    month_dates=[m for m in dates if m.group(2) or m.group(3) or m.group(5)=='월']
    month_range=None
    if len(dates)==len(month_dates)==2:
        first,last=month_dates
        if re.fullmatch(r'\s*(?:부터|[~～–])\s*',text[first.end():last.start()]):
            if first.group(1) and (last.group(1) or last.group(5)=='월'):
                month_range=tuple(f'{m.group(1) or first.group(1)}-{(m.group(2) or m.group(3) or m.group(4)).zfill(2)}'
                                  for m in month_dates)
    if len(month_dates)>1 and not month_range:
        # Keep thousands separators intact and require an unambiguous month per claim.
        clauses=re.split(r'(?<!\d),|,(?!\d)|[;，]',text)
        if len(clauses)==1:  # 쉼표 없는 비교 문장: 각 월 표현부터 다음 월 표현 앞까지를 한 주장으로 본다.
            cuts=[0,*(m.start() for m in month_dates[1:]),len(text)]
            clauses=[text[a:b] for a,b in zip(cuts,cuts[1:])]
        return check_clauses(clauses,True)
    numbers=[]
    ok=True
    # Check each digest independently: an explanation may discuss multiple tasks,
    # but a single claim must have a consistent metric, unit, period and target.
    candidates=[]
    explicit_months=set()
    for d in digests:
        dates_ok=True
        windows=[d.get(k) for k in ('period','compare_period','article_period') if d.get(k)]
        if d.get('anchor'): windows.append({'start':str(d['anchor']), 'end':str(d['anchor'])})
        for m in dates:
            year, month1, month2, count, kind=m.groups()
            if year:
                ym=year+('-'+(month1 or month2).zfill(2) if month1 or month2 else '')
                if len(ym)==7: explicit_months.add(ym)
                dates_ok &= any(str(p['start'])[:len(ym)]<=ym<=str(p['end'])[:len(ym)] for p in windows)
            elif kind=='개월':
                lengths=[(int(p['end'][:4])-int(p['start'][:4]))*12+int(p['end'][5:7])-int(p['start'][5:7])+1 for p in windows]
                dates_ok &= int(count) in lengths or (int(count)==12 and d.get('kind')=='PREDICTION')
            else:
                dates_ok &= any(any((int(p['start'][5:7])-1+i)%12+1==int(count) for i in range(
                    min(12,(int(p['end'][:4])-int(p['start'][:4]))*12+int(p['end'][5:7])-int(p['start'][5:7])+1))) for p in windows)
        if dates_ok: candidates.append(d)
    if dates and not candidates: ok=False
    dated=text
    text=DATE.sub(' ',text)
    mentioned=names_in(text) or set(_inherited_names)
    facts=[f for d in candidates for f in evidence(d) if mentioned<=f[3]
           and not (('수입' in sentence and d.get('trade_flow')=='EXPORT') or
                    ('수출' in sentence and d.get('trade_flow')=='IMPORT'))]
    if len(mentioned)>1 and not facts and not _require_month:
        # "미국은 3억 달러, 중국은 2억 달러": 한 행에 두 국가가 없으면 이름마다 주장을 나눠 대조한다.
        # 날짜가 있는 원문에서 나누고, 날짜가 없는 조각에는 문장의 기간 표현을 붙여 조각마다 기간도 다시 본다.
        starts=sorted({m.start() for name in mentioned for m in _name_pattern(name,name in countries).finditer(dated)})
        if len(starts)>1:
            cuts=[0,*starts[1:],len(dated)]
            when=' '.join(m.group() for m in dates)
            return check_clauses([(c if DATE.search(c) else when+' '+c) for c in (dated[a:b] for a,b in zip(cuts,cuts[1:]))],False)
    for name in sorted(mentioned,key=len,reverse=True): text=text.replace(name,' ')
    tokens=[m for m in TOKEN.finditer(text) if not _target_count(text,m)]
    # 숫자 없는 문장("수출액은 모두 미국 달러(USD) 단위입니다")은 수치 검증 대상이 아니다. 기간 표현만 본다.
    if mentioned and not facts and tokens: ok=False
    for match in tokens:
        raw,scale,unit=match.group('value','scale','unit')
        value=Decimal(raw.replace(',',''))
        unit={'달러':'USD','usd':'USD','개':'COUNT','건':'COUNT','kg':'KG','%p':'%p'}.get((unit or '').lower(),unit)
        if scale and not unit: unit='USD'
        numbers.append(_shown(match))
        granularity=Decimal(1).scaleb(value.as_tuple().exponent)
        claim=_clause(text,match.start(),match.end())
        change=CHANGE.match(text,match.end()) if unit in ('%','%p','USD') else None
        direction=('down' if change['down'] else 'up') if change else None
        claim_direction=False   # 주장 안의 증감 단어에서 정한 방향은 증감률 지표에만 적용한다
        if not direction and unit in ('%','%p'):
            rest=claim.replace(match.group(),' ',1)
            down,up=bool(DOWN.search(rest)),bool(UP.search(rest))
            if down!=up: direction,claim_direction='down' if down else 'up',True
        if direction and raw[0] in '+-' and (value<0)!=(direction=='down'):
            ok=False  # "+5.2% 감소"처럼 부호와 증감 표현이 어긋난다
            continue
        def matches(fact):
            key,amount,expected,identities=fact
            if not unit or expected!=unit: return False
            if 'duration:12' in identities:
                subject=re.sub(r'(?:과거|지난|직전|최근|앞선)\s*(?:대비|기준월)\s*','',sentence)
                directions={word in PAST_WORDS for word in DIRECTION.findall(subject)}
                if directions and directions!={key=='exp_12m'}: return False
            monthly={value for value in identities if re.fullmatch(r'\d{4}-\d{2}',value)}
            periods=[tuple(i.split(':')[1:]) for i in identities if i.startswith('period:')]
            years={m.group(1) for m in dates if m.group(1) and not (m.group(2) or m.group(3))}
            durations={int(m.group(4)) for m in dates if m.group(5)=='개월'}
            if durations and (periods or monthly or 'duration:12' in identities):
                lengths={(int(end[:4])-int(start[:4]))*12+int(end[5:])-int(start[5:])+1
                         for start,end in periods} if periods else {1}
                if 'duration:12' in identities: lengths={12}
                if not durations<=lengths: return False
            if month_range:
                if month_range not in periods: return False
            elif key in PERIOD_AMOUNTS or periods:
                if month_dates and not monthly: return False
                if years and not any((f'{year}-01',f'{year}-12') in periods for year in years) and not (
                        'prediction' in identities and all(any(start[:4]==year for start,_ in periods) for year in years)):
                    return False  # 예측 결과는 기준월이 속한 연도 언급만 허용한다
            elif years and monthly: return False
            if _require_month:
                if not month_dates or not monthly: return False
            if not month_range and month_dates and monthly and month_dates[0].group(5)=='월':
                if not any(int(month[5:])==int(month_dates[0].group(4)) for month in monthly): return False
            if explicit_months and monthly and not monthly.intersection(explicit_months): return False
            if expected=='USD' and '수출' in sentence and 'import' in key: return False
            if expected=='USD' and '수입' in sentence and 'export' in key: return False
            # 지표 표현은 그 금액이 있는 주장(claim)에서만 본다. "총 1,410만 달러이며, 월평균은 117.5만 달러"
            if expected=='USD' and ('월평균' in claim)!=('monthly_avg' in key): return False
            if expected=='USD' and any(word in claim for word in ('총액','합계')):
                aggregate=key in ('total_usd','compare_total_usd') or ((periods or not monthly) and key in ('amount_usd','export_amount_usd','import_amount_usd'))
                if not aggregate: return False
            if expected=='%' and '비중' in claim and key!='share_pct': return False
            if expected=='%' and '전년' in claim and key not in ('yoy_pct','change_pct'): return False
            if expected=='%' and '전월' in claim and key not in ('mom_pct','change_pct'): return False
            if expected=='위' and 'prediction' in identities:
                directions={rank_key for label,rank_key in (('품목','country_rank'),('국가','product_rank'))
                            if re.search(label+r'(?:별|\s*(?:중|내|안))?\s*순위',claim)}
                if directions and directions!={key}: return False
            # 증감 표현이 붙은 비율은 증감률이다. 비중을 근거로 "5% 증가"·"증가해 5%"를 통과시키지 않는다.
            # 값과 떨어진 증감 단어는 그 주장이 비중을 말할 때만("비중은 증가해 40.0%") 비중 값을 허용한다.
            if direction and key=='share_pct' and (not claim_direction or not re.search(r'비중|점유', claim)): return False
            claimed=value
            if direction and (not claim_direction or key in CHANGE_KEYS or key.endswith('_pp')):  # 증감 표현이 붙은 값은 근거의 부호와 같은 방향이어야 한다
                if amount==0 or (amount<0)!=(direction=='down'): return False
                amount,claimed=abs(amount),abs(value)
            with localcontext() as context:
                context.prec=max(32,len(amount.as_tuple().digits)+abs(granularity.as_tuple().exponent)+16)
                return (amount/SCALE[scale]).quantize(granularity,rounding=ROUND_HALF_UP)==claimed
        if not any(matches(f) for f in facts): ok=False
    return ok,numbers


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


def verify(state,known_names=(),*,snapshots=None,countries=()):
    snapshots=snapshots or {}
    def source(step_id):
        digest=state.results[step_id].digest or {}
        if step_id not in snapshots: return digest
        # 저장 행에는 없는 결과 전체 합계(순위 total_usd·비중 분모)는 같은 결과로 만든 digest 값을 근거로 더한다
        return {**snapshots[step_id],**{k:digest[k] for k in ('total_usd','denominator_usd') if k in digest}}
    by_task={t.task_id:[source(s.step_id) for s in t.steps
                       if s.step_id in state.results and (s.step_id in snapshots or state.results[s.step_id].digest)]
             for t in state.plan.tasks}
    checks=[]
    identities=set()
    for group in by_task.values():
        for digest in group:
            for _,_,_,ids in evidence(digest): identities.update(ids)
    # 사전 이름은 답변에 나온 것만 남긴다(약 1.5만 개 품목명을 문장마다 다시 훑지 않게). 결과 행 이름의 일부인 사전 이름
    # ('전동축[캠샤프트…]' 안의 '전동축')은 그 결과를 줄여 부른 것이라 결과 밖 대상(D-77)으로 보지 않는다.
    # 국가명은 빼지 않는다. '수단'은 '남수단'을 줄여 부른 것이 아니라 다른 국가다.
    countries=frozenset(countries)
    text=' '.join([*(x for c in state.draft.conclusion for x in c.sentences),*state.draft.explanation,state.draft.turn_summary])
    names={n for n in known_names if n and n in text
           and (n in countries or not any(n!=i and n in i for i in identities))}|identities
    index=0
    def add(sentence,digests):
        good,numbers=check_sentence(sentence,digests,known_names=names,countries=countries)
        source=any(d.get('_verification_source')=='SNAPSHOT' for d in digests)
        unavailable=any(d.get('_source_unavailable') for d in digests)
        checks.append(SentenceCheck(index=index,numbers=numbers,status='PASS' if good else 'MISMATCH',
            basis='SNAPSHOT' if source else 'DIGEST',
            reason='MATCH' if good else 'SOURCE_UNAVAILABLE' if unavailable else 'NOT_CONFIRMED'))
    for task,conclusion in zip(state.plan.tasks,state.draft.conclusion,strict=True):
        generated=bool(by_task[task.task_id]) and fixed_conclusion(state,task) is None
        for sentence in conclusion.sentences:
            if generated: add(sentence,by_task[task.task_id])
            index+=1
    for sentence in state.draft.explanation:
        add(sentence,[d for group in by_task.values() for d in group]); index+=1
    primary=state.plan.tasks[0]
    summary_ok=True
    if state.draft.turn_summary and fixed_conclusion(state,primary) is None:
        summary_ok,_=check_sentence(state.draft.turn_summary,by_task[primary.task_id],known_names=names,countries=countries)
        if not summary_ok: state.draft.turn_summary=''
    state.verification=Verification(checks=checks,regeneration_count=state.verification.regeneration_count,
        status='PASS' if summary_ok and all(c.status=='PASS' for c in checks) else 'NEEDS_REVIEW')
