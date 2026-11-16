"""#120 베타테스트 2차 수정: 기사 발췌 '…', 리포트 꺾은선 투명도·점 값."""
import re
import unittest

from backend.app.api.news import SUMMARY_CHARS, excerpt
from backend.app.services.report_templates import AREA_OPACITY, line_chart


class ExcerptTest(unittest.TestCase):
    def test_cut_text_ends_with_ellipsis_within_limit(self):
        text = '가' * (SUMMARY_CHARS + 50)
        cut = excerpt(text)
        self.assertTrue(cut.endswith('…'))
        self.assertEqual(len(cut), SUMMARY_CHARS)

    def test_short_text_is_unchanged(self):
        self.assertEqual(excerpt('  짧은 본문  '), '짧은 본문')
        self.assertEqual(excerpt('나' * SUMMARY_CHARS), '나' * SUMMARY_CHARS)
        self.assertEqual(excerpt(None), '')


class LineChartTest(unittest.TestCase):
    def test_area_is_translucent(self):
        svg = str(line_chart(['1', '2', '3'], [{'values': [1e8, 2e8, 3e8], 'area': True}], divisor=1e8))
        self.assertIn(f'fill-opacity="{AREA_OPACITY}"', svg)
        self.assertTrue(0.5 <= AREA_OPACITY <= 0.6)

    @staticmethod
    def values(svg):
        """눈금·가로축 라벨을 뺀 값 라벨(소수 2자리)."""
        return [t for t in re.findall(r'<text[^>]*>([^<]*)</text>', str(svg)) if re.fullmatch(r'-?[\d,]+\.\d\d', t)]

    def test_single_series_shows_each_point_value_once(self):
        # 점마다 값을 쓰고, 마지막 값은 끝 값 라벨 한 번만 쓴다(중복 없음)
        self.assertEqual(self.values(line_chart(['1', '2', '3'], [{'values': [1e8, 2e8, 3e8]}], divisor=1e8)),
                         ['1.00', '2.00', '3.00'])

    def test_comparison_chart_labels_the_analysis_line(self):
        # 분석 선 + 비교 선이면 분석(첫) 선만 점 값을 쓰고, 비교 선은 끝 값만 쓴다
        self.assertEqual(self.values(line_chart(['1', '2', '3'], [{'values': [4e8, 5e8, 6e8]}, {'values': [1e8, 2e8, 3e8], 'dashed': True}], divisor=1e8)),
                         ['4.00', '5.00', '6.00', '3.00'])

    def test_several_series_keep_end_labels_only(self):
        # 국가별처럼 선이 셋 이상이면 숫자가 겹치므로 기존처럼 끝 값만 쓴다
        three = [{'values': [1e8, 2e8, 3e8]}, {'values': [4e8, 5e8, 6e8]}, {'values': [7e8, 8e8, 9e8]}]
        self.assertEqual(self.values(line_chart(['1', '2', '3'], three, divisor=1e8)), ['3.00', '6.00', '9.00'])

    def test_crowded_points_do_not_overlap(self):
        # 달이 많으면 겹치지 않는 위치에만 쓴다(전부 쓰지 않는다)
        shown = self.values(line_chart([str(i) for i in range(1, 37)], [{'values': [i * 1e8 for i in range(1, 37)]}], divisor=1e8))
        self.assertLess(len(shown), 36)
        self.assertIn('36.00', shown)


if __name__ == '__main__':
    unittest.main()
