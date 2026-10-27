"""단신속보 CSV/JSON → MySQL 기사·태그·일별 집계. 실행: python -m backend.app.scheduler.jobs.shortnews_load --raw 경로"""
import argparse
import logging
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import MetaData, Table, delete, select

from backend.app.db.batch_log import Source, ensure_source
from backend.app.db.session import make_engine, mysql_url, session_scope
from backend.app.db.news_lock import article_load_lock
from backend.app.models.batch import ServiceOption
from backend.app.models.news import ArticleTag
from backend.app.scheduler.jobs import news_load as news_job
from backend.app.services import news_load as news, shortnews_load as sn
from backend.app.services import news_schema

ROOT = Path(__file__).resolve().parents[4]
LOG_DIR = ROOT / "data" / "shortnews" / "logs"
SOURCE = Source(sn.SOURCE_CODE, "KOTRA 단신속보",
                {"provider": "KOTRA", "api": "https://apis.data.go.kr/B410001/shortBreakingNews/shortBreakingNews",
                 "format": "json/xml"})
JOB_NAME = "SHORTNEWS_LOAD"
LEGACY_SOURCE_CODE = "KOTRA_SHORTNEWS"
log = logging.getLogger("shortnews_load")


def now():
    return datetime.now(ZoneInfo("Asia/Seoul")).replace(tzinfo=None, microsecond=0)


def reflect(engine, name):
    # 신규 ERD의 필수 JSON을 처리하되, 공용 ORM과 기존 DB를 자동 변경하지 않습니다.
    return Table(name, MetaData(), autoload_with=engine, resolve_fks=False)


def register_source(session):
    ensure_source(session, SOURCE)
    if session.get(ServiceOption, SOURCE.code).option_type != "SOURCE":
        raise ValueError(f"{SOURCE.code} 코드가 SOURCE 외 용도로 사용 중입니다")


def migrate_source(session, table):
    """옛 출처를 동일 article_id로 전환합니다. 기존 충돌은 임의 병합·삭제하지 않습니다."""
    old, current = table.alias("old_shortnews"), table.alias("current_shortnews")
    conflict = session.scalar(select(old.c.external_id).select_from(old.join(
        current, (old.c.external_id == current.c.external_id) &
        (current.c.source_code == SOURCE.code))).where(
            old.c.source_code == LEGACY_SOURCE_CODE).limit(1))
    if conflict is not None:
        raise ValueError("단신속보 출처 전환 충돌: KOTRA_SHORTNEWS와 KOTRA_SHORT_NEWS에 "
                         "동일 기사 ID가 이미 존재합니다. 기존 행·관리자 정보·참조를 확인하여 통합해야 합니다")
    days = set(session.scalars(select(table.c.published_date).where(
        table.c.source_code == LEGACY_SOURCE_CODE).distinct()))
    if days:
        session.execute(table.update().where(table.c.source_code == LEGACY_SOURCE_CODE)
                        .values(source_code=SOURCE.code))
    return days


def prepare(raws, cleans):
    """같은 ID는 기존 뉴스 적재와 같이 마지막 행 사용. 잘못된 행을 조용히 제외하지 않습니다."""
    articles, originals, cleaned = {}, {}, {}
    for number, raw in enumerate(raws, 1):
        try:
            row = sn.article_row(raw)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"원본 {number}번째 행: {exc}") from None
        articles[row["external_id"]] = row
        originals[row["external_id"]] = raw
    for number, clean in enumerate(cleans, 1):
        key = sn.article_id(clean.get("nttSn"))
        if not set(sn.CLEAN_COLUMNS).issubset(clean):
            raise ValueError(f"정제 결과 {number}번째 행의 필수 컬럼 누락")
        cleaned[key] = clean
    return list(articles.values()), originals, cleaned


def load(engine, raws, cleans=()):
    """기사·태그·일별 집계를 단일 트랜잭션으로 적재합니다. 실행 이력은 run()에서 기록합니다."""
    articles, originals, cleaned = prepare(raws, cleans)
    external_ids = [r["external_id"] for r in articles]
    unknown_countries = set()
    hs_ignored = sum(bool(news.split_values(c.get('hs6'))) for c in cleaned.values())
    tag_count = manual_kept = 0
    with article_load_lock(engine), session_scope(engine) as session:
        # 마이그레이션 잠금 대기 중 바뀐 스키마를 사용하지 않도록 획득 후 조회합니다.
        news_job.check_tables(engine)
        table, country = reflect(session.connection(), "article"), reflect(session.connection(), "country")
        register_source(session)
        migrated_days = migrate_source(session, table)
        countries = list(session.execute(select(country)).mappings())
        country_set = {r["country_code"] for r in countries}
        lookup = sn.country_lookup(countries)
        days = migrated_days | {r["published_date"] for r in articles} | news_job.old_dates(session, external_ids, SOURCE.code)
        # 공용 upsert는 기사 내용만 UPDATE하므로 기존 공개 상태·최초 적재 시각·관리자 JSON은 유지됩니다.
        news_job.upsert_articles(session, articles, table=table)
        ids = news_job.article_ids(session, external_ids, SOURCE.code)
        metadata = news_schema.article_metadata(session, table, ids.values())
        tags = []
        for row in articles:
            key = row["external_id"]
            article_id = ids[key]
            meta = metadata.get(article_id) or {}
            clean = cleaned.get(key)
            # 정제 결과가 없으면 국가만 갱신하고 기존 키워드·HS 태그는 보존합니다.
            values = dict(clean) if clean is not None else {"kw_topic": "", "hs6": ""}
            # 과거 정제 파일의 추가 hs6 컬럼도 적재하지 않습니다. 기존 HS 태그는 건드리지 않습니다.
            values["hs6"] = ""
            if clean is None:
                codes, unknown = sn.country_codes(originals[key].get("nat"), lookup)
                values["country_all"] = "|".join(codes)
            else:
                unknown = set(news.split_values(values["country_all"])) - country_set
            unknown_countries.update(unknown)
            candidates = news.tag_rows(values, article_id, country_set, set(), None)
            replace = []
            if not meta.get("country_tags_manual", False):
                replace.append("country_code")
            else:
                manual_kept += 1
            if clean is not None:
                replace.append("keyword_text")
            for column in replace:
                session.execute(delete(ArticleTag).where(ArticleTag.article_id == article_id,
                                                        getattr(ArticleTag, column).isnot(None)))
            tags.extend(t for t in candidates if any(t[c] is not None for c in replace))
        news_job._insert(session, ArticleTag, tags)
        tag_count = len(tags)
        keyword_rows, product_rows = news_job.rebuild_daily(session, days)
    return {"articles": len(articles), "received": len(raws), "duplicates": len(raws) - len(articles),
            "tags_written": tag_count, "manual_tag_groups_preserved": manual_kept,
            "without_clean": sum(r["external_id"] not in cleaned for r in articles),
            "unknown_countries": sorted(unknown_countries), "ignored_hs_rows": hs_ignored,
            "keyword_daily": keyword_rows, "product_daily": product_rows}


@contextmanager
def track_load(engine, period, received):
    """기존 batch_run 흐름에 신규 ERD detail_json을 반영합니다. 기사와 별도 커밋하여 실패도 남깁니다."""
    values = {"source_code": SOURCE.code, "job_name": JOB_NAME, "target_table": "article",
              "period_start": period[0], "period_end": period[1], "status": "RUNNING",
              "started_at": now(), "request_count": 0, "received_count": received}
    with article_load_lock(engine), session_scope(engine) as session:
        table = reflect(session.connection(), "batch_run")
        if "detail_json" in table.c:
            values["detail_json"] = {"schema_version": 1}
        register_source(session)
        run_id = session.execute(table.insert().values(**values)).inserted_primary_key[0]
    detail = {"schema_version": 1}
    status = "FAILED"
    try:
        yield detail
        status = "SUCCESS"
    except BaseException as exc:
        detail["error_type"] = type(exc).__name__
        raise
    finally:
        end = {"status": status, "finished_at": now()}
        if "detail_json" in table.c:
            end["detail_json"] = detail
        try:
            with article_load_lock(engine), session_scope(engine) as session:
                session.execute(table.update().where(table.c.run_id == run_id).values(**end))
        except Exception as exc:
            # 기사 트랜잭션 성공과 이력 갱신 실패를 혼동하지 않습니다.
            log.warning("기사 처리 상태 %s, batch_run %s 종료 기록 실패: %s", status, run_id, type(exc).__name__)


def setup_log(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"shortnews_load_{datetime.now():%Y%m%d_%H%M%S_%f}.log"
    for handler in log.handlers:
        handler.close()
    log.handlers.clear()
    log.setLevel(logging.INFO)
    for handler in (logging.StreamHandler(), logging.FileHandler(path, encoding="utf-8")):
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(handler)
    return path


def run(raw_path, clean_path=None, db_url=None, log_dir=LOG_DIR, *, period=None):
    """성공 0 / 실패 1. DB/테이블 생성 및 API 수집은 실행하지 않습니다."""
    engine = None
    setup_log(log_dir)
    try:
        raws = sn.read_records(raw_path)
        cleans = sn.read_records(clean_path, sn.CLEAN_COLUMNS) if clean_path else []
        articles, _, _ = prepare(raws, cleans)
        days = [r["published_date"] for r in articles] or [now().date()]
        engine = make_engine(db_url or mysql_url(ROOT / ".env"))
        # 일일 작업은 0건이어도 조회한 전날을 이력에 남깁니다. 파일 단독 적재는 기존 날짜 계산을 유지합니다.
        with track_load(engine, period or (min(days), max(days)), len(raws)) as detail:
            result = load(engine, raws, cleans)
            detail["result"] = result
        log.info("적재 완료: 기사 %s건, 중복 입력 %s건, 태그 %s행, 키워드 집계 %s행, 품목 집계 %s행",
                 result["articles"], result["duplicates"], result["tags_written"],
                 result["keyword_daily"], result["product_daily"])
        if result["unknown_countries"]:
            log.warning("미매핑 국가(기사는 적재): %s", result["unknown_countries"])
        if result["ignored_hs_rows"]:
            log.warning("단신속보는 HS 적재·집계 미지원: 정제 결과 %s행의 HS 입력 무시", result["ignored_hs_rows"])
        return 0
    except (ValueError, OSError) as exc:
        log.error("적재 실패: %s", exc)
        return 1
    except Exception as exc:
        # DB 예외의 SQL 매개변수에는 원문·접속 정보가 들어갈 수 있으므로 출력하지 않습니다.
        log.error("적재 실패: %s (기사·태그·집계 트랜잭션 실패 시 전체 롤백)", type(exc).__name__)
        return 1
    finally:
        if engine is not None:
            engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, required=True, help="shortnews_raw.csv 또는 shortnews_raw.json")
    parser.add_argument("--clean", type=Path, help="선택: nttSn, kw_topic, country_all 정제 결과 CSV/JSON (HS 미지원)")
    parser.add_argument("--db-url", help="SQLAlchemy URL. 생략하면 저장소 루트 .env 및 환경변수의 MYSQL_* 사용")
    parser.add_argument("--log-dir", type=Path, default=LOG_DIR)
    args = parser.parse_args()
    raise SystemExit(run(args.raw, args.clean, args.db_url, args.log_dir))


if __name__ == "__main__":
    main()
