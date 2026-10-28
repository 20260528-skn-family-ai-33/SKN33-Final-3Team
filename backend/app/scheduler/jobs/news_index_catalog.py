"""읽기 전용 DB 조회와 수집 CSV로 본문 색인 이관용 JSON 카탈로그를 만듭니다."""
import argparse
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[4]


def main(argv=None):
    from backend.app.db.session import make_engine, mysql_url, ENV_PATH
    from backend.app.schemas.rag import ArticleSource
    from backend.app.services.rag.catalog_export import export_catalog
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', action='append', choices=[source.value for source in ArticleSource],
                        help='조회할 출처; 반복 지정 가능, 기본 KOTRA_MARKET')
    parser.add_argument('--raw', type=Path, action='append', help='수집 CSV; 반복 지정 가능')
    parser.add_argument('--raw-dir', type=Path, default=ROOT / 'data/news',
                        help='--raw 생략 시 news_raw_*.csv 전체를 찾을 디렉터리')
    parser.add_argument('--dictionary-dir', type=Path, default=ROOT / 'data/news_keywords')
    parser.add_argument('--output', type=Path, default=ROOT / 'data/news/migrations/catalog.json')
    parser.add_argument('--env', type=Path, default=ENV_PATH, help='MYSQL_* 설정; 읽기 전용 계정 권장')
    args = parser.parse_args(argv)
    try:
        sources = args.source or ['KOTRA_MARKET']
        raw_paths = args.raw if args.raw is not None else (sorted(args.raw_dir.glob('news_raw_*.csv')) if 'KOTRA_MARKET' in sources else [])
        engine = make_engine(mysql_url(args.env))
        try:
            result = export_catalog(engine, args.output, raw_paths=raw_paths,
                                    sources=sources, dictionary_dir=args.dictionary_dir)
        finally:
            engine.dispose()
    except Exception as error:
        code = str(error) if isinstance(error, ValueError) and re.fullmatch(r'[a-z][a-z0-9_]+', str(error)) else 'catalog_export_failed'
        print(json.dumps({'status': 'failed', 'error_type': type(error).__name__, 'error_code': code}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
