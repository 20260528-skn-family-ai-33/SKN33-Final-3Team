"""기본은 적재 계획. --apply에서만 MySQL 적재하며 일일 작업은 날짜·출처를 지정합니다."""
import argparse
import json
import sqlite3
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from filelock import FileLock
from news.project_articles import select_prepared_articles
from news.mysql_store import MySQLArticleStore

def make_project_engine():
    """단독 CLI에서도 팀 공통 SQLAlchemy+PyMySQL 연결 설정을 사용합니다."""
    import sys
    repository = Path(__file__).resolve().parents[5]
    if str(repository) not in sys.path:
        sys.path.insert(0, str(repository))
    from backend.app.db.session import make_engine
    return make_engine()


def atomic_result(path, result):
    if path is None:
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


def execute(args, engine_factory=None):
    """적재·동일 데이터·실패를 구분합니다. 일일 실행에서도 원문은 읽기 전용입니다."""
    result = {'mode': 'applied' if args.apply else 'plan_only', 'status': 'running',
              'target_date': args.target_date.isoformat() if args.target_date else None,
              'sites': args.sites, 'mysql_connected': False, 'include_tags': args.include_tags,
              'articles_to_store': 0, 'inserted': 0, 'existing': 0, 'failed': 0,
              'unprocessed': 0, 'rejected': [], 'unscoped_errors': [], 'article_results': []}
    source = prepared = connection = engine = None
    try:
        atomic_result(args.result_file, result)
        mapping = json.loads(args.source_map.read_text(encoding='utf-8'))
        if (not isinstance(mapping, dict) or not mapping
                or any(not isinstance(v, str) or not v.strip() or len(v)>40 for v in mapping.values())):
            raise ValueError('SOURCE 매핑 형식 오류')
        if len(set(mapping.values())) != len(mapping):
            raise ValueError('서로 다른 언론사에 같은 SOURCE 코드를 지정할 수 없습니다')
        root = args.data_dir.resolve()
        # 수집·수동 적재와 같은 잠금을 사용합니다. 조회부터 적재까지 스냅샷이 바뀌지 않습니다.
        with FileLock(str(root/'crawler.lock'), timeout=0):
            source = sqlite3.connect((root/'articles.sqlite3').as_uri()+'?mode=ro', uri=True)
            source.row_factory = sqlite3.Row
            prepared = sqlite3.connect((root/'prepared.sqlite3').as_uri()+'?mode=ro', uri=True)
            selection = select_prepared_articles(SimpleNamespace(db=source, root=root), prepared,
                                                 args.target_date, args.sites)
            articles = selection.pop('articles')
            result.update(selection)
            result['articles_to_store'] = len(articles)
            result['unprocessed'] = len(articles)
            if {row['source_code'] for row in articles} - mapping.keys():
                raise ValueError('SOURCE 매핑에 누락된 출처가 있습니다')
            result['source_mapping'] = mapping
            # 기존 전체 적재 명령은 오류가 있으면 중단합니다. 일일 범위에서는 정상 기사만 처리합니다.
            if args.target_date is None and (result['rejected'] or result['unscoped_errors']):
                raise ValueError('적재 대상 검증 실패')
            if not args.apply:
                result['status'] = 'plan_only'
                result['notice'] = '실제 SOURCE 등록·외래 키·서버 SQL은 연결 전까지 미확인'
            elif not articles:
                result['status'] = 'partial' if result['rejected'] or result['unscoped_errors'] else 'complete'
            else:
                engine = (engine_factory or make_project_engine)()
                connection = engine.connect()
                result['mysql_connected'] = True
                loader = MySQLArticleStore(connection, mapping, include_tags=args.include_tags)
                for article in articles:
                    identity = {k: article[k] for k in ('source_code','external_id','source_url')}
                    try:
                        outcome = loader.save(article, return_result=True)
                    except Exception as exc:
                        result['failed'] += 1
                        result['unprocessed'] -= 1
                        result['article_results'].append({**identity, 'status':'failed', 'error_type':type(exc).__name__})
                        # 충돌·연결 오류 뒤에 계속 쓰지 않습니다. 앞서 커밋한 행은 보존합니다.
                        raise
                    result[outcome['status']] += 1
                    result['unprocessed'] -= 1
                    result['article_results'].append({**identity, **outcome})
                    atomic_result(args.result_file, result)
                result['status'] = 'partial' if result['rejected'] or result['unscoped_errors'] else 'complete'
    except Exception as exc:
        result['status'] = 'failed'
        # 드라이버 오류 원문에는 접속 정보가 들어갈 수 있어 종류만 기록합니다.
        result['error_type'] = type(exc).__name__
    finally:
        for resource in (connection, prepared, source):
            if resource is not None:
                try:
                    resource.close()
                except Exception as exc:
                    result['status'] = 'failed'
                    result['close_error_type'] = type(exc).__name__
        if engine is not None:
            try:
                engine.dispose()
            except Exception as exc:
                result['status'] = 'failed'
                result['close_error_type'] = type(exc).__name__
        result['stored_articles'] = result['inserted'] + result['existing']
        atomic_result(args.result_file, result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--source-map', type=Path, required=True)
    parser.add_argument('--target-date', type=date.fromisoformat, help='일일 적재 대상 게시 날짜')
    parser.add_argument('--sites', nargs='+', help='적재할 크롤러 출처 코드')
    parser.add_argument('--result-file', type=Path, help='이번 적재 결과 JSON')
    parser.add_argument('--include-tags', action='store_true')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    result = execute(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['status'] in ('plan_only','complete') else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        raise SystemExit(f'적재 처리 중단 ({type(exc).__name__}). 원문은 유지됩니다.') from None
