"""기사 일별 집계로 주요 이슈(FR-204)를 판정해 alert_event에 저장하고 회원 알림함에 전달합니다.

실행 (저장소 루트에서):
  python -m backend.app.scheduler.jobs.major_issue_detect                    # 어제(KST) 기준, 스케줄러와 같은 동작
  python -m backend.app.scheduler.jobs.major_issue_detect --date 2026-10-14  # 기준일 T 지정
스케줄: backend/app/scheduler/main.py가 매일 기사 수집 뒤에 실행합니다.

- 기사량 판정(FR-204-01): 최근 7일(T-6~T) 기사 수 ÷ 직전 8주 주평균(T-62~T-7 합계÷8) ≥ 3. 주평균이 정확히 0일 때만 분모를 1로 봅니다.
  키워드와 품목(HS6·HS4)을 각각 판정하고, 국가 조건 없는 전체 집계 행(country_code NULL)을 씁니다.
  일별 집계는 기사를 숨겨도 줄지 않으므로 비공개 기사를 뺀 공개 기사 수로 판정하고, 공개 근거 기사가 없으면 이슈로 만들지 않습니다.
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

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from backend.app.api.common import digest, now
from backend.app.api.database import Database, Store
from backend.app.api.event_delivery import deliver_event
from backend.app.api.news import TRADE_REGULATION_CATEGORIES, blocked_article
from backend.app.api.subscriptions import sources_ready
from backend.app.db.batch_log import Source, track_batch
from backend.app.db.session import make_engine
from backend.app.services.article_sources import KEYWORD_DAILY_SOURCES, MARKET, PRODUCT_DAILY_SOURCES, SHORT_NEWS

KST = ZoneInfo("Asia/Seoul")
JOB_NAME = "MAJOR_ISSUE_DETECT"
TARGET = "news_keyword_daily"
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


def _hidden_tags(store, column, sources, start, end, countries=(), blocked=()):
    """기간 안 비공개 기사와 회원이 차단한 기사의 태그 행 (값, ..., 기사 ID). 일별 집계는 줄지 않으므로 뺄 건수를 여기서 구합니다."""
    a, t = store.t("article"), store.t("article_tag")
    country_tag = t.alias("country_tag")
    columns = [*column(t), *([country_tag.c.country_code] if countries else []), a.c.article_id]
    excluded = a.c.visibility_status != "VISIBLE"
    if blocked:
        excluded = or_(excluded, blocked_article(store, blocked))
    stmt = select(*columns).select_from(a).join(t, t.c.article_id == a.c.article_id).where(
        excluded, a.c.source_code.in_(sources), a.c.published_date >= start, a.c.published_date <= end)
    if countries:
        stmt = stmt.join(country_tag, country_tag.c.article_id == a.c.article_id).where(country_tag.c.country_code.in_(countries))
    return store.c.execute(stmt.distinct()).all()


def _subtract(totals, hidden):
    """일별 집계 합계에서 숨긴 기사를 빼고, 남은 공개 기사가 있는 항목만 돌려줍니다."""
    counts = {key: total - len(hidden.get(key, ())) for key, total in totals.items()}
    return {key: count for key, count in counts.items() if count > 0}


def keyword_counts(store, start, end, countries=(), blocked=()):
    """공개 기사 기준 {(키워드, 국가): 건수}. 국가를 주지 않으면 전체 집계 행(국가 None)입니다. blocked를 주면 그 회원의 차단 기사도 뺍니다."""
    t = store.t("news_keyword_daily")
    scope = t.c.country_code.in_(countries) if countries else t.c.country_code.is_(None)
    rows = store.c.execute(select(t.c.keyword_text, t.c.country_code, func.sum(t.c.day_count)).where(
        scope, t.c.metric_date >= start, t.c.metric_date <= end).group_by(t.c.keyword_text, t.c.country_code))
    totals = {(keyword, country): int(total) for keyword, country, total in rows}
    hidden = {}
    for row in _hidden_tags(store, lambda tag: [tag.c.keyword_text], KEYWORD_DAILY_SOURCES, start, end, countries, blocked):
        hidden.setdefault((row[0], row[1] if countries else None), set()).add(row[-1])
    return _subtract(totals, hidden)


def product_counts(store, start, end, blocked=()):
    """공개 기사 기준 {(품목 코드, 판본): 건수}. HS6 태그 기사는 위 단계 HS4 집계에도 들어 있습니다. blocked는 keyword_counts와 같습니다."""
    t = store.t("news_product_daily")
    rows = store.c.execute(select(t.c.hs_code, t.c.hs_ver, func.sum(t.c.day_count)).where(
        t.c.country_code.is_(None), t.c.metric_date >= start, t.c.metric_date <= end).group_by(t.c.hs_code, t.c.hs_ver))
    totals = {(code, version): int(total) for code, version, total in rows}
    hidden = {}
    for code, version, article_id in _hidden_tags(store, lambda tag: [tag.c.hs_code, tag.c.hs_ver], PRODUCT_DAILY_SOURCES, start, end, blocked=blocked):
        if code:
            hidden.setdefault((code, version), set()).add(article_id)
            hidden.setdefault((code[:4], version), set()).add(article_id)
    return _subtract(totals, hidden)


def _spikes(counts, target):
    recent_window, baseline_window = windows(target)
    recent, baseline = counts(*recent_window), counts(*baseline_window)
    for key, count in sorted(recent.items()):
        average, ratio = spike(count, baseline.get(key, 0))
        if count > 0 and ratio >= MINIMUM_RATIO:
            yield key, count, average, ratio


def _articles(store, condition, sources, start, end):
    """사건의 근거 기사: 공개 기사 중 최신순. 전체 기사 수와 같을 필요는 없습니다."""
    a, t = store.t("article"), store.t("article_tag")
    stmt = (select(a.c.article_id, a.c.published_date).join(t, t.c.article_id == a.c.article_id)
            .where(condition(t), a.c.visibility_status == "VISIBLE", a.c.source_code.in_(sources),
                   a.c.published_date >= start, a.c.published_date <= end)
            .distinct().order_by(a.c.published_date.desc(), a.c.article_id.desc()).limit(ARTICLE_LIMIT))
    return [int(row[0]) for row in store.c.execute(stmt)]


def _register(store, key, article_id, payload):
    """사건 키를 원자적으로 등록합니다. 새로 등록했으면 사건 ID, 다른 작업이 먼저 등록했으면 None.

    정기 판정과 수동 재실행이 같은 기준일을 함께 판정하면 둘 다 '없음'을 확인한 뒤 저장할 수 있습니다. 고유 제약(uq_event_key)이
    늦은 쪽을 막으므로, 그 저장만 세이브포인트로 되돌리고 판정 작업 전체는 계속합니다. 사건 키 중복이 아닌 오류는 그대로 올립니다."""
    events = store.t("alert_event")
    try:
        with store.c.begin_nested():
            return store.insert("alert_event", event_type="MAJOR_ISSUE", article_id=article_id, event_key=key,
                                payload_json={"schema_version": 1, **payload}, created_at=now())
    except IntegrityError:
        # 잠금 읽기로 다른 작업이 커밋한 최신 행을 본다(MySQL 반복 읽기의 이전 스냅샷이 아니라).
        if store.c.scalar(select(events.c.event_id).where(events.c.event_key == key).with_for_update()) is None:
            raise
        return None


def _save(store, key_parts, article_id, payload):
    """사건을 한 번만 저장하고 전달합니다. (새 사건 여부, 전달 건수). 새로 등록한 작업만 회원 알림을 전달합니다."""
    events = store.t("alert_event")
    key = digest(["MAJOR_ISSUE", *key_parts])
    if store.c.scalar(select(events.c.event_id).where(events.c.event_key == key)) is not None:
        return False, 0  # 저장과 전달은 한 트랜잭션이므로 이미 있는 사건은 전달도 끝났습니다. 나중에 알림을 켠 회원에게 다시 보내지 않습니다.
    identity = _register(store, key, article_id, payload)
    if identity is None:
        log.info("major_issue_already_registered key=%s", key)
        return False, 0
    return True, deliver_event(store, identity)


def _spike_payload(target, label, subject, count, average, ratio, article_ids):
    recent_window, baseline_window = windows(target)
    return {"label": label, "reason": "WEEKLY_NEWS_SPIKE", "target": subject, "recent_week_count": count,
            "baseline_weekly": average, "ratio": ratio, "metric_date": target.isoformat(),
            "window_start": recent_window[0].isoformat(), "window_end": recent_window[1].isoformat(),
            "baseline_start": baseline_window[0].isoformat(), "baseline_end": baseline_window[1].isoformat(),
            "article_ids": article_ids}


def keyword_issues(store, target):
    """키워드 급증 이슈 (사건 키, 대표 기사, 내용). 공개 근거 기사가 없으면 이슈로 보지 않습니다."""
    recent_window = windows(target)[0]
    topics = store.t("keyword_topic")
    for (keyword, _), count, average, ratio in _spikes(lambda start, end: keyword_counts(store, start, end), target):
        ids = _articles(store, lambda t, k=keyword: t.c.keyword_text == k, KEYWORD_DAILY_SOURCES, *recent_window)
        if not ids:
            log.warning("major_issue_no_visible_evidence keyword=%s target=%s", keyword, target)
            continue
        # 동의어는 대표어 차단에도 걸리도록 대표어를 함께 저장합니다(notifications.event_allowed).
        canonical = store.c.scalar(select(topics.c.canonical_text).where(topics.c.keyword_text == keyword))
        subject = {"keyword_text": keyword, **({"kw_topic": canonical} if canonical else {})}
        yield ["KEYWORD", keyword, target.isoformat()], ids[0], _spike_payload(target, keyword, subject, count, average, ratio, ids)


def product_issues(store, target):
    """품목 급증 이슈 (사건 키, 대표 기사, 내용). 공개 근거 기사가 없으면 이슈로 보지 않습니다."""
    recent_window = windows(target)[0]
    names = store.t("hs_code")
    for (code, version), count, average, ratio in _spikes(lambda start, end: product_counts(store, start, end), target):
        # HS4 집계에는 그 아래 HS6 태그 기사가 들어 있다
        ids = _articles(store, lambda t, c=code, v=version: (t.c.hs_code.startswith(c)) & (t.c.hs_ver == v), PRODUCT_DAILY_SOURCES, *recent_window)
        if not ids:
            log.warning("major_issue_no_visible_evidence hs_code=%s target=%s", code, target)
            continue
        label = store.c.scalar(select(names.c.name_ko).where(names.c.hs_code == code, names.c.hs_ver == version)) or code
        yield ["PRODUCT", code, version, target.isoformat()], ids[0], _spike_payload(target, label, {"hs_code": code, "hs_ver": version}, count, average, ratio, ids)


def trade_regulation_issues(store, start, end):
    """start~end에 발행된 통상·규제 기사 이슈 (사건 키, 대표 기사, 내용). 기사마다 한 건입니다."""
    a = store.t("article")
    rows = store.rows(select(a.c.article_id, a.c.title).where(
        a.c.source_code == MARKET, a.c.category.in_(TRADE_REGULATION_CATEGORIES), a.c.visibility_status == "VISIBLE",
        a.c.published_date >= start, a.c.published_date <= end).order_by(a.c.published_date.desc(), a.c.article_id.desc()))
    for row in rows:
        identity = int(row["article_id"])
        yield ["KOTRA_TRADE_REGULATION", identity], identity, {"label": row["title"], "reason": "KOTRA_TRADE_REGULATION", "target": {},
                                                               "metric_date": end.isoformat(), "article_ids": [identity]}


def detect_keywords(store, target):
    for parts, article_id, payload in list(keyword_issues(store, target)):
        yield _save(store, parts, article_id, payload)


def detect_products(store, target):
    for parts, article_id, payload in list(product_issues(store, target)):
        yield _save(store, parts, article_id, payload)


def detect_trade_regulation(store, target):
    for parts, article_id, payload in list(trade_regulation_issues(store, target - timedelta(days=TRADE_LOOKBACK_DAYS - 1), target)):
        yield _save(store, parts, article_id, payload)


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
