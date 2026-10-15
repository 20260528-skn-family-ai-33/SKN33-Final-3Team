"""HS 판 간 대응표 적재를 가상 xlsx와 메모리 SQLite로 확인합니다 (UNSD 다운로드 없음).

실행: python -m unittest backend/tests/test_hs_correlation_load.py
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

from backend.app.db.crud import CRUD
from backend.app.db.session import make_engine, session_scope
from backend.app.models.base import Base
from backend.app.models.hs_code_correlation import HsCodeCorrelation
from backend.app.scheduler.jobs import hs_correlation_load as hc

# UNSD 파일과 같은 모양: 'Between' 줄, 머리글 줄, 데이터
CORR = [["Between", None, None], ["HS2022", "HS2017", "Relationship"],
        ["010121", "010121", "1:1"],
        ["150920", "150910", "n:1"], ["150930", "150910", "n:1"], ["150940", "150910", "n:1"],  # 분할
        ["850131", "850131", "n:n"], ["850131", "850140", "n:n"], ["850140", "850140", "n:n"],  # 얽힌 묶음
        [None, None, None]]


def write_xlsx(path, rows):
    with pd.ExcelWriter(path) as w:
        pd.DataFrame([["From HS2022", "To HS2017"]]).to_excel(w, sheet_name="HS2022-HS2017 Conversions", header=False, index=False)
        pd.DataFrame(rows).to_excel(w, sheet_name="HS2022-HS2017 Correlations", header=False, index=False)


class HsCorrelationLoadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.xlsx = self.dir / "HS2022toHS2017.xlsx"
        write_xlsx(self.xlsx, CORR)

    def tearDown(self):
        for h in hc.log.handlers:
            h.close()
        hc.log.handlers.clear()
        self.tmp.cleanup()

    def test_parse_and_groups(self):
        pairs = hc.parse(self.xlsx, "HS2022", "HS2017")
        self.assertEqual(len(pairs), 7)
        groups = hc.group_codes(pairs)
        self.assertEqual({c: groups[c] for c in ("150920", "150930", "150940")}, dict.fromkeys(("150920", "150930", "150940"), "150920"))
        self.assertEqual((groups["850131"], groups["850140"]), ("850131", "850131"))  # n:n 묶음은 하나의 그룹
        self.assertEqual(groups["010121"], "010121")

    def test_hub_code_not_merged(self):
        # 이전 코드 여러 개와 이어진 신설 코드(8549 같은 허브)는 묶지 않아 다른 그룹을 합치지 않습니다.
        pairs = [("854921", "850131", "n:n"), ("854921", "010121", "n:n"), ("854921", "150910", "n:n"),
                 ("850131", "850131", "1:1"), ("010121", "010121", "1:1")]
        with mock.patch.object(hc, "HUB_FANOUT", 2):
            groups = hc.group_codes(pairs)
        self.assertEqual(groups, {"854921": "854921", "850131": "850131", "010121": "010121"})

    def test_parse_rejects_bad_format(self):
        bad = self.dir / "bad.xlsx"
        write_xlsx(bad, CORR[:2] + [["15092", "150910", "n:1"]])
        with self.assertRaisesRegex(ValueError, "형식 오류"):
            hc.parse(bad, "HS2022", "HS2017")
        with self.assertRaisesRegex(ValueError, "머리글"):
            hc.parse(self.xlsx, "HS2028", "HS2022")
        for cols in (1, 2):  # 열이 모자라면 KeyError가 아니라 ValueError (캐시 복구 분기가 받도록)
            short = self.dir / f"short{cols}.xlsx"
            write_xlsx(short, [row[:cols] for row in CORR])
            with self.assertRaisesRegex(ValueError, "3개 필요"):
                hc.parse(short, "HS2022", "HS2017")

    def test_run_replaces_pairs_of_same_versions(self):
        db = f"sqlite:///{(self.dir / 'hs.db').as_posix()}"
        engine = make_engine(db)
        Base.metadata.create_all(engine)
        with session_scope(engine) as s:  # 원천에서 빠진 이전 대응과 다른 판 쌍의 행
            CRUD(HsCodeCorrelation).upsert_many(s, [
                {"new_ver": "HS2022", "new_code": "999998", "old_ver": "HS2017", "old_code": "999998", "relation": "1:1",
                 "group_code": "999998", "source_edition": "old"},
                {"new_ver": "HS2017", "new_code": "150910", "old_ver": "HS2012", "old_code": "150910", "relation": "1:1",
                 "group_code": "150910", "source_edition": "other"}])
        self.assertEqual(hc.run("HS2022", "HS2017", xlsx=self.xlsx, db_url=db, log_dir=self.dir, track=False), 0)
        with session_scope(engine) as s:
            corr = CRUD(HsCodeCorrelation)
            self.assertEqual(corr.count(s, new_ver="HS2022", old_ver="HS2017"), 7)
            self.assertIsNone(corr.get(s, ("HS2022", "999998", "HS2017", "999998")))  # 빠진 대응은 지워짐
            self.assertIsNotNone(corr.get(s, ("HS2017", "150910", "HS2012", "150910")))  # 다른 판 쌍은 그대로
            self.assertEqual(corr.get(s, ("HS2022", "150930", "HS2017", "150910")).group_code, "150920")
        engine.dispose()

    def test_bad_download_not_cached_and_bad_cache_redownloaded(self):
        class Res:
            def __init__(self, content):
                self.status_code, self.content, self.headers = 200, content, {}

            def raise_for_status(self):
                pass

        save = self.dir / "corr"
        good = self.xlsx.read_bytes()
        with mock.patch.object(hc.requests, "get", side_effect=[Res(b"PKbroken"), Res(good)]) as get:
            with self.assertRaisesRegex(ValueError, "xlsx로 읽지 못했습니다"):
                hc.fetch_pairs("HS2022", "HS2017", save)
            self.assertEqual(list(save.iterdir()), [])  # 손상 파일은 캐시에 남지 않음
            pairs, downloaded = hc.fetch_pairs("HS2022", "HS2017", save)  # 다음 실행에서 다시 받음
            self.assertEqual((len(pairs), downloaded, get.call_count), (7, True, 2))
        (save / "HS2022toHS2017.xlsx").write_bytes(b"PKbroken")  # 받아 둔 파일이 손상된 경우
        with mock.patch.object(hc.requests, "get", return_value=Res(good)) as get:
            self.assertEqual(hc.fetch_pairs("HS2022", "HS2017", save)[1], True)
            self.assertEqual(hc.fetch_pairs("HS2022", "HS2017", save)[1], False)  # 고친 캐시는 다시 받지 않음
            self.assertEqual(get.call_count, 1)

    def test_run_fails_without_table(self):
        db = f"sqlite:///{(self.dir / 'empty.db').as_posix()}"
        self.assertEqual(hc.run("HS2022", "HS2017", xlsx=self.xlsx, db_url=db, log_dir=self.dir, track=False), 1)


if __name__ == "__main__":
    unittest.main()
