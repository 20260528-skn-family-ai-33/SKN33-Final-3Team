"""PDF 한글 글꼴: Chromium이 거부하는 글꼴 테이블을 빼고, 글꼴을 읽지 못하면 한글 없는 PDF 대신 오류를 낸다(#95 PR 리뷰)."""
import io
import tempfile
import unittest
from pathlib import Path

from backend.app.api.common import ApiError
from backend.app.api.config import Settings


def korean_font():
    font = Path(Settings().get('API_REPORT_FONT', 'C:/Windows/Fonts/malgun.ttf'))
    return font if font.is_file() else None


class ReportFontTest(unittest.TestCase):
    def setUp(self):
        self.font = korean_font()
        if not self.font:
            self.skipTest('PDF 한글 글꼴 없음')
        try:
            from fontTools.ttLib import TTFont
            from fontTools.ttLib.tables.DefaultTable import DefaultTable
        except ImportError:
            self.skipTest('fontTools 없음')
        # 나눔고딕 TTF처럼 길이 0인 TSI3(VTT 편집용) 테이블이 있는 글꼴을 만든다. Chromium(OTS)은 이 글꼴 전체를 거부한다.
        font = TTFont(str(self.font), lazy=True)
        self.addCleanup(font.close)
        for tag in [t for t in font.keys() if t.startswith('TSI')]:   # CI의 나눔고딕처럼 원래 TSI*가 있으면 먼저 빼고 하나만 넣는다
            del font[tag]
        table = DefaultTable('TSI3')   # 해석하지 않고 바이트 그대로 저장(길이 0)
        table.data = b''
        font['TSI3'] = table
        for cmap in font['cmap'].tables:
            if cmap.isUnicode() and 32 in cmap.cmap:
                cmap.cmap[1] = cmap.cmap[32]
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.broken = Path(self.directory.name) / 'with-tsi3.ttf'
        font.save(str(self.broken))

    def render(self, font_path, text='미국 화장품', **settings):
        from pypdf import PdfReader
        from backend.app.services import report_templates as rt
        from backend.app.services.report_pdf import font_css, template_pdf
        font_css.cache_clear()
        context = rt.sample_context('ondemand')
        context['conditions']['hs_items'][0].update(label=text, display=text)
        try:
            pdf = template_pdf('RPT_ON_DEMAND', context, {'API_REPORT_FONT': str(font_path), **settings})
        except ApiError as exc:
            if exc.code == 'REPORT_RENDERER_UNAVAILABLE':
                self.skipTest(f'PDF 렌더러 없음: {exc}')
            raise
        return ''.join(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages[:2])

    def test_rejected_font_tables_are_removed_before_embedding(self):
        from fontTools.ttLib import TTFont
        from backend.app.services.report_pdf import browser_font
        ready = browser_font(self.broken.read_bytes())
        cleaned = TTFont(io.BytesIO(ready))
        self.assertNotIn('TSI3', cleaned.keys())
        self.assertIn('glyf', cleaned.keys())   # 글자 모양은 그대로
        for cmap in cleaned['cmap'].tables:
            if cmap.isUnicode() and 32 in cmap.cmap:
                self.assertNotEqual(cmap.cmap.get(1), cmap.cmap[32])
        self.assertIs(browser_font(ready), ready)

    def font_status(self, data):
        """Chromium이 이 글꼴 바이트를 읽는지(loaded/error). 시스템 한글 글꼴 대체와 상관없이 지정 글꼴 자체를 본다."""
        import base64
        try:
            from playwright.sync_api import sync_playwright, Error as PlaywrightError
        except ImportError:
            self.skipTest('Playwright 없음')
        html = ("<style>@font-face{font-family:T;src:url(data:font/ttf;base64,"
                + base64.b64encode(data).decode('ascii') + ")}body{font-family:T}</style><p>미국 화장품</p>")
        try:
            with sync_playwright() as runtime:
                browser = runtime.chromium.launch()
                page = browser.new_page()
                page.set_content(html)
                page.evaluate('document.fonts.ready')
                status = page.evaluate("() => [...document.fonts].map(f => f.status)")
                browser.close()
        except PlaywrightError as exc:
            self.skipTest(f'브라우저 없음: {exc}')
        return status

    def test_font_with_tsi3_table_still_renders_korean(self):
        from backend.app.services.report_pdf import browser_font
        broken = self.broken.read_bytes()
        self.assertEqual(self.font_status(broken), ['error'])                 # 리뷰 재현: 그대로 넣으면 거부
        self.assertEqual(self.font_status(browser_font(broken)), ['loaded'])  # 테이블을 빼면 읽는다
        text = self.render(self.broken)
        self.assertIn('미국', text)
        self.assertIn('화장품', text)

    def test_unreadable_font_raises_instead_of_korean_less_pdf(self):
        garbage = Path(self.directory.name) / 'garbage.ttf'
        garbage.write_bytes(b'not a font' * 100)
        with self.assertRaises(ApiError) as caught:
            self.render(garbage)
        self.assertEqual(caught.exception.code, 'REPORT_FONT_UNAVAILABLE')
        self.assertFalse(caught.exception.retryable)   # 글꼴 파일 문제는 다시 해도 같다

    def test_font_not_yet_used_by_layout_is_loaded_before_check(self):
        # 레이아웃이 글꼴 로드를 시작하기 전이면 fonts.ready가 바로 끝나 'unloaded'로 실패했다(간헐적 리포트 실패).
        # 글자가 없는 문서로 그 상태를 고정해 재현한다.
        from unittest.mock import patch
        from backend.app.services.report_pdf import font_css, template_pdf
        font_css.cache_clear()
        with patch('backend.app.services.report_templates.render', return_value='<html><head></head><body></body></html>'):
            try:
                pdf = template_pdf('RPT_ON_DEMAND', {}, {'API_REPORT_FONT': str(self.font)})
            except ApiError as exc:
                if exc.code == 'REPORT_RENDERER_UNAVAILABLE':
                    self.skipTest(f'PDF 렌더러 없음: {exc}')
                raise
        self.assertTrue(pdf.startswith(b'%PDF'))

    def test_font_loading_at_first_check_waits_until_loaded(self):
        # 첫 확인에서 'loading'이면 기다렸다가 다시 확인해 'loaded'로 통과해야 한다(서버에서 로드가 늦는 경우).
        from unittest.mock import patch
        try:
            from playwright.sync_api import Page
        except ImportError:
            self.skipTest('Playwright 없음')
        from backend.app.services import report_pdf
        first_loading = ("() => { if (!window.__fontPolled) { window.__fontPolled = true; return ['loading']; }"
                         " return (" + report_pdf.FONT_STATES_JS + ")(); }")
        waits, original = [], Page.wait_for_timeout
        def spy(page, timeout):
            waits.append(timeout)
            return original(page, timeout)
        with patch.object(report_pdf, 'FONT_STATES_JS', first_loading), patch.object(Page, 'wait_for_timeout', spy):
            text = self.render(self.font)
        self.assertTrue(waits)            # 한 번 이상 기다린 뒤
        self.assertIn('미국', text)        # 한글이 든 PDF로 끝난다

    def test_font_still_loading_after_limit_is_retryable(self):
        # 글꼴 파일이 아니라 로드 시간이 문제인 경우다. 워커가 리포트 작업을 다시 시도하도록 retryable로 올린다.
        from unittest.mock import patch
        from backend.app.services import report_pdf
        with patch.object(report_pdf, 'FONT_STATES_JS', "() => ['loading']"), \
                self.assertRaises(ApiError) as caught:
            self.render(self.font, API_REPORT_FONT_LOAD_SECONDS='0.2')   # 설정값(문자열)으로 상한을 줄인다
        self.assertEqual(caught.exception.code, 'REPORT_FONT_UNAVAILABLE')
        self.assertTrue(caught.exception.retryable)
        self.assertNotIn('API_REPORT_FONT', caught.exception.message)   # 글꼴 파일 탓으로 안내하지 않는다


if __name__ == '__main__':
    unittest.main()
