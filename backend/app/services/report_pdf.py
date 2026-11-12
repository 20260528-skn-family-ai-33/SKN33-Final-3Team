"""Render bundled report HTML templates in an isolated, offline Chromium page."""
from contextlib import contextmanager
import base64
import logging
from functools import lru_cache
from pathlib import Path

from backend.app.api.common import ApiError


def browser_font(data):
    """Chromium(OTS)이 거부하는 글꼴 테이블을 뺀다. 나눔고딕 TTF의 VTT 편집용 TSI* 테이블(예: 길이 0인 TSI3)이 있으면
    글꼴 전체가 거부돼 한글이 빠진 PDF가 만들어진다. 화면 표시에 쓰이지 않는 테이블이라 빼도 글자 모양은 같다."""
    try:
        import io
        from fontTools.ttLib import TTFont
        font = TTFont(io.BytesIO(data), lazy=True)
        tags = [tag for tag in font.keys() if tag.startswith('TSI')]
        for tag in tags:
            del font[tag]
        changed = bool(tags)
        for table in font['cmap'].tables:
            if not table.isUnicode() or 32 not in table.cmap:
                continue
            # NanumGothic shares the space glyph with control characters.
            # Chromium can otherwise emit U+0001 for spaces in the PDF's ToUnicode map.
            for code in range(32):
                if table.cmap.get(code) == table.cmap[32]:
                    del table.cmap[code]
                    changed = True
        if not changed:
            return data
        out = io.BytesIO()
        font.save(out)
        return out.getvalue()
    except Exception:   # fontTools가 없거나 읽지 못하는 형식이면 원본을 쓰고, 로드 확인(template_pdf)에서 거른다
        return data


@lru_cache(maxsize=4)
def font_css(path, mtime_ns, size):
    # The file signature invalidates the cache when the configured font is replaced.
    encoded = base64.b64encode(browser_font(Path(path).read_bytes())).decode('ascii')
    return ("<style>@font-face{font-family:ReportKorean;src:url(data:font/ttf;base64,"
            + encoded + ")}body{font-family:ReportKorean,sans-serif}</style>")


@contextmanager
def print_page(report_type, context, settings, *, scale=1):
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
            from backend.app.services.report_browser import launch_chromium
            with launch_chromium(runtime) as browser:
                stage='PAGE_SETUP'
                page = browser.new_page(java_script_enabled=False, offline=True, device_scale_factor=scale)
                page.route('**/*', lambda route: route.abort())
                page.set_default_timeout(30000)
                page.emulate_media(media='print')
                stage='HTML_LOAD'
                page.set_content(html, wait_until='load')
                stage='FONT_LOAD'
                page.evaluate('document.fonts.ready')
                # 지정한 한글 글꼴을 브라우저가 거부하면 한글이 빠진 PDF가 조용히 만들어진다. 저장 전에 로드 결과를 확인한다.
                states = page.evaluate("""() => [...document.fonts]
                    .filter(f => f.family.replace(/["']/g, '') === 'ReportKorean').map(f => f.status)""")
                if not states or any(s != 'loaded' for s in states):
                    raise ApiError(503, 'REPORT_FONT_UNAVAILABLE',
                                   'PDF 한글 글꼴을 브라우저가 읽지 못했습니다. API_REPORT_FONT의 글꼴 파일을 확인하세요.')
                stage='DOCUMENT_RENDER'
                yield page
    except PlaywrightError as exc:
        logging.getLogger(__name__).exception('report_pdf_failed stage=%s',stage)
        message={
            'BROWSER_START':'PDF용 Chromium을 실행하지 못했습니다. 워커 실행 환경과 브라우저 경로를 확인하세요.',
            'PAGE_SETUP':'PDF용 브라우저 페이지 준비에 실패했습니다.',
            'HTML_LOAD':'리포트 HTML을 PDF 브라우저에 불러오지 못했습니다.',
            'FONT_LOAD':'PDF 한글 글꼴 로딩에 실패했습니다.',
            'DOCUMENT_RENDER':'리포트 인쇄 양식을 파일로 변환하지 못했습니다.'}[stage]
        raise ApiError(503, 'REPORT_RENDERER_UNAVAILABLE',
                       message,
                       retryable=True) from exc


def template_pdf(report_type, context, settings):
    with print_page(report_type, context, settings) as page:
        return pdf_from_page(page)


def pdf_from_page(page):
    return page.pdf(format='A4', prefer_css_page_size=True, print_background=True,
                    display_header_footer=False,
                    margin={'top':'0','bottom':'0','left':'0','right':'0'})
