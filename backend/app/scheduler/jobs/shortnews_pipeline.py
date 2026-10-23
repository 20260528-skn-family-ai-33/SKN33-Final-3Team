"""단신속보 원본 → 최신 분류 후보·LLM → MySQL → Chroma short_news.

어느 단계든 실패하면 성공으로 보고하지 않습니다. MySQL 커밋 후 Chroma 실패는
동일 기사 ID upsert와 내용 기반 분석 캐시로 다음 실행에서 복구합니다.
"""
import argparse
import hashlib
import json
import logging
import os
from pathlib import Path

from dotenv import dotenv_values
from filelock import FileLock
from sqlalchemy import bindparam, text

from backend.app.db.news_lock import database_identity
from backend.app.db.session import make_engine, mysql_url
from backend.app.services import shortnews_analysis as analysis, shortnews_keywords as keywords
from backend.app.services import shortnews_load as service
from backend.app.scheduler.jobs import shortnews_load as loader

ROOT = Path(__file__).resolve().parents[4]
log = logging.getLogger(__name__)


def sync_articles(engine, articles, originals, results, collection, encode, revision):
    external_ids = [a['external_id'] for a in articles]
    rows, db_keywords = {}, {}
    with engine.connect() as conn:
        for start in range(0, len(external_ids), 1000):
            params = {'s': service.SOURCE_CODE, 'ids': external_ids[start:start + 1000]}
            query = text('SELECT article_id,external_id,title,published_date,source_url,body_masked FROM article WHERE source_code=:s AND external_id IN :ids').bindparams(bindparam('ids', expanding=True))
            rows.update({r['external_id']: dict(r) for r in conn.execute(query, params).mappings()})
            query = text('SELECT a.external_id,t.keyword_text FROM article a JOIN article_tag t ON t.article_id=a.article_id WHERE a.source_code=:s AND a.external_id IN :ids AND t.keyword_text IS NOT NULL').bindparams(bindparam('ids', expanding=True))
            for ext, word in conn.execute(query, params):
                db_keywords.setdefault(ext, set()).add(word)
    for row in rows.values():
        row['published_date'] = service.published_date(str(row['published_date']))
    ids = [f"{service.SOURCE_CODE}:{a['external_id']}" for a in articles]
    saved = collection.get(ids=ids, include=['metadatas', 'documents']) if ids else {'ids': [], 'metadatas': [], 'documents': []}
    have = {key: (meta, doc) for key, meta, doc in zip(saved['ids'], saved['metadatas'], saved['documents'])}
    updated = 0
    expected = {}
    for article, key in zip(articles, ids):
        ext = article['external_id']
        row = rows.get(ext)
        if row is None or any(row[k] != article[k] for k in ('body_masked', 'title', 'published_date', 'source_url')):
            raise ValueError('MySQL 기사와 분석 입력 불일치')
        result, raw = results[ext], originals[ext]
        if db_keywords.get(ext, set()) != set(result['keywords']):
            raise ValueError('MySQL 키워드와 분석 결과 불일치')
        summary = ' '.join(result['summary_sentences'])
        meta = {'article_id': row['article_id'], 'external_id': ext, 'source_code': service.SOURCE_CODE,
                'title': row['title'], 'published_date': row['published_date'].isoformat(),
                'published_ymd': int(row['published_date'].strftime('%Y%m%d')),
                'source_url': row['source_url'] or '', 'nat': service.text(raw.get('nat')),
                'regn': service.text(raw.get('regn')), 'kbc': service.text(raw.get('kbc')),
                'summary': summary, 'keywords': ', '.join(result['keywords']),
                'classification_revision': revision}
        body = row['body_masked']
        fingerprint = hashlib.sha256(json.dumps({'metadata': meta, 'body': body}, sort_keys=True).encode()).hexdigest()
        meta['content_hash'] = fingerprint
        expected[key] = (meta, body)
        if have.get(key) == (meta, body):
            continue
        embedding_text = f"{row['title']}\n{summary}\n키워드: {', '.join(result['keywords'])}"
        collection.upsert(ids=[key], documents=[body], metadatas=[meta], embeddings=encode([embedding_text]))
        updated += 1
    if ids:
        got = collection.get(ids=ids, include=['metadatas', 'documents'])
        actual = {key: (meta, doc) for key, meta, doc in zip(got['ids'], got['metadatas'], got['documents'])}
        if actual != expected:
            raise ValueError('Chroma 저장 검증 실패')
    return {'articles': len(ids), 'updated': updated, 'existing': len(ids) - updated}


def run(raw_path, *, db_url=None, env_path=ROOT / '.env', chroma_path=ROOT / 'chroma_data',
        log_dir=ROOT / 'data/shortnews/pipeline_logs', period=None, api=None, client=None):
    import chromadb
    from openai import OpenAI
    directory = Path(log_dir)
    directory.mkdir(parents=True, exist_ok=True)
    report = {'status': 'RUNNING', 'stage': 'prepare', 'mysql': 'NOT_STARTED', 'chroma': 'NOT_STARTED'}
    engine = None
    owned_api = api is None
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
            api = api or OpenAI(api_key=values.get('OPENAI_API_KEY'), timeout=60)
            client = client or chromadb.PersistentClient(path=str(chroma_path))
            encode = lambda texts: analysis.embed(api, texts)
            report['stage'] = 'classification_index'
            snapshot = keywords.read_catalog(engine)
            index, report['keyword_index'] = keywords.sync_index(snapshot, client, encode)
            report['stage'] = 'analysis'
            results, cache_hits = {}, 0
            for article in articles:
                result, cached = analysis.analyze(article, snapshot, index, encode,
                    lambda row, choices: analysis.ask(api, row, choices), ROOT / 'data/shortnews/analysis_cache')
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
                       'country_all': '|'.join(service.country_codes(originals[a['external_id']].get('nat'), lookup)[0]),
                       'hs6': ''} for a in articles]
            clean_path = directory / 'shortnews_selected_keywords.json'
            analysis.atomic_json(clean_path, cleans)
            report['stage'] = 'mysql'
            if loader.run(raw_path, clean_path, db_url=engine.url, log_dir=directory / 'mysql', period=period):
                raise RuntimeError('MySQL 적재 실패')
            report['mysql'] = 'SUCCESS'
            report['stage'] = 'chroma'
            collection = client.get_or_create_collection('short_news', embedding_function=None,
                metadata={'hnsw:space': 'cosine', 'embedding_model': keywords.MODEL, 'database_id': identity})
            if (collection.metadata or {}).get('embedding_model') != keywords.MODEL or (collection.metadata or {}).get('database_id') != identity:
                raise ValueError('short_news 모델 또는 대상 DB 불일치')
            report['chroma_result'] = sync_articles(engine, articles, originals, results, collection, encode, snapshot.revision)
            report['chroma'] = 'SUCCESS'
            if keywords.read_catalog(engine).revision != snapshot.revision:
                raise ValueError('저장 중 DB 분류 변경. 재실행 필요')
            report.update(status='SUCCESS', stage='finished')
    except Exception as exc:
        report.update(status='FAILED', error_type=type(exc).__name__)
        log.error('단신속보 처리 실패: 단계=%s 오류=%s', report['stage'], type(exc).__name__)
    finally:
        if engine is not None: engine.dispose()
        if owned_api and api is not None: api.close()
        analysis.atomic_json(directory / 'pipeline_result.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--raw', type=Path, required=True)
    parser.add_argument('--env', type=Path, default=ROOT / '.env')
    parser.add_argument('--chroma-path', type=Path, default=ROOT / 'chroma_data')
    parser.add_argument('--log-dir', type=Path, default=ROOT / 'data/shortnews/pipeline_logs')
    args = parser.parse_args()
    report = run(args.raw, env_path=args.env, chroma_path=args.chroma_path, log_dir=args.log_dir)
    print(json.dumps(report, ensure_ascii=False))
    raise SystemExit(0 if report['status'] == 'SUCCESS' else 1)


if __name__ == '__main__': main()
