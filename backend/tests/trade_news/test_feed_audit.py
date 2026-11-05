"""사이트맵 보존·날짜 집계는 수집 판정을 바꾸지 않는지 합성 자료로 검증합니다."""
import asyncio
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from xml.sax.saxutils import escape

import pytest
from scrapy import Request
from scrapy.http import TextResponse, XmlResponse
from scrapy.downloadermiddlewares.redirect import RedirectMiddleware
from scrapy.exceptions import IgnoreRequest
from scrapy.settings import Settings
from scrapy.statscollectors import StatsCollector
from news import feed_audit, settings
from news.daily import daily_summary, initialize
from news.run_summary import summarize_run
from news.save import Store
from news.spiders.articles import ArticleSpider
from news.traffic import TrafficMiddleware
from .test_traffic import clock

FEED = 'https://www.fnnews.com/sitemap/news/latest.xml'
DAY = '2026-10-28'


def xml(dates, title='반도체 수출 계약 체결'):
    entries = []
    for n, value in enumerate(dates):
        stamp = '' if value is None else '<ns2:publication_date><![CDATA[' + value + ']]></ns2:publication_date>'
        entries.append(f'<url><loc>https://www.fnnews.com/news/20261028{n:010d}</loc>'
                       '<lastmod>2026-10-29T01:00:00Z</lastmod><ns2:news>' + stamp +
                       '<ns2:title>' + escape(title) + '</ns2:title></ns2:news></url>')
    return ('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
            'xmlns:ns2="http://www.google.com/schemas/sitemap-news/0.9">' + ''.join(entries) + '</urlset>').encode()


@pytest.fixture
def context(tmp_path):
    store = Store(tmp_path / 'data')
    initialize(store)
    config = Settings()
    config.setmodule(settings)
    crawler = SimpleNamespace(settings=config)
    crawler.stats = StatsCollector(crawler)
    day = date.fromisoformat(DAY)
    spider = ArticleSpider(store, ['fnnews'], discover=True, since=day, until=day, daily_target=day)
    spider.crawler = crawler
    spider.request_feed(FEED)  # 대기열 등록만 하며 네트워크 요청은 실행하지 않습니다.
    yield SimpleNamespace(store=store, spider=spider, crawler=crawler, root=store.root)
    store.close()


def response(body, *, status=200, request=None, attached=True, headers=None):
    request = request or Request(FEED, meta={'site': 'fnnews', 'request_role': 'list'})
    return XmlResponse(FEED, body=body, encoding='utf-8', status=status,
                       request=request if attached else None, headers=headers)


def reports(ctx):
    stats = {**ctx.crawler.stats.get_stats(), 'finish_reason': 'finished'}
    summary = summarize_run(ctx.store, ['fnnews'], {'fnnews': 0}, stats, 100,
                            discover=True, target_date=DAY, list_urls=[FEED])
    report = daily_summary(ctx.store, DAY, ['fnnews'], set(), summary)
    return summary, report


@pytest.mark.parametrize('value,expected', [
    (DAY, True), ('2026-10-27', False), ('2026-10-29', False),
    ('2026-10-28T00:00:00+09:00', True), ('2026-10-28T23:59:59+09:00', True),
    ('2026-10-27T15:00:00Z', True), ('2026-10-28T14:59:59Z', True),
    ('2026-10-28T15:00:00Z', False),
    ('Wed, 28 Oct 2026 14:30:00 GMT', True), ('Wed, 28 Oct 2026 15:30:00 GMT', False),
    (None, True), ('broken-date', True),
])
def test_date_selection_is_unchanged(context, value, expected):
    assert context.spider.in_period(value) is expected


def test_snapshot_and_full_date_distribution_before_title_selection(context):
    body = xml([DAY, '2026-10-29', '2026-10-27'])
    res = response(body, headers={'Date': 'Thu, 29 Oct 2026 02:00:00 GMT', 'Age': '120',
                                 'ETag': 'synthetic', 'Set-Cookie': 'session=synthetic',
                                 'Authorization': 'synthetic'})
    rows = list(context.spider.parse_feed(res))
    assert len(rows) == 1
    record = context.crawler.stats.get_value('feed_date_observations')[0]
    assert record['publication_date_counts'] == {'2026-10-27': 1, DAY: 1, '2026-10-29': 1}
    assert record['target_date_matches'] == 1 and record['outside_target_date'] == 2
    assert record['earliest_publication_date'] == '2026-10-27'
    assert record['latest_publication_date'] == '2026-10-29'
    assert (context.root / record['body_file']).read_bytes() == body
    assert record['body_sha256'] == hashlib.sha256(body).hexdigest()
    assert set(record['headers']) == {'Date', 'Age', 'ETag'}
    assert json.loads((context.root / record['metadata_file']).read_text()) == record
    assert record['received_at'].endswith('+09:00')
    summary, report = reports(context)
    assert summary['sites']['fnnews']['feed_date_status'] == 'target_date_found'
    assert not any('대상일 기사 미확보' in n for n in report['notes'])


def test_900_valid_outside_dates_are_reported_separately_from_completion(context):
    assert not list(context.spider.parse_feed(response(xml(['2026-10-29'] * 900))))
    summary, report = reports(context)
    site = summary['sites']['fnnews']
    assert site['status'] == 'available_candidates_processed'  # 완료 상태의 기존 의미 유지
    assert site['feed_date_status'] == 'target_date_not_found'
    assert site['feed_date_observations'][0]['entries_total'] == 900
    assert site['feed_date_observations'][0]['publication_date_counts'] == {'2026-10-29': 900}
    assert report['counts']['listed_urls'] == report['counts']['title_candidates'] == 0
    assert summary['execution_healthy']
    assert any('대상일 기사 미확보' in n and '무역 기사가 없다는 뜻이 아님' in n for n in report['notes'])
    assert not list(context.store.pending('fnnews'))


def test_mixed_900_entries_are_all_counted_even_with_weak_titles(context):
    list(context.spider.parse_feed(response(xml(['2026-10-29'] * 494 + [DAY] * 406, '동네 산책로 안내'))))
    record = context.crawler.stats.get_value('feed_date_observations')[0]
    assert record['entries_total'] == 900 and record['target_date_matches'] == 406
    assert context.store.db.execute('SELECT COUNT(*) FROM daily_inventory').fetchone()[0] == 406
    assert not context.store.pending('fnnews')


def test_missing_invalid_dates_keep_existing_body_check_path(context):
    requests = list(context.spider.parse_feed(response(xml([None, 'broken-date', '2026-10-29']))))
    assert len(requests) == 2  # 날짜 불명확 항목을 새 조건으로 제외하지 않습니다.
    summary, report = reports(context)
    site = summary['sites']['fnnews']
    assert site['feed_date_status'] == 'target_date_unconfirmed'
    record = site['feed_date_observations'][0]
    assert record['missing_publication_dates'] == record['invalid_publication_dates'] == 1
    assert any('날짜 누락·해석 실패' in n for n in report['notes'])


def test_empty_feed_and_index_are_not_confused(context):
    list(context.spider.parse_feed(response(xml([]))))
    assert reports(context)[0]['sites']['fnnews']['feed_date_status'] == 'target_date_not_found'
    context.crawler.stats.set_value('feed_date_observations', [])
    list(context.spider.parse_feed(response(b'<sitemapindex><sitemap><loc>https://www.fnnews.com/sitemap/old.xml</loc></sitemap></sitemapindex>')))
    assert reports(context)[0]['sites']['fnnews']['feed_date_status'] == 'not_observed'
    assert not context.spider.feed_seen - {FEED}  # 다른 목록을 새로 탐색하지 않습니다.


def test_malformed_feed_is_preserved_but_still_fails(context):
    body = b'<html><title>not XML</title></html>'
    assert not list(context.spider.parse_feed(response(body)))
    snapshot = next((context.root / 'feed_responses').rglob('*.xml'))
    assert snapshot.read_bytes() == body
    summary, report = reports(context)
    assert not summary['execution_healthy'] and report['counts']['list_pages_failed'] == 1


def test_downloader_response_without_request_and_callback_save_once(context):
    request = Request(FEED, meta={'site': 'fnnews', 'request_role': 'list'})
    res = response(xml([DAY]), request=request, attached=False)
    TrafficMiddleware(context.crawler).process_response(request, res, context.spider)
    assert request.meta['_feed_snapshot']['http_status'] == 200
    res.request = request
    list(context.spider.parse_feed(res))
    assert len(list((context.root / 'feed_responses').rglob('*.xml'))) == 1


def test_403_retries_keep_separate_responses_and_budget_rules(context):
    middleware = TrafficMiddleware(context.crawler)
    request = Request(FEED, meta={'site': 'fnnews', 'request_role': 'list'})
    asyncio.run(middleware.process_request(request, context.spider))
    retry = middleware.process_response(request, response(b'<error/>', status=403, request=request), context.spider)
    first = request.meta['_feed_snapshot']['body_file']
    # 시간을 가상으로 경과시킨 상태를 만들어 실제 대기·요청은 하지 않습니다.
    context.store.update_state('fnnews', next_at=0)
    with context.store.db:
        context.store.db.execute('UPDATE list_retries SET next_at=0')
    asyncio.run(middleware.process_request(retry, context.spider))
    middleware.process_response(retry, response(b'<error/>', status=403, request=retry), context.spider)
    assert retry.meta['_feed_snapshot']['body_file'] != first
    assert len(list((context.root / 'feed_responses').rglob('*.xml'))) == 2
    assert context.store.used('fnnews') == 2
    assert context.store.db.execute('SELECT attempts FROM list_retries').fetchone()[0] == 2


def test_snapshot_failure_is_error_without_changing_article_selection(context, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError('synthetic disk failure')
    monkeypatch.setattr(feed_audit, 'atomic_json', fail)
    assert len(list(context.spider.parse_feed(response(xml([DAY]))))) == 1
    summary, _ = reports(context)
    assert not summary['execution_healthy'] and summary['errors']['feed_snapshot_errors'] == 1


def test_date_metadata_write_failure_is_not_reported_as_success(context, monkeypatch):
    res = response(xml([DAY]))
    feed_audit.ensure_snapshot(context.spider, res)
    def fail(*args, **kwargs):
        raise OSError('synthetic metadata failure')
    monkeypatch.setattr(feed_audit, 'atomic_json', fail)
    assert len(list(context.spider.parse_feed(res))) == 1
    summary, _ = reports(context)
    assert not summary['execution_healthy']
    assert summary['sites']['fnnews']['feed_date_observations'][0]['snapshot_status'] == 'failed'


def test_no_article_body_response_is_archived(context):
    url = 'https://www.fnnews.com/news/202610280000000001'
    req = Request(url, meta={'site': 'fnnews'})
    res = TextResponse(url, body=b'actual article body', request=req)
    assert feed_audit.ensure_snapshot(context.spider, res) is None
    assert not (context.root / 'feed_responses').exists()


def test_multiple_runs_do_not_overwrite_previous_responses(context):
    for _ in range(2):
        list(context.spider.parse_feed(response(xml([DAY]))))
    files = list((context.root / 'feed_responses').rglob('*.xml'))
    assert len(files) == 2 and all(p.read_bytes() == xml([DAY]) for p in files)
    assert len(list((context.root / 'feed_responses').rglob('*.json'))) == 2


@pytest.mark.parametrize('in_target', [False, True])
def test_daily_entrypoint_publishes_snapshots_dates_and_missing_notice(tmp_path, in_target):
    root = Path(__file__).resolve().parents[3]
    collector = root / 'backend/app/scheduler/collectors/trade_news'
    tests = Path(__file__).parent
    body = xml([DAY if in_target else '2026-10-29'])
    article = 'https://www.fnnews.com/news/202610280000000000'
    spec = {
        'https://www.fnnews.com/robots.txt': {'body': 'User-agent: *\nAllow: /'},
        FEED: {'body': body.decode(), 'headers': {'Content-Type': 'application/xml', 'Age': '120'}},
        article: {'body': '<meta property="article:published_time" content="2026-10-28">'
                  '<h1 class="article-view__title">반도체 수출 계약 체결</h1>'
                  '<div class="article-view__body">한국 시험기업은 미국에 반도체 수출 100억원 계약을 체결했다.</div>'},
    }
    (tmp_path / 'responses.json').write_text(json.dumps(spec))
    env = {**os.environ, 'OFFLINE_DIR': str(tmp_path), 'OFFLINE_ENTRYPOINT': 'daily_collect',
           'OFFLINE_RUN_AT': '2026-10-29T03:00:00+09:00', 'OFFLINE_VIRTUAL_CLOCK': '1',
           'PYTHONDONTWRITEBYTECODE': '1',
           'PYTHONPATH': os.pathsep.join([str(root), str(tests), str(collector)])}
    result = subprocess.run([sys.executable, str(tests / 'offline_runner.py'),
                             '--data-dir', str(tmp_path / 'data'), '--sites', 'fnnews'],
                            cwd=collector, env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (tmp_path / 'network_attempts.log').exists()
    report = json.loads((tmp_path / 'data/daily_runs' / DAY / 'latest.json').read_text())
    assert report['execution_healthy'] and report['status'] == 'completed'
    site = report['operational']['sites']['fnnews']
    assert site['feed_date_status'] == ('target_date_found' if in_target else 'target_date_not_found')
    observation = site['feed_date_observations'][0]
    assert observation['target_date_matches'] == int(in_target)
    assert (tmp_path / 'data' / observation['body_file']).read_bytes() == body
    assert observation['headers']['Age'] == '120'
    assert report['counts']['eligible_stored'] == int(in_target)
    assert any('대상일 기사 미확보' in n for n in report['notes']) is (not in_target)
    downloads = [json.loads(line) for line in (tmp_path / 'downloads.jsonl').read_text().splitlines()]
    assert len(downloads) == 2 + int(in_target)  # robots·사이트맵·선택된 본문만 처리합니다.


@pytest.mark.parametrize('locations', [
    ['https://fnnews.com/sitemap/news/latest.xml'],
    ['https://www.fnnews.com/sitemap/news/current.xml'],
    ['https://fnnews.com/sitemap/news/latest.xml', 'https://www.fnnews.com/sitemap/news/current.xml'],
])
@pytest.mark.parametrize('explicit_origin', [True, False])
def test_same_site_redirect_preserves_final_xml_and_completes_original_queue(context, clock, locations, explicit_origin):
    # 실제 Scrapy 리다이렉트 미들웨어를 사용하되 내려받기는 실행하지 않습니다.
    middleware = TrafficMiddleware(context.crawler)
    redirects = RedirectMiddleware(context.crawler.settings)
    context.spider.feed_seen.clear()
    request = context.spider.request_feed(FEED)
    assert request.meta['feed_origin_url'] == FEED
    if not explicit_origin:
        request.meta.pop('feed_origin_url')  # 기존 redirect_urls 메타데이터도 지원합니다.
    for location in locations:
        asyncio.run(middleware.process_request(request, context.spider))
        res = XmlResponse(request.url, status=302, headers={'Location': location}, body=b'redirect', request=request)
        middleware.process_response(request, res, context.spider)
        request = redirects.process_response(request, res, context.spider)
    asyncio.run(middleware.process_request(request, context.spider))
    body = xml([DAY])
    res = XmlResponse(request.url, body=body, request=request)
    middleware.process_response(request, res, context.spider)
    assert len(list(context.spider.parse_feed(res))) == 1
    record = context.crawler.stats.get_value('feed_date_observations')[0]
    assert record['feed_origin_url'] == record['url'] == FEED
    assert record['response_url'] == locations[-1]
    assert record['snapshot_status'] == 'saved'
    assert (context.root / record['body_file']).read_bytes() == body
    assert len(list((context.root / 'feed_responses').rglob('*.xml'))) == len(locations) + 1
    assert context.store.db.execute('SELECT url,status FROM discovery_queue').fetchall()[0]['status'] == 'processed'
    assert reports(context)[1]['counts']['list_pages_pending'] == 0
    assert context.store.used('fnnews') == len(locations) + 1
    assert clock.waits and all(wait >= 60 for wait in clock.waits)


def test_redirect_does_not_expand_site_or_archive_article_body(context, clock):
    middleware = TrafficMiddleware(context.crawler)
    request = Request('https://www.hankyung.com/sitemap/latest-article.xml',
                      meta={'site': 'fnnews', 'request_role': 'list', 'feed_origin_url': FEED})
    with pytest.raises(IgnoreRequest):
        asyncio.run(middleware.process_request(request, context.spider))
    assert context.store.used('fnnews') == 0
    article = Request('https://www.fnnews.com/news/202610280000000001',
                      meta={'site': 'fnnews', 'request_role': 'list', 'feed_origin_url': FEED})
    res = TextResponse(article.url, body=b'article', request=article)
    assert feed_audit.ensure_snapshot(context.spider, res) is None
    assert not (context.root / 'feed_responses').exists()


def test_redirected_feed_still_has_three_403_attempts(context, clock):
    middleware = TrafficMiddleware(context.crawler)
    request = Request('https://fnnews.com/sitemap/news/latest.xml',
                      meta={'site': 'fnnews', 'request_role': 'list', 'feed_origin_url': FEED,
                            'redirect_urls': [FEED]})
    for attempt in range(1, 4):
        asyncio.run(middleware.process_request(request, context.spider))
        res = XmlResponse(request.url, body=b'<error/>', status=403, request=request)
        if attempt < 3:
            request = middleware.process_response(request, res, context.spider)
        else:
            with pytest.raises(IgnoreRequest):
                middleware.process_response(request, res, context.spider)
    assert context.store.used('fnnews') == 3
    with pytest.raises(IgnoreRequest):
        asyncio.run(middleware.process_request(request, context.spider))
    assert context.store.used('fnnews') == 3
    assert len(list((context.root / 'feed_responses').rglob('*.xml'))) == 3


@pytest.mark.parametrize('invalid', ['9999-12-31T23:59:59-12:00', '0001-01-01T00:00:00+14:00'])
@pytest.mark.parametrize('missing_url', [True, False])
def test_overflow_date_does_not_stop_later_valid_entries(context, invalid, missing_url):
    body = xml([invalid, DAY])
    if missing_url:
        import re
        body = re.sub(rb'<loc>.*?</loc>', b'', body, count=1)
    requests = list(context.spider.parse_feed(response(body)))
    assert len(requests) == (1 if missing_url else 2)
    record = context.crawler.stats.get_value('feed_date_observations')[0]
    assert record['invalid_publication_dates'] == 1 and record['target_date_matches'] == 1
    assert record['publication_date_counts'] == {DAY: 1}
    assert (context.root / record['body_file']).read_bytes() == body
    assert reports(context)[0]['execution_healthy']


@pytest.mark.parametrize('invalid', ['9999-12-31T23:59:59-12:00', '0001-01-01T00:00:00+14:00'])
def test_only_overflow_dates_are_unconfirmed_not_absent(context, invalid):
    assert len(list(context.spider.parse_feed(response(xml([invalid]))))) == 1
    summary, report = reports(context)
    assert summary['sites']['fnnews']['feed_date_status'] == 'target_date_unconfirmed'
    assert any('날짜 누락·해석 실패' in note for note in report['notes'])


@pytest.mark.parametrize('redirected', [True, False])
def test_daily_cli_saves_valid_article_with_invalid_date_entry_and_redirect(tmp_path, redirected):
    root = Path(__file__).resolve().parents[3]
    collector = root / 'backend/app/scheduler/collectors/trade_news'
    tests = Path(__file__).parent
    import re
    body = re.sub(rb'<loc>.*?</loc>', b'', xml(['9999-12-31T23:59:59-12:00', DAY]), count=1)
    final = 'https://fnnews.com/sitemap/news/latest.xml' if redirected else FEED
    article = 'https://www.fnnews.com/news/202610280000000001'
    spec = {
        host + '/robots.txt': {'body': 'User-agent: *\nAllow: /'}
        for host in ('https://www.fnnews.com', 'https://fnnews.com')
    }
    spec[FEED] = {'status': 302, 'headers': {'Location': final}, 'body': ''} if redirected else {'body': body.decode()}
    spec[final] = {'body': body.decode(), 'headers': {'Content-Type': 'application/xml'}}
    spec[article] = {'body': '<meta property="article:published_time" content="2026-10-28">'
                    '<h1 class="article-view__title">반도체 수출 계약 체결</h1>'
                    '<div class="article-view__body">한국 시험기업은 미국에 반도체 수출 100억원 계약을 체결했다.</div>'}
    (tmp_path / 'responses.json').write_text(json.dumps(spec))
    env = {**os.environ, 'OFFLINE_DIR': str(tmp_path), 'OFFLINE_ENTRYPOINT': 'daily_collect',
           'OFFLINE_RUN_AT': '2026-10-29T03:00:00+09:00', 'OFFLINE_VIRTUAL_CLOCK': '1',
           'PYTHONDONTWRITEBYTECODE': '1',
           'PYTHONPATH': os.pathsep.join([str(root), str(tests), str(collector)])}
    for attempt in range(2):
        result = subprocess.run([sys.executable, str(tests / 'offline_runner.py'),
                                 '--data-dir', str(tmp_path / 'data'), '--sites', 'fnnews'],
                                cwd=collector, env=env, capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr
        report = json.loads((tmp_path / 'data/daily_runs' / DAY / 'latest.json').read_text())
        assert report['execution_healthy'] and report['counts']['eligible_stored'] == 1
        assert report['counts']['list_pages_pending'] == 0
        record = report['operational']['sites']['fnnews']['feed_date_observations'][0]
        assert record['invalid_publication_dates'] == record['target_date_matches'] == 1
        assert record['feed_origin_url'] == FEED and record['response_url'] == final
        assert (tmp_path / 'data' / record['body_file']).read_bytes() == body
        assert not (tmp_path / 'network_attempts.log').exists()
    downloads = [json.loads(line) for line in (tmp_path / 'downloads.jsonl').read_text().splitlines()]
    assert sum(row['url'] == article for row in downloads) == 1
