"""공통 CRUD와 HS 코드 적재를 메모리 SQLite로 확인합니다 (MySQL 없이 실행).

실행 (저장소 루트에서): python -m unittest backend/tests/test_db_crud.py
"""
import csv
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.db.crud import CRUD  # noqa: E402
from backend.app.db.session import make_engine, session_scope  # noqa: E402
from backend.app.models.base import Base  # noqa: E402
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
        with session_scope(self.engine) as s:
            self.hs.create(s, hs_code="0101211000", name_ko="농가 사육용", hs_ver="HS2022")
        with session_scope(self.engine) as s:
            obj = self.hs.get(s, "0101211000")
            self.assertEqual((obj.depth, obj.name_ko), (10, "농가 사육용"))  # depth는 DB가 계산
            self.hs.update(s, "0101211000", name_ko="농가용")
            with self.assertRaises(ValueError):
                self.hs.update(s, "0101211000", depth=6)  # 계산 컬럼은 수정 불가
        with session_scope(self.engine) as s:
            self.assertEqual(self.hs.get(s, "0101211000").name_ko, "농가용")
            self.assertTrue(self.hs.delete(s, "0101211000"))
            self.assertFalse(self.hs.delete(s, "0101211000"))

    def test_upsert_list_count(self):
        with session_scope(self.engine) as s:
            self.assertEqual(self.hs.upsert_many(s, ROWS), 3)
            self.hs.upsert_many(s, [{**ROWS[1], "name_ko": "말"}])  # 같은 키 → 갱신
        with session_scope(self.engine) as s:
            self.assertEqual(self.hs.count(s), 3)
            self.assertEqual(self.hs.get(s, "0101").name_ko, "말")
            self.assertEqual([r.hs_code for r in self.hs.list(s, depth=6)], ["010121"])


class HsCodeLoadTest(unittest.TestCase):
    def test_validate_catches_bad_rows(self):
        bad = [{**ROWS[0], "hs_code": "123", "depth": "3"}, {**ROWS[1], "depth": "6"}, {**ROWS[2], "name_ko": ""},
               ROWS[1], {**ROWS[0], "hs_code": "02", "name_ko": "가" * 501}]
        errors = hs_code_load.validate(bad)
        self.assertEqual(len(errors), 5)
        self.assertEqual(hs_code_load.validate(ROWS), [])

    def test_load_csv_keeps_leading_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hs.csv"
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=hs_code_load.COLUMNS)
                writer.writeheader()
                writer.writerows(ROWS)
            engine = make_engine("sqlite://")
            self.assertEqual(hs_code_load.load(engine, hs_code_load.read_rows(path)), 3)
            with session_scope(engine) as s:
                self.assertEqual(CRUD(HsCodeMst).get(s, "010121").depth, 6)


if __name__ == "__main__":
    unittest.main()
