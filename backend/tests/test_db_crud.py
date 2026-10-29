"""공통 CRUD와 HS 코드 적재를 메모리 SQLite로 확인합니다 (MySQL 없이 실행).

실행 (저장소 루트에서): python -m unittest backend/tests/test_db_crud.py
"""
import csv
import logging
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sqlalchemy.exc import OperationalError

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.db.crud import CRUD  # noqa: E402
from backend.app.db.session import make_engine, session_scope  # noqa: E402
from backend.app.models.base import Base  # noqa: E402
from backend.app.models.batch import BatchRun  # noqa: E402
from backend.app.models.hs_code import HsCodeMst  # noqa: E402
from backend.app.scheduler.jobs import hs_code_load  # noqa: E402

ROWS = [
    {"hs_code": "01", "depth": "2", "name_ko": "제1류 살아 있는 동물", "source_edition": "src", "hs_ver": "HS2022"},
    {"hs_code": "0101", "depth": "4", "name_ko": "살아 있는 말", "source_edition": "src", "hs_ver": "HS2022"},
    {"hs_code": "010121", "depth": "6", "name_ko": "번식용 말|번식용", "source_edition": "src", "hs_ver": "HS2022"},
]


class CrudTest(unittest.TestCase):
    def setUp(self):
        self.engine = make_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.hs = CRUD(HsCodeMst)

    def test_create_get_update_delete(self):
        key = ("0101211000", "HS2022")  # 기본키 (hs_code, hs_ver)
        with session_scope(self.engine) as s:
            self.hs.create(s, hs_code=key[0], hs_ver=key[1], name_ko="농가 사육용", source_edition="src")
        with session_scope(self.engine) as s:
            obj = self.hs.get(s, key)
            self.assertEqual((obj.depth, obj.name_ko), (10, "농가 사육용"))  # depth는 DB가 계산
            self.hs.update(s, key, name_ko="농가용")
            with self.assertRaises(ValueError):
                self.hs.update(s, key, depth=6)  # 계산 컬럼은 수정 불가
        with session_scope(self.engine) as s:
            self.assertEqual(self.hs.get(s, key).name_ko, "농가용")
            self.assertTrue(self.hs.delete(s, key))
            self.assertFalse(self.hs.delete(s, key))

    def test_upsert_list_count(self):
        db_rows = [hs_code_load.to_db_row(r) for r in ROWS]
        with session_scope(self.engine) as s:
            self.assertEqual(self.hs.upsert_many(s, db_rows), 3)
            self.hs.upsert_many(s, [{**db_rows[1], "name_ko": "말"}])  # 같은 키 → 갱신
        with session_scope(self.engine) as s:
            self.assertEqual(self.hs.count(s), 3)
            self.assertEqual(self.hs.get(s, ("0101", "HS2022")).name_ko, "말")
            self.assertEqual([r.hs_code for r in self.hs.list(s, depth=6)], ["010121"])

    def test_upsert_rows_with_different_columns(self):
        base = {"hs_ver": "HS2022", "name_ko": "가", "source_edition": "src"}
        rows = [{**base, "hs_code": "01"}, {**base, "hs_code": "010121", "original_name_ko": "번식용"}]
        for ordered in (rows, rows[::-1]):  # 어느 순서든 값이 빠지거나 오류가 나면 안 됨
            engine = make_engine("sqlite://")
            Base.metadata.create_all(engine)
            with session_scope(engine) as s:
                self.hs.upsert_many(s, ordered)
            with session_scope(engine) as s:
                got = (self.hs.get(s, ("01", "HS2022")).original_name_ko,
                       self.hs.get(s, ("010121", "HS2022")).original_name_ko)
                self.assertEqual(got, (None, "번식용"))

    def test_upsert_keeps_last_input_for_same_key(self):
        # 컬럼 구성이 first·last와 middle이 다름 → 순서가 바뀌면 middle이 남던 문제
        k = {"hs_code": "0101211000", "hs_ver": "HS2022", "source_edition": "s"}
        with session_scope(self.engine) as s:
            self.hs.upsert_many(s, [{**k, "name_ko": "first", "original_name_ko": None}, {**k, "name_ko": "middle"},
                                    {**k, "name_ko": "last", "original_name_ko": None}])
        with session_scope(self.engine) as s:
            self.assertEqual(self.hs.get(s, ("0101211000", "HS2022")).name_ko, "last")


class HsCodeLoadTest(unittest.TestCase):
    def test_validate_catches_bad_rows(self):
        bad = [{**ROWS[0], "hs_code": "123", "depth": "3"}, {**ROWS[1], "depth": "6"}, ROWS[1],
               {**ROWS[0], "hs_code": "02", "name_ko": "가" * 501}, {**ROWS[0], "hs_code": "03", "hs_ver": "hs2022"},
               {**ROWS[0], "hs_code": "04", "source_edition": ""}, {**ROWS[0], "hs_code": "05", "name_ko": "가|나"}]
        self.assertEqual(len(hs_code_load.validate(bad)), 7)
        # 6단위: 구분기호 없음(개수 오류 + 원문 없음), 요약 빈 값, 원문 빈 값
        six = [{**ROWS[2], "name_ko": "번식용 말"}, {**ROWS[2], "hs_code": "010122", "name_ko": "|번식용"},
               {**ROWS[2], "hs_code": "010123", "name_ko": "번식용 말|"}]
        self.assertEqual(len(hs_code_load.validate(six)), 4)
        unicode_digits = [{**ROWS[0], "hs_code": "¹²", "depth": "2"}, {**ROWS[0], "hs_code": "٠١", "depth": "2"}]
        self.assertEqual(len(hs_code_load.validate(unicode_digits)), 2)  # ASCII 0~9만 허용
        self.assertEqual(hs_code_load.validate(ROWS), [])

    def test_validate_enforces_hs6_summary_rule(self):
        long_name = [*ROWS[:2], {**ROWS[2], "name_ko": "가" * 21 + "|번식용"}]
        dup = [*ROWS, {**ROWS[2], "hs_code": "010129", "name_ko": "번식용 말|기타"}]
        other_ver = [*ROWS, {**ROWS[2], "hs_ver": "HS2017"}]  # 판이 다르면 같은 요약 허용
        self.assertEqual(len(hs_code_load.validate(long_name)), 1)
        self.assertEqual(len(hs_code_load.validate(dup)), 1)
        self.assertEqual(hs_code_load.validate(other_ver), [])

    def test_load_csv_keeps_leading_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hs.csv"
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=hs_code_load.COLUMNS)
                writer.writeheader()
                writer.writerows(ROWS)
            engine = make_engine("sqlite://")
            with self.assertRaises(hs_code_load.TableMissingError):  # 테이블은 만들지 않음
                hs_code_load.load(engine, hs_code_load.read_rows(path))
            Base.metadata.create_all(engine)
            self.assertEqual(hs_code_load.load(engine, hs_code_load.read_rows(path)), 3)
            with session_scope(engine) as s:
                obj = CRUD(HsCodeMst).get(s, ("010121", "HS2022"))
                self.assertEqual((obj.depth, obj.name_ko, obj.original_name_ko), (6, "번식용 말", "번식용"))
                self.assertIsNone(CRUD(HsCodeMst).get(s, ("0101", "HS2022")).original_name_ko)

    def test_load_rolls_back_when_count_fails(self):
        # 행 수 세기가 실패하면 '롤백' 안내와 실제 결과가 같아야 함 (저장된 채 남으면 안 됨)
        engine = make_engine("sqlite://")
        Base.metadata.create_all(engine)
        with mock.patch.object(CRUD, "count", side_effect=OperationalError("SELECT", {}, Exception("x"))):
            with self.assertRaises(OperationalError):
                hs_code_load.load(engine, ROWS)
        with session_scope(engine) as s:
            self.assertEqual(s.query(HsCodeMst).count(), 0)
        engine.dispose()

    def test_run_logs_result_and_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            good, bad = tmp / "good.csv", tmp / "bad.csv"
            for path, rows in ((good, ROWS), (bad, [{**ROWS[0], "hs_code": "1"}])):
                with open(path, "w", encoding="utf-8-sig", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=hs_code_load.COLUMNS)
                    writer.writeheader()
                    writer.writerows(rows)
            db = f"sqlite:///{(tmp / 'hs.db').as_posix()}"
            cases = [
                (tmp / "none.csv", db, "적재 실패: [Errno 2]"),          # CSV 없음
                (bad, db, "적재 실패: 검증 오류"),                       # 검증 오류
                (good, db, "적재 실패: sqlite:///"),                    # 테이블 없음
                (good, "mysql+pymysql://u:p@127.0.0.1:1/x", "적재 실패: DB 오류"),  # DB 연결 실패
            ]
            for csv_path, url, expected in cases:
                log_dir = tmp / f"logs_{len(list(tmp.iterdir()))}"
                self.assertEqual(hs_code_load.run(csv_path, url, log_dir), 1)
                self.assertIn(expected, next(log_dir.glob("*.log")).read_text(encoding="utf-8"))
            engine = make_engine(db)
            Base.metadata.create_all(engine)
            engine.dispose()
            self.assertEqual(hs_code_load.run(good, db, tmp / "logs_ok"), 0)
            self.assertIn("적재 완료: 3행", next((tmp / "logs_ok").glob("*.log")).read_text(encoding="utf-8"))
            self.assertEqual(hs_code_load.run(bad, db, tmp / "logs_bad"), 1)  # 검증 오류도 FAILED로 기록
            engine = make_engine(db)
            with session_scope(engine) as s:  # 단독 실행은 실행마다 batch_run 한 건 (HS2022 기간)
                runs = [(r.job_name, str(r.period_start), str(r.period_end), r.status, r.received_count, r.request_count)
                        for r in s.query(BatchRun).order_by(BatchRun.run_id)]
            engine.dispose()
            self.assertEqual(runs, [("HS_CODE_LOAD", "2022-01-01", "2027-12-31", "SUCCESS", 3, 0),
                                    ("HS_CODE_LOAD", "2022-01-01", "2027-12-31", "FAILED", 1, 0)])
            for h in logging.getLogger("hs_code_load").handlers:  # 임시 폴더를 지우기 전에 로그 파일 닫기
                h.close()


if __name__ == "__main__":
    unittest.main()
