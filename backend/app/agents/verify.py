"""Match generated claims to the same bounded evidence used by compose."""
import re
from decimal import Decimal, ROUND_HALF_UP,localcontext
from backend.app.agents.compose import fixed_conclusion
from backend.app.agents.state import SentenceCheck, Verification

TOKEN = re.compile(r'(?<![\d.])(?P<value>[+-]?\d[\d,]*(?:\.\d+)?)\s*(?P<scale>[만억조])?\s*(?P<unit>%p|%|달러|USD|배|위|개|건|kg)?', re.I)
DATE = re.compile(r'(?<!\d)(\d{4})\s*(?:년\s*(?:(\d{1,2})\s*월)?|-(\d{2})(?:-\d{2})?)|(\d{1,3})\s*(개월|월)')
SCALE = {None:1,'만':10**4,'억':10**8,'조':10**12}


def evidence(digest):
    """Keep row identities and monthly identities attached to each numeric cell."""
    columns={c['key']:c.get('unit') for c in digest.get('columns', [])}
    def walk(row, identities):
        if isinstance(row,list):
            for item in row: yield from walk(item, identities)
        if not isinstance(row,dict): return
        ids=identities | {str(row[k]) for k in ('name','code','product_name','country_name','country_code','hs_code','hs6','month','trade_month') if row.get(k)}
        for key,value in row.items():
            if isinstance(value,(dict,list)): yield from walk(value,ids)
            elif isinstance(value,(float,int)) and not isinstance(value,bool):
                unit=columns.get(key)
                if key.endswith('_usd') or key=='exp_12m': unit='USD'
                elif key.endswith('_pct'): unit='%'
                elif key.endswith('_pp'): unit='%p'
                elif key=='rank' or key.endswith('_rank'): unit='위'
                if unit: yield key,Decimal(str(value)),unit,ids
    return list(walk(digest,set()))


def check_sentence(sentence, digests, *, known_names=()):
    text=re.sub(r'\[\d+\]', '',sentence)
    dates=list(DATE.finditer(text))
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
    text=DATE.sub(' ',text)
    mentioned={name for name in known_names if name and name in text}
    mentioned={name for name in mentioned if not any(name!=other and name in other for other in mentioned)}
    for name in sorted(mentioned,key=len,reverse=True): text=text.replace(name,' ')
    facts=[f for d in candidates for f in evidence(d) if mentioned<=f[3]
           and not (('수입' in sentence and d.get('trade_flow')=='EXPORT') or
                    ('수출' in sentence and d.get('trade_flow')=='IMPORT'))]
    if mentioned and not facts: ok=False
    for match in TOKEN.finditer(text):
        raw,scale,unit=match.group('value','scale','unit')
        value=Decimal(raw.replace(',',''))
        unit={'달러':'USD','usd':'USD','개':'COUNT','건':'COUNT','kg':'KG','%p':'%p'}.get((unit or '').lower(),unit)
        if scale and not unit: unit='USD'
        numbers.append(match.group().strip()[:40])
        granularity=Decimal(1).scaleb(value.as_tuple().exponent)
        def matches(fact):
            key,amount,expected,identities=fact
            if not unit or expected!=unit: return False
            monthly={value for value in identities if re.fullmatch(r'\d{4}-\d{2}',value)}
            if explicit_months and monthly and not monthly.intersection(explicit_months): return False
            if expected=='USD' and '수출' in sentence and 'import' in key: return False
            if expected=='USD' and '수입' in sentence and 'export' in key: return False
            if expected=='USD' and ('월평균' in sentence)!=('monthly_avg' in key): return False
            if expected=='USD' and any(word in sentence for word in ('총액','합계')):
                aggregate=key in ('total_usd','compare_total_usd') or (not monthly and key in ('amount_usd','export_amount_usd','import_amount_usd'))
                if not aggregate: return False
            if expected=='%' and '비중' in sentence and key!='share_pct': return False
            if expected=='%' and '전년' in sentence and key not in ('yoy_pct','change_pct'): return False
            if expected=='%' and '전월' in sentence and key not in ('mom_pct','change_pct'): return False
            with localcontext() as context:
                context.prec=max(32,len(amount.as_tuple().digits)+abs(granularity.as_tuple().exponent)+16)
                return (amount/SCALE[scale]).quantize(granularity,rounding=ROUND_HALF_UP)==value
        if not any(matches(f) for f in facts): ok=False
    return ok,numbers


def snapshot_evidence(payload):
    """Full saved rows, never paginated or bounded for LLM input. No new SQL."""
    conditions=payload.get('conditions') or {}
    return {**{k:conditions[k] for k in ('period','compare_period','trade_flow') if k in conditions},
            'columns':payload.get('columns',[]),'rows':payload.get('rows',[]),
            '_verification_source':'SNAPSHOT'}


def verify(state,known_names=(),*,snapshots=None):
    snapshots=snapshots or {}
    by_task={t.task_id:[snapshots.get(s.step_id,state.results[s.step_id].digest) for s in t.steps
                       if s.step_id in state.results and (s.step_id in snapshots or state.results[s.step_id].digest)]
             for t in state.plan.tasks}
    checks=[]
    names=set(known_names)
    for group in by_task.values():
        for digest in group:
            for _,_,_,ids in evidence(digest): names.update(ids)
    index=0
    def add(sentence,digests):
        good,numbers=check_sentence(sentence,digests,known_names=names)
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
        summary_ok,_=check_sentence(state.draft.turn_summary,by_task[primary.task_id],known_names=names)
        if not summary_ok: state.draft.turn_summary=''
    state.verification=Verification(checks=checks,regeneration_count=state.verification.regeneration_count,
        status='PASS' if summary_ok and all(c.status=='PASS' for c in checks) else 'NEEDS_REVIEW')
