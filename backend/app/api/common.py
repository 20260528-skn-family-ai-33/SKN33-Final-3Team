import calendar
import hashlib
import json
import re
from datetime import date, datetime, timezone
from decimal import Decimal


class ApiError(Exception):
    def __init__(self, status, code, message, details=None, retryable=False):
        self.status, self.code, self.message = status, code, message
        self.details, self.retryable = details or [], retryable


def unavailable(message, code='DATA_NOT_READY'):
    raise ApiError(503, code, message, retryable=True)


def require(test, message, code='VALIDATION_ERROR', status=400):
    if not test:
        raise ApiError(status, code, message)


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def json_value(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, datetime):
        return value.replace(tzinfo=value.tzinfo or timezone.utc).astimezone(timezone.utc).isoformat().replace('+00:00','Z')
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def clean(value):
    return json.loads(json.dumps(value, ensure_ascii=False, default=json_value, allow_nan=False))


def digest(value):
    return hashlib.sha256(json.dumps(clean(value), sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def month(value):
    require(isinstance(value, str) and re.fullmatch(r'\d{4}-\d{2}', value), '월은 YYYY-MM 형식입니다.')
    try:
        return date.fromisoformat(value+'-01')
    except ValueError:
        raise ApiError(400, 'VALIDATION_ERROR', '유효하지 않은 월입니다.')


def shift_month(value, delta):
    y, m = divmod(value.year * 12 + value.month - 1 + delta, 12)
    return date(y, m+1, 1)


def month_end(value):
    return value.replace(day=calendar.monthrange(value.year, value.month)[1])


def period(start, end):
    return {'start': start.isoformat(), 'end': month_end(end).isoformat()}


def percentage(current, baseline):
    if baseline is None or baseline == 0 or current is None:
        return None
    return round((Decimal(current)-Decimal(baseline))*100/Decimal(baseline), 6)


def page_info(page, size, total):
    return {'page': page, 'page_size': size, 'total': total, 'has_next': page*size < total}


def mask_text(text):
    text = re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[이메일]', text)
    return re.sub(r'(?<!\d)(?:\+?82[- .]?)?0?1[016789][- .]?\d{3,4}[- .]?\d{4}(?!\d)', '[전화번호]', text)
