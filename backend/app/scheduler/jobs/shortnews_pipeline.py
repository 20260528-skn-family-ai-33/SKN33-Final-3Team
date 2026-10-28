"""단신속보 원본 → 최신 분류 후보·LLM → MySQL → 공통 kotra_article 본문 세대 + news_keyword.

어느 단계든 실패하면 성공으로 보고하지 않습니다. MySQL 커밋 후 Chroma 실패는
동일 기사 ID upsert와 내용 기반 분석 캐시로 다음 실행에서 복구합니다.
"""
import argparse
from copy import deepcopy
import hashlib
import json
import logging
import os
from pathlib import Path
from uuid import uuid4

from dotenv import dotenv_values
from filelock import FileLock
from sqlalchemy import text

from backend.app.db.news_lock import database_identity
from backend.app.db.session import make_engine, mysql_url
from backend.app.services import shortnews_analysis as analysis, shortnews_keywords as keywords
from backend.app.services import news_embedding as shared_embedding
from backend.app.services import shortnews_load as service, shortnews_vectors as vectors
from backend.app.scheduler.jobs import shortnews_load as loader

ROOT = Path(__file__).resolve().parents[4]
log = logging.getLogger(__name__)


def run(raw_path, *, db_url=None, env_path=ROOT / '.env', chroma_path=ROOT / 'chroma_data',
        log_dir=ROOT / 'data/shortnews/pipeline_logs', period=None, api=None, client=None, vector_root=None):
    import chromadb
    from openai import OpenAI
    directory = Path(log_dir)
    directory.mkdir(parents=True, exist_ok=True)
    report = {'run_id':uuid4().hex, 'status': 'RUNNING', 'stage': 'prepare', 'mysql': 'NOT_STARTED', 'chroma': 'NOT_STARTED',
              'usage':{}, 'usage_policy':'current_attempt_only; chroma_result usage is a subset, do not sum twice'}
    engine = None
    owned_api = api is None
    owned_client = client is None
    primary = None
    try:
        raws = service.read_records(raw_path)
        articles, originals, _ = loader.prepare(raws, [])
        engine = make_engine(db_url or mysql_url(env_path))
        identity = database_identity(engine.url)
        lock_dir = ROOT / 'data/shortnews/locks'
        lock_dir.mkdir(parents=True, exist_ok=True)
        with FileLock(str(lock_dir / (identity + '.pipeline.lock')), timeout=0):
            if not articles:
                report['stage'] = 'mysql'
                if loader.run(raw_path, db_url=engine.url, log_dir=directory / 'mysql', period=period):
                    raise RuntimeError('MySQL 빈 날짜 처리 실패')
                report.update(status='SUCCESS', stage='finished', mysql='SUCCESS', chroma='SUCCESS',
                              articles=0, analysis_cached=0, analysis_requested=0,
                              chroma_result={'articles': 0, 'updated': 0, 'existing': 0})
                return report
            values = {**dotenv_values(env_path), **os.environ}
            api = api or OpenAI(api_key=values.get('OPENAI_API_KEY'), timeout=60, max_retries=0)
            client = client or chromadb.PersistentClient(path=str(chroma_path))
            with keywords.index_lock(client):
                vector_stage = ['body']
                def record(stage, value):
                    counter = report['usage'].setdefault(stage, {'model':value['model'], 'model_calls':0,
                        'tokens':0, 'input_tokens':0, 'output_tokens':0, 'reported_responses':0, 'unreported_calls':0,
                        'estimated_usd':None})
                    counter['model_calls'] += 1
                    counter['reported_responses' if value['reported'] else 'unreported_calls'] += 1
                    for key in ('tokens','input_tokens','output_tokens'):
                        counter[key] += value.get(key) or 0
                    if (value['model'] == shared_embedding.MODEL and counter['reported_responses']
                            and (stage in ('body', 'keywords') or keywords.MODEL == shared_embedding.MODEL)):
                        from backend.app.scheduler.jobs.news_embed import PRICE_PER_M
                        counter['estimated_usd'] = counter['tokens'] * PRICE_PER_M / 1e6
                    analysis.atomic_json(directory / 'pipeline_result.json', report)
                def tracked_encode(embedding, current_stage):
                    def encode(texts):
                        def counted(value):
                            encode.usage['tokens'] += value['tokens'] or 0
                            encode.usage['reported_responses'] += int(value['reported'])
                            record(current_stage(), value)
                        return embedding(api, texts, usage_sink=counted)
                    encode.usage = {'tokens':0, 'reported_responses':0}
                    return encode
                classification_encode = tracked_encode(analysis.embed, lambda:
                    'candidate_query' if report['stage'] == 'analysis' else report['stage'])
                common_encode = tracked_encode(shared_embedding.embed, lambda: vector_stage[0])
                report['stage'] = 'classification_index'
                snapshot = keywords.read_catalog(engine)
                index, report['keyword_index'] = keywords.sync_index(snapshot, client, classification_encode)
                report['stage'] = 'analysis'
                results, cache_hits = {}, 0
                for article in articles:
                    result, cached = analysis.analyze(article, snapshot, index, classification_encode,
                        lambda row, choices: analysis.ask(api, row, choices,
                            usage_sink=lambda value: record('analysis_llm',value)), ROOT / 'data/shortnews/analysis_cache')
                    results[article['external_id']] = result
                    cache_hits += int(cached)
                report.update(articles=len(articles), analysis_cached=cache_hits,
                              analysis_requested=len(articles) - cache_hits, classification_revision=snapshot.revision)
                if keywords.read_catalog(engine).revision != snapshot.revision:
                    raise ValueError('분석 중 DB 분류 변경')
                with engine.connect() as conn:
                    countries = list(conn.execute(text('SELECT country_code,name_ko FROM country')).mappings())
                lookup = service.country_lookup(countries)
                cleans = [{'nttSn': a['external_id'], 'kw_topic': '|'.join(results[a['external_id']]['keywords']),
                           'country_all': '|'.join(service.country_codes(originals[a['external_id']].get('nat'), lookup)[0])} for a in articles]
                clean_path = directory / 'shortnews_selected_keywords.json'
                analysis.atomic_json(clean_path, cleans)
                report['stage'] = 'mysql'
                if loader.run(raw_path, clean_path, db_url=engine.url, log_dir=directory / 'mysql', period=period):
                    raise RuntimeError('MySQL 적재 실패')
                report['mysql'] = 'SUCCESS'
                report['stage'] = 'chroma'
                # 요약은 재시도 캐시와 이 실행의 결과 파일에 보존합니다. 본문 임베딩 입력에는 넣지 않습니다.
                summary_path = directory / 'shortnews_analysis_results.json'
                analysis.atomic_json(summary_path, {
                    'source_code': service.SOURCE_CODE, 'classification_revision': snapshot.revision,
                    'articles': {a['external_id']: {
                        **results[a['external_id']],
                        'body_hash': hashlib.sha256(a['body_masked'].encode('utf-8')).hexdigest()
                    } for a in articles}})
                report['analysis_result_file'] = str(summary_path)
                def checkpoint(value):
                    vector_stage[0] = 'keywords' if value.get('keywords',{}).get('status')=='running' else 'body'
                    report['chroma_result'] = deepcopy(value)
                    analysis.atomic_json(directory / 'pipeline_result.json', report)
                report['chroma_result'] = vectors.sync_body(db_url=engine.url, root=vector_root,
                                                            embed=common_encode, progress=checkpoint)
                if report['chroma_result']['status'] != 'complete':
                    report.update(status='PARTIAL', chroma='PARTIAL' if report['chroma_result']['body']['status'] == 'complete' else 'FAILED',
                                  error_code=report['chroma_result'].get('error_code'))
                    return report
                report['chroma'] = 'SUCCESS'
                if keywords.read_catalog(engine).revision != snapshot.revision:
                    raise ValueError('저장 중 DB 분류 변경. 재실행 필요')
                report['stage'] = 'index_cleanup'
                report['removed_keyword_indexes'] = keywords.cleanup_indexes(snapshot, client)
                report.update(status='SUCCESS', stage='finished')
    except Exception as exc:
        report.update(status='PARTIAL' if report['mysql'] == 'SUCCESS' else 'FAILED', error_type=type(exc).__name__)
        if report['stage'] == 'chroma':
            report['chroma'] = 'FAILED'
        log.error('단신속보 처리 실패: 단계=%s 오류=%s', report['stage'], type(exc).__name__)
    except BaseException as exc:
        primary = exc
        raise
    finally:
        cleanup_interrupt = None
        if primary is not None:
            report.update(status='PARTIAL' if report['mysql'] == 'SUCCESS' else 'FAILED',
                          error_type=type(primary).__name__)
        cleanup_errors = []
        for name, resource, owned, method in (
                ('database', engine, True, 'dispose'),
                ('openai', api, owned_api, 'close'),
                ('chroma', client, owned_client, 'close')):
            if resource is not None and owned:
                try:
                    getattr(resource, method)()
                except BaseException as exc:
                    cleanup_errors.append({'resource': name, 'error_type': type(exc).__name__})
                    if primary is not None:
                        primary.add_note(f'cleanup_failed:{name}:{type(exc).__name__}')
                    elif not isinstance(exc, Exception):
                        if cleanup_interrupt is None:
                            cleanup_interrupt = exc
                        else:
                            cleanup_interrupt.add_note(f'cleanup_failed:{name}:{type(exc).__name__}')
        if cleanup_errors:
            report['cleanup_errors'] = cleanup_errors
            # 이미 실패한 작업의 최초 원인은 보존하고, 성공한 작업도 종료 실패를 숨기지 않습니다.
            if report['status'] not in ('FAILED', 'PARTIAL'):
                report.update(status='FAILED', stage='resource_cleanup', error_type=cleanup_errors[0]['error_type'])
        try:
            analysis.atomic_json(directory / 'pipeline_result.json', report)
        except BaseException as exc:
            interrupted = primary if primary is not None else cleanup_interrupt
            if interrupted is None:
                raise
            interrupted.add_note(f'pipeline_report_write_failed:{type(exc).__name__}')
        if cleanup_interrupt is not None:
            raise cleanup_interrupt
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--raw', type=Path, required=True)
    parser.add_argument('--env', type=Path, default=ROOT / '.env')
    parser.add_argument('--chroma-path', type=Path, default=ROOT / 'chroma_data')
    parser.add_argument('--vector-root', type=Path, help='공통 본문 세대 경로. 생략 시 공통 기본 경로 사용')
    parser.add_argument('--log-dir', type=Path, default=ROOT / 'data/shortnews/pipeline_logs')
    args = parser.parse_args()
    report = run(args.raw, env_path=args.env, chroma_path=args.chroma_path, log_dir=args.log_dir, vector_root=args.vector_root)
    print(json.dumps(report, ensure_ascii=False))
    raise SystemExit(0 if report['status'] == 'SUCCESS' else 1)


if __name__ == '__main__': main()
