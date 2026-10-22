"""수집 종료와 목표 달성을 구별하는 운영 요약입니다. 요청이나 상태 변경은 하지 않습니다."""
import time
from datetime import datetime
from news.save import KST


def summarize_run(store, sites, before, stats, daily_limit, target=0, discover=False, target_date=None, list_urls=None):
    # 엔진 종료만으로 성공이라고 판단하지 않습니다. 오류와 목표 수치는 별도로 표시합니다.
    error_keys = ('spider_exceptions/count', 'item_error_count', 'log_count/ERROR', 'log_count/CRITICAL')
    errors = {key: stats[key] for key in error_keys if stats.get(key)}
    finish = stats.get('finish_reason')
    healthy = not errors and finish == 'finished'
    results = {}
    for site in sites:
        saved = store.count_articles(site)
        pending_all = len(store.pending(site))
        pending = (store.db.execute("SELECT COUNT(*) FROM queue q JOIN queue_period p USING(url) WHERE q.site=? AND q.status='pending' AND p.since_date=? AND p.until_date=?",(site,target_date,target_date)).fetchone()[0] if target_date else pending_all)
        used = store.used(site)
        # state()는 없는 행을 생성하므로 요약에서는 직접 조회합니다.
        row = store.db.execute('SELECT * FROM state WHERE site=?', (site,)).fetchone()
        state = dict(row) if row else {}
        list_blocks = [dict(r) for r in store.db.execute('SELECT path,url,reason FROM list_blocks WHERE site=?',(site,))]
        list_retries = [dict(r) for r in store.db.execute('SELECT url,attempts,next_at FROM list_retries WHERE site=?',(site,))]
        historical_blocks, historical_retries = [], []
        if target_date and list_urls is not None:
            from urllib.parse import urlsplit
            paths={urlsplit(u).path for u in list_urls}
            historical_blocks=[r for r in list_blocks if r['path'] not in paths]
            historical_retries=[r for r in list_retries if r['url'] not in list_urls]
            list_blocks=[r for r in list_blocks if r['path'] in paths]
            list_retries=[r for r in list_retries if r['url'] in list_urls]
        access = store.db.execute('SELECT needs_probe FROM body_access WHERE site=?',(site,)).fetchone()
        needs_probe = bool(access and access[0])
        met = saved >= target if target else None
        if not healthy:
            status = 'execution_error'
        elif state.get('manual'):
            status = 'manual_stop'
        elif state.get('until', 0) > time.time():
            status = 'cooldown'
        elif needs_probe:
            status = 'body_access_unverified'
        elif met:
            status = 'target_reached'
        elif used >= daily_limit:
            status = 'daily_budget_reached'
        elif list_blocks:
            status = 'list_paths_blocked'
        elif list_retries:
            status = 'list_retry_pending'
        elif pending:
            status = 'pending_remaining'
        else:
            status = 'available_candidates_processed'
        # 목록 소진은 해당 기간의 모든 기사를 찾았다는 뜻이 아닙니다.
        results[site] = {
            'target_date':target_date,'pending_all_dates':pending_all,
            'historical_list_path_blocks':historical_blocks,'historical_list_retries':historical_retries,
            'list_retries': list_retries,
            'list_path_blocks': list_blocks, 'body_access_unverified': needs_probe,
            'status': status, 'saved_total': saved,
            'stored_count_change': saved - before.get(site, saved),
            'target': target or None, 'target_reached': met,
            'remaining_to_target': max(0, target-saved) if target else None,
            'pending': pending, 'used_today': used,
            'daily_limit': daily_limit, 'remaining_requests_today': max(0, daily_limit-used),
            'queue_status_counts': dict(store.db.execute(
                'SELECT status,COUNT(*) FROM queue WHERE site=? GROUP BY status', (site,))),
            'stop_reason': state.get('reason') if status in ('manual_stop', 'cooldown') else None,
            'resume_after': state.get('until') if status == 'cooldown' else None,
            'more_discovery_needed': bool(target and not met and not pending),
            'discovery_counts': {key.split('/')[-1]:value for key,value in stats.items()
                                 if key.startswith(f'discovery/{site}/')},
        }
    return {'generated_at': datetime.now(KST).isoformat(), 'finish_reason': finish,
            'execution_healthy': healthy, 'errors': errors, 'discovery_enabled': discover,
            'coverage_complete': False,
            'coverage_note': '제한된 공식 목록과 입력 URL 처리 결과이며 전체 기사 탐색 완료를 뜻하지 않습니다.',
            'sites': results}
