"""정기 리포트 HTML 템플릿(월간·주간·분기) 렌더를 예시 데이터로 확인합니다 (DB·네트워크 없이 실행).

실행: python -m unittest backend/tests/test_report_templates.py
"""
import copy
import re
import unittest

from jinja2 import UndefinedError

from backend.app.services import report_templates as rt


def pages(html):
    return len(re.findall(r'<section class="page', html))


class SampleRenderTest(unittest.TestCase):
    def test_samples_render_with_expected_pages(self):
        for kind, expected in (('monthly', 7), ('quarterly', 9), ('weekly', 5)):
            with self.subTest(kind=kind):
                html = rt.render(rt.KINDS[kind], rt.sample_context(kind))
                self.assertEqual(pages(html), expected)
                self.assertIn(f'{expected} / {expected}', html)
                self.assertIn(rt.sample_context(kind)['report']['report_id'], html)

    def test_monthly_values_come_from_context(self):
        html = rt.render('RPT_MONTHLY', rt.sample_context('monthly'))
        self.assertIn('879.0', html)                       # 879,000,000 USD → 백만 달러
        self.assertIn('<strong>879.0백만 달러</strong>', html)  # 문장 속 **강조**
        self.assertIn('class="chg down">▼ 14.8%', html)

    def test_quarterly_year_labels_and_top10_cap(self):
        context = rt.sample_context('quarterly')
        extra = dict(context['items'][0]['top_countries'][0], rank=11, name='열한번째')
        context['items'][0]['top_countries'].append(extra)
        html = rt.render('RPT_QUARTERLY', context)
        self.assertIn('2025년 분기', html)
        self.assertIn('(2025 vs 2024)', html)
        self.assertNotIn('열한번째', html)

    def test_unsupported_type_and_missing_variable_fail(self):
        with self.assertRaises(ValueError):
            rt.render('RPT_ON_DEMAND', {})
        context = rt.sample_context('monthly')
        del context['verification']
        with self.assertRaises(UndefinedError):
            rt.render('RPT_MONTHLY', context)
        for kind, key in (('monthly', 'articles'), ('weekly', 'country_counts')):  # 쪽 나눔이 먼저 읽는 값
            context = rt.sample_context(kind)
            del context[key]
            with self.subTest(key=key), self.assertRaises(UndefinedError):
                rt.render(rt.KINDS[kind], context)

    def test_long_item_name_keeps_figures_in_subtitle(self):
        context = rt.sample_context('monthly')
        context['items'][0]['name'] = '향수·화장품·화장용 제품 중 기초화장품(피부 보호용 크림·로션 등) ' * 4
        html = rt.render('RPT_MONTHLY', context)
        sub = re.search(r'기초화장품 상세</h2>\s*<p class="sec-sub">([^<]*)</p>', html).group(1)
        self.assertIn('…', sub)
        self.assertLess(len(sub), 120)
        self.assertIn('12월 수출 879.0백만 달러', sub)

    def test_many_weekly_countries_split_table_and_total_on_last_page(self):
        context = rt.sample_context('weekly')
        row = context['country_counts'][0]
        context['country_counts'] = [dict(row, country_code=f'C{i:02d}', name=f'국가{i:02d}', market=i % 7, short=i % 5)
                                     for i in range(1, 31)]
        grid = [b for page in rt.layout('RPT_WEEKLY', context)['articles'] for b in page if b['key'] == 'grid']
        self.assertGreater(len(grid), 1)
        self.assertEqual([(b['start'], b['end']) for b in grid],
                         [(grid[0]['start'], grid[0]['end'])] + [(p['end'], n['end']) for p, n in zip(grid, grid[1:])])
        self.assertEqual(grid[-1]['end'], 31)  # 국가 30개 + 합계 행
        html = rt.render('RPT_WEEKLY', context)
        self.assertEqual(html.count('<tr class="total"><td>합계</td>'), 1)
        self.assertIn('국가별 기사 수 (계속)', html)
        self.assertIn('국가30', html)
        self.assertLess(html.index('국가30'), html.index('<tr class="total"><td>합계</td>'))

    def test_many_interest_countries_continue_detail_page_and_toc(self):
        context = rt.sample_context('monthly')
        base = context['items'][0]['country_trend'][0]
        country = context['conditions']['countries'][0]
        context['conditions']['countries'] = [dict(country, country_code=f'C{i:02d}', name=f'국가{i:02d}') for i in range(40)]
        for item in context['items']:
            item['country_trend'] = [dict(base, country_code=f'C{i:02d}', name=f'국가{i:02d}') for i in range(40)]
        details = rt.layout('RPT_MONTHLY', context)['details']
        self.assertTrue(all(len(pages) > 1 for pages in details))
        html = rt.render('RPT_MONTHLY', context)
        self.assertIn('기초화장품 상세 (계속)', html)
        self.assertEqual(html.count('<tr class="total"><td></td><td>전체</td>'), 2)  # 품목마다 전체 행 1번
        second = 3 + len(rt.layout('RPT_MONTHLY', context)['summary']) + len(details[0])
        self.assertRegex(html, rf'<span class="t">라면 상세</span><span>{second}</span>')
        self.assertEqual(pages(html), 2 + 1 + sum(len(p) for p in details) + 1 + 1)

    def test_long_sentences_and_spikes_continue_on_next_page(self):
        context = rt.sample_context('weekly')
        context['summary']['sentences'] = [f'핵심 문장 {i} ' + '가나다라 ' * 20 for i in range(1, 31)]
        context['keyword_spikes'] = [dict(context['keyword_spikes'][0], keyword=f'키워드{i}') for i in range(45)]
        html = rt.render('RPT_WEEKLY', context)
        self.assertIn('이번 주 핵심 (계속)', html)
        self.assertRegex(html, r'<ol start="\d+">')
        spikes = [b for p in rt.layout('RPT_WEEKLY', context)['keywords'] for b in p if b['key'] == 'spikes']
        self.assertGreater(len(spikes), 1)
        self.assertEqual(spikes[-1]['end'], 45)

    def test_quarterly_country_pages_hold_one_item_when_legend_is_long(self):
        context = rt.sample_context('quarterly')
        self.assertEqual(rt.layout('RPT_QUARTERLY', context)['country_per_page'], 2)
        base = context['conditions']['countries'][0]
        context['conditions']['countries'] = [dict(base, country_code=f'C{i:02d}', name=f'국가{i:02d}') for i in range(60)]
        self.assertEqual(rt.layout('RPT_QUARTERLY', context)['country_per_page'], 1)

    def test_long_country_names_make_weekly_table_rows_taller(self):
        context = rt.sample_context('weekly')
        row = context['country_counts'][0]
        context['country_counts'] = [dict(row, country_code=f'C{i:02d}', name=f'국가{i:02d}') for i in range(20)]
        first_rows = lambda: rt.layout('RPT_WEEKLY', context)['articles'][0][0]['end']  # 첫 쪽에 들어간 표 행 수
        short = first_rows()
        for r in context['country_counts']:
            r['name'] = '세인트빈센트그레나딘연방공화국' + r['country_code']  # 국가 칸에서 줄바꿈되는 이름
        self.assertLess(first_rows(), short)

    def test_hbar_labels_are_shortened_to_label_width(self):
        self.assertEqual(rt._fit('미국', 44, 10), '미국')
        fitted = rt._fit('세인트빈센트그레나딘', 44, 10)
        self.assertTrue(fitted.endswith('…'))
        self.assertLessEqual(rt._em(fitted) * 10, 44)
        svg = str(rt.hbar_chart([{'label': '세인트빈센트그레나딘 · 생산자책임재활용제도', 'values': [3]}], ['#000'], label_width=50))
        self.assertIn('…</text>', svg)
        self.assertNotIn('생산자책임', svg)

    def test_hbar_peak_keeps_scale_across_pages(self):
        rows = [{'label': 'A', 'values': [5]}]
        self.assertNotEqual(str(rt.hbar_chart(rows, ['#000'])), str(rt.hbar_chart(rows, ['#000'], peak=10)))
        self.assertEqual(str(rt.hbar_chart(rows, ['#000'])), str(rt.hbar_chart(rows, ['#000'], peak=5)))

    def test_context_layout_key_is_replaced(self):
        context = rt.sample_context('weekly')
        context['layout'] = 'stale'
        self.assertEqual(pages(rt.render('RPT_WEEKLY', context)), 5)


class VariableLengthTest(unittest.TestCase):
    def test_monthly_items_and_articles_add_pages(self):
        context = rt.sample_context('monthly')
        third = copy.deepcopy(context['items'][0])
        third.update(label='세번째', hs_code='999999')
        context['items'].append(third)
        for row in context['interest_overview']:
            row['values'].append(row['values'][0])
        long_summary = '가나다라마바사 아자차카타 ' * 30  # 약 400자 발췌
        context['articles'] = [dict(context['articles'][0], no=i, summary=long_summary) for i in range(1, 12)]  # 11건 → 기사 3쪽
        html = rt.render('RPT_MONTHLY', context)
        self.assertEqual(pages(html), 2 + 1 + 3 + 3 + 1)
        self.assertIn('세번째 상세', html)
        self.assertIn('현지 이슈 (계속)', html)
        self.assertIn('10 / 10', html)

    def test_overflowing_summary_moves_to_continued_page_and_toc_follows(self):
        context = rt.sample_context('monthly')
        for n in range(3, 7):  # 품목 6개
            extra = copy.deepcopy(context['items'][0])
            extra.update(label=f'품목{n}', hs_code=f'99990{n}')
            context['items'].append(extra)
        for row in context['interest_overview']:
            row['values'] = row['values'] * 3
        context['summary']['check_points'] = [{'target': '대상', 'content': '확인 내용 ' * 40}] * 6
        html = rt.render('RPT_MONTHLY', context)
        summary_pages = len(rt.layout('RPT_MONTHLY', context)['summary'])
        self.assertGreater(summary_pages, 1)
        self.assertIn('이달의 요약 (계속)', html)
        self.assertEqual(html.count('<h3>관심 국가 한눈에'), 2)  # 품목 3개씩 두 표
        first_item_page = 3 + summary_pages
        self.assertRegex(html, rf'<span class="t">기초화장품 상세</span><span>{first_item_page}</span>')
        self.assertEqual(pages(html), 2 + summary_pages + 6 + 1 + 1)

    def test_flow_splits_rows_and_never_leaves_heading_alone(self):
        blocks = [{'key': 'a', 'height': 210}, {'key': 't', 'head': 20, 'rows': [30] * 5, 'empty': 8}]
        result = rt.flow(blocks, 250, 250)
        self.assertEqual([[(b['key'], b['start'], b['end'], b['cont']) for b in p] for p in result],
                         [[('a', 0, 0, False)], [('t', 0, 5, False)]])  # 머리글+첫 행이 남은 40mm에 안 들어가면 다음 쪽
        result = rt.flow([{'key': 't', 'head': 20, 'rows': [100] * 5, 'empty': 8}], 250, 150)
        self.assertEqual([[(b['start'], b['end'], b['cont']) for b in p] for p in result],
                         [[(0, 2, False)], [(2, 3, True)], [(3, 4, True)], [(4, 5, True)]])

    def test_empty_lists_show_messages(self):
        weekly = rt.sample_context('weekly')
        weekly.update(articles=[], keyword_spikes=[], short_news={'total': 0, 'items': []})
        weekly['summary']['issues'] = []
        html = rt.render('RPT_WEEKLY', weekly)
        self.assertEqual(pages(html), 5)
        self.assertIn('관련 기사 없음', html)
        self.assertIn('키워드 언급 집계가 없습니다', html)
        self.assertIn('주요 이슈가 없습니다', html)

        quarterly = rt.sample_context('quarterly')
        quarterly.update(articles=[], one_off_note=None, article_limit=None)
        html = rt.render('RPT_QUARTERLY', quarterly)
        self.assertIn('국가당 건수 제한 해제', html)

    def test_sample_mark_follows_flag(self):
        context = rt.sample_context('weekly')
        self.assertIn('템플릿 예시', rt.render('RPT_WEEKLY', context))
        context['report']['is_sample'] = False
        html = rt.render('RPT_WEEKLY', context)
        self.assertNotIn('템플릿 예시', html)
        self.assertNotIn('예시 기사', html)


class FormatTest(unittest.TestCase):
    def test_number_filters(self):
        self.assertEqual(rt.musd(879_000_000), '879.0')
        self.assertEqual(rt.musd(-2_400_000, 2), '−2.40')
        self.assertEqual(rt.musd(None), '—')
        self.assertEqual(rt.signed_pct(16.6), '+16.6%')
        self.assertEqual(rt.signed_pct(-13.8), '−13.8%')
        self.assertEqual(rt.signed(6), '+6')
        self.assertIn('▲ 19.4%', rt.change(19.4))
        self.assertIn('신규', rt.change(None, new=True))
        self.assertIn('—', rt.change(None))
        self.assertEqual(rt.ymdhm('2026-01-16T06:00:00'), '2026.01.16 06:00')
        self.assertEqual(rt.circled(3), '③')

    def test_text_is_escaped(self):
        self.assertEqual(str(rt.emph('<b>x</b> **강조**')), '&lt;b&gt;x&lt;/b&gt; <strong>강조</strong>')
        context = rt.sample_context('weekly')
        context['articles'][0]['title'] = '<script>alert(1)</script>'
        html = rt.render('RPT_WEEKLY', context)
        self.assertNotIn('<script>alert(1)</script>', html)

    def test_line_chart_breaks_at_missing_values(self):
        svg = str(rt.line_chart(['1', '2', '3', '4', '5', '6'], [{'values': [1e6, 2e6, None, 3e6, None, 4e6]}]))
        self.assertEqual(svg.count('<polyline'), 1)       # 1~2월만 선으로 연결
        self.assertEqual(svg.count('r="1.8"'), 2)         # 앞뒤가 비어 있는 4월·6월은 점
        self.assertIn('>4.0</text>', svg)                 # 끝 값 라벨은 마지막 실제 값

    def test_end_labels_stay_inside_chart_or_move_to_legend(self):
        labels = [str(i) for i in range(13)]
        same = lambda n: [{'values': [1e8] * 13}] * n  # 끝 값이 모두 같아 라벨이 한곳에 몰리는 경우
        for n in (2, 8):
            ys = [float(y) for y in re.findall(r'y="([\d.]+)" class="end"', str(rt.line_chart(labels, same(n), height=130)))]
            self.assertEqual(len(ys), n)
            self.assertGreaterEqual(min(ys), 10)          # 그래프 영역 위
            self.assertLessEqual(max(ys), 130 - 24 + 3)   # 그래프 영역 아래(+글자 기준선)
        self.assertNotIn('class="end"', str(rt.line_chart(labels, same(12), height=130)))
        self.assertFalse(rt.end_labels_fit(12, 130))
        self.assertTrue(rt.end_labels_fit(8, 130))

        context = rt.sample_context('monthly')
        country = context['conditions']['countries'][0]
        context['conditions']['countries'] = [dict(country, country_code=f'C{i:02d}', name=f'국가{i:02d}') for i in range(12)]
        base = context['items'][0]['country_trend'][0]
        for item in context['items']:
            item['country_trend'] = [dict(base, country_code=f'C{i:02d}', name=f'국가{i:02d}') for i in range(12)]
        html = rt.render('RPT_MONTHLY', context)
        self.assertIn('범례 숫자는 마지막 값(', html)
        self.assertIn('국가11 ' + rt.musd(rt.last_value(base['values'])), html)

    def test_nice_scale_covers_data(self):
        low, high, ticks = rt.nice_scale([602e6 / 1e6, 879.0])
        self.assertLessEqual(low, 602)
        self.assertGreaterEqual(high, 879)
        self.assertEqual(ticks[0], low)
        self.assertEqual(rt.nice_scale([0, 0], zero=True)[:2], (0, 1))


if __name__ == '__main__':
    unittest.main()
