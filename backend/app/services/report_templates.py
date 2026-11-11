"""리포트(정기 월간·주간·분기, 요청형) HTML 템플릿 렌더링.

템플릿은 backend/app/templates/reports/에 있고, 수치·문장·기사·목록은 모두 컨텍스트 변수로 받습니다.
금액은 결과표의 USD 원값을 그대로 받고 표시 형식(정기는 백만 달러, 요청형은 억 달러·증감률·▲▼)만 여기서 바꿉니다.
그래프는 JavaScript 없이 SVG로 그려 HTML → PDF 변환에서도 그대로 나옵니다.

미리보기: python -m backend.app.services.report_templates monthly --out tmp/report_monthly.html
"""
import argparse
import json
import math
import re
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, UndefinedError, select_autoescape
from markupsafe import Markup, escape

TEMPLATE_DIR = Path(__file__).resolve().parents[1] / 'templates' / 'reports'
SAMPLE_DIR = TEMPLATE_DIR / 'samples'
TEMPLATES = {'RPT_MONTHLY': 'monthly.html', 'RPT_WEEKLY': 'weekly.html', 'RPT_QUARTERLY': 'quarterly.html',
             'RPT_ON_DEMAND': 'ondemand.html'}
KINDS = {'monthly': 'RPT_MONTHLY', 'weekly': 'RPT_WEEKLY', 'quarterly': 'RPT_QUARTERLY', 'ondemand': 'RPT_ON_DEMAND'}

ACCENT = '#006849'
UP, DOWN, MUTED, GRID, PREVIOUS = '#0B7A4B', '#C0392B', '#8A8F98', '#E6E3DD', '#A8A39B'
# 관심 국가 선 색상. 같은 리포트 안의 모든 그래프에서 국가 순서대로 같은 색을 씁니다.
SERIES_COLORS = ['#E07B24', '#2E6DB4', '#8E5BB5', '#16877C', '#C2477A', '#6B7A2A']
MINUS = '−'
# 리포트의 금액·비율 표시는 소수 2자리로 맞춘다(2026-11-06 사용자 결정). 개수(국가 수·건수)는 정수다
REPORT_DIGITS = 2


# ---------- 표시 형식 ----------

def _number(value, digits):
    return f'{abs(value):,.{digits}f}'


def musd(value, digits=REPORT_DIGITS):
    """USD → 백만 달러 숫자 문자열 (단위 글자는 템플릿에서 붙임)."""
    if value is None:
        return '—'
    text = _number(value / 1e6, digits)
    return (MINUS + text) if value < 0 else text


def _round_eok(value, digits):
    """억 달러 값 → (소수 자릿수, 반올림한 절댓값 Decimal). 웹 adapters.reportEok와 같은 규칙이다.
    - 값의 최단 십진 표기(repr, JS String과 같음)를 기준으로 반올림하고 0.5는 0에서 먼 쪽으로 올린다
      (부동소수점 내부값 0.000149999…를 쓰면 웹과 PDF가 0.0002·0.0001로 갈린다).
    - 0.1억 미만은 유효숫자 1자리(0.03·0.0005)까지 자릿수를 늘려 0.00으로 뭉개지지 않게 한다."""
    exact = Decimal(repr(abs(float(value))))
    places = max(digits, -exact.adjusted()) if value and abs(value) < 0.1 else digits
    return places, exact.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def _number_eok(value, digits):
    places, rounded = _round_eok(value, digits)
    text = f'{rounded:,.{places}f}'
    return text if places == digits else text.rstrip('0').rstrip('.')


def eok(value, digits=REPORT_DIGITS):
    """USD 실적 금액 → 억 달러 숫자 문자열 (요청형 리포트). 단위 글자는 템플릿에서 붙임.
    실적 0은 그 기간에 실적이 없다는 뜻이라 —로 쓴다(웹 화면과 같은 규칙). 증감액처럼 실제 0이 의미 있는 값은 signed_eok."""
    if value is None or value == 0:
        return '—'
    text = _number_eok(value / 1e8, digits)
    return (MINUS + text) if value < 0 else text


def signed_eok(value, digits=REPORT_DIGITS):
    """증감액(USD) → 부호 붙은 억 달러 (+12.30 · −2.40 · +0.03)."""
    if value is None:
        return '—'
    v = value / 1e8
    return ('+' if v > 0 else MINUS if v < 0 else '') + _number_eok(v, digits)


def rank_move(value):
    """순위 변동: 양수 = 올라감(▲), 음수 = 내려감(▼), 0 = –, 'NEW' = 신규, None = 비교 없음(—)."""
    if value is None:
        return Markup('<span class="chg none">—</span>')
    if value == 'NEW':
        return Markup('<span class="chg new">신규</span>')
    if value == 0:
        return Markup('<span class="chg flat">–</span>')
    cls, mark = ('up', '▲') if value > 0 else ('down', '▼')
    return Markup(f'<span class="chg {cls}">{mark}{abs(int(value))}</span>')


def num(value, digits=0):
    if value is None:
        return '—'
    text = _number(value, digits)
    return (MINUS + text) if value < 0 else text


def pct(value, digits=REPORT_DIGITS):
    return '—' if value is None else num(value, digits) + '%'


def signed_pct(value, digits=REPORT_DIGITS):
    if value is None:
        return '—'
    return ('+' if value > 0 else MINUS if value < 0 else '') + _number(value, digits) + '%'


def signed(value, digits=0):
    if value is None:
        return '—'
    return ('+' if value > 0 else MINUS if value < 0 else '') + _number(value, digits)


def change(value, digits=REPORT_DIGITS, new=False):
    """증감률을 ▲(증가)/▼(감소) 색 표시로. 비교 값이 0이라 계산할 수 없으면 new=True → '신규'."""
    if new:
        return Markup('<span class="chg new">신규</span>')
    if value is None:
        return Markup('<span class="chg none">—</span>')
    if value == 0:
        return Markup(f'<span class="chg flat">– {_number(0, digits)}%</span>')
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


def _tick_digits(ticks):
    """눈금 간격을 그대로 보일 소수 자릿수 (최소 1). 0.25 간격은 2자리, 억 달러 소규모 시장의 0.005 간격은 3자리."""
    step = abs(ticks[1] - ticks[0]) if len(ticks) > 1 else 1
    if step <= 0:
        return 1
    digits = max(1, -math.floor(math.log10(step)))  # 간격의 첫 유효 숫자 자리부터
    while digits < 12 and abs(step * 10 ** digits - round(step * 10 ** digits)) > 1e-6:
        digits += 1
    return digits


def _tick_label(value, digits=1):
    # 정수 판정도 표시 자릿수 기준으로 (아주 작은 눈금 5e-10을 0으로 보지 않게)
    return f'{value:,.0f}' if abs(value - round(value)) < 10 ** -(digits + 3) else f'{value:,.{digits}f}'


def _axis_room(ticks, minimum):
    """세로축 숫자(.axis 9.5)가 왼쪽 밖으로 나가지 않게 가장 긴 눈금 폭 + 8만큼 비웁니다 (기본 폭보다 좁히지 않음)."""
    digits = _tick_digits(ticks)
    return max(minimum, max(_em(_tick_label(t, digits)) for t in ticks) * 9.5 + 8)


def line_chart(labels, series, *, height=190, width=640, divisor=1e6, digits=REPORT_DIGITS, zero=False):
    """series: [{values(USD 또는 원값, divisor로 나눠 표시), color?, dashed?, area?}]. 선 끝에 마지막 값 라벨."""
    top, bottom = 10, 24
    scaled = [[None if v is None else v / divisor for v in s['values']] for s in series]
    low, high, ticks = nice_scale([v for vals in scaled for v in vals], zero=zero)
    left = _axis_room(ticks, 44)
    # 끝 값 라벨(.end 10.5, 굵게)이 오른쪽 밖으로 나가지 않게 가장 긴 값 폭만큼 비움 (기본 64)
    label = (lambda v: (MINUS if v < 0 else '') + _number_eok(v, digits)) if divisor == 1e8 else (lambda v: num(v, digits))
    right = max([64] + [_em(label(v)) * 11 + 12 for vals in scaled for v in vals if v is not None])
    plot_w, plot_h = width - left - right, height - top - bottom
    step = plot_w / max(len(labels) - 1, 1)
    x = lambda i: left + i * step
    y = lambda v: top + plot_h - (v - low) / (high - low) * plot_h
    out = [f'<svg class="chart" viewBox="0 0 {width} {height}" width="100%" role="img">']
    for t in ticks:
        out.append(f'<line x1="{left}" x2="{width - right}" y1="{y(t):.1f}" y2="{y(t):.1f}" stroke="{GRID}" stroke-width="1"/>')
        out.append(f'<text x="{left - 6}" y="{y(t) + 3:.1f}" text-anchor="end" class="axis">{_tick_label(t, _tick_digits(ticks))}</text>')
    for i in _visible_labels(labels, x):
        out.append(f'<text x="{x(i):.1f}" y="{height - 6}" text-anchor="middle" class="axis">{escape(labels[i])}</text>')
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
    _spread([e for e in sorted(ends, key=lambda e: e[1])], top, top + plot_h)
    for px, py, value, color in ends:
        out.append(f'<circle cx="{px:.1f}" cy="{y(value):.1f}" r="3" fill="{color}"/>')
        if show_labels:
            out.append(f'<text x="{px + 7:.1f}" y="{py + 3:.1f}" class="end" fill="{color}">{label(value)}</text>')
    out.append('</svg>')
    return Markup(''.join(out))


END_GAP = 12  # 끝 값 라벨 사이 최소 간격 (SVG 단위, 라벨 글자 10.5)


def _visible_labels(labels, x, font=9.5, gap=4):
    """가로축 라벨이 겹치지 않게 그릴 위치만 고릅니다. 연도가 붙은 라벨('24.1')·처음·끝을 먼저 두고,
    나머지는 앞뒤 라벨과 겹치지 않을 때만 둡니다 (달이 많은 요청형 기간에서 '12'와 '24.1'이 붙는 문제)."""
    n = len(labels)
    span = lambda i: (x(i) - _em(labels[i]) * font / 2, x(i) + _em(labels[i]) * font / 2)
    priority = sorted(range(n), key=lambda i: (0 if i in (0, n - 1) or '.' in str(labels[i]) else 1, i))
    chosen = []
    for i in priority:
        left, right = span(i)
        if all(right + gap <= span(j)[0] or span(j)[1] + gap <= left for j in chosen):
            chosen.append(i)
    return sorted(chosen)


def _spread(ends, low, high):
    """y 순으로 정렬된 라벨 [x, y, ...]를 간격 END_GAP 이상, low~high 안에 두도록 y를 고칩니다.
    아래로 밀며 겹침을 풀고(low 이상), 위로 당기며 high를 넘지 않게 합니다.
    (len-1)·END_GAP ≤ high−low(end_labels_fit)이면 두 경계와 간격을 모두 만족합니다."""
    for i, e in enumerate(ends):
        e[1] = max(e[1], low, ends[i - 1][1] + END_GAP if i else low)
    for i in range(len(ends) - 1, -1, -1):
        e = ends[i]
        e[1] = min(e[1], high, ends[i + 1][1] - END_GAP if i < len(ends) - 1 else high)


def end_labels_fit(count, height):
    """끝 값 라벨 count개가 높이 height인 line_chart의 그래프 영역 안에 겹치지 않고 들어가는지."""
    return count <= 1 or (count - 1) * END_GAP <= height - 34


def has_values(series_values):
    """값 목록들 중 null이 아닌 값이 하나라도 있는지 (없으면 템플릿이 “데이터 준비 중” 표시)"""
    return any(v is not None for values in series_values for v in values)


def full_period_label(labels, i):
    """축 라벨은 연도가 바뀔 때만 '24.1Q'·'25.1'처럼 연도를 붙이므로('2Q'·'11'은 연도 생략),
    앞쪽에서 가장 가까운 연도를 찾아 붙인 기간 이름을 돌려줍니다. 예: ['24.1Q','2Q','3Q','4Q'], 3 → '24.4Q'"""
    label = str(labels[i])
    if '.' in label:
        return label
    year = next((str(labels[j]).split('.')[0] for j in range(i - 1, -1, -1) if '.' in str(labels[j])), None)
    return f'{year}.{label}' if year else label


def legend_value(values, labels):
    """범례에 붙일 끝 값 문자열. 마지막 기간이 null이면 값이 있는 마지막 기간을 연도까지 괄호로 표시합니다.
    마지막 기간 여부는 라벨 글자가 아니라 위치로 판단합니다('4Q'가 연도마다 반복되므로).
    예: '171.2' · '10.0(25.11)' · '10.0(24.4Q)' · 값이 하나도 없으면 '—'"""
    point = next(((i, v) for i, v in reversed(list(enumerate(values))) if v is not None), None)
    if point is None:
        return '—'
    i, value = point
    return musd(value) + ('' if i == len(values) - 1 else f'({full_period_label(labels, i)})')


def bar_chart(labels, values, colors, *, height=200, width=330, divisor=1e6, digits=REPORT_DIGITS, show=(), ticks=5):
    """세로 막대. show: 값 라벨을 표시할 막대 위치 목록. ticks: 눈금 개수 기준 (nice_scale)."""
    right, top, bottom = 6, 14, 22
    scaled = [None if v is None else v / divisor for v in values]
    low, high, ticks = nice_scale(scaled, ticks=ticks, zero=True)
    left = _axis_room(ticks, 40)
    plot_w, plot_h = width - left - right, height - top - bottom
    slot = plot_w / max(len(labels), 1)
    bar = slot * 0.62
    y = lambda v: top + plot_h - (v - low) / (high - low) * plot_h
    out = [f'<svg class="chart" viewBox="0 0 {width} {height}" width="100%" role="img">']
    for t in ticks:
        out.append(f'<line x1="{left}" x2="{width - right}" y1="{y(t):.1f}" y2="{y(t):.1f}" stroke="{GRID}"/>')
        out.append(f'<text x="{left - 5}" y="{y(t) + 3:.1f}" text-anchor="end" class="axis">{_tick_label(t, _tick_digits(ticks))}</text>')
    for i, (label, v, color) in enumerate(zip(labels, scaled, colors)):
        cx = left + slot * i + slot / 2
        if v is not None:
            out.append(f'<rect x="{cx - bar / 2:.1f}" y="{y(v):.1f}" width="{bar:.1f}" height="{y(low) - y(v):.1f}" fill="{color}"/>')
            if i in show:
                out.append(f'<text x="{cx:.1f}" y="{y(v) - 4:.1f}" text-anchor="middle" class="val">{num(v, digits)}</text>')
        out.append(f'<text x="{cx:.1f}" y="{height - 6}" text-anchor="middle" class="axis">{escape(label)}</text>')
    out.append('</svg>')
    return Markup(''.join(out))


def hbar_chart(rows, colors, *, label_width=120, width=640, row_height=26, digits=REPORT_DIGITS, divisor=1, tail_width=0, peak=None):
    """가로 막대. rows: [{label, values:[현재, 비교...], tail?:{text, badge?}}]. digits는 막대별 목록도 가능합니다.
    막대 길이는 peak(없으면 rows 최대값) 기준 — 여러 쪽에 나눠 그릴 때 같은 peak를 주면 막대 길이를 비교할 수 있습니다."""
    peak_value = peak if peak is not None else max((v for r in rows for v in r['values'] if v is not None), default=0)
    peak = peak_value / divisor or 1
    bars = len(colors)
    bar_h = 8 if bars > 1 else 12
    places_of = lambda j: digits[j] if isinstance(digits, (list, tuple)) else digits
    # 막대 끝 숫자가 오른쪽 밖으로 나가지 않게 가장 긴 숫자 폭만큼 비웁니다 (peak 기준이라 쪽이 나뉘어도 막대 비율 동일)
    fmt = (lambda v, d: (MINUS if v < 0 else '') + _number_eok(v, d)) if divisor == 1e8 else num   # 억 단위는 웹과 같은 작은 값 규칙
    texts = [fmt(peak_value / divisor, places_of(0))] + [fmt(v / divisor, places_of(j)) for r in rows
                                                  for j, v in enumerate(r['values']) if v is not None]
    plot_w = width - label_width - tail_width - max(40, max(_em(t) for t in texts) * 11 + 10)
    height = row_height * len(rows) + 4
    out = [f'<svg class="chart" viewBox="0 0 {width} {height}" width="100%" role="img">']
    for i, r in enumerate(rows):
        row_top = i * row_height + (row_height - bars * (bar_h + 2)) / 2
        label = _fit(r['label'], label_width - 6, 11)  # 라벨(11)이 막대 위로 겹치지 않게 칸 폭에 맞춰 줄임
        out.append(f'<text x="0" y="{i * row_height + row_height / 2 + 4:.1f}" class="lbl">{escape(label)}</text>')
        for j, (v, color) in enumerate(zip(r['values'], colors)):
            if v is None:
                continue
            w = max(v / divisor / peak * plot_w, 1)
            yy = row_top + j * (bar_h + 2)
            out.append(f'<rect x="{label_width}" y="{yy:.1f}" width="{w:.1f}" height="{bar_h}" rx="2" fill="{color}"/>')
            cls = 'val' if j == 0 else 'axis'
            out.append(f'<text x="{label_width + w + 5:.1f}" y="{yy + bar_h - 1:.1f}" class="{cls}">{fmt(v / divisor, places_of(j))}</text>')
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


def diverging_chart(rows, *, width=640, row_height=26, label_width=80, divisor=1e8, digits=REPORT_DIGITS):
    """증감액 막대(양식 '국가별 증감액'). rows: [{label, value}]. 가운데 축에서 증가는 오른쪽(UP), 감소는 왼쪽(DOWN)으로 그린다.
    국가명은 증가 막대의 왼쪽, 감소 막대의 오른쪽에 둔다."""
    fmt = lambda v: ('+' if v > 0 else MINUS if v < 0 else '') + _number_eok(abs(v), digits)
    peak = max((abs(r['value']) for r in rows), default=0) or 1
    texts = [fmt(r['value'] / divisor) for r in rows]
    pad = max([40] + [_em(t) * 11 + 10 for t in texts])   # 막대 끝 숫자가 밖으로 나가지 않게
    half = (width - 2 * (label_width + pad)) / 2
    axis = width / 2
    height = row_height * len(rows) + 4
    out = [f'<svg class="chart" viewBox="0 0 {width} {height}" width="100%" role="img">',
           f'<line x1="{axis}" x2="{axis}" y1="0" y2="{height}" stroke="#2B2E33" stroke-width="1"/>']
    for i, (r, text) in enumerate(zip(rows, texts)):
        v = r['value']; w = max(abs(v) / peak * half, 1); y = i * row_height + (row_height - 12) / 2; ty = y + 10
        label = escape(_fit(r['label'], label_width - 6, 11))
        if v >= 0:
            out.append(f'<rect x="{axis}" y="{y:.1f}" width="{w:.1f}" height="12" rx="2" fill="{UP}"/>'
                       f'<text x="{axis + w + 5:.1f}" y="{ty:.1f}" class="val">{text}</text>'
                       f'<text x="{axis - 6:.1f}" y="{ty:.1f}" text-anchor="end" class="lbl">{label}</text>')
        else:
            out.append(f'<rect x="{axis - w:.1f}" y="{y:.1f}" width="{w:.1f}" height="12" rx="2" fill="{DOWN}"/>'
                       f'<text x="{axis - w - 5:.1f}" y="{ty:.1f}" text-anchor="end" class="val">{text}</text>'
                       f'<text x="{axis + 6:.1f}" y="{ty:.1f}" class="lbl">{label}</text>')
    out.append('</svg>')
    return Markup(''.join(out))


# ---------- 쪽 나누기 ----------
# 쪽 높이가 고정이라 넘치는 내용은 잘립니다. 블록 높이를 mm로 추정해 넘치면 다음 쪽(“(계속)”)으로 보냅니다.
# 한글 1자 = 1em(Noto Sans KR 기준)으로 계산하며, 폭이 더 좁은 글꼴(맑은 고딕)에서는 아래 여백이 조금 남습니다.

# 아래 값은 report.css(양식 PDF 기준 글자 크기·여백)로 렌더해 잰 높이입니다. CSS를 바꾸면 함께 맞춰야 합니다.
BODY_MM = 250   # A4 297mm − 위 25mm − 아래 18mm − 여유 4mm
TEXT_MM = 180   # 210mm − 좌우 15mm
PT_MM = 0.3528
H3_MM = 13.2    # 소제목 10.5pt (위 여백 포함)
TH_MM = 8.4     # 표 머리 행 (8.2pt)
ROW_MM = 3.9    # 표 한 행의 위아래 여백 (줄 높이는 별도)
ROW1_MM = 8.8   # 한 줄짜리 표 행 (9pt)
KPI_MM = 31     # KPI 카드 줄 (아래 여백 4mm 포함)
POLICY_MM = 4.6  # 기사 노출 기준 줄
LINE = {7.8: 4.27, 7.9: 4.32, 8.2: 4.48, 8.6: 4.7, 9: 4.92, 9.8: 5.36, 10.5: 5.74}  # 글자 크기(pt) → 줄 높이(mm), line-height 1.55


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


def _row(cells, size=9):
    """표 한 행 높이. cells: [(문자열, 칸 너비 mm)]"""
    return ROW_MM + LINE[size] * max([1] + [_lines(text, size, width) for text, width in cells])


def _heading(sub=None):
    return 9.6 + (LINE[8.6] * min(_lines(sub, 8.6, TEXT_MM), 2) + 4 if sub else 0)  # 소제목은 CSS로 최대 두 줄


def _sentences(sentences, title=0):
    """요약 문장 상자(9.8pt). 문장 단위로 나뉘며 다음 쪽에서 번호가 이어집니다. title: 위 소제목 높이"""
    return _table('sentences', [LINE[9.8] * _lines(s.replace('**', ''), 9.8, TEXT_MM - 15) + 1.2 for s in sentences],
                  head=title + 11, empty=0)


def _legend_lines(names, width=TEXT_MM):
    """범례(8.2pt) 줄 수. 항목 = 이름 + 표식(약 4.5mm) + 간격 4mm"""
    lines, used = 1, 0.0
    for name in names:
        w = _em(name) * 8.2 * PT_MM + 8.5
        if used and used + w > width:
            lines, used = lines + 1, 0.0
        used += w
    return lines


def _legend(names):
    # 위아래 여백 1mm씩 + 8.2pt 줄. 범례는 flex gap(4mm)이 줄 사이에도 붙습니다 (실측 1줄 6.5mm)
    lines = _legend_lines(names)
    return 2 + LINE[8.2] * lines + 4 * (lines - 1)


def _svg(height, width=640):
    """본문 폭에 맞춰 늘어나는 SVG 높이(mm)"""
    return height * TEXT_MM / width


def _article(a):
    meta = ' · '.join(str(a.get(k)) for k in ('country', 'item_label', 'published_date', 'source_label', 'office') if a.get(k))
    # 제목 10.5pt · 정보 줄 7.9pt · 발췌 9pt · 링크 7.9pt, 카드 안 줄 간격 1.4 (실측 400자 발췌 45.9mm, 짧은 발췌 23.7mm)
    line = lambda size: size * 1.4 * PT_MM
    return (4.7 + line(10.5) * _lines(a.get('title'), 10.5, TEXT_MM - 6) + line(7.9) * _lines(meta, 7.9, TEXT_MM) + 1
            + line(9) * _lines(a.get('summary'), 9, TEXT_MM) + line(7.9) + 0.6)


def _table(key, rows, head=None, empty=ROW1_MM, **extra):
    """나눌 수 있는 표 블록. 행이 없으면 안내 행 높이만 차지합니다."""
    return dict(extra, key=key, head=H3_MM + TH_MM if head is None else head, rows=rows, empty=empty)


# 유망시장 표 칸 너비(mm). ondemand.html의 th 너비와 같아야 한다. 글자 폭 = 칸 너비 − 좌우 여백 4.8mm.
PROMISING_COLS = (24, 30, 50, 20, 24, 32)
COMPACT_ROW_MM = 7.6   # table.compact 한 줄 행(7.9pt, 실측)
CHANGE_NAME_MM = 20    # 유망시장 증감 표(두 칸 중 한 칸, 5열)의 국가명 글자 폭 추정(mm). 실측보다 좁게 잡아 줄 수를 넉넉히 센다
                       # (긴 국가명 5행: 맑은 고딕 실측 75.9mm, 이 값의 추정 91.8mm — 글자가 넓은 Linux 글꼴 여유)
DIVERGING_ROW = 22     # 유망시장 증감액 막대 한 줄 높이(SVG 단위). ondemand.html과 같아야 한다
BOX_GAP_MIN, BOX_GAP_MAX = 6, 20   # 증감액 그래프와 ML 박스 사이 여백(mm). 남는 공간에 따라 이 범위에서 정한다


def change_tables_height(changes):
    """유망시장 성장률·감소율 상위 표(두 칸 나란히) 블록 높이(mm). 국가명이 좁은 칸에서 여러 줄로 바뀌면 행이 높아지므로
    두 표 각각의 행 높이 합을 재고 더 높은 쪽을 쓴다."""
    def rows(items):
        return sum(COMPACT_ROW_MM + LINE[7.9] * (_lines(i.get('country') or '', 7.9, CHANGE_NAME_MM) - 1)
                   for i in items) or COMPACT_ROW_MM
    return H3_MM + TH_MM + max(rows(changes['growth']), rows(changes['decline'])) + 2


def _promising_table(promising):
    """유망시장 표. 국가·품목 이름이 길면 줄이 바뀌므로 행마다 높이를 잰다(머리 행도 줄바꿈 반영).
    국가명은 굵은 글씨라 폭을 6% 좁게 본다."""
    w = [c - 4.8 for c in PROMISING_COLS]
    heads = [promising.get('rank_label') or '', '국가', '품목', 'HS 코드', '증감 예상', '최근 12개월 수출액']
    head = TH_MM + LINE[8.2] * (max(_lines(t, 8.2, x) for t, x in zip(heads, w)) - 1)
    def rank(r):
        return f"{r.get('rank') if r.get('rank') is not None else '—'}" + (f" / {num(r['size'])}" if r.get('size') else '')
    rows = [_row([(rank(r), w[0]), (r.get('country') or '', w[1] * 0.94), (r.get('product') or '', w[2]),
                  (r.get('hs6') or '', w[3]), (r.get('outlook') or '—', w[4]), (eok(r.get('exp_12m_usd'), 2), w[5])])
            for r in promising['rows']]
    return _table('ptable', rows, head=head)


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


def _contents_chunks(value):
    """Keep all text, splitting a very long information row into bounded continuations."""
    text = str(value)
    chunks = []
    limit = 6 * 140 / (9 * PT_MM)
    while _em(text) > limit:
        width, stop, boundary = 0, 0, 0
        for i, char in enumerate(text):
            width += _em(char)
            if width > limit:
                break
            stop = i + 1
            if char.isspace():
                boundary = stop
        stop = boundary if boundary > stop // 2 else stop
        chunks.append(text[:stop])
        text = text[stop:]
    chunks.append(text)
    return chunks


def contents_layout(toc, info, notes, report):
    """Paginate the actual template values, including metadata too long for one row.

    Match the existing TOC/table typography; reserve space for each repeated heading.
    The render pass below uses this page count to shift every body page and TOC target.
    """
    rows = []
    for row in info:
        for i, chunk in enumerate(_contents_chunks(row['value'])):
            rows.append({'label': row['label'] + (' (계속)' if i else ''), 'value': chunk})
    note_parts = [chunk for note in notes for chunk in _contents_chunks(note)]
    blocks = [
        _table('toc', [6.3 + 11 * 1.55 * PT_MM * max(1, _lines(r['title'], 11, 150))
                       for r in toc], head=13),
        _table('info', [_row([(r['label'], 20), (r['value'], 140)]) for r in rows], head=H3_MM),
        _table('notes', [0.8 + LINE[7.8] * max(1, _lines(n, 7.8, 175))
                         for n in note_parts], head=2),
    ]
    room = BODY_MM - _heading(report['period_label'] + ' · ' + report['title'])
    return {'pages': flow(blocks, room, room), 'info': rows, 'notes': note_parts}


def _summary_blocks(ctx):
    """월간·분기 요약: KPI · 요약 문장 · 품목 표 · 관심 국가 표(품목 3개씩) · 확인할 점"""
    items = ctx['items']
    blocks = [{'key': 'kpis', 'height': KPI_MM}]
    if ctx['summary']['sentences']:  # 요약 방식이 정해지지 않으면(null·빈 목록) 상자째 숨김
        blocks.append(_sentences(ctx['summary']['sentences']))
    # 품목 행 = 품목명(9pt, 칸 약 26mm) + HS 코드 줄(7.9pt) (실측 13.7mm)
    blocks.append(_table('items', [ROW_MM + LINE[9] * _lines(i['label'], 9, 26) + 4.9 for i in items]))
    groups = [list(range(n, min(n + 3, len(items)))) for n in range(0, len(items), 3)] or [[]]
    header = max([1] + [_lines(items[i]['label'], 8.2, 20) for g in groups for i in g])
    for g in groups:
        blocks.append(_table('overview', [ROW1_MM] * len(ctx['interest_overview']), head=H3_MM + ROW_MM + LINE[8.2] * header,
                             group=g))  # 행이 없으면 “데이터 준비 중” 안내 행
    checks = ctx['summary']['check_points']
    if checks:
        blocks.append(_table('checks', [_row([(r['target'], 28), (r['content'], 145)]) for r in checks]))
    return blocks


def _article_blocks(articles, tail=None):
    blocks = [_table('articles', [_article(a) for a in articles], head=0, empty=15)]
    if tail:
        blocks.append({'key': 'tail', 'height': 4 + LINE[7.9] * _lines(tail, 7.9, TEXT_MM)})
    return blocks


def _legend_names(series, labels, height):
    """템플릿 범례에 실제로 들어갈 글자. 끝 값 라벨이 그래프에 안 들어가면 이름 뒤에 끝 값(기간)이 붙습니다."""
    if end_labels_fit(len(series), height):
        return [s['name'] for s in series]
    return [s['name'] + ' ' + legend_value(s['values'], labels) for s in series]


def _detail_blocks(item):
    """월간 품목 상세: 추이 그래프 · 관심 국가 추이(범례가 국가 수만큼 늘어남) · 주요 수출국 표(10개국 + 기타 + 전체)"""
    countries = item['country_trend']
    country = H3_MM + (_legend(_legend_names(countries, item['trend']['labels'], 115)) + _svg(115) if countries else 12)
    # 상위 국가(없으면 “데이터 준비 중” 1행) + 기타 + 전체
    top = (len(item['top_countries'][:10]) or 1) + (1 if item['others'] else 0) + 1
    # 그래프 높이(140·115)는 monthly.html과 같아야 합니다 (양식의 그래프 높이 약 40·32mm)
    return [{'key': 'trend', 'height': _legend(['', '']) + _svg(140)},
            {'key': 'country', 'height': country},
            _table('top', [ROW1_MM] * top)]


def _detail_heading(item):
    """월간 상세 소제목 높이 — 템플릿 문구와 같은 길이로 추정 (품목명은 40자로 줄임)"""
    name = str(item['name'])
    name = name if len(name) <= 40 else name[:39] + '…'
    return _heading(f"HS {item['hs_code']} {name} · 12월 수출 {musd(item['amount_usd'])}백만 달러 · 전월 +00.00% · 전년 동월 +00.00%")


def layout(report_type, ctx):
    """템플릿이 쓰는 쪽 나눔. {구역: 쪽 목록}"""
    rest = BODY_MM - _heading()
    if report_type in ('RPT_MONTHLY', 'RPT_QUARTERLY'):
        summary = flow(_summary_blocks(ctx), BODY_MM - _heading('요약 · 비교 기준 · 단위 · 문장 속 수치 검증'), rest)
        tail = ctx.get('one_off_note') if report_type == 'RPT_QUARTERLY' else None
        articles = flow(_article_blocks(ctx['articles'], tail), BODY_MM - _heading('현지 이슈 · 기준') - POLICY_MM, rest)
        pages = {'summary': summary, 'articles': articles}
        if report_type == 'RPT_MONTHLY':
            pages['details'] = [flow(_detail_blocks(item), BODY_MM - _detail_heading(item), rest) for item in ctx['items']]
        else:
            # 관심 국가 추이 쪽: 국가가 많아 범례가 길면 한 쪽에 품목 1개 (8개 분기·3개월 그래프 중 가장 긴 범례 기준)
            names = [c['name'] for c in ctx['conditions']['countries']]
            legends = [names]
            for item in ctx['items']:
                legends.append(_legend_names(item['country_quarters'], item['quarters']['labels'], 235))
                legends.append(_legend_names(item['country_months']['series'], item['country_months']['labels'], 235))
            chart = H3_MM + max(_legend(n) for n in legends) + _svg(235)  # 높이 235는 quarterly.html과 같아야 함
            sub = ' · '.join(names) + ' | 국가별 선 색상은 모든 그래프에서 같습니다'
            pages['country_per_page'] = 2 if 2 * chart + 6 <= BODY_MM - _heading(sub) else 1
        return pages
    if report_type == 'RPT_WEEKLY':
        s = ctx['summary']
        issues = [_row([(r['title'], 76), (r['basis'], 35), (r['related'], 17)]) for r in s['issues']]
        blocks = [{'key': 'kpis', 'height': KPI_MM}]
        if s['sentences']:  # 요약 방식이 정해지지 않으면(null·빈 목록) “이번 주 핵심”째 숨김
            blocks.append(_sentences(s['sentences'], title=H3_MM))
        blocks.append(_table('issues', issues))
        if s['schedule']:
            blocks.append(_table('schedule', [_row([(r['content'], 118), (r['related'], 32)]) for r in s['schedule']]))
        blocks.append({'key': 'note', 'height': 3 + (4 if s.get('spike_gap_note') else 2) * LINE[7.9]})
        summary = flow(blocks, BODY_MM - _heading('요약') - 4.3, rest)  # 주간 요약 제목 아래 구분선(0.3mm + 4mm)
        # 국가별 기사 수 표·그래프는 국가 행 단위로 나눕니다. 마지막 행(인덱스 = 국가 수)은 합계 행입니다.
        # 표 행(8.8mm~, 국가명이 길면 국가 칸 약 30mm에서 줄바꿈)이 그래프 막대(약 7mm)보다 높아 표 기준으로 계산합니다.
        grid = _table('grid', [_row([(r['name'] + (' 관심' if r.get('is_interest') else ''), 30)]) for r in ctx['country_counts']]
                      + [ROW1_MM])
        # 양식처럼 기사 목록 다음에 국가별 기사 수 표·그래프
        articles = flow(_article_blocks(ctx['articles']) + [grid],
                        BODY_MM - _heading('기사 · 기준') - POLICY_MM, rest)
        spikes = ctx['keyword_spikes']
        shorts = ctx['short_news']['items']
        # 급증 키워드 그래프는 키워드 행 단위로 나눕니다 (범례 + 행마다 30 단위 높이)
        keywords = flow([_table('spikes', [_svg(30)] * len(spikes), head=_legend(['', '']) + _svg(4), empty=15),
                         _table('shorts', [_row([(r['content'], 125)]) for r in shorts]),
                         {'key': 'sources', 'height': H3_MM + TH_MM + 2 * ROW1_MM + ROW_MM + 2 * LINE[9] + 3 + 2 * LINE[7.8]}],
                        BODY_MM - _heading('급증 키워드 · 기사 분포 · 기준 설명 문구'), rest)
        return {'summary': summary, 'articles': articles, 'keywords': keywords}
    if report_type == 'RPT_ON_DEMAND':
        return _ondemand_layout(ctx, rest)
    return {}


# 요청형 섹션 (양식 순서). SUMMARY는 항상, 나머지는 사용자가 고른 것만.
# GROWTH_RISK는 유망시장(증가·감소 예상)이다. 채팅 유망시장과 같은 ML 결과(promising)가 있을 때 그린다.
ONDEMAND_SECTIONS = ('SUMMARY', 'KPI', 'TOP_COUNTRIES', 'GROWTH_RISK', 'LOCAL_ISSUES')
HALF_MM = (TEXT_MM - 6) / 2  # 두 칸 배치(.grid2)의 한 칸 폭


def ondemand_sections(ctx):
    """실제로 그릴 섹션 코드 (목차·쪽 번호·포함 항목에 같은 목록을 씁니다).
    승인한 채팅 결과로 만든 리포트(results)는 핵심 요약과 결과표만 그립니다. 결과표에 없는 집계 섹션은 만들지 않습니다(D-90)."""
    if ctx.get('results') is not None:
        return ['SUMMARY', 'RESULTS']
    chosen = set(ctx['conditions']['sections'])
    return [s for s in ONDEMAND_SECTIONS if s == 'SUMMARY' or (s in chosen and (s != 'GROWTH_RISK' or 'promising' in ctx))]


def _result_blocks(results):
    """결과표마다 (월별 추이면 그래프) + 표. 표는 행 단위로 쪽을 나눕니다. 칸 폭은 본문 폭을 열 수로 나눈 값으로 추정합니다."""
    blocks = []
    for i, table in enumerate(results):
        chart = table.get('chart')
        if chart:  # 소제목 + 범례 + 그래프(높이 230은 ondemand.html과 같아야 함)
            blocks.append({'key': 'rchart', 'table': i,
                           'height': H3_MM + _legend([s['name'] for s in chart['series']]) + _svg(230)})
        width = TEXT_MM / max(len(table['columns']), 1) - 2
        blocks.append(_table('rtable', [_row([(cell, width) for cell in row]) for row in table['rows']],
                             head=H3_MM + TH_MM + LINE[8.2] * max(_lines(c['label'], 8.2, width) for c in table['columns'])
                             if table['columns'] else None, table=i))
    return blocks


def _ondemand_layout(ctx, rest):
    # 핵심 요약 값은 항상 받고, 나머지 구역 데이터(monthly·top_countries 등)는 그 섹션을 골랐을 때만 읽습니다.
    sections = ondemand_sections(ctx)
    s = ctx['summary']
    pages = {'sections': sections}

    if 'RESULTS' in sections:  # 결과 기반: 집계 카드 없이 검토표(결과 출처·누락·미포함 항목)만
        pages['summary'] = flow([_table('checks', [_row([(r['target'], 26), (r['content'], 92), (r['basis'], 44)])
                                                   for r in s['check_points']])],
                                BODY_MM - _heading('승인한 AI 분석 결과표로 작성 · 다시 조회하지 않음'), rest)
        pages['results'] = flow(_result_blocks(ctx['results']), BODY_MM - _heading('대화에서 승인한 결과표 사본 · 금액 단위는 각 열 머리글'), rest)
        return pages

    blocks = [{'key': 'cards', 'height': KPI_MM}]
    if s['sentences']:
        blocks.append(_sentences(s['sentences']))
    if 'KPI' in sections:  # 주요 지표 카드 8개 (4개씩 2줄, 실측 한 줄 25.4mm + 간격 3mm)
        blocks.append({'key': 'metrics', 'height': H3_MM + 2 * 25.4 + 3 + 4})
    if s['check_points']:
        blocks.append(_table('checks', [_row([(r['target'], 26), (r['content'], 92), (r['basis'], 44)])
                                        for r in s['check_points']]))
    pages['summary'] = flow(blocks, BODY_MM - _heading('수치는 결과표에서 직접 넣고 문장 속 수치는 대조해 검증 · 검증 통과 5/5'), rest)

    if 'KPI' in sections:
        monthly = ctx['monthly']
        rows = len(monthly['rows']) + 1  # 합계 행 포함
        chart = _legend(['', '']) + _svg(230)  # 높이 230은 ondemand.html과 같아야 함
        side = ((H3_MM + TH_MM + ROW1_MM * len(monthly['quarters'])) if monthly['quarters'] else 0) \
            + ((H3_MM + sum(9 * 1.75 * PT_MM * _lines(t, 9, HALF_MM) + 1 for t in monthly['notes'])) if monthly['notes'] else 0)
        if rows <= 14:  # 양식처럼 왼쪽 월별 표, 오른쪽 분기별 합계·읽는 법
            blocks = [{'key': 'chart', 'height': chart},
                      {'key': 'mgrid', 'height': 5 + max(TH_MM + ROW1_MM * rows, side)}]
        else:  # 기간이 길면 월별 표를 본문 폭으로 나눠 그리고 분기별 합계·읽는 법은 그 뒤에
            blocks = [{'key': 'chart', 'height': chart}, _table('mtable', [ROW1_MM] * rows)]
            if side:
                blocks.append({'key': 'mside', 'height': side})
        if ctx.get('scope_comparison'):
            scopes=ctx['scope_comparison']
            blocks.extend([{'key':'scope_chart','height':H3_MM+_legend([s['name'] for s in scopes['series']])+_svg(230)+12},
                           _table('scope_table',[ROW1_MM]*len(scopes['rows']))])
        pages['monthly'] = flow(blocks, BODY_MM - _heading('월별 수출액과 비교 · 단위 억 달러'), rest)

    if 'TOP_COUNTRIES' in sections:
        top = ctx['top_countries'][:10]
        rows = (len(top) or 1) + (1 if ctx['others'] else 0) + 1
        bars = (30 * max(len(top), 1) + 4) * HALF_MM / 320  # hbar_chart(width=320) 한 칸 폭
        note = (ctx['concentration'] or {}).get('note')  # 집중도 미준비(null)면 “데이터 준비 중”
        right = H3_MM + TH_MM + 4 * ROW1_MM + (9 * 1.75 * PT_MM * _lines(note + ' 비중 = 국가별 수출액 ÷ 전체 수출액 × 100.', 9, HALF_MM) + 3 if note else 0)
        pages['top'] = flow([_table('ttable', [ROW1_MM] * rows, head=TH_MM),
                             {'key': 'tgrid', 'height': max(H3_MM + _legend(['', '']) + bars, right)}],
                            BODY_MM - _heading('수출액 상위 10개국 · 순위 변동 · 단위 억 달러'), rest)

    if 'GROWTH_RISK' in sections:
        promising = ctx['promising']
        changes = ctx.get('changes') or {'status': 'NOT_READY'}
        blocks = []
        if changes['status'] == 'READY' and (changes['growth'] or changes['decline'] or changes['bars']):
            # 성장률 상위·감소율 상위 표를 두 칸으로 나란히(table.compact, 국가명 한 줄 기준)
            blocks.append({'key': 'ctables', 'height': change_tables_height(changes)})
            if changes['bars']:  # 소제목 + 증감액 막대(ondemand.html의 diverging_chart row_height와 같은 값)
                blocks.append({'key': 'cchart', 'height': H3_MM + 3.5 + _svg(DIVERGING_ROW * len(changes['bars']) + 4)})   # .cchart-head 위 여백
        else:
            blocks.append({'key': 'cnone', 'height': H3_MM + ROW1_MM + 6})
        # 아래 박스: ML 유망시장 분석(소제목 + 안내 문장 + 예측 표). 박스 안쪽 위아래 여백 6mm
        note = {'key': 'pnote', 'height': LINE[9] * _lines(promising['text'], 9, TEXT_MM - 10) + 7}
        table = _promising_table(promising)
        table = dict(table, head=table['head'] + 6)   # 행 단위로 나뉘는 표. 박스 여백은 머리 높이에 더한다
        formula = {'key': 'cformula', 'height': 3 + 2 * LINE[7.9]}
        # 박스 위 여백: 한 쪽에 남는 공간이 있으면 최대 BOX_GAP_MAX까지 띄우고, 빠듯하면 BOX_GAP_MIN만 둔다(쪽이 넘치지 않게).
        first = BODY_MM - _heading('실제 수출 증감 · 모델 예측 · 단위 억 달러')
        used = (sum(b['height'] for b in blocks) + H3_MM + note['height'] + formula['height']
                + table['head'] + (sum(table['rows']) if table['rows'] else table['empty']))
        gap = max(BOX_GAP_MIN, min(BOX_GAP_MAX, first - used - BOX_GAP_MIN))
        blocks += [{'key': 'mlhead', 'gap': round(gap, 1), 'height': H3_MM + gap - 2}, note, table, formula]
        pages['promising'] = flow(blocks, first, rest)

    if 'LOCAL_ISSUES' in sections:
        pages['articles'] = flow(_article_blocks(ctx['articles']), BODY_MM - _heading('현지 이슈 · 기준') - POLICY_MM, rest)
    return pages


# ---------- 렌더 ----------

def environment():
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=select_autoescape(['html']),
                      undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True)
    env.filters.update(musd=musd, num=num, pct=pct, signed_pct=signed_pct, signed=signed, change=change,
                       emph=emph, ymd=ymd, ymdhm=ymdhm, circled=circled, eok=eok, signed_eok=signed_eok,
                       rank_move=rank_move)
    env.globals.update(line_chart=line_chart, bar_chart=bar_chart, hbar_chart=hbar_chart, diverging_chart=diverging_chart,
                       end_labels_fit=end_labels_fit, legend_value=legend_value, has_values=has_values,
                       full_period_label=full_period_label,
                       series_colors=SERIES_COLORS,
                       colors={'accent': ACCENT, 'up': UP, 'down': DOWN, 'muted': MUTED, 'previous': PREVIOUS},
                       css=(TEMPLATE_DIR / 'report.css').read_text(encoding='utf-8'))
    return env


def render(report_type, context):
    """정기 3종과 RPT_ON_DEMAND. 빠진 변수는 StrictUndefined로 오류."""
    if report_type not in TEMPLATES:
        raise ValueError(f'unsupported_report_type: {report_type}')
    try:
        pages = layout(report_type, context)
    except KeyError as missing:
        # 쪽 나눔 계산이 템플릿보다 먼저 값을 읽으므로, 빠진 변수 오류를 템플릿과 같은 종류로 맞춥니다.
        raise UndefinedError(f'{missing.args[0]!r} is undefined') from missing
    env = environment()
    front_count = 1

    def plan_contents(toc, info, notes, report):
        nonlocal front_count
        planned = contents_layout(toc, info, notes, report)
        front_count = len(planned['pages'])
        return planned

    env.globals['contents_layout'] = plan_contents
    template = env.get_template(TEMPLATES[report_type])
    # First pass measures the template's own metadata. Re-render with the resulting
    # offset so TOC destinations, body page numbers and all totals agree.
    for _ in range(3):
        pages['contents_count'] = front_count
        html = template.render({**context, 'layout': pages})
        if pages['contents_count'] == front_count:
            return html
    raise ValueError('Report contents pagination did not converge')


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
