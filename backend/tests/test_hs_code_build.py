"""HS 코드 마스터 생성 코드의 6단위 이름 규칙을 가상 데이터로 확인합니다 (LLM 호출 없음).

실행: python -m unittest backend/tests/test_hs_code_build.py
"""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

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

    def test_summarize_validates_and_creates_cache_dir(self):
        # LLM이 null·'|' 포함·너무 긴 값을 주면 캐시에 넣지 않고, 기존 캐시의 'None'도 재요청합니다.
        replies = {"a": None, "b": "x|y", "c": "가" * (hb.NAME_MAX + 1), "d": " 살아 있는 말 ", "e": "당나귀"}
        sent = []

        class FakeOpenAI:
            def __init__(self, api_key):
                self.chat = self.completions = self

            def create(self, messages, **_):
                chunk = json.loads(messages[1]["content"])
                sent.append(sorted(chunk))
                content = json.dumps({c: replies[c] for c in chunk}, ensure_ascii=False)
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(hb, "OpenAI", FakeOpenAI):
            cache_path = Path(tmp) / "없는" / "폴더" / "cache.json"  # 아직 없는 폴더
            got = hb.summarize(dict.fromkeys("abcd", "입력"), "p", "m", "k", cache_path, "t")
            self.assertEqual(got, {"d": "살아 있는 말"})
            self.assertEqual(json.loads(cache_path.read_text(encoding="utf-8")), {"d": "살아 있는 말"})

            cache_path.write_text(json.dumps({"d": "살아 있는 말", "e": "None"}), encoding="utf-8")
            sent.clear()
            got = hb.summarize(dict.fromkeys("de", "입력"), "p", "m", "k", cache_path, "t")
            self.assertEqual(sent, [["e"]])  # 유효한 d는 재요청하지 않음
            self.assertEqual(got, {"d": "살아 있는 말", "e": "당나귀"})

    def test_summary_character_limit(self):
        self.assertTrue(hb.valid_summary("가" * 20))
        self.assertFalse(hb.valid_summary("가" * 21))
        self.assertFalse(hb.valid_summary("가" * 10 + " " + "나" * 10))

    def test_summary_rejects_hs_codes_but_allows_dimensions(self):
        for value in ("기초화장품330499", "3304.99 기초화장품", "3304 99 화장품", "기초화장용 기타 제품E", "납박(0.2mm 이하)M"):
            self.assertFalse(hb.valid_summary(value), value)
        self.assertTrue(hb.valid_summary("납 시트·박(0.2mm 이하)"))

    def test_trailing_letter_allowed_only_when_in_source(self):
        self.assertTrue(hb.valid_summary("비타민C 및 유도체", "비타민 C와 이들의 유도체"))
        self.assertFalse(hb.valid_summary("기초화장용 제품E", "기타 > PET 용기"))  # PET의 E는 단독 글자가 아님
        self.assertFalse(hb.valid_summary("비타민C"))

    def test_cache_keeps_summaries_outside_current_items(self):
        class FakeOpenAI:
            def __init__(self, api_key):
                pass

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(hb, "OpenAI", FakeOpenAI):
            path = Path(tmp) / "cache.json"
            path.write_text(json.dumps({"a": "말", "z": "수동 요약으로 바뀐 코드"}, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(hb.summarize({"a": "원문"}, hb.PROMPT, "m", "k", path, "t"), {"a": "말"})
            self.assertIn("z", json.loads(path.read_text(encoding="utf-8")))

    def test_summary_input_includes_chapter_material(self):
        names = {"78": "납과 그 제품", "7804": "납 판·시트·박", "780411": "두께 0.2mm 이하"}
        self.assertEqual(hb.summary_input("780411", names), "납과 그 제품 > 납 판·시트·박 > 두께 0.2mm 이하")

    def test_compact_summary_preserves_threshold_meaning_without_truncation(self):
        value = hb.compact_summary("면 85% 미만 200g 이하 표백 안 한 평직물")
        self.assertIn("<85%", value)
        self.assertIn("≤200g", value)
        self.assertIn("미표백", value)
        self.assertIn("평직물", value)
        self.assertTrue(hb.valid_summary(value))
        self.assertEqual(hb.compact_summary("가" * 21), "가" * 21)
        self.assertEqual(hb.compact_summary("기초화장품"), "기초화장품")

    def test_duplicate_response_and_reserved_names_are_retried(self):
        replies = [{"a": "수동 품목", "b": "동일 품목", "c": "동일 품목"},
                   {"a": "새 품목", "c": "다른 품목"}]
        sent = []

        class FakeOpenAI:
            def __init__(self, api_key):
                self.chat = self.completions = self

            def create(self, messages, **_):
                sent.append(set(json.loads(messages[1]["content"])))
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                    content=json.dumps(replies[len(sent) - 1])))])

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(hb, "OpenAI", FakeOpenAI):
            got = hb.summarize(dict.fromkeys("abc", "원문"), hb.PROMPT, "m", "k",
                               Path(tmp) / "cache.json", "t", reserved=["수동 품목"])
            self.assertEqual(sent, [{"a", "b", "c"}, {"a", "c"}])
            self.assertEqual(got, {"a": "새 품목", "b": "동일 품목", "c": "다른 품목"})

    def test_summarize_repairs_cached_and_cross_batch_duplicates(self):
        sent = []

        class FakeOpenAI:
            def __init__(self, api_key):
                self.chat = self.completions = self

            def create(self, messages, **_):
                chunk = json.loads(messages[1]["content"])
                sent.append(set(chunk))
                # 첫 응답은 기존 캐시와 충돌, 두 번째는 길이 초과, 마지막은 정상.
                value = ["기존 요약", "가" * 21, "새 품목 요약"][(len(sent) - 1) % 3]
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                    content=json.dumps(dict.fromkeys(chunk, value))))])

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(hb, "OpenAI", FakeOpenAI), mock.patch.object(hb, "BATCH", 1):
            path = Path(tmp) / "cache.json"
            path.write_text(json.dumps({"a": "기존 요약", "b": "기존 요약", "c": "가" * 21}))
            got = hb.summarize(dict.fromkeys("abc", "품목 원문"), hb.PROMPT, "m", "k", path, "t")
            self.assertEqual(got, {"a": "기존 요약", "b": "새 품목 요약"})
            self.assertEqual(sent, [{"b"}] * 3 + [{"c"}] * 3)
            self.assertEqual(len(set(got.values())), len(got))

    def test_failed_summaries_do_not_write_csv(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(hb, "setup_log"), mock.patch.object(hb, "dotenv_values", return_value={"OPENAI_API_KEY": "fake"}), mock.patch.object(hb, "load_names", return_value=NAMES), mock.patch.object(hb, "summarize", return_value={}):
            output = Path(tmp)
            csv_path = output / "hs_code_mst.csv"
            csv_path.write_text("기존 결과", encoding="utf-8")
            result = hb.run(customs_xlsx="hs_names_20260101.xlsx", output_dir=output, cache_dir=output)
            self.assertIsNone(result["csv"])
            self.assertGreater(result["missing"], 0)
            self.assertIn("requests", result)  # hs_code_refresh가 batch_run.request_count에 더함
            self.assertEqual(csv_path.read_text(encoding="utf-8"), "기존 결과")


if __name__ == "__main__":
    unittest.main()
