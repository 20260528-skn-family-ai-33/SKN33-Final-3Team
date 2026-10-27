"""후보 모델 조회의 국가 입력 해석 (국가 시드 SQL만 읽음).

실행: python -m unittest backend.tests.test_ml_candidate_query
"""
import unittest

from backend.app.ml import sources as S
from backend.app.ml.candidate_query import country_code


class CountryCodeTest(unittest.TestCase):
    def setUp(self):
        countries = S.countries()
        self.countries = countries[countries.country_role == "COUNTRY"]

    def test_code_name_and_unique_part(self):
        self.assertEqual(country_code("vn", self.countries), "VN")
        self.assertEqual(country_code("베트남", self.countries), "VN")
        self.assertEqual(country_code("기니", self.countries), "GN")  # 정확히 같은 이름이 '적도 기니'보다 먼저

    def test_ambiguous_or_unknown_name_is_refused(self):
        with self.assertRaisesRegex(ValueError, "여러 개"):
            country_code("아", self.countries)
        with self.assertRaisesRegex(ValueError, "찾지 못했"):
            country_code("없는나라", self.countries)


if __name__ == "__main__":
    unittest.main()
