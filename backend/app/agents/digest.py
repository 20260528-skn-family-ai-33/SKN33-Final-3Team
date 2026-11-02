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
