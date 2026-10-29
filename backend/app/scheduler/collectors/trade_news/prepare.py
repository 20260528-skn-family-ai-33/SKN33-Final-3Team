"""기존 수집 DB를 읽어 서비스용 준비 결과를 생성합니다. 네트워크 요청 없음."""
import argparse
import json
import sqlite3
from pathlib import Path
from news.preparation import prepare_article
from news.staging import PreparationStore
from news.dictionary import load_dictionary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', required=True, type=Path)
    parser.add_argument('--output-database', required=True, type=Path)
    parser.add_argument('--dictionary', type=Path, help='검증된 국가·HS 사전 JSON. 없으면 코드 추정 안 함')
    args = parser.parse_args()
    if args.database.resolve() == args.output_database.resolve():
        parser.error('수집 DB와 준비 DB는 구분해야 합니다. 수집 DB를 수정하지 않습니다.')
    dictionaries = load_dictionary(args.dictionary)
    c = sqlite3.connect(args.database.resolve().as_uri() + '?mode=ro', uri=True)
    try:
        # 보존된 각 원문을 정제·검수하여 준비합니다.
        rows = c.execute('SELECT payload FROM article_history ORDER BY site,article_id').fetchall()
    finally:
        c.close()
    args.output_database.parent.mkdir(parents=True, exist_ok=True)
    store = PreparationStore(args.output_database)
    total = eligible = 0
    try:
        for payload, in rows:
            article = json.loads(payload)
            result = prepare_article(article, dictionaries.get('countries', []), dictionaries.get('products', []),
                                     dictionaries.get('keywords', []))
            store.save(article, result)
            total += 1
            eligible += result['eligible']
    finally:
        store.close()
    print(json.dumps({'originals_processed': total, 'body_and_topic_eligible': eligible,
                      'note': '정제·검수 결과이며 프로젝트 MySQL 적재 완료 수치가 아님'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
