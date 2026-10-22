"""기사 원문에서 서비스용 데이터와 판정 근거를 만드는 공통 단계입니다."""
from datetime import datetime
from news.dates import publication_date
from news.validate import validate_article
from news.clean import clean_body, quality
from news.focus import assess_focus
from news.tagging import extract_tags
from news.dictionary import dictionary_fingerprint


def prepare_article(article, countries=(), products=()):
    validate_article(article)
    cleaned = clean_body(article['body'])
    body_quality = quality(article['title'], cleaned)
    focus = assess_focus(article, cleaned['body_masked'])
    tags = extract_tags(article['title'], cleaned['body_masked'], countries, products)
    tags['dictionary_sha256'] = dictionary_fingerprint({'countries':list(countries), 'products':list(products)})
    # 품질 문제와 주제 제외는 별도 이유로 기록합니다. 원문 삭제 사유로 사용하지 않습니다.
    eligible = body_quality['usable_body'] and focus['accepted']
    return {'source_code': article['source_code'], 'external_id': article['source_article_id'],
            'source_url': article['source_url'], 'title': article['title'],
            'published_date': publication_date(article['published_at']).isoformat(),
            'published_at': article['published_at'], 'collected_at': article['collected_at'],
            'body_masked': cleaned['body_masked'], 'category': None,
            'eligible': eligible, 'quality': body_quality, 'focus': focus,
            'cleaning': {k: v for k, v in cleaned.items() if k != 'body_masked'}, 'tagging': tags}
