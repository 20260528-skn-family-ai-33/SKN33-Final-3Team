"""Bounded numeric evidence passed to compose (§11.2)."""
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
import json
import math

from backend.app.agents.state import MAX_DIGEST_BYTES, shorten_name

# TODO(C-27): tune evidence size with the golden set.
MAX_DIGEST_ROWS = 60


def number(value, places=0):
    if value is None: return None
    decimal = Decimal(str(value))
    if not decimal.is_finite(): raise ValueError('Digest numbers must be finite')
    rounded = decimal.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    return int(rounded) if places == 0 else float(rounded)


def _normalize(value, key=''):
    if isinstance(value, dict): return {k: _normalize(v, k) for k, v in value.items()}
    if isinstance(value, (list, tuple)): return [_normalize(v, key) for v in value]
    if isinstance(value, (date, datetime)): return value.isoformat()
    if isinstance(value, str):
        if key in ('name', 'country_name'): return shorten_name(value)
        return value[:200 if key in ('summary', 'sql_summary') else 100]
    if isinstance(value, (Decimal, int, float)) and not isinstance(value, bool):
        if key.endswith('_usd') or key == 'exp_12m': return number(value)
        if key.endswith('_pct'): return number(value, 1)
        if isinstance(value, Decimal): value = float(value)
        if not math.isfinite(value): raise ValueError('Digest numbers must be finite')
    return value


def korean_usd(value):
    """답변에 그대로 옮길 금액 표기. 만 단위로 반올림한다(2,610만 달러, 1억 2,346만 달러). 1만 달러 미만은 달러 그대로.
    LLM이 만·억 단위를 직접 바꾸면 자릿수가 틀리므로(2,610만 → 2억6100만) 코드가 만든다."""
    if value is None: return None
    amount = Decimal(str(value))
    sign, amount = ('-' if amount < 0 else ''), abs(amount)
    whole = int(amount.quantize(Decimal(1), rounding=ROUND_HALF_UP))
    if whole < 10**4: return f'{sign}{whole:,}달러'
    eok, man = divmod(int((amount / 10**4).quantize(Decimal(1), rounding=ROUND_HALF_UP)), 10**4)
    return sign + ' '.join([f'{eok:,}억'] * bool(eok) + [f'{man:,}만'] * bool(man)) + ' 달러'


def display_amounts(digest):
    """compose 입력용 사본: 금액 숫자를 korean_usd 표기로 바꾼다. 검증은 원래 digest의 숫자로 한다."""
    usd = {c['key'] for c in digest.get('columns', []) if isinstance(c, dict) and c.get('unit') == 'USD'}
    def walk(value, key=''):
        if isinstance(value, dict): return {k: v if k == 'columns' else walk(v, k) for k, v in value.items()}
        if isinstance(value, list): return [walk(v, key) for v in value]
        if (isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)
                and (key.endswith('_usd') or key == 'exp_12m' or key in usd)):
            return korean_usd(value)
        return value
    return walk(digest)


def bounded(payload):
    result = _normalize(deepcopy(payload))
    result.setdefault('truncated', False)
    def encode(value):
        return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
    def lists(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key != 'columns': yield from lists(child)
        elif isinstance(value, list):
            if value: yield value
            for child in value: yield from lists(child)
    while len(encode(result)) > MAX_DIGEST_BYTES:
        candidates = [items for items in lists(result) if items]
        if not candidates: raise ValueError('Digest metadata exceeds the evidence budget')
        # Remove the last row from the largest leaf list before removing a target.
        leaves = [items for items in candidates if not any(list(lists(x)) for x in items)]
        max(leaves or candidates, key=lambda items: len(encode(items))).pop()
        result['truncated'] = True
    return result
