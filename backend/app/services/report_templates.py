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

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
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
        points = [(x(i), y(v)) for i, v in enumerate(vals) if v is not None]
        if not points:
            continue
        path = ' '.join(f'{px:.1f},{py:.1f}' for px, py in points)
        if s.get('area'):
            base = y(low)
            out.append(f'<polygon points="{points[0][0]:.1f},{base:.1f} {path} {points[-1][0]:.1f},{base:.1f}" fill="{color}" opacity="0.07"/>')
        dash = ' stroke-dasharray="5 4"' if s.get('dashed') else ''
        out.append(f'<polyline points="{path}" fill="none" stroke="{color}" stroke-width="{1.6 if s.get("dashed") else 2}"{dash}/>')
        last = next(v for v in reversed(vals) if v is not None)
        ends.append([points[-1][0], points[-1][1], last, color])
    # 끝 값 라벨이 겹치지 않게 세로로 벌림
    ends.sort(key=lambda e: e[1])
    for i in range(1, len(ends)):
        if ends[i][1] - ends[i - 1][1] < 11:
            ends[i][1] = ends[i - 1][1] + 11
    for px, py, value, color in ends:
        out.append(f'<circle cx="{px:.1f}" cy="{y(value):.1f}" r="3" fill="{color}"/>')
        out.append(f'<text x="{px + 7:.1f}" y="{py + 3:.1f}" class="end" fill="{color}">{num(value, digits)}</text>')
    out.append('</svg>')
    return Markup(''.join(out))


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


def hbar_chart(rows, colors, *, label_width=120, width=640, row_height=26, digits=1, divisor=1, tail_width=0):
    """가로 막대. rows: [{label, values:[현재, 비교...], tail?:{text, badge?}}]. digits는 막대별 목록도 가능. 막대 길이는 전체 최대값 기준."""
    peak = max((v for r in rows for v in r['values'] if v is not None), default=0) / divisor or 1
    bars = len(colors)
    bar_h = 8 if bars > 1 else 12
    plot_w = width - label_width - tail_width - 40
    height = row_height * len(rows) + 4
    out = [f'<svg class="chart" viewBox="0 0 {width} {height}" width="100%" role="img">']
    for i, r in enumerate(rows):
        top = i * row_height + (row_height - bars * (bar_h + 2)) / 2
        out.append(f'<text x="0" y="{i * row_height + row_height / 2 + 4:.1f}" class="lbl">{escape(r["label"])}</text>')
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


# ---------- 렌더 ----------

def environment():
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=select_autoescape(['html']),
                      undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True)
    env.filters.update(musd=musd, num=num, pct=pct, signed_pct=signed_pct, signed=signed, change=change,
                       emph=emph, ymd=ymd, ymdhm=ymdhm, circled=circled)
    env.globals.update(line_chart=line_chart, bar_chart=bar_chart, hbar_chart=hbar_chart,
                       series_colors=SERIES_COLORS,
                       colors={'accent': ACCENT, 'up': UP, 'down': DOWN, 'muted': MUTED, 'previous': PREVIOUS},
                       css=(TEMPLATE_DIR / 'report.css').read_text(encoding='utf-8'))
    return env


def render(report_type, context):
    """report_type: RPT_MONTHLY · RPT_WEEKLY · RPT_QUARTERLY. 빠진 변수는 StrictUndefined로 오류."""
    if report_type not in TEMPLATES:
        raise ValueError(f'unsupported_report_type: {report_type}')
    return environment().get_template(TEMPLATES[report_type]).render(**context)


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
