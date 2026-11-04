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


if __name__ == '__main__':
    unittest.main()
