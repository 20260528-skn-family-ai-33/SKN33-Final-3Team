import calendar
import hashlib
import json
import re
from datetime import date, datetime, timezone
from decimal import Decimal


class ApiError(Exception):
    def __init__(self, status, code, message, details=None, retryable=False, retry_after=None):
        self.status, self.code, self.message = status, code, message
        self.details, self.retryable = details or [], retryable
        self.retry_after = retry_after


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


# 생성 SQL 결과의 기간을 증명하지 못했을 때(agents.sql.period_covered) 채팅 답변과 리포트에 같은 안내를 붙인다.
LLM_SQL_PERIOD_WARNING = '생성 SQL 결과는 집계 기간을 확인할 수 없어 답변 문장의 수치를 검증하지 않았습니다. 수치는 결과표에서 확인해 주세요.'


def sql_period_unproven(meta):
    """작성 입력·경고·리포트에서 기간 미확인으로 다룰 생성 SQL 결과인지(digest·meta·리포트 사본 meta 공통).
    조회 기간 전체(period_verified)나 연도별 합계·차액 칸(annual_verified)을 SQL 식으로 증명한 결과는 아니다.
    최종 답변의 수치 문장 차단은 sql_period_partial로 판단한다."""
    meta = meta or {}
    return (meta.get('kind') == 'LLM_SQL' and meta.get('period_verified') is not True
            and meta.get('annual_verified') is not True)


def sql_period_partial(meta):
    """최종 답변에서 검증 실패 수치 문장을 결과표 안내로 바꿀 생성 SQL 결과인지. 조회 기간 전체를 증명하지 못했으면
    연간 칸만 증명했어도(annual_verified) 해당한다. 증명한 칸으로 검증된 문장은 그대로 남는다."""
    meta = meta or {}
    return meta.get('kind') == 'LLM_SQL' and meta.get('period_verified') is not True


def period_label(start, end):
    """표 열·차트 시리즈용 기간 이름. 한 해 전체면 '2023년', 아니면 '2023-01~2023-06'. date와 'YYYY-MM-DD' 문자열을 받는다."""
    start, end = str(start)[:7], str(end)[:7]
    return f'{start[:4]}년' if start[:4] == end[:4] and (start[5:], end[5:]) == ('01', '12') else f'{start}~{end}'


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
