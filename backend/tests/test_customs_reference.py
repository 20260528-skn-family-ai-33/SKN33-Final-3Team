"""SQL 국가 사전 조회·기존 HS 사전의 판본 연결·원문 보류를 검증한다."""
import csv
import tempfile
import unittest
from backend.app.db.session import make_engine
from pathlib import Path

from backend.app.services.customs_reference import load_country_references, resolve_hs_versions, plan_hs_references
from backend.app.services.customs_stage import Stage, ValidationError


class CountryReferenceTest(unittest.TestCase):
    def setUp(self):
        self.engine = make_engine("sqlite://")
        with self.engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE market_region (region_id INTEGER PRIMARY KEY,parent_id INTEGER,region_level INTEGER,name_ko TEXT)")
            connection.exec_driver_sql("CREATE TABLE currency (currency_code TEXT PRIMARY KEY,name_ko TEXT)")
            connection.exec_driver_sql("CREATE TABLE country (country_code TEXT PRIMARY KEY,name_ko TEXT,country_role TEXT,region_id INTEGER,currency_code TEXT)")
            connection.exec_driver_sql("INSERT INTO market_region VALUES (2,NULL,1,'아프리카'),(18,2,2,'남아프리카')")
            connection.exec_driver_sql("INSERT INTO currency VALUES ('USD','미국 달러')")
            connection.exec_driver_sql("INSERT INTO country VALUES ('NA','DB 국가명','COUNTRY',18,'USD'),('ZZ','특수','SPECIAL',NULL,NULL),('TOTAL','총계','TOTAL',NULL,NULL)")

    def tearDown(self):
        self.engine.dispose()

    def test_names_regions_and_currency_come_from_database(self):
        with self.engine.connect() as connection:
            references = load_country_references(connection)
        self.assertEqual(set(references), {"NA", "ZZ"})
        self.assertEqual(references["NA"]["name_ko"], "DB 국가명")
        self.assertEqual(references["NA"]["region_name_ko"], "남아프리카")
        self.assertEqual(references["NA"]["continent_name_ko"], "아프리카")
        self.assertEqual(references["NA"]["currency_name_ko"], "미국 달러")
        self.assertIsNone(references["ZZ"]["region_id"])
        self.assertIsNone(references["ZZ"]["currency_code"])

    def test_missing_parent_or_currency_is_rejected(self):
        for statement in ("DELETE FROM market_region WHERE region_id=2", "DELETE FROM currency"):
            with self.subTest(statement=statement):
                with self.engine.connect() as connection:
                    connection.exec_driver_sql(statement)
                    with self.assertRaises(ValidationError):
                        load_country_references(connection)
                    connection.rollback()

    def test_empty_dictionary_is_not_rebuilt_from_csv(self):
        with self.engine.begin() as connection:
            connection.exec_driver_sql("DELETE FROM country")
        with self.engine.connect() as connection, self.assertRaises(ValidationError):
            load_country_references(connection)


class HsResolutionTest(unittest.TestCase):
    pairs = {("0101210000", "HS2022"), ("010121", "HS2022"),
             ("3402201000", "HS2017"), ("340220", "HS2017")}

    def test_explicit_version_does_not_reclassify_legacy_code(self):
        versions, missing = resolve_hs_versions({"0101210000", "3402201000"}, self.pairs, "HS2022")
        self.assertEqual(versions, {"0101210000": "HS2022"})
        self.assertEqual(set(missing), {"3402201000"})

    def test_auto_follows_unique_dictionary_versions(self):
        versions, missing = resolve_hs_versions({"0101210000", "3402201000"}, self.pairs, "auto")
        self.assertEqual(versions, {"0101210000": "HS2022", "3402201000": "HS2017"})
        self.assertEqual(missing, {})

    def test_parent_must_exist_in_same_version(self):
        pairs = {("0101210000", "HS2022"), ("010121", "HS2017")}
        for mode in ("auto", "HS2022"):
            versions, missing = resolve_hs_versions({"0101210000"}, pairs, mode)
            self.assertEqual(versions, {})
            self.assertIn("동일 판본 HS6", missing["0101210000"])

    def test_ambiguous_code_is_not_selected_by_parent_availability(self):
        pairs = self.pairs | {("0101210000", "HS2017")}
        versions, missing = resolve_hs_versions({"0101210000"}, pairs, "auto")
        self.assertEqual(versions, {})
        self.assertIn("복수", missing["0101210000"])
        self.assertEqual(resolve_hs_versions({"0101210000"}, pairs, "HS2022")[1], {})

    def test_unknown_code_is_reported_without_dictionary_mutation(self):
        pairs = set(self.pairs)
        versions, missing = resolve_hs_versions({"9999999290"}, pairs, "auto")
        self.assertFalse(versions)
        self.assertIn("HS10 사전 누락", missing["9999999290"])
        self.assertEqual(pairs, self.pairs)
        with self.assertRaises(ValidationError):
            resolve_hs_versions(set(), pairs, "hs2022")


class SupplementPlanTest(unittest.TestCase):
    parents = {(c, "HS2022") for c in ("01", "0101", "010121")}

    def plan(self, name="실적 원문", pairs=None, requested="auto"):
        from unittest.mock import MagicMock
        stage = MagicMock()
        stage.references.return_value = ({"NA"}, {"0101210000", "010121"})
        stage.hs_sources.return_value = {"0101210000": (name, "2023-02-01")} if name else {}
        return plan_hs_references(stage, self.parents if pairs is None else pairs, requested)

    def test_auto_uses_all_three_parents_and_preserves_leading_zero(self):
        versions, missing, additions = self.plan()
        self.assertEqual(versions, {"0101210000": "HS2022"})
        self.assertFalse(missing)
        self.assertEqual(additions["0101210000"],
            ("0101210000", "HS2022", "실적 원문", "관세청 수출입실적 2023년 2월"))

    def test_missing_one_parent_or_invalid_name_is_not_added(self):
        for pairs, name in ((self.parents - {("01", "HS2022")}, "이름"), (self.parents, None)):
            versions, missing, additions = self.plan(pairs=pairs, name=name)
            self.assertFalse(versions)
            self.assertIn("0101210000", missing)
            self.assertFalse(additions)

    def test_ambiguous_parents_are_held_but_explicit_version_is_allowed(self):
        pairs = self.parents | {(c, "HS2017") for c in ("01", "0101", "010121")}
        self.assertIn("0101210000", self.plan(pairs=pairs)[1])
        self.assertEqual(self.plan(pairs=pairs, requested="HS2017")[0], {"0101210000": "HS2017"})

    def test_existing_hs10_needs_no_csv_name_and_is_not_replaced(self):
        pairs = self.parents | {("0101210000", "HS2022")}
        versions, missing, additions = self.plan(pairs=pairs, name=None)
        self.assertEqual(versions, {"0101210000": "HS2022"})
        self.assertFalse(missing or additions)


class HoldTest(unittest.TestCase):
    def prepare(self, root):
        path = root / "raw.csv"
        # 원문에 있는 추가 컬럼, 선행 0과 공백 중량도 보존해야 한다.
        path.write_text("year,statCd,hsCd,expDlr,expWgt,impDlr,impWgt,statKor\n"
            "2025.01,NA,0101210000,1,0,0,,원문 A\n"
            "2025.01,NA,0101290000,2,0,0,,원문 B\n", encoding="utf-8-sig")
        stage = Stage(root / "stage.db")
        stage.ingest(path, 2025, root / "errors.jsonl")
        return path, stage

    def test_only_unmapped_rows_held_original_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path, stage = self.prepare(root)
            with stage:
                report = stage.hold_unmapped_hs(path, 2025, root / "held.csv", {"0101290000": "HS10 사전 누락"})
                self.assertEqual(report["rows_held"], 1)
                self.assertEqual(stage.summary()[0]["export_usd"], "1.00")
                with (root / "held.csv").open(encoding="utf-8-sig", newline="") as stream:
                    held = list(csv.DictReader(stream))
                self.assertEqual(held[0]["hsCd"], "0101290000")
                self.assertEqual(held[0]["impWgt"], "")
                self.assertEqual(held[0]["statKor"], "원문 B")
                self.assertEqual(held[0]["_hold_record"], "2")
                # 사전 보완 후 보류 CSV 자체를 기존 적재 CLI로 재처리할 수 있다.
                with Stage(root / "held.db") as retry:
                    self.assertEqual(retry.ingest(root / "held.csv", 2025, root / "retry_errors.jsonl")["rows_valid"], 1)

    def test_missing_parent_holds_and_all_held_cannot_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path, stage = self.prepare(root)
            with stage, self.assertRaises(ValidationError):
                stage.hold_unmapped_hs(path, 2025, root / "held.csv", {"0101210000": "동일 판본 HS6 사전 누락", "0101290000": "동일 판본 HS6 사전 누락"})
            with (root / "held.csv").open(encoding="utf-8-sig", newline="") as stream:
                self.assertEqual(len(list(csv.DictReader(stream))), 2)

    def test_changed_source_keeps_stage_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path, stage = self.prepare(root)
            path.write_text(path.read_text(encoding="utf-8-sig").replace(",2,0,", ",9,0,"), encoding="utf-8-sig")
            with stage:
                with self.assertRaises(ValidationError):
                    stage.hold_unmapped_hs(path, 2025, root / "held.csv", {"0101290000": "HS10 사전 누락"})
                self.assertEqual(stage.summary()[0]["rows"], 2)

    def test_summary_keeps_same_hs6_in_different_versions_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path, stage = self.prepare(root)
            # 두 HS10의 앞 6자리를 같게 하되 판본별 집계는 분리한다.
            stage.close()
            path.write_text(path.read_text(encoding="utf-8-sig").replace("0101290000", "0101211000"),
                            encoding="utf-8-sig")
            with Stage(root / "versions.db") as stage:
                stage.ingest(path, 2025, root / "version_errors.jsonl")
                stage.hs_versions = {"0101210000": "HS2022", "0101211000": "HS2017"}
                self.assertEqual(stage.summary()[0]["hs6_groups"], 2)
                self.assertEqual(stage.summary()[0]["export_usd"], "3.00")


if __name__ == "__main__":
    unittest.main()
