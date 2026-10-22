"""기존 DB·로그를 읽어 미완료 작업만 집계합니다. 대기열 수정·재요청은 하지 않습니다."""
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, date
from pathlib import Path
from urllib.parse import urlsplit
from news.save import KST

LABELS = {'waiting':'대기', 'failed':'실패', 'blocked':'차단'}
FAILURES = {'parse_failed':'본문 추출 실패', 'validation_failed':'필수값 검증 실패',
            'request_failed':'HTTP/요청 실패', 'download_failed':'네트워크 요청 실패',
            'storage_failed':'저장 처리 실패', 'pipeline_failed':'정제·검증·저장 파이프라인 오류',
            'feed_structure_failed':'목록 XML 구조 검증 실패'}


def _events(path):
    """마지막 요청 이후의 실패만 사용합니다. 손상된 로그는 누락 사실을 표시합니다."""
    latest, damaged = {}, 0
    if not path.exists():
        return latest, damaged
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            try:
                e=json.loads(line)
                if not isinstance(e,dict): raise ValueError('로그 객체 아님')
            except (ValueError, TypeError):
                damaged+=1
                continue
            if not e.get('url'): continue
            key=(e.get('target_date'),e['url'])
            if e.get('event') in ('request','manual_requeue'):
                latest[key]=e
            elif e.get('event') in FAILURES:
                latest[key]=e
    return latest,damaged


def summarize_unfinished(directory, target, sites, daily_limit=100, now=None):
    """날짜는 작업 대상 날짜입니다. 게시일을 아직 읽지 못한 기사도 섞여 있습니다."""
    root=Path(directory).resolve()
    target=date.fromisoformat(str(target)).isoformat()
    now=now or datetime.now(KST)
    if now.tzinfo is None: raise ValueError('시간대가 있는 집계 시각이 필요합니다')
    now=now.astimezone(KST)
    # Store를 생성하지 않으므로 집계만 실행할 때 DB 마이그레이션도 발생하지 않습니다.
    with sqlite3.connect((root/'articles.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row
        db.execute('BEGIN')
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        def rows(table):
            return [dict(r) for r in db.execute('SELECT * FROM '+table)] if table in tables else []
        queue, periods, inventory = rows('queue'), rows('queue_period'), rows('daily_inventory')
        discovery=rows('discovery_queue')
        states={r['site']:r for r in rows('state')}
        blocks={(r['site'],r['path']):r for r in rows('list_blocks')}
        retries={(r['site'],r['path']):r for r in rows('list_retries')}
        access={r['site']:r['needs_probe'] for r in rows('body_access')}
        used={r['site']:r['count'] for r in rows('budget') if r['day']==now.date().isoformat()}
        done={r[0] for table in ('article_history','reference_articles') if table in tables for r in db.execute('SELECT url FROM '+table)}
    periods={r['url']:r for r in periods}
    inventory_dates=defaultdict(set)
    for r in inventory: inventory_dates[r['url']].add(r['target'])
    tasks=[]
    for row in queue:
        if row['site'] not in sites or row['url'] in done: continue
        if row['status'] not in ('pending','failed','not_found','robots_denied'): continue
        p=periods.get(row['url'],{})
        day=p.get('since_date') if p.get('since_date')==p.get('until_date') else None
        if not p and len(inventory_dates[row['url']])==1:
            day=next(iter(inventory_dates[row['url']]))
        # 범위만 있거나 날짜가 없는 과거 데이터는 특정 날짜로 추정하지 않습니다.
        try: day=date.fromisoformat(day).isoformat() if day else None
        except ValueError: day=None
        tasks.append({'target_date':day,'site':row['site'],'url':row['url'],
                      'kind':'article','queue_status':row['status'], 'period':p})
    for row in discovery:
        if row['site'] in sites and row['status'] not in ('processed','source_replaced'):
            tasks.append({'target_date':row['target'],'site':row['site'],'url':row['url'],
                          'kind':'list_page','queue_status':row['status']})
    # 같은 목록 URL이 여러 날짜의 작업일 수 있어 날짜 없는 로그를 임의 연결하지 않습니다.
    dates_by_url=defaultdict(set)
    for row in discovery+inventory:
        dates_by_url[row['url']].add(row['target'])
    for task in tasks: dates_by_url[task['url']].add(task['target_date'])
    events,damaged=_events(root/'events.jsonl')
    for task in tasks:
        site,url,kind=task['site'],task['url'],task['kind']
        path=urlsplit(url).path
        state=states.get(site,{})
        retry=retries.get((site,path)) if kind=='list_page' else None
        block=blocks.get((site,path)) if kind=='list_page' else None
        event=events.get((task['target_date'],url))
        if event is None and len(dates_by_url[url])==1:
            event=events.get((None,url))
        if event and event.get('event') not in FAILURES: event=None
        # 확정된 실패 이력은 현재 사이트 중단 때문에 차단 건수로 중복 계산하지 않습니다.
        status=task['queue_status']
        if status=='robots_denied':
            category,code,reason='blocked','robots_denied','robots 규칙으로 요청 금지'
        elif status in ('failed','not_found'):
            category,code='failed',event['event'] if event else status
            reason=FAILURES.get(code,'HTTP 404: 기사 없음' if status=='not_found' else '실패 기록 있음, 상세 원인 로그 없음')
        elif state.get('manual'):
            category,code,reason='blocked','site_manual_stop',state.get('reason') or '사이트 수동 중단 상태'
        elif state.get('until',0)>now.timestamp():
            category,code,reason='blocked','site_cooldown',state.get('reason') or '사이트 재개 제한 시각 이전'
        elif block or (retry and retry['attempts']>=3):
            category,code='blocked','list_path_blocked'
            reason=block['reason'] if block else '목록 총 3회 시도 소진'
        elif kind=='article' and access.get(site):
            category,code,reason='blocked','body_hold','원인 확인이 필요한 기존 본문 보류'
        elif retry:
            category,code='waiting','list_retry_wait'
            reason=f"목록 재시도 대기: 최초 포함 {retry['attempts']}/3회 사용"
        elif event:
            category,code,reason='failed',event['event'],FAILURES[event['event']]
        elif used.get(site,0)>=daily_limit:
            category,code,reason='waiting','daily_budget','집계 시점의 사이트 일일 요청 상한 도달'
        else:
            category,code,reason='waiting','pending','대기열 미처리; 상세 중단 원인은 기록으로 확인되지 않음'
        if category=='failed' and event:
            detail=event.get('error') or (f"HTTP {event['status']}" if event.get('status') else None)
            if detail: reason += ': '+str(detail)
        task.update(category=category,label=LABELS[category],reason_code=code,reason=reason)
        if event:
            task['failure_evidence']={k:event[k] for k in ('event','time','status','error','target_date') if k in event}
        if retry: task['retry']={k:retry[k] for k in ('url','attempts','next_at')}
        if state.get('until',0)>now.timestamp(): task['site_resume_after']=state['until']
        if task['target_date'] is None: task['date_note']='날짜 미상 또는 여러 날짜 범위: 날짜별 합계에 임의 배분하지 않음'

    def counts(items, kind):
        return {label:sum(t['kind']==kind and t['category']==label for t in items) for label in LABELS}
    groups=[]
    for day in sorted({t['target_date'] for t in tasks},key=lambda d:d or '',reverse=True):
        items=[t for t in tasks if t['target_date']==day]
        scope='undated' if day is None else 'current' if day==target else 'past' if day<target else 'other'
        reasons={}
        for t in items:
            key=(t['kind'],t['category'],t['reason_code'],t['reason'])
            reasons[key]=reasons.get(key,0)+1
        groups.append({'target_date':day,'scope':scope,'articles':counts(items,'article'),
                       'list_pages':counts(items,'list_page'),
                       'reasons':[dict(kind=k[0],category=k[1],code=k[2],reason=k[3],count=n) for k,n in sorted(reasons.items())],
                       'items':items})
    return {'target_date':target,'generated_at':now.isoformat(),'timezone':'Asia/Seoul',
            'automatic_recollection':False,'groups':groups,
            'totals':{'articles':counts(tasks,'article'),'list_pages':counts(tasks,'list_page')},
            'damaged_log_lines':damaged,
            'notes':['날짜는 저장된 작업 대상 날짜이며 실제 게시일 확인 전인 항목도 포함',
                     '현재 저장된 상태 기준 집계. 최초 중단 원인을 추정하지 않음',
                     '기사 건수와 목록 페이지 수는 합산하지 않음',
                     '저장·본문 없음 완료 및 유료 제외·검수 미통과는 미완료에서 제외',
                     '제목 후보로 선택되지 않아 대기열에 등록되지 않은 항목은 기존 일일 미검수 집계에서 확인',
                     '같은 목록 URL도 대상 날짜가 다르면 별도 목록 작업. 날짜 없는 공유 URL 오류 로그는 임의 귀속하지 않음']}


def render_text(report):
    lines=[f"미완료 작업 집계 — 이번 대상 날짜 {report['target_date']}",
           '자동 재수집 없음 | 단위: 기사 건 / 목록 페이지',
           '대상 날짜 | 구분 | 기사 대기/실패/차단 | 목록 대기/실패/차단']
    labels={'current':'이번 대상','past':'과거','other':'다른 날짜','undated':'날짜 미상'}
    for group in report['groups']:
        numbers=lambda kind:'/'.join(str(group[kind][k]) for k in LABELS)
        lines.append(f"{group['target_date'] or '미상'} | {labels[group['scope']]} | {numbers('articles')} | {numbers('list_pages')}")
        for reason in group['reasons']:
            unit='기사' if reason['kind']=='article' else '목록'
            lines.append(f"  - {unit} {LABELS[reason['category']]} {reason['count']}: {reason['reason']}")
    if not report['groups']: lines.append('등록된 미완료 작업 없음')
    if report['damaged_log_lines']: lines.append(f"읽지 못한 로그 {report['damaged_log_lines']}줄: 상세 원인 일부 누락 가능")
    lines.extend(report['notes'])
    return '\n'.join(lines)+'\n'
