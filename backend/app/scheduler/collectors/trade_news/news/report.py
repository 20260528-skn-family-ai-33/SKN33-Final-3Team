"""보고서 링크를 별도로 입력하지 않고 검증된 DB 원문 URL로 생성합니다."""
import json
from news.dates import publication_date
from pathlib import Path
from news.validate import validate_article


def validate_report_row(row):
    """운영 대상의 URL·출처·기사 ID와 저장 식별자가 일치하는지 확인합니다."""
    article = json.loads(row['payload'])
    validate_article(article)
    if (row['url'], row['site'], row['article_id']) != (
            article['source_url'], article['source_code'], article['source_article_id']):
        raise ValueError('DB 식별자와 기사 출처가 달라 보고서 생성을 중단합니다.')
    return article


def write_report(store):
    # 전체 보고서에서는 현재 목록과 보존 원문을 모두 검증합니다.
    for row in store.db.execute("SELECT * FROM article_history UNION ALL SELECT * FROM reference_articles"):
        validate_report_row(row)
    articles = []
    for row in store.db.execute("SELECT * FROM articles ORDER BY site, article_id"):
        article = validate_report_row(row)
        articles.append(article)
    result = {"articles": [{"site": a["source_code"], "title": a["title"],
               "url": a["source_url"], "published_at": a["published_at"]} for a in articles],
              "note": "검수를 통과한 각 원문을 독립적으로 저장한 목록입니다."}
    lines = ["DB 기반 저장 기사 목록", result["note"], ""]
    for i, article in enumerate(result["articles"], 1):
        lines += [f'{i}. [{article["site"]}] {article["title"]}',f'   원문: {article["url"]}']
    for name, content in [("inventory_report.json", json.dumps(result, ensure_ascii=False, indent=2)),
                          ("inventory_report.txt", "\n".join(lines) + "\n")]:
        path = Path(store.root) / name
        tmp = path.with_suffix(".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)
