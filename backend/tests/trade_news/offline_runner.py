"""실제 Scrapy 엔진·CLI를 실행하되 HTTP 핸들러는 가상 응답으로 대체."""
import json
import base64
import os
import socket
import time
from pathlib import Path

ROOT = Path(os.environ["OFFLINE_DIR"])
os.chdir(ROOT)  # Chroma 등 SDK의 기본 .env 탐색도 시험 디렉터리로 제한합니다.

# 별도 시험 자식도 업무용 data/news/load_locks를 사용하지 않습니다.
from backend.app.db import news_lock
news_lock.LOAD_LOCK_DIR = ROOT / 'article_load_locks'

def deny(*args, **kwargs):
    with (ROOT / "network_attempts.log").open("a") as f:
        f.write(repr(args) + "\n")
    raise AssertionError("외부 네트워크 연결 금지")

# Scrapy를 불러오기 전부터 DNS와 TCP 연결을 막음
socket.socket.connect = deny
socket.socket.connect_ex = deny
socket.getaddrinfo = deny

from scrapy.http import TextResponse
from twisted.internet.defer import succeed

class OfflineHandler:
    def __init__(self, settings, crawler=None):
        self.spec = json.loads((ROOT / "responses.json").read_text())
        self.counts = {}
        if (ROOT / 'downloads.jsonl').exists():
            for line in (ROOT / 'downloads.jsonl').read_text().splitlines():
                url = json.loads(line)['url']
                self.counts[url] = self.counts.get(url,0)+1

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler.settings, crawler)

    def download_request(self, request, spider):
        with (ROOT / "downloads.jsonl").open("a") as f:
            f.write(json.dumps({"url": request.url, "time": time.monotonic(), "reserved_at": request.meta.get("request_reserved_at")}) + "\n")
        if request.url not in self.spec:
            raise AssertionError("미등록 테스트 URL: " + request.url)
        spec = self.spec[request.url]
        if isinstance(spec,list):
            spec = spec[min(self.counts.get(request.url,0),len(spec)-1)]
        self.counts[request.url] = self.counts.get(request.url,0)+1
        request.meta["download_latency"] = 0.002
        return succeed(TextResponse(request.url, status=spec.get("status", 200),
            body=base64.b64decode(spec["body_base64"]) if "body_base64" in spec else spec.get("body", "").encode(), encoding="utf-8", request=request,
            headers=spec.get("headers", {"Content-Type": "text/html; charset=utf-8"})))

    def close(self):
        return succeed(None)

if __name__ == "__main__":
    # 전날 자동 계산도 고정 시각으로 재현합니다. 운영 코드는 변경하지 않습니다.
    if os.environ.get('OFFLINE_RUN_AT'):
        from datetime import datetime
        import news.daily as daily
        original_yesterday = daily.yesterday
        frozen_now = datetime.fromisoformat(os.environ['OFFLINE_RUN_AT'])
        daily.yesterday = lambda now=None: original_yesterday(now or frozen_now)
    # 예산의 한국 날짜만 가상화합니다. 실제 코드나 운영 예산은 변경하지 않습니다.
    if os.environ.get("OFFLINE_BUDGET_DAY"):
        from datetime import date
        import news.save as save
        day = date.fromisoformat(os.environ["OFFLINE_BUDGET_DAY"]).isoformat()
        save.today = lambda: day
    if os.environ.get('OFFLINE_VIRTUAL_CLOCK') == '1':
        import asyncio
        from types import SimpleNamespace
        import news.save as save
        import news.traffic as traffic
        clock = [time.time()]
        real_sleep = asyncio.sleep
        async def advance(seconds):
            clock[0] += seconds
            await real_sleep(0)
        save.time = traffic.time = SimpleNamespace(time=lambda: clock[0])
        traffic.asyncio = SimpleNamespace(sleep=advance)
    if os.environ.get('OFFLINE_BATCH_DB'):
        # 시험 전용 SQLite만 주입. 운영 명령이나 접속 설정은 변경하지 않습니다.
        from sqlalchemy import create_engine
        import news.batch_history as history
        history.make_history_engine = lambda: create_engine('sqlite:///' + os.environ['OFFLINE_BATCH_DB'])
        from backend.app.services import news_schema
        # 관리자 ERD 스냅샷도 시험 폴더에만 씁니다. 실제 정책·데이터 폴더는 사용하지 않습니다.
        news_schema.ROOT = ROOT / 'policy'
        news_schema.SNAPSHOT_DIR = ROOT / 'snapshots'
    if os.environ.get('OFFLINE_PAUSE_STAGE'):
        # 부모만 종료되는 시험: 자식은 crawler.lock을 가진 채 대기하고 해제 파일로 종료시킵니다.
        import news.run_lifecycle as lifecycle
        original_guard = lifecycle.require_current_job
        def paused_guard(root, stage):
            if stage == os.environ['OFFLINE_PAUSE_STAGE']:
                (ROOT / 'child_started.json').write_text(json.dumps({'pid': os.getpid(), 'stage': stage}))
                deadline = time.monotonic() + 25
                while not (ROOT / 'release_child').exists():
                    if time.monotonic() >= deadline:
                        raise RuntimeError('시험 자식 대기 시간 초과')
                    time.sleep(0.02)
            return original_guard(root, stage)
        lifecycle.require_current_job = paused_guard
    from news import settings
    settings.DOWNLOAD_HANDLERS = {
        "http": "offline_runner.OfflineHandler",
        "https": "offline_runner.OfflineHandler",
    }
    # 대기만 단축. 나머지 robots·상한·Pipeline·DB·CLI는 실제 코드 그대로 사용
    settings.DOWNLOAD_DELAY = 0.03
    settings.AUTOTHROTTLE_ENABLED = False
    # 실패 종료 코드 검증용 오류 주입. 테스트 실행기 안에서만 사용합니다.
    if os.environ.get('OFFLINE_FAIL_SAVE') == '1':
        from news.save import SavePipeline
        def fail_save(self, item, spider):
            raise OSError('로컬 저장 실패 시험')
        SavePipeline.process_item = fail_save
    # 강제 종료 시험은 별도 시험 프로세스만 종료합니다. 실제 DB·네트워크를 사용하지 않습니다.
    mode = os.environ.get('OFFLINE_FAULT_MODE')
    if mode in ('after_article_commit', 'pause_after_article_commit'):
        from news.mysql_store import MySQLArticleStore
        original_article_save = MySQLArticleStore.save
        def crash_after_article_commit(self, *args, **kwargs):
            original_article_save(self, *args, **kwargs)
            if mode == 'pause_after_article_commit':
                (ROOT / 'article_committed').touch()
                time.sleep(60)
            os._exit(77)
        MySQLArticleStore.save = crash_after_article_commit
    elif mode == 'after_db_commit':
        from news.save import Store
        original_save = Store.save_article
        def crash_after_commit(self, article):
            original_save(self, article)
            os._exit(73)
        Store.save_article = crash_after_commit
    elif mode == 'before_json_replace':
        original_replace = Path.replace
        def crash_before_replace(self, target):
            if self.parent.name == 'articles' and self.suffix == '.tmp':
                os._exit(74)
            return original_replace(self, target)
        Path.replace = crash_before_replace
    elif mode in ('report_cache_failure','after_state_commit'):
        import news.run_lifecycle as lifecycle
        original_publish = lifecycle.publish_run
        publishing = [False]
        def publishing_run(*args, **kwargs):
            publishing[0] = True
            try:return original_publish(*args, **kwargs)
            finally:publishing[0] = False
        lifecycle.publish_run = publishing_run
        if mode == 'report_cache_failure':
            original_json = lifecycle.atomic_json
            def fail_report_cache(path, value):
                if publishing[0] and path.name == 'last_success.json':
                    raise OSError('시험 표시용 보고서 출력 실패')
                return original_json(path, value)
            lifecycle.atomic_json = fail_report_cache
        else:
            original_commit = lifecycle._commit
            def crash_after_state(root, state):
                original_commit(root, state)
                if publishing[0]:os._exit(75)
            lifecycle._commit = crash_after_state
    import runpy
    import sys
    from types import SimpleNamespace
    entry = os.environ.get("OFFLINE_ENTRYPOINT", "crawl")
    if entry == "collect":
        import collect
        real_run = collect.subprocess.run
        def offline_child(args, cwd, check):
            assert Path(args[1]) == Path(collect.__file__).with_name("crawl.py")
            collect.subprocess.run = real_run  # Scrapy 내부의 로컬 시스템 정보 조회는 원래 함수 사용
            sys.argv = args[1:]
            try:
                runpy.run_path(args[1], run_name="__main__")
            except SystemExit as exc:
                return SimpleNamespace(returncode=exc.code or 0)
            return SimpleNamespace(returncode=0)
        # 실행 명령 생성은 실제 래퍼를 사용하고, 자식 실행만 가상 HTTP 환경에서 수행.
        collect.subprocess.run = offline_child
        runpy.run_path(str(Path(collect.__file__).with_name(entry + ".py")), run_name="__main__")
    elif entry == 'daily_collect':
        # 운영과 같이 수집기는 현재 시험 프로세스에서 직접 실행됩니다.
        import daily_collect
        runpy.run_path(daily_collect.__file__, run_name='__main__')
    elif entry == 'news_vector':
        from backend.app.scheduler.jobs import news_vector, news_embed, rag_index
        from backend.app.services.rag.vocabulary import Vocabulary
        from backend.app.services.news_keywords.keyword_clean import Categories
        news_embed.make_engine = lambda url=None: history.make_history_engine()
        rag_index.Vocabulary.load = lambda: Vocabulary(categories=Categories(rules=False))
        def fake_embed(texts):
            return [[1.0]+[0.0]*3071 for _ in texts]
        original_index = news_embed.run_sources
        def isolated(sources, **options):
            options['root'] = ROOT / 'vectors'
            options['db_url'] = 'isolated'
            options['embed'] = fake_embed
            if os.environ.get('OFFLINE_VECTOR_FAULT') == 'pause':
                (ROOT/'vector_started').touch()
                time.sleep(60)
            if os.environ.get('OFFLINE_VECTOR_FAULT') == 'exit124':
                # 느린 시작이 기존 2초를 넘는 상황에서도 자발 종료를 시간 초과와 구분합니다.
                time.sleep(3)
                (ROOT/'vector_exit124_ready').touch()
                raise SystemExit(124)
            return original_index(sources, **options)
        news_embed.run_sources = isolated
        raise SystemExit(news_vector.main())
    elif entry == 'load_project':
        # 실제 적재 CLI에도 시험용 SQLite만 주입합니다.
        import load_project
        if not os.environ.get('OFFLINE_BATCH_DB'):
            raise AssertionError('시험용 SQLite 경로 필요')
        load_project.make_project_engine = history.make_history_engine
        raise SystemExit(load_project.main())
    else:
        from crawl import main
        main()
