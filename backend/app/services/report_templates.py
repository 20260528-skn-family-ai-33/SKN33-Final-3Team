"""정기 리포트(월간·주간·분기) HTML 템플릿 렌더링.

템플릿은 backend/app/templates/reports/에 있고, 수치·문장·기사·목록은 모두 컨텍스트 변수로 받습니다.
금액은 결과표의 USD 원값을 그대로 받고 표시 형식(백만 달러·증감률·▲▼)만 여기서 바꿉니다.
그래프는 JavaScript 없이 SVG로 그려 HTML → PDF 변환에서도 그대로 나옵니다.

미리보기: python -m backend.app.services.report_templates monthly --out tmp/report_monthly.html
"""
import argparse
import json
import math
import re
from datetime import date, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, UndefinedError, select_autoescape
from markupsafe import Markup, escape

TEMPLATE_DIR = Path(__file__).resolve().parents[1] / 'templates' / 'reports'
SAMPLE_DIR = TEMPLATE_DIR / 'samples'
TEMPLATES = {'RPT_MONTHLY': 'monthly.html', 'RPT_WEEKLY': 'weekly.html', 'RPT_QUARTERLY': 'quarterly.html'}
KINDS = {'monthly': 'RPT_MONTHLY', 'weekly': 'RPT_WEEKLY', 'quarterly': 'RPT_QUARTERLY'}

ACCENT = '#006849'
UP, DOWN, MUTED, GRID, PREVIOUS = '#0B7A4B', '#C0392B', '#8A8F98', '#E6E3DD', '#A8A39B'
# 관심 국가 선 색상. 같은 리포트 안의 모든 그래프에서 국가 순서대로 같은 색을 씁니다.
SERIES_COLORS = ['#E07B24', '#2E6DB4', '#8E5BB5', '#16877C', '#C2477A', '#6B7A2A']
MINUS = '−'


# ---------- 표시 형식 ----------

def _number(value, digits):
    return f'{abs(value):,.{digits}f}'


def musd(value, digits=1):
    """USD → 백만 달러 숫자 문자열 (단위 글자는 템플릿에서 붙임)."""
    if value is None:
        return '—'
    text = _number(value / 1e6, digits)
    return (MINUS + text) if value < 0 else text


def num(value, digits=0):
    if value is None:
        return '—'
    text = _number(value, digits)
    return (MINUS + text) if value < 0 else text


def pct(value, digits=1):
    return '—' if value is None else num(value, digits) + '%'


def signed_pct(value, digits=1):
    if value is None:
        return '—'
    return ('+' if value > 0 else MINUS if value < 0 else '') + _number(value, digits) + '%'


def signed(value, digits=0):
    if value is None:
        return '—'
    return ('+' if value > 0 else MINUS if value < 0 else '') + _number(value, digits)


def change(value, digits=1, new=False):
    """증감률을 ▲(증가)/▼(감소) 색 표시로. 비교 값이 0이라 계산할 수 없으면 new=True → '신규'."""
    if new:
        return Markup('<span class="chg new">신규</span>')
    if value is None:
        return Markup('<span class="chg none">—</span>')
    if value == 0:
        return Markup('<span class="chg flat">– 0.0%</span>')
    cls, mark = ('up', '▲') if value > 0 else ('down', '▼')
    return Markup(f'<span class="chg {cls}">{mark} {_number(value, digits)}%</span>')


def emph(text):
    """문장 안의 **강조**를 굵은 강조색으로. 나머지는 HTML 이스케이프."""
    escaped = str(escape(text))
    return Markup(re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', escaped))


def _as_datetime(value):
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    return datetime.fromisoformat(str(value).replace('Z', '+00:00'))


def ymd(value):
    return '—' if value is None else _as_datetime(value).strftime('%Y.%m.%d')


def ymdhm(value):
    return '—' if value is None else _as_datetime(value).strftime('%Y.%m.%d %H:%M')


def circled(n):
    return chr(0x2460 + n - 1) if 1 <= n <= 20 else f'({n})'


# ---------- 그래프 (SVG) ----------

def nice_scale(values, ticks=5, zero=False):
    """축 눈금: 데이터 범위를 1·2·2.5·5×10^k 간격으로 덮는 (최소, 최대, 눈금 목록)."""
    data = [v for v in values if v is not None]
    if not data:
        return 0, 1, [0, 1]
    low, high = min(data), max(data)
    if zero:
        low = min(0, low)
    if high == low:
        high = low + (abs(low) or 1)
    raw = (high - low) / max(ticks - 1, 1)
    power = 10 ** math.floor(math.log10(raw))
    step = next(m * power for m in (1, 2, 2.5, 5, 10) if m * power >= raw)
    start = math.floor(low / step) * step
    end = math.ceil(high / step) * step
    if not zero and start == low and start - step >= 0:
        start -= step  # 최저값이 축에 붙지 않게 한 칸 여유
    count = int(round((end - start) / step))
    return start, end, [start + i * step for i in range(count + 1)]


def _segments(values):
    """[(위치, 값)] 묶음 목록. None에서 끊어 연속 구간끼리만 묶습니다."""
    segments, current = [], []
    for i, v in enumerate(values):
        if v is None:
            if current:
                segments.append(current)
            current = []
        else:
            current.append((i, v))
    if current:
        segments.append(current)
    return segments


def _fit(text, width, font_px):
    """SVG 글자가 width(SVG 단위)를 넘으면 뒤를 …로 줄입니다. 글자 폭은 _em() 기준."""
    text = str(text)
    if _em(text) * font_px <= width:
        return text
    while text and (_em(text) + 1) * font_px > width:
        text = text[:-1]
    return text.rstrip() + '…'


def _tick_label(value):
    return f'{value:,.0f}' if abs(value - round(value)) < 1e-9 else f'{value:,.1f}'


def line_chart(labels, series, *, height=190, width=640, divisor=1e6, digits=1, zero=False):
    """series: [{values(USD 또는 원값, divisor로 나눠 표시), color?, dashed?, area?}]. 선 끝에 마지막 값 라벨."""
    left, right, top, bottom = 44, 64, 10, 24
    scaled = [[None if v is None else v / divisor for v in s['values']] for s in series]
    low, high, ticks = nice_scale([v for vals in scaled for v in vals], zero=zero)
    plot_w, plot_h = width - left - right, height - top - bottom
    step = plot_w / max(len(labels) - 1, 1)
    x = lambda i: left + i * step
    y = lambda v: top + plot_h - (v - low) / (high - low) * plot_h
    out = [f'<svg class="chart" viewBox="0 0 {width} {height}" width="100%" role="img">']
    for t in ticks:
        out.append(f'<line x1="{left}" x2="{width - right}" y1="{y(t):.1f}" y2="{y(t):.1f}" stroke="{GRID}" stroke-width="1"/>')
        out.append(f'<text x="{left - 6}" y="{y(t) + 3:.1f}" text-anchor="end" class="axis">{_tick_label(t)}</text>')
    for i, label in enumerate(labels):
        out.append(f'<text x="{x(i):.1f}" y="{height - 6}" text-anchor="middle" class="axis">{escape(label)}</text>')
    ends = []
    for s, vals in zip(series, scaled):
        color = s.get('color', ACCENT)
        dash = ' stroke-dasharray="5 4"' if s.get('dashed') else ''
        width_px = 1.6 if s.get('dashed') else 2
        # 값이 없는 달(null)은 이어 그리지 않고 선을 끊습니다. 앞뒤가 모두 비어 있는 점은 작은 원으로 표시합니다.
        for segment in _segments(vals):
            points = [(x(i), y(v)) for i, v in segment]
            path = ' '.join(f'{px:.1f},{py:.1f}' for px, py in points)
            if s.get('area') and len(points) > 1:
                base = y(low)
                out.append(f'<polygon points="{points[0][0]:.1f},{base:.1f} {path} {points[-1][0]:.1f},{base:.1f}" fill="{color}" opacity="0.07"/>')
            if len(points) > 1:
                out.append(f'<polyline points="{path}" fill="none" stroke="{color}" stroke-width="{width_px}"{dash}/>')
            else:
                out.append(f'<circle cx="{points[0][0]:.1f}" cy="{points[0][1]:.1f}" r="1.8" fill="{color}"/>')
        last = next(((i, v) for i, v in reversed(list(enumerate(vals))) if v is not None), None)
        if last:
            ends.append([x(last[0]), y(last[1]), last[1], color])
    show_labels = end_labels_fit(len(ends), height)
    # 끝 값 라벨이 겹치지 않게 세로로 벌리되 그래프 영역(top ~ top+plot_h) 안에 둡니다.
    # 라벨이 다 들어갈 자리가 없으면 그리지 않고, 템플릿이 end_labels_fit()으로 확인해 범례에 끝 값을 붙입니다.
    ends.sort(key=lambda e: e[1])
    for i in range(1, len(ends)):
        if ends[i][1] - ends[i - 1][1] < END_GAP:
            ends[i][1] = ends[i - 1][1] + END_GAP
    if ends and ends[-1][1] > top + plot_h:
        shift = ends[-1][1] - (top + plot_h)
        for e in ends:
            e[1] -= shift
        for i in range(len(ends) - 2, -1, -1):  # 위로 올린 뒤 위쪽 라벨끼리 다시 겹치지 않게
            ends[i][1] = min(ends[i][1], ends[i + 1][1] - END_GAP)
    for px, py, value, color in ends:
        out.append(f'<circle cx="{px:.1f}" cy="{y(value):.1f}" r="3" fill="{color}"/>')
        if show_labels:
            out.append(f'<text x="{px + 7:.1f}" y="{py + 3:.1f}" class="end" fill="{color}">{num(value, digits)}</text>')
    out.append('</svg>')
    return Markup(''.join(out))


END_GAP = 11  # 끝 값 라벨 사이 최소 간격 (SVG 단위)


def end_labels_fit(count, height):
    """끝 값 라벨 count개가 높이 height인 line_chart의 그래프 영역 안에 겹치지 않고 들어가는지."""
    return count <= 1 or (count - 1) * END_GAP <= height - 34


def last_value(values):
    """마지막 실제 값 (없으면 None)"""
    return next((v for v in reversed(values) if v is not None), None)


def bar_chart(labels, values, colors, *, height=200, width=330, divisor=1e6, digits=0, show=()):
    """세로 막대. show: 값 라벨을 표시할 막대 위치 목록."""
    left, right, top, bottom = 40, 6, 14, 22
    scaled = [None if v is None else v / divisor for v in values]
    low, high, ticks = nice_scale(scaled, zero=True)
    plot_w, plot_h = width - left - right, height - top - bottom
    slot = plot_w / max(len(labels), 1)
    bar = slot * 0.62
    y = lambda v: top + plot_h - (v - low) / (high - low) * plot_h
    out = [f'<svg class="chart" viewBox="0 0 {width} {height}" width="100%" role="img">']
    for t in ticks:
        out.append(f'<line x1="{left}" x2="{width - right}" y1="{y(t):.1f}" y2="{y(t):.1f}" stroke="{GRID}"/>')
        out.append(f'<text x="{left - 5}" y="{y(t) + 3:.1f}" text-anchor="end" class="axis">{_tick_label(t)}</text>')
    for i, (label, v, color) in enumerate(zip(labels, scaled, colors)):
        cx = left + slot * i + slot / 2
        if v is not None:
            out.append(f'<rect x="{cx - bar / 2:.1f}" y="{y(v):.1f}" width="{bar:.1f}" height="{y(low) - y(v):.1f}" fill="{color}"/>')
            if i in show:
                out.append(f'<text x="{cx:.1f}" y="{y(v) - 4:.1f}" text-anchor="middle" class="val">{num(v, digits)}</text>')
        out.append(f'<text x="{cx:.1f}" y="{height - 6}" text-anchor="middle" class="axis">{escape(label)}</text>')
    out.append('</svg>')
    return Markup(''.join(out))


def hbar_chart(rows, colors, *, label_width=120, width=640, row_height=26, digits=1, divisor=1, tail_width=0, peak=None):
    """가로 막대. rows: [{label, values:[현재, 비교...], tail?:{text, badge?}}]. digits는 막대별 목록도 가능.
    막대 길이는 peak(없으면 rows 최대값) 기준 — 여러 쪽에 나눠 그릴 때 같은 peak를 주면 막대 길이를 비교할 수 있습니다."""
    top = peak if peak is not None else max((v for r in rows for v in r['values'] if v is not None), default=0)
    peak = top / divisor or 1
    bars = len(colors)
    bar_h = 8 if bars > 1 else 12
    plot_w = width - label_width - tail_width - 40
    height = row_height * len(rows) + 4
    out = [f'<svg class="chart" viewBox="0 0 {width} {height}" width="100%" role="img">']
    for i, r in enumerate(rows):
        top = i * row_height + (row_height - bars * (bar_h + 2)) / 2
        label = _fit(r['label'], label_width - 6, 10)  # 라벨이 막대 위로 겹치지 않게 칸 폭에 맞춰 줄임
        out.append(f'<text x="0" y="{i * row_height + row_height / 2 + 4:.1f}" class="lbl">{escape(label)}</text>')
        for j, (v, color) in enumerate(zip(r['values'], colors)):
            if v is None:
                continue
            w = max(v / divisor / peak * plot_w, 1)
            yy = top + j * (bar_h + 2)
            out.append(f'<rect x="{label_width}" y="{yy:.1f}" width="{w:.1f}" height="{bar_h}" rx="2" fill="{color}"/>')
            cls = 'val' if j == 0 else 'axis'
            places = digits[j] if isinstance(digits, (list, tuple)) else digits
            out.append(f'<text x="{label_width + w + 5:.1f}" y="{yy + bar_h - 1:.1f}" class="{cls}">{num(v / divisor, places)}</text>')
        tail = r.get('tail')
        if tail:
            ty = i * row_height + row_height / 2 + 4
            out.append(f'<text x="{width - tail_width}" y="{ty:.1f}" class="tail">{escape(tail["text"])}</text>')
            if tail.get('badge'):
                bx = width - tail_width + 44
                out.append(f'<rect x="{bx}" y="{ty - 11:.1f}" width="30" height="15" rx="3" fill="#FBE9DD"/>'
                           f'<text x="{bx + 15}" y="{ty:.1f}" text-anchor="middle" class="badge">{escape(tail["badge"])}</text>')
    out.append('</svg>')
    return Markup(''.join(out))


# ---------- 쪽 나누기 ----------
# 쪽 높이가 고정이라 넘치는 내용은 잘립니다. 블록 높이를 mm로 추정해 넘치면 다음 쪽(“(계속)”)으로 보냅니다.
# 한글 1자 = 1em(Noto Sans KR 기준)으로 계산하며, 폭이 더 좁은 글꼴(맑은 고딕)에서는 아래 여백이 조금 남습니다.

BODY_MM = 252   # A4 297mm − 위 21mm − 아래 20mm − 여유 4mm
TEXT_MM = 178   # 210mm − 좌우 16mm
PT_MM = 0.3528
H3_MM = 13      # 소제목(위 여백 포함)
ROW_MM = 4.3    # 표 한 행의 위아래 여백 (줄 높이는 별도)
LINE = {7: 3.83, 7.6: 4.2, 7.8: 4.27, 8.2: 4.48, 9: 4.92, 9.6: 5.25}  # 글자 크기(pt) → 줄 높이(mm), line-height 1.55


def _em(text):
    width = 0.0
    for ch in str(text):
        if ch == ' ':
            width += 0.3
        elif ord(ch) < 0x2E80:
            width += 0.6 if ch.isalnum() else 0.4
        else:
            width += 1.0
    return width


def _lines(text, size, width_mm):
    if text is None or text == '':
        return 0
    per_line = width_mm / (size * PT_MM)
    return max(1, math.ceil(_em(text) * 1.02 / per_line))


def _row(cells, size=7.8):
    """표 한 행 높이. cells: [(문자열, 칸 너비 mm)]"""
    return ROW_MM + LINE[size] * max([1] + [_lines(text, size, width) for text, width in cells])


def _heading(sub=None):
    return 9.7 + (LINE[7.6] * min(_lines(sub, 7.6, TEXT_MM), 2) + 4 if sub else 0)  # 소제목은 CSS로 최대 두 줄


def _sentences(sentences, title=0):
    """요약 문장 상자. 문장 단위로 나뉘며 다음 쪽에서 번호가 이어집니다. title: 위 소제목 높이"""
    return _table('sentences', [LINE[9] * _lines(s.replace('**', ''), 9, TEXT_MM - 15) + 1.2 for s in sentences],
                  head=title + 11, empty=0)


def _legend_lines(names, width=TEXT_MM):
    """범례 줄 수. 항목 = 이름 + 표식(약 4.5mm) + 간격 4mm"""
    lines, used = 1, 0.0
    for name in names:
        w = _em(name) * 7 * PT_MM + 8.5
        if used and used + w > width:
            lines, used = lines + 1, 0.0
        used += w
    return lines


def _legend(names):
    # 위아래 여백 1mm씩 + 7pt 줄. 범례는 flex gap(4mm)이 줄 사이에도 붙습니다 (실측 1줄 5.6mm, 6줄 42.9mm)
    lines = _legend_lines(names)
    return 2 + 3.83 * lines + 4 * (lines - 1)


def _svg(height, width=640):
    """본문 폭에 맞춰 늘어나는 SVG 높이(mm)"""
    return height * TEXT_MM / width


def _article(a):
    meta = ' · '.join(str(a.get(k)) for k in ('country', 'item_label', 'published_date', 'source_label', 'office') if a.get(k))
    return (5.5 + LINE[9.6] * _lines(a.get('title'), 9.6, TEXT_MM - 6) + LINE[7] * _lines(meta, 7, TEXT_MM) + 1
            + LINE[8.2] * _lines(a.get('summary'), 8.2, TEXT_MM) + 4.6)


def _table(key, rows, head=None, empty=8.6, **extra):
    """나눌 수 있는 표 블록. 행이 없으면 안내 행 높이만 차지합니다."""
    return dict(extra, key=key, head=H3_MM + 9 if head is None else head, rows=rows, empty=empty)


def flow(blocks, first, rest):
    """블록을 쪽에 채웁니다. 표·기사 목록(rows)은 행 단위로 나누고, 소제목만 쪽 끝에 남기지 않습니다.
    반환: 쪽 목록 [[{key, start, end, cont, ...}]]. start:end는 그 쪽에 넣을 행 범위, cont는 이어지는 표 여부."""
    pages, page, room = [], [], first

    def place(entry, height):
        nonlocal page, room
        if page and height > room:
            pages.append(page)
            page, room = [], rest
        page.append(entry)
        room -= height

    for block in blocks:
        info = {k: v for k, v in block.items() if k not in ('height', 'head', 'rows', 'empty')}
        rows = block.get('rows')
        if rows is None:
            place(dict(info, start=0, end=0, cont=False), block['height'])
            continue
        if not rows:
            place(dict(info, start=0, end=0, cont=False), block['head'] + block['empty'])
            continue
        head, i = block['head'], 0
        while i < len(rows):
            used = head + rows[i]
            if page and used > room:
                pages.append(page)
                page, room = [], rest
            end = i + 1
            while end < len(rows) and used + rows[end] <= room:
                used += rows[end]
                end += 1
            page.append(dict(info, start=i, end=end, cont=i > 0))
            room -= used
            i = end
            if i < len(rows):
                pages.append(page)
                page, room = [], rest
    pages.append(page)
    return pages


def _summary_blocks(ctx):
    """월간·분기 요약: KPI · 요약 문장 · 품목 표 · 관심 국가 표(품목 3개씩) · 확인할 점"""
    items = ctx['items']
    blocks = [{'key': 'kpis', 'height': 29}, _sentences(ctx['summary']['sentences'])]
    blocks.append(_table('items', [ROW_MM + LINE[7.8] * _lines(i['label'], 7.8, 26) + LINE[7] for i in items]))
    groups = [list(range(n, min(n + 3, len(items)))) for n in range(0, len(items), 3)] or [[]]
    header = max([1] + [_lines(items[i]['label'], 7.8, 20) for g in groups for i in g])
    for g in groups:
        blocks.append(_table('overview', [8.6] * len(ctx['interest_overview']), head=H3_MM + ROW_MM + LINE[7.8] * header,
                             empty=0, group=g))
    checks = ctx['summary']['check_points']
    if checks:
        blocks.append(_table('checks', [_row([(r['target'], 28), (r['content'], 145)]) for r in checks]))
    return blocks


def _article_blocks(articles, tail=None):
    blocks = [_table('articles', [_article(a) for a in articles], head=0, empty=15)]
    if tail:
        blocks.append({'key': 'tail', 'height': 4 + LINE[7] * _lines(tail, 7, TEXT_MM)})
    return blocks


def _detail_blocks(item):
    """월간 품목 상세: 추이 그래프 · 관심 국가 추이(범례가 국가 수만큼 늘어남) · 주요 수출국 표(10개국 + 기타 + 전체)"""
    countries = item['country_trend']
    # 끝 값 라벨이 들어가지 않으면 범례 이름 뒤에 끝 값이 붙어 범례가 넓어집니다
    suffix = '' if end_labels_fit(len(countries), 130) else ' 0,000.0'
    country = H3_MM + (_legend([s['name'] + suffix for s in countries]) + _svg(130) if countries else 12)
    top = len(item['top_countries'][:10]) + (1 if item['others'] else 0) + 1
    # 그래프 높이(150·130)는 monthly.html과 같아야 합니다. 품목명이 두 줄이어도 국가 3개면 한 쪽에 들어가는 크기
    return [{'key': 'trend', 'height': _legend(['', '']) + _svg(150)},
            {'key': 'country', 'height': country},
            _table('top', [8.5] * top)]


def layout(report_type, ctx):
    """템플릿이 쓰는 쪽 나눔. {구역: 쪽 목록}"""
    rest = BODY_MM - _heading()
    if report_type in ('RPT_MONTHLY', 'RPT_QUARTERLY'):
        summary = flow(_summary_blocks(ctx), BODY_MM - _heading('요약 · 비교 기준 · 단위 · 문장 속 수치 검증'), rest)
        tail = ctx.get('one_off_note') if report_type == 'RPT_QUARTERLY' else None
        articles = flow(_article_blocks(ctx['articles'], tail), BODY_MM - _heading('현지 이슈 · 기준') - 4.5, rest)
        pages = {'summary': summary, 'articles': articles}
        two_lines = BODY_MM - _heading('가' * 200)  # 상세 소제목은 품목명이 길면 두 줄
        if report_type == 'RPT_MONTHLY':
            pages['details'] = [flow(_detail_blocks(item), two_lines, rest) for item in ctx['items']]
        else:
            # 관심 국가 추이 쪽: 국가가 많아 범례가 길면 한 쪽에 품목 1개
            names = [c['name'] for c in ctx['conditions']['countries']]
            suffix = '' if end_labels_fit(len(names), 250) else ' 0,000.0'  # 끝 값이 범례로 옮겨 가는 경우
            chart = H3_MM + _legend([n + suffix for n in names]) + _svg(250)
            pages['country_per_page'] = 2 if 2 * chart + 6 <= two_lines else 1
        return pages
    if report_type == 'RPT_WEEKLY':
        s = ctx['summary']
        issues = [_row([(r['title'], 77), (r['basis'], 38), (r['related'], 20)]) for r in s['issues']]
        blocks = [{'key': 'kpis', 'height': 29}, _sentences(s['sentences'], title=H3_MM), _table('issues', issues)]
        if s['schedule']:
            blocks.append(_table('schedule', [_row([(r['content'], 118), (r['related'], 32)]) for r in s['schedule']]))
        blocks.append({'key': 'note', 'height': 3 + 2 * LINE[7]})
        summary = flow(blocks, BODY_MM - _heading('요약') - 4, rest)
        # 국가별 기사 수 표·그래프는 국가 행 단위로 나눕니다. 마지막 행(인덱스 = 국가 수)은 합계 행입니다.
        # 표 행(8.6mm~, 국가명이 길면 국가 칸 약 30mm에서 줄바꿈)이 그래프 막대(약 6.9mm)보다 높아 표 기준으로 계산합니다.
        grid = _table('grid', [_row([(r['name'] + (' 관심' if r.get('is_interest') else ''), 30)]) for r in ctx['country_counts']]
                      + [8.6])
        articles = flow([grid] + _article_blocks(ctx['articles']),
                        BODY_MM - _heading('기사 · 기준') - 4.5, rest)
        spikes = ctx['keyword_spikes']
        shorts = ctx['short_news']['items']
        # 급증 키워드 그래프는 키워드 행 단위로 나눕니다 (범례 + 행마다 30 단위 높이)
        keywords = flow([_table('spikes', [_svg(30)] * len(spikes), head=_legend(['', '']) + _svg(4), empty=15),
                         _table('shorts', [_row([(r['content'], 125)]) for r in shorts]),
                         {'key': 'sources', 'height': H3_MM + 9 + 3 * 8.6 + LINE[7.8] + 3 + 2 * LINE[7]}],
                        BODY_MM - _heading('급증 키워드 · 기사 분포 · 기준 설명 문구'), rest)
        return {'summary': summary, 'articles': articles, 'keywords': keywords}
    return {}


# ---------- 렌더 ----------

def environment():
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=select_autoescape(['html']),
                      undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True)
    env.filters.update(musd=musd, num=num, pct=pct, signed_pct=signed_pct, signed=signed, change=change,
                       emph=emph, ymd=ymd, ymdhm=ymdhm, circled=circled)
    env.globals.update(line_chart=line_chart, bar_chart=bar_chart, hbar_chart=hbar_chart,
                       end_labels_fit=end_labels_fit, last_value=last_value,
                       series_colors=SERIES_COLORS,
                       colors={'accent': ACCENT, 'up': UP, 'down': DOWN, 'muted': MUTED, 'previous': PREVIOUS},
                       css=(TEMPLATE_DIR / 'report.css').read_text(encoding='utf-8'))
    return env


def render(report_type, context):
    """report_type: RPT_MONTHLY · RPT_WEEKLY · RPT_QUARTERLY. 빠진 변수는 StrictUndefined로 오류."""
    if report_type not in TEMPLATES:
        raise ValueError(f'unsupported_report_type: {report_type}')
    try:
        pages = layout(report_type, context)
    except KeyError as missing:
        # 쪽 나눔 계산이 템플릿보다 먼저 값을 읽으므로, 빠진 변수 오류를 템플릿과 같은 종류로 맞춥니다.
        raise UndefinedError(f'{missing.args[0]!r} is undefined') from missing
    return environment().get_template(TEMPLATES[report_type]).render({**context, 'layout': pages})


def sample_context(kind):
    return json.loads((SAMPLE_DIR / f'{kind}.json').read_text(encoding='utf-8'))


def main():
    parser = argparse.ArgumentParser(description='예시 데이터로 정기 리포트 HTML을 렌더합니다.')
    parser.add_argument('kind', choices=sorted(KINDS))
    parser.add_argument('--data', type=Path, help='컨텍스트 JSON (기본: templates/reports/samples/<kind>.json)')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    context = json.loads(args.data.read_text(encoding='utf-8')) if args.data else sample_context(args.kind)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(KINDS[args.kind], context), encoding='utf-8')
    print(args.out)


if __name__ == '__main__':
    main()
