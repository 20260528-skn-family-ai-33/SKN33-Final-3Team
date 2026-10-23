"""해외시장뉴스 기사·태그·일별 집계를 MySQL에 적재합니다 (article → article_tag → news_keyword_daily·news_product_daily).

실행 (저장소 루트에서): python -m backend.app.scheduler.jobs.news_load [--raw 경로] [--clean 경로]
- 입력: data/news의 news_raw_YYYYMMDD_YYYYMMDD.csv(끝 날짜가 가장 늦은 파일) + data/news_keywords/news_keywords_clean.csv.
  키워드 결과가 없는 기사도 article에는 넣고, 그 기사의 기존 태그는 건드리지 않습니다 (새 기사면 태그 없음).
- 테이블은 만들지 않습니다. 테이블이 없으면 오류 로그를 남기고 종료합니다 (db/mysql/init/01_schema.sql 먼저 실행).
- 출처 KOTRA_MARKET은 service_option에 없으면 batch_run 기록 때 자동으로 등록됩니다 (batch_log.ensure_source).
- 같은 (source_code, external_id) 기사가 있으면 제목·발행일·URL·본문·분류를 갱신합니다 (article_id 유지).
  원본에 같은 기사 ID가 두 번 있으면 마지막 행을 씁니다.
- 키워드 결과가 있는 기사의 태그는 지우고 새로 넣되, 관리자가 수동 지정한 국가·HS 태그는 보존합니다.
  원본은 전체인데 키워드 결과가 일부 기간뿐이어도(--since) 나머지 기사의 태그·집계는 그대로 남습니다.
- 일별 집계는 입력 기사의 발행일(발행일이 바뀐 기사는 예전 날짜도)만 지우고 DB 전체 기사(다른 출처 포함)에서 다시 셉니다
  (DISTINCT article_id).
- 품목 태그는 hs_code 테이블에 있는 코드만 넣습니다. hs_code가 비어 있으면 품목 태그·집계 0건으로 넘어가고 경고만 남깁니다.
- 전체를 한 트랜잭션으로 넣습니다. 중간에 실패하면 모두 롤백되어 아무것도 반영되지 않습니다.
- 실행할 때마다 batch_run에 한 건 기록합니다 (job NEWS_LOAD, 대상 article, 수신 = 원본 기사 수, API 호출 0).
- 결과·오류는 화면과 data/news/logs/news_load_<시각>.log에 남깁니다.
"""
import argparse
import csv
import logging
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import delete, func, inspect, select
from sqlalchemy.dialects import mysql, sqlite
from sqlalchemy.exc import SQLAlchemyError

from backend.app.db.batch_log import Source, track_batch, ensure_source
from backend.app.db.session import make_engine, session_scope
from backend.app.db.news_lock import article_load_lock
from backend.app.models.hs_code import HsCodeMst
from backend.app.models.news import COUNTRY, Article, ArticleTag, NewsKeywordDaily, NewsProductDaily
from backend.app.scheduler.jobs.hs_code_load import hb
from backend.app.services import news_load as nl
from backend.app.services import news_schema

ROOT = Path(__file__).resolve().parents[4]
RAW_DIR = ROOT / "data" / "news"
DEFAULT_CLEAN = ROOT / "data" / "news_keywords" / "news_keywords_clean.csv"
LOG_DIR = RAW_DIR / "logs"
TABLES = [Article, ArticleTag, NewsKeywordDaily, NewsProductDaily]
JOB_NAME = "NEWS_LOAD"
SOURCE = Source(nl.SOURCE_CODE, "KOTRA 해외시장뉴스",
                {"provider": "KOTRA", "api": "https://apis.data.go.kr/B410001/kotra_overseasMarketNews/ovseaMrktNews/"
                                             "ovseaMrktNews", "format": "json"})  # news_collect.py의 API_URL
BATCH = 200  # 한 INSERT에 넣는 기사 수 (본문이 길어 max_allowed_packet을 넘지 않게)
log = logging.getLogger("news_load")


class TableMissingError(RuntimeError):
    pass


def setup_log(log_dir):
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"news_load_{datetime.now():%Y%m%d_%H%M%S}.log"
    log.setLevel(logging.DEBUG)
    for h in log.handlers:
        h.close()
    log.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    for h, level in ((logging.StreamHandler(), logging.INFO), (logging.FileHandler(path, encoding="utf-8"), logging.DEBUG)):
        h.setFormatter(fmt)
        h.setLevel(level)
        log.addHandler(h)
    return path


def latest_raw(raw_dir=RAW_DIR):
    """기간이 붙은 통합 원본(news_raw_20220101_20261013.csv) 중 끝 날짜가 가장 늦은 것. 같으면 기간이 가장 긴 것."""
    found = sorted(raw_dir.glob("news_raw_????????_????????.csv"),
                   key=lambda p: (p.stem[-8:], -int(p.stem[-17:-9])))  # (끝 날짜, 시작 날짜가 이를수록 뒤)
    if not found:
        raise FileNotFoundError(f"{raw_dir}에 news_raw_YYYYMMDD_YYYYMMDD.csv가 없습니다.")
    return found[-1]


def read_csv(path, columns):
    csv.field_size_limit(sys.maxsize if sys.maxsize < 2 ** 31 else 2 ** 31 - 1)  # 본문 HTML이 수 MB인 기사가 있음
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        missing = set(columns) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path}에 {', '.join(sorted(missing))} 컬럼이 없습니다.")
        return [{k: r[k] for k in columns} for r in reader]


def check_tables(engine):
    names = set(inspect(engine).get_table_names())
    missing = [t.__tablename__ for t in TABLES if t.__tablename__ not in names]
    if missing:
        raise TableMissingError(f"{engine.url.render_as_string(hide_password=True)}에 {', '.join(missing)} 테이블이 없습니다. "
                                "db/mysql/init/01_schema.sql을 먼저 실행하세요.")


def upsert_articles(session, rows, table=None):
    """(source_code, external_id) 기준 upsert. CRUD.upsert_many는 기본키 기준이라 자동 번호 테이블에 쓰지 않습니다."""
    table = news_schema.reflect(session.connection(), "article") if table is None else table
    rows = news_schema.article_insert_rows(session, table, rows)
    update = ["title", "published_date", "source_url", "body_masked", "category"]
    dialect = session.get_bind().dialect.name
    for i in range(0, len(rows), BATCH):
        chunk = rows[i:i + BATCH]
        if dialect == "mysql":
            stmt = mysql.insert(table).values(chunk)
            stmt = stmt.on_duplicate_key_update({k: stmt.inserted[k] for k in update})
        elif dialect == "sqlite":
            stmt = sqlite.insert(table).values(chunk)
            stmt = stmt.on_conflict_do_update(index_elements=["source_code", "external_id"],
                                              set_={k: stmt.excluded[k] for k in update})
        else:
            raise NotImplementedError(f"upsert 미지원 DB: {dialect}")
        session.execute(stmt)


def _in_chunks(values, size=1000):
    values = list(values)
    for i in range(0, len(values), size):
        yield values[i:i + size]


def article_ids(session, external_ids, source_code=nl.SOURCE_CODE):
    out = {}
    for chunk in _in_chunks(external_ids):
        out |= dict(session.execute(select(Article.external_id, Article.article_id).where(
            Article.source_code == source_code, Article.external_id.in_(chunk))).all())
    return out


def keyword_article_counts(engine):
    """article_tag 키워드별 고유 기사 수 {키워드: 기사 수} (누적, 출처 무관). 테이블이 없으면 빈 Counter."""
    if ArticleTag.__tablename__ not in inspect(engine).get_table_names():
        return Counter()
    with session_scope(engine) as s:
        q = (select(ArticleTag.keyword_text, func.count(func.distinct(ArticleTag.article_id)))
             .where(ArticleTag.keyword_text.isnot(None)).group_by(ArticleTag.keyword_text))
        return Counter(dict(s.execute(q).all()))


def old_dates(session, external_ids, source_code=nl.SOURCE_CODE):
    """이미 DB에 있는 기사들의 현재 발행일."""
    out = set()
    for chunk in _in_chunks(external_ids):
        out |= set(session.scalars(select(Article.published_date).where(
            Article.source_code == source_code, Article.external_id.in_(chunk)).distinct()))
    return out


def rebuild_daily(session, days):
    """발행일이 days인 DB 전체 기사(출처 무관)로 두 집계 테이블의 그날 행을 다시 만듭니다."""
    days = sorted(days)
    a, t = Article.__table__, ArticleTag.__table__
    dates, keywords, countries, hs_tags = {}, [], {}, []
    for chunk in _in_chunks(days):
        q = select(t.c.article_id, a.c.published_date, t.c.keyword_text, t.c.country_code, t.c.hs_code, t.c.hs_ver) \
            .join(a, a.c.article_id == t.c.article_id).where(a.c.published_date.in_(chunk))
        for article_id, day, kw, country, hs, ver in session.execute(q):
            dates[article_id] = day
            if kw is not None:
                keywords.append((article_id, kw))
            elif country is not None:
                countries.setdefault(article_id, set()).add(country)
            elif len(hs) == 6:
                hs_tags.append((article_id, hs, ver))
    hs_codes = {tuple(r) for r in session.execute(select(HsCodeMst.hs_code, HsCodeMst.hs_ver).where(
        HsCodeMst.hs_code.in_({h[:4] for _, h, _ in hs_tags})))} if hs_tags else set()
    kw_counts = nl.daily_counts(dates, keywords, countries)
    hs_counts = nl.daily_counts(dates, nl.product_targets(hs_tags, hs_codes), countries)
    for chunk in _in_chunks(days):
        session.execute(delete(NewsKeywordDaily).where(NewsKeywordDaily.metric_date.in_(chunk)))
        session.execute(delete(NewsProductDaily).where(NewsProductDaily.metric_date.in_(chunk)))
    _insert(session, NewsKeywordDaily, [{"metric_date": d, "keyword_text": k, "country_code": c, "day_count": n}
                                        for (d, k, c), n in kw_counts.items()])
    _insert(session, NewsProductDaily, [{"metric_date": d, "hs_code": h, "hs_ver": v, "country_code": c,
                                         "day_count": n} for (d, (h, v), c), n in hs_counts.items()])
    return len(kw_counts), len(hs_counts)


def _insert(session, model, rows, size=2000):
    for i in range(0, len(rows), size):
        session.execute(model.__table__.insert(), rows[i:i + size])


def load(engine, raws, cleans):
    """적재 결과 건수 dict를 돌려줍니다. 한 트랜잭션: 어느 단계든 실패하면 전체 롤백."""
    check_tables(engine)
    # 같은 기사 ID가 두 번 오면 마지막 행 (한 INSERT 안에서 같은 키를 두 번 갱신하지 않게)
    articles = list({r["external_id"]: r for r in map(nl.article_row, raws)}.values())
    for r in articles:
        r["published_date"] = date.fromisoformat(r["published_date"])
    by_id = {c["bbstxSn"].strip(): c for c in cleans}
    with article_load_lock(engine), session_scope(engine) as s:
        ensure_source(s, SOURCE)
        table = news_schema.reflect(s.connection(), "article")
        countries = set(s.scalars(select(COUNTRY.c.country_code)))
        hs_codes = {tuple(r) for r in s.execute(select(HsCodeMst.hs_code, HsCodeMst.hs_ver).where(
            HsCodeMst.hs_code.like("______")))}  # 6자리 (밑줄 6개)
        # 발행일이 바뀐 기사는 예전 날짜 집계에서도 빠져야 하므로 예전 날짜도 다시 셈
        days = {r["published_date"] for r in articles} | old_dates(s, [r["external_id"] for r in articles])
        upsert_articles(s, articles, table)
        ids = article_ids(s, [r["external_id"] for r in articles])
        # 태그는 키워드 결과가 있는 기사만 다시 넣음 (결과가 없는 기사의 기존 태그를 지우지 않게)
        tagged = [r for r in articles if r["external_id"] in by_id]
        metadata = news_schema.article_metadata(s, table, ids.values())
        tags, unknown = [], set()
        replace_ids = {column: [] for column in ("keyword_text", "country_code", "hs_code")}
        for r in tagged:
            clean = by_id[r["external_id"]]
            article_id = ids[r["external_id"]]
            meta = metadata.get(article_id) or {}
            replace = ["keyword_text"]
            if not meta.get("country_tags_manual", False):
                replace.append("country_code")
            if not meta.get("hs_tags_manual", False):
                replace.append("hs_code")
            for column in replace:
                replace_ids[column].append(article_id)
            candidates = nl.tag_rows(clean, article_id, countries, hs_codes, hb.hs_ver_for(r["published_date"]))
            tags += [t for t in candidates if any(t[c] is not None for c in replace)]
            unknown |= set(nl.split_values(clean.get("country_all"))) - countries
        for column, affected_ids in replace_ids.items():
            for chunk in _in_chunks(affected_ids):
                s.execute(delete(ArticleTag).where(ArticleTag.article_id.in_(chunk),
                                                  getattr(ArticleTag, column).isnot(None)))
        _insert(s, ArticleTag, tags)
        kw_rows, hs_rows = rebuild_daily(s, days)
    kinds = {k: sum(t[k] is not None for t in tags) for k in ("keyword_text", "country_code", "hs_code")}
    return {"articles": len(articles), "no_keyword_result": sum(r["external_id"] not in by_id for r in articles),
            "tags": len(tags), **kinds, "unknown_countries": sorted(unknown), "hs_dictionary": len(hs_codes),
            "keyword_daily": kw_rows, "product_daily": hs_rows}


def run(raw_path=None, clean_path=DEFAULT_CLEAN, db_url=None, log_dir=LOG_DIR, track=True):
    """적재를 실행하고 결과·오류를 로그에 남깁니다. 성공 0, 실패 1. track=False면 batch_run 기록을 생략합니다."""
    log_path = setup_log(log_dir)
    try:
        raw_path = Path(raw_path) if raw_path else latest_raw()
        raws = read_csv(raw_path, nl.RAW_COLUMNS)
        cleans = read_csv(clean_path, nl.CLEAN_COLUMNS)
    except (FileNotFoundError, ValueError) as e:
        log.error(f"적재 실패: {e} | 로그: {log_path}")
        return 1
    log.info(f"기사 적재 시작 | 원본 {raw_path.name} {len(raws):,}건 | 키워드 결과 {Path(clean_path).name} {len(cleans):,}건")
    days = sorted(str(r["othbcDt"])[:10] for r in raws) or [date.today().isoformat()]
    period = date.fromisoformat(days[0]), date.fromisoformat(days[-1])
    if not track:
        return _run(raws, cleans, db_url, log_path)
    batch_engine = make_engine(db_url) if db_url else None
    try:
        with track_batch(SOURCE, JOB_NAME, "article", *period, engine=batch_engine, logger=log) as batch:
            batch.received_count = len(raws)
            rc = _run(raws, cleans, db_url, log_path)
            if rc != 0:
                batch.fail()
    finally:
        if batch_engine is not None:
            batch_engine.dispose()
    return rc


def _run(raws, cleans, db_url, log_path):
    started = datetime.now()
    engine = None
    try:
        engine = make_engine(db_url)
        log.info(f"DB {engine.url.render_as_string(hide_password=True)}")
        out = load(engine, raws, cleans)
    except TableMissingError as e:
        log.error(f"적재 실패: {e} | 로그: {log_path}")
        return 1
    except SQLAlchemyError as e:
        log.error(f"적재 실패: DB 오류로 롤백되어 반영되지 않았습니다. {type(e).__name__}: {e.__cause__ or e} | 로그: {log_path}")
        log.debug("DB 오류 상세", exc_info=True)
        return 1
    except Exception:
        log.exception(f"적재 실패: 예상하지 못한 오류 | 로그: {log_path}")
        return 1
    finally:
        if engine is not None:
            engine.dispose()
    if out["no_keyword_result"]:
        log.warning(f"키워드 결과가 없는 기사 {out['no_keyword_result']:,}건은 기존 태그를 그대로 두었습니다 "
                    f"(새 기사면 태그 없음, keyword_clean.py로 그 기간을 정리한 뒤 다시 실행)")
    if out["unknown_countries"]:
        log.warning(f"country 테이블에 없는 국가 코드 {', '.join(out['unknown_countries'])}는 태그에서 뺐습니다")
    if not out["hs_dictionary"]:
        log.warning("hs_code 테이블이 비어 있어 품목 태그·집계를 넣지 않았습니다 (hs_code_load 후 다시 실행)")
    log.info(f"적재 완료: 기사 {out['articles']:,}건 | 태그 {out['tags']:,}건 (키워드 {out['keyword_text']:,} · "
             f"국가 {out['country_code']:,} · 품목 {out['hs_code']:,}) | 키워드 집계 {out['keyword_daily']:,}행 · "
             f"품목 집계 {out['product_daily']:,}행 | 소요 {str(datetime.now() - started).split('.')[0]} | 로그: {log_path}")
    return 0


def main():
    parser = argparse.ArgumentParser(description="해외시장뉴스 기사·태그·일별 집계를 MySQL에 적재")
    parser.add_argument("--raw", type=Path, help="원본 CSV (기본: data/news의 최신 news_raw_YYYYMMDD_YYYYMMDD.csv)")
    parser.add_argument("--clean", type=Path, default=DEFAULT_CLEAN, help="키워드 결과 CSV (기본: news_keywords_clean.csv)")
    parser.add_argument("--db-url", help="SQLAlchemy DB URL (기본: .env의 MYSQL_* 값)")
    args = parser.parse_args()
    raise SystemExit(run(args.raw, args.clean, args.db_url))


if __name__ == "__main__":
    main()
