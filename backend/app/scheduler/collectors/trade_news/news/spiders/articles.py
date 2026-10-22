"""허용 언론사 수집기: URL 목록 또는 공식 사이트맵·RSS에서 후보를 수집."""
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urldefrag, urlsplit
import scrapy
from lxml import etree
from bs4 import BeautifulSoup
from scrapy.exceptions import IgnoreRequest
from scrapy.spidermiddlewares.httperror import HttpError
from news.parse import parse_article, PaidArticleError
from news.urls import FEEDS, DAILY_SITES, article_url, site_of
from news.save import KST


class ArticleSpider(scrapy.Spider):
    name = "articles"
    allowed_domains = ["www.hankyung.com", "www.fnnews.com", "fnnews.com"]

    def __init__(self, store, sites, urls=None, discover=False, since=None, until=None, feed_urls=None, input_only=False, daily_target=None, **kwargs):
        super().__init__(**kwargs)
        self.daily_target = daily_target
        self.store = store
        self.sites = sites
        self.urls = urls or []
        self.input_only = input_only
        self.discover = discover
        self.feed_urls = feed_urls or [FEEDS[site] for site in sites]
        # 기본 일일 실행은 사이트맵만 사용합니다. 명시한 시작점은 그대로 유지합니다.
        self.daily_sitemap = bool(daily_target and set(sites) & set(DAILY_SITES)
                                  and (not feed_urls or all(url in [FEEDS[s] for s in DAILY_SITES] for url in feed_urls)))
        self.since = since
        self.until = until
        self.scheduled = set()
        self.feed_seen = set()
        self.article_inflight = {}

    @classmethod
    def from_crawler(cls, crawler, *args, **kwargs):
        spider = super().from_crawler(crawler, *args, **kwargs)
        # 저장 또는 검증 실패가 확정되기 전에는 다음 본문 요청을 시작하지 않음
        crawler.signals.connect(spider.item_finished, signal=scrapy.signals.item_scraped)
        crawler.signals.connect(spider.item_finished, signal=scrapy.signals.item_dropped)
        crawler.signals.connect(spider.item_finished, signal=scrapy.signals.item_error)
        return spider

    def item_finished(self, item, response, **kwargs):
        failure = kwargs.get('failure')
        if failure is not None:
            # 오류 원인만 기록합니다. 기존 대기·재개 동작은 변경하지 않습니다.
            self.store.event('pipeline_failed',url=response.meta.get('queue_url',response.url),
                             error=failure.getErrorMessage())
        self.finish_article(response)

    def finish_article(self, request_or_response):
        site = request_or_response.meta.get("site")
        self.article_inflight.pop(site, None)

    def in_period(self, value, period=None):
        if not value:
            return True  # 목록에 날짜가 없으면 본문 추출 뒤 검사함
        try:
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                parsed = parsedate_to_datetime(value)  # RSS pubDate의 RFC 날짜 형식
            # 시간대 없는 국내 날짜는 실행 PC의 시간대와 무관하게 한국 시간으로 해석합니다.
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=KST)
            published_date = parsed.astimezone(KST).date()
        except (ValueError, TypeError):
            return True
        since, until = self.since, self.until
        if period is not None:
            since = date.fromisoformat(period['since_date']) if period['since_date'] else None
            until = date.fromisoformat(period['until_date']) if period['until_date'] else None
        return (not since or published_date >= since) and (not until or published_date <= until)

    def request_article(self, url):
        try:
            url, site, article_id = article_url(url)
        except ValueError:
            return None
        if site not in self.sites:
            return None
        if self.store.exists(url, site, article_id):
            self.store.mark(url, "saved")
            self.crawler.stats.inc_value("articles/skipped_existing")
            return None
        self.store.enqueue(url, site)
        # failed·filtered·not_found·excluded_paid·completed_no_body는 자동 재시도하지 않음. 수동 requeue로만 변경
        if url not in self.store.pending(site) or url in self.scheduled:
            return None
        period = self.store.preserve_queue_period(url, self.since, self.until)
        self.scheduled.add(url)
        return scrapy.Request(url, callback=self.parse_article_response, errback=self.on_error,
                              meta={"site": site, "queue_url": url, "queue_period": period})

    async def start(self):
        # 이전 실행에서 예산·차단으로 남겨둔 기사부터 이어서 진행함
        for site in ([] if self.input_only else self.sites):
            for url in self.store.pending(site):
                if self.daily_target:
                    period = self.store.db.execute('SELECT * FROM queue_period WHERE url=?', (url,)).fetchone()
                    if not period or period['since_date'] != self.daily_target.isoformat() or period['until_date'] != self.daily_target.isoformat():
                        continue  # 다른 날짜 대기열은 보존하고 해당 날짜 재개 때 처리합니다.
                request = self.request_article(url)
                if request:
                    yield request
        for url in self.urls:
            request = self.request_article(url)
            if request:
                yield request
        if self.discover:
            pending = []
            if self.daily_target:
                pending = [row['url'] for row in self.store.db.execute(
                    "SELECT * FROM discovery_queue WHERE target=? AND status='pending' ORDER BY rowid",
                    (self.daily_target.isoformat(),)) if row['site'] in self.sites]
            for feed_url in [*pending, *self.feed_urls]:
                request = self.request_feed(feed_url)
                if request:
                    yield request

    def request_feed(self, url):
        url = urldefrag(url)[0]
        try:
            site = site_of(url)
        except ValueError:
            return None
        if site not in self.sites or url in self.feed_seen:
            return None
        # 기본 일일 실행은 언론사별 최신 사이트맵 한 개만 요청합니다.
        # 과거 전체뉴스·날짜별 색인·하위 사이트맵 대기열도 재요청하지 않습니다.
        # 원래 URL·날짜·차단·예산 기록은 삭제하지 않고 목록 상태만 전환합니다.
        if self.daily_sitemap and site in DAILY_SITES and url != FEEDS[site]:
            with self.store.db:
                self.store.db.execute("UPDATE discovery_queue SET status='source_replaced' WHERE target=? AND url=? AND status='pending'",
                    (self.daily_target.isoformat(), url))
            return None
        self.feed_seen.add(url)
        if self.daily_target:
            # 전송 전 목록 URL을 보존해 예산·중단으로 못 읽은 다음 페이지도 재개합니다.
            with self.store.db:
                self.store.db.execute('INSERT OR REPLACE INTO discovery_queue VALUES (?,?,?,?)',
                    (self.daily_target.isoformat(),url,site,'pending'))
        return scrapy.Request(url, callback=self.parse_feed, errback=self.on_error,
                              meta={'site':site,'request_role':'list'})

    def finish_feed(self, response, status='processed'):
        if self.daily_target:
            with self.store.db:
                self.store.db.execute("UPDATE discovery_queue SET status=? WHERE target=? AND url=?",
                    (status,self.daily_target.isoformat(),response.request.url))

    def parse_feed(self, response):
        # 일일 모드에서는 공식 HTML 목록도 같은 병합 경로로 처리합니다.
        # 요청 URL의 날짜가 아니라 응답의 실제 기사 날짜를 사용합니다.
        soup = BeautifulSoup(response.text, 'html.parser')
        if self.daily_target and soup.select_one('.allnews-list'):
            from news.daily import record_candidate
            for item in soup.select('.allnews-list .news-item'):
                link, stamp = item.select_one('.news-tit a[href]'), item.select_one('.txt-date')
                if not link or not stamp:
                    continue
                published = stamp.get_text(strip=True).replace('.', '-')
                if not self.in_period(published):
                    continue
                url = urljoin(response.url, link['href'])
                try:
                    if site_of(url) != response.meta['site']:
                        continue
                    candidate = record_candidate(self.store, self.daily_target.isoformat(), url,
                        link.get_text(' ',strip=True), published, response.url)
                except ValueError:
                    continue
                if candidate:
                    request = self.request_article(url)
                    if request:
                        yield request
            # 실제 페이지에 있는 탐색 링크만 따릅니다. 번호를 추측해 URL을 만들지 않습니다.
            # 페이지 묶음의 '다음'이 11로 점프해도 2~10 링크를 함께 읽습니다.
            for link in soup.select('a[rel~=next][href], a.btn-next[href], .pagination a[href], .paging a[href], .pagination-wrap a[href]'):
                if link.get('aria-disabled') == 'true' or 'disabled' in link.get('class', []):
                    continue
                url = urljoin(response.url,link['href'])
                try:
                    if site_of(url) != response.meta['site'] or urlsplit(url).path != urlsplit(response.url).path:
                        continue
                except ValueError:
                    continue
                request = self.request_feed(url)
                if request:
                    yield request
            self.finish_feed(response)
            return
        # 200 안내 HTML이나 깨진 XML을 '기사 0건'으로 처리하지 않습니다.
        # 정상적으로 빈 urlset/RSS와 전날 기사가 없는 정상 목록은 그대로 허용합니다.
        try:
            root = etree.fromstring(response.body, parser=etree.XMLParser(
                recover=False, resolve_entities=False, no_network=True))
            if etree.QName(root).localname not in ('urlset', 'sitemapindex', 'rss', 'RDF'):
                raise ValueError('지원하는 사이트맵·RSS 구조가 아닙니다')
        except (etree.XMLSyntaxError, ValueError):
            site = response.meta['site']
            self.store.event('feed_structure_failed', site=site, url=response.request.url,
                target_date=self.daily_target.isoformat() if self.daily_target else None,
                error='사이트맵·RSS XML 형식 또는 루트 구조 오류')
            self.crawler.stats.inc_value('feed_structure_errors')
            self.crawler.stats.inc_value(f'discovery/{site}/feed_structure_errors')
            self.finish_feed(response, 'failed')
            return
        # XML 네임스페이스를 제거해 RSS와 뉴스 사이트맵을 공통 처리함
        selector = scrapy.Selector(response, type="xml")
        selector.remove_namespaces()
        site = response.meta["site"]
        prefix = f'discovery/{site}/'
        self.crawler.stats.inc_value(prefix + 'feed_responses')
        # 목록 개수 상한 없이 연결된 사이트맵을 탐색합니다. 전송 상한은 traffic.py가 관리합니다.
        for loc in selector.xpath("//sitemap/loc/text()").getall():
            url = urljoin(response.url,loc.strip())
            try:
                if site_of(url) != site:
                    continue
            except ValueError:
                continue
            request = self.request_feed(url)
            if request:
                yield request
        entries = selector.xpath("//item | //url")
        if not entries and not selector.xpath("//sitemap"):
            self.store.event("feed_empty_or_changed", site=site, url=response.url)
        for entry in entries:
            self.crawler.stats.inc_value(prefix + 'entries_seen')
            url = entry.xpath("./link/text() | ./loc/text()").get()
            if not url:
                self.crawler.stats.inc_value(prefix + 'missing_url')
                continue
            # 일반 모드는 본문 전체 판단, 일일 모드만 승인된 제목 후보 표시를 사용합니다.
            published = entry.xpath(".//publication_date/text() | ./pubDate/text()").get()
            if not self.in_period(published):
                self.crawler.stats.inc_value(prefix + 'date_filtered')
                continue
            if self.daily_target:
                from news.daily import record_candidate
                title = entry.xpath('.//title/text()').get()
                try:
                    candidate = record_candidate(self.store, self.daily_target.isoformat(),
                        urljoin(response.url,url.strip()), title, published, response.url)
                except ValueError:
                    continue
                if not candidate:
                    continue
            request = self.request_article(urljoin(response.url, url.strip()))
            if request:
                self.crawler.stats.inc_value(prefix + 'articles_scheduled')
                yield request

        self.finish_feed(response)

    def parse_article_response(self, response):
        original = response.meta["queue_url"]
        if response.status == 200 and not response.text.strip():
            # 수집 응답이 비어 있으면 사용자 기준에 따라 '본문 없음'으로 작업 완료.
            # 실제 사이트의 본문 존재 여부를 추정하거나 빈 기사를 적재하지 않습니다.
            from pathlib import Path
            _, site, article_id = article_url(original)
            diagnostics = Path(self.store.root) / 'diagnostics'
            diagnostics.mkdir(exist_ok=True)
            (diagnostics / f'{site}_{article_id}.html').write_bytes(response.body)
            self.store.mark(original,'completed_no_body')
            self.store.event('body_empty_completed',url=original,status=200,
                             response_bytes=len(response.body),reason='본문 없음 — 완료 (수집 응답 기준)')
            self.crawler.stats.inc_value('articles/completed_no_body')
            self.finish_article(response)
            return
        try:
            article = parse_article(response.text, response.url,
                                    self.crawler.settings.getlist("KEYWORDS"))
        except PaidArticleError as exc:
            # 유료 메타데이터로 확인된 기사는 실패·재시도 대상으로 남기지 않습니다.
            from pathlib import Path
            _, site, article_id = article_url(original)
            diagnostics = Path(self.store.root) / 'diagnostics'
            diagnostics.mkdir(exist_ok=True)
            (diagnostics / f'{site}_{article_id}.html').write_text(response.text,encoding='utf-8')
            self.store.mark(original,'excluded_paid')
            self.store.event('paid_excluded',url=original,reason=str(exc),
                             evidence='isAccessibleForFree=false')
            self.crawler.stats.inc_value('articles/paid_excluded')
            self.finish_article(response)
            return
        except (ValueError, TypeError) as exc:
            # 추출 실패 응답은 로컬 진단용으로 보존해 추가 요청 없이 구조를 확인합니다.
            from pathlib import Path
            _, failed_site, failed_id = article_url(original)
            diagnostics = Path(self.store.root) / "diagnostics"
            diagnostics.mkdir(exist_ok=True)
            (diagnostics / f"{failed_site}_{failed_id}.html").write_text(response.text, encoding="utf-8")
            self.store.mark(original, "failed")
            self.store.event("parse_failed", url=original, error=str(exc))
            self.finish_article(response)
            return
        # 키워드는 참고값입니다. 빈 배열이어도 본문 검수로 전달합니다.
        if not self.in_period(article["published_at"], response.meta.get('queue_period')):
            self.store.mark(original, "filtered")
            self.store.event("filtered", url=original)
            self.finish_article(response)
            return
        # 리다이렉트 전 URL도 저장 결과와 함께 갱신하도록 내부 필드로 전달함
        article["_queue_url"] = original
        yield article

    def on_error(self, failure):
        request = failure.request
        self.finish_article(request)
        url = request.meta.get("queue_url")
        # HttpError도 IgnoreRequest의 하위 클래스이므로 HTTP 오류를 먼저 구분함
        if failure.check(IgnoreRequest) and not failure.check(HttpError):
            # robots의 명시적 금지는 재시도하지 않음. 예산·중단은 pending 보존
            if url and "robots.txt" in failure.getErrorMessage() and "Forbidden" in failure.getErrorMessage():
                self.store.mark(url, "robots_denied")
            return
        status = failure.value.response.status if failure.check(HttpError) else None
        if url:
            self.store.mark(url, "not_found" if status == 404 else "failed")
        self.store.event("request_failed", url=request.url, status=status,
                         error=failure.getErrorMessage())
