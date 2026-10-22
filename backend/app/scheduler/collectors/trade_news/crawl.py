"""진입점. 한 번 수집하고 종료하며 수동 상태 조회·재개·재처리를 지원."""
import argparse
import json
import sys
import time
from datetime import date, datetime
from pathlib import Path
from filelock import FileLock, Timeout
from scrapy.crawler import CrawlerProcess
from scrapy.settings import Settings
from news import settings as defaults
from news.save import Store, KST
from news.spiders.articles import ArticleSpider
from news.urls import article_url, site_of
from news.run_summary import summarize_run
from news.dictionary import load_dictionary
from news.daily import yesterday


def main():
    parser = argparse.ArgumentParser(description="한국경제·매일경제 보수적 기사 수집")
    parser.add_argument("--sites", nargs="+", choices=["hankyung", "mk", "fnnews"], default=["hankyung"])
    parser.add_argument("--urls-file", type=Path, help="기사 URL을 한 줄에 하나씩 기록한 UTF-8 파일")
    parser.add_argument("--input-only", action="store_true", help="기존 대기열은 보존하고 이번 URL 파일만 처리")
    parser.add_argument("--discover", action="store_true", help="공식 사이트맵·RSS에서 신규 후보 발견")
    parser.add_argument("--feed-url", action="append", default=[], help="--discover에서 기본 목록 대신 사용할 같은 사이트의 RSS·사이트맵 URL")
    parser.add_argument("--since", type=date.fromisoformat, help="게시일 시작 YYYY-MM-DD")
    parser.add_argument("--until", type=date.fromisoformat, help="게시일 끝 YYYY-MM-DD")
    parser.add_argument("--daily-limit", type=int, default=defaults.DAILY_REQUEST_LIMIT, help=f"사이트별 일일 요청 상한: 기본 {defaults.DAILY_REQUEST_LIMIT}, 최대 100")
    parser.add_argument("--tag-dictionary", type=Path, help="검증된 프로젝트 국가·HS 사전 JSON")
    parser.add_argument("--max-articles-per-site", type=int, default=0, help="데이터 폴더 기준 사이트별 저장 상한. 0이면 제한 없음")
    parser.add_argument("--data-dir", type=Path, default=Path(defaults.DATA_DIR))
    parser.add_argument("--exclude-data-dir", type=Path, action="append", default=[], help="기존 기사 DB: 같은 원문 URL·식별자의 본문 재요청 방지용")
    parser.add_argument("--status", action="store_true", help="상태만 조회; 네트워크 요청 없음")
    parser.add_argument("--resume-site", choices=["hankyung", "mk", "fnnews"], help="원인 해결 후 수동 중단 해제")
    parser.add_argument("--requeue-url", help="파서 수정 후 실패 URL 한 건을 다시 대기열에 등록")
    parser.add_argument('--daily-target', type=date.fromisoformat, help='일일 처리 대상 날짜를 실행 전체에 고정')
    args = parser.parse_args()
    # 날짜 없는 탐색은 일일 실행으로 맞춰 과거 대기열까지 처리하지 않습니다.
    # 명시한 날짜·사이트·목록은 수동 작업 범위로 유지합니다.
    if args.discover and not args.daily_target and not args.since and not args.until:
        args.daily_target = yesterday()
    if args.daily_target:
        if args.since or args.until or not args.discover or args.max_articles_per_site:
            parser.error('일일 모드는 discover·무제한 건수로 사용하며 since/until과 병용하지 않습니다')
        args.since = args.until = args.daily_target
    for feed in args.feed_url:
        try:
            if site_of(feed) not in args.sites:
                parser.error("목록 URL의 사이트가 --sites 대상과 다릅니다.")
        except ValueError as exc:
            parser.error(str(exc))
    if args.feed_url and not args.discover:
        parser.error("--feed-url은 --discover와 함께 사용하세요.")
    if args.max_articles_per_site < 0:
        parser.error("기사 저장 상한은 0 이상이어야 합니다.")
    if not 1 <= args.daily_limit <= 100:
        parser.error("일일 요청 상한은 1~100입니다.")
    if args.since and args.until and args.since > args.until:
        parser.error("시작일이 종료일보다 늦습니다.")
    if args.input_only and (not args.urls_file or args.discover):
        parser.error("--input-only는 --urls-file과 함께 사용하며 --discover와 병용하지 않습니다.")
    admin = args.status or args.resume_site or args.requeue_url
    dictionary = None
    if not admin:
        try:
            dictionary = load_dictionary(args.tag_dictionary)
        except (OSError, ValueError) as exc:
            parser.error(f'태그 사전 검증 실패: {exc}')
    if not admin and not args.urls_file and not args.discover:
        parser.error("--urls-file 또는 --discover를 지정하세요. 대기열만 처리하려면 빈 URL 파일을 지정하세요.")
    urls = []
    if args.urls_file:
        for line in args.urls_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                try:
                    canonical, site, _ = article_url(line)
                except ValueError as exc:
                    parser.error(str(exc))
                if site in args.sites:
                    urls.append(canonical)
    # 날짜 없는 탐색은 위에서 전날로 고정합니다. 수동 URL 입력은 별도 경로입니다.
    since = args.since
    if args.discover and since is None:
        since = args.until or yesterday()
    args.data_dir.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(args.data_dir / "crawler.lock"))
    try:
        with lock.acquire(timeout=0):
            store = Store(args.data_dir)
            store.event_target = args.daily_target.isoformat() if args.daily_target else None
            stage='initialization'
            started_at=datetime.now(KST).isoformat()
            try:
                if args.status:
                    for site in args.sites:
                        print(json.dumps({"site": site, "used_today": store.used(site),
                              "pending": len(store.pending(site)), **store.state(site)}, ensure_ascii=False))
                    return
                if args.resume_site:
                    if store.state(args.resume_site)["until"] > time.time():
                        parser.error("저장된 재개 제한 시각이 지나지 않아 해제할 수 없습니다.")
                    store.update_state(args.resume_site, manual=0, until=0, reason="", failures=0)
                    store.event("manual_resume", site=args.resume_site)
                    return
                if args.requeue_url:
                    url, site, _ = article_url(args.requeue_url)
                    store.enqueue(url, site)
                    store.mark(url, "pending")
                    store.event("manual_requeue", url=url)
                    return
                for directory in args.exclude_data_dir:
                    store.import_reference(directory)
                settings = Settings()
                settings.setmodule(defaults)
                settings.set("MAX_ARTICLES_PER_SITE", args.max_articles_per_site, priority="cmdline")
                settings.set("DAILY_REQUEST_LIMIT", args.daily_limit, priority="cmdline")
                settings.set("LOG_FILE", str(args.data_dir / "scrapy.log"), priority="cmdline")
                settings.set("DATA_DIR", str(args.data_dir), priority="cmdline")
                if args.tag_dictionary:
                    settings.set("TAG_DICTIONARY", str(args.tag_dictionary.resolve()), priority="cmdline")
                settings.set('TAG_DICTIONARY_DATA', dictionary, priority='cmdline')
                # 다음 날짜 재실행 시 연속 오류 횟수는 초기화; 수동 차단은 그대로 유지
                for site in args.sites:
                    state = store.state(site)
                    if not state["manual"] and state["until"] <= time.time():
                        store.update_state(site, failures=0)
                from news.daily import initialize, history_urls, daily_summary
                initialize(store)
                stage='history_index'
                history_before = history_urls(store,args.daily_target.isoformat() if args.daily_target else None,args.sites)
                events_path = store.root / 'events.jsonl'
                event_offset = events_path.stat().st_size if events_path.exists() else 0
                process = CrawlerProcess(settings)
                before = {site: store.count_articles(site) for site in args.sites}
                crawler = process.create_crawler(ArticleSpider)
                process.crawl(crawler, store=store, sites=args.sites, urls=urls,
                              discover=args.discover, since=since, until=args.until, feed_urls=args.feed_url, input_only=args.input_only, daily_target=args.daily_target)
                stage='collection'
                process.start()
                stats = crawler.stats.get_stats()
                stage='run_stats'
                (args.data_dir / "last_run.json").write_text(
                    json.dumps(stats, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
                stage='json_export'
                store.export(full=not bool(args.daily_target))
                stage='run_summary'
                summary = summarize_run(store, args.sites, before, stats, args.daily_limit,
                                        args.max_articles_per_site, args.discover,
                                        target_date=args.daily_target.isoformat() if args.daily_target else None,
                                        list_urls=crawler.spider.feed_urls)
                from news.date_index import data_issues
                summary['data_issues']=data_issues(store.db)
                summary['pending_json_exports']=store.db.execute('SELECT COUNT(*) FROM export_pending').fetchone()[0]
                (args.data_dir / 'run_summary.json').write_text(
                    json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
                if args.daily_target:
                    stage='daily_report'
                    report = daily_summary(store, args.daily_target.isoformat(), args.sites, history_before, summary, event_offset)
                    report_dir = args.data_dir / 'daily_runs' / args.daily_target.isoformat()
                    report_dir.mkdir(parents=True, exist_ok=True)
                    encoded = json.dumps(report, ensure_ascii=False, indent=2)
                    (report_dir / 'latest.json').write_text(encoded, encoding='utf-8')
                    from news.unfinished import render_text
                    (report_dir / 'unfinished.json').write_text(
                        json.dumps(report['unfinished'],ensure_ascii=False,indent=2),encoding='utf-8')
                    (report_dir / 'unfinished.txt').write_text(render_text(report['unfinished']),encoding='utf-8')
                    (report_dir / (datetime.now(KST).strftime('%Y%m%dT%H%M%S%f') + '.json')).write_text(encoded, encoding='utf-8')
                print("수집 작업 종료. 결과·중단 사유:", args.data_dir)
                # 배치 호출자가 내부 저장·검증 오류를 성공 실행으로 오인하지 않게 합니다.
                if not summary["execution_healthy"]:
                    raise SystemExit(1)
            except Exception as exc:
                # 엔진 종료 뒤 저장/집계 실패도 이번 실행의 실패로 남깁니다.
                # 이전 성공 보고서를 최신 결과로 오인하지 않도록 별도 최소 보고서를 씁니다.
                from news.failure_report import write_failure_report
                write_failure_report(store, args, stage, exc, started_at)
                raise
            finally:
                # 정상 경로의 내보내기를 반복하지 않습니다. 저장된 export_pending은 다음 실행에서 복구합니다.
                store.close()
    except Timeout:
        raise SystemExit("같은 데이터 폴더를 사용하는 수집기가 이미 실행 중입니다.")


if __name__ == "__main__":
    main()
