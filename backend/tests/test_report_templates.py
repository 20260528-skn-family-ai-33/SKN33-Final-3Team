"""리포트 HTML 템플릿(정기 월간·주간·분기, 요청형) 렌더를 예시 데이터로 확인합니다 (DB·네트워크 없이 실행).

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
        for kind, expected in (('monthly', 7), ('quarterly', 9), ('weekly', 5), ('ondemand', 7)):
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
            rt.render('RPT_UNKNOWN', {})
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
        context['country_counts'] = [dict(row, country_code=f'C{i:02d}', name=f'국가{i:02d}') for i in range(40)]
        # 국가별 기사 수 표(grid)가 처음 놓인 쪽에 들어간 행 수 (양식처럼 기사 목록 다음에 옴)
        first_rows = lambda: next(b['end'] for p in rt.layout('RPT_WEEKLY', context)['articles'] for b in p if b['key'] == 'grid')
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


class PendingValueTest(unittest.TestCase):
    """팀 기준 미정 블록은 숨기고, 통계 미준비는 “데이터 준비 중”, 문장 검증 전에는 “검증 통과”를 쓰지 않습니다."""

    def test_unverified_sentences_are_not_shown_as_passed(self):
        for kind in ('monthly', 'quarterly'):
            context = rt.sample_context(kind)
            context['verification'] = None
            html = rt.render(rt.KINDS[kind], context)
            with self.subTest(kind=kind):
                self.assertNotIn('검증 통과', html)
                self.assertIn('문장 속 수치 검증 전', html)

    def test_undecided_blocks_are_hidden(self):
        context = rt.sample_context('monthly')
        context['summary'].update(highlight=None, sentences=None, check_points=None)
        html = rt.render('RPT_MONTHLY', context)
        self.assertIn('class="kpis c2"', html)          # 품목 카드 2개만
        self.assertNotIn('class="note"', html)
        self.assertNotIn('확인할 점', html)

        weekly = rt.sample_context('weekly')
        weekly['summary'].update(spike_ratio=None, sentences=None, schedule=None)
        html = rt.render('RPT_WEEKLY', weekly)
        self.assertNotIn('>급증 키워드<', html)
        self.assertNotIn('class="badge">급증', html)
        self.assertNotIn('이상을 급증으로', html)
        self.assertNotIn('이번 주 핵심', html)
        self.assertNotIn('이번 주 일정', html)
        self.assertNotIn('None', html)

    def test_issue_threshold_is_separate_from_keyword_threshold(self):
        weekly = rt.sample_context('weekly')
        self.assertIn('직전 8주 주평균의 3배 이상이거나 KOTRA', rt.render('RPT_WEEKLY', weekly))
        weekly['summary']['issue_spike_ratio'] = None
        html = rt.render('RPT_WEEKLY', weekly)
        self.assertIn('전체 기사 기준 · KOTRA 통상·규제 분류', html)
        self.assertIn('8주 주평균의 2.5배 이상', html)  # 관심 키워드 기준은 그대로

    def test_unready_statistics_show_pending(self):
        monthly = rt.sample_context('monthly')
        for item in monthly['items']:
            item['trend'].update(current=[None] * 13, previous=[None] * 13)
            item['top_countries'], item['others'] = [], None
        monthly['interest_overview'] = []
        html = rt.render('RPT_MONTHLY', monthly)
        self.assertGreaterEqual(html.count('데이터 준비 중'), 5)  # 품목 2개 × (추이·주요 수출국) + 관심 국가 표
        self.assertEqual(html.count('<tr class="total"><td></td><td>전체</td>'), 2)

        quarterly = rt.sample_context('quarterly')
        for item in quarterly['items']:
            item['quarters']['values'] = [None] * 8
            item['top_countries'], item['others'], item['top5_compare'] = [], None, []
        self.assertGreaterEqual(rt.render('RPT_QUARTERLY', quarterly).count('데이터 준비 중'), 6)


class OnDemandTest(unittest.TestCase):
    """요청형(RPT_ON_DEMAND): 이양원님과 합의한 표시 규칙 (#45)."""

    def render(self, update=None, **conditions):
        context = rt.sample_context('ondemand')
        context['conditions'].update(conditions)
        if update:
            update(context)
        return rt.render('RPT_ON_DEMAND', context)

    def toc(self, html):
        return re.findall(r'<span class="t">([^<]+)</span><span>(\d+)</span>', html)

    def test_sample_matches_form_and_hides_growth_risk(self):
        html = self.render()
        self.assertIn('기초화장품 수출 시장 분석', html)
        self.assertIn('86.4<span class="u">억 달러</span>', html)     # USD 원값 → 억 달러
        self.assertIn('<td class="r">16.12</td>', html)
        # GROWTH_RISK는 골랐어도 기준 확정 전까지 목차·쪽·포함 항목에서 빠짐
        self.assertEqual(self.toc(html), [('핵심 요약', '3'), ('월별 수출 추이', '4'), ('주요 수출국', '5'),
                                          ('현지 이슈', '6'), ('데이터 기준 · 출처', '7')])
        self.assertNotIn('증가 감소', html)
        self.assertIn('핵심 요약 · 주요 지표 · 월별 수출 추이 · 주요 수출국 · 현지 이슈', html)

    def test_title_follows_selected_items(self):
        item = rt.sample_context('ondemand')['conditions']['hs_items'][0]
        many = [item, dict(item, hs_code='330410', label='립', display='립 · HS 330410'),
                dict(item, hs_code='3305', label='모발', display='모발 · HS 3305')]
        html = self.render(hs_items=many)
        self.assertIn('기초화장품 외 2개 품목 수출 시장 분석', html)
        self.assertIn('기초화장품 등 · HS 330499 (6단위) · 립 · HS 330410 · 모발 · HS 3305', html)  # 실제 선택 목록
        self.assertIn('선택한 품목 3개의 수출액을 합산', html)
        self.assertIn('전체 품목 수출 시장 분석', self.render(hs_items=[]))

    def test_unselected_sections_leave_toc_and_numbering(self):
        html = self.render(sections=[])
        self.assertEqual(self.toc(html), [('핵심 요약', '3'), ('데이터 기준 · 출처', '4')])
        self.assertEqual(pages(html), 4)
        self.assertNotIn('주요 지표', html)            # 주요 지표 카드는 KPI 소속
        self.assertIn('<span class="n">02</span>데이터 기준', html)
        html = self.render(sections=['LOCAL_ISSUES'])
        self.assertEqual([t for t, _ in self.toc(html)], ['핵심 요약', '현지 이슈', '데이터 기준 · 출처'])

    def test_compare_modes(self):
        none = self.render(compare=dict(rt.sample_context('ondemand')['conditions']['compare'], mode='NONE'))
        self.assertNotIn('2024 수출액', none)
        self.assertNotIn('변동</th>', none)
        self.assertIn('비교 기간</th><td>없음', none)
        custom = self.render(compare=dict(rt.sample_context('ondemand')['conditions']['compare'], mode='CUSTOM',
                                          monthly_paired=False))
        self.assertIn('비교는 기간 합계만', custom)
        self.assertIn('월별로 짝짓지 않고', custom)
        self.assertNotIn('<th class="r">증감액</th>', custom)   # 월별 비교 열 없음
        self.assertIn('2024 수출액', custom)                      # 기간 합계 비교는 표시

    def test_missing_compare_data_differs_from_none(self):
        def clear(context):
            k = context['summary']['kpi']
            k.update(prev_amount_usd=None, change_pct=None, prev_country_count=None, country_count_diff=None)
        html = self.render(clear, compare=dict(rt.sample_context('ondemand')['conditions']['compare'], status='NOT_READY'))
        self.assertIn('비교 데이터 준비 중', html)
        self.assertIn('비교 값은 “—”로 표시', html)
        self.assertNotIn('전년 —개국', html)
        # 백엔드가 이전 값을 함께 넘겨도 준비 전이면 이전 값·증감·순위 변동을 보이지 않음
        mixed = self.render(compare=dict(rt.sample_context('ondemand')['conditions']['compare'], status='NOT_READY'))
        self.assertNotIn('전년 1위', mixed)
        self.assertRegex(mixed, r'증감률</div>\s*<div class="v">—<')                        # 증감률 카드
        self.assertRegex(mixed, r'<td class="b">미국</td>\s*<td class="r">16\.12</td><td class="r">18\.7%</td>\s*'
                                r'<td class="r">—</td><td class="r">—</td>')                  # 이전 수출액·증감
        self.assertNotRegex(mixed, r'<td>[▲▼]\d')                                            # 순위 변동
        self.assertRegex(mixed, r'상위 1개국 비중</td><td class="r">18\.7%</td><td class="r">—</td>')

    def test_request_channel_metric_and_verification(self):
        html = self.render(request_channel='FORM', metric={'code': 'GROWTH', 'label': '증감률'})
        self.assertIn('리포트 요청 화면', html)
        self.assertIn('증감률 · 현재 계산·정렬 기준: 수출액', html)
        self.assertIn('AI 분석 대화에서 요청', self.render())
        self.assertNotIn('화장품 수출 어디서', self.render())     # 질문 원문은 넣지 않음
        html = self.render(lambda c: c.update(verification=None))
        self.assertNotIn('검증 통과', html)
        self.assertNotIn('검증했', html)                         # 검증 전에는 완료 문구가 없음
        self.assertIn('문장 속 수치 검증 전', html)
        self.assertIn('문장 속 수치 검증 통과', self.render())

    def test_unselected_sections_need_no_data_and_unready_values_do_not_fail(self):
        def strip(context):
            for key in ('monthly', 'top_countries', 'others', 'concentration', 'articles', 'article_limit'):
                del context[key]
        self.assertEqual(pages(self.render(strip, sections=[])), 4)  # 요약 값만 있으면 됨
        html = self.render(lambda c: c.update(concentration=None))
        self.assertIn('데이터 준비 중', html)
        html = self.render(lambda c: c['summary']['kpi'].update(top1_name=None, top1_share_pct=None))
        self.assertRegex(html, r'1위 수출국</div>\s*<div class="v">—<')
        self.assertNotIn('1위 교체', html)                                      # 현재 1위가 없으면 비교 문장 없음
        self.assertNotIn('에도 1위', html)
        self.assertNotIn('None', html)
        zero = self.render(lambda c: c['summary']['kpi'].update(amount_usd=0, country_count=0, top1_name=None,
                                                                 top1_share_pct=None, top3_share_pct=None))
        self.assertNotIn('1위 교체', zero)                                      # 현재 수출 0 (비교 기간에는 있음)
        self.assertNotIn('None', zero)
        self.assertNotIn('(전년 1위', zero)
        self.assertIn('중국 → 미국으로 1위 교체', self.render())               # 둘 다 있으면 그대로 표시
        self.assertRegex(zero, r'<td>전체</td><td class="r">0\.00</td><td class="r">—</td>')  # 수출 0이면 비중 100% 아님
        empty = self.render(lambda c: c['concentration']['countries'].update(current=None))
        self.assertNotIn('—개국', empty)                                        # 값 없는 수출국 수는 — 만

    def test_month_on_month_wording(self):
        compare = dict(rt.sample_context('ondemand')['conditions']['compare'], mode='MOM')
        html = self.render(compare=compare)
        self.assertIn('전월 비교', html)
        self.assertNotIn('전월 같은 달', html)

    def test_long_periods_and_many_articles_add_pages(self):
        def grow(context):
            m = context['monthly']
            m['labels'] = m['labels'] * 3
            m['current'], m['previous'], m['rows'] = m['current'] * 3, m['previous'] * 3, m['rows'] * 3
            context['articles'] = [dict(context['articles'][0], no=i) for i in range(1, 16)]
        html = self.render(grow)
        self.assertIn('월별 수출 추이 (계속)', html)
        self.assertIn('현지 이슈 (계속)', html)
        layout = rt.layout('RPT_ON_DEMAND', rt.sample_context('ondemand'))
        self.assertEqual(layout['sections'], ['SUMMARY', 'KPI', 'TOP_COUNTRIES', 'LOCAL_ISSUES'])


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
        self.assertEqual(rt.eok(8_640_000_000, 1), '86.4')           # 요청형: USD → 억 달러
        self.assertEqual(rt.eok(1_612_000_000), '16.12')
        self.assertEqual(rt.signed_eok(-240_000_000), '−2.40')
        self.assertEqual(rt.eok(None), '—')
        self.assertIn('▲1', rt.rank_move(1))
        self.assertIn('▼2', rt.rank_move(-2))
        self.assertIn('–', rt.rank_move(0))
        self.assertIn('신규', rt.rank_move('NEW'))
        self.assertIn('—', rt.rank_move(None))

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
        context['items'][0]['country_trend'][11]['values'] = base['values'][:-1] + [None]  # 국가11만 마지막 달 미수집
        html = rt.render('RPT_MONTHLY', context)
        self.assertIn('범례 숫자는 25.12 값 (괄호는 값이 있는 마지막 기간)', html)
        self.assertIn('국가10 ' + rt.musd(base['values'][-1]) + '<', html)
        self.assertIn('국가11 ' + rt.musd(base['values'][-2]) + '(25.11)<', html)  # 이전 기간은 연도까지 표시

    def test_end_labels_keep_both_bounds_with_one_large_value(self):
        # 양원님 재현 조건: 큰 값 하나 + 작은 값 7개 (기존에는 큰 값 라벨이 y=-52로 그래프 위로 벗어남)
        labels = [str(i) for i in range(13)]
        for ends in ([100000000] + [1000000] * 7, [1000000] * 7 + [100000000], [5e7, 1e6, 1e8, 1e6, 2e6, 1e6, 9e7, 1e6, 1e6]):
            for height in (130, 250):
                svg = str(rt.line_chart(labels, [{'values': [v] * 13} for v in ends], height=height))
                ys = sorted(float(y) - 3 for y in re.findall(r'y="(-?[\d.]+)" class="end"', svg))
                with self.subTest(ends=ends, height=height):
                    self.assertEqual(len(ys), len(ends))
                    self.assertGreaterEqual(ys[0], 10)                       # 그래프 영역 위
                    self.assertLessEqual(ys[-1], height - 24)                # 그래프 영역 아래
                    self.assertTrue(all(b - a >= rt.END_GAP - 0.01 for a, b in zip(ys, ys[1:])))

    def test_legend_value_marks_period_when_last_is_missing(self):
        labels = ['10', '11', '12']
        self.assertEqual(rt.legend_value([1e6, 2e6, 3e6], labels), '3.0')
        self.assertEqual(rt.legend_value([1e6, 1e7, None], labels), '10.0(11)')
        self.assertEqual(rt.legend_value([1e6, None, None], labels), '1.0(10)')
        self.assertEqual(rt.legend_value([None, None, None], labels), '—')
        self.assertEqual(rt.legend_value([1e6, 2e6, 0], labels), '0.0')  # 실제 0은 그대로

    def test_legend_value_tells_apart_same_quarter_of_different_years(self):
        # 양원님 재현 조건: 최근 8개 분기 라벨은 '4Q'가 연도마다 반복 → 2024년 4분기 값만 있으면 2025년 4분기로 오해하던 문제
        labels = ['24.1Q', '2Q', '3Q', '4Q', '25.1Q', '2Q', '3Q', '4Q']
        self.assertEqual(rt.legend_value([None] * 3 + [1e7] + [None] * 4, labels), '10.0(24.4Q)')
        self.assertEqual(rt.legend_value([None] * 6 + [1e7, None], labels), '10.0(25.3Q)')
        self.assertEqual(rt.legend_value([None] * 7 + [1e7], labels), '10.0')
        self.assertEqual(rt.full_period_label(['24.12', '25.1', '2', '3'], 3), '25.3')
        self.assertEqual(rt.full_period_label(['24.12', '25.1', '2', '3'], 0), '24.12')
        self.assertEqual(rt.full_period_label(['2025.10', '2025.11', '2025.12'], 1), '2025.11')

        context = rt.sample_context('quarterly')
        base = context['conditions']['countries'][0]
        context['conditions']['countries'] = [dict(base, country_code=f'C{i:02d}', name=f'국가{i:02d}') for i in range(24)]
        for item in context['items']:
            s = item['country_quarters'][0]
            item['country_quarters'] = [dict(s, country_code=f'C{i:02d}', name=f'국가{i:02d}',
                                             values=[None] * 3 + [1e7] + [None] * 4) for i in range(24)]
            s = item['country_months']['series'][0]
            item['country_months']['series'] = [dict(s, country_code=f'C{i:02d}', name=f'국가{i:02d}') for i in range(24)]
        html = rt.render('RPT_QUARTERLY', context)
        self.assertIn('국가01 10.0(24.4Q)<', html)
        self.assertNotIn('국가01 10.0<', html)
        self.assertIn('범례 숫자는 25.4Q 값', html)  # 안내 문구도 연도까지 표시

    def test_hbar_reserves_room_for_long_values(self):
        svg = str(rt.hbar_chart([{'label': '미국', 'values': [99_000e6, 45_000e6]}], ['#000', '#999'],
                                label_width=60, width=320, row_height=30, divisor=1e6, digits=1))
        for x, text in re.findall(r'<text x="([\d.]+)"[^>]*>([\d,.]+)</text>', svg):
            self.assertLessEqual(float(x) + rt._em(text) * 9.5, 320)

    def test_axis_labels_do_not_overlap_on_long_periods(self):
        labels = [f'{y % 100}.1' if i == 1 else f'{i}' for y in (2023, 2024, 2025) for i in range(1, 13)]  # 36개월
        svg = str(rt.line_chart(labels, [{'values': [1e8] * 36}]))
        shown = re.findall(r'<text x="([\d.]+)" y="\d+" text-anchor="middle" class="axis">([^<]+)<', svg)
        self.assertLess(len(shown), 36)
        self.assertTrue({'23.1', '24.1', '25.1'} <= {t for _, t in shown})       # 연도 라벨은 남김
        spans = [(float(x) - rt._em(t) * 4.75, float(x) + rt._em(t) * 4.75) for x, t in shown]
        self.assertTrue(all(a[1] <= b[0] for a, b in zip(spans, spans[1:])))   # 서로 겹치지 않음
        short = str(rt.line_chart([str(i) for i in range(1, 14)], [{'values': [1e8] * 13}]))
        self.assertEqual(short.count('text-anchor="middle" class="axis"'), 13)   # 13개월은 모두 표시

    def test_small_ranges_keep_distinct_axis_ticks(self):
        # 월 100만~210만 달러를 억 달러로 그리면 눈금 간격이 0.005 → 모두 0.0이 되면 안 됨
        svg = str(rt.line_chart([str(i) for i in range(1, 13)], [{'values': [1e6 + i * 1e5 for i in range(12)]}],
                                divisor=1e8, digits=2))
        ticks = re.findall(r'text-anchor="end" class="axis">([^<]+)<', svg)
        self.assertEqual(ticks, ['0.005', '0.010', '0.015', '0.020', '0.025'])
        left = float(re.search(r'<line x1="([\d.]+)"', svg).group(1))
        self.assertGreaterEqual(left - 6, rt._em('0.025') * 9.5)                 # 늘어난 자릿수도 왼쪽 안에 들어감
        quarter = str(rt.bar_chart(['a', 'b'], [0.3e6, 0.9e6], ['#000', '#000']))
        self.assertEqual(re.findall(r'class="axis">([^<]+)<', quarter)[:5], ['0', '0.25', '0.50', '0.75', '1'])
        self.assertEqual(rt._tick_digits([0, 2.5, 5]), 1)                       # 기존 간격은 그대로 한 자리
        for low, high in ((2e5, 4e5), (1e3, 5e3), (10, 50), (1, 3)):            # 월 수십만 ~ 몇 달러
            svg = str(rt.line_chart(['1', '2'], [{'values': [low, high]}], divisor=1e8, digits=2))
            ticks = re.findall(r'text-anchor="end" class="axis">([^<]+)<', svg)
            self.assertEqual(len(set(ticks)), len(ticks), ticks)
        tiny = str(rt.bar_chart(['a', 'b'], [0.1, 0.17], ['#000', '#000'], divisor=1e8))  # 0 다음 눈금 5e-10
        ticks = re.findall(r'class="axis">([^<]+)<', tiny)[:5]
        self.assertEqual(len(set(ticks)), len(ticks), ticks)

    def test_nice_scale_covers_data(self):
        low, high, ticks = rt.nice_scale([602e6 / 1e6, 879.0])
        self.assertLessEqual(low, 602)
        self.assertGreaterEqual(high, 879)
        self.assertEqual(ticks[0], low)
        self.assertEqual(rt.nice_scale([0, 0], zero=True)[:2], (0, 1))


if __name__ == '__main__':
    unittest.main()
