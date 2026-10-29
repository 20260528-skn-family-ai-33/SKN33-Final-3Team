"""Render the bundled periodic HTML templates in an isolated, offline Chromium page."""
import base64
from pathlib import Path

from backend.app.api.common import ApiError


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
    encoded = base64.b64encode(font.read_bytes()).decode('ascii')
    html = render(report_type, context)
    font_css = ("<style>@font-face{font-family:ReportKorean;src:url(data:font/ttf;base64,"
                + encoded + ")}body{font-family:ReportKorean,sans-serif}</style>")
    html = html.replace('</head>', font_css + '</head>', 1)
    try:
        with sync_playwright() as runtime:
            with runtime.chromium.launch(headless=True, timeout=30000) as browser:
                page = browser.new_page(java_script_enabled=False, offline=True)
                page.route('**/*', lambda route: route.abort())
                page.set_default_timeout(30000)
                page.emulate_media(media='print')
                page.set_content(html, wait_until='load')
                page.evaluate('document.fonts.ready')
                return page.pdf(format='A4', prefer_css_page_size=True, print_background=True,
                                display_header_footer=False,
                                margin={'top':'0','bottom':'0','left':'0','right':'0'})
    except PlaywrightError as exc:
        raise ApiError(503, 'REPORT_RENDERER_UNAVAILABLE',
                       'HTML PDF 생성에 실패했습니다. 워커의 Chromium 설치와 실행 환경을 확인하세요.',
                       retryable=True) from exc
