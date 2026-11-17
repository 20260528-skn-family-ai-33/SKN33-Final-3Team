"""기사 출처 코드. 일별 집계와 숨김·차단 차감이 같은 범위를 쓰도록 이 목록을 함께 씁니다."""

MARKET = "KOTRA_MARKET"
SHORT_NEWS = "KOTRA_SHORT_NEWS"
# 옛 단신 코드. 다음 단신 적재의 shortnews_load.migrate_source가 현재 코드로 바꾸기 전까지 DB에 남아 있을 수 있습니다.
LEGACY_SHORT_NEWS = "KOTRA_SHORTNEWS"

SHORT_NEWS_SOURCES = (SHORT_NEWS, LEGACY_SHORT_NEWS)
KEYWORD_DAILY_SOURCES = (MARKET, *SHORT_NEWS_SOURCES)  # news_keyword_daily에 들어가는 출처
PRODUCT_DAILY_SOURCES = (MARKET,)  # news_product_daily에 들어가는 출처

# API가 내려주는 기사 표시명. 검색과 프론트 표시가 같은 이름을 사용한다.
ARTICLE_SOURCE_NAMES = {
    MARKET: 'KOTRA 해외시장뉴스',
    SHORT_NEWS: 'KOTRA 단신속보',
    LEGACY_SHORT_NEWS: 'KOTRA 단신속보',
    'HANKYUNG': '한국경제',
    'FNNEWS': '파이낸셜뉴스',
}

# 표시명과 별칭은 대소문자를 구분하지 않고 정확히 일치할 때만 출처로 검색한다.
ARTICLE_SOURCE_ALIASES = {'kotra': (MARKET, *SHORT_NEWS_SOURCES)}


def article_source_name(code):
    return ARTICLE_SOURCE_NAMES.get(code, code or '출처 미제공')


def matching_source_codes(query):
    name = query.strip().casefold()
    return tuple(code for code, label in ARTICLE_SOURCE_NAMES.items() if name == label.casefold()) + ARTICLE_SOURCE_ALIASES.get(name, ())
