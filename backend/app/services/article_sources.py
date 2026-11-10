"""기사 출처 코드. 일별 집계와 숨김·차단 차감이 같은 범위를 쓰도록 이 목록을 함께 씁니다."""

MARKET = "KOTRA_MARKET"
SHORT_NEWS = "KOTRA_SHORT_NEWS"
# 옛 단신 코드. 다음 단신 적재의 shortnews_load.migrate_source가 현재 코드로 바꾸기 전까지 DB에 남아 있을 수 있습니다.
LEGACY_SHORT_NEWS = "KOTRA_SHORTNEWS"

SHORT_NEWS_SOURCES = (SHORT_NEWS, LEGACY_SHORT_NEWS)
KEYWORD_DAILY_SOURCES = (MARKET, *SHORT_NEWS_SOURCES)  # news_keyword_daily에 들어가는 출처
PRODUCT_DAILY_SOURCES = (MARKET,)  # news_product_daily에 들어가는 출처
