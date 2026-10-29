"""PDF의 보수적인 초기 운영값. 사이트 허용량을 의미하지 않습니다."""
from pathlib import Path

BOT_NAME = "trade_news"
SPIDER_MODULES = ["news.spiders"]
NEWSPIDER_MODULE = "news.spiders"
USER_AGENT = "TradeNewsResearch/0.1"
ROBOTSTXT_OBEY = True
CONCURRENT_REQUESTS = 1              # 두 사이트를 합쳐 진행 중 요청 1개
CONCURRENT_REQUESTS_PER_DOMAIN = 1
# 일반 요청도 FIFO로 처리해 먼저 발견한 기사부터 수집함
SCHEDULER_MEMORY_QUEUE = "scrapy.squeues.FifoMemoryQueue"
SCHEDULER_DISK_QUEUE = "scrapy.squeues.PickleFifoDiskQueue"
DOWNLOAD_DELAY = 60                  # 요청 시작 사이 최소 간격
RANDOMIZE_DOWNLOAD_DELAY = False     # 무작위 간격으로 하한이 낮아지지 않게 함
AUTOTHROTTLE_ENABLED = True
AUTOTHROTTLE_START_DELAY = 60
AUTOTHROTTLE_MAX_DELAY = 300
AUTOTHROTTLE_TARGET_CONCURRENCY = 0.1
DOWNLOAD_TIMEOUT = 60
DOWNLOAD_MAXSIZE = 5 * 1024 * 1024
RETRY_ENABLED = False                # 즉시 자동 재시도하지 않음
COOKIES_ENABLED = False
TELNETCONSOLE_ENABLED = False
REDIRECT_MAX_TIMES = 3
LOG_LEVEL = "INFO"
LOGSTATS_INTERVAL = 60
TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"
DATA_DIR = str(Path(__file__).resolve().parents[1] / "data")
DAILY_REQUEST_LIMIT = 100            # 모든 진입점의 공통 기본값. robots·목록·본문 요청 포함
KEYWORDS = ["무역", "수출", "관세"]
TAG_DICTIONARY = None               # 프로젝트에서 검증한 국가·HS 사전 경로
ITEM_PIPELINES = {
    "news.validate.ValidatePipeline": 200,
    "news.service_pipeline.ServicePreparationPipeline": 250,
    "news.save.SavePipeline": 300,
}
DOWNLOADER_MIDDLEWARES = {
    # 응답은 큰 우선순위부터 처리: 압축 해제 후 보안 화면·403을 판정합니다.
    "scrapy.downloadermiddlewares.httpcompression.HttpCompressionMiddleware": 910,
    # robots 검사 이후 요청량을 예약하고, robots 요청 자체도 계산함
    "news.traffic.TrafficMiddleware": 900,
}
