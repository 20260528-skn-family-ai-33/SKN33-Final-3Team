"""최신 DB 분류 전체를 단신속보 키워드 후보용 Chroma 인덱스로 준비합니다."""
import argparse
import json
import logging
import os
from pathlib import Path

from dotenv import dotenv_values
from filelock import FileLock

from backend.app.db.session import mysql_url, make_engine
from backend.app.services import shortnews_keywords as keywords

ROOT = Path(__file__).resolve().parents[4]
log = logging.getLogger(__name__)


def run(*, env_path=ROOT / '.env', chroma_path=ROOT / 'chroma_data'):
    import chromadb
    from openai import OpenAI

    values = {**dotenv_values(env_path), **os.environ}
    engine = make_engine(mysql_url(env_path))
    path = Path(chroma_path)
    path.mkdir(parents=True, exist_ok=True)
    try:
        with FileLock(str(path / 'short_news_keywords.lock'), timeout=0):
            snapshot = keywords.read_catalog(engine)
            client = chromadb.PersistentClient(path=str(path))
            with OpenAI(api_key=values.get('OPENAI_API_KEY'), timeout=60) as api:
                completed = 0

                def embed(words):
                    nonlocal completed
                    result = api.embeddings.create(model=keywords.MODEL, input=words,
                                                   dimensions=keywords.DIMENSIONS)
                    ordered = sorted(result.data, key=lambda item: item.index)
                    if [item.index for item in ordered] != list(range(len(words))):
                        raise ValueError('임베딩 응답 순번 불일치')
                    completed += len(words)
                    log.info('이번 실행 키워드 임베딩 %d건 / 분류 전체 %d건', completed, len(snapshot.rows))
                    return [item.embedding for item in ordered]

                _, result = keywords.sync_index(snapshot, client, embed)
            if keywords.read_catalog(engine).revision != snapshot.revision:
                raise ValueError('색인 중 DB 분류가 변경되었습니다. 다시 실행해야 합니다')
            return result
    finally:
        engine.dispose()


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
