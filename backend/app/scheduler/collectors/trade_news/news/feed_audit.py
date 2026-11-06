"""추가 요청 없이 받은 최신 사이트맵과 날짜 판정 근거를 보존합니다."""
import hashlib
from collections import Counter
from datetime import datetime
from email.utils import parsedate_to_datetime
from uuid import uuid4

from news.save import KST
from news.run_lifecycle import atomic_json
from news.urls import FEEDS, article_url, site_of


def publication_day(value):
    """정상 날짜의 선택 기준은 유지하고 누락·변환 실패는 None으로 구분합니다."""
    if not value:
        return None
    try:
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=KST)
        return parsed.astimezone(KST).date()
    except (ValueError, TypeError, OverflowError):
        return None


def original_feed_url(request):
    """최초 목록 식별자는 주소 이동 뒤에도 보존합니다. 새 탐색 주소를 만들지는 않습니다."""
    redirects = request.meta.get('redirect_urls', [])
    return request.meta.get('feed_origin_url') or (redirects[0] if redirects else request.url)


def ensure_snapshot(spider, response, crawler=None, request=None):
    """응답당 한 번 저장합니다. 쿠키·인증 헤더와 기사 본문 응답은 저장하지 않습니다."""
    request = request or response.request
    if request is None:
        return None
    origin = original_feed_url(request)
    if origin not in FEEDS.values():
        return None
    try:
        if site_of(request.url) != site_of(origin):
            return None
    except ValueError:
        return None
    # 사이트맵이 기사 주소로 이동한 경우에는 기사 본문을 사이트맵으로 저장하지 않습니다.
    try:
        article_url(request.url)
    except ValueError:
        pass
    else:
        return None
    if '_feed_snapshot' in request.meta:
        return request.meta['_feed_snapshot']
    target = getattr(spider, 'daily_target', None)
    target = target.isoformat() if target else None
    record = {'site': request.meta['site'], 'target_date': target, 'feed_origin_url': origin,
              'requested_url': request.url, 'response_url': response.url,
              'received_at': datetime.now(KST).isoformat(), 'http_status': response.status,
              'body_bytes': len(response.body), 'body_sha256': hashlib.sha256(response.body).hexdigest(),
              'headers': {name: response.headers[name].decode('latin-1')
                          for name in ('Date', 'Last-Modified', 'Age', 'ETag', 'Cache-Control',
                                       'Content-Type', 'Content-Encoding') if name in response.headers}}
    try:
        directory = spider.store.root / 'feed_responses' / record['site'] / (target or 'manual')
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (uuid4().hex + '.xml')
        # Scrapy가 압축을 해제한 응답 본문입니다. 고유 파일명으로 과거 응답을 덮어쓰지 않습니다.
        path.write_bytes(response.body)
        record.update(snapshot_status='saved', body_file=str(path.relative_to(spider.store.root)),
                      metadata_file=str(path.with_suffix('.json').relative_to(spider.store.root)))
        atomic_json(path.with_suffix('.json'), record)
    except OSError as error:
        record.update(snapshot_status='failed', snapshot_error_type=type(error).__name__)
        crawler = crawler or getattr(spider, 'crawler', None)
        stats = getattr(crawler, 'stats', None)
        if stats is not None:
            stats.inc_value('feed_snapshot_errors')
    request.meta['_feed_snapshot'] = record
    spider.store.event('feed_response_snapshot', **record)
    return record


def record_dates(spider, response, values, *, is_index=False):
    """후보 판단 전 전체 항목을 집계합니다. 집계를 수집 조건으로 사용하지 않습니다."""
    target = getattr(spider, 'daily_target', None)
    counts, missing, invalid = Counter(), 0, 0
    for value in values:
        if not value:
            missing += 1
        elif (day := publication_day(value)) is None:
            invalid += 1
        else:
            counts[day.isoformat()] += 1
    target_text = target.isoformat() if target else None
    matched = counts.get(target_text, 0) if target else None
    record = {**(ensure_snapshot(spider, response) or {}), 'site': response.meta['site'],
              'url': original_feed_url(response.request), 'target_date': target_text, 'entries_total': len(values),
              'publication_date_counts': dict(sorted(counts.items())),
              'earliest_publication_date': min(counts) if counts else None,
              'latest_publication_date': max(counts) if counts else None,
              'target_date_matches': matched, 'outside_target_date': sum(counts.values()) - matched if target else None,
              'missing_publication_dates': missing, 'invalid_publication_dates': invalid,
              'is_sitemap_index': is_index}
    observations = list(spider.crawler.stats.get_value('feed_date_observations', []))
    observations.append(record)
    spider.crawler.stats.set_value('feed_date_observations', observations)
    spider.store.event('feed_date_summary', **record)
    # 원래 응답의 메타데이터에도 집계를 연결합니다. 저장 실패는 실행 오류로 기록합니다.
    if record.get('snapshot_status') == 'saved':
        try:
            atomic_json(spider.store.root / record['metadata_file'], record)
        except OSError as error:
            record.update(snapshot_status='failed', snapshot_error_type=type(error).__name__)
            spider.crawler.stats.inc_value('feed_snapshot_errors')
            spider.store.event('feed_snapshot_metadata_failed', site=record['site'],
                               error_type=type(error).__name__)
    return record


def discovery_note(observations, target):
    """처리 완료와 대상일 확보 여부를 구분하며 본문 주제 판단에 사용하지 않습니다."""
    usable = [r for r in observations if not r['is_sitemap_index']]
    if not target or not usable:
        return 'not_observed', None
    if any(r['target_date_matches'] for r in usable):
        return 'target_date_found', None
    if any(r['missing_publication_dates'] or r['invalid_publication_dates'] for r in usable):
        return 'target_date_unconfirmed', '사이트맵 날짜 누락·해석 실패가 있어 대상일 기사 확보 여부 확인 필요'
    return 'target_date_not_found', '최신 사이트맵에서 대상일 기사 미확보 — 해당 언론사에 무역 기사가 없다는 뜻이 아님'
