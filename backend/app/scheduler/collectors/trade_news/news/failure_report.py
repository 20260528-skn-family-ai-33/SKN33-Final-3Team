"""저장·집계 실패를 이전 성공 결과와 구분해 기록합니다. 네트워크는 사용하지 않습니다."""
import json
import os
import sys
from datetime import datetime
from news.save import KST
from news.date_index import data_issues
from news.run_lifecycle import atomic_json, record_failure


def write_failure_report(store, args, stage, error, started_at, run=None):
    """보고서 디스크도 쓰기 불능이면 stderr에 알리고 원래 오류 종료를 유지합니다."""
    now=datetime.now(KST)
    target=args.daily_target.isoformat() if args.daily_target else None
    report={'run_id':os.environ.get('TRADE_NEWS_RUN_ID'), 'generated_at':now.isoformat(), 'started_at':started_at,
            'target_date':target,'timezone':'Asia/Seoul',
            'execution_healthy':False,'status':'execution_error',
            'failed_stage':stage,'error':{'type':type(error).__name__,'message':str(error)},
            'counts':None,'counts_available':False,
            'notes':['이번 실행이 실패했습니다. 이전 성공 건수를 이번 결과로 사용하지 않습니다.'],
            'mysql_loaded':False, 'report_scope':'target_date' if target else 'run'}
    if run:
        report.update(run_id=run['run_id'], attempt_id=run['attempt_id'], finished_at=now.isoformat())
    try:
        report['pending_json_exports']=store.db.execute('SELECT COUNT(*) FROM export_pending').fetchone()[0]
        report['data_issues']=data_issues(store.db)
    except Exception as diagnostic_error:
        report['diagnostic_error']=str(diagnostic_error)
    if run:
        try:
            report = record_failure(store.root,run,report)
        except Exception as write_error:
            # 확정 파일을 쓸 수 없으면 기존 성공 결과를 임의로 덮어쓰지 않습니다.
            print(f'실행 실패 확정 기록 불가: {write_error}',file=sys.stderr)
        print(json.dumps(report,ensure_ascii=False),file=sys.stderr)
        return report
    paths=[store.root/'run_summary.json']
    if target:
        directory=store.root/'daily_runs'/target
        paths.extend([directory/'latest.json',directory/(now.strftime('%Y%m%dT%H%M%S%f')+'.json')])
    # 한 경로에 기록하지 못해도 다른 보고서 경로 기록을 각각 시도합니다.
    for path in paths:
        try:atomic_json(path,report)
        except Exception as write_error:
            print(f'실패 보고서 기록 불가: {path}: {write_error}',file=sys.stderr)
    print(json.dumps(report,ensure_ascii=False),file=sys.stderr)
    return report
