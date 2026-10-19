"""service_option 단위/흐름 테스트 및 명시적으로 분리한 MySQL 통합 테스트.

실행: python -m unittest backend.tests.test_service_option_load -v
SQLite 흐름 테스트는 스키마 검사를 대체하며 MySQL 검증 완료를 의미하지 않는다.
통합 테스트: SERVICE_OPTION_TEST_DATABASE=service_option_test_<새이름> 과
SERVICE_OPTION_TEST_HOST/PORT/USER/PASSWORD 설정. 기존 DB는 재사용하지 않는다.
테스트가 새로 만든 전용 DB는 결과 확인용으로 남긴다 (자동 DROP 없음).
"""
import io
import json
import os
import re
import tempfile
import unittest
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date
from pathlib import Path
from threading import Barrier
from unittest import mock

from sqlalchemy import URL, create_engine, event, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from backend.app.db.batch_log import track_batch, Source
from backend.app.db.crud import CRUD
from backend.app.models.batch import ServiceOption
from backend.app.scheduler.jobs import service_option_load as cli
from backend.app.services import service_option_load as loader


def source(code="TEST_SOURCE", **changes):
    row = dict(option_code=code, option_type="SOURCE", label_ko="테스트 출처",
               value_json={"url": "https://example.org/source"}, sort_order=10, enabled=True)
    row.update(changes)
    return row


def source_job(name="TRADE_COLLECTION", *, schedule=None, scope=None, targets=None):
    return {"job_name": name, "schedule": schedule or {"type": "DAILY", "time": "03:00", "timezone": "Asia/Seoul"},
            "collects": ["검증용 수집 정보"], "collection_scope": scope or
            {"mode": "RECENT_MONTHS", "anchor": "LATEST_AVAILABLE_MONTH", "months": 3},
            "target_tables": targets or ["export_monthly", "import_monthly"]}


def configured_source(code="CUSTOMS", jobs=None):
    return source(code, value_json={"url": "https://example.org/api", "license": "테스트 계약",
        "daily_limit": 10000, "data_period": "MONTHLY", "source_update_frequency": None,
        "jobs": [source_job()] if jobs is None else jobs})


def terms(code="TERMS_20261019", effective="2026-10-19 09:00:00", enabled=True):
    return dict(option_code=code, option_type="NOTIFICATION_TERMS", label_ko="알림 약관",
                value_json={"body": "실제 테스트 본문", "effective_at": effective}, sort_order=10, enabled=enabled)


def weekly():
    return dict(option_code="WEEKLY", option_type="REPORT_PERIOD", label_ko="주간", sort_order=10,
                enabled=True, value_json={"trigger": "WEEKLY_SCHEDULE", "weekday": 1, "time": "09:00",
                "timezone": "Asia/Seoul", "news_only": True, "report_type_code": "RPT_WEEKLY"})


class FilesTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.seed_path = self.folder / "seed.json"

    def seed(self, rows, **changes):
        obj = dict(schema_version=1, seed_version="test.1", records=deepcopy(rows),
                   provenance={r["option_code"]: {"basis": "test fixture"} for r in rows})
        obj.update(changes)
        self.seed_path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
        return self.seed_path

    def plan_file(self, report, apply=()):
        plan = json.loads((Path(report["output_dir"]) / "plan.json").read_text(encoding="utf-8"))
        for row in plan["records"]:
            row["apply"] = row["option_code"] in apply
        path = self.folder / "approved.json"
        path.write_text(json.dumps(plan), encoding="utf-8")
        return path


class ManifestTest(FilesTest):
    def test_base_seed_and_existing_consumers(self):
        seed = loader.read_manifest()
        rows = {r["option_code"]: r for r in seed.records}
        self.assertEqual(set(rows), loader.BASE_CODES)
        self.assertEqual(Counter(r["option_type"] for r in seed.records),
                         dict(SOURCE=3, REPORT_PERIOD=3, REPORT_TYPE=4, RULE=2))
        for code in ("WEEKLY", "MONTHLY", "QUARTERLY"):
            self.assertFalse(rows[code]["enabled"])
            self.assertEqual(rows[code]["value_json"], {})
        from backend.app.services.customs_metrics import validate_rules
        rules = rows["CUSTOMS_ANOMALY"]["value_json"]
        self.assertEqual(rules, {"r1": {"spike_ratio": 3, "return_pct": 20},
                                 "r2": {"hs_prefixes": ["88", "89"]}, "r3": {"unit_ratio": 5}})
        parsed = validate_rules(rules)
        self.assertEqual(parsed["r2"], ("88", "89"))
        self.assertEqual(parsed["r1"], (3, 20))
        self.assertEqual(rows["INTEREST_SCOPE"]["value_json"],
                         dict(include_linked_countries=True, top_country_count=5, lookback_months=12))
        from backend.app.services.customs_reference import CUSTOMS_SOURCE
        from backend.app.scheduler.jobs.hs_code_load import SOURCE as HS_SOURCE
        from backend.app.scheduler.jobs.hs_correlation_load import SOURCE as CORR_SOURCE
        for consumer in (CUSTOMS_SOURCE, HS_SOURCE, CORR_SOURCE):
            value = rows[consumer.code]["value_json"]
            self.assertEqual({k: value[k] for k in consumer.value}, consumer.value)
            self.assertEqual(value["jobs"], [])
            self.assertIsNone(value["daily_limit"])
            self.assertIsNone(value["license"])

    def test_validate_never_creates_engine_and_reports_db_not_checked(self):
        with mock.patch.object(loader, "make_engine", side_effect=AssertionError("DB 연결 금지")):
            result = loader.run(output=self.folder)
        self.assertEqual(result["status"], "VALIDATED")
        self.assertEqual(result["input_count"], 12)
        self.assertEqual(result["verification"]["database"], "NOT_CHECKED")
        self.assertFalse(result["recalculation_required"])
        self.assertEqual(result["readiness"]["report_schedule"], "NOT_CONFIGURED")
        self.assertEqual(result["readiness"]["notification_terms"], "NOT_CONFIGURED")

    def test_invalid_inputs_are_rejected_before_db(self):
        good = source()
        mutations = [dict(sort_order=True), dict(sort_order=1.0), dict(sort_order="1"),
                     dict(sort_order=-1), dict(sort_order=65536), dict(enabled=1), dict(enabled="true"),
                     dict(option_code="customs"), dict(option_code="A" * 41), dict(option_type="UNKNOWN"),
                     dict(label_ko=" "), dict(label_ko=" 출처"), dict(label_ko="a" * 121),
                     dict(value_json=[]), dict(value_json=None), dict(value_json="{}"), dict(value_json={}),
                     dict(extra="unknown"), dict(value_json={"url": "javascript:alert(1)"}),
                     dict(value_json={"url": "https://user:secret@example.org"}),
                     dict(value_json={"url": "https://example.org?serviceKey=secret"}),
                     dict(value_json={"url": "https://example.org?access_token=secret"}),
                     dict(value_json={"url": "https://example.org", "api_key": "secret"})]
        for change in mutations:
            with self.subTest(change=list(change)):
                path = self.seed([{**good, **change}])
                with mock.patch.object(loader, "make_engine", side_effect=AssertionError("DB 연결 금지")):
                    report = loader.run(mode="load", seed_path=path, output=self.folder)
                self.assertEqual(report["exit_code"], 1)
                self.assertEqual(report["commit_state"], "NOT_ATTEMPTED")
        self.seed([good])
        data = json.loads(self.seed_path.read_text(encoding="utf-8"))
        for change in (dict(schema_version=True), dict(schema_version=2), dict(records=[]),
                       dict(records=[good] * 1001), dict(provenance={}),
                       dict(records=[{k: v for k, v in good.items() if k != "enabled"}])):
            with self.subTest(top_level=list(change)):
                self.seed_path.write_text(json.dumps({**data, **change}), encoding="utf-8")
                with self.assertRaises(loader.ContractError):
                    loader.read_manifest(self.seed_path)

    def test_strict_parser_bom_duplicate_codes_and_size(self):
        path = self.seed([source()])
        path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
        self.assertEqual(len(loader.read_manifest(path).records), 1)
        self.seed([source(), source()])
        with self.assertRaisesRegex(loader.ContractError, "중복"):
            loader.read_manifest(path)
        for raw in ('{"schema_version":1,"schema_version":1}', '{"x":{"key":1,"key":2}}',
                    '{"x":NaN}', '{"x":Infinity}', '{"x":-Infinity}'):
            path.write_text(raw, encoding="utf-8")
            with self.assertRaises(loader.ContractError):
                loader.read_json(path)
        path = self.seed([source()])
        path.write_text(path.read_text(encoding="utf-8").replace('"sort_order": 10', '"sort_order": 1e999'), encoding="utf-8")
        with self.assertRaises(loader.ContractError):
            loader.read_manifest(path)
        path.write_bytes(b" " * (5 * 1024 * 1024 + 1))
        with self.assertRaisesRegex(loader.ContractError, "크기"):
            loader.read_json(path)

    def test_rule_boundaries_and_prefixes(self):
        row = next(r for r in loader.read_manifest().records if r["option_code"] == "CUSTOMS_ANOMALY")
        values = [{}, {"r1": {"spike_ratio": 1, "return_pct": 20}},
                  {"r1": {"spike_ratio": True, "return_pct": 20}},
                  {"r1": {"spike_ratio": "3", "return_pct": 20}},
                  {"r1": {"spike_ratio": 3, "return_pct": -1}},
                  {"r1": {"spike_ratio": 3, "return_pct": 101}},
                  {"r1": {"spike_ratio": 3}}, {"r3": {"unit_ratio": 1}},
                  {"r3": {"unit_ratio": float("inf")}}, {"other": {}},
                  {"r2": {"hs_prefixes": []}}, {"r2": {"hs_prefixes": [88]}},
                  {"r2": {"hs_prefixes": ["８８"]}}, {"r2": {"hs_prefixes": ["001"]}},
                  {"r2": {"hs_prefixes": ["88", "88"]}}]
        for value in values:
            with self.subTest(value=value), self.assertRaises(loader.ContractError):
                loader.validate_record({**row, "value_json": value})
        for pct in (0, 100):
            loader.validate_record({**row, "value_json": {"r1": {"spike_ratio": 1.01, "return_pct": pct}}})
        loader.validate_record({**row, "value_json": {"r2": {"hs_prefixes": ["00", "0101", "010121", "0101210000"]}}})
        with self.assertRaisesRegex(loader.ContractError, "등록되지"):
            loader.validate_record({**row, "option_code": "UNKNOWN_RULE"})

    def test_interest_scope_strict_types(self):
        row = next(r for r in loader.read_manifest().records if r["option_code"] == "INTEREST_SCOPE")
        for change in (dict(top_country_count=True), dict(lookback_months="12"),
                       dict(top_country_count=0), dict(include_linked_countries=1), dict(extra=1)):
            with self.subTest(change=change), self.assertRaises(loader.ContractError):
                loader.validate_record({**row, "value_json": {**row["value_json"], **change}})

    def test_terms_schedule_and_relations(self):
        for row in (terms(effective="2026-02-30 00:00:00"), terms(effective="2026-10-19T09:00:00"),
                    {**terms(), "value_json": {"body": " ", "effective_at": "2026-10-19 09:00:00"}}):
            with self.assertRaises(loader.ContractError):
                loader.validate_record(row)
        loader.read_manifest(self.seed([terms(effective="2099-01-01 00:00:00")]))
        with self.assertRaisesRegex(loader.ContractError, "동일 적용"):
            loader.read_manifest(self.seed([terms(), terms("TERMS_OTHER")]))
        loader.read_manifest(self.seed([terms(), terms("TERMS_DISABLED", enabled=False)]))
        period = weekly()
        loader.read_manifest(self.seed([period]))  # DB 부모 참조는 check에서 확인
        for change in (dict(weekday=True), dict(weekday=8), dict(time="24:00"), dict(news_only=1),
                       dict(timezone="UTC"), dict(trigger="CUSTOM_TRIGGER")):
            with self.subTest(change=change), self.assertRaises(loader.ContractError):
                loader.validate_record({**period, "value_json": {**period["value_json"], **change}})
        parent = dict(option_code="RPT_WEEKLY", option_type="REPORT_TYPE", label_ko="리포트",
                      value_json={}, sort_order=10, enabled=False)
        with self.assertRaisesRegex(loader.ContractError, "비활성"):
            loader.read_manifest(self.seed([period, parent]))
        with self.assertRaises(loader.ContractError):
            loader.validate_record({**period, "option_code": "QUARTERLY"})

    def test_un_metadata_requires_explicit_period(self):
        value = dict(url="https://comtradeapi.un.org", license="조건 확인 근거는 provenance에 별도 기록",
                     data_period="MONTHLY_AND_ANNUAL", source_update_frequency=None,
                     jobs=[dict(job_name="UN_FILE_IMPORT", schedule=dict(type="MANUAL", timezone="Asia/Seoul"),
                                collects=["전 세계 수입액", "한국산 수입액"],
                                collection_scope=dict(mode="EXPLICIT_PERIOD", start="2024-01-01", end="2025-12-31"),
                                target_tables=["un_data"])])
        row = source("UN_COMTRADE_SOURCE")
        row["value_json"] = value
        loader.validate_record(row)
        value["jobs"][0]["collection_scope"]["start"] = "2026-01-01"
        with self.assertRaises(loader.ContractError):
            loader.validate_record(row)

    def test_semantic_comparison_hashes(self):
        self.assertTrue(loader.same({"a": 3, "b": [0, "01"]}, {"b": [0.0, "01"], "a": 3.0}))
        self.assertEqual(loader.state_hash({"a": 3}), loader.state_hash({"a": 3.0}))
        self.assertFalse(loader.same(True, 1))
        self.assertFalse(loader.same(["88", "89"], ["89", "88"]))
        self.assertFalse(loader.same(123456789012345678901234567890, 123456789012345678901234567891))

    def test_output_failure_prevents_connection_and_safe_errors_hide_secrets(self):
        blocked = self.folder / "blocked"
        blocked.write_text("a file", encoding="utf-8")
        with mock.patch.object(loader, "make_engine", side_effect=AssertionError("DB 연결 금지")):
            report = loader.run(mode="load", output=blocked)
        self.assertEqual(report["exit_code"], 2)
        self.assertEqual(report["commit_state"], "NOT_ATTEMPTED")
        exc = OperationalError("password=TOP_SECRET", {}, Exception(1045, "TOP_SECRET"))
        self.assertNotIn("TOP_SECRET", json.dumps(loader.safe_error(exc, "connect")))

    def test_cli_defaults_and_load_only_plan(self):
        with mock.patch.object(cli, "run", return_value={"exit_code": 0}) as run, mock.patch("sys.stdout", io.StringIO()):
            self.assertEqual(cli.main([]), 0)
            self.assertEqual(run.call_args.kwargs["mode"], "validate")
        with mock.patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
            cli.main(["--mode", "check", "--apply-plan", "plan.json"])


class SourceContractTest(FilesTest):
    def test_existing_customs_jobs_register_with_real_processing_tables(self):
        from backend.app.services.customs_load import TABLES
        raw = sorted(t for t in TABLES if t.endswith("monthly"))
        summary = sorted(t for t in TABLES if t.endswith("summary"))
        jobs = [source_job("CUSTOMS_CSV_LOAD", targets=raw),
                source_job("HS6_AGGREGATION", targets=summary,
                           schedule={"type": "AFTER_JOB", "after_job_name": "CUSTOMS_CSV_LOAD"}),
                source_job("CUSTOMS_METRICS", targets=raw,
                           schedule={"type": "AFTER_JOB", "after_job_name": "CUSTOMS_CSV_LOAD"})]
        report = loader.run(seed_path=self.seed([configured_source(jobs=jobs)]), output=self.folder)
        self.assertEqual(report["exit_code"], 0, report)
        self.assertEqual([j["job_name"] for j in report["readiness"]["sources"]["CUSTOMS"]["jobs"]],
                         ["CUSTOMS_CSV_LOAD", "HS6_AGGREGATION", "CUSTOMS_METRICS"])
        with self.assertRaises(loader.ContractError) as error:
            loader.validate_record(configured_source(jobs=[source_job("CUSTOMS_LOAD", targets=list(TABLES))]))
        self.assertEqual(error.exception.code, "UNKNOWN_JOB")

    def test_every_job_requires_all_mandatory_targets(self):
        for name, contract in loader.JOB_TARGETS.items():
            with self.subTest(job=name):
                loader.validate_record(configured_source(jobs=[source_job(name, targets=sorted(contract.required))]))
                loader.validate_record(configured_source(jobs=[source_job(name, targets=sorted(contract.required | contract.optional))]))
            if len(contract.required) > 1:
                for missing in contract.required:
                    with self.subTest(job=name, missing=missing), self.assertRaises(loader.ContractError) as error:
                        loader.validate_record(configured_source(jobs=[source_job(name, targets=sorted(contract.required - {missing}))]))
                    self.assertEqual(error.exception.code, "JOB_TARGET_MISMATCH")
        for name, targets in (("NEWS_COLLECTION", ["article_tag"]), ("HS_CODE_REFRESH", ["hs_code_correlation"])):
            with self.subTest(job=name), self.assertRaises(loader.ContractError) as error:
                loader.validate_record(configured_source(jobs=[source_job(name, targets=targets)]))
            self.assertEqual(error.exception.code, "JOB_TARGET_MISMATCH")

    def test_active_empty_period_is_rejected_before_db_and_disabled_is_allowed(self):
        for code in ("WEEKLY", "MONTHLY", "QUARTERLY"):
            row = dict(option_code=code, option_type="REPORT_PERIOD", label_ko="발행 주기",
                       value_json={}, enabled=True, sort_order=10)
            with self.subTest(period=code):
                path = self.seed([row])
                with mock.patch.object(loader, "make_engine", side_effect=AssertionError("DB 연결 금지")):
                    report = loader.run(mode="load", seed_path=path, output=self.folder)
                self.assertEqual(report["errors"][0]["code"], "ACTIVE_PERIOD_NOT_CONFIGURED")
                self.assertEqual(report["exit_code"], 1)
                loader.validate_record({**row, "enabled": False})
        # REPORT_TYPE은 활성 단순 선택지이므로 빈 객체를 계속 허용한다.
        loader.validate_record(dict(option_code="RPT_WEEKLY", option_type="REPORT_TYPE", label_ko="주간 리포트",
                                    value_json={}, enabled=True, sort_order=10))

    def test_weekly_activation_requires_each_field(self):
        row = weekly()
        loader.validate_record(row)
        for field in row["value_json"]:
            value = {k: v for k, v in row["value_json"].items() if k != field}
            with self.subTest(missing=field), self.assertRaises(loader.ContractError):
                loader.validate_record({**row, "value_json": value})

    def test_all_guide_schedules_and_scopes(self):
        valid = [
            source_job(),
            source_job(schedule={"type": "INTERVAL", "every_minutes": 60},
                       scope={"mode": "RECENT_DAYS", "days": 3, "timezone": "Asia/Seoul"}),
            source_job(schedule={"type": "MANUAL"}, scope={"mode": "EXPLICIT_PERIOD", "start": "2026-01-01", "end": "2026-10-19"}),
            source_job(schedule={"type": "MANUAL", "timezone": "Asia/Seoul"},
                       scope={"mode": "EXPLICIT_PERIOD", "start": "2026-10-19", "end": "2026-10-19"}),
            source_job(schedule={"type": "DAILY", "time": "00:00", "timezone": "UTC"},
                       scope={"mode": "RECENT_DAYS", "days": 1}),
        ]
        for job in valid:
            with self.subTest(schedule=job["schedule"]):
                loader.validate_record(configured_source(jobs=[job]))
        news = source_job("NEWS_COLLECTION", targets=["article"], schedule={"type": "INTERVAL", "every_minutes": 60},
                          scope={"mode": "RECENT_DAYS", "days": 3, "timezone": "Asia/Seoul"})
        tagging = source_job("ARTICLE_TAGGING", targets=["article_tag"],
                             schedule={"type": "AFTER_JOB", "after_job_name": "NEWS_COLLECTION"})
        aggregate = source_job("NEWS_DAILY_AGGREGATION", targets=["news_product_daily", "news_keyword_daily"],
                               schedule={"type": "AFTER_JOB", "after_job_name": "ARTICLE_TAGGING"})
        row = configured_source("KOTRA_MARKET", jobs=[aggregate, tagging, news])  # 선행 작업이 뒤에 있어도 허용
        row["value_json"].update(data_period="ARTICLE", daily_limit=None)
        report = loader.run(seed_path=self.seed([row]), output=self.folder)
        self.assertEqual(report["exit_code"], 0, report)
        info = report["readiness"]["sources"]["KOTRA_MARKET"]
        self.assertEqual(info["configuration"], "REGISTERED")
        self.assertEqual(len(info["jobs"]), 3)
        self.assertFalse(info["execution_performed"])
        self.assertEqual(info["consumer_integration"], "NOT_CHECKED")

    def test_source_metadata_types_and_unconfirmed_values(self):
        good = configured_source()
        for change in (dict(daily_limit=True), dict(daily_limit=0), dict(daily_limit=-1), dict(daily_limit=3.0),
                       dict(daily_limit="10000"), dict(license=[]), dict(source_update_frequency=7),
                       dict(data_period="HOURLY"), dict(data_period=[]), dict(jobs=None), dict(jobs={}),
                       dict(jobs="TRADE_COLLECTION"), dict(unexpected="typo")):
            with self.subTest(change=change), self.assertRaises(loader.ContractError):
                loader.validate_record({**good, "value_json": {**good["value_json"], **change}})
        for key in ("data_period", "source_update_frequency"):
            value = deepcopy(good["value_json"])
            del value[key]
            with self.subTest(missing=key), self.assertRaises(loader.ContractError):
                loader.validate_record({**good, "value_json": value})
        row = configured_source(jobs=[])
        row["value_json"].update(license=None, daily_limit=None, data_period=None)
        loader.validate_record(row)
        info = loader.source_readiness(row)
        self.assertEqual(info["configuration"], "NOT_CONFIGURED")
        self.assertIn("daily_limit", info["unconfirmed_metadata"])
        self.assertEqual(loader.source_readiness(source())["configuration"], "NOT_CONFIGURED")

    def test_invalid_schedule_fields_types_and_timezones(self):
        schedules = [{}, None, [], {"type": "MONTHLY"}, {"type": "DAILY", "time": "24:00", "timezone": "Asia/Seoul"},
                     {"type": "DAILY", "time": "9:00", "timezone": "Asia/Seoul"},
                     {"type": "DAILY", "time": "09:00", "timezone": "INVALID/ZONE"},
                     {"type": "DAILY", "time": "09:00", "timezone": "Asia/Seoul", "every_minutes": 1},
                     {"type": "DAILY", "time": "09:00"}, {"type": "INTERVAL", "every_minutes": True},
                     {"type": "INTERVAL", "every_minutes": 0}, {"type": "INTERVAL", "every_minutes": "60"},
                     {"type": "INTERVAL", "every_minutes": 60, "time": "09:00"},
                     {"type": "MANUAL", "time": "09:00"}, {"type": "AFTER_JOB"},
                     {"type": "AFTER_JOB", "after_job_name": []}]
        for schedule in schedules:
            with self.subTest(schedule=schedule), self.assertRaises(loader.ContractError):
                loader.validate_schedule(schedule)

    def test_invalid_collection_scope_fields_and_dates(self):
        scopes = [{}, [], {"mode": "UNKNOWN"}, {"mode": "RECENT_MONTHS", "anchor": "TODAY", "months": 3},
                  {"mode": "RECENT_MONTHS", "anchor": "LATEST_AVAILABLE_MONTH", "months": True},
                  {"mode": "RECENT_MONTHS", "anchor": "LATEST_AVAILABLE_MONTH", "months": 0},
                  {"mode": "RECENT_DAYS", "days": -1, "timezone": "Asia/Seoul"},
                  {"mode": "RECENT_DAYS", "days": 1},
                  {"mode": "EXPLICIT_PERIOD", "start": "2026-02-30", "end": "2026-03-01"},
                  {"mode": "EXPLICIT_PERIOD", "start": "2026-03-02", "end": "2026-03-01"},
                  {"mode": "EXPLICIT_PERIOD", "start": "2026-3-01", "end": "2026-03-02"},
                  {"mode": "EXPLICIT_PERIOD", "start": "2026-03-01", "end": "2026-03-02", "months": 3}]
        for scope in scopes:
            with self.subTest(scope=scope), self.assertRaises(loader.ContractError):
                loader.validate_scope(scope, {"type": "INTERVAL", "every_minutes": 60})

    def test_job_target_contract_and_required_payloads(self):
        good = source_job()
        changes = [dict(job_name="UNKNOWN_JOB"), dict(job_name="a"), dict(job_name="A" * 61),
                   dict(collects=[]), dict(collects=[1]), dict(collects=["내용", "내용"]),
                   dict(target_tables=[]), dict(target_tables=["article"]), dict(target_tables=["users"]),
                   dict(target_tables=["export_monthly; DROP TABLE users"]),
                   dict(target_tables=["export_monthly", "export_monthly"]), dict(extra="typo")]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(loader.ContractError):
                loader.validate_record(configured_source(jobs=[{**good, **change}]))
        for key in good:
            job = {k: v for k, v in good.items() if k != key}
            with self.subTest(missing=key), self.assertRaises(loader.ContractError):
                loader.validate_record(configured_source(jobs=[job]))

    def test_duplicate_missing_cross_source_and_cyclic_dependencies(self):
        first = source_job("NEWS_COLLECTION", targets=["article"])
        second = source_job("ARTICLE_TAGGING", targets=["article_tag"],
                            schedule={"type": "AFTER_JOB", "after_job_name": "NEWS_COLLECTION"})
        cases = [([first, first], "DUPLICATE_JOB_NAME"), ([second], "MISSING_PREDECESSOR")]
        self_ref = deepcopy(first)
        self_ref["schedule"] = {"type": "AFTER_JOB", "after_job_name": "NEWS_COLLECTION"}
        cases.append(([self_ref], "CYCLIC_JOBS"))
        cycle = deepcopy(first)
        cycle["schedule"] = {"type": "AFTER_JOB", "after_job_name": "ARTICLE_TAGGING"}
        cases.append(([cycle, second], "CYCLIC_JOBS"))
        for jobs, expected in cases:
            with self.subTest(expected=expected), self.assertRaises(loader.ContractError) as error:
                loader.validate_record(configured_source("KOTRA_MARKET", jobs))
            self.assertEqual(error.exception.code, expected)
        with self.assertRaises(loader.ContractError):
            loader.read_manifest(self.seed([configured_source("SOURCE_A", [first]), configured_source("SOURCE_B", [second])]))

    def test_all_guide_rule_and_report_contracts(self):
        trade = dict(size_min_usd=1000000, growth_min_pct=150, growth_delta_usd=1000000,
                     drop_max_pct=-60, drop_delta_usd=-1000000, large_growth_min_pct=200)
        news = dict(interest_ratio=2.5, major_ratio=3, zero_denominator=1)
        for code, value in (("TRADE_CHANGE_RULE", trade), ("NEWS_SPIKE_RULE", news)):
            row = dict(option_code=code, option_type="RULE", value_json=value,
                       label_ko="문서 규칙", sort_order=1, enabled=False)
            loader.validate_record(row)
            for key in value:
                for invalid in (True, "1", None, float("inf")):
                    with self.subTest(code=code, key=key, invalid=invalid), self.assertRaises(loader.ContractError):
                        loader.validate_record({**row, "value_json": {**value, key: invalid}})
        for change in (dict(drop_max_pct=1), dict(drop_max_pct=-101), dict(drop_delta_usd=1),
                       dict(large_growth_min_pct=100), dict(growth_delta_usd=-1)):
            with self.subTest(change=change), self.assertRaises(loader.ContractError):
                loader.validate_record(dict(option_code="TRADE_CHANGE_RULE", option_type="RULE", label_ko="규칙",
                    sort_order=0, enabled=False, value_json={**trade, **change}))
        with self.assertRaises(loader.ContractError):
            loader.validate_record(dict(option_code="NEWS_SPIKE_RULE", option_type="RULE", label_ko="규칙", sort_order=0,
                                        enabled=False, value_json={**news, "zero_denominator": 0}))
        for code, trigger in (("MONTHLY", "VALIDATED_MONTH_STATS"), ("QUARTERLY", "VALIDATED_QUARTER_STATS")):
            row = dict(option_code=code, option_type="REPORT_PERIOD", label_ko="통계 리포트", sort_order=1,
                       enabled=False, value_json=dict(trigger=trigger, news_only=False, report_type_code="RPT_" + code))
            loader.validate_record(row)
            with self.assertRaises(loader.ContractError):
                loader.validate_record({**row, "value_json": {**row["value_json"], "news_only": True}})

    def test_un_metadata_uses_same_contract_without_running_un_jobs(self):
        imported = source_job("UN_FILE_IMPORT", targets=["un_data"], schedule={"type": "MANUAL", "timezone": "Asia/Seoul"},
            scope={"mode": "EXPLICIT_PERIOD", "start": "2024-01-01", "end": "2025-12-31"})
        refresh = source_job("UN_DATA_REFRESH", targets=["un_data"],
                             schedule={"type": "AFTER_JOB", "after_job_name": "UN_FILE_IMPORT"})
        row = configured_source("UN_COMTRADE_SOURCE", jobs=[imported, refresh])
        row["value_json"]["data_period"] = "MONTHLY_AND_ANNUAL"
        report = loader.run(seed_path=self.seed([row]), output=self.folder)
        self.assertEqual(report["exit_code"], 0, report)
        self.assertFalse(report["source_jobs_executed"])
        self.assertFalse(report["batch_run_written"])
        self.assertEqual(len(report["readiness"]["sources"]["UN_COMTRADE_SOURCE"]["jobs"]), 2)


class FlowTest(FilesTest):
    """테스트 DB 쓰기·실패 분기는 SQLite로 검증. 실제 스키마 검사는 아래 MySQL 테스트 전용."""

    def setUp(self):
        super().setUp()
        self.engine = create_engine("sqlite://")
        self.addCleanup(self.engine.dispose)
        ServiceOption.__table__.create(self.engine)
        self.schema = {"tables": {"service_option"}, "references": []}
        patcher = mock.patch.object(loader, "inspect_schema", return_value=self.schema)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_loader(self, **kwargs):
        return loader.run(engine=self.engine, output=self.folder, **kwargs)

    def insert(self, *rows):
        with Session(self.engine) as session:
            for row in rows:
                CRUD(ServiceOption).create(session, **deepcopy(row))
            session.commit()

    def rows(self):
        with self.engine.connect() as conn:
            return {r["option_code"]: dict(r) for r in conn.execute(select(ServiceOption.__table__)).mappings()}

    def test_empty_db_reexecution_and_metadata_only_changes(self):
        first = self.run_loader(mode="load")
        self.assertEqual(first["status"], "LOADED")
        self.assertEqual(first["inserted_count"], 12)
        self.assertTrue(first["recalculation_required"])
        self.assertEqual(first["verification"]["public"], "PASSED")
        before = self.rows()
        second = self.run_loader(mode="load")
        self.assertEqual((second["inserted_count"], second["updated_count"], second["unchanged_count"]), (0, 0, 12))
        self.assertFalse(second["recalculation_required"])
        data = json.loads(loader.DEFAULT_SEED.read_text(encoding="utf-8"))
        data["seed_version"] = "a-new-version"
        data["provenance"]["CUSTOMS"]["basis"] = "changed evidence"
        for row in data["records"]:
            if row["option_code"] == "CUSTOMS_ANOMALY":
                row["value_json"] = {"r3": {"unit_ratio": 5.0}, "r2": {"hs_prefixes": ["88", "89"]},
                                     "r1": {"return_pct": 20.0, "spike_ratio": 3.0}}
        self.seed_path.write_text(json.dumps(data), encoding="utf-8")
        third = self.run_loader(mode="load", seed_path=self.seed_path)
        self.assertEqual((third["inserted_count"], third["updated_count"]), (0, 0))
        self.assertEqual(before, self.rows())

    def test_preserves_custom_values_disabled_and_out_of_scope(self):
        initial = loader.read_manifest().records
        customs = deepcopy(next(r for r in initial if r["option_code"] == "CUSTOMS"))
        customs.update(value_json={}, enabled=False)
        rule = deepcopy(next(r for r in initial if r["option_code"] == "CUSTOMS_ANOMALY"))
        rule.update(value_json={"r2": {"hs_prefixes": ["01"]}}, enabled=False)
        extra = source("CUSTOM_SOURCE")
        self.insert(customs, rule, extra)
        result = self.run_loader(mode="load")
        self.assertEqual((result["inserted_count"], result["preserved_count"]), (10, 2))
        self.assertEqual(result["status"], "LOADED_WITH_WARNINGS")
        self.assertFalse(result["recalculation_required"])
        for row in (customs, rule, extra):
            self.assertEqual(self.rows()[row["option_code"]], row)
        self.assertIn("LEGACY_METADATA_INCOMPLETE", [w["code"] for w in result["warnings"]])

    def test_type_conflict_has_zero_writes(self):
        row = dict(option_code="CUSTOMS", option_type="RULE", label_ko="잘못된 종류", value_json={}, sort_order=0, enabled=True)
        self.insert(row)
        result = self.run_loader(mode="load")
        self.assertEqual(result["errors"][0]["code"], "TYPE_CONFLICT")
        self.assertEqual(list(self.rows()), ["CUSTOMS"])

    def test_check_only_selects_and_records_plan(self):
        statements = []
        def record(_conn, _cursor, statement, *_args):
            statements.append(statement)
        event.listen(self.engine, "before_cursor_execute", record)
        try:
            result = self.run_loader(mode="check")
        finally:
            event.remove(self.engine, "before_cursor_execute", record)
        self.assertEqual(result["status"], "CHECKED")
        self.assertEqual(result["planned_insert_count"], 12)
        self.assertEqual(result["inserted_count"], 0)
        self.assertTrue(all(stmt.lstrip().upper().startswith("SELECT") for stmt in statements))
        self.assertEqual(self.rows(), {})
        plan = json.loads((Path(result["output_dir"]) / "plan.json").read_text(encoding="utf-8"))
        self.assertEqual(len(plan["records"]), 12)
        self.assertTrue(all(r["apply"] is False for r in plan["records"]))

    def test_approved_update_replaces_json_and_stale_current_state_rejected(self):
        rule = deepcopy(next(r for r in loader.read_manifest().records if r["option_code"] == "CUSTOMS_ANOMALY"))
        rule["value_json"] = {"r2": {"hs_prefixes": ["01"]}}
        self.insert(rule)
        checked = self.run_loader(mode="check")
        approved = self.plan_file(checked, ["CUSTOMS_ANOMALY"])
        result = self.run_loader(mode="load", apply_plan=approved)
        self.assertEqual((result["inserted_count"], result["updated_count"]), (11, 1))
        self.assertTrue(result["recalculation_required"])
        self.assertIn("r3", self.rows()["CUSTOMS_ANOMALY"]["value_json"])
        result = self.run_loader(mode="load", apply_plan=approved)
        self.assertEqual(result["errors"][0]["code"], "STALE_PLAN")

    def test_plan_tampering_target_seed_before_and_actions_rejected(self):
        row = source()
        self.insert({**row, "label_ko": "old"})
        path = self.seed([row])
        checked = self.run_loader(mode="check", seed_path=path)
        plan_path = self.plan_file(checked, ["TEST_SOURCE"])
        original = json.loads(plan_path.read_text(encoding="utf-8"))
        mutations = []
        for key, value in (("seed_sha256", "bad"), ("seed_version", "bad"), ("target_db", {"database": "another"})):
            plan = deepcopy(original)
            plan[key] = value
            mutations.append(plan)
        for key, value in (("before_hash", "bad"), ("proposed_action", "DELETE"), ("apply", 1),
                           ("desired", {**row, "label_ko": "tampered"}), ("before", None), ("option_code", "OTHER")):
            plan = deepcopy(original)
            plan["records"][0][key] = value
            mutations.append(plan)
        duplicate = deepcopy(original)
        duplicate["records"] *= 2
        mutations.append(duplicate)
        for plan in mutations:
            with self.subTest(plan=plan.get("seed_sha256")):
                plan_path.write_text(json.dumps(plan), encoding="utf-8")
                result = self.run_loader(mode="load", seed_path=path, apply_plan=plan_path)
                self.assertEqual(result["exit_code"], 1)
                self.assertEqual(self.rows()[row["option_code"]]["label_ko"], "old")

    def test_plan_label_only_change_does_not_request_recalculation(self):
        rule = deepcopy(next(r for r in loader.read_manifest().records if r["option_code"] == "CUSTOMS_ANOMALY"))
        self.insert({**rule, "label_ko": "이전 표시명"})
        path = self.seed([rule])
        plan = self.plan_file(self.run_loader(mode="check", seed_path=path), ["CUSTOMS_ANOMALY"])
        result = self.run_loader(mode="load", seed_path=path, apply_plan=plan)
        self.assertEqual(result["updated_count"], 1)
        self.assertFalse(result["recalculation_required"])

    def test_disabled_rule_does_not_require_recalculation_until_enabled(self):
        rule = deepcopy(next(r for r in loader.read_manifest().records if r["option_code"] == "CUSTOMS_ANOMALY"))
        rule["enabled"] = False
        result = self.run_loader(mode="load", seed_path=self.seed([rule]))
        self.assertFalse(result["recalculation_required"])
        rule["value_json"]["r3"]["unit_ratio"] = 7
        checked = self.run_loader(mode="check", seed_path=self.seed([rule]))
        result = self.run_loader(mode="load", seed_path=self.seed_path,
                                 apply_plan=self.plan_file(checked, ["CUSTOMS_ANOMALY"]))
        self.assertEqual(result["updated_count"], 1)
        self.assertFalse(result["recalculation_required"])
        rule["enabled"] = True
        checked = self.run_loader(mode="check", seed_path=self.seed([rule]))
        result = self.run_loader(mode="load", seed_path=self.seed_path,
                                 apply_plan=self.plan_file(checked, ["CUSTOMS_ANOMALY"]))
        self.assertTrue(result["recalculation_required"])

    def test_terms_immutability_duplicates_and_new_version(self):
        self.insert(terms())
        modified = terms()
        modified["value_json"]["body"] = "다른 본문"
        result = self.run_loader(mode="load", seed_path=self.seed([modified]))
        self.assertEqual(result["errors"][0]["code"], "IMMUTABLE_TERMS_CONFLICT")
        result = self.run_loader(mode="load", seed_path=self.seed([terms("TERMS_SECOND")]))
        self.assertEqual(result["errors"][0]["code"], "AMBIGUOUS_TERMS")
        result = self.run_loader(mode="load", seed_path=self.seed([terms("TERMS_FUTURE", "2099-01-01 00:00:00")]))
        self.assertEqual(result["inserted_count"], 1)
        row = {**terms(), "enabled": False}
        checked = self.run_loader(mode="check", seed_path=self.seed([row]))
        result = self.run_loader(mode="load", seed_path=self.seed_path,
                                 apply_plan=self.plan_file(checked, [row["option_code"]]))
        self.assertEqual(result["updated_count"], 1)

    def test_missing_wrong_disabled_parent_and_new_parent(self):
        path = self.seed([weekly()])
        result = self.run_loader(mode="load", seed_path=path)
        self.assertEqual(result["errors"][0]["code"], "INVALID_REFERENCE")
        self.assertFalse(self.rows())
        parent = dict(option_code="RPT_WEEKLY", option_type="REPORT_TYPE", label_ko="주간", value_json={}, sort_order=10, enabled=True)
        result = self.run_loader(mode="load", seed_path=self.seed([weekly(), parent]))
        self.assertEqual(result["inserted_count"], 2)
        # 부모만 비활성화하는 계획도 기존 활성 주기에서 역참조를 확인한다.
        path = self.seed([{**parent, "enabled": False}])
        checked = self.run_loader(mode="check", seed_path=path)
        result = self.run_loader(mode="load", seed_path=path, apply_plan=self.plan_file(checked, ["RPT_WEEKLY"]))
        self.assertEqual(result["errors"][0]["code"], "DISABLED_REFERENCE")
        self.assertTrue(self.rows()["RPT_WEEKLY"]["enabled"])

    def test_nth_insert_error_rolls_back_all(self):
        original = CRUD.create
        attempts = []
        def fail(crud, session, **values):
            attempts.append(values["option_code"])
            if len(attempts) == 4:
                raise OperationalError("hidden", {}, Exception(1213, "hidden"))
            return original(crud, session, **values)
        with mock.patch.object(CRUD, "create", fail):
            result = self.run_loader(mode="load")
        self.assertTrue(result["rolled_back"])
        self.assertEqual(result["rollback_state"], "CONFIRMED")
        self.assertEqual(result["inserted_count"], 0)
        self.assertTrue(result["retryable"])
        self.assertFalse(result["action_required"])
        self.assertEqual(self.rows(), {})

    def test_nth_update_error_rolls_back_prior_updates(self):
        desired = [source("SOURCE_A"), source("SOURCE_B")]
        before = [{**row, "label_ko": "기존 표시명"} for row in desired]
        self.insert(*before)
        path = self.seed(desired)
        plan = self.plan_file(self.run_loader(mode="check", seed_path=path), [r["option_code"] for r in desired])
        original = CRUD.update
        calls = []
        def fail(crud, session, code, **values):
            calls.append(code)
            if len(calls) == 2:
                raise OperationalError("hidden", {}, Exception(1062, "duplicate"))
            return original(crud, session, code, **values)
        with mock.patch.object(CRUD, "update", fail):
            result = self.run_loader(mode="load", seed_path=path, apply_plan=plan)
        self.assertEqual(result["updated_count"], 0)
        self.assertTrue(result["rolled_back"])
        self.assertFalse(result["retryable"])
        self.assertEqual(self.rows(), {r["option_code"]: r for r in before})

    def test_auth_schema_and_transient_errors_are_classified(self):
        for code, retryable in ((1045, False), (1142, False), (2003, True), (1205, True), (1062, False)):
            with self.subTest(code=code), mock.patch.object(loader, "inspect_schema", side_effect=
                    OperationalError("hidden", {}, Exception(code, "hidden"))):
                result = self.run_loader(mode="load")
                self.assertEqual(result["retryable"], retryable)
                self.assertEqual(result["action_required"], not retryable)
                self.assertEqual(result["exit_code"], 2)
        with mock.patch.object(loader, "inspect_schema", side_effect=loader.ContractError("UNSUPPORTED_SCHEMA", "스키마 불일치")):
            result = self.run_loader(mode="check")
        self.assertEqual(result["exit_code"], 2)
        self.assertFalse(result["retryable"])

    def test_snapshot_failure_before_first_write(self):
        original = loader.Artifacts.write
        def fail(artifacts, name, value):
            if name == "before_snapshot.json" and "existing" in value:
                raise PermissionError("TOP_SECRET")
            return original(artifacts, name, value)
        with mock.patch.object(loader.Artifacts, "write", fail):
            result = self.run_loader(mode="load")
        self.assertEqual(result["errors"][0]["stage"], "snapshot")
        self.assertFalse(self.rows())
        self.assertFalse(result["retryable"])

    def test_cleanup_failures_preserve_first_error_and_try_other_cleanup(self):
        class BrokenSession(Session):
            def rollback(self):
                super().rollback()
                raise RuntimeError("rollback failure")
            def close(self):
                super().close()
                raise RuntimeError("close failure")
        original = CRUD.create
        attempts = []
        def fail(crud, session, **values):
            if attempts:
                raise OperationalError("hidden", {}, Exception(1213, "original error"))
            attempts.append(1)
            return original(crud, session, **values)
        with mock.patch.object(loader, "Session", BrokenSession), mock.patch.object(CRUD, "create", fail):
            result = self.run_loader(mode="load")
        self.assertEqual(result["errors"][0]["db_error_code"], 1213)
        self.assertEqual([e["stage"] for e in result["cleanup_errors"]], ["rollback", "session_close"])
        self.assertEqual(result["rollback_state"], "UNKNOWN")
        self.assertIsNone(result["rolled_back"])
        self.assertFalse(result["retryable"])
        self.assertTrue(result["action_required"])
        self.assertFalse(self.rows())

    def test_commit_unknown_even_if_following_rollback_succeeds(self):
        class LostCommitResponse(Session):
            def commit(self):
                super().commit()  # DB에는 실제로 저장되었으나 응답 유실을 흉내 낸다.
                raise OperationalError("TOP_SECRET", {}, Exception(2013, "TOP_SECRET"))
        with mock.patch.object(loader, "Session", LostCommitResponse):
            result = self.run_loader(mode="load")
        self.assertEqual((result["status"], result["commit_state"], result["exit_code"]), ("COMMIT_UNKNOWN", "UNKNOWN", 3))
        self.assertFalse(result["retryable"])
        self.assertIsNone(result["rolled_back"])
        self.assertTrue(result["action_required"])
        self.assertEqual(len(self.rows()), 12)
        self.assertNotIn("TOP_SECRET", json.dumps(result))

    def test_postcommit_verification_failure_keeps_committed_counts(self):
        original = loader._verify
        calls = []
        def fail(actual, expected, codes):
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError("public read failed")
            original(actual, expected, codes)
        with mock.patch.object(loader, "_verify", fail):
            result = self.run_loader(mode="load")
        self.assertEqual((result["status"], result["exit_code"]), ("COMMITTED_VERIFY_FAILED", 3))
        self.assertEqual(result["inserted_count"], 12)
        self.assertEqual(result["commit_state"], "CONFIRMED")
        self.assertFalse(result["rolled_back"])

    def test_final_report_failure_is_not_downgraded_to_cleanup_warning(self):
        original = loader.Artifacts.write
        def fail(artifacts, name, value):
            if name == "report.json" and value["commit_state"] == "CONFIRMED":
                raise PermissionError("report unavailable")
            return original(artifacts, name, value)
        with mock.patch.object(loader.Artifacts, "write", fail):
            result = self.run_loader(mode="load")
        self.assertEqual((result["status"], result["exit_code"]), ("COMMITTED_REPORT_FAILED", 3))
        self.assertEqual(result["inserted_count"], 12)
        self.assertEqual(len(self.rows()), 12)

    def test_successful_commit_cleanup_failure_keeps_load_success(self):
        class CloseErrorSession(Session):
            def close(self):
                super().close()
                raise RuntimeError("close error")
        with mock.patch.object(loader, "Session", CloseErrorSession):
            result = self.run_loader(mode="load")
        self.assertEqual(result["status"], "LOADED_WITH_WARNINGS")
        self.assertEqual(result["commit_state"], "CONFIRMED")
        self.assertEqual(result["exit_code"], 0)
        self.assertTrue(result["action_required"])

    def test_current_db_not_previous_success_controls_missing_rows(self):
        self.run_loader(mode="load")
        with self.engine.begin() as conn:
            conn.execute(ServiceOption.__table__.delete().where(ServiceOption.option_code == "CUSTOMS"))
        result = self.run_loader(mode="load")
        self.assertEqual(result["inserted_count"], 1)

    def test_input_reference_queries_do_not_scale_per_row(self):
        rows = [source(f"SOURCE_{i}") for i in range(150)]
        statements = []
        def record(_conn, _cursor, statement, *_args):
            statements.append(statement)
        event.listen(self.engine, "before_cursor_execute", record)
        try:
            result = self.run_loader(mode="check", seed_path=self.seed(rows))
        finally:
            event.remove(self.engine, "before_cursor_execute", record)
        self.assertEqual(result["exit_code"], 0)
        self.assertLessEqual(len(statements), 3)

    def test_full_source_metadata_roundtrip_and_explicit_replacement(self):
        row = configured_source()
        path = self.seed([row])
        statements = []
        def record(_conn, _cursor, statement, *_args):
            statements.append(statement)
        event.listen(self.engine, "before_cursor_execute", record)
        try:
            with mock.patch("backend.app.db.batch_log.track_batch", side_effect=AssertionError("실행 이력 작성 금지")):
                result = self.run_loader(mode="load", seed_path=path)
        finally:
            event.remove(self.engine, "before_cursor_execute", record)
        self.assertEqual(result["inserted_count"], 1, result)
        self.assertEqual(self.rows()[row["option_code"]], row)
        self.assertTrue(result["configuration_only"])
        self.assertFalse(result["source_jobs_executed"])
        self.assertFalse(result["batch_run_written"])
        writes = [s for s in statements if s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "CREATE"))]
        self.assertTrue(writes)
        self.assertTrue(all(s.lstrip().upper().startswith("INSERT INTO SERVICE_OPTION") for s in writes))
        row["value_json"]["jobs"][0]["schedule"] = {"type": "INTERVAL", "every_minutes": 60}
        checked = self.run_loader(mode="check", seed_path=self.seed([row]))
        preserved = self.run_loader(mode="load", seed_path=self.seed_path)
        self.assertEqual(preserved["preserved_count"], 1)
        self.assertEqual(self.rows()[row["option_code"]]["value_json"]["jobs"][0]["schedule"]["type"], "DAILY")
        applied = self.run_loader(mode="load", seed_path=self.seed_path,
                                  apply_plan=self.plan_file(checked, [row["option_code"]]))
        self.assertEqual(applied["updated_count"], 1)
        self.assertEqual(self.rows()[row["option_code"]], row)
        self.assertNotIn("time", self.rows()[row["option_code"]]["value_json"]["jobs"][0]["schedule"])

    def test_legacy_active_empty_period_needs_explicit_correction_plan(self):
        legacy = {**weekly(), "value_json": {}}
        self.insert(legacy)  # 이전 버전이 만든 DB 상태를 fixture로 재현한다.
        checked = self.run_loader(mode="check")
        self.assertEqual(checked["errors"][0]["code"], "ACTIVE_PERIOD_NOT_CONFIGURED")
        plan = self.plan_file(checked, ["WEEKLY"])
        result = self.run_loader(mode="load")
        self.assertEqual(result["exit_code"], 1)
        self.assertEqual(self.rows(), {"WEEKLY": legacy})
        corrected = self.run_loader(mode="load", apply_plan=plan)
        self.assertEqual(corrected["exit_code"], 0, corrected)
        self.assertEqual((corrected["inserted_count"], corrected["updated_count"]), (11, 1))
        self.assertFalse(self.rows()["WEEKLY"]["enabled"])
        configured = weekly()
        path = self.seed([configured])
        checked = self.run_loader(mode="check", seed_path=path)
        activated = self.run_loader(mode="load", seed_path=path, apply_plan=self.plan_file(checked, ["WEEKLY"]))
        self.assertEqual(activated["updated_count"], 1)
        self.assertEqual(self.rows()["WEEKLY"], configured)

    def test_incomplete_fixed_job_target_load_never_writes(self):
        row = configured_source(jobs=[source_job("CUSTOMS_METRICS", targets=["export_monthly"])])
        result = self.run_loader(mode="load", seed_path=self.seed([row]))
        self.assertEqual(result["errors"][0]["code"], "JOB_TARGET_MISMATCH")
        self.assertEqual(result["verification"]["database"], "NOT_CHECKED")
        self.assertEqual(self.rows(), {})


@unittest.skipUnless(os.environ.get("SERVICE_OPTION_TEST_DATABASE"), "전용 MySQL 테스트 DB 미설정")
class MySQLIntegrationTest(FilesTest):
    """환경변수로 지정한 새 전용 DB만 생성한다. 개발 DB와 공용 .env를 읽지 않는다."""

    @classmethod
    def setUpClass(cls):
        database = os.environ["SERVICE_OPTION_TEST_DATABASE"]
        if not re.fullmatch(r"service_option_test_[A-Za-z0-9_]+", database):
            raise ValueError("새 DB 이름은 service_option_test_ 접두사가 필요합니다.")
        for key in ("HOST", "PORT", "USER", "PASSWORD"):
            if "SERVICE_OPTION_TEST_" + key not in os.environ:
                raise ValueError("통합 테스트는 별도 접속 정보 4개를 모두 명시해야 합니다.")
        url = URL.create("mysql+pymysql", host=os.environ["SERVICE_OPTION_TEST_HOST"],
                         port=int(os.environ["SERVICE_OPTION_TEST_PORT"]),
                         username=os.environ["SERVICE_OPTION_TEST_USER"], password=os.environ["SERVICE_OPTION_TEST_PASSWORD"])
        admin = create_engine(url)
        try:
            with admin.connect() as conn:
                # IF NOT EXISTS를 쓰지 않는다. 기존 DB라면 테스트를 시작하지 않는다.
                conn.exec_driver_sql(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci")
        finally:
            admin.dispose()
        cls.engine = create_engine(url.set(database=database), pool_pre_ping=True)
        cls.addClassCleanup(cls.engine.dispose)
        ddl = (loader.ROOT / "db/mysql/init/01_schema.sql").read_text(encoding="utf-8")
        with cls.engine.begin() as conn:
            for statement in ddl.split(";"):
                if statement.strip():
                    conn.exec_driver_sql(statement)

    def setUp(self):
        super().setUp()
        # 이 테스트 클래스가 새로 만든 전용 DB의 fixture만 정리한다.
        with self.engine.begin() as conn:
            for table in ("batch_run", "article", "report_subscription", "report", "user_consent"):
                conn.exec_driver_sql(f"DELETE FROM `{table}`")
            conn.exec_driver_sql("DELETE FROM service_option")

    def run_loader(self, **kwargs):
        return loader.run(engine=self.engine, output=self.folder, **kwargs)

    def read_rows(self):
        with self.engine.connect() as conn:
            return {r["option_code"]: dict(r) for r in conn.execute(select(ServiceOption.__table__)).mappings()}

    def test_preservation_approved_change_and_stale_plan(self):
        rule = deepcopy(next(r for r in loader.read_manifest().records if r["option_code"] == "CUSTOMS_ANOMALY"))
        old = {**rule, "enabled": False, "value_json": {"r2": {"hs_prefixes": ["01"]}}}
        with self.engine.begin() as conn:
            conn.execute(ServiceOption.__table__.insert(), [old, source("CUSTOM_SOURCE")])
        result = self.run_loader(mode="load")
        self.assertEqual((result["inserted_count"], result["preserved_count"]), (11, 1), result)
        self.assertEqual(self.read_rows()["CUSTOMS_ANOMALY"], old)
        self.assertIn("CUSTOM_SOURCE", self.read_rows())
        plan = self.plan_file(self.run_loader(mode="check"), ["CUSTOMS_ANOMALY"])
        result = self.run_loader(mode="load", apply_plan=plan)
        self.assertEqual(result["updated_count"], 1, result)
        self.assertTrue(result["recalculation_required"])
        self.assertEqual(self.read_rows()["CUSTOMS_ANOMALY"], rule)
        result = self.run_loader(mode="load", apply_plan=plan)
        self.assertEqual(result["errors"][0]["code"], "STALE_PLAN")

    def test_real_transaction_failure_and_unrelated_tables_unchanged(self):
        with self.engine.begin() as conn:
            conn.exec_driver_sql("INSERT INTO users(email,password_hash) VALUES ('fixture@example.org','TEST HASH')")
            tables = loader.inspect_schema(conn)["tables"] - {"service_option"}
            before = {t: conn.exec_driver_sql(f"SELECT * FROM `{t}`").fetchall() for t in tables}
        original = CRUD.create
        calls = []
        def fail(crud, session, **values):
            if len(calls) == 3:
                # 실제 MySQL의 PK 위반을 일으켜 이전 DML까지 롤백되는지 확인한다.
                session.execute(ServiceOption.__table__.insert().values(**deepcopy(calls[0])))
            calls.append(values)
            return original(crud, session, **values)
        with mock.patch.object(CRUD, "create", fail):
            result = self.run_loader(mode="load")
        self.assertEqual(result["errors"][0]["db_error_code"], 1062, result)
        self.assertTrue(result["rolled_back"])
        self.assertFalse(self.read_rows())
        self.assertEqual(self.run_loader(mode="load")["inserted_count"], 12)
        with self.engine.connect() as conn:
            after = {t: conn.exec_driver_sql(f"SELECT * FROM `{t}`").fetchall() for t in tables}
        self.assertEqual(before, after)

    def test_terms_and_final_reference_validation(self):
        result = self.run_loader(mode="load", seed_path=self.seed([terms()]))
        self.assertEqual(result["inserted_count"], 1, result)
        changed = terms()
        changed["value_json"]["body"] = "변경 본문"
        result = self.run_loader(mode="load", seed_path=self.seed([changed]))
        self.assertEqual(result["errors"][0]["code"], "IMMUTABLE_TERMS_CONFLICT")
        result = self.run_loader(mode="load", seed_path=self.seed([terms("TERMS_SECOND")]))
        self.assertEqual(result["errors"][0]["code"], "AMBIGUOUS_TERMS")
        result = self.run_loader(mode="load", seed_path=self.seed([weekly()]))
        self.assertEqual(result["errors"][0]["code"], "INVALID_REFERENCE")
        parent = dict(option_code="RPT_WEEKLY", option_type="REPORT_TYPE", label_ko="리포트", value_json={}, sort_order=1, enabled=True)
        result = self.run_loader(mode="load", seed_path=self.seed([weekly(), parent]))
        self.assertEqual(result["inserted_count"], 2, result)

    def test_native_types_collation_and_reexecution(self):
        result = self.run_loader(mode="load")
        self.assertEqual(result["inserted_count"], 12, result)
        result = self.run_loader(mode="load")
        self.assertEqual((result["inserted_count"], result["updated_count"]), (0, 0))
        with self.engine.begin() as conn:
            conn.execute(ServiceOption.__table__.update().where(ServiceOption.option_code == "CUSTOMS")
                         .values(option_code="customs"))
        result = self.run_loader(mode="load")
        self.assertEqual(result["errors"][0]["code"], "CODE_COLLATION_CONFLICT")
        # 입력 범위 밖의 비표준 코드는 보존·보고하며 무관한 출처 추가를 막지 않는다.
        result = self.run_loader(mode="load", seed_path=self.seed([source()]))
        self.assertEqual(result["inserted_count"], 1, result)
        self.assertIn("customs", result["readiness"]["nonstandard_codes"])

    def test_check_with_read_only_transaction_and_fk_integrity(self):
        read_engine = create_engine(self.engine.url)
        @event.listens_for(read_engine, "connect")
        def read_only(dbapi, _record):
            with dbapi.cursor() as cursor:
                cursor.execute("SET SESSION TRANSACTION READ ONLY")
        statements = []
        def record(_conn, _cursor, statement, *_args):
            statements.append(statement)
        event.listen(read_engine, "before_cursor_execute", record)
        try:
            result = loader.run(mode="check", engine=read_engine, output=self.folder)
        finally:
            read_engine.dispose()
        self.assertEqual(result["exit_code"], 0, result)
        self.assertFalse(any(s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER")) for s in statements))
        self.run_loader(mode="load")
        with Session(self.engine) as session:
            with track_batch(Source("CUSTOMS", "관세청", {"url": "https://example.org"}),
                             "TEST_SERVICE_OPTION", "hs_code", date(2026, 10, 1), date(2026, 10, 1), engine=self.engine):
                pass
            self.assertEqual(session.scalar(select(ServiceOption).where(ServiceOption.option_code == "CUSTOMS")).value_json,
                             next(r["value_json"] for r in loader.read_manifest().records if r["option_code"] == "CUSTOMS"))

    def test_full_metadata_and_un_pre_registration_only(self):
        row = configured_source()
        job = source_job("UN_FILE_IMPORT", targets=["un_data"], schedule={"type": "MANUAL"},
                         scope={"mode": "EXPLICIT_PERIOD", "start": "2024-01-01", "end": "2025-12-31"})
        un = configured_source("UN_COMTRADE_SOURCE", [job])
        un["value_json"]["data_period"] = "MONTHLY_AND_ANNUAL"
        result = self.run_loader(mode="load", seed_path=self.seed([row, un]))
        self.assertEqual(result["inserted_count"], 2, result)
        self.assertEqual(self.read_rows(), {r["option_code"]: r for r in (row, un)})
        info = result["readiness"]["sources"]["UN_COMTRADE_SOURCE"]["jobs"][0]
        self.assertEqual(info["missing_db_tables"], ["un_data"])
        self.assertEqual(info["missing_batch_targets"], ["un_data"])
        with self.engine.connect() as conn:
            self.assertEqual(conn.exec_driver_sql("SELECT COUNT(*) FROM batch_run").scalar_one(), 0)
        self.assertFalse(result["source_jobs_executed"])
        self.assertFalse(result["readiness"]["un_ready"])

    def test_review_fixes_real_jobs_targets_and_weekly_activation(self):
        legacy = {**weekly(), "value_json": {}}
        with self.engine.begin() as conn:
            conn.execute(ServiceOption.__table__.insert().values(**legacy))
        checked = self.run_loader(mode="check")
        self.assertEqual(checked["errors"][0]["code"], "ACTIVE_PERIOD_NOT_CONFIGURED")
        result = self.run_loader(mode="load", apply_plan=self.plan_file(checked, ["WEEKLY"]))
        self.assertEqual(result["exit_code"], 0, result)
        self.assertFalse(self.read_rows()["WEEKLY"]["enabled"])
        row = configured_source(jobs=[source_job("CUSTOMS_CSV_LOAD"), source_job("HS6_AGGREGATION",
                targets=["export_summary", "import_summary"],
                schedule={"type": "AFTER_JOB", "after_job_name": "CUSTOMS_CSV_LOAD"}),
                source_job("CUSTOMS_METRICS")])
        path = self.seed([row])
        checked = self.run_loader(mode="check", seed_path=path)
        result = self.run_loader(mode="load", seed_path=path, apply_plan=self.plan_file(checked, ["CUSTOMS"]))
        self.assertEqual(result["updated_count"], 1, result)
        self.assertEqual(self.read_rows()["CUSTOMS"], row)
        incomplete = deepcopy(row)
        incomplete["value_json"]["jobs"][-1]["target_tables"] = ["export_monthly"]
        result = self.run_loader(mode="load", seed_path=self.seed([incomplete]))
        self.assertEqual(result["errors"][0]["code"], "JOB_TARGET_MISMATCH")
        self.assertEqual(self.read_rows()["CUSTOMS"], row)
        path = self.seed([weekly()])
        checked = self.run_loader(mode="check", seed_path=path)
        result = self.run_loader(mode="load", seed_path=path, apply_plan=self.plan_file(checked, ["WEEKLY"]))
        self.assertEqual(result["updated_count"], 1, result)
        self.assertEqual(self.read_rows()["WEEKLY"], weekly())
        with self.engine.connect() as conn:
            self.assertEqual(conn.exec_driver_sql("SELECT COUNT(*) FROM batch_run").scalar_one(), 0)

    def test_unique_race_rolls_back_without_overwriting(self):
        seed = self.seed([source()])
        barrier = Barrier(2)
        original = loader.build_plan
        def synchronize(*args):
            plan = original(*args)
            barrier.wait(timeout=15)
            return plan
        with mock.patch.object(loader, "build_plan", synchronize), ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.run_loader, mode="load", seed_path=seed) for _ in range(2)]
            results = [future.result(timeout=25) for future in futures]
        self.assertEqual(sum(r["exit_code"] == 0 for r in results), 1, results)
        failure = next(r for r in results if r["exit_code"] != 0)
        self.assertIn(failure["errors"][0]["db_error_code"], (1062, 1205, 1213))
        self.assertEqual(failure["inserted_count"], 0)


if __name__ == "__main__":
    unittest.main()
