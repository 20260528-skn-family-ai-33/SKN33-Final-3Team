"""DB 설계서의 방향별 HS10 이력 평균·단가 중앙값·일회성 가능성 계산."""
import calendar
import hashlib
import json
from collections import Counter
from contextlib import ExitStack
from datetime import date
from decimal import Decimal, ROUND_HALF_UP, localcontext
from itertools import groupby
from statistics import median

from sqlalchemy import select

from backend.app.db.batch_log import track_batch
from backend.app.db.crud import CRUD
from backend.app.db.session import session_scope
from backend.app.models.batch import ServiceOption
from backend.app.services.customs_reference import CUSTOMS_SOURCE, load_country_references
from backend.app.services.customs_load import cleanup_safely
from backend.app.services.customs_stage import ValidationError, RetryableError

RULE_CODE = "CUSTOMS_ANOMALY"


def month_number(month):
    return month.year * 12 + month.month - 1


def month_date(number):
    year, month = divmod(number, 12)
    return date(year, month + 1, 1)


def validate_rules(rules):
    """미확정 임계값을 기본 숫자로 추정하지 않는다. 개별 규칙 설정은 가능하다."""
    if not isinstance(rules, dict) or set(rules) - {"r1", "r2", "r3"}:
        raise ValidationError("RULE 설정은 r1/r2/r3 객체여야 함")
    parsed = {}
    try:
        if "r1" in rules:
            if set(rules["r1"]) != {"spike_ratio", "return_pct"}:
                raise ValueError
            n = Decimal(str(rules["r1"]["spike_ratio"]))
            m = Decimal(str(rules["r1"]["return_pct"]))
            if not n.is_finite() or not m.is_finite() or n <= 1 or not 0 <= m <= 100:
                raise ValueError
            parsed["r1"] = (n, m)
        if "r2" in rules:
            if set(rules["r2"]) != {"hs_prefixes"}:
                raise ValueError
            prefixes = rules["r2"]["hs_prefixes"]
            if not isinstance(prefixes, list) or not prefixes or any(
                    not isinstance(p, str) or len(p) not in (2, 4, 6, 10) or not p.isascii()
                    or not p.isdigit() for p in prefixes):
                raise ValueError
            parsed["r2"] = tuple(prefixes)
        if "r3" in rules:
            if set(rules["r3"]) != {"unit_ratio"}:
                raise ValueError
            k = Decimal(str(rules["r3"]["unit_ratio"]))
            if not k.is_finite() or k <= 1:
                raise ValueError
            parsed["r3"] = k
    except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
        raise ValidationError("RULE 임계값 또는 HS 목록 형식 오류") from exc
    return parsed


def calculate_series(rows, hs10, rules, first, last):
    """정렬된 한 국가·HS10·판본만 처리한다. 누락 월을 0으로 만들지 않는다.

    rows: (trade_month, amount_usd, weight_kg). 평균에는 실제 관측된 0금액도 포함.
    R3 중앙값은 중량 양수인 과거 월만 사용하며 반올림 전에 판정한다.
    """
    by_month = {month_number(r[0]): r for r in rows}
    with localcontext() as context:
        context.prec = 60
        for current, amount, weight in rows:
            if not first <= current <= last:
                continue
            number = month_number(current)
            history = [by_month[n] for n in range(number - 12, number) if n in by_month]
            count = len(history)
            baseline = sum((r[1] for r in history), Decimal(0)) / count if count else None
            units = [r[1] / r[2] for r in history if r[2] is not None and r[2] > 0]
            unit_median = median(units) if units else None
            unit = amount / weight if weight is not None and weight > 0 else None
            flags, reasons = [], []
            if not history:
                reasons.append("NO_AMOUNT_HISTORY")
            if not units:
                reasons.append("NO_VALID_UNIT_HISTORY")
            for name in ("r1", "r2", "r3"):
                if name not in rules:
                    reasons.append(name.upper() + "_UNCONFIGURED")
            if "r1" in rules:
                following = by_month.get(number + 1)
                if count < 3:
                    reasons.append("R1_HISTORY_LT_3")
                elif following is None:
                    reasons.append("R1_NEXT_MONTH_MISSING")
                elif baseline is None or baseline <= 0:
                    reasons.append("R1_ZERO_BASELINE")
                else:
                    n, m = rules["r1"]
                    if amount >= baseline * n and abs(following[1] - baseline) <= baseline * m / 100:
                        flags.append("R1")
            if "r2" in rules and hs10.startswith(rules["r2"]):
                if amount > 0:
                    flags.append("R2")
                else:
                    reasons.append("R2_ZERO_AMOUNT")
            if "r3" in rules:
                if count < 3 or len(units) < 3:
                    reasons.append("R3_HISTORY_LT_3")
                elif unit is None:
                    reasons.append("R3_CURRENT_WEIGHT_MISSING_OR_ZERO")
                elif amount == 0:
                    # 금액 0·중량 양수는 별도 원천 검수 대상이며 저단가 이상으로 판정하지 않는다.
                    reasons.append("R3_CURRENT_AMOUNT_ZERO")
                elif unit_median is None or unit_median <= 0:
                    reasons.append("R3_ZERO_MEDIAN")
                elif unit >= unit_median * rules["r3"] or unit <= unit_median / rules["r3"]:
                    flags.append("R3")
            stored_median = unit_median
            if stored_median is not None:
                stored_median = stored_median.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
                if stored_median >= Decimal(10) ** 18:
                    stored_median = None
                    reasons.append("MEDIAN_DECIMAL_RANGE")
            code = "_".join(flags) if flags else ("NONE" if rules else None)
            yield (current, count,
                   baseline.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if baseline is not None else None,
                   stored_median, code, tuple(reasons), amount, weight)


def configure_rules(engine, r1_spike_ratio=None, r1_return_pct=None,
                    r2_hs_prefixes=None, r3_unit_ratio=None):
    """명시적으로 요청한 규칙만 공통 CRUD로 갱신한다."""
    if (r1_spike_ratio is None) != (r1_return_pct is None):
        raise ValidationError("R1 급증 배수와 복귀 허용 범위를 함께 지정해야 함")
    if all(value is None for value in (r1_spike_ratio, r2_hs_prefixes, r3_unit_ratio)):
        return
    with session_scope(engine) as session:
        existing = session.scalar(select(ServiceOption).where(
            ServiceOption.option_code == RULE_CODE).with_for_update())
        if existing and existing.option_type != "RULE":
            raise ValidationError("기존 일회성 옵션이 RULE 종류가 아님")
        rules = dict(existing.value_json) if existing else {}
        if r1_spike_ratio is not None:
            rules["r1"] = {"spike_ratio": r1_spike_ratio, "return_pct": r1_return_pct}
        if r2_hs_prefixes is not None:
            rules["r2"] = {"hs_prefixes": r2_hs_prefixes}
        if r3_unit_ratio is not None:
            rules["r3"] = {"unit_ratio": r3_unit_ratio}
        validate_rules(rules)
        values = {"value_json": rules, "enabled": True}
        store = CRUD(ServiceOption)
        if existing:
            store.update(session, RULE_CODE, **values)
        else:
            store.create(session, option_code=RULE_CODE, option_type="RULE",
                         label_ko="수출입 일회성 가능성", **values)


class MetricCalculator:
    """계산을 임시 표에 끝낸 후 두 방향을 월별 트랜잭션으로 공개한다."""

    def __init__(self, connection, log=lambda *a, **k: None, logger=None):
        self.connection, self.log = connection, log
        self.logger = logger

    def calculate(self, year, batch_size=2000):
        if not 2 <= year <= 9997 or batch_size <= 0:
            raise ValidationError("year는 2~9997, batch_size는 양수여야 함")
        # 새 1월이 들어오면 전년도 12월 R1도 다시 계산한다.
        # 과거 연도 정정은 이미 저장된 다음 12개월의 평균·중앙값에도 영향을 준다.
        first, last = date(year - 1, 12, 1), date(year + 1, 12, 1)
        read_first, read_last = month_date(month_number(first) - 12), date(year + 2, 1, 1)
        connection = self.connection
        result = connection.exec_driver_sql("SELECT DATABASE()")
        lock = "customs_load:" + hashlib.sha256(result.fetchone()[0].encode()).hexdigest()[:48]
        result = connection.exec_driver_sql("SELECT GET_LOCK(%s,0)", (lock,))
        if result.fetchone()[0] != 1:
            raise RetryableError("다른 수출입 적재/계산 작업이 실행 중임")
        try:
            return self._calculate(first, last, read_first, read_last, batch_size)
        finally:
            connection = self.connection
            cleanup_safely(self.logger,
                metrics_rollback=connection.rollback,
                metrics_drop_stage=lambda: connection.exec_driver_sql("DROP TEMPORARY TABLE IF EXISTS customs_metrics_stage"),
                metrics_release_lock=lambda: connection.exec_driver_sql("SELECT RELEASE_LOCK(%s)", (lock,)))

    def _calculate(self, first, last, read_first, read_last, batch_size):
        reasons = Counter()
        connection = self.connection
        result = connection.exec_driver_sql("SET time_zone='+09:00'")
        result = connection.exec_driver_sql("SELECT value_json FROM service_option WHERE option_code=%s AND option_type='RULE' AND enabled=1", (RULE_CODE,))
        row = result.fetchone()
        raw_rules = json.loads(row[0]) if row else {}
        rules = validate_rules(raw_rules)
        result = connection.exec_driver_sql("""CREATE TEMPORARY TABLE customs_metrics_stage (
            direction TINYINT NOT NULL, trade_month DATE NOT NULL,
            country_code VARCHAR(12) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
            hs10_code VARCHAR(10) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
            hs_ver VARCHAR(10) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
            history_months TINYINT NOT NULL, baseline_amount_usd DECIMAL(22,2),
            median_unit_usd_kg DECIMAL(24,6), anomaly_code VARCHAR(12),
            amount_usd DECIMAL(22,2) NOT NULL, weight_kg DECIMAL(22,3),
            PRIMARY KEY(direction,trade_month,country_code,hs10_code,hs_ver)
        ) ENGINE=InnoDB""")
        countries = list(load_country_references(connection))
        self.connection.rollback()
        totals = {}
        for direction, table in enumerate(("export_monthly", "import_monthly")):
            processed = 0
            for country in countries:
                connection = self.connection
                result = connection.exec_driver_sql(f"""SELECT hs10_code,hs_ver,trade_month,amount_usd,weight_kg
                    FROM {table} WHERE country_code=%s AND trade_month BETWEEN %s AND %s
                    ORDER BY hs10_code,hs_ver,trade_month""", (country, read_first, read_last))
                source = result.fetchall()
                self.connection.rollback()
                pending = []
                for (hs10, version), group in groupby(source, key=lambda r: r[:2]):
                    rows = [(r[2], r[3], r[4]) for r in group]
                    for month, count, baseline, unit, code, skipped, amount, weight in calculate_series(rows, hs10, rules, first, last):
                        pending.append((direction, month, country, hs10, version, count, baseline, unit, code, amount, weight))
                        reasons.update(f"{table}:{reason}" for reason in skipped)
                        processed += 1
                        if len(pending) == batch_size:
                            self._stage(pending)
                            pending = []
                if pending:
                    self._stage(pending)
            totals[table] = processed
            self.log("metrics_staged", target_table=table, rows=processed)
        connection = self.connection
        result = connection.exec_driver_sql("SELECT DISTINCT trade_month FROM customs_metrics_stage ORDER BY trade_month")
        months = [r[0] for r in result.fetchall()]
        self.connection.rollback()
        published = [self._publish(month) for month in months]
        return {"rows": totals, "rules": raw_rules, "skipped_reasons": dict(reasons), "months": published}

    def _stage(self, rows):
        connection = self.connection
        connection.exec_driver_sql("INSERT INTO customs_metrics_stage VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", rows)
        self.connection.commit()

    def _publish(self, month):
        connection = self.connection
        tables = ("export_monthly", "import_monthly")
        counts = [connection.exec_driver_sql(
            "SELECT COUNT(*) FROM customs_metrics_stage WHERE direction=%s AND trade_month=%s",
            (direction, month)).scalar_one() for direction in range(2)]
        connection.rollback()
        with ExitStack() as batches:
            records = {}
            for table, count in zip(tables, counts):
                record = batches.enter_context(track_batch(
                    CUSTOMS_SOURCE, "CUSTOMS_METRICS", table, month,
                    month.replace(day=calendar.monthrange(month.year, month.month)[1]),
                    engine=connection.engine, logger=self.logger))
                record.received_count = count
                records[table] = record
            run_ids = {table: record.run_id for table, record in records.items()}
            committing = False
            try:
                connection.begin()
                for direction, table in enumerate(("export_monthly", "import_monthly")):
                    join = "m.trade_month=s.trade_month AND m.country_code=s.country_code AND m.hs10_code=s.hs10_code AND m.hs_ver=s.hs_ver"
                    scope = (direction, month)
                    result = connection.exec_driver_sql(f"""SELECT COUNT(*) FROM customs_metrics_stage s LEFT JOIN {table} m ON {join}
                        WHERE s.direction=%s AND s.trade_month=%s AND (m.hs10_code IS NULL OR
                        NOT (m.amount_usd <=> s.amount_usd) OR NOT (m.weight_kg <=> s.weight_kg))""", scope)
                    if result.fetchone()[0]:
                        raise ValidationError("계산 도중 원본 변경/누락; 기존 월 계산값 유지")
                    result = connection.exec_driver_sql(f"""UPDATE {table} m JOIN customs_metrics_stage s ON {join}
                        SET m.history_months=s.history_months,m.baseline_amount_usd=s.baseline_amount_usd,
                            m.median_unit_usd_kg=s.median_unit_usd_kg,m.anomaly_code=s.anomaly_code
                        WHERE s.direction=%s AND s.trade_month=%s""", scope)
                    result = connection.exec_driver_sql(f"""SELECT COUNT(*) FROM customs_metrics_stage s JOIN {table} m ON {join}
                        WHERE s.direction=%s AND s.trade_month=%s AND (NOT(m.history_months <=> s.history_months)
                        OR NOT(m.baseline_amount_usd <=> s.baseline_amount_usd)
                        OR NOT(m.median_unit_usd_kg <=> s.median_unit_usd_kg) OR NOT(m.anomaly_code <=> s.anomaly_code))""", scope)
                    if result.fetchone()[0]:
                        raise ValidationError("계산 결과 공개 검증 실패")
                committing = True
                connection.commit()
            except Exception:
                cleanup_safely(self.logger, metrics_publish_rollback=connection.rollback)
                if committing:
                    self.log("metrics_commit_unknown", trade_month=str(month), run_ids=run_ids)
                raise
        self.log("metrics_month_success", trade_month=str(month), run_ids=run_ids)
        return {"trade_month": str(month), "run_ids": run_ids}
