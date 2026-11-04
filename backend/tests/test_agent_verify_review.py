"""PR #61 재검수: 숫자 대조 오탐·누락 회귀 (verify.py)."""
import re
import unittest
from functools import lru_cache
from pathlib import Path

from backend.app.agents.verify import check_sentence

SEED = Path(__file__).resolve().parents[2] / 'db/mysql/init/11_hs_code_seed.sql'
PERIOD = {'start': '2025-01', 'end': '2025-12'}
COMPARE = {'start': '2024-01', 'end': '2024-12'}


@lru_cache(maxsize=1)
def all_product_names():
    """전체 품목표 이름(1글자 '위'·'조'·'배', 2글자 '기타' 등 포함)."""
    text = SEED.read_text(encoding='utf-8')
    return frozenset(n.replace("''", "'") for n in re.findall(r"\('\d+', 'HS\d+', '((?:[^']|'')*)'", text))


def ranking():
    return {'kind': 'RANKING', 'period': PERIOD, 'trade_flow': 'EXPORT', 'group': 'COUNTRY',
            'top': [{'rank': 1, 'code': 'US', 'name': '미국', 'amount_usd': 300_000_000, 'share_pct': 40.0},
                    {'rank': 2, 'code': 'CN', 'name': '중국', 'amount_usd': 200_000_000, 'share_pct': 25.0}]}


def trend_rows(change):
    """저장 결과(스냅샷) 모양: 대상 칸이 월 행마다 붙는다(templates TREND)."""
    target = {'country_code': 'US', 'country_name': '미국', 'total_usd': 1_200_000_000,
              'monthly_avg_usd': 100_000_000, 'change_pct': change}
    return {'kind': 'TREND', 'period': PERIOD, 'compare_period': COMPARE, 'trade_flow': 'EXPORT',
            'rows': [{**target, 'month': f'2025-{m:02d}', 'amount_usd': 100_000_000} for m in range(1, 13)]}


class FullProductTableNamesTest(unittest.TestCase):
    def test_short_product_names_do_not_swallow_units_or_labels(self):
        names = all_product_names()
        self.assertTrue({'위', '조', '배', '기타'} <= names)
        known = names | {'미국', '중국'}
        share = {'kind': 'SHARE', 'period': PERIOD, 'trade_flow': 'EXPORT', 'denominator_usd': 1_000_000_000,
                 'items': [{'code': 'US', 'name': '미국', 'amount_usd': 400_000_000, 'share_pct': 40.0},
                           {'code': None, 'name': '기타', 'amount_usd': 100_000_000, 'share_pct': 10.0}]}
        for sentence, digest in [('2025년 수출 1위는 미국입니다.', ranking()),
                                 ('미국은 3억 달러로 1위입니다.', ranking()),
                                 ('기타 국가의 비중은 10.0%입니다.', share),
                                 ('미국 비중은 40.0%입니다.', share)]:
            with self.subTest(sentence=sentence):
                self.assertTrue(check_sentence(sentence, [digest], known_names=known)[0])
        self.assertFalse(check_sentence('미국은 2위입니다.', [ranking()], known_names=known)[0])

    def test_country_code_does_not_match_inside_usd(self):
        self.assertTrue(check_sentence('미국 수출은 300000000 USD입니다.', [ranking()],
                                       known_names={'미국', 'US'})[0])


class ClaimSplitTest(unittest.TestCase):
    def test_two_countries_in_one_sentence(self):
        known = {'미국', '중국'}
        self.assertTrue(check_sentence('미국은 3억 달러, 중국은 2억 달러입니다.', [ranking()], known_names=known)[0])
        self.assertTrue(check_sentence('미국은 1위이고 중국은 2위입니다.', [ranking()], known_names=known)[0])
        self.assertFalse(check_sentence('미국은 2억 달러, 중국은 3억 달러입니다.', [ranking()], known_names=known)[0])

    def test_ro_splits_total_and_monthly_average(self):
        self.assertTrue(check_sentence('2025년 미국 수출 총액은 12억 달러로 월평균 1억 달러입니다.',
                                       [trend_rows(5.2)], known_names={'미국'})[0])

    def test_change_pct_is_period_value_in_saved_rows(self):
        self.assertTrue(check_sentence('2025년 미국 수출은 전년 대비 5.2% 증가했습니다.',
                                       [trend_rows(5.2)], known_names={'미국'})[0])

    def test_share_label_is_identifier(self):
        snapshot = {'period': PERIOD, 'trade_flow': 'EXPORT', 'rows': [
            {'country_code': 'US', 'label': '미국', 'share_pct': 40.0},
            {'country_code': 'CN', 'label': '중국', 'share_pct': 25.0}]}
        self.assertTrue(check_sentence('중국 비중은 25.0%입니다.', [snapshot], known_names={'미국', '중국'})[0])
        self.assertFalse(check_sentence('중국 비중은 40.0%입니다.', [snapshot], known_names={'미국', '중국'})[0])

    def test_hs6_is_not_a_number(self):
        digest = {'kind': 'RANKING', 'period': PERIOD, 'trade_flow': 'EXPORT', 'group': 'HS6',
                  'top': [{'rank': 1, 'code': '330499', 'name': '기초화장품', 'amount_usd': 50_000_000}]}
        self.assertTrue(check_sentence('HS6 기준 1위는 기초화장품입니다.', [digest], known_names=set())[0])


class ChangeWordTest(unittest.TestCase):
    def test_direction_anywhere_in_claim(self):
        for sentence in ['미국 수출은 전년 대비 5.2%의 감소를 보였습니다.', '미국 수출 감소율은 5.2%입니다.',
                         '미국 수출은 전년보다 감소하여 5.2% 줄었습니다.', '미국 수출은 전년 대비 감소하여 변동률 5.2%를 기록했습니다.',
                         '미국 수출은 전년보다 5.2% 낮은 수준입니다.']:
            with self.subTest(sentence=sentence):
                self.assertTrue(check_sentence(sentence, [trend_rows(-5.2)], known_names={'미국'})[0])
                self.assertFalse(check_sentence(sentence, [trend_rows(5.2)], known_names={'미국'})[0])
        self.assertFalse(check_sentence('미국 수출 증가율은 5.2%입니다.', [trend_rows(-5.2)], known_names={'미국'})[0])


class ReviewFollowupTest(unittest.TestCase):
    """2차 검토: 국가별 분리의 기간 검사, '대중국', 넓은 증감 단어, 비중을 증감률 근거로 쓰는 문제."""
    def test_split_claims_keep_period_check(self):
        known = {'미국', '중국'}
        self.assertFalse(check_sentence('2023년 미국은 3억 달러, 중국은 2억 달러입니다.', [ranking()], known_names=known)[0])
        self.assertTrue(check_sentence('2025년 미국은 3억 달러, 중국은 2억 달러입니다.', [ranking()], known_names=known)[0])

    def test_dae_prefix_names_country(self):
        known = {'미국', '중국'}
        self.assertTrue(check_sentence('한국의 대중국 수출은 2억 달러입니다.', [ranking()], known_names=known, countries=known)[0])
        self.assertFalse(check_sentence('한국의 대중국 수출은 3억 달러입니다.', [ranking()], known_names=known, countries=known)[0])

    def test_dae_exception_is_only_for_countries(self):
        from backend.app.agents.verify import _name_pattern
        self.assertIsNotNone(_name_pattern('중국', True).search('대중국 수출'))
        self.assertIsNone(_name_pattern('리석', False).search('대리석 수출'))
        self.assertIsNone(_name_pattern('중국', True).search('확대중국'))

    def test_share_change_column_backs_change_claim(self):
        digest = {'kind': 'LLM_SQL', 'period': PERIOD, 'trade_flow': 'EXPORT',
                  'columns': [{'key': 'country_name', 'unit': None}, {'key': 'share_change_pp', 'unit': '%p'}],
                  'rows': [{'country_name': '미국', 'share_change_pp': 2.1}]}
        self.assertTrue(check_sentence('미국 비중이 2.1%p 증가했습니다.', [digest], known_names={'미국'})[0])

    def test_dictionary_name_inside_result_name_is_not_outside_target(self):
        from backend.tests.test_agent_compose import ComposeTest
        from backend.app.agents.state import ToolResult, Draft
        from backend.app.agents.verify import verify
        state = ComposeTest().state(['STATISTICS']); task = state.plan.tasks[0]; task.answer_status = 'ANSWERED'
        step = next(s for s in task.steps if s.tool == 'query_trade_data')
        long_name = '전동축[캠샤프트(cam shaft)와…ersal joint)를 포함한다]'
        state.results[step.step_id] = ToolResult(status='OK', result_id='r', digest={'rows': [{'name': long_name, 'amount_usd': 999}]})
        for sentence, expected in [('전동축 수출은 999 USD입니다.', 'PASS'), ('기초화장품 수출은 999 USD입니다.', 'MISMATCH')]:
            with self.subTest(sentence=sentence):
                state.draft = Draft(conclusion=[{'task_id': 't1', 'sentences': [sentence]}], turn_summary='')
                verify(state, known_names={'전동축', '기초화장품'})
                self.assertEqual(state.verification.checks[0].status, expected)

    def test_unrelated_words_are_not_directions(self):
        for sentence in ['수출액이 가장 높은 미국의 증감률은 -5.2%입니다.', '미국 수출 증감률은 -5.2%입니다(오늘 기준).']:
            with self.subTest(sentence=sentence):
                self.assertTrue(check_sentence(sentence, [trend_rows(-5.2)], known_names={'미국'})[0])

    def test_share_does_not_ground_change_claim(self):
        digest = ranking(); digest['top'][0]['share_pct'] = 5.0
        self.assertFalse(check_sentence('미국 수출이 5.0% 증가했습니다.', [digest], known_names={'미국', '중국'})[0])
        self.assertTrue(check_sentence('미국 수출 비중은 5.0%입니다.', [digest], known_names={'미국', '중국'})[0])
        # 증감 단어가 값 바로 뒤가 아니면 비중 값 대조를 막지 않는다.
        self.assertTrue(check_sentence('미국 비중은 증가해 5.0%입니다.', [digest], known_names={'미국', '중국'})[0])


class CountrySubstringTest(unittest.TestCase):
    def test_country_inside_another_country_name_is_still_a_country(self):
        from backend.tests.test_agent_compose import ComposeTest
        from backend.app.agents.state import ToolResult, Draft
        from backend.app.agents.verify import verify
        state = ComposeTest().state(['STATISTICS']); task = state.plan.tasks[0]; task.answer_status = 'ANSWERED'
        step = next(s for s in task.steps if s.tool == 'query_trade_data')
        state.results[step.step_id] = ToolResult(status='OK', result_id='r', digest={'rows': [{'name': '남수단', 'amount_usd': 999}]})
        state.draft = Draft(conclusion=[{'task_id': 't1', 'sentences': ['수단 수출은 999 USD입니다.']}], turn_summary='')
        verify(state, known_names={'수단', '남수단'}, countries={'수단', '남수단'})
        self.assertEqual(state.verification.checks[0].status, 'MISMATCH')


if __name__ == '__main__':
    unittest.main()
