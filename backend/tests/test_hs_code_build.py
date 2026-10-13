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
         "8605": "철도용 객차", "8605001000": "객차"} | hb.KR_SPECIAL


class HsCodeBuildTest(unittest.TestCase):
    def test_six_digit_name_fallbacks(self):
        self.assertEqual(hb.six_parts("010121", NAMES), ["살아 있는 말", "말", "번식용"])  # 6단위 시트
        self.assertEqual(hb.six_parts("010129", NAMES), ["살아 있는 말", "말", "기타"])    # 10단위 XXXXXX0000
        self.assertEqual(hb.six_parts("010130", NAMES), ["살아 있는 말", "당나귀"])        # 5단위 없음
        self.assertEqual(hb.six_parts("860500", NAMES), ["철도용 객차"])                  # XXXX00 → 4단위, 중복 제거

    def test_codes_include_all_levels_and_specials(self):
        levels = hb.codes_by_level(NAMES)
        self.assertEqual(levels[4], ["0101", "2424", "8605"])
        self.assertEqual(levels[6], ["010121", "010129", "010130", "242400", "860500"])
        self.assertNotIn("01012", sum(levels.values(), []))  # 5단위는 행으로 넣지 않음

    def test_build_name_ko(self):
        rows = {r["hs_code"]: r for r in hb.build(NAMES, {"010129": "번식용 외 살아 있는 말"}, "src", "HS2022")}
        self.assertEqual(rows["010129"]["name_ko"], "번식용 외 살아 있는 말|기타")
        self.assertEqual(rows["0101"]["name_ko"], "살아 있는 말")
        self.assertEqual([rows[c]["depth"] for c in ("01", "0101", "010129", "0101211000")], [2, 4, 6, 10])
        self.assertEqual(rows["0101211000"]["name_ko"], "농가 사육용")
        self.assertEqual(rows["010130"]["name_ko"], "|당나귀")  # 요약 누락이면 앞이 비어 있음
        self.assertEqual(set(rows["01"]), set(hb.FIELDS))


if __name__ == "__main__":
    unittest.main()
