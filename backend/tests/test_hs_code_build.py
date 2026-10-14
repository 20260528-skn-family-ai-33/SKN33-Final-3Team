"""HS 코드 마스터 생성 코드의 6단위 이름 규칙을 가상 데이터로 확인합니다 (LLM 호출 없음).

실행: python -m unittest backend/tests/test_hs_code_build.py
"""
import importlib.util
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "app/scheduler/collectors/hscode/hs_code_build.py"
spec = importlib.util.spec_from_file_location("hs_code_build", SRC)
hb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hb)

NAMES = {"01": "제1류 살아 있는 동물", "0101": "살아 있는 말", "01012": "말", "010121": "번식용",
         "0101211000": "농가 사육용", "0101290000": "기타", "0101300000": "당나귀",
         "8605": "철도용 객차", "8605001000": "객차"} | hb.KR_SPECIAL | hb.UN_STATS


class HsCodeBuildTest(unittest.TestCase):
    def test_six_digit_name_fallbacks(self):
        self.assertEqual(hb.six_parts("010121", NAMES), ["살아 있는 말", "말", "번식용"])  # 6단위 시트
        self.assertEqual(hb.six_parts("010129", NAMES), ["살아 있는 말", "말", "기타"])    # 10단위 XXXXXX0000
        self.assertEqual(hb.six_parts("010130", NAMES), ["살아 있는 말", "당나귀"])        # 5단위 없음
        self.assertEqual(hb.six_parts("860500", NAMES), ["철도용 객차"])                  # XXXX00 → 4단위, 중복 제거

    def test_codes_include_all_levels_and_specials(self):
        levels = hb.codes_by_level(NAMES)
        self.assertEqual(levels[2], ["01", "99"])
        self.assertEqual(levels[4], ["0101", "2424", "8605", "9999"])
        self.assertEqual(levels[6], ["010121", "010129", "010130", "242400", "860500", "999999"])
        self.assertNotIn("01012", sum(levels.values(), []))  # 5단위는 행으로 넣지 않음

    def test_build_name_ko(self):
        rows = {r["hs_code"]: r for r in hb.build(NAMES, {"010129": "번식용 외 살아 있는 말"}, "src", "HS2022")}
        self.assertEqual(rows["010129"]["name_ko"], "번식용 외 살아 있는 말|기타")
        self.assertEqual(rows["0101"]["name_ko"], "살아 있는 말")
        self.assertEqual([rows[c]["depth"] for c in ("01", "0101", "010129", "0101211000")], [2, 4, 6, 10])
        self.assertEqual(rows["0101211000"]["name_ko"], "농가 사육용")
        self.assertEqual(rows["010130"]["name_ko"], "|당나귀")  # 요약 누락이면 앞이 비어 있음
        self.assertEqual(set(rows["01"]), set(hb.FIELDS))

    def test_six_name_kept_when_same_as_four_digit_name(self):
        # 관세청 xlsx의 7804처럼 4단위명이 6단위명과 같아도 5단위명이 아니라 6단위명을 저장해야 합니다.
        names = {"7804": "시트[0.2밀리미터 이하]", "78041": "판ㆍ시트", "780411": "시트[0.2밀리미터 이하]",
                 "7804111000": "시트"}
        rows = {r["hs_code"]: r for r in hb.build(names, {"780411": "납 시트"}, "src", "HS2022")}
        self.assertEqual(rows["780411"]["name_ko"], "납 시트|시트[0.2밀리미터 이하]")

    def test_six_digit_original_kept_full(self):
        # 원본은 DB original_name_ko(TEXT)로 가므로 길어도 줄이지 않고, 500자 검사는 요약 부분만 봅니다.
        long = "청어[클루페아 하렌구스(Clupea harengus)]" * 30
        names = {"0305": "건조 어류", "030554": long, "0305541000": "청어"}  # 6단위 목록은 10단위에서 만듦
        rows = {r["hs_code"]: r for r in hb.build(names, {"030554": "건조 청어"}, "src", "HS2022")}
        self.assertEqual(rows["030554"]["name_ko"], "건조 청어|" + long)
        self.assertEqual(hb.db_name(rows["030554"]["name_ko"]), "건조 청어")
        self.assertEqual(hb.db_name("살아 있는 말"), "살아 있는 말")

    def test_overrides_file(self):
        overrides = hb.load_overrides(hb.overrides_path("HS2022"))
        self.assertEqual(overrides["610323"], "남성용 합성섬유 편물 앙상블")
        self.assertEqual(len(set(overrides.values())), len(overrides))

    def test_hs_ver_for_edition_date(self):
        cases = {"20210101": "HS2017", "20220101": "HS2022", "20271231": "HS2022", "20280101": "HS2028",
                 "20321231": "HS2028", "20330101": "HS2033", "20380115": "HS2038"}
        for date, ver in cases.items():
            self.assertEqual(hb.hs_ver_for(date), ver, date)
        self.assertEqual(hb.edition_date("관세청_HS부호 단위별 품목명_20280101.xlsx"), "20280101")
        self.assertIsNone(hb.edition_date("hs.xlsx"))

    def test_un_statistical_codes_fixed(self):
        rows = {r["hs_code"]: r for r in hb.build(NAMES, {"999999": hb.UN_STATS_NAME}, "관세청", "HS2022")}
        self.assertEqual(rows["999999"]["name_ko"], "품목 미지정(UN 통계용)|Commodities not specified according to kind")
        self.assertEqual((rows["99"]["name_ko"], rows["9999"]["name_ko"]), (hb.UN_STATS_NAME, hb.UN_STATS_NAME))
        self.assertEqual({rows[c]["source_edition"] for c in ("99", "9999", "999999")}, {hb.UN_STATS_SOURCE})
        self.assertEqual(rows["0101"]["source_edition"], "관세청")


if __name__ == "__main__":
    unittest.main()
