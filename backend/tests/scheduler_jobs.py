"""스케줄러 테스트가 함께 쓰는 기대 작업 ID. 작업을 더하거나 빼면 여기만 고친다.

테스트마다 작업 수·목록을 따로 적으면 브랜치마다 작업을 더할 때 병합 충돌과 실패가 반복된다.
"""
# 단신속보(SHORTNEWS_DAILY_ENABLED)를 켜지 않았을 때 항상 등록되는 작업
BASE_JOBS = frozenset({
    "hs_code_refresh", "hs_code_refresh_retry", "hs_code_embed",
    "un_data_monthly", "un_data_startup", "un_data_resume",
    "customs_monthly", "customs_monthly_startup",
    "fx_daily_load", "fx_daily_startup",
    "external_refresh", "trade_news_daily",
})
# 단신속보를 켜면 더해지는 작업
SHORTNEWS_JOBS = frozenset({"shortnews_daily", "shortnews_startup"})
