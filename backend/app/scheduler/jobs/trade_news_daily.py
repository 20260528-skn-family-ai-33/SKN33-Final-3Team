"""전날 기사 수집 → 검수 통과 기사 적재. 등록은 기본 일시중지, import만으로 실행하지 않음."""
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo
from backend.app.scheduler.collectors.trade_news.news.run_lifecycle import atomic_json
from backend.app.scheduler.jobs.news_process import run_bounded

ROOT = Path(__file__).resolve().parents[4]
ENV_PATH = ROOT / '.env'
CONFIGURATION_ERRORS = ROOT / 'tmp/trade_news_job_errors'
COLLECTOR = Path(__file__).resolve().parents[1] / 'collectors/trade_news'
KST = ZoneInfo('Asia/Seoul')
SITES = ('hankyung', 'fnnews')
SOURCE_CODES = {'hankyung': 'HANKYUNG', 'fnnews': 'FNNEWS'}
JOB_ID = 'trade_news_daily'
log = logging.getLogger(__name__)
# 두 사이트의 100회 × (최대 간격 300초 + 요청 제한 60초)에 여유 2시간을 둡니다.
DEFAULT_COLLECTION_TIMEOUT = 22 * 3600
DEFAULT_LOAD_TIMEOUT = 3600


def time_limits(values):
    limits = {}
    for stage, key, default in (
        ('collection', 'NEWS_COLLECTION_TIMEOUT_SECONDS', DEFAULT_COLLECTION_TIMEOUT),
        ('mysql', 'NEWS_LOAD_TIMEOUT_SECONDS', DEFAULT_LOAD_TIMEOUT),
        ('dictionary', 'NEWS_DICTIONARY_TIMEOUT_SECONDS', DEFAULT_LOAD_TIMEOUT),
        ('vector', 'NEWS_VECTOR_TIMEOUT_SECONDS', DEFAULT_LOAD_TIMEOUT),
    ):
        value = str(values.get(key, default)).strip()
        if not value.isascii() or not value.isdecimal() or int(value) <= 0:
            raise ValueError(f'{key}: 양의 정수 초 단위 필요')
        limits[stage] = int(value)
    return limits


def settings(env_path=ENV_PATH):
    from dotenv import dotenv_values
    return {**dotenv_values(env_path), **os.environ}


def register_jobs(scheduler, *, env_path=ENV_PATH):
    """일정 정의만 등록합니다. 스케줄러 시작·즉시 실행·DB 연결은 하지 않습니다."""
    from apscheduler.triggers.cron import CronTrigger
    enabled = str(settings(env_path).get('NEWS_DAILY_ENABLED', 'false')).strip().lower() in ('true', '1', 'yes')
    options = {} if enabled else {'next_run_time': None}
    scheduler.add_job(run_with_rag, CronTrigger(hour=3, minute=0, second=0, timezone=KST),
                      id=JOB_ID, kwargs={'env_path': env_path}, max_instances=1, coalesce=True,
                      misfire_grace_time=6 * 3600, replace_existing=True, **options)


def run_with_rag(data_dir=None, source_map=None, *, now=None, runner=None, env_path=ENV_PATH):
    """예약 진입점: 동일 실행 잠금 안에서 수집·SQL·벡터를 단계별 제한 시간으로 실행합니다."""
    return run(data_dir, source_map, now=now, runner=runner, env_path=env_path, include_vector=True)


def absolute_path(value):
    path = Path(value).expanduser()
    return (path if path.is_absolute() else ROOT / path).resolve()


def data_settings(data_dir, env_path):
    values = settings(env_path)
    data_dir = data_dir or values.get('NEWS_CRAWL_DATA_DIR')
    if not data_dir:
        raise ValueError('기존 NEWS_CRAWL_DATA_DIR 설정 필요')
    root = absolute_path(data_dir)
    budget = root / 'articles.sqlite3'
    if not budget.is_file():
        raise ValueError('기존 요청 예산 DB 없음')
    with closing(sqlite3.connect(budget.as_uri() + '?mode=ro', uri=True)) as db:
        db.execute('SELECT day,site,count FROM budget LIMIT 0')
    return root, values


def mapping_settings(source_map, values):
    mapping = absolute_path(source_map or values.get('NEWS_SOURCE_MAP') or COLLECTOR / 'source_map.sample.json')
    codes = json.loads(mapping.read_text(encoding='utf-8'))
    # 자동 실행에서는 수집 이력·적재·집계 뷰가 동일한 팀 SOURCE를 사용해야 합니다.
    if (not isinstance(codes, dict) or not codes
            or any(not isinstance(v, str) or not v.strip() or len(v) > 40 for v in codes.values())
            or len(set(codes.values())) != len(codes)
            or any(codes.get(site) != code for site, code in SOURCE_CODES.items())):
        raise ValueError('팀 SOURCE 매핑과 불일치')
    return mapping


def persist(root, result):
    """current_job.json이 기준. 날짜별 파일은 표시용이므로 출력 실패와 작업 실패를 구분합니다."""
    result['report_errors'] = []
    atomic_json(root / 'current_job.json', result)
    directory = root / 'daily_runs' / result['target_date']
    for path in (directory / ('job_' + result['run_id'] + '.json'), directory / 'job_latest.json'):
        try:
            atomic_json(path, result)
        except Exception as exc:
            result['report_errors'].append({'file': path.name, 'error_type': type(exc).__name__})
            log.warning('기사 일일 작업 표시 파일 출력 실패 (%s)', type(exc).__name__)
    if result['report_errors']:
        atomic_json(root / 'current_job.json', result)


def recover_previous(root):
    path = root / 'current_job.json'
    if not path.exists():
        return
    previous = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(previous, dict) or not re.fullmatch(r'[a-f0-9]{32}', previous.get('run_id', '')):
        raise ValueError('일일 작업 상태 형식 오류')
    if date.fromisoformat(previous['target_date']).isoformat() != previous['target_date']:
        raise ValueError('일일 작업 날짜 형식 오류')
    # 잠금 획득 후 이전 부모 작업은 종료됨을 확인합니다. 과거 기사를 다시 수집하지 않습니다.
    if previous['status'] == 'running':
        previous.update(status='interrupted', finished_at=datetime.now(KST).isoformat(),
                        interruption_detected=True)
        stage = previous.get('stage')
        if stage in ('collection', 'mysql', 'dictionary', 'vector') and previous.get(stage, {}).get('status') == 'running':
            previous[stage]['status'] = 'interrupted'
    persist(root, previous)


def collection_result(root, run_id, target):
    """이전 성공/표시용 캐시 대신 이번 실행의 확정 결과만 적재와 연결합니다."""
    current = json.loads((root / 'current_run.json').read_text(encoding='utf-8'))
    if (current.get('run_id') != run_id or current.get('target_date') != target
            or current.get('status') not in ('completed', 'execution_error')):
        raise ValueError('이번 수집 종료 결과 미확정')
    ref = current.get('history', {}).get(current.get('attempt_id'))
    if not isinstance(ref, str) or not re.fullmatch(r'[a-f0-9]{32}\.json', ref):
        raise ValueError('수집 결과 참조 오류')
    bundle = json.loads((root / 'run_records' / ref).read_text(encoding='utf-8'))
    report = bundle['daily']
    if (bundle['current'].get('attempt_id') != current['attempt_id']
            or report.get('run_id') != run_id or report.get('target_date') != target
            or set(report.get('operational', {}).get('sites', {})) != set(SITES)):
        raise ValueError('수집 결과 날짜·출처·실행 ID 불일치')
    return report, 'run_records/' + ref


def collection_status(report, returncode):
    if returncode or not report.get('execution_healthy'):
        return 'failed'
    sites = report['operational']['sites']
    counts = report.get('counts') or {}
    if (any(sites[site].get('status') != 'available_candidates_processed' for site in SITES)
            or any(counts.get(k, 0) for k in ('pending', 'unreviewed_candidates', 'parse_or_request_failed',
                                             'list_pages_pending', 'list_pages_failed'))):
        return 'partial'
    return 'complete'


def run(data_dir=None, source_map=None, *, now=None, runner=None, env_path=ENV_PATH, include_vector=False):
    """날짜는 시작 시 한 번 고정합니다. 실제 예약 실행은 scheduler.start() 이후에만 가능합니다."""
    from filelock import FileLock, Timeout
    runner = runner or run_bounded
    started = now or datetime.now(KST)
    if started.tzinfo is None:
        raise ValueError('실행 시각의 시간대 필요')
    target = (started.astimezone(KST).date() - timedelta(days=1)).isoformat()
    run_id = uuid4().hex
    result = {'run_id': run_id, 'target_date': target, 'sites': list(SITES), 'timezone': 'Asia/Seoul',
              'started_at': started.astimezone(KST).isoformat(), 'status': 'running', 'stage': 'configuration',
              'collection': {'status': 'not_started'}, 'mysql': {'status': 'not_started'},
              'dictionary': {'status':'not_started'}, 'vector': {'status':'not_started'}, 'usage':{k:{'tokens':0,'estimated_usd':0.0,'model_calls':0} for k in ('dictionary','collection','mysql')}}
    try:
        root, values = data_settings(data_dir, env_path)
    except Exception as exc:
        result.update(status='failed', error_type=type(exc).__name__, finished_at=datetime.now(KST).isoformat())
        log.error('기사 실행 설정 오류 (%s). 기존 예산 DB와 SOURCE 설정을 확인하세요.', type(exc).__name__)
        try:
            atomic_json(CONFIGURATION_ERRORS / ('job_' + run_id + '.json'), result)
        except Exception as write_error:
            result['report_error_type'] = type(write_error).__name__
        return result
    try:
        # 자식의 crawler.lock과 분리해 수집부터 적재까지 전체 작업의 중복 호출을 막습니다.
        with FileLock(str(root / 'daily_job.lock'), timeout=0):
            # 부모만 종료되어도 자식은 crawler.lock을 보유합니다. 살아 있는 작업의 상태는 덮어쓰지 않습니다.
            # 새 실행 ID 확정까지 잠가, 아직 시작하지 못한 이전 자식도 이후 단계에서 식별할 수 있게 합니다.
            with FileLock(str(root / 'crawler.lock'), timeout=0):
                recover_previous(root)
                persist(root, result)
            try:
                # 경로가 확인된 뒤의 매핑 오류도 이번 실행의 실패로 남깁니다.
                mapping = mapping_settings(source_map, values)
                limits = time_limits(values)
                result['timeouts_seconds'] = limits
                directory = root / 'daily_runs' / target
                env = {k: str(v) for k, v in values.items() if v is not None}
                env['TRADE_NEWS_RUN_ID'] = run_id
                env['TRADE_NEWS_JOB_RUN_ID'] = run_id
                # 현재 공용 스케줄러의 Python을 사용하고 Scrapy reactor는 자식에만 설치합니다.
                with (directory / ('job_' + run_id + '.log')).open('a', encoding='utf-8') as stream:
                    dictionary_args = []
                    if include_vector:
                        result['stage'] = 'dictionary'
                        result['dictionary'] = {'status':'running'}
                        persist(root, result)
                        dictionary_path = directory / ('tag_dictionary_' + run_id + '.json')
                        exported = runner([sys.executable, str(COLLECTOR / 'load_project.py'),
                            '--data-dir', str(root), '--source-map', str(mapping), '--apply',
                            '--export-tag-dictionary', str(dictionary_path)], cwd=COLLECTOR, env=env,
                            stdout=stream, stderr=subprocess.STDOUT, check=False, timeout=limits['dictionary'])
                        if exported.returncode or not dictionary_path.is_file():
                            raise ValueError('project_dictionary_export_failed')
                        dictionary_args = ['--tag-dictionary', str(dictionary_path)]
                        result['dictionary'] = {'status':'complete', 'file':str(dictionary_path.relative_to(root))}
                    elif values.get('NEWS_TAG_DICTIONARY'):
                        dictionary_args = ['--tag-dictionary', str(absolute_path(values['NEWS_TAG_DICTIONARY']))]
                    result['stage'] = 'collection'
                    result['collection'] = {'status': 'running'}
                    persist(root, result)
                    collected = runner([sys.executable, str(COLLECTOR / 'daily_collect.py'), '--data-dir', str(root),
                                        '--target-date', target, '--sites', *SITES, '--record-batch-run', *dictionary_args],
                                       cwd=COLLECTOR, env=env, stdout=stream, stderr=subprocess.STDOUT, check=False,
                                       timeout=limits['collection'])
                    report, reference = collection_result(root, run_id, target)
                    # 상세 목록·기사·오류는 확정 보고서 한 곳에 두고 상위 결과에는 요약과 참조만 보관합니다.
                    result['collection'] = {'status': collection_status(report, collected.returncode),
                                            'returncode': collected.returncode, 'report_file': reference,
                                            'counts': report.get('counts', {}),
                                            'requests_this_attempt': report.get('requests_this_attempt', {}),
                                            'sites': report['operational']['sites']}
                    # 일부 수집 실패가 있어도 저장된 정상 기사만 기존 적재 검증으로 확인합니다.
                    # 실행 미확정/다른 실행의 보고서는 위에서 중단하므로 이전 성공으로 진행하지 않습니다.
                    result['stage'] = 'mysql'
                    result['mysql'] = {'status': 'running'}
                    persist(root, result)
                    output = directory / ('mysql_' + run_id + '.json')
                    loaded = runner([sys.executable, str(COLLECTOR / 'load_project.py'), '--data-dir', str(root),
                                     '--source-map', str(mapping), '--target-date', target, '--sites', *SITES,
                                     '--result-file', str(output), '--apply', '--record-batch-run',
                                     '--include-tags', '--generate-tags'],
                                    cwd=COLLECTOR, env=env, stdout=stream, stderr=subprocess.STDOUT, check=False,
                                    timeout=limits['mysql'])
                    mysql = json.loads(output.read_text(encoding='utf-8'))
                    if (mysql.get('run_id') != run_id or mysql.get('target_date') != target
                            or mysql.get('sites') != list(SITES) or mysql.get('mode') != 'applied'
                            or mysql.get('status') not in ('complete', 'partial', 'failed')):
                        raise ValueError('이번 적재 결과 식별자·상태 불일치')
                    summary_keys = ('run_id', 'status', 'target_date', 'sites', 'mode', 'mysql_connected',
                                    'articles_to_store', 'inserted', 'existing', 'failed', 'unprocessed',
                                    'stored_articles', 'batch_run', 'error_type', 'close_error_type',
                                    'include_tags', 'generate_tags', 'tag_generation')
                    result['mysql'] = {**{k: mysql[k] for k in summary_keys if k in mysql},
                                       'returncode': loaded.returncode, 'result_file': str(output.relative_to(root)),
                                       'rejected_count': len(mysql.get('rejected', [])),
                                       'unscoped_error_count': len(mysql.get('unscoped_errors', []))}
                    statuses = (result['collection']['status'], mysql['status'])
                    result['status'] = ('failed' if 'failed' in statuses or loaded.returncode not in (0, 1)
                                        else 'partial' if 'partial' in statuses or loaded.returncode
                                        else 'complete')
                    if include_vector and mysql['status'] in ('complete', 'partial') and loaded.returncode in (0, 1):
                        before_vector = result['status']
                        result.update(status='running', stage='vector')
                        result['vector'] = {'status':'running', 'sources':list(SOURCE_CODES.values())}
                        persist(root, result)
                        vector_output = directory / ('vector_' + run_id + '.json')
                        vector_args = []
                        if values.get('NEWS_VECTOR_ROOT'):
                            vector_args = ['--root', str(absolute_path(values['NEWS_VECTOR_ROOT']))]
                        indexed = runner([sys.executable, str(Path(__file__).with_name('news_vector.py')),
                            '--data-dir', str(root), '--target-date', target, '--result-file', str(vector_output),
                            *vector_args], cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT,
                            check=False, timeout=limits['vector'])
                        vector = json.loads(vector_output.read_text())
                        if (vector.get('run_id') != run_id or vector.get('target_date') != target
                                or vector.get('sources') != list(SOURCE_CODES.values())
                                or vector.get('status') not in ('complete','partial','failed')):
                            raise ValueError('vector_result_identity_mismatch')
                        result['vector'] = {**vector, 'returncode':indexed.returncode,
                                            'result_file':str(vector_output.relative_to(root))}
                        result['usage']['vector'] = vector.get('usage', {})
                        result['status'] = before_vector
                        if indexed.returncode or vector['status'] != 'complete':
                            result['status'] = 'partial' if result['status'] != 'failed' else 'failed'
                    result['stage'] = 'finished'
            except Exception as exc:
                result.update(status='partial' if result['stage']=='vector' and result['mysql'].get('status') in ('complete','partial') and result['collection'].get('status') != 'failed' else 'failed', error_type=type(exc).__name__)
                if result['stage'] in ('collection', 'mysql', 'dictionary', 'vector'):
                    result[result['stage']]['status'] = 'failed'
                    if result['stage'] == 'vector':
                        # 강제 종료 전 자식이 확정한 단계·사용량만 보존합니다. 이전 실행 파일은 읽지 않습니다.
                        try:
                            partial = json.loads(vector_output.read_text())
                            if partial.get('run_id') == run_id and partial.get('target_date') == target:
                                result['vector'].update(partial, status='failed', error_code=type(exc).__name__)
                                result['usage']['vector'] = partial.get('usage', {})
                        except (OSError, ValueError):
                            pass
                    if isinstance(exc, subprocess.TimeoutExpired):
                        result[result['stage']].update(reason='timeout', timeout_seconds=exc.timeout)
                log.error('기사 일일 작업 실패: 단계=%s, 오류=%s', result['stage'], type(exc).__name__)
            finally:
                result['finished_at'] = datetime.now(KST).isoformat()
                persist(root, result)
    except Timeout as exc:
        result.update(status='already_running', stage='lock', active_lock=Path(exc.lock_file).name)
        log.info('기사 일일 작업 또는 자식 처리 중: 기존 상태를 유지하고 중복 호출을 건너뜁니다.')
    except Exception as exc:
        result.update(status='failed', error_type=type(exc).__name__)
        log.error('기사 일일 작업 상태/잠금 오류 (%s)', type(exc).__name__)
    log.info('기사 일일 작업: 날짜=%s 상태=%s 신규=%s 기존=%s 실패=%s', target, result['status'],
             result['mysql'].get('inserted', 0), result['mysql'].get('existing', 0), result['mysql'].get('failed', 0))
    return result
