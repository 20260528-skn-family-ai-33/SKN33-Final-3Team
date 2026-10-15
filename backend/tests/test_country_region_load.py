"""대륙·하위 지역 적재를 실제 CSV와 메모리 SQLite로 확인합니다.

실행: python -m unittest backend/tests/test_country_region_load.py
"""
import unittest

from sqlalchemy import select

from backend.app.db.session import make_engine, session_scope
from backend.app.models.base import Base
from backend.app.models.region import Country, MarketRegion
from backend.app.scheduler.jobs import country_region_load as cr
from backend.app.services import country_region as ref


class CountryRegionLoadTest(unittest.TestCase):
    def test_reference_csv_valid(self):
        regions, mapping, errors = ref.load_reference()
        self.assertEqual(errors, [])
        self.assertEqual(sum(r["region_level"] == 1 for r in regions), 5)
        self.assertEqual(mapping["VN"], 35)  # 동남아시아
        self.assertEqual(mapping["US"], 21)  # 북아메리카
        self.assertEqual((ref.region_id_of("VN"), ref.region_id_of("AQ")), (35, None))

    def test_bad_region_name_rejected(self):
        _, _, errors = ref.build([{"region_id": "142", "parent_id": "", "region_level": "1", "name_ko": "아시아"}],
                                [{"country_code": "VN", "region_name": "아시아"}])  # 대륙에는 직접 매핑 불가
        self.assertEqual(len(errors), 1)

    def test_load_sqlite(self):
        engine = make_engine("sqlite://")
        Base.metadata.create_all(engine, tables=[MarketRegion.__table__, Country.__table__])
        with session_scope(engine) as s:
            s.add_all([Country(country_code="VN", name_ko="베트남"), Country(country_code="AQ", name_ko="남극")])
        regions, mapping, _ = ref.load_reference()
        for _ in range(2):  # 다시 실행해도 같은 결과
            updated, unmapped = cr.load(engine, regions, mapping)
        self.assertEqual((updated, unmapped), (1, ["AQ"]))
        with session_scope(engine) as s:
            self.assertEqual(s.scalar(select(Country.region_id).where(Country.country_code == "VN")), 35)
            self.assertEqual(s.scalar(select(MarketRegion.parent_id).where(MarketRegion.region_id == 35)), 142)


if __name__ == "__main__":
    unittest.main()
