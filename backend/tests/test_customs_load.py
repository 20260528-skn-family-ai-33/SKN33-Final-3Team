"""CSV 경계 조건과 전용 MySQL 테스트 DB의 공개 처리를 검증한다."""
import csv
import json
import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from backend.app.services.customs_stage import FIELDS, Stage, ValidationError, normalize
from backend.app.services.customs_load import Loader, TABLES
from backend.app.scheduler.jobs.customs_load import run
from argparse import Namespace
from sqlalchemy import URL
from backend.app.db.session import make_engine


def row(**changes):
    return {"year": "2025.01", "statCd": "NA", "hsCd": "0101210000",
            "expDlr": "100.01", "expWgt": "0", "impDlr": "0", "impWgt": "", **changes}


def write_csv(path, rows):
    with Path(path).open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=(*FIELDS, "statKor"))
        writer.writeheader()
        writer.writerows(rows)


class CsvValidationTest(unittest.TestCase):
    def test_codes_zero_and_null_preserved(self):
        normalized = normalize(row(), 2025)
        self.assertEqual(normalized, ("2025-01-01", "NA", "0101210000", "100.01", "0.000", "0.00", None))

    def test_totals_excluded_but_malformed_totals_fail(self):
        self.assertIsNone(normalize(row(year="총계", statCd="-", hsCd="-"), 2025))
        with self.assertRaises(ValidationError):
            normalize(row(year="총계"), 2025)

    def test_bad_dates_codes_and_decimals_fail(self):
        changes = [dict(year="2024.01"), dict(year="2025.13"), dict(year="2025.1"),
                   dict(hsCd="101210000"), dict(statCd="-"), dict(expDlr=""),
                   dict(expDlr="-1"), dict(expDlr="NaN"), dict(expDlr="1e3"),
                   dict(expDlr="1.001"), dict(expWgt="0.0001"),
                   dict(expDlr="100000000000000000000"), dict(impWgt="-1")]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValidationError):
                normalize(row(**change), 2025)

    def test_unsorted_months_duplicates_errors_and_exact_totals(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_csv(root / "raw.csv", [row(year="2025.02"), row(),
                row(hsCd="0101211000", expDlr="0.02"), row(), row(expDlr="-1"),
                row(year="총계", statCd="-", hsCd="-")])
            with Stage(root / "stage.db") as stage:
                counts = stage.ingest(root / "raw.csv", 2025, root / "errors.jsonl", batch_size=1)
                self.assertEqual(counts, {"rows_total": 6, "rows_valid": 3, "rows_total_line": 1,
                    "invalid_rows": 1, "invalid_rows_held": 0, "duplicate_keys": 1})
                result = stage.summary()
                self.assertEqual(result[0]["export_usd"], "100.03")
                self.assertEqual(result[0]["hs6_groups"], 1)
                self.assertEqual(stage.references(), ({"NA"}, {"0101210000", "0101211000", "010121"}))
                self.assertEqual(len(list(stage.rows("2025-01-01", 1))), 2)
            errors = [json.loads(line) for line in (root / "errors.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([error["record"] for error in errors], [4, 5])

    def test_large_amount_exact_aggregation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_csv(root / "raw.csv", [row(expDlr="99999999999999999999.99"),
                row(hsCd="0101211000", expDlr="99999999999999999999.99")])
            with Stage(root / "stage.db") as stage:
                stage.ingest(root / "raw.csv", 2025, root / "errors.jsonl")
                self.assertEqual(stage.summary()[0]["export_usd"], "199999999999999999999.98")

    def test_invalid_csv_never_calls_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_csv(root / "raw.csv", [row(expDlr="-1")])
            args = Namespace(csv=root / "raw.csv", year=2025, mode="load", hs_ver="HS2022",
                output=root / "out", batch_size=1, env=root / "missing.env", source_code="CUSTOMS")
            with patch("backend.app.scheduler.jobs.customs_load.make_engine") as connect:
                self.assertEqual(run(args), 1)
                connect.assert_not_called()
            report = json.loads(next((root / "out").glob("*/report.json")).read_text(encoding="utf-8"))
            self.assertEqual(report["error"]["error_type"], "ValidationError")
            self.assertFalse(report["database_loaded"])

    def test_invalid_hold_preserves_raw_and_never_accepts_duplicate_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_csv(root / "raw.csv", [row(year="2022.01"), row(year="2022.01", hsCd="621142900")])
            with Stage(root / "stage.db") as stage:
                result = stage.ingest(root / "raw.csv", 2022, root / "errors.jsonl",
                                      invalid_output=root / "invalid.csv")
                self.assertTrue(stage.validated)
                self.assertEqual(result["invalid_rows_held"], 1)
            with (root / "invalid.csv").open(encoding="utf-8-sig", newline="") as stream:
                held = list(csv.DictReader(stream))
                self.assertEqual(held[0]["hsCd"], "621142900")
                self.assertEqual(held[0]["_hold_record"], "2")
            write_csv(root / "dup.csv", [row(), row()])
            with Stage(root / "dup.db") as stage:
                stage.ingest(root / "dup.csv", 2025, root / "dup_errors.jsonl", invalid_output=root / "dup_hold.csv")
                self.assertFalse(stage.validated)
            (root / "broken.csv").write_text(
                "year,statCd,hsCd,expDlr,expWgt,impDlr,impWgt\n"
                "2025.01,NA,0101210000,1,0,0,0\n2025.02,NA,0101210000,1,0,0\n", encoding="utf-8")
            with Stage(root / "broken.db") as stage:
                counts = stage.ingest(root / "broken.csv", 2025, root / "broken_errors.jsonl",
                                      invalid_output=root / "broken_hold.csv")
                self.assertFalse(stage.validated)
                self.assertEqual(counts["invalid_rows_held"], 0)

    def test_check_resolves_dictionary_and_holds_without_any_database_write(self):
        from unittest.mock import MagicMock
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "customs_raw_2022.csv"
            fields = (*FIELDS, "statKor")
            with raw.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                writer.writerows([row(year="2022.01", statKor="번식용"),
                    row(year="2022.01", hsCd="9999999290", statKor="-"),
                    row(year="2022.01", hsCd="621142900", statKor="-")])
            args = Namespace(csv=raw, year=2022, mode="check", hs_ver="HS2022", source_code="CUSTOMS",
                batch_size=2, env=root / "unused.env", output=root / "out", invalid_policy="hold",
                unmapped_policy="hold")
            engine = MagicMock()
            connection = engine.connect.return_value.__enter__.return_value
            connection.exec_driver_sql.return_value.fetchall.return_value = [
                ("0101210000", "HS2022"), ("010121", "HS2022")]
            with patch("backend.app.scheduler.jobs.customs_load.make_engine", return_value=engine), \
                    patch("backend.app.scheduler.jobs.customs_load.mysql_url", return_value="mock_url"), \
                    patch("backend.app.scheduler.jobs.customs_load.Loader") as loader, \
                    patch("backend.app.scheduler.jobs.customs_load.MetricCalculator") as metrics, \
                    patch("builtins.print"):
                self.assertEqual(run(args), 0)
                loader.return_value.load.assert_not_called()
                loader.return_value.preflight.assert_called_once()
                metrics.assert_not_called()
            connection.commit.assert_not_called()
            self.assertTrue(all(call.args[0].startswith("SELECT ")
                                for call in connection.exec_driver_sql.call_args_list))
            engine.dispose.assert_called_once()
            report = json.loads(next((root / "out").glob("*/report.json")).read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "CHECKED_WITH_HOLDS")
            self.assertFalse(report["database_loaded"])
            self.assertFalse(report["dictionary_changed"])
            self.assertEqual(report["hs10_codes_by_version"], {"HS2022": 1})
            self.assertIn("9999999290", report["hs_reference_errors"])
            self.assertEqual(report["rows_held"], 1)
            self.assertEqual(report["invalid_rows_held"], 1)

    def test_load_never_changes_dictionary_and_calculates_only_after_preflight(self):
        from unittest.mock import MagicMock
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "customs_raw_2023.csv"
            with raw.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=(*FIELDS, "statKor"))
                writer.writeheader()
                writer.writerow(row(year="2023.01", statKor="번식용"))
            args = Namespace(csv=raw, year=2023, mode="load", hs_ver="HS2022", source_code="CUSTOMS",
                batch_size=1, env=root / "unused.env", output=root / "out", invalid_policy="fail",
                unmapped_policy="hold")
            for fail in (True, False):
                engine = MagicMock()
                connection = engine.connect.return_value.__enter__.return_value
                connection.exec_driver_sql.return_value.fetchall.return_value = [("0101210000", "HS2022"), ("010121", "HS2022")]
                with patch("backend.app.scheduler.jobs.customs_load.make_engine", return_value=engine), \
                        patch("backend.app.scheduler.jobs.customs_load.mysql_url", return_value="mock_url"), \
                        patch("backend.app.scheduler.jobs.customs_load.Loader") as loader, \
                        patch("backend.app.scheduler.jobs.customs_load.MetricCalculator") as metrics, \
                        patch("builtins.print"):
                    def preflight(*arguments):
                        connection.commit.assert_not_called()
                        if fail:
                            raise ValidationError("국가 사전 누락 모의")
                    loader.return_value.preflight.side_effect = preflight
                    metrics.return_value.calculate.return_value = {"mock": True}
                    self.assertEqual(run(args), int(fail))
                    if fail:
                        connection.commit.assert_not_called()
                        loader.return_value.load.assert_not_called()
                        metrics.assert_not_called()
                    else:
                        connection.commit.assert_not_called()
                        loader.return_value.load.assert_called_once()
                        metrics.return_value.calculate.assert_called_once_with(2023, 1)


@unittest.skipUnless(os.environ.get("CUSTOMS_TEST_DATABASE"), "전용 MySQL 테스트 DB 미설정")
class MySQLLoadTest(unittest.TestCase):
    """CUSTOMS_TEST_DATABASE는 customs_test_로 시작하는 일회용 전용 DB만 허용한다."""

    @classmethod
    def setUpClass(cls):
        database = os.environ["CUSTOMS_TEST_DATABASE"]
        if not database.startswith("customs_test_") or not database.replace("_", "").isalnum():
            raise ValueError("CUSTOMS_TEST_DATABASE는 customs_test_ 접두사 필수")
        cls.created_database = False
        url = URL.create("mysql+pymysql",
            host=os.environ.get("CUSTOMS_TEST_HOST", "127.0.0.1"),
            port=int(os.environ.get("CUSTOMS_TEST_PORT", "3307")),
            username=os.environ.get("CUSTOMS_TEST_USER", "root"),
            password=os.environ.get("CUSTOMS_TEST_PASSWORD", ""))
        admin = make_engine(url)
        try:
            with admin.connect() as connection:
                connection.exec_driver_sql(f"CREATE DATABASE {database} CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci")
                cls.created_database = True
        finally:
            admin.dispose()
        cls.engine = make_engine(url.set(database=database))
        cls.connection = cls.engine.connect()
        schema_dir = Path(__file__).resolve().parents[2] / "db/mysql/init"
        for name in ("01_schema.sql", "02_hs_code_correlation.sql", "03_market_region_seed.sql", "04_country_seed.sql", "05_customs_batch_target.sql"):
            for statement in (schema_dir / name).read_text(encoding="utf-8-sig").split(";"):
                if statement.strip():
                    cls.connection.exec_driver_sql(statement)
        cls.connection.exec_driver_sql("INSERT INTO service_option (option_code,option_type,label_ko,value_json) VALUES ('CUSTOMS','SOURCE','관세청','{}')")
        cls.connection.exec_driver_sql("INSERT INTO hs_code (hs_code,hs_ver,name_ko,original_name_ko,source_edition) VALUES (%s,'HS2022',%s,%s,'TEST FIXTURE')", [
            ("010121", "테스트 HS6", "테스트 원본명"), ("0101210000", "테스트 HS10", None),
            ("0101211000", "테스트 HS10", None)])
        cls.connection.commit()

    @classmethod
    def tearDownClass(cls):
        cls.connection.close()
        cls.engine.dispose()

    def setUp(self):
        self.events = []
        self.loader = Loader(self.connection, lambda event, **values: self.events.append((event, values)))
        connection = self.connection
        for table in (*TABLES, "batch_run"):
            result = connection.exec_driver_sql(f"DELETE FROM {table}")
        result = connection.exec_driver_sql("DELETE FROM hs_code WHERE hs_ver<>'HS2022' OR hs_code NOT IN ('010121','0101210000','0101211000')")
        result = connection.exec_driver_sql("DELETE FROM service_option WHERE option_code='CUSTOMS_ANOMALY'")
        self.connection.commit()

    def test_sql_country_seed_is_reusable_and_trade_load_preserves_mappings(self):
        from backend.app.services.customs_reference import load_country_references
        country_before = self.query("SELECT * FROM country ORDER BY country_code")
        self.assertEqual(len(country_before), 243)
        self.assertEqual(self.query("SELECT COUNT(*) FROM market_region"), ((27,),))
        with self.engine.begin() as connection:
            folder = Path(__file__).resolve().parents[2] / "db/mysql/init"
            for name in ("03_market_region_seed.sql", "04_country_seed.sql"):
                connection.exec_driver_sql((folder / name).read_text(encoding="utf-8"))
        self.assertEqual(self.query("SELECT * FROM country ORDER BY country_code"), country_before)
        with self.engine.connect() as connection:
            references = load_country_references(connection)
            self.assertEqual(references["NA"]["region_id"], 18)
            self.assertEqual(references["NA"]["continent_name_ko"], "아프리카")
            self.assertIsNone(references["AQ"]["region_id"])
            self.assertIsNone(references["NA"]["currency_code"])
        self.load([row()])
        self.assertEqual(self.query("SELECT * FROM country ORDER BY country_code"), country_before)

    def test_common_tracker_initializes_source_without_country_writer(self):
        self.connection.exec_driver_sql("DELETE FROM service_option WHERE option_code='CUSTOMS'")
        self.connection.commit()
        self.load([row()])
        self.assertEqual(self.query("SELECT option_type,enabled FROM service_option WHERE option_code='CUSTOMS'"), (("SOURCE", 1),))
        self.assertEqual(self.query("SELECT status,COUNT(*) FROM batch_run GROUP BY status"), (("SUCCESS", 4),))

    def test_missing_country_is_reported_before_writes_without_csv_fallback(self):
        with self.assertRaises(ValidationError):
            self.load([row(statCd="AX")])
        self.assertEqual(self.query("SELECT COUNT(*) FROM country WHERE country_code='AX'"), ((0,),))
        self.assertEqual(self.query("SELECT COUNT(*) FROM export_monthly"), ((0,),))
        self.assertEqual(self.query("SELECT COUNT(*) FROM batch_run"), ((0,),))
        self.assertEqual(self.events[0][0], "reference_missing")
        self.assertEqual(self.events[0][1]["country"], ["AX"])

    def test_pr12_dictionary_loader_then_mixed_version_trade_load(self):
        from backend.app.scheduler.jobs.hs_code_load import load, validate
        from backend.app.services.customs_metrics import MetricCalculator
        rows = [dict(hs_code="340220", depth="6", name_ko="세정제|소매용 조제품",
                     source_edition="TEST FIXTURE", hs_ver="HS2017"),
                dict(hs_code="3402201000", depth="10", name_ko="세정제",
                     source_edition="TEST SUPPLEMENT", hs_ver="HS2017")]
        self.assertEqual(validate(rows), [])
        load(self.engine, rows)
        raw = [row(), row(year="2025.02", expDlr="200.02"),
               row(hsCd="3402201000", expDlr="10"),
               row(year="2025.02", hsCd="3402201000", expDlr="30")]
        self.load(raw, "auto")
        self.load(raw, "auto")
        MetricCalculator(self.connection).calculate(2025)
        for table in ("export_monthly", "import_monthly"):
            self.assertEqual(self.query(f"SELECT hs_ver,COUNT(*) FROM {table} GROUP BY hs_ver ORDER BY hs_ver"),
                             (("HS2017", 2), ("HS2022", 2)))
        self.assertEqual(tuple((v, str(a)) for v, a in self.query(
            "SELECT hs_ver,amount_usd FROM export_summary WHERE trade_month='2025-02-01' ORDER BY hs_ver")),
            (("HS2017", "30.00"), ("HS2022", "200.02")))
        self.assertEqual(tuple((v, str(a)) for v, a in self.query(
            "SELECT hs_ver,baseline_amount_usd FROM export_monthly WHERE trade_month='2025-02-01' ORDER BY hs_ver")),
            (("HS2017", "10.00"), ("HS2022", "100.01")))
        self.assertEqual(self.query("SELECT source_edition FROM hs_code WHERE hs_code='3402201000'"),
                         (("TEST SUPPLEMENT",),))
        self.assertEqual(self.query("SELECT name_ko,original_name_ko FROM hs_code WHERE hs_code='340220'"),
                         (("세정제", "소매용 조제품"),))

    def test_auto_ambiguous_version_rejected_before_any_trade_write(self):
        connection = self.connection
        result = connection.exec_driver_sql("INSERT INTO hs_code (hs_code,hs_ver,name_ko,original_name_ko,source_edition) VALUES "
                       "('010121','HS2017','테스트','테스트','TEST'),"
                       "('0101210000','HS2017','테스트',NULL,'TEST')")
        self.connection.commit()
        with self.assertRaises(ValidationError):
            self.load([row()], "auto")
        self.assertEqual(self.query("SELECT COUNT(*) FROM export_monthly"), ((0,),))
        self.assertEqual(self.query("SELECT COUNT(*) FROM batch_run"), ((0,),))

    def test_metrics_publish_without_changing_raw_amounts(self):
        from backend.app.services.customs_metrics import MetricCalculator
        self.load([row(year="2025.01",expDlr="100",expWgt="10",impDlr="10",impWgt="10"),
                   row(year="2025.02",expDlr="300",expWgt="10",impDlr="30",impWgt="10"),
                   row(year="2025.04",expDlr="1000",expWgt="10",impDlr="100",impWgt="10")])
        connection = self.connection
        result = connection.exec_driver_sql("INSERT INTO service_option (option_code,option_type,label_ko,value_json) VALUES ('CUSTOMS_ANOMALY','RULE','테스트 규칙',%s)",
                       (json.dumps({"r2":{"hs_prefixes":["01"]}}),))
        self.connection.commit()
        result=MetricCalculator(self.connection).calculate(2025)
        self.assertEqual(result["rows"],{"export_monthly":3,"import_monthly":3})
        connection = self.connection
        result = connection.exec_driver_sql("SELECT amount_usd,history_months,baseline_amount_usd,median_unit_usd_kg,anomaly_code FROM export_monthly WHERE trade_month='2025-04-01'")
        self.assertEqual(result.fetchone(),(1000,2,200,20,"R2"))
        result = connection.exec_driver_sql("SELECT baseline_amount_usd,median_unit_usd_kg FROM import_monthly WHERE trade_month='2025-04-01'")
        self.assertEqual(result.fetchone(),(20,2))
        result = connection.exec_driver_sql("SELECT history_months,baseline_amount_usd,median_unit_usd_kg FROM export_monthly WHERE trade_month='2025-01-01'")
        self.assertEqual(result.fetchone(),(0,None,None))
        result = connection.exec_driver_sql("SELECT COUNT(*) FROM batch_run WHERE job_name='CUSTOMS_METRICS' AND status='SUCCESS'")
        self.assertEqual(result.fetchone()[0],6)
        result = connection.exec_driver_sql("DELETE FROM service_option WHERE option_code='CUSTOMS_ANOMALY'")
        self.connection.commit()

    def test_metrics_recalculate_existing_following_year(self):
        from backend.app.services.customs_metrics import MetricCalculator
        self.load([row(expDlr="300",expWgt="10")])
        connection = self.connection
        for table in ("export_monthly","import_monthly"):
            result = connection.exec_driver_sql(f"INSERT INTO {table} (trade_month,country_code,hs10_code,hs_ver,amount_usd,weight_kg) VALUES ('2024-12-01','NA','0101210000','HS2022',100,10),('2026-01-01','NA','0101210000','HS2022',500,10)")
        self.connection.commit()
        MetricCalculator(self.connection).calculate(2025)
        self.assertEqual(self.query("SELECT history_months,baseline_amount_usd FROM export_monthly WHERE trade_month='2026-01-01'"),((1,300),))

    def test_metrics_roll_back_both_directions_on_second_update_failure(self):
        from backend.app.services.customs_metrics import MetricCalculator
        self.load([row()])
        execute = self.connection.exec_driver_sql
        def fail_import(sql, *args, **kwargs):
            if sql.startswith("UPDATE import_monthly m JOIN customs_metrics_stage"):
                raise ValidationError("수입 계산 공개 실패 모의")
            return execute(sql, *args, **kwargs)
        with patch.object(self.connection, "exec_driver_sql", side_effect=fail_import):
            with self.assertRaises(ValidationError):
                MetricCalculator(self.connection).calculate(2025)
        for table in ("export_monthly", "import_monthly"):
            self.assertEqual(self.query(f"SELECT history_months FROM {table}"), ((None,),))
        self.assertEqual(self.query("SELECT status,COUNT(*) FROM batch_run WHERE job_name='CUSTOMS_METRICS' GROUP BY status"), (("FAILED", 2),))

    def load(self, rows, hs_ver="HS2022"):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_csv(root / "raw.csv", rows)
            with Stage(root / "stage.db") as stage:
                counts = stage.ingest(root / "raw.csv", 2025, root / "errors.jsonl", 1)
                self.assertEqual(counts["invalid_rows"] + counts["duplicate_keys"], 0)
                return self.loader.load(stage, hs_ver, batch_size=1)

    def query(self, sql):
        connection = self.connection
        result = connection.exec_driver_sql(sql)
        rows = tuple(tuple(row) for row in result.fetchall())
        self.connection.rollback()
        return rows

    def test_repeat_and_correction_keep_omitted_rows_and_exact_summary(self):
        original = [row(), row(hsCd="0101211000", expDlr="0.02", impDlr="1.01")]
        self.load(original)
        self.load(original)
        self.assertEqual(self.query("SELECT COUNT(*) FROM export_monthly")[0][0], 2)
        self.assertEqual(str(self.query("SELECT amount_usd FROM export_summary")[0][0]), "100.03")
        self.assertEqual(str(self.query("SELECT amount_usd FROM import_summary")[0][0]), "1.01")
        self.assertEqual(self.query("SELECT DISTINCT status FROM batch_run"), (("SUCCESS",),))
        self.load([row(expDlr="200.00")])
        self.assertEqual(self.query("SELECT COUNT(*) FROM export_monthly")[0][0], 2)
        self.assertEqual(str(self.query("SELECT amount_usd FROM export_summary")[0][0]), "200.02")
        self.assertEqual(self.query("SELECT DISTINCT request_count FROM batch_run"), ((0,),))

    def add_parents(self):
        with self.engine.begin() as connection:
            connection.exec_driver_sql("INSERT INTO hs_code (hs_code,hs_ver,name_ko,source_edition) VALUES "
                "('01','HS2022','동물','PARENT'),('0101','HS2022','말','PARENT')")

    def test_unregistered_hs10_is_added_atomically_and_repeat_keeps_dictionary(self):
        self.add_parents()
        raw = row(hsCd="0101219999", statKor="실적에서 확인한 품목명")
        self.load([raw], "auto")
        self.assertEqual(self.query("SELECT name_ko,original_name_ko,source_edition,depth FROM hs_code WHERE hs_code='0101219999'"),
            (("실적에서 확인한 품목명", None, "관세청 수출입실적 2025년 1월", 10),))
        self.load([{**raw, "statKor": "이름을 바꾸면 안 됨"}], "auto")
        self.assertEqual(self.query("SELECT name_ko FROM hs_code WHERE hs_code='0101219999'"), (("실적에서 확인한 품목명",),))
        self.assertEqual(self.query("SELECT COUNT(*) FROM export_monthly"), ((1,),))

    def test_check_plans_new_hs10_without_writing_then_load_publishes_it(self):
        self.add_parents()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_csv(root / "raw.csv", [row(hsCd="0101219999", statKor="검증된 신규 품목")])
            args = Namespace(csv=root / "raw.csv", year=2025, mode="check", hs_ver="auto",
                output=root / "out", batch_size=1, env=root / "unused.env", source_code="CUSTOMS")
            with patch("backend.app.scheduler.jobs.customs_load.mysql_url", return_value=self.engine.url):
                report = run(args, return_report=True)
                self.assertEqual(report["status"], "CHECKED")
                self.assertEqual(report["hs10_supplement_plan"][0]["hs_code"], "0101219999")
                self.assertFalse(report["dictionary_changed"])
                self.assertEqual(self.query("SELECT COUNT(*) FROM hs_code WHERE hs_code='0101219999'"), ((0,),))
                self.assertEqual(self.query("SELECT COUNT(*) FROM batch_run"), ((0,),))
                args.mode = "load"
                report = run(args, return_report=True)
                self.assertEqual(report["status"], "LOADED")
                self.assertTrue(report["dictionary_changed"])
                self.assertEqual(report["metrics"]["rows"]["export_monthly"], 1)

    def test_supplement_rolls_back_with_failed_trade_month(self):
        self.add_parents()
        with patch.object(self.loader, "verify", side_effect=ValidationError("월별 적재 실패")):
            with self.assertRaises(ValidationError):
                self.load([row(hsCd="0101219999", statKor="신규 품목")])
        self.assertEqual(self.query("SELECT COUNT(*) FROM hs_code WHERE hs_code='0101219999'"), ((0,),))
        self.assertEqual(self.query("SELECT COUNT(*) FROM export_monthly"), ((0,),))

    def test_racing_existing_supplement_preserves_authoritative_name(self):
        from backend.app.services.customs_reference import insert_hs_additions
        self.add_parents()
        with self.engine.begin() as connection:
            # 다른 작업이 먼저 등록한 행을 중복 키 경로로 만나는 상황.
            connection.exec_driver_sql("INSERT INTO hs_code (hs_code,hs_ver,name_ko,source_edition) "
                "VALUES ('0101219999','HS2022','공식 사전 이름','OFFICIAL')")
            insert_hs_additions(connection, [("0101219999", "HS2022", "CSV 이름", "CSV")])
        self.assertEqual(self.query("SELECT name_ko,source_edition FROM hs_code WHERE hs_code='0101219999'"),
                         (("공식 사전 이름", "OFFICIAL"),))

    def test_two_connections_add_same_code_without_overwriting_winner(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from backend.app.services.customs_reference import insert_hs_additions
        self.add_parents()
        barrier = Barrier(2)
        def add(name):
            with self.engine.begin() as connection:
                barrier.wait(timeout=10)
                insert_hs_additions(connection, [("0101219999", "HS2022", name, name)])
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(add, name) for name in ("첫 번째", "두 번째")]
            for future in futures:
                future.result(timeout=20)
        rows = self.query("SELECT name_ko,source_edition FROM hs_code WHERE hs_code='0101219999'")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], rows[0][1])
        self.assertIn(rows[0][0], ("첫 번째", "두 번째"))

    def test_enum_migration_preserves_future_values_and_can_repeat(self):
        with self.engine.begin() as connection:
            connection.exec_driver_sql("ALTER TABLE batch_run MODIFY target_table ENUM('export_monthly','future_target') NOT NULL")
            sql = (Path(__file__).resolve().parents[2] / "db/mysql/init/05_customs_batch_target.sql").read_text(encoding="utf-8")
            migration = sql[sql.index('SET @batch_target_type'):]
            for _ in range(2):
                for statement in migration.split(';'):
                    if statement.strip():
                        connection.exec_driver_sql(statement)
            enum = connection.exec_driver_sql("SELECT COLUMN_TYPE FROM information_schema.columns WHERE "
                "table_schema=DATABASE() AND table_name='batch_run' AND column_name='target_table'").scalar_one()
            self.assertIn("'future_target'", enum)
            self.assertIn("'hs_code_correlation'", enum)
            # 다른 테스트를 위해 공통 모델과 같은 ENUM으로 복구한다.
            from backend.app.models.batch import TARGET_TABLES
            values = ','.join("'" + v + "'" for v in TARGET_TABLES)
            connection.exec_driver_sql(f"ALTER TABLE batch_run MODIFY target_table ENUM({values}) NOT NULL")

    def test_missing_dictionary_rejected_before_publication(self):
        with self.assertRaises(ValidationError):
            self.load([row(hsCd="9999990000")])
        self.assertEqual(self.query("SELECT COUNT(*) FROM export_monthly")[0][0], 0)
        self.assertEqual(self.events[0][0], "reference_missing")

    def test_failure_rolls_back_both_directions_and_records_failed(self):
        self.load([row()])
        class FailingLoader(Loader):
            @classmethod
            def verify(cls, connection, direction):
                if direction == "import":
                    raise ValidationError("의도적인 검증 실패")
                return super().verify(connection, direction)
        self.loader = FailingLoader(self.connection, self.loader.log)
        with self.assertRaises(ValidationError):
            self.load([row(expDlr="777", impDlr="888")])
        self.assertEqual(str(self.query("SELECT amount_usd FROM export_monthly")[0][0]), "100.01")
        self.assertEqual(str(self.query("SELECT amount_usd FROM export_summary")[0][0]), "100.01")
        self.assertEqual(str(self.query("SELECT amount_usd FROM import_monthly")[0][0]), "0.00")
        self.assertEqual(self.query("SELECT COUNT(*) FROM batch_run WHERE status='FAILED'")[0][0], 4)

    def test_unchanged_values_keep_derived_fields_changed_history_invalidates(self):
        self.load([row(), row(year="2025.02"), row(year="2025.03")])
        connection = self.connection
        result = connection.exec_driver_sql("UPDATE export_monthly SET history_months=3,baseline_amount_usd=1,median_unit_usd_kg=1,anomaly_code='R2'")
        self.connection.commit()
        self.load([row(year="2025.02")])
        self.assertEqual(self.query("SELECT DISTINCT anomaly_code FROM export_monthly"), (("R2",),))
        self.load([row(year="2025.02", expDlr="200")])
        self.assertEqual(self.query("SELECT DISTINCT anomaly_code FROM export_monthly"), ((None,),))

    def test_parallel_loader_lock_rejected(self):
        import hashlib
        database = self.connection.exec_driver_sql("SELECT DATABASE()").scalar_one()
        lock = "customs_load:" + hashlib.sha256(database.encode()).hexdigest()[:48]
        self.connection.exec_driver_sql("SELECT GET_LOCK(%s,0)", (lock,))
        self.connection.rollback()
        try:
            with self.engine.connect() as other:
                with tempfile.TemporaryDirectory() as tmp, Stage(Path(tmp) / "stage.db") as stage:
                    root = Path(tmp)
                    write_csv(root / "raw.csv", [row()])
                    stage.ingest(root / "raw.csv", 2025, root / "errors.jsonl")
                    with self.assertRaises(ValidationError):
                        Loader(other, self.loader.log).load(stage, "HS2022")
        finally:
            self.connection.exec_driver_sql("SELECT RELEASE_LOCK(%s)", (lock,))
            self.connection.rollback()

    def test_same_observation_in_another_version_rejected(self):
        connection = self.connection
        result = connection.exec_driver_sql("INSERT INTO hs_code (hs_code,hs_ver,name_ko,original_name_ko,source_edition) VALUES ('010121','HS2017','테스트','테스트','TEST FIXTURE'),('0101210000','HS2017','테스트',NULL,'TEST FIXTURE')")
        result = connection.exec_driver_sql("INSERT INTO export_monthly (trade_month,country_code,hs10_code,hs_ver,amount_usd) VALUES ('2025-01-01','NA','0101210000','HS2017',1)")
        self.connection.commit()
        with self.assertRaises(ValidationError):
            self.load([row()])
        self.assertEqual(self.query("SELECT hs_ver FROM export_monthly"), (("HS2017",),))
        self.assertEqual(self.query("SELECT COUNT(*) FROM import_monthly")[0][0], 0)

    def test_committed_month_survives_later_month_failure(self):
        class FebruaryFailure(Loader):
            @classmethod
            def verify(cls, connection, direction):
                result = connection.exec_driver_sql("SELECT MONTH(trade_month) FROM customs_stage LIMIT 1")
                if result.fetchone()[0] == 2:
                    raise ValidationError("2월 검증 실패")
                return super().verify(connection, direction)
        self.loader = FebruaryFailure(self.connection, self.loader.log)
        with self.assertRaises(ValidationError):
            self.load([row(), row(year="2025.02")])
        self.assertEqual(self.query("SELECT trade_month FROM export_monthly")[0][0].isoformat(), "2025-01-01")
        self.assertEqual(self.query("SELECT COUNT(*) FROM export_monthly")[0][0], 1)
        self.assertEqual(self.query("SELECT status,COUNT(*) FROM batch_run GROUP BY status ORDER BY status"),
                         (("SUCCESS", 4), ("FAILED", 4)))

    def test_commit_response_loss_is_reported_with_common_failed_record(self):
        class ResponseLostConnection:
            def __init__(self, connection):
                self.connection = connection
                self.publishing = False
            def __getattr__(self, name):
                return getattr(self.connection, name)
            def begin(self):
                self.publishing = True
                return self.connection.begin()
            def commit(self):
                self.connection.commit()
                if self.publishing:
                    raise RuntimeError("COMMIT 응답 손실 모의")
        self.loader = Loader(ResponseLostConnection(self.connection), self.loader.log)
        with self.assertRaises(RuntimeError):
            self.load([row()])
        self.assertEqual(self.query("SELECT COUNT(*) FROM export_monthly")[0][0], 1)
        # 공통 기록기는 예외가 난 작업을 FAILED로 기록한다. 데이터 상태는 COMMIT_UNKNOWN 로그와 함께 확인한다.
        self.assertEqual(self.query("SELECT DISTINCT status FROM batch_run"), (("FAILED",),))
        self.assertIn("commit_unknown", [event for event, _ in self.events])

    def test_batch_finish_failure_warns_and_keeps_published_data(self):
        from sqlalchemy import event
        def fail_finish(conn, connection, sql, params, context, executemany):
            if sql.startswith("UPDATE batch_run") and any(
                    row.get("status") == "SUCCESS" for row in context.compiled_parameters or []):
                raise RuntimeError("실행 결과 기록 실패 모의")
        event.listen(self.engine, "before_cursor_execute", fail_finish)
        try:
            with self.assertLogs("batch_log", level="WARNING") as messages:
                self.load([row()])
            self.assertEqual(len(messages.output), 4)
        finally:
            event.remove(self.engine, "before_cursor_execute", fail_finish)
        for table in TABLES:
            self.assertEqual(self.query(f"SELECT COUNT(*) FROM {table}"), ((1,),))
        self.assertEqual(self.query("SELECT status,COUNT(*) FROM batch_run GROUP BY status"), (("RUNNING", 4),))

    def test_job_runs_validation_hold_load_and_metrics_on_test_database(self):
        from backend.app.services.customs_metrics import configure_rules
        configure_rules(self.engine, r2_hs_prefixes=["01"])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_csv(root / "raw.csv", [row(), row(year="2025.02", expDlr="200"),
                row(hsCd="9999999290"), row(hsCd="621142900")])
            args = Namespace(csv=root / "raw.csv", year=2025, mode="load", hs_ver="auto",
                output=root / "out", batch_size=1, env=root / "unused.env", source_code="CUSTOMS",
                unmapped_policy="hold", invalid_policy="hold")
            with patch("backend.app.scheduler.jobs.customs_load.mysql_url", return_value=self.engine.url), \
                    patch("builtins.print"):
                self.assertEqual(run(args), 0)
            report = json.loads(next((root / "out").glob("*/report.json")).read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "LOADED_WITH_HOLDS")
            self.assertEqual((report["rows_eligible"], report["rows_held"], report["invalid_rows_held"]), (2, 1, 1))
            self.assertEqual(report["metrics"]["rows"], {"export_monthly": 2, "import_monthly": 2})
            self.assertFalse(report["dictionary_changed"])
        self.assertEqual(self.query("SELECT COUNT(*) FROM hs_code"), ((3,),))
        self.assertEqual(self.query("SELECT status,COUNT(*) FROM batch_run GROUP BY status"), (("SUCCESS", 12),))
        self.assertEqual(tuple(tuple(str(v) for v in record) for record in self.query(
            "SELECT history_months,baseline_amount_usd,anomaly_code FROM export_monthly WHERE trade_month='2025-02-01'")),
            (("1", "100.01", "R2"),))


if __name__ == "__main__":
    unittest.main()
