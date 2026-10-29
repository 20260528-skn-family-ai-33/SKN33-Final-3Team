"""게시 날짜를 한국 달력 날짜로 통일합니다. 수집 시각은 운영 로그용으로만 유지합니다."""
from datetime import date, datetime
from zoneinfo import ZoneInfo


def publication_date(value):
    # 새 데이터는 YYYY-MM-DD, 기존 시각 데이터는 한국 날짜로 변환해 호환합니다.
    if not isinstance(value, str) or not value.strip():
        raise ValueError('게시 날짜가 비어 있습니다')
    if len(value) == 10:
        return date.fromisoformat(value)
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(ZoneInfo('Asia/Seoul'))
    return parsed.date()
