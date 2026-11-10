"""주간 리포트 출처가 실제로 보여 준 수치·항목에 쓰인 기사만 가리키는지 웹 상세·DOCX·PDF에서 함께 확인합니다.

일별 집계는 실제 적재와 같은 rebuild_daily로 만든다(PR #68 9차 리뷰).
"""
import io
import re
import unittest
from datetime import date

from backend.app.api import files, reports
from backend.app.api.database import Store
from backend.app.services.report_context import WEEKLY_ISSUE_LIMIT, build_context
from backend.app.services.report_templates import render
from backend.tests import test_backend_api as fixtures
from backend.tests import test_news_daily_sources as daily  # 클래스를 이 모듈 이름으로 두면 그 테스트가 여기서 한 번 더 돈다

MARKET, SHORT, LEGACY = 'KOTRA_MARKET', 'KOTRA_SHORT_NEWS', 'KOTRA_SHORTNEWS'
MARKET_LABEL, SHORT_LABEL = 'KOTRA 해외시장뉴스', 'KOTRA 단신속보'
RECENT_DAY, BASELINE_DAY = date(2025, 12, 21), date(2025, 12, 1)  # 주간 리포트 기간 12-15~12-21, 직전 8주 10-20~12-14


class WeeklyReportSourcesTest(unittest.TestCase):
    setUp = fixtures.BackendApiTest.setUp
    tearDown = fixtures.BackendApiTest.tearDown
    signup = fixtures.BackendApiTest.signup
    next_id = 5000
    article = daily.DailySourcesTest.article
    rebuild = daily.DailySourcesTest.rebuild
    collected = daily.DailySourcesTest.collected
    block = daily.DailySourcesTest.block

    def articles(self, count, source, day, keyword, country='US', **extra):
        for _ in range(count):
            self.article(source, day, (keyword,), countries=(country,), **extra)

    def weekly(self, uid):
        """작업자와 같은 순서로 만든 주간 문서: 웹 상세·DOCX가 쓰는 문서와 PDF 맥락."""
        record = {'report_id': 42, 'report_type': 'RPT_WEEKLY', 'conditions_json': {
            'period': {'start': '2025-12-15', 'end': '2025-12-21'}, 'hs_items': [{'hs_code': '330499', 'hs_ver': 'HS2022'}],
            'country_codes': ['US'], 'compare': 'YOY_PERIOD', 'compare_period': {'start': '2024-12-15', 'end': '2024-12-31'},
            'sections': ['KPI', 'TOP_COUNTRIES'], 'formats': ['PDF', 'DOCX']}}
        with self.engine.begin() as c:
            store = Store(self.app.state.database, c)
            doc = reports.build_document(store, uid, record)
            doc['template_pdf'] = {'version': 1, 'report_type': 'RPT_WEEKLY', 'context': build_context(store, uid, record, doc)}
            reports.attach_weekly_summary(doc)
        return doc

    def shown_sources(self, doc):
        """(웹 상세 출처 라벨, DOCX 본문, PDF 출처 칸 문구)."""
        from docx import Document
        web = [s['label'] for s in doc['meta']['sources']]
        docx = '\n'.join(p.text for p in Document(io.BytesIO(files.report_docx(doc, self.app.state.settings))).paragraphs)
        html = render('RPT_WEEKLY', doc['template_pdf']['context'])
        pdf = re.search(r'<td>기사</td><td>(.*?)</td>', html, re.S).group(1)
        return web, docx, pdf

    def assert_sources(self, doc, labels):
        web, docx, pdf = self.shown_sources(doc)
        self.assertEqual(sorted(web), sorted(labels))
        for label in (MARKET_LABEL, SHORT_LABEL):
            expected = 1 if label in labels else 0
            with self.subTest(label=label):
                self.assertEqual(docx.count(label), expected)
                self.assertEqual(pdf.count(label), expected)

    def test_baseline_only_short_news_is_a_source_of_the_spike_ratio(self):
        """최근 7일 시장뉴스 6건, 직전 8주 단신 16건: 배수 6÷(16÷8)=3배의 비교값은 단신이 정했으므로 출처에 둘 다 싣는다."""
        uid = int(self.signup())
        self.collected()
        self.articles(6, MARKET, RECENT_DAY, '관세')
        self.articles(16, SHORT, BASELINE_DAY, '관세')
        self.rebuild()
        doc = self.weekly(uid)
        ctx = doc['template_pdf']['context']
        self.assertEqual(ctx['keyword_spikes'], [{'keyword': '관세', 'country': '미국', 'this_week': 6, 'weekly_avg_8w': 2.0, 'ratio': 3.0}])
        self.assert_sources(doc, [MARKET_LABEL, SHORT_LABEL])

    def test_keyword_spike_baseline_alone_adds_its_source(self):
        """주요 이슈가 없어도(전체 기준은 기준 미달) 미국 급증 키워드의 비교값을 정한 미국 단신은 출처에 남는다."""
        uid = int(self.signup())
        self.collected()
        self.articles(6, MARKET, RECENT_DAY, '관세')
        self.articles(16, SHORT, BASELINE_DAY, '관세')
        self.articles(100, MARKET, BASELINE_DAY, '관세', country='CN')  # 전체 기준 주평균 14.5 → 이슈 아님
        self.rebuild()
        doc = self.weekly(uid)
        ctx = doc['template_pdf']['context']
        self.assertEqual([(r['keyword'], r['this_week'], r['weekly_avg_8w'], r['ratio']) for r in ctx['keyword_spikes']], [('관세', 6, 2.0, 3.0)])
        self.assertEqual(ctx['summary']['issues'], [])
        self.assert_sources(doc, [MARKET_LABEL, SHORT_LABEL])

    def test_baseline_sources_follow_the_count_filters(self):
        """직전 8주 기사라도 건수에 들어가지 않은 기사(다른 국가·비공개·회원 차단·다른 키워드·집계 밖 매체)는 출처에 넣지 않는다."""
        uid = int(self.signup())
        self.collected()
        self.articles(6, MARKET, RECENT_DAY, '관세')
        self.articles(100, MARKET, BASELINE_DAY, '관세', country='CN')  # 전체 기준 이슈를 막는다
        self.articles(16, LEGACY, BASELINE_DAY, '관세', country='CN')  # 미국 집계에 들어가지 않는다
        self.articles(4, 'HANKYUNG', BASELINE_DAY, '관세')  # 키워드 일별 집계 출처가 아니다
        self.articles(4, SHORT, BASELINE_DAY, '관세', visible='HIDDEN')
        for _ in range(4):
            self.article(SHORT, BASELINE_DAY, ('관세', '차단'))
        self.articles(4, SHORT, BASELINE_DAY, '물류')
        self.rebuild()
        self.block(uid, '차단')
        doc = self.weekly(uid)
        ctx = doc['template_pdf']['context']
        self.assertEqual([(r['keyword'], r['this_week'], r['ratio']) for r in ctx['keyword_spikes']], [('관세', 6, 6.0)])
        self.assertEqual(ctx['summary']['issues'], [])
        self.assert_sources(doc, [MARKET_LABEL])

    def test_major_issue_spike_baseline_is_a_source(self):
        """전체 기준 주요 이슈의 비교값을 정한 다른 국가 단신도 그 이슈를 보여 주면 출처에 싣는다(같은 원인)."""
        uid = int(self.signup())
        self.collected()
        self.articles(6, MARKET, RECENT_DAY, '관세')
        self.articles(16, SHORT, BASELINE_DAY, '관세', country='CN')
        self.rebuild()
        doc = self.weekly(uid)
        ctx = doc['template_pdf']['context']
        self.assertEqual([(r['keyword'], r['ratio']) for r in ctx['keyword_spikes']], [('관세', 6.0)])  # 미국 기준엔 중국 단신이 없다
        issues = ctx['summary']['issues']
        self.assertEqual([r['title'] for r in issues], ['‘관세’ 기사 급증'])
        self.assertIn('주평균 2.0건', issues[0]['basis'])
        self.assert_sources(doc, [MARKET_LABEL, SHORT_LABEL])

    def issue_scenario(self, market_issues):
        """시장뉴스 기반 급증 이슈 market_issues개 + 중국 단신 기반 급증 이슈 1개(정렬상 마지막)."""
        uid = int(self.signup())
        self.collected()
        for i in range(market_issues):
            self.articles(3, MARKET, RECENT_DAY, f'가{i:02d}')
        self.articles(3, SHORT, RECENT_DAY, '하중국', country='CN')
        self.rebuild()
        return self.weekly(uid)

    def test_issue_cut_by_the_display_limit_leaves_no_source(self):
        doc = self.issue_scenario(WEEKLY_ISSUE_LIMIT)
        issues = doc['template_pdf']['context']['summary']['issues']
        self.assertEqual(len(issues), WEEKLY_ISSUE_LIMIT)
        self.assertNotIn('‘하중국’ 기사 급증', [r['title'] for r in issues])
        self.assert_sources(doc, [MARKET_LABEL])

    def test_issue_inside_the_display_limit_keeps_its_source(self):
        doc = self.issue_scenario(WEEKLY_ISSUE_LIMIT - 1)
        issues = doc['template_pdf']['context']['summary']['issues']
        self.assertEqual(len(issues), WEEKLY_ISSUE_LIMIT)
        self.assertEqual(issues[-1]['title'], '‘하중국’ 기사 급증')
        self.assert_sources(doc, [MARKET_LABEL, SHORT_LABEL])

    def test_keyword_spike_cut_by_the_display_limit_leaves_no_source(self):
        """급증 키워드 표도 최대 건수만 싣는다. 잘린 키워드의 출처는 남기지 않는다(같은 원인)."""
        uid = int(self.signup())
        self.collected()
        for i in range(WEEKLY_ISSUE_LIMIT):
            self.articles(3, MARKET, RECENT_DAY, f'가{i:02d}')
        self.articles(3, SHORT, RECENT_DAY, '하미국')
        self.rebuild()
        doc = self.weekly(uid)
        ctx = doc['template_pdf']['context']
        self.assertEqual(ctx['summary']['counts']['spike_keywords'], WEEKLY_ISSUE_LIMIT + 1)  # 요약 카드는 제한 전 건수
        self.assertEqual(len(ctx['keyword_spikes']), WEEKLY_ISSUE_LIMIT)
        self.assertNotIn('하미국', [r['keyword'] for r in ctx['keyword_spikes']])
        self.assertNotIn('‘하미국’ 기사 급증', [r['title'] for r in ctx['summary']['issues']])
        self.assert_sources(doc, [MARKET_LABEL])

    def test_same_media_used_by_a_shown_item_stays_once(self):
        """잘린 항목과 표시 항목이 같은 매체를 쓰면 그 매체는 한 번만 남는다."""
        uid = int(self.signup())
        self.collected()
        for i in range(WEEKLY_ISSUE_LIMIT - 1):
            self.articles(3, MARKET, RECENT_DAY, f'가{i:02d}')
        self.articles(3, SHORT, RECENT_DAY, '나단신')   # 표시 범위 안 (10번째)
        self.articles(3, SHORT, RECENT_DAY, '하중국', country='CN')  # 11번째로 잘림
        self.rebuild()
        doc = self.weekly(uid)
        titles = [r['title'] for r in doc['template_pdf']['context']['summary']['issues']]
        self.assertIn('‘나단신’ 기사 급증', titles)
        self.assertNotIn('‘하중국’ 기사 급증', titles)
        self.assert_sources(doc, [MARKET_LABEL, SHORT_LABEL])


if __name__ == '__main__':
    unittest.main()
