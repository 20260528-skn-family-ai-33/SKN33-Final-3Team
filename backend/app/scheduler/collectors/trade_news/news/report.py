"""보고서 링크를 별도로 입력하지 않고 검증된 DB 원문 URL로 생성합니다."""
import json
import re
from news.dates import publication_date
from pathlib import Path
from news.validate import validate_article, validate_fields


def validate_report_row(row):
    """기존 네이버 저장 기록도 읽되 신규 수집 허용 범위는 변경하지 않습니다."""
    article = json.loads(row['payload'])
    validate_fields(article)
    if article['source_code'] == 'naver':
        # 저장 당시 사용한 네이버 정규 URL과 언론사번호_기사번호를 대조합니다.
        match = re.fullmatch(r'https://n\.news\.naver\.com/mnews/article/(\d{3})/(\d+)', article['source_url'])
        if not match or article['source_article_id'] != '_'.join(match.groups()):
            raise ValueError('저장된 네이버 URL과 기사 ID가 일치하지 않습니다.')
    else:
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
