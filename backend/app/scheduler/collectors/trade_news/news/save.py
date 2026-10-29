"""저장 전용: SQLite를 원본 저장소로 쓰고 UTF-8 JSON으로 내보냅니다."""
import json
from news.dates import publication_date
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from news import date_index

KST = ZoneInfo("Asia/Seoul")


def today():
    return datetime.now(KST).date().isoformat()


class Store:
    def __init__(self, directory):
        self.root = Path(directory)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / "articles.sqlite3")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS articles (
          url TEXT PRIMARY KEY, site TEXT NOT NULL, article_id TEXT NOT NULL,
          payload TEXT NOT NULL, UNIQUE(site, article_id));
        CREATE TABLE IF NOT EXISTS article_history (
          url TEXT PRIMARY KEY, site TEXT NOT NULL, article_id TEXT NOT NULL,
          payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS reference_articles (
          url TEXT PRIMARY KEY, site TEXT NOT NULL, article_id TEXT NOT NULL,
          payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS queue (
          url TEXT PRIMARY KEY, site TEXT NOT NULL, status TEXT NOT NULL,
          added REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS queue_period (
          url TEXT PRIMARY KEY REFERENCES queue(url), since_date TEXT, until_date TEXT);
        CREATE TABLE IF NOT EXISTS budget (
          day TEXT NOT NULL, site TEXT NOT NULL, count INTEGER NOT NULL,
          PRIMARY KEY(day, site));
        CREATE TABLE IF NOT EXISTS discovery_queue (
          target TEXT NOT NULL, url TEXT NOT NULL, site TEXT NOT NULL,
          status TEXT NOT NULL, PRIMARY KEY(target,url));
        CREATE TABLE IF NOT EXISTS list_blocks (
          site TEXT NOT NULL, path TEXT NOT NULL, url TEXT NOT NULL, reason TEXT NOT NULL,
          PRIMARY KEY(site,path));
        CREATE TABLE IF NOT EXISTS list_retries (
          site TEXT NOT NULL, path TEXT NOT NULL, url TEXT NOT NULL,
          attempts INTEGER NOT NULL, next_at REAL NOT NULL,
          PRIMARY KEY(site,path));
        CREATE TABLE IF NOT EXISTS body_access (
          site TEXT PRIMARY KEY, needs_probe INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS state (
          site TEXT PRIMARY KEY, until REAL NOT NULL DEFAULT 0,
          manual INTEGER NOT NULL DEFAULT 0, reason TEXT NOT NULL DEFAULT '',
          next_at REAL NOT NULL DEFAULT 0, failures INTEGER NOT NULL DEFAULT 0);
        """)
        self.db.execute("INSERT OR IGNORE INTO article_history SELECT * FROM articles")
        self.db.commit()
        date_index.initialize(self.db)
        self.release_legacy_list_body_holds()

    def release_legacy_list_body_holds(self):
        """구버전의 일반 목록 403 보류만 해제합니다. 사이트 차단·예산은 보존합니다."""
        # 구버전에서 needs_probe=1을 설정한 곳은 일반 목록 403 처리였습니다.
        # 대응하는 목록 기록이 없는 보류는 출처를 확정하지 않고 그대로 둡니다.
        rows = self.db.execute("""
            SELECT b.site FROM body_access b LEFT JOIN state s ON s.site=b.site
            WHERE b.needs_probe=1 AND COALESCE(s.manual,0)=0 AND COALESCE(s.until,0)<=?
            AND (EXISTS (SELECT 1 FROM list_retries r WHERE r.site=b.site AND r.attempts BETWEEN 1 AND 3)
              OR EXISTS (SELECT 1 FROM list_blocks l WHERE l.site=b.site AND l.reason IN
                ('HTTP 403','HTTP 403: 최초 포함 총 3회 실패','일반 목록 403 시도 총 3회 소진')))
        """, (time.time(),)).fetchall()
        for row in rows:
            with self.db:
                self.db.execute('UPDATE body_access SET needs_probe=0 WHERE site=?',(row['site'],))
            self.event('legacy_list_body_hold_released',site=row['site'],
                       reason='일반 목록 403 보류와 실제 본문/사이트 중단 분리')

    def event(self, kind, **details):
        # 본문이나 인증정보는 로그에 기록하지 않음
        record = {"time": datetime.now(KST).isoformat(), "event": kind, **details}
        # 날짜별 원인 집계용 메타데이터. 수집 범위나 대기열 상태를 바꾸지 않습니다.
        if getattr(self, 'event_target', None):
            record.setdefault('target_date', self.event_target)
        with (self.root / "events.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        history = getattr(self, 'batch_history', None)
        if history is not None:
            history.event(record)

    def count_articles(self, site):
        return self.db.execute("SELECT COUNT(*) FROM articles WHERE site=?", (site,)).fetchone()[0]

    def exists(self, url, site=None, article_id=None):
        return self.db.execute(
            "SELECT 1 FROM articles WHERE url=? OR (site=? AND article_id=?) "
            "UNION ALL SELECT 1 FROM reference_articles WHERE url=? OR (site=? AND article_id=?) "
            "UNION ALL SELECT 1 FROM article_history WHERE url=? OR (site=? AND article_id=?)",
            (url, site, article_id, url, site, article_id, url, site, article_id)).fetchone() is not None

    def import_reference(self, directory):
        # 기존 원문의 URL·식별자는 재수집 방지용으로 보존하고 신규 저장 수에 포함하지 않습니다.
        from news.validate import validate_article
        source = Path(directory).resolve() / "articles.sqlite3"
        if source == (self.root / "articles.sqlite3").resolve():
            raise ValueError("기존 데이터와 신규 저장 폴더는 달라야 합니다.")
        previous = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
        try:
            has_history = previous.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='article_history'").fetchone()
            table = "article_history" if has_history else "articles"
            rows = previous.execute("SELECT url,site,article_id,payload FROM " + table).fetchall()
            for url, site, article_id, payload in rows:
                item = validate_article(json.loads(payload))
                if (url, site, article_id) != (item["source_url"], item["source_code"], item["source_article_id"]):
                    raise ValueError("기존 DB의 식별자 불일치")
            with self.db:
                self.db.executemany("INSERT OR IGNORE INTO reference_articles VALUES (?,?,?,?)", rows)
            self.event("reference_imported", directory=str(Path(directory).resolve()), count=len(rows))
        finally:
            previous.close()

    def enqueue(self, url, site):
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO queue VALUES (?, ?, 'pending', ?)",
                            (url, site, time.time()))

    def pending(self, site):
        return [r[0] for r in self.db.execute(
            "SELECT url FROM queue WHERE site=? AND status='pending' ORDER BY added",
            (site,))]

    def preserve_queue_period(self, url, since, until):
        # 최초 요청 범위를 보존합니다. 일일 자동 범위가 이동해도 대기 기사를 버리지 않습니다.
        # 기존 DB에 범위가 없는 대기 항목은 처음 재개한 실행의 범위를 사용합니다.
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO queue_period VALUES(?,?,?)',
                            (url, since.isoformat() if since else None, until.isoformat() if until else None))
        row = self.db.execute('SELECT since_date,until_date FROM queue_period WHERE url=?', (url,)).fetchone()
        return dict(row)

    def mark(self, url, status):
        with self.db:
            self.db.execute("UPDATE queue SET status=? WHERE url=?", (status, url))

    def state(self, site):
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO state(site) VALUES (?)", (site,))
        return dict(self.db.execute("SELECT * FROM state WHERE site=?", (site,)).fetchone())

    def update_state(self, site, **values):
        self.state(site)
        allowed = {"until", "manual", "reason", "next_at", "failures"}
        if not values or not values.keys() <= allowed:
            raise ValueError("허용되지 않은 상태 필드")
        with self.db:
            self.db.execute("UPDATE state SET " + ",".join(f"{k}=?" for k in values)
                            + " WHERE site=?", (*values.values(), site))

    def used(self, site):
        row = self.db.execute("SELECT count FROM budget WHERE day=? AND site=?",
                              (today(), site)).fetchone()
        return row[0] if row else 0

    def reserve(self, site, limit, delay, list_retry_url=None):
        # 전송 직전에 차감. 연결 실패도 차감하고 프로세스 재시작 시에도 유지함
        with self.db:
            if self.used(site) >= limit:
                return False
            if list_retry_url:
                # 재시도 횟수와 요청 예산을 같은 트랜잭션으로 예약합니다.
                # 전송 직후 종료되어도 다시 실행하며 횟수를 초기화하지 않습니다.
                row = self.db.execute('SELECT attempts,next_at FROM list_retries WHERE site=? AND url=?',
                                      (site,list_retry_url)).fetchone()
                if not row or row['attempts'] >= 3 or row['next_at'] > time.time():
                    return False
                self.db.execute('UPDATE list_retries SET attempts=attempts+1, next_at=? WHERE site=? AND url=?',
                                (time.time()+max(60,delay),site,list_retry_url))
            self.db.execute("INSERT INTO budget VALUES (?, ?, 1) "
                            "ON CONFLICT(day,site) DO UPDATE SET count=count+1",
                            (today(), site))
            self.db.execute("UPDATE state SET next_at=? WHERE site=?",
                            (time.time() + delay, site))
        return True

    def save_article(self, article):
        publication_date(article["published_at"])
        url = article["source_url"]
        # 같은 URL·출처 ID의 재실행만 방지합니다. 서로 다른 원문은 각각 저장합니다.
        if self.exists(url, article["source_code"], article["source_article_id"]):
            return False
        row=(url,article["source_code"],article["source_article_id"],json.dumps(article,ensure_ascii=False))
        with self.db:
            self.db.execute("INSERT INTO article_history VALUES (?,?,?,?)",row)
            self.db.execute("INSERT INTO articles VALUES (?,?,?,?)",row)
            self.db.execute("INSERT OR IGNORE INTO export_pending VALUES (?)",(url,))
        return True

    def export_pending_json(self):
        """변경 기사만 내보냅니다. DB 대기 기록은 파일 저장 후 지워 중단 후 복구합니다."""
        from news.report import validate_report_row
        directory = self.root / 'articles'
        directory.mkdir(exist_ok=True)
        with self.db:date_index.sync_dates(self.db)
        invalid={r[0] for r in self.db.execute('SELECT url FROM date_errors')}
        urls=[r[0] for r in self.db.execute('SELECT url FROM export_pending ORDER BY url') if r[0] not in invalid]
        # 날짜 오류 원문은 기존 파일·대기 기록을 유지하고 정상 기사 파일 저장을 계속합니다.
        for url in urls:
            row=self.db.execute('SELECT * FROM article_history WHERE url=?',(url,)).fetchone()
            if row is None:
                # 삭제 복구는 명시적 전체 export에서 처리합니다. 원문을 추정하지 않습니다.
                raise ValueError('JSON 내보내기 대기 기사의 원문이 없습니다. 전체 저장 결과 점검이 필요합니다.')
            article=validate_report_row(row)
            name=f"{row['site']}_{row['article_id']}.json"
            target=directory/name
            tmp=target.with_suffix('.tmp')
            tmp.write_text(json.dumps(article,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(target)
            with self.db:self.db.execute('DELETE FROM export_pending WHERE url=?',(url,))
        return len(urls)

    def export(self, full=True):
        if not full:
            return self.export_pending_json()

        # JSON은 DB의 복사본. 중간 실패 시 기존 JSON을 보존하고 다음 실행에서 재생성
        directory = self.root / "articles"
        directory.mkdir(exist_ok=True)
        for row in self.db.execute("SELECT site,article_id,payload FROM articles"):
            path = directory / f"{row['site']}_{row['article_id']}.json"
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(json.loads(row['payload']), ensure_ascii=False,
                                      indent=2), encoding="utf-8")
            tmp.replace(path)

        from news.report import write_report
        write_report(self)
        with self.db:self.db.execute('DELETE FROM export_pending')

    def close(self):
        self.db.close()


class SavePipeline:
    def process_item(self, item, spider):
        store = spider.store
        original = item.pop("_queue_url", item["source_url"])
        store.save_article(item)
        store.mark(original, "saved")
        store.event("saved", url=item["source_url"])
        spider.crawler.stats.inc_value("articles/saved")
        return item
