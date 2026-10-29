"""수집 범위와 URL 정규화: 허용한 언론사의 HTTPS 주소만 사용."""
import re
from urllib.parse import urlsplit, urlunsplit

HOSTS = {"www.hankyung.com": "hankyung", "www.fnnews.com": "fnnews", "fnnews.com": "fnnews"}
DAILY_SITES = ("hankyung", "fnnews")

PATTERNS = {
    "fnnews": r"/news/(\d+)$",
    "hankyung": r"/article/(\d{12}[0-9A-Za-z])$",
}
FEEDS = {
    "fnnews": "https://www.fnnews.com/sitemap/news/latest.xml",
    "hankyung": "https://www.hankyung.com/sitemap/latest-article.xml",
}


def site_of(url):
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.username or parsed.password
            or parsed.port not in (None, 443) or parsed.hostname not in HOSTS):
        raise ValueError("허용한 HTTPS 호스트가 아닙니다: " + url)
    return HOSTS[parsed.hostname]


def article_url(url):
    # 기사 주소의 추적 쿼리·프래그먼트·마지막 슬래시를 제거함
    site = site_of(url)
    parsed = urlsplit(url)
    path = parsed.path.rstrip("/")
    match = re.fullmatch(PATTERNS[site], path)
    if not match:
        raise ValueError("지원하지 않는 기사 주소입니다: " + url)
    return urlunsplit(("https", parsed.hostname, path, "", "")), site, match[1]
