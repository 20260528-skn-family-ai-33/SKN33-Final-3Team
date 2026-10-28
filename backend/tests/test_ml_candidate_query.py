"""후보 모델 조회의 입력 해석·순위·캐시 (국가 시드 SQL과 가상 표만 읽음).

실행: python -m unittest backend.tests.test_ml_candidate_query
"""
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from backend.app.ml import candidate_query as Q
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


class QueryTest(unittest.TestCase):
    def test_old_enriched_cache_is_not_used_for_snapshot_predictions(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / "snap.pkl"
            frame = pd.DataFrame({"anchor": pd.to_datetime(["2026-08-01", "2026-09-01"]),
                                  "country_code": ["VN", "VN"], "mkt_growth": [float("nan"), float("nan")]})
            frame.to_pickle(snapshot)
            legacy = snapshot.with_name("snap_latest.pkl")
            frame.iloc[[1]].assign(mkt_growth=8.).to_pickle(legacy)
            later = time.time() + 10
            os.utime(legacy, (later, later))  # 이전 캐시가 더 최신이어도 원본으로 다시 만든다
            with patch.object(Q, "load_snapshot", wraps=Q.load_snapshot) as load:
                actual = Q.latest_rows(snapshot)
            load.assert_called_once_with(snapshot)
            pd.testing.assert_frame_equal(actual, frame.iloc[[1]])
            self.assertEqual(pd.read_pickle(legacy).mkt_growth.iloc[0], 8.)  # 이전 캐시를 덮어쓰지 않는다

    def test_hs6_member_code_maps_to_comparison_code(self):
        mapping = {("HS2022", "850761"): "850760"}
        self.assertEqual(Q.comparison_hs6("850761", mapping), "850760")
        self.assertEqual(Q.comparison_hs6("030211", mapping), "030211")  # 묶이지 않은 코드는 그대로

    def test_correspondence_table_is_read_once(self):
        corr = pd.DataFrame({"new_code": ["850761"], "old_code": ["850760"], "group_code": ["850760"]})
        Q._hs_mapping.cache_clear()
        try:
            with patch.object(Q.S, "hs_correlation", return_value=corr) as read:
                self.assertEqual(Q.comparison_hs6("850761"), "850760")
                self.assertEqual(Q.comparison_hs6("030211"), "030211")
            read.assert_called_once()
        finally:
            Q._hs_mapping.cache_clear()  # 다른 테스트가 가짜 대응표를 쓰지 않게

    def test_resolve_target_stops_even_if_error_returns(self):
        countries = S.countries()
        countries = countries[countries.country_role == "COUNTRY"]
        messages = []
        for country, hs6 in (("없는나라", None), ("VN", "8507600000")):
            with self.subTest(country=country, hs6=hs6), self.assertRaises(ValueError):
                Q.resolve_target(country, hs6, countries, messages.append)  # 기록만 하고 돌아오는 error
        self.assertEqual(len(messages), 2)

    def test_pair_rank_counts_higher_scores(self):
        pred = pd.DataFrame({"country_code": ["VN", "VN", "VN", "US"], "hs6": ["111111", "222222", "333333", "222222"],
                             "score": [.9, .5, .1, .7]})
        rank = Q.pair_rank(pred, "VN", "222222")
        self.assertEqual((rank["country_rank"], rank["country_size"]), (2, 3))
        self.assertEqual((rank["product_rank"], rank["product_size"]), (2, 2))
        self.assertIsNone(Q.pair_rank(pred, "VN", "999999"))

    def test_latest_cache_is_reused_until_snapshot_changes(self):
        folder = Path(tempfile.mkdtemp())
        snapshot = folder / "snap.pkl"
        frame = pd.DataFrame({"anchor": pd.to_datetime(["2026-08-01", "2026-09-01"]), "country_code": ["VN", "VN"]})
        frame.to_pickle(snapshot)
        self.assertEqual(len(Q.latest_rows(snapshot)), 1)  # 최신 기준월만
        with patch.object(Q, "load_snapshot", side_effect=AssertionError("캐시를 써야 함")):
            self.assertEqual(Q.latest_rows(snapshot).anchor.max(), pd.Timestamp("2026-09-01"))
        later = time.time() + 10
        os.utime(snapshot, (later, later))  # 스냅샷 갱신
        with patch.object(Q, "load_snapshot", wraps=Q.load_snapshot) as load:
            Q.latest_rows(snapshot)
        load.assert_called_once()

    def test_invalid_top_or_long_hs_code_is_refused(self):
        for argv in (["--country", "VN", "--top", "0"], ["--hs6", "8507600000"]):
            with self.subTest(argv=argv), patch("sys.argv", ["candidate_query", *argv]), patch("sys.stderr"), \
                    patch.object(Q.joblib, "load", side_effect=AssertionError("모델을 읽기 전에 멈춰야 함")), \
                    self.assertRaises(SystemExit):
                Q.main()


if __name__ == "__main__":
    unittest.main()
