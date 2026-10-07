"""관세청 수집 코드의 파싱·원본 보존·통계를 가상 API 응답으로 확인합니다.

실행: python -m unittest backend/tests/test_customs_collect.py
"""
import importlib.util
import tempfile
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "app/scheduler/collectors/customs/customs_collect.py"
spec = importlib.util.spec_from_file_location("customs_collect", SRC)
cc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cc)

OK_XML = """<?xml version="1.0" encoding="UTF-8"?>
<response><header><resultCode>00</resultCode><resultMsg>NORMAL SERVICE.</resultMsg></header>
<body><items>
<item><balPayments>502244</balPayments><expDlr>807248</expDlr><expWgt>13596</expWgt><hsCd>3304101000</hsCd>
<impDlr>305004</impDlr><impWgt>3139</impWgt><statCd>US</statCd><statCdCntnKor1>미국</statCdCntnKor1>
<statKor>립스틱
</statKor><year>2025.01</year></item>
<item><balPayments>-290</balPayments><expDlr>0</expDlr><expWgt>0</expWgt><hsCd>4202299000</hsCd>
<impDlr>290</impDlr><impWgt>1</impWgt><statCd>US</statCd><statCdCntnKor1>미국</statCdCntnKor1>
<statKor>기타</statKor><year>2025.02</year></item>
<item><balPayments>501954</balPayments><expDlr>807248</expDlr><expWgt>13596</expWgt><hsCd>-</hsCd>
<impDlr>305294</impDlr><impWgt>3140</impWgt><statCd>-</statCd><statCdCntnKor1>-</statCdCntnKor1>
<statKor>-</statKor><year>총계</year></item>
</items></body></response>""".encode("utf-8")


class CustomsCollectTest(unittest.TestCase):
    def test_parse_keeps_raw_values(self):
        rows = cc.parse_response(OK_XML)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["hsCd"], "3304101000")
        self.assertEqual(rows[0]["statKor"], "립스틱\n")  # 원본 줄바꿈 보존
        self.assertEqual(rows[2]["year"], "총계")  # 총계 행 보존

    def test_error_responses(self):
        with self.assertRaises(ValueError):
            cc.parse_response(b"<response><header><resultCode>30</resultCode></header></response>")
        with self.assertRaises(ValueError):
            cc.parse_response(b"<OpenAPI_ServiceResponse><cmmMsgHeader><returnReasonCode>22"
                              b"</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>")

    def test_merge_and_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            cc.write_csv(cache / "US.csv", cc.parse_response(OK_XML))
            cc.write_csv(cache / "NA.csv", [])  # 거래 없는 국가 (나미비아 코드 NA)
            rows = cc.merge(["US", "NA"], cache, cache / "raw.csv")
            self.assertEqual(cc.read_csv(cache / "raw.csv"), rows)
            self.assertEqual(rows[0]["statKor"], "립스틱\n")
            stats = cc.summarize(rows, 2025, ["US", "NA"], [])
        self.assertEqual((stats["rows_total"], stats["rows_data"], stats["rows_total_line"]), (3, 2, 1))
        self.assertEqual(stats["months"], ["2025.01", "2025.02"])
        self.assertEqual((stats["duplicate_keys"], stats["out_of_year_rows"]), (0, 0))
        self.assertEqual((stats["export_usd"], stats["import_usd"]), (807248, 305294))
        self.assertTrue(stats["collection_complete"])


if __name__ == "__main__":
    unittest.main()
