"""기본은 적재 계획만 확인합니다. --apply를 지정해야 프로젝트 MySQL에 연결·적재합니다."""
import argparse
import json
import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from filelock import FileLock
from news.project_articles import prepared_articles
from news.mysql_store import MySQLArticleStore


# 기존 백엔드와 동일하게 저장소 루트 .env를 사용합니다. 실행 환경변수가 우선합니다.
ENV_PATH = Path(__file__).resolve().parents[5] / '.env'


def connection_options(env_path=ENV_PATH):
    # 실제 적재를 선택한 경우에만 .env 의존성과 접속 설정을 읽습니다.
    from dotenv import dotenv_values

    values = {**dotenv_values(env_path), **os.environ}
    names = {'host':'MYSQL_HOST','user':'MYSQL_USER','password':'MYSQL_PASSWORD','database':'MYSQL_DATABASE'}
    missing = [name for name in names.values() if not values.get(name)]
    if missing:
        raise ValueError('필수 환경변수 누락: '+', '.join(missing))
    options = {key:values[name] for key,name in names.items()}
    options['port'] = int(values.get('MYSQL_PORT') or 3306)
    if not 1 <= options['port'] <= 65535:
        raise ValueError('MYSQL_PORT 범위 오류')
    return {**options,'connection_timeout':10,'autocommit':False,'charset':'utf8mb4'}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True, help='검증된 articles.sqlite3와 prepared.sqlite3가 있는 폴더')
    parser.add_argument('--source-map', type=Path, required=True, help='크롤러 출처→프로젝트에 등록한 SOURCE 코드 JSON')
    parser.add_argument('--include-tags', action='store_true', help='선택: article_tag도 함께 적재. 기본은 article만 저장')
    parser.add_argument('--apply', action='store_true', help='명시한 프로젝트 DB에 실제 저장')
    args = parser.parse_args()
    mapping = json.loads(args.source_map.read_text(encoding='utf-8'))
    if not isinstance(mapping,dict) or not mapping or any(not isinstance(v,str) or not v.strip() or len(v)>40 for v in mapping.values()):
        parser.error('SOURCE 매핑은 비어 있지 않은 코드 문자열 객체여야 합니다')
    root = args.data_dir.resolve()
    source = sqlite3.connect((root/'articles.sqlite3').as_uri()+'?mode=ro',uri=True)
    source.row_factory = sqlite3.Row
    prepared = None
    connection = None
    try:
        prepared = sqlite3.connect((root/'prepared.sqlite3').as_uri()+'?mode=ro',uri=True)
        store = SimpleNamespace(db=source,root=root)
        with FileLock(str(root/'crawler.lock'),timeout=0):
            articles = prepared_articles(store,prepared)
        sites = {row['source_code'] for row in articles}
        if sites-mapping.keys():
            parser.error('SOURCE 매핑에 누락된 출처: '+', '.join(sorted(sites-mapping.keys())))
        if len(set(mapping.values())) != len(mapping):
            parser.error('서로 다른 언론사에 같은 SOURCE 코드를 지정할 수 없습니다')
        if not args.apply:
            print(json.dumps({'mode':'plan_only','mysql_connected':False,
                              'articles_to_store':len(articles),
                              'include_tags':args.include_tags,
                              'source_mapping':mapping,
                              'notice':'SOURCE 등록·사전 FK·서버 SQL 검증은 실제 연결 전까지 미확인'},ensure_ascii=False,indent=2))
            return
        options = connection_options()
        try:
            import mysql.connector
        except ImportError:
            parser.error('실제 적재에는 mysql-connector-python 설치가 필요합니다')
        # 자동 연결·예약은 없습니다. 이 분기는 --apply 명시 실행에만 도달합니다.
        connection = mysql.connector.connect(**options)
        loader=MySQLArticleStore(connection,mapping,include_tags=args.include_tags)
        with FileLock(str(root/'crawler.lock'),timeout=0):
            ids=[loader.save(article) for article in prepared_articles(store,prepared)]
        print(json.dumps({'mode':'applied','stored_articles':len(ids)},ensure_ascii=False,indent=2))
    finally:
        if connection is not None:
            connection.close()
        if prepared is not None:
            prepared.close()
        source.close()


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # 접속 드라이버의 오류 메시지에는 서버·계정 정보가 포함될 수 있어 출력하지 않습니다.
        raise SystemExit(f'적재 처리 중단 ({type(exc).__name__}). 원인 해결 후 재실행하세요. 원문은 유지되며 같은 식별자로 재실행할 수 있습니다.') from None
