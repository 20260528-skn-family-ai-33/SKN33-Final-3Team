"""Render bundled report HTML templates in an isolated, offline Chromium page."""
from contextlib import contextmanager
import base64
import logging
from functools import lru_cache
import math
from pathlib import Path
import time

from backend.app.api.common import ApiError

FONT_FAMILY = 'ReportKorean'
# 리포트 생성은 report 행 잠금을 잡은 트랜잭션 안에서 돈다(worker.py). 오래 기다리지 않고 실패해 잠금을 풀고 워커 재시도에 맡긴다.
# 로컬 측정: 맑은 고딕 로드 0.005~0.042초. 서버(나눔고딕)는 재지 않은 임시값이라 API_REPORT_FONT_LOAD_SECONDS로 바꿀 수 있다.
FONT_LOAD_SECONDS = 5
FONT_LOAD_MAX_SECONDS = 30   # 페이지 기본 대기 시간(set_default_timeout)과 같다. 잠금을 잡고 이보다 오래 기다리지 않는다


def font_load_seconds(settings):
    """API_REPORT_FONT_LOAD_SECONDS. 숫자가 아니거나 음수·nan·inf면 기본값을 쓴다(nan이면 대기가 끝나지 않는다).
    FONT_LOAD_MAX_SECONDS보다 크면 그 값으로 줄인다."""
    value = settings.get('API_REPORT_FONT_LOAD_SECONDS', FONT_LOAD_SECONDS)
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        seconds = math.nan
    if not math.isfinite(seconds) or seconds < 0:
        logging.getLogger(__name__).warning('report_font_load_seconds_invalid value=%r default=%s', value, FONT_LOAD_SECONDS)
        return FONT_LOAD_SECONDS
    if seconds > FONT_LOAD_MAX_SECONDS:
        logging.getLogger(__name__).warning('report_font_load_seconds_capped value=%r max=%s', value, FONT_LOAD_MAX_SECONDS)
        return FONT_LOAD_MAX_SECONDS
    return seconds


FONT_STATES_JS = """() => [...document.fonts]
    .filter(f => f.family.replace(/["']/g, '') === '""" + FONT_FAMILY + """').map(f => f.status)"""


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
    return ("<style>@font-face{font-family:" + FONT_FAMILY + ";src:url(data:font/ttf;base64,"
            + encoded + ")}body{font-family:" + FONT_FAMILY + ",sans-serif}</style>")


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
                # 지정한 한글 글꼴을 브라우저가 거부하면 한글이 빠진 PDF가 조용히 만들어진다. 저장 전에 로드 결과를 확인한다.
                # fonts.ready는 레이아웃이 글꼴 로드를 시작하기 전이면 바로 끝나므로('unloaded'/'loading'에서 확인) 로드를 직접 시작한다.
                # JS를 끈 페이지는 setTimeout이 돌지 않아 페이지 안에서 기다리면 상한을 둘 수 없다. 상태를 여기서 확인한다.
                page.evaluate("() => { document.fonts.load('16px " + FONT_FAMILY + "', '가').catch(() => {}); }")
                deadline = time.monotonic() + font_load_seconds(settings)
                while True:
                    states = page.evaluate(FONT_STATES_JS)
                    if (states and all(s == 'loaded' for s in states)) or 'error' in states or time.monotonic() > deadline:
                        break
                    page.wait_for_timeout(100)   # time.sleep과 달리 기다리는 동안 route 등 Playwright 이벤트를 처리한다
                if not states or any(s != 'loaded' for s in states):
                    logging.getLogger(__name__).warning('report_font_not_loaded states=%s', states)
                    # 'error'는 글꼴 파일을 고쳐야 하고, 로드가 끝나지 않은 경우는 다시 시도하면 될 수 있다.
                    if states and 'error' not in states:
                        raise ApiError(503, 'REPORT_FONT_UNAVAILABLE',
                                       'PDF 한글 글꼴을 제시간에 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.', retryable=True)
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
