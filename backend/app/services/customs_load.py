"""검증된 임시 데이터를 월별 트랜잭션으로 MySQL에 공개한다."""
import calendar
import hashlib
import logging
from contextlib import ExitStack, contextmanager
from collections import defaultdict
from datetime import date
from time import perf_counter

from backend.app.services.customs_stage import ValidationError, RetryableError
from backend.app.db.batch_log import Source, track_batch
from backend.app.services.customs_reference import (CUSTOMS_SOURCE, load_country_references,
    resolve_hs_versions, plan_hs_references, insert_hs_additions)

TABLES = ("export_monthly", "import_monthly", "export_summary", "import_summary")
STAGE_DDL = """CREATE TEMPORARY TABLE customs_stage (
    trade_month DATE NOT NULL,
    country_code VARCHAR(12) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    hs10_code VARCHAR(10) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    hs6_code VARCHAR(6) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    hs_ver VARCHAR(10) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    export_amount DECIMAL(22,2) NOT NULL, export_weight DECIMAL(22,3) NULL,
    import_amount DECIMAL(22,2) NOT NULL, import_weight DECIMAL(22,3) NULL,
    export_changed BOOLEAN NOT NULL DEFAULT TRUE,
    import_changed BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (trade_month,country_code,hs10_code,hs_ver),
    KEY ix_stage_group (trade_month,country_code,hs6_code,hs_ver)
) ENGINE=InnoDB"""
STAGE_INSERT = """INSERT INTO customs_stage
    (trade_month,country_code,hs10_code,hs6_code,hs_ver,
     export_amount,export_weight,import_amount,import_weight)
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)"""
RAW_JOIN = """m.trade_month=s.trade_month AND m.country_code=s.country_code
    AND m.hs10_code=s.hs10_code AND m.hs_ver=s.hs_ver"""
GROUP_JOIN = """m.trade_month=k.trade_month AND m.country_code=k.country_code
    AND m.hs6_code=k.hs6_code AND m.hs_ver=k.hs_ver"""
KEYS = "(SELECT DISTINCT trade_month,country_code,hs6_code,hs_ver FROM customs_stage)"


def safe_error(exc):
    """DB 예외의 본문에는 접속 정보가 포함될 수 있어 종류와 숫자 코드만 반환한다."""
    original = getattr(exc, "orig", exc)
    code = original.args[0] if original.args and isinstance(original.args[0], int) else None
    retryable = isinstance(exc, RetryableError) or (
        isinstance(exc, OSError) and not isinstance(exc, (FileNotFoundError, PermissionError))) or (
        code in {1040, 1042, 1047, 1158, 1159, 1160, 1161, 1205, 1213, 2002, 2003, 2006, 2013})
    return {"error_type": type(exc).__name__, "db_error_code": code, "retryable": retryable,
            "reason": str(exc) if isinstance(exc, ValidationError)
            else "처리 실패; 파일 경로·의존성·DB 설정/서버 로그 확인"}


def cleanup_safely(logger=None, **operations):
    """뒷정리는 각각 시도하되 원래 작업의 오류나 반환값을 덮어쓰지 않는다."""
    logger = logger if logger is not None else logging.getLogger(__name__)
    for name, operation in operations.items():
        try:
            operation()
        except Exception as exc:
            logger.warning("수출입 뒷정리 실패 (%s): %s", name, safe_error(exc))


@contextmanager
def job_logging(path):
    """작업별 표준 로그를 화면과 파일에 남기고 핸들러를 닫는다."""
    logger = logging.getLogger(f"customs.{path.parent.name}.{path.stem}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handlers = [logging.FileHandler(path, encoding="utf-8"), logging.StreamHandler()]
    for handler in handlers:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    try:
        yield logger
    finally:
        for handler in handlers:
            logger.removeHandler(handler)
            handler.close()


class Loader:
    def __init__(self, connection, log, logger=None):
        self.connection = connection
        self.log = log
        self.logger = logger

    def preflight(self, stage, hs_ver, source_code, *, hs_plan=None):
        resolve_hs_versions((), (), hs_ver)
        connection = self.connection
        result = connection.exec_driver_sql("SELECT VERSION(), DATABASE()")
        version, database = result.fetchone()
        if not version.startswith("8.4.") or not database:
            raise ValidationError("승인 스키마는 MySQL 8.4의 명시된 DB에서 실행해야 함")
        result = connection.exec_driver_sql("SELECT @@SESSION.foreign_key_checks, @@SESSION.sql_mode")
        fk, mode = result.fetchone()
        if fk != 1 or not ({"STRICT_TRANS_TABLES", "STRICT_ALL_TABLES"} & set(mode.split(","))):
            raise ValidationError("FK 검사와 strict SQL 모드가 필요함")
        result = connection.exec_driver_sql("""SELECT table_name,engine FROM information_schema.tables
            WHERE table_schema=DATABASE() AND table_name IN
            ('export_monthly','import_monthly','export_summary','import_summary')""")
        engines = dict(result.fetchall())
        if any(engines.get(table) != "InnoDB" for table in TABLES):
            raise ValidationError("원본·집계는 승인 DDL의 InnoDB 테이블이어야 함")
        result = connection.exec_driver_sql("""SELECT table_name,column_name,extra FROM information_schema.columns
            WHERE table_schema=DATABASE() AND table_name IN ('export_monthly','import_monthly')
            AND column_name IN ('hs4_code','hs6_code')""")
        generated = {(t, c) for t, c, extra in result.fetchall() if "STORED GENERATED" in extra}
        if len(generated) != 4:
            raise ValidationError("hs4_code/hs6_code 자동 생성 컬럼이 승인 DDL과 다름")
        self.check_keys(connection)
        result = connection.exec_driver_sql("SELECT option_type,enabled FROM service_option WHERE option_code=%s", (source_code,))
        source = result.fetchone()
        if source is not None and source != ("SOURCE", 1):
            raise ValidationError("기존 source_code는 활성화된 SOURCE 옵션이어야 함")
        # 출처가 없으면 실제 적재 시 공통 track_batch가 등록한다. check 모드는 쓰지 않는다.
        countries, codes = stage.references()
        country_references = load_country_references(connection)
        available_countries = set(country_references)
        if hs_plan is None:
            result = connection.exec_driver_sql("SELECT hs_code,hs_ver FROM hs_code")
            versions, missing_hs, additions = plan_hs_references(stage, result.fetchall(), hs_ver)
        else:
            versions, missing_hs, additions = hs_plan
        missing = {"country": sorted(countries - available_countries),
                   "hs_code": missing_hs, "hs_ver": hs_ver}
        if missing["country"] or missing["hs_code"]:
            self.log("reference_missing", **missing)
            raise ValidationError("국가·HS 판본·상위 코드·보완 품목명 검증 실패; 오류 로그 확인")
        if hs_ver == "auto" and stage.hs_versions and stage.hs_versions != versions:
            raise ValidationError("검증 이후 HS 사전 판본이 변경됨; 다시 검증해야 함")
        stage.hs_versions = versions
        stage.hs_additions = additions
        self.connection.rollback()  # 사전 조회 스냅샷을 끝낸다.
        self.log("reference_validated", hs_ver=hs_ver, source_code=source_code,
                 countries_with_region=sum(country_references[c]["region_id"] is not None for c in countries),
                 countries_with_currency=sum(country_references[c]["currency_code"] is not None for c in countries))

    @staticmethod
    def check_keys(connection):
        """기존 DB도 업무 PK와 복합 FK가 실제로 존재하는지 확인한다."""
        result = connection.exec_driver_sql("""SELECT table_name,constraint_name,column_name,
            referenced_table_name,referenced_column_name FROM information_schema.key_column_usage
            WHERE table_schema=DATABASE() AND table_name IN
            ('export_monthly','import_monthly','export_summary','import_summary')
            ORDER BY table_name,constraint_name,ordinal_position""")
        keys = defaultdict(list)
        for table, name, column, parent, parent_column in result.fetchall():
            keys[table, name].append((column, parent, parent_column))
        for table in TABLES:
            hs_column = "hs10_code" if table.endswith("monthly") else "hs6_code"
            primary = tuple(c[0] for c in keys[table, "PRIMARY"])
            if primary != ("trade_month", "country_code", hs_column, "hs_ver"):
                raise ValidationError(f"{table}: 업무 PK가 승인 DDL과 다름")
            expected = [[("country_code", "country", "country_code")],
                        [(hs_column, "hs_code", "hs_code"), ("hs_ver", "hs_code", "hs_ver")]]
            if table.endswith("monthly"):
                expected.append([("hs6_code", "hs_code", "hs_code"), ("hs_ver", "hs_code", "hs_ver")])
            actual = [columns for (name, _), columns in keys.items() if name == table]
            if any(fk not in actual for fk in expected):
                raise ValidationError(f"{table}: 국가/HS 복합 FK가 승인 DDL과 다름")

    def load(self, stage, hs_ver, source_code="CUSTOMS", batch_size=2000):
        """이 적재 프로그램끼리 DB 단위 잠금으로 동시 실행을 막는다."""
        if not stage.validated or batch_size <= 0:
            raise ValidationError("오류·중복이 없는 CSV 검증과 양수 batch_size가 필요함")
        connection = self.connection
        result = connection.exec_driver_sql("SET time_zone='+09:00'")
        result = connection.exec_driver_sql("SELECT DATABASE()")
        database = result.fetchone()[0]
        lock = "customs_load:" + hashlib.sha256(database.encode()).hexdigest()[:48]
        result = connection.exec_driver_sql("SELECT GET_LOCK(%s,0)", (lock,))
        if result.fetchone()[0] != 1:
            raise RetryableError("다른 수출입 적재 작업이 실행 중임")
        try:
            self.preflight(stage, hs_ver, source_code)
            connection = self.connection
            result = connection.exec_driver_sql(STAGE_DDL)
            result = []
            for month in stage.months():
                result.append(self.publish_month(stage, month, hs_ver, source_code, batch_size))
            return result
        finally:
            connection = self.connection
            cleanup_safely(self.logger,
                load_rollback=connection.rollback,
                load_drop_stage=lambda: connection.exec_driver_sql("DROP TEMPORARY TABLE IF EXISTS customs_stage"),
                load_release_lock=lambda: connection.exec_driver_sql("SELECT RELEASE_LOCK(%s)", (lock,)))

    def publish_month(self, stage, month, hs_ver, source_code, batch_size):
        started = perf_counter()
        # 임시 영역 INSERT도 나눠 커밋한다. 아직 공개 원본을 수정하지 않는다.
        connection = self.connection
        result = connection.exec_driver_sql("DELETE FROM customs_stage")
        self.connection.commit()
        count = 0
        for batch in stage.rows(month, batch_size):
            values = [(m, c, h, h[:6], stage.hs_version(h, hs_ver), ea, ew, ia, iw)
                      for m, c, h, ea, ew, ia, iw in batch]
            connection = self.connection
            connection.exec_driver_sql(STAGE_INSERT, values)
            self.connection.commit()
            count += len(values)
        start = date.fromisoformat(month)
        end = start.replace(day=calendar.monthrange(start.year, start.month)[1])
        with ExitStack() as batches:
            source = Source(source_code, CUSTOMS_SOURCE.label_ko, CUSTOMS_SOURCE.value)
            records = {}
            for table in TABLES:
                job = "CUSTOMS_CSV_LOAD" if table.endswith("monthly") else "HS6_AGGREGATION"
                record = batches.enter_context(track_batch(
                    source, job, table, start, end, engine=self.connection.engine, logger=self.logger))
                record.received_count = count
                records[table] = record
            run_ids = {table: record.run_id for table, record in records.items()}
            self.log("month_started", trade_month=month, rows=count, run_ids=run_ids)
            committing = False
            try:
                self.connection.begin()
                connection = self.connection
                month_codes = {r[0] for r in connection.exec_driver_sql(
                    "SELECT DISTINCT hs10_code FROM customs_stage").fetchall()}
                ensured_hs10 = insert_hs_additions(connection,
                    [stage.hs_additions[c] for c in sorted(month_codes) if c in stage.hs_additions])
                for direction in ("export", "import"):
                    table = f"{direction}_monthly"
                    # 기존 실적을 다른 판본으로 중복 저장하거나 자동 교체하지 않는다.
                    result = connection.exec_driver_sql(f"""SELECT COUNT(*) FROM {table} m JOIN customs_stage s
                        ON m.trade_month=s.trade_month AND m.country_code=s.country_code
                        AND m.hs10_code=s.hs10_code WHERE m.hs_ver<>s.hs_ver""")
                    if result.scalar_one():
                        raise ValidationError(f"{month}: 다른 판본의 동일 관측값이 존재함; 별도 정정 절차 필요")
                    self.merge_raw(connection, direction, month)
                    self.merge_summary(connection, direction)
                    self.verify(connection, direction)
                # 원본 2개와 집계 2개는 함께 커밋한다. 실행 결과 기록은 공통 기록기가 별도 처리한다.
                committing = True
                self.connection.commit()
            except Exception as exc:
                cleanup_safely(self.logger, publish_rollback=self.connection.rollback)
                event = "commit_unknown" if committing else "month_failed"
                self.log(event, trade_month=month, run_ids=run_ids, **safe_error(exc))
                raise
        self.log("month_success", trade_month=month, rows=count, run_ids=run_ids,
                 ensured_hs10=ensured_hs10,
                 elapsed_seconds=round(perf_counter() - started, 3))
        return {"trade_month": month, "rows": count, "run_ids": run_ids, "status": "SUCCESS"}

    @staticmethod
    def merge_raw(connection, direction, month):
        table = f"{direction}_monthly"
        result = connection.exec_driver_sql(f"""UPDATE customs_stage s JOIN {table} m ON {RAW_JOIN}
            SET s.{direction}_changed = NOT ((m.amount_usd <=> s.{direction}_amount)
            AND (m.weight_kg <=> s.{direction}_weight))""")
        # 정정·새 관측값으로 과거/미래 판정 근거가 달라지는 범위를 미계산으로 돌린다.
        result = connection.exec_driver_sql(f"""UPDATE {table} m JOIN customs_stage s
            ON m.country_code=s.country_code AND m.hs10_code=s.hs10_code AND m.hs_ver=s.hs_ver
            AND s.trade_month=%s
            SET m.history_months=NULL,m.baseline_amount_usd=NULL,
                m.median_unit_usd_kg=NULL,m.anomaly_code=NULL
            WHERE s.{direction}_changed=1
                AND m.trade_month BETWEEN DATE_SUB(%s,INTERVAL 1 MONTH)
                    AND DATE_ADD(%s,INTERVAL 12 MONTH)
                AND (m.history_months IS NOT NULL OR m.baseline_amount_usd IS NOT NULL
                    OR m.median_unit_usd_kg IS NOT NULL OR m.anomaly_code IS NOT NULL)""",
                       (month, month, month))
        result = connection.exec_driver_sql(f"""UPDATE {table} m JOIN customs_stage s ON {RAW_JOIN}
            SET m.amount_usd=s.{direction}_amount,m.weight_kg=s.{direction}_weight
            WHERE s.{direction}_changed=1""")
        result = connection.exec_driver_sql(f"""INSERT INTO {table}
            (trade_month,country_code,hs10_code,hs_ver,amount_usd,weight_kg)
            SELECT s.trade_month,s.country_code,s.hs10_code,s.hs_ver,s.{direction}_amount,s.{direction}_weight
            FROM customs_stage s LEFT JOIN {table} m ON {RAW_JOIN}
            WHERE m.hs10_code IS NULL""")

    @staticmethod
    def aggregate(direction):
        # 입력에서 빠진 원본을 삭제하지 않고 영향받은 HS6를 공개 원본 전체로 재계산한다.
        return f"""SELECT m.trade_month,m.country_code,m.hs6_code,m.hs_ver,SUM(m.amount_usd) AS total
            FROM {direction}_monthly m JOIN {KEYS} k ON {GROUP_JOIN}
            GROUP BY m.trade_month,m.country_code,m.hs6_code,m.hs_ver"""

    @classmethod
    def merge_summary(cls, connection, direction):
        table = f"{direction}_summary"
        aggregate = cls.aggregate(direction)
        # 대상 집계를 다시 LEFT JOIN하면 INSERT SELECT가 자기 참조 결과를
        # 별도로 버퍼링한다. 업무 PK로 upsert하여 집계 계산과 저장을 한 번에 한다.
        result = connection.exec_driver_sql(f"""INSERT INTO {table} (trade_month,country_code,hs6_code,hs_ver,amount_usd)
            SELECT a.trade_month,a.country_code,a.hs6_code,a.hs_ver,a.total
            FROM ({aggregate}) a ON DUPLICATE KEY UPDATE amount_usd=a.total""")

    @classmethod
    def verify(cls, connection, direction):
        result = connection.exec_driver_sql(f"""SELECT COUNT(*) FROM customs_stage s LEFT JOIN {direction}_monthly m ON {RAW_JOIN}
            WHERE m.hs10_code IS NULL OR NOT (m.amount_usd <=> s.{direction}_amount)
                OR NOT (m.weight_kg <=> s.{direction}_weight)""")
        if result.fetchone()[0]:
            raise ValidationError(f"{direction}: CSV와 적재 원본 불일치")
        aggregate = cls.aggregate(direction)
        result = connection.exec_driver_sql(f"""SELECT COUNT(*) FROM ({aggregate}) a LEFT JOIN {direction}_summary t
            ON t.trade_month=a.trade_month AND t.country_code=a.country_code
            AND t.hs6_code=a.hs6_code AND t.hs_ver=a.hs_ver
            WHERE t.hs6_code IS NULL OR t.amount_usd<>a.total""")
        if result.fetchone()[0]:
            raise ValidationError(f"{direction}: 원본과 HS6 집계 불일치")
