"""사이트별 HTML 추출. 본문 문단과 줄바꿈은 유지하고 광고 요소만 제거."""
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup
from news.urls import article_url
from news.dates import publication_date
from news.relevance import matched_keywords

SELECTORS = {
    "fnnews": ("h1.article-view__title", ".article-view__body"),
    # 일반 기사와 article-body 클래스만 사용하는 기사 본문을 모두 찾습니다.
    "hankyung": ("h1.headline", "#articletxt, .article-body"),
    "mk": (".news_ttl_sec .news_ttl, .news_ttl_sec h2, h1.view_head_title, h1.top_title", ".news_cnt_detail_wrap, #article_body"),
}


class PaidArticleError(ValueError):
    """유료로 확인된 운영 대상 기사: 추출 실패가 아니라 수집 제외로 처리할 사유입니다."""


def article_json(soup):
    # JSON-LD에서 게시 날짜를 보완하되 수정 날짜를 게시 날짜로 바꾸지 않음
    def walk(node):
        if isinstance(node, list):
            for part in node:
                yield from walk(part)
        elif isinstance(node, dict):
            kind = node.get("@type", [])
            kind = [kind] if isinstance(kind, str) else kind
            if any(x in kind for x in ("NewsArticle", "Article", "ReportageNewsArticle")):
                yield node
            yield from walk(node.get("@graph", []))
    for tag in soup.select('script[type="application/ld+json"]'):
        try:
            yield from walk(json.loads(tag.get_text()))
        except (ValueError, TypeError):
            continue


def parse_article(html, url, keywords):
    canonical, site, article_id = article_url(url)
    soup = BeautifulSoup(html, "html.parser")
    metadata = next(article_json(soup), {})
    # 로그인·유료 장벽의 일부 본문을 전체 기사로 잘못 저장하지 않음
    if metadata.get("isAccessibleForFree") in (False, "False", "false"):
        error = PaidArticleError if site in ('hankyung', 'fnnews') else ValueError
        raise error("유료 또는 접근 제한 기사입니다.")
    title_selector, body_selector = SELECTORS[site]
    title = soup.select_one(title_selector)
    body = soup.select_one(body_selector)
    if body is None and not (site == "fnnews" and metadata.get("articleBody")):
        raise ValueError("본문 선택자를 찾지 못했습니다. HTML 구조 확인이 필요합니다.")
    if site == "fnnews" and metadata.get("articleBody"):
        body_text = metadata["articleBody"]
    else:
        for tag in body.select("script, style, iframe, noscript, figure, .ad-area-wrap, "
                               ".ad_wrap, .adbox, .advertisement, .news_detail_foot" + (", .mobile_hot" if site == "fnnews" else "")):
            tag.decompose()
        for br in body.select("br"):
            br.replace_with("\n")
        # 인라인 강조 태그 사이를 강제로 줄바꿈하지 않으며 문단 끝만 구분함
        for block in body.select("p, div, li"):
            block.append("\n")
        body_text = "\n".join(line.strip() for line in body.get_text().splitlines() if line.strip())
    heading = title.get_text(" ", strip=True) if title else ""
    heading = heading or metadata.get("headline", "")
    if not heading:
        og_title = soup.select_one('meta[property="og:title"]')
        heading = og_title.get("content", "") if og_title else ""
    date = soup.select_one('meta[property="article:published_time"]')
    published = date.get("content", "") if date else metadata.get("datePublished", "")
    return {
        "source_code": site, "source_article_id": article_id, "source_url": canonical,
        "title": heading, "body": body_text, "published_at": publication_date(published).isoformat(),
        "collected_at": datetime.now(ZoneInfo("Asia/Seoul")).isoformat(),
        "keywords": matched_keywords(heading + " " + body_text, keywords),
    }
