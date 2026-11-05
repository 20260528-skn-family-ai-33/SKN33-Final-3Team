"""기사 일별 집계로 주요 이슈(FR-204)를 판정해 alert_event에 저장하고 회원 알림함에 전달합니다.

실행 (저장소 루트에서):
  python -m backend.app.scheduler.jobs.major_issue_detect                    # 어제(KST) 기준, 스케줄러와 같은 동작
  python -m backend.app.scheduler.jobs.major_issue_detect --date 2026-10-14  # 기준일 T 지정
스케줄: backend/app/scheduler/main.py가 매일 기사 수집 뒤에 실행합니다.

- 기사량 판정(FR-204-01): 최근 7일(T-6~T) 기사 수 ÷ 직전 8주 주평균(T-62~T-7 합계÷8) ≥ 3. 주평균이 정확히 0일 때만 분모를 1로 봅니다.
  키워드와 품목(HS6·HS4)을 각각 판정하고, 국가 조건 없는 전체 집계 행(country_code NULL)을 씁니다.
- 분류 판정(FR-204-03): 최근 3일에 발행된 KOTRA 시장뉴스 중 원천 분류가 통상·규제인 기사. 기사마다 한 번만 사건을 만듭니다.
  늦게 등록된 기사도 잡도록 시장뉴스 수집 범위(DR-02, 최근 3일)만큼 봅니다.
- 수집이 끝나지 않은 날을 0건으로 보지 않습니다(FR-906-05). T-62~T에 집계 대상 출처의 수집 성공 기록이 없는 날이 있으면
  그 판정은 건너뛰고 경고 로그를 남깁니다. 키워드 집계는 시장뉴스+단신, 품목 집계는 시장뉴스만 대상입니다.
- 같은 사건은 event_key로 한 번만 저장하고, 전달은 사건을 처음 만들 때 그 시점의 수신 대상에게 한 번만 합니다.
  저장과 전달은 한 트랜잭션입니다. 알림 수신 조건(전체 ON·약관 동의·차단 키워드)은 deliver_event가 확인합니다.
- 실행 기록은 batch_run에 남깁니다. batch_run.target_table에 alert_event가 없어 판정 입력인 news_keyword_daily로 기록합니다.
- 그날 마지막 회차(08:00)나 날짜를 지정한 실행에서도 기사량 판정을 건너뛰면 FAILED로 기록해 관리자 알림에 보이게 합니다.
  05:00 회차는 수집이 늦었을 수 있어 로그만 남기고 08:00에 다시 판정합니다. 통상·규제 판정 결과는 그대로 저장됩니다.
"""
import argparse
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from backend.app.api.common import digest, now
from backend.app.api.database import Database, Store
from backend.app.api.event_delivery import deliver_event
from backend.app.api.news import TRADE_REGULATION_CATEGORIES
from backend.app.api.subscriptions import sources_ready
from backend.app.db.batch_log import Source, track_batch
from backend.app.db.session import make_engine

KST = ZoneInfo("Asia/Seoul")
JOB_NAME = "MAJOR_ISSUE_DETECT"
TARGET = "news_keyword_daily"
MARKET = "KOTRA_MARKET"
SHORT_NEWS = "KOTRA_SHORT_NEWS"
SOURCE = Source(MARKET, "KOTRA 해외시장뉴스")
MINIMUM_RATIO = 3
BASELINE_WEEKS = 8
TRADE_LOOKBACK_DAYS = 3  # DR-02: 시장뉴스는 매일 최근 3일치를 수집한다
ARTICLE_LIMIT = 5  # 사건마다 근거로 보여 줄 대표 기사 수
FINAL_RUN_HOUR = 8  # scheduler/main.py의 마지막 회차. 이후에도 건너뛰면 실패로 기록한다
log =logging.getLogger("major_issue_detect")


def windows(target):
    """최근 7일과 겹치지 않는 직전 56일 (시작일, 종료일). 경계는 팀 검토 대상 DEC-47."""
    return (target - timedelta(days=6), target), (target - timedelta(days=62), target - timedelta(days=7))


def spike(recent_count, baseline_count):
    """(실제 주평균, 적용 배수). 주평균이 정확히 0일 때만 분모를 1로 보고, 저장하는 평균은 0 그대로 둡니다."""
    average = Decimal(baseline_count) / BASELINE_WEEKS
    return float(average), float(Decimal(recent_count) / (average or 1))


def _counts(store, table, keys, start, end):
    t = store.t(table)
    columns = [t.c[key] for key in keys]
    rows = store.c.execute(select(*columns, func.sum(t.c.day_count)).where(
        t.c.country_code.is_(None), t.c.metric_date >= start, t.c.metric_date <= end).group_by(*columns))
    return {tuple(row[:-1]): int(row[-1]) for row in rows}


def _spikes(store, table, keys, target):
    recent_window, baseline_window = windows(target)
    recent, baseline = _counts(store, table, keys, *recent_window), _counts(store, table, keys, *baseline_window)
    for key, count in sorted(recent.items()):
        average, ratio = spike(count, baseline.get(key, 0))
        if count > 0 and ratio >= MINIMUM_RATIO:
            yield key, count, average, ratio


def _articles(store, condition, sources, start, end):
    """사건의 근거 기사: 공개 기사 중 최신순. 전체 기사 수와 같을 필요는 없습니다."""
    a, t = store.t("article"), store.t("article_tag")
    stmt = (select(a.c.article_id).join(t, t.c.article_id == a.c.article_id)
            .where(condition(t), a.c.visibility_status == "VISIBLE", a.c.source_code.in_(sources),
                   a.c.published_date >= start, a.c.published_date <= end)
            .distinct().order_by(a.c.article_id.desc()).limit(ARTICLE_LIMIT))
    return [int(identity) for identity in store.c.execute(stmt).scalars()]


def _save(store, key_parts, article_id, payload):
    """사건을 한 번만 저장하고 전달합니다. (새 사건 여부, 전달 건수)"""
    events = store.t("alert_event")
    key = digest(["MAJOR_ISSUE", *key_parts])
    identity = store.c.scalar(select(events.c.event_id).where(events.c.event_key == key))
    if identity is not None:
        return False, 0  # 저장과 전달은 한 트랜잭션이므로 이미 있는 사건은 전달도 끝났습니다. 나중에 알림을 켠 회원에게 다시 보내지 않습니다.
    identity = store.insert("alert_event", event_type="MAJOR_ISSUE", article_id=article_id, event_key=key,
                            payload_json={"schema_version": 1, **payload}, created_at=now())
    return True, deliver_event(store, identity)


def _spike_payload(target, label, subject, count, average, ratio, article_ids):
    recent_window, baseline_window = windows(target)
    return {"label": label, "reason": "WEEKLY_NEWS_SPIKE", "target": subject, "recent_week_count": count,
            "baseline_weekly": average, "ratio": ratio, "metric_date": target.isoformat(),
            "window_start": recent_window[0].isoformat(), "window_end": recent_window[1].isoformat(),
            "baseline_start": baseline_window[0].isoformat(), "baseline_end": baseline_window[1].isoformat(),
            "article_ids": article_ids}


def detect_keywords(store, target):
    recent_window = windows(target)[0]
    topics = store.t("keyword_topic")
    for (keyword,), count, average, ratio in _spikes(store, "news_keyword_daily", ["keyword_text"], target):
        ids = _articles(store, lambda t, k=keyword: t.c.keyword_text == k, (MARKET, SHORT_NEWS), *recent_window)
        # 동의어는 대표어 차단에도 걸리도록 대표어를 함께 저장합니다(notifications.event_allowed).
        canonical = store.c.scalar(select(topics.c.canonical_text).where(topics.c.keyword_text == keyword))
        subject = {"keyword_text": keyword, **({"kw_topic": canonical} if canonical else {})}
        payload = _spike_payload(target, keyword, subject, count, average, ratio, ids)
        yield _save(store, ["KEYWORD", keyword, target.isoformat()], ids[0] if ids else None, payload)


def detect_products(store, target):
    recent_window = windows(target)[0]
    names = store.t("hs_code")
    for (code, version), count, average, ratio in _spikes(store, "news_product_daily", ["hs_code", "hs_ver"], target):
        # HS4 집계에는 그 아래 HS6 태그 기사가 들어 있다
        ids = _articles(store, lambda t, c=code, v=version: (t.c.hs_code.startswith(c)) & (t.c.hs_ver == v), (MARKET,), *recent_window)
        label = store.c.scalar(select(names.c.name_ko).where(names.c.hs_code == code, names.c.hs_ver == version)) or code
        payload = _spike_payload(target, label, {"hs_code": code, "hs_ver": version}, count, average, ratio, ids)
        yield _save(store, ["PRODUCT", code, version, target.isoformat()], ids[0] if ids else None, payload)


def detect_trade_regulation(store, target):
    a = store.t("article")
    rows = store.rows(select(a.c.article_id, a.c.title).where(
        a.c.source_code == MARKET, a.c.category.in_(TRADE_REGULATION_CATEGORIES), a.c.visibility_status == "VISIBLE",
        a.c.published_date >= target - timedelta(days=TRADE_LOOKBACK_DAYS - 1), a.c.published_date <= target))
    for row in rows:
        identity = int(row["article_id"])
        payload = {"label": row["title"], "reason": "KOTRA_TRADE_REGULATION", "target": {},
                   "metric_date": target.isoformat(), "article_ids": [identity]}
        yield _save(store, ["KOTRA_TRADE_REGULATION", identity], identity, payload)


def detect(database, target):
    """한 기준일을 판정합니다. 수집이 끝나지 않은 기간의 기사량 판정은 건너뜁니다."""
    start = windows(target)[1][0]
    summary = {"events": 0, "deliveries": 0, "skipped": []}
    with database.engine.begin() as connection:
        store = Store(database, connection)
        jobs = [("trade_regulation", (), detect_trade_regulation),
                ("keyword", (MARKET, SHORT_NEWS), detect_keywords), ("product", (MARKET,), detect_products)]
        for name, sources, job in jobs:
            if sources and not sources_ready(store, sources, start, target):
                summary["skipped"].append(name)
                log.warning("major_issue_skipped kind=%s sources=%s period=%s~%s: 수집 완료 기록이 없는 날이 있습니다", name, ",".join(sources), start, target)
                continue
            for created, delivered in job(store, target):
                summary["events"] += int(created)
                summary["deliveries"] += delivered
    return summary


def is_final_run(target_given, moment):
    """이번 실행 뒤로 같은 기준일을 다시 판정할 회차가 없으면 True."""
    return target_given or moment.hour >= FINAL_RUN_HOUR


def run(target=None, engine=None):
    moment = datetime.now(KST)
    final = is_final_run(target is not None, moment)
    target = target or moment.date() - timedelta(days=1)
    own_engine = engine is None
    engine = engine or make_engine()
    try:
        with track_batch(SOURCE, JOB_NAME, TARGET, target, target, engine=engine, logger=log) as batch:
            summary = detect(Database(engine), target)
            batch.received_count = summary["events"]
            if summary["skipped"] and final:
                batch.fail()
        log.info("major_issue_done target=%s events=%s deliveries=%s skipped=%s", target, summary["events"], summary["deliveries"], summary["skipped"])
        return summary
    finally:
        if own_engine:
            engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date", type=date.fromisoformat, help="기준일 T (YYYY-MM-DD). 기본은 어제(KST)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    print(run(args.date))


if __name__ == "__main__":
    main()
