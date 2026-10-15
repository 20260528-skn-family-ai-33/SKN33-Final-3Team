"""UN HS 목록 대조를 가짜 UN 목록 파일로 확인합니다 (네트워크 없이 실행).

실행 (저장소 루트에서): python -m unittest backend/tests/test_hs_code_un_check.py
"""
import csv
import importlib.util
import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "app/scheduler/collectors/hscode/hs_code_un_check.py"
spec = importlib.util.spec_from_file_location("hs_code_un_check", SRC)
uc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(uc)

UN = [{"id": "TOTAL", "text": "Total", "aggrlevel": 0}, {"id": "01", "text": "01 - Animals; live", "aggrlevel": 2},
      {"id": "0101", "text": "0101 - Horses", "aggrlevel": 4}, {"id": "010121", "text": "010121 - Horses; pure-bred",
                                                                  "aggrlevel": 6},
      {"id": "999999", "text": "999999 - Commodities not specified", "aggrlevel": 6},
      {"id": "010130", "text": "010130 - Asses; live", "aggrlevel": 6}]
CSV_CODES = ["01", "0101", "010121", "242400", "010199", "0101211000"]


class UnCheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        (self.dir / "H6.json").write_text(json.dumps({"classCode": "H6", "className": "HS2022", "results": UN}),
                                          encoding="utf-8")  # 저장된 파일이 있으면 다운로드하지 않음
        self.csv = self.dir / "hs.csv"
        with open(self.csv, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["hs_code", "depth", "name_ko", "source_edition", "hs_ver"])
            w.writerows([[c, len(c), "x", "src", "HS2022"] for c in CSV_CODES])

    def tearDown(self):
        for h in uc.log.handlers:
            h.close()
        uc.log.handlers.clear()
        self.tmp.cleanup()

    def test_compare_splits_known_and_unknown(self):
        un = uc.fetch_un("H6", self.dir)
        codes, ver = uc.read_csv_codes(self.csv)
        self.assertEqual((ver, "0101211000" in codes), ("HS2022", False))  # 10단위는 대조 안 함
        diffs = {(d["side"], d["hs_code"]): d["known"] for d in uc.compare(codes, un)}
        self.assertEqual(diffs, {("CSV에만", "242400"): True, ("CSV에만", "010199"): False,
                                 ("UN에만", "999999"): True, ("UN에만", "010130"): False})

    def test_run_exit_codes_and_report(self):
        self.assertEqual(uc.run(self.csv, save_dir=self.dir, log_dir=self.dir / "logs"), 1)  # 확인 필요 차이 있음
        report = list(csv.DictReader(open(self.dir / "un_check_HS2022_H6.csv", encoding="utf-8-sig")))
        self.assertEqual(len(report), 4)
        # UN에 아직 그 판 목록이 없으면(404) 2
        with mock.patch.object(uc.requests, "get", return_value=mock.Mock(status_code=404)):
            self.assertEqual(uc.run(self.csv, un_class="H7", save_dir=self.dir, log_dir=self.dir / "logs"), 2)

    def test_bad_download_is_not_cached(self):
        # HTTP 200으로 HTML 오류 화면이 와도 캐시에 남기지 않고, 다음 실행에서 다시 받아야 함
        (self.dir / "H6.json").unlink()
        html = mock.Mock(status_code=200, content=b"<html>error</html>", raise_for_status=lambda: None)
        good = mock.Mock(status_code=200, raise_for_status=lambda: None,
                         content=json.dumps({"classCode": "H6", "className": "HS2022", "results": UN}).encode())
        for _ in range(2):
            with mock.patch.object(uc.requests, "get", return_value=html) as get:
                self.assertEqual(uc.run(self.csv, save_dir=self.dir, log_dir=self.dir / "logs"), 2)  # 예외 아닌 대조 불가
            self.assertEqual(get.call_count, 1)  # 두 번째도 다시 받음
            self.assertFalse((self.dir / "H6.json").exists())
        (self.dir / "H6.json").write_text("<html>깨진 캐시</html>", encoding="utf-8")  # 이미 깨진 캐시는 교체
        with mock.patch.object(uc.requests, "get", return_value=good):
            self.assertEqual(uc.run(self.csv, save_dir=self.dir, log_dir=self.dir / "logs"), 1)
        self.assertEqual(uc.parse_un((self.dir / "H6.json").read_bytes(), "H6")[1], "HS2022")

    def test_unknown_hs_version_is_reported(self):
        with open(self.csv, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["hs_code", "depth", "name_ko", "source_edition", "hs_ver"])
            w.writerow(["01", 2, "x", "src", "HS2099"])
        self.assertEqual(uc.run(self.csv, save_dir=self.dir, log_dir=self.dir / "logs"), 2)


if __name__ == "__main__":
    unittest.main()
