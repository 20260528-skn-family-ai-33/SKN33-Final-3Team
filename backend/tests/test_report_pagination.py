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


class ContentsPaginationTest(unittest.TestCase):
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


if __name__ == '__main__':
    unittest.main()
