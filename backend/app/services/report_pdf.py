"""Render bundled report HTML templates in an isolated, offline Chromium page."""
import base64
import logging
from functools import lru_cache
from pathlib import Path

from backend.app.api.common import ApiError


@lru_cache(maxsize=4)
def font_css(path, mtime_ns, size):
    # The file signature invalidates the cache when the configured font is replaced.
    encoded = base64.b64encode(Path(path).read_bytes()).decode('ascii')
    return ("<style>@font-face{font-family:ReportKorean;src:url(data:font/ttf;base64,"
            + encoded + ")}body{font-family:ReportKorean,sans-serif}</style>")


def template_pdf(report_type, context, settings):
    try:
        from playwright.sync_api import sync_playwright, Error as PlaywrightError
        from backend.app.services.report_templates import render
    except ImportError as exc:
        raise ApiError(503, 'REPORT_RENDERER_UNAVAILABLE',
                       '워커에 requirements-api.txt와 Playwright Chromium 설치가 필요합니다.') from exc

    font = Path(settings.get('API_REPORT_FONT', 'C:/Windows/Fonts/malgun.ttf'))
    if not font.is_file():
        raise ApiError(503, 'REPORT_FONT_UNAVAILABLE', '한글 PDF 글꼴 API_REPORT_FONT 설정이 필요합니다.')
    # Embed only the explicitly configured local font; articles cannot fetch remote assets.
    stamp = font.stat()
    embedded_font = font_css(str(font.resolve()), stamp.st_mtime_ns, stamp.st_size)
    html = render(report_type, context)
    html = html.replace('</head>', embedded_font + '</head>', 1)
    stage='BROWSER_START'
    try:
        with sync_playwright() as runtime:
            with runtime.chromium.launch(headless=True, executable_path=runtime.chromium.executable_path, timeout=30000) as browser:
                stage='PAGE_SETUP'
                page = browser.new_page(java_script_enabled=False, offline=True)
                page.route('**/*', lambda route: route.abort())
                page.set_default_timeout(30000)
                page.emulate_media(media='print')
                stage='HTML_LOAD'
                page.set_content(html, wait_until='load')
                stage='FONT_LOAD'
                page.evaluate('document.fonts.ready')
                stage='PDF_RENDER'
                return page.pdf(format='A4', prefer_css_page_size=True, print_background=True,
                                display_header_footer=False,
                                margin={'top':'0','bottom':'0','left':'0','right':'0'})
    except PlaywrightError as exc:
        logging.getLogger(__name__).exception('report_pdf_failed stage=%s',stage)
        message={
            'BROWSER_START':'PDF용 Chromium을 실행하지 못했습니다. 워커 실행 환경과 브라우저 경로를 확인하세요.',
            'PAGE_SETUP':'PDF용 브라우저 페이지 준비에 실패했습니다.',
            'HTML_LOAD':'리포트 HTML을 PDF 브라우저에 불러오지 못했습니다.',
            'FONT_LOAD':'PDF 한글 글꼴 로딩에 실패했습니다.',
            'PDF_RENDER':'리포트 PDF 변환에 실패했습니다.'}[stage]
        raise ApiError(503, 'REPORT_RENDERER_UNAVAILABLE',
                       message,
                       retryable=True) from exc
