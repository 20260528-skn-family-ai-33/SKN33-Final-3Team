"""Contents pagination: preserved metadata, real page destinations and print bounds."""
import base64
import copy
import io
import os
import re
import unittest
from pathlib import Path

from backend.app.services import report_templates as rt


def large_context(kind, count, long_metadata=False):
    ctx = rt.sample_context(kind)
    base = ctx.get('items', ctx['conditions']['hs_items'])[0]
    items = []
    for i in range(count):
        label = f'검증 품목 {i+1:03}'
        items.append({**copy.deepcopy(base), 'hs_code': str(330400+i),
                      'label': label, 'display': label + (' 긴 품목 설명' * 20 if long_metadata else '')})
    ctx['conditions']['hs_items'] = items
    if kind in ('monthly', 'quarterly'):
        ctx['items'] = items
        for row in ctx['interest_overview']:
            row['values'] = [copy.deepcopy(row['values'][0]) for _ in items]
    elif kind == 'weekly' and long_metadata:
        # Weekly uses label rather than display in its information table.
        ctx['conditions']['hs_items'] = [{**i, 'label': i['display']} for i in items]
    return ctx


LONG_COUNTRIES = ['보스니아 헤르체고비나', '중앙아프리카공화국', '세인트빈센트 그레나딘', '트리니다드 토바고', '미국']
LONG_PRODUCTS = ['기타 메이크업용이나 피부 미용용 제품류(선크림이나 선탠 제품을 포함한다)와 매니큐어용이나 페디큐어용 제품류',
                 '반도체 디바이스(광전성 반도체 디바이스를 포함한다)와 그 밖의 집적회로 부분품',
                 '승용자동차와 그 밖의 차량(주로 사람을 수송할 수 있도록 설계된 것)', '향수와 화장수', '립 메이크업용 제품류']


def promising_context(count, long=True):
    """국가 5개 × 품목 5개처럼 이름이 길어 줄이 바뀌는 유망시장 예측 결과."""
    ctx = rt.sample_context('ondemand')
    base = ctx['promising']['rows'][0]
    ctx['promising']['rows'] = [{**base, 'rank': i + 1, 'size': 1240, 'hs6': '330499', 'outlook': '증가 예상', 'exp_12m_usd': 1.23e9,
                                 'country': LONG_COUNTRIES[i % 5] if long else '미국',
                                 'product': LONG_PRODUCTS[(i // 5) % 5] if long else '향수'} for i in range(count)]
    return ctx


class ContentsPaginationTest(unittest.TestCase):
    def test_promising_rows_with_wrapped_names_get_their_own_height(self):
        for count in (25, 60):
            with self.subTest(rows=count):
                pages = rt.layout('RPT_ON_DEMAND', promising_context(count))['promising']
                parts = [(b['start'], b['end']) for page in pages for b in page if b['key'] == 'ptable']
                # 모든 행이 빠짐없이, 겹치지 않고 한 번씩 들어간다
                self.assertEqual([i for start, end in parts for i in range(start, end)], list(range(count)))
        short = rt.layout('RPT_ON_DEMAND', promising_context(25, long=False))['promising']
        long = rt.layout('RPT_ON_DEMAND', promising_context(25))['promising']
        first = lambda pages: next(b['end'] - b['start'] for b in pages[0] if b['key'] == 'ptable')
        # 줄이 바뀌는 행은 더 높으므로 첫 쪽에 들어가는 행 수가 줄어든다(예전에는 23행을 넣어 아래가 잘렸다)
        self.assertLess(first(long), first(short))
        self.assertGreater(len(long), len(short))


    def test_long_information_keeps_every_character_and_repeats_row_label(self):
        value = ' · '.join(f'품목 {i:03} 긴 설명 (HS {330400+i})' for i in range(100))
        plan = rt.contents_layout([{'title': '요약', 'page': 3}],
                                 [{'label': '관심 품목', 'value': value}], ['기준 안내'],
                                 {'period_label': '2026년 8월', 'title': '월간 리포트'})
        self.assertGreater(len(plan['pages']), 1)
        self.assertEqual(''.join(r['value'] for r in plan['info']), value)
        self.assertTrue(all(r['label'] == '관심 품목 (계속)' for r in plan['info'][1:]))

    def test_unbroken_information_is_split_without_losing_text(self):
        text = '<가나다&>' * 1000
        parts = rt._contents_chunks(text)
        self.assertGreater(len(parts), 1)
        self.assertEqual(''.join(parts), text)
        self.assertTrue(all(len(part) < 500 for part in parts))

    def test_numbering_is_contiguous_and_context_is_not_mutated(self):
        for kind in rt.KINDS:
            ctx = large_context(kind, 30)
            original = copy.deepcopy(ctx)
            html = rt.render(rt.KINDS[kind], ctx)
            count = len(re.findall(r'<section class="page', html))
            self.assertEqual(re.findall(r'<div class="no">(\d+) / (\d+)</div>', html),
                             [(str(n), str(count)) for n in range(1, count+1)])
            self.assertEqual(ctx, original)
            self.assertEqual(rt.render(rt.KINDS[kind], ctx), html)

    @unittest.skipUnless(os.environ.get('RUN_REPORT_PDF_TESTS') == '1', 'Requires Playwright Chromium')
    def test_word_contents_references_headings_for_long_promising_table(self):
        from docx import Document
        from docx.oxml.ns import qn
        from backend.app.api.config import Settings
        from backend.app.services.report_word import append_print_template

        doc = Document()
        append_print_template(doc, {'report_type': 'RPT_ON_DEMAND', 'context': promising_context(25)}, Settings())
        fields = [text.strip() for text in doc.element.xpath('.//w:instrText/text()')]
        refs = [f.split()[1] for f in fields if f.startswith('PAGEREF ')]
        bookmarks = doc.element.xpath('.//w:bookmarkStart/@w:name')
        self.assertTrue(any(f.startswith('TOC ') for f in fields))
        self.assertEqual(len(refs), 6)
        self.assertEqual(len(set(refs)), len(refs))
        for ref in refs:
            self.assertIn(ref, bookmarks)
        source_heading = next(p for p in doc.paragraphs if '데이터 기준 · 출처' in p.text and p._p.xpath('w:bookmarkStart'))
        self.assertIn(refs[-1], source_heading._p.xpath('w:bookmarkStart/@w:name'))
        self.assertEqual(doc.settings.element.find(qn('w:updateFields')).get(qn('w:val')), 'true')

    @unittest.skipUnless(os.environ.get('RUN_REPORT_PDF_TESTS') == '1', 'Requires Playwright Chromium')
    def test_quarterly_word_keeps_nested_charts_and_resolvable_contents(self):
        from docx import Document
        from docx.oxml.ns import qn
        from backend.app.api.config import Settings
        from backend.app.services.report_pdf import print_page
        from backend.app.services.report_word import append_print_template

        saved = {'report_type': 'RPT_QUARTERLY', 'context': rt.sample_context('quarterly')}
        settings = Settings()
        doc = Document()
        with print_page(saved['report_type'], saved['context'], settings, scale=2) as page:
            charts = page.locator('svg.chart')
            expected = [svg.screenshot(animations='disabled') for svg in charts.all()]
            self.assertGreater(len(expected), 1)
            target = page.evaluate("""() => {
                const sheets = document.querySelectorAll('section.page')
                const rows = document.querySelectorAll('.toc [data-toc-page]')
                const page = Number(rows[0].dataset.tocPage)
                const heading = sheets[page - 1].querySelector('h2')
                const wrapper = document.createElement('div')
                heading.replaceWith(wrapper)
                wrapper.append(heading)
                rows[1].dataset.tocPage = sheets.length + 1
                return page
            }""")
            append_print_template(doc, saved, settings, page=page)
        actual = [doc.part.related_parts[shape._inline.xpath('.//a:blip')[0].get(qn('r:embed'))].blob
                  for shape in doc.inline_shapes]
        self.assertEqual(len(actual), len(expected))
        self.assertTrue(all(a == b for a, b in zip(actual, expected)))
        bookmarks = set(doc.element.xpath('.//w:bookmarkStart/@w:name'))
        self.assertIn(f'ReportPage{target}', bookmarks)
        refs = [f.split()[1] for f in doc.element.xpath('.//w:instrText/text()') if 'PAGEREF ' in f]
        self.assertIn(f'ReportPage{target}', refs)
        self.assertTrue(set(refs) <= bookmarks)

    @unittest.skipUnless(os.environ.get('RUN_REPORT_PDF_TESTS') == '1', 'Requires Playwright Chromium')
    def test_print_bounds_toc_destinations_and_pdf_page_count(self):
        from playwright.sync_api import sync_playwright
        from pypdf import PdfReader
        from backend.app.api.config import Settings

        font = Path(Settings().get('API_REPORT_FONT', 'C:/Windows/Fonts/malgun.ttf'))
        encoded = base64.b64encode(font.read_bytes()).decode('ascii')
        with sync_playwright() as runtime, runtime.chromium.launch() as browser:
            page = browser.new_page(java_script_enabled=False, offline=True)
            page.route('**/*', lambda route: route.abort())
            page.emulate_media(media='print')
            for kind in rt.KINDS:
                for count, long in ((1, False), (10, False), (30, False), (100, True)):
                    with self.subTest(kind=kind, items=count, long_metadata=long):
                        html = rt.render(rt.KINDS[kind], large_context(kind, count, long))
                        html = html.replace('</head>', '<style>@font-face{font-family:ReportKorean;src:url(data:font/ttf;base64,'
                                            + encoded + ')}body{font-family:ReportKorean,sans-serif}</style></head>')
                        page.set_content(html)
                        page.evaluate('document.fonts.ready')
                        result = page.evaluate('''() => {
                            const pages=[...document.querySelectorAll('.page')];
                            const overflow=pages.flatMap((p,i)=>{
                                const bottom=p.querySelector('.ftr').getBoundingClientRect().top;
                                return [...p.children].filter(e=>!e.matches('.ftr,.hdr,.top'))
                                    .filter(e=>e.getBoundingClientRect().bottom>bottom+0.5)
                                    .map(e=>({page:i+1,tag:e.tagName,cls:e.className}));
                            });
                            const toc=[...document.querySelectorAll('.toc .row')].map(r=>{
                                const title=r.querySelector('.t').textContent.trim();
                                const target=Number(r.lastElementChild.textContent);
                                const heading=pages[target-1]?.querySelector('h2')?.textContent || '';
                                return {title,target,heading};
                            });
                            return {count:pages.length,overflow,toc};
                        }''')
                        self.assertEqual(result['overflow'], [])
                        for entry in result['toc']:
                            self.assertIn(entry['title'], entry['heading'], entry)
                        # Export the two previously failing cases and check physical pages.
                        if kind in ('monthly', 'ondemand') and count in (10, 30):
                            pdf = page.pdf(format='A4', prefer_css_page_size=True, print_background=True,
                                           margin={'top':'0','bottom':'0','left':'0','right':'0'})
                            self.assertEqual(len(PdfReader(io.BytesIO(pdf)).pages), result['count'])
                            output = os.environ.get('REPORT_PAGINATION_QA_DIRECTORY')
                            if output:
                                folder = Path(output); folder.mkdir(parents=True, exist_ok=True)
                                (folder / f'{kind}-{count}-items.pdf').write_bytes(pdf)
                                (folder / f'{kind}-{count}-items.html').write_text(html, encoding='utf-8')
            # 유망시장: 줄바꿈되는 긴 국가·품목명도 바닥글 위에서 끝나고 다음 쪽에서 이어진다
            for count in (25, 60):
                with self.subTest(promising=count):
                    html = rt.render('RPT_ON_DEMAND', promising_context(count))
                    html = html.replace('</head>', '<style>@font-face{font-family:ReportKorean;src:url(data:font/ttf;base64,'
                                        + encoded + ')}body{font-family:ReportKorean,sans-serif}</style></head>')
                    page.set_content(html)
                    page.evaluate('document.fonts.ready')
                    rows = page.evaluate('''() => [...document.querySelectorAll('.page')].flatMap((p,i)=>{
                        const bottom=p.querySelector('.ftr').getBoundingClientRect().top;
                        return [...p.querySelectorAll('table[style*="table-layout:fixed"] tr')].filter(r=>!r.querySelector('th'))
                            .map(r=>({page:i+1,rank:r.cells[0].textContent.split('/')[0].trim(),over:r.getBoundingClientRect().bottom>bottom+0.5}));
                    })''')
                    self.assertEqual([r['rank'] for r in rows], [str(i + 1) for i in range(count)])
                    self.assertEqual([r for r in rows if r['over']], [])


if __name__ == '__main__':
    unittest.main()
