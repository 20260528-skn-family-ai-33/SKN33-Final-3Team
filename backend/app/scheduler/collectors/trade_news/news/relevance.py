"""제목·도입부 참고 지표. 최종 판정은 focus.assess_focus에서 전체 본문으로 수행합니다."""
import re
from scrapy.exceptions import DropItem

TRADE = re.compile(r"무역|수출|수입(?!액?차)|관세(?!청)|통상|FTA|자유무역|수입규제|덤핑", re.I)
# 수출이 배경으로 등장해도 제목의 중심이 내수·주가·상품 홍보면 제외합니다.
SIDE_TOPIC = re.compile(r"소비|내수|가계|구매력|주가|목표가|실적 기대|추천주|증시|급등주|제품 소개|상품 소개|브랜드대상")
DIRECT_TRADE = re.compile(r"수출입|수출액|수입액|무역수지|무역협정|무역휴전|수출규제|수출통제|관세(?!청)|통상협상|FTA", re.I)


def matched_keywords(text, keywords):
    # 기관명 '관세청'만 등장한 기사를 관세 정책 기사로 분류하지 않습니다.
    cleaned = text.replace("관세청", "")
    return [word for word in keywords if word in cleaned]


def assess_relevance(article):
    title = article["title"]
    # 참고 지표만 계산합니다. 이 값의 실패만으로 최종 기사 제외를 결정하지 않습니다.
    lead = [part.strip() for part in re.split(r"(?<=[.!?。])\s+|\n", article["body"].strip()) if part.strip()][:2]
    lead = " ".join(lead)[:600]
    if SIDE_TOPIC.search(title) and not DIRECT_TRADE.search(title):
        return False, "제목의 중심이 소비·내수·증권·상품 등 주변 주제"
    if not TRADE.search(title):
        return False, "제목에 직접적인 무역 주제 근거 없음"
    if not TRADE.search(lead):
        return False, "기사 도입부에서 무역 주제를 확인할 수 없음"
    return True, "제목과 도입부에서 무역 주제 확인"


class RelevancePipeline:
    def process_item(self, item, spider):
        # 과거 파이프라인을 직접 쓰더라도 도입부 참고 지표로 탈락시키지 않습니다.
        from news.focus import assess_focus
        from news.clean import clean_body
        result = assess_focus(item, clean_body(item["body"])["body_masked"])
        accepted, reason = result["accepted"], result["reason"]
        if not accepted:
            spider.store.mark(item.get("_queue_url", item["source_url"]), "filtered")
            spider.store.event("topic_filtered", url=item["source_url"], reason=reason)
            spider.crawler.stats.inc_value("articles/topic_filtered")
            raise DropItem(reason)
        return item
