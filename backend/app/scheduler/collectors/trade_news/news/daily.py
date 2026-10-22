"""전날 기사 작업의 날짜·목록·집계. 목표 10건은 중단 조건이 아닙니다."""
import json
import sqlite3
from datetime import datetime, timedelta, date
from pathlib import Path
from news.save import KST
from news.candidates import annotate_candidate
from news.urls import article_url


def yesterday(now=None):
    now = now or datetime.now(KST)
    if now.tzinfo is None:
        raise ValueError('시간대가 있는 실행 시각이 필요합니다')
    return now.astimezone(KST).date() - timedelta(days=1)


def initialize(store):
    with store.db:
        store.db.execute('''CREATE TABLE IF NOT EXISTS daily_inventory (
          target TEXT NOT NULL, url TEXT NOT NULL, site TEXT NOT NULL,
          title TEXT, published TEXT, annotation TEXT, origin TEXT,
          PRIMARY KEY(target,url))''')


def record_candidate(store, target, url, title, published, origin):
    """URL을 병합하고 제목을 보완합니다. 제목이 없는 항목도 삭제하지 않습니다."""
    url, site, _ = article_url(url)
    previous = store.db.execute('SELECT * FROM daily_inventory WHERE target=? AND url=?', (target,url)).fetchone()
    title = title or (previous['title'] if previous else '')
    published = published or (previous['published'] if previous else '')
    annotation = annotate_candidate(title) if title else None
    with store.db:
        store.db.execute('INSERT OR REPLACE INTO daily_inventory VALUES (?,?,?,?,?,?,?)',
                         (target,url,site,title,published,json.dumps(annotation,ensure_ascii=False),origin))
    # 현재 승인된 제목 후보 규칙만 사용하며 본문 미검수는 비무역으로 기록하지 않습니다.
    return bool(annotation and annotation['label'] != '제목상 근거 약함')


def history_rows(store, target=None, sites=None):
    if target is None:
        return store.db.execute('SELECT * FROM article_history').fetchall()
    from news.date_index import sync_dates
    with store.db:sync_dates(store.db)
    placeholders=','.join('?' for _ in sites)
    return store.db.execute(f'SELECT h.* FROM article_history h JOIN article_dates d USING(url) WHERE d.day=? AND h.site IN ({placeholders})',
                            (target,*sites)).fetchall()


def history_urls(store, target=None, sites=None):
    if target is None:
        return {r[0] for r in store.db.execute('SELECT url FROM article_history')}
    return {r['url'] for r in history_rows(store,target,sites)}


def read_prepared_summary(path, target, sites):
    """오류 행을 별도 기록하고 정상 대상 날짜만 집계합니다. 준비 DB는 읽기 전용입니다."""
    prepared, errors = {}, []
    if not path.exists():
        return prepared, errors
    placeholders = ','.join('?' for _ in sites)
    with sqlite3.connect(path.as_uri()+'?mode=ro', uri=True) as db:
        # SQL json_extract는 과거 행 하나가 손상되어도 전체 조회를 중단하므로 행별로 해석합니다.
        rows = db.execute(f'SELECT source_code,external_id,payload FROM prepared_article WHERE source_code IN ({placeholders})', tuple(sites))
        for source, external_id, payload in rows:
            identity = {'source_code':source, 'external_id':external_id, 'published_date':None}
            try:
                a = json.loads(payload)
            except (ValueError, TypeError, UnicodeError):
                errors.append({**identity, 'reason_code':'invalid_json', 'reason':'준비 결과 JSON을 읽을 수 없어 날짜별 집계 보류'})
                continue
            try:
                if not isinstance(a, dict):
                    raise ValueError('준비 결과가 객체가 아님')
                day = date.fromisoformat(a['published_date']).isoformat()
                identity['published_date'] = day
            except (ValueError, TypeError, KeyError):
                errors.append({**identity, 'reason_code':'invalid_date', 'reason':'준비 결과 게시 날짜를 확인할 수 없어 날짜별 집계 보류'})
                continue
            if day != target:
                continue
            try:
                if (a['source_code'], a['external_id']) != (source, external_id):
                    raise ValueError('준비 결과 식별자 불일치')
                url, url_site, url_id = article_url(a['source_url'])
                if (url_site, url_id) != (source, external_id):
                    raise ValueError('준비 결과 URL 식별자 불일치')
                if (not isinstance(a['quality'], dict) or not isinstance(a['focus'], dict)
                        or type(a['quality']['usable_body']) is not bool
                        or type(a['focus']['accepted']) is not bool):
                    raise ValueError('준비 결과 품질·주제 판정 형식 오류')
            except (ValueError, TypeError, KeyError, AttributeError):
                errors.append({**identity, 'reason_code':'invalid_prepared_fields', 'reason':'준비 결과 식별자·URL·품질·주제 필드 오류로 집계 보류'})
                continue
            prepared[url] = a
    return prepared, errors


def daily_summary(store, target, sites, before, operational, event_offset=0):
    inventory = [dict(r) for r in store.db.execute('SELECT * FROM daily_inventory WHERE target=?',(target,)) if r['site'] in sites]
    from news.report import validate_report_row
    scoped = {r['url']:validate_report_row(r) for r in history_rows(store,target,sites)}
    prepared, prepared_errors = read_prepared_summary(store.root/'prepared.sqlite3', target, sites)
    queues = {}
    for r in inventory:
        row=store.db.execute('SELECT status FROM queue WHERE url=?',(r['url'],)).fetchone()
        if row:queues[r['url']]=row['status']
    candidates = [r for r in inventory if json.loads(r['annotation']) and json.loads(r['annotation'])['label']!='제목상 근거 약함']
    new = set(scoped)-before
    counts = {'listed_urls':len(inventory), 'titles_available':sum(bool(r['title']) for r in inventory),
              'missing_titles':sum(not r['title'] for r in inventory), 'title_candidates':len(candidates),
              'body_prepared':len(prepared), 'prepared_data_errors':len(prepared_errors), 'quality_failed':sum(not a['quality']['usable_body'] for a in prepared.values()),
              'topic_not_passed':sum(a['quality']['usable_body'] and not a['focus']['accepted'] for a in prepared.values()),
              'eligible_stored':len(scoped), 'new_records_this_attempt':len(new),
              'pending':sum(queues.get(r['url'])=='pending' for r in candidates),
              'parse_or_request_failed':sum(queues.get(r['url']) in ('failed','not_found','robots_denied') for r in candidates),
              'completed_no_body':sum(queues.get(r['url'])=='completed_no_body' for r in candidates),
              'paid_excluded':sum(queues.get(r['url'])=='excluded_paid' for r in candidates),
              'unreviewed_candidates':sum(r['url'] not in prepared and r['url'] not in scoped and queues.get(r['url']) not in ('excluded_paid','completed_no_body') for r in candidates)}
    requests = {'article':0, 'list':0, 'robots':0, 'total':0}
    events = store.root/'events.jsonl'
    if events.exists():
        with events.open('rb') as stream:
            stream.seek(event_offset)
            for line in stream:
                event=json.loads(line)
                if event.get('event')!='request' or event.get('site') not in sites:
                    continue
                url=event['url']
                kind='robots' if url.endswith('/robots.txt') else 'list'
                if kind!='robots':
                    try:
                        article_url(url)
                        kind='article'
                    except ValueError:
                        pass
                requests[kind]+=1
                requests['total']+=1
    discovery = [dict(r) for r in store.db.execute('SELECT * FROM discovery_queue WHERE target=?',(target,)) if r['site'] in sites]
    counts['list_pages_known'] = len(discovery)
    counts['list_pages_processed'] = sum(r['status']=='processed' for r in discovery)
    counts['list_pages_pending'] = sum(r['status']=='pending' for r in discovery)
    counts['list_pages_failed'] = sum(r['status']=='failed' for r in discovery)
    reasons=[]
    if counts['list_pages_pending']: reasons.append('처리하지 못한 목록 페이지가 남아 있음')
    if counts['list_pages_failed']: reasons.append('목록 형식·추출 실패 항목 있음')
    if counts['missing_titles']: reasons.append('제목 미확보 항목 있음')
    if counts['unreviewed_candidates']: reasons.append('본문 미검수 후보 있음')
    for site, status in operational['sites'].items():
        if status['status'] not in ('available_candidates_processed',): reasons.append(site+': '+status['status'])
    from news.unfinished import summarize_unfinished
    unfinished=summarize_unfinished(store.root,target,sites,operational['sites'][sites[0]]['daily_limit'])
    from news.date_index import data_issues
    issues=data_issues(store.db)
    issues['prepared_errors'] = prepared_errors
    issues['prepared_summary_complete'] = not prepared_errors
    if prepared_errors:
        reasons.append('준비 데이터 오류 '+str(len(prepared_errors))+'건은 원본을 보존하고 집계를 보류했습니다. data_issues.prepared_errors 확인')
    if issues['date_errors']:reasons.append(issues['note'])
    return {'data_issues':issues,'report_scope':'target_date','full_inventory_report_refreshed':False,
            'unfinished':unfinished,'target_date':target,'timezone':'Asia/Seoul','goal_hint':10,'article_cap':None,
            'counts':counts,'requests_this_attempt':requests,'notes':reasons,'coverage_complete':False,
            'coverage_note':'확보한 공식 목록 범위이며 해당 날짜 전수 탐색을 보장하지 않음',
            'mysql_loaded':False,'operational':operational,'inventory':inventory,'list_pages':discovery,
            'articles':[{'url':u,'title':a['title'],'new_this_attempt':u in new} for u,a in scoped.items()]}
