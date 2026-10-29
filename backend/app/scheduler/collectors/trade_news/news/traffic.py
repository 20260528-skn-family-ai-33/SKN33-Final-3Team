"""요청 제한 전용: 예산·간격·사이트 중단 상태를 검사하고 저장."""
import asyncio
import time
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit
from bs4 import BeautifulSoup
from scrapy.exceptions import IgnoreRequest
from news.save import KST
from news.urls import site_of, article_url


def next_day():
    now = datetime.now(KST)
    return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def retry_after(value):
    # Retry-After는 초 또는 HTTP 날짜 두 형태를 모두 지원함
    if not value:
        return None
    try:
        text = value.decode("ascii").strip()
        if text.isdigit():
            return time.time() + int(text)
        value = parsedate_to_datetime(text)
        return value.timestamp() if value.utcoffset() is not None else None
    except (ValueError, TypeError, OverflowError, UnicodeError):
        return None


def challenge_page(response):
    # 본문에서 단순히 CAPTCHA라는 단어를 언급했다고 차단으로 오인하지 않음
    if not hasattr(response, "text"):
        return False
    soup = BeautifulSoup(response.text, "html.parser")
    title = soup.title.get_text(" ", strip=True).lower() if soup.title else ""
    return any(x in title for x in ("just a moment", "access denied", "captcha",
                                    "접근이 차단", "접근 제한", "보안 확인"))


def request_kind(request):
    path = urlsplit(request.url).path
    if path == '/robots.txt':
        return 'robots'
    try:
        article_url(request.url)
        return 'body'
    except ValueError:
        if request.meta.get('request_role') == 'list' or path.startswith('/sitemap/') or path.startswith('/rss/') or path == '/all-news':
            return 'list'
        return 'unknown'


class TrafficMiddleware:
    def __init__(self, crawler):
        self.crawler = crawler
        self.probes = set()
        self.halted = set()  # 한 번 중단한 사이트는 같은 실행에서 다시 시작하지 않음

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler)

    def stop(self, spider, site, reason, until=0, manual=False):
        self.halted.add(site)
        spider.store.update_state(site, until=until, manual=int(manual), reason=reason)
        spider.store.event("site_stopped", site=site, reason=reason, until=until, manual=manual)
        spider.logger.warning("%s 수집 중단: %s", site, reason)

    def blocked(self, store, site):
        state = store.state(site)
        return site in self.halted or state["manual"] or state["until"] > time.time()

    def list_retry(self, store, request, site):
        return store.db.execute('SELECT * FROM list_retries WHERE site=? AND path=?',
                               (site,urlsplit(request.url).path)).fetchone()

    def list_delay(self, site):
        # 응답 완료 뒤 최소 60초. 현재 AutoThrottle 간격이 더 길면 그대로 지킵니다.
        engine = getattr(self.crawler, 'engine', None)
        slots = getattr(getattr(engine, 'downloader', None), 'slots', {})
        slot = slots.get(site)
        return max(60, self.crawler.settings.getfloat('DOWNLOAD_DELAY'), getattr(slot, 'delay', 0))

    def block_list(self, store, site, url, reason):
        with store.db:
            store.db.execute('INSERT OR REPLACE INTO list_blocks VALUES (?,?,?,?)',
                             (site,urlsplit(url).path,url,reason))
        store.event('list_path_stopped',site=site,url=url,path=urlsplit(url).path,
                    reason=reason,scope='list_path')

    def check_scope(self, request, spider, site):
        # 목록 차단은 쿼리를 제외한 경로 단위로 지속 보존합니다.
        path = urlsplit(request.url).path
        if spider.store.db.execute('SELECT 1 FROM list_blocks WHERE site=? AND path=?', (site,path)).fetchone():
            raise IgnoreRequest('목록 경로 중단: 자동 재시도하지 않습니다')
        retry = self.list_retry(spider.store, request, site)
        if retry and request_kind(request) == 'list':
            if retry['attempts'] >= 3:
                # 마지막 요청의 응답 전에 종료된 경우에도 네 번째 요청은 보내지 않습니다.
                self.block_list(spider.store,site,retry['url'],'일반 목록 403 시도 총 3회 소진')
                raise IgnoreRequest('목록 요청 총 3회 소진')
            if request.url != retry['url']:
                raise IgnoreRequest('동일 목록 URL 재시도 중: 다른 페이지는 대기열 보존')
        row = spider.store.db.execute('SELECT needs_probe FROM body_access WHERE site=?',(site,)).fetchone()
        if request_kind(request)=='body' and row and row[0]:
            if not request.meta.get('body_access_probe'):
                raise IgnoreRequest('본문 접근 미확인: 승인된 단건 확인 전 대기열 보존')
            if site in self.probes:
                raise IgnoreRequest('이번 실행의 본문 접근 확인 1회 사용 완료')

    async def process_request(self, request, spider):
        try:
            site = site_of(request.url)
        except ValueError as exc:
            raise IgnoreRequest(str(exc)) from exc
        if site not in spider.sites:
            raise IgnoreRequest("이번 실행 대상이 아닌 사이트")
        # 다른 언론사로 리다이렉트되면 원래 사이트의 요청으로 취급하지 않음
        if request.meta.get("site", site) != site:
            raise IgnoreRequest("다른 언론사로의 리다이렉트는 수집하지 않습니다.")
        request.meta["site"] = site
        request.meta["download_slot"] = site
        store = spider.store
        cap = self.crawler.settings.getint("MAX_ARTICLES_PER_SITE", 0)
        queued_url = request.meta.get("queue_url")
        if cap and queued_url:
            # 이전 기사의 검증·저장 완료를 기다려 5건 이후 본문 요청을 방지함
            while (site in spider.article_inflight
                   and spider.article_inflight[site] != queued_url):
                await asyncio.sleep(0.02)
        if cap and store.count_articles(site) >= cap:
            raise IgnoreRequest("사이트별 기사 저장 상한 도달")
        if self.blocked(store, site):
            raise IgnoreRequest("사이트 중단 상태: 대기열을 다음 실행까지 보존합니다.")
        self.check_scope(request, spider, site)
        limit = self.crawler.settings.getint("DAILY_REQUEST_LIMIT")
        if store.used(site) >= limit:
            self.halted.add(site)
            store.event("budget_exhausted", site=site, limit=limit)
            raise IgnoreRequest("일일 요청 상한 도달")
        # time.sleep은 Scrapy 이벤트 루프를 멈추므로 비동기 대기를 사용함
        retry = self.list_retry(store, request, site) if request_kind(request)=='list' else None
        wait = max(0, store.state(site)["next_at"] - time.time(),
                   (retry['next_at'] - time.time()) if retry else 0)
        if wait:
            await asyncio.sleep(wait)
        if self.blocked(store, site):
            raise IgnoreRequest("대기 중 사이트가 중단되었습니다.")
        self.check_scope(request, spider, site)
        delay = self.crawler.settings.getfloat("DOWNLOAD_DELAY")
        if retry:
            delay = max(delay, self.list_delay(site))
        if not store.reserve(site, limit, delay, list_retry_url=request.url if retry else None):
            self.halted.add(site)
            raise IgnoreRequest("일일 요청 상한 도달")
        if request.meta.get("body_access_probe"):
            self.probes.add(site)
        if cap and queued_url:
            spider.article_inflight[site] = queued_url
        request.meta["request_reserved_at"] = time.time()
        store.event("request", site=site, url=request.url, daily_count=store.used(site))

    def process_response(self, request, response, spider):
        site = request.meta.get("site")
        if not site:
            return response
        store = spider.store
        store.event("response", site=site, url=response.url, status=response.status,
                    latency=request.meta.get("download_latency"))
        is_robots = urlsplit(request.url).path == "/robots.txt"
        if response.status == 429:
            resume = retry_after(response.headers.get("Retry-After"))
            self.stop(spider, site, "HTTP 429", until=max(next_day(), resume or 0),
                      manual=resume is None)
            raise IgnoreRequest("요청 제한: 사이트 작업 종료")
        # 명시적 보안 화면은 응답 코드·요청 종류와 무관하게 사이트 전체 중단입니다.
        if challenge_page(response):
            self.stop(spider, site, "접근 차단 또는 보안 확인", manual=True)
            raise IgnoreRequest("접근 차단")
        if response.status == 403 and request_kind(request) == 'list':
            row = self.list_retry(store, request, site)
            attempts = row['attempts'] if row else 1  # 최초 403 응답도 총 시도에 포함
            next_at = max(time.time()+self.list_delay(site),store.state(site)['next_at'])
            with store.db:
                store.db.execute('INSERT OR REPLACE INTO list_retries VALUES (?,?,?,?,?)',
                    (site,urlsplit(request.url).path,request.url,attempts,next_at))
            if attempts >= 3:
                self.block_list(store,site,request.url,'HTTP 403: 최초 포함 총 3회 실패')
                raise IgnoreRequest('목록 경로 HTTP 403: 총 3회 실패')
            store.event('list_retry_scheduled',site=site,url=request.url,attempts=attempts,
                        next_at=next_at,max_attempts=3)
            # 일반 Scrapy 자동 재시도는 계속 끕니다. 일반 목록 403만 같은 URL로 재예약.
            # 실제 대기·예산·사이트 중단 검사는 process_request에서 다시 적용합니다.
            return request.replace(dont_filter=True)
        if request_kind(request)=='list' and response.status == 200:
            with store.db:
                store.db.execute('DELETE FROM list_retries WHERE site=? AND path=? AND url=?',
                    (site,urlsplit(request.url).path,request.url))
            # 일반 목록 오류는 본문 보류를 만들지 않습니다. 본문은 실제 응답으로 판단합니다.
        if response.status == 403:
            self.stop(spider, site, "접근 차단 또는 보안 확인", manual=True)
            raise IgnoreRequest("접근 차단")
        # Scrapy 기본 robots 처리는 오류 시 허용될 수 있어 더 보수적으로 중단함
        if is_robots and response.status != 200:
            self.stop(spider, site, "robots.txt 확인 실패", manual=True)
            raise IgnoreRequest("robots.txt를 확인하지 못했습니다.")
        if request.meta.get('body_access_probe') and response.status == 200:
            # 빈 응답·유료 일부 본문을 접근 정상으로 판단하지 않습니다.
            from news.parse import parse_article
            from news.validate import validate_article
            from news.clean import clean_body, quality
            try:
                article = validate_article(parse_article(response.text, response.url, []))
                usable = quality(article['title'], clean_body(article['body']))['usable_body']
            except (ValueError, TypeError, AttributeError):
                usable = False
            if usable:
                with store.db:
                    store.db.execute('INSERT OR REPLACE INTO body_access VALUES (?,0)',(site,))
                store.event('body_access_verified',site=site,url=request.url)
        if 500 <= response.status <= 599:
            self.record_failure(spider, site)
        else:
            store.update_state(site, failures=0)
        return response

    def record_failure(self, spider, site):
        count = spider.store.state(site)["failures"] + 1
        spider.store.update_state(site, failures=count)
        if count >= 3:
            self.stop(spider, site, "연속 서버·네트워크 오류 3회", until=next_day())

    def process_exception(self, request, exception, spider):
        if isinstance(exception, IgnoreRequest):
            return None
        site = request.meta.get("site")
        if site:
            spider.store.event("download_failed", site=site, url=request.url,
                               error=type(exception).__name__)
            if urlsplit(request.url).path == "/robots.txt":
                self.stop(spider, site, "robots.txt 네트워크 오류", manual=True)
            else:
                self.record_failure(spider, site)
        return None
