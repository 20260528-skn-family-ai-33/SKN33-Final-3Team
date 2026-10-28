"""검증 전용: 필수 문자열, 시각, URL·출처·기사 ID 일치 여부를 검사."""
from datetime import datetime
from scrapy.exceptions import DropItem
from news.urls import article_url
from news.dates import publication_date


def validate_fields(article):
    if not isinstance(article, dict):
        raise ValueError("기사 데이터는 dict여야 합니다.")
    # 빈 본문, 누락된 제목 및 식별자를 정상 기사로 저장하지 않음
    for key in ("title", "body", "published_at", "collected_at", "source_code",
                "source_article_id", "source_url"):
        if not isinstance(article.get(key), str) or not article[key].strip():
            raise ValueError(f"{key}: 비어 있거나 문자열이 아닙니다.")
    # 게시일은 날짜로 검증합니다. 수집 시각은 예산·실행 로그 때문에 시간대를 유지합니다.
    publication_date(article["published_at"])
    for key in ("collected_at",):
        value = datetime.fromisoformat(article[key].replace("Z", "+00:00"))
        if value.utcoffset() is None:
            raise ValueError(f"{key}: 시간대 정보가 없습니다.")
    if not isinstance(article.get("keywords"), list):
        raise ValueError("keywords는 배열이어야 합니다.")
    return article


def validate_article(article):
    # 신규 수집은 설정한 언론사 URL 허용 범위를 엄격하게 검사합니다.
    validate_fields(article)
    canonical, site, article_id = article_url(article["source_url"])
    if (canonical != article["source_url"] or site != article["source_code"]
            or article_id != article["source_article_id"]):
        raise ValueError("원문 URL과 출처·기사 ID가 일치하지 않습니다.")
    return article


class ValidatePipeline:
    def process_item(self, item, spider):
        try:
            return validate_article(item)
        except (ValueError, TypeError) as exc:
            spider.store.mark(item.get("_queue_url", item.get("source_url", "")), "failed")
            spider.store.event("validation_failed", url=item.get("source_url"), error=str(exc))
            raise DropItem(str(exc)) from exc
