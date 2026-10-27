"""최신 DB 분류 전체를 단신속보 키워드 후보용 Chroma 인덱스로 준비합니다."""
import argparse
import json
import logging
import os
from pathlib import Path
from uuid import uuid4

from dotenv import dotenv_values

from backend.app.db.session import mysql_url, make_engine
from backend.app.services import shortnews_keywords as keywords, shortnews_analysis as analysis

ROOT = Path(__file__).resolve().parents[4]
log = logging.getLogger(__name__)


def run(*, env_path=ROOT / '.env', chroma_path=ROOT / 'chroma_data'):
    import chromadb
    from openai import OpenAI

    values = {**dotenv_values(env_path), **os.environ}
    engine = make_engine(mysql_url(env_path))
    client = None
    usage = {'tokens':0, 'model_calls':0, 'reported_responses':0, 'unreported_calls':0}
    report = {'run_id':uuid4().hex, 'status':'RUNNING', 'usage':usage}
    report_path = Path(chroma_path) / 'candidate_index_run.json'
    try:
        path = Path(chroma_path)
        path.mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(path=str(path))
        with keywords.index_lock(client):
            snapshot = keywords.read_catalog(engine)
            with OpenAI(api_key=values.get('OPENAI_API_KEY'), timeout=60, max_retries=0) as api:
                completed = 0

                def embed(words):
                    nonlocal completed
                    def counted(value):
                        usage['model_calls'] += 1
                        usage['tokens'] += value['tokens'] or 0
                        usage['reported_responses' if value['reported'] else 'unreported_calls'] += 1
                        analysis.atomic_json(report_path, report)
                    vectors = analysis.embed(api, words, usage_sink=counted)
                    completed += len(words)
                    log.info('이번 실행 키워드 임베딩 %d건 / 분류 전체 %d건', completed, len(snapshot.rows))
                    return vectors

                _, result = keywords.sync_index(snapshot, client, embed)
            if keywords.read_catalog(engine).revision != snapshot.revision:
                raise ValueError('색인 중 DB 분류가 변경되었습니다. 다시 실행해야 합니다')
            result['removed_keyword_indexes'] = keywords.cleanup_indexes(snapshot, client)
            report.update(result, status='SUCCESS')
            return report
    except Exception as exc:
        report.update(status='FAILED', error_type=type(exc).__name__)
        raise
    finally:
        try:
            try:
                if client is not None: client.close()
            finally:
                engine.dispose()
        except Exception as exc:
            report.update(status='FAILED', cleanup_error_type=type(exc).__name__)
            raise
        finally:
            analysis.atomic_json(report_path, report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env', type=Path, default=ROOT / '.env')
    parser.add_argument('--chroma-path', type=Path, default=ROOT / 'chroma_data')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    # HTTP 클라이언트의 예외 원문·URL 대신 오류 종류만 출력합니다.
    logging.getLogger('httpx').setLevel(logging.WARNING)
    try:
        result = run(env_path=args.env, chroma_path=args.chroma_path)
        print(json.dumps({'status': 'SUCCESS', **result}, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({'status': 'FAILED', 'error_type': type(exc).__name__}))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
