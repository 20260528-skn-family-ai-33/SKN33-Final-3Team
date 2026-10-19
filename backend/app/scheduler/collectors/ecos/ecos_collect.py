"""한국은행 ECOS 일별 환율(731Y001 주요국 통화의 대원화환율)을 조회합니다. 호출과 파싱·검증만 담당하고 DB에는 쓰지 않습니다.

실행 (저장소 루트에서): python -m backend.app.scheduler.collectors.ecos.ecos_collect --start 2026-10-01 --end 2026-10-07 [--items 0000001,0000002]
- 인증키는 저장소 루트 .env의 ecos 또는 ECOS_API_KEY 값을 씁니다 (대소문자 구분 없음, 환경변수가 있으면 우선).
- 반환값은 (기준일, 환율) 목록입니다. 기준일은 응답의 TIME(원천 영업일)이며 실행한 날짜가 아닙니다.
- 환율은 Decimal 그대로 돌려줍니다. JPY처럼 100단위 통화도 나누지 않습니다 (currency.quote_unit이 단위를 뜻함).
- 조회 기간에 자료가 없으면(주말·휴일만 조회, INFO-200) 빈 목록입니다. 인증·요청 오류는 EcosError로 알립니다.
- 인증키가 들어간 URL은 로그·예외 메시지에 남기지 않습니다.
"""
import argparse
import os
import re
import time
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import requests
from dotenv import dotenv_values

# 인증키는 저장소 루트의 .env에서 읽습니다.
ENV_PATH = Path(__file__).resolve().parents[5] / ".env"
ENV_KEYS = ("ecos", "ecos_api_key")
API_BASE = "https://ecos.bok.or.kr/api"
STAT_CODE = "731Y001"  # 주요국 통화의 대원화환율 (매매기준율)
CYCLE = "D"
PAGE_SIZE = 1000
RETRIES = 3
RETRY_WAIT = 5
TIMEOUT = 60
NO_DATA = "INFO-200"  # 해당하는 데이터가 없음 (정상)
# ECOS 서버 쪽 일시 오류. 그 밖의 코드(INFO-100 인증키 오류, ERROR-1xx 요청 형식 오류 등)는 다시 불러도 같으므로 재시도하지 않음
RETRY_CODES = {"ERROR-500", "ERROR-600", "ERROR-601", "ERROR-602"}


class EcosError(Exception):
    """ECOS 호출·응답 오류. retryable이면 잠시 뒤 다시 실행해 볼 만한 오류입니다."""

    def __init__(self, message, code=None, retryable=False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def load_key(env_path=ENV_PATH):
    values = {k.lower(): v for k, v in {**dotenv_values(env_path), **os.environ}.items() if v}
    key = next((values[name].strip() for name in ENV_KEYS if values.get(name, "").strip()), "")
    if not key:
        raise EcosError(f"{env_path}에 ECOS 인증키(ecos 또는 ECOS_API_KEY)가 없습니다.")
    return key


def mask(text, key):
    return str(text).replace(key, "***") if key else str(text)


def page_url(key, item_code, start, end, first, last):
    return (f"{API_BASE}/StatisticSearch/{key}/json/kr/{first}/{last}/{STAT_CODE}/{CYCLE}/"
            f"{start:%Y%m%d}/{end:%Y%m%d}/{item_code}")


def parse_page(payload, item_code):
    """한 페이지 응답을 (전체 건수, [(기준일, 환율)])로 바꿉니다. 자료 없음은 (0, [])입니다."""
    if not isinstance(payload, dict):
        raise EcosError("ECOS 응답이 JSON 객체가 아닙니다.")
    if "RESULT" in payload:
        code = str(payload["RESULT"].get("CODE", ""))
        if code == NO_DATA:
            return 0, []
        raise EcosError(f"ECOS 오류 {code}: {payload['RESULT'].get('MESSAGE', '')}", code, code in RETRY_CODES)
    body = payload.get("StatisticSearch")
    if not isinstance(body, dict) or not isinstance(body.get("row"), list):
        raise EcosError("ECOS 응답에 StatisticSearch.row가 없습니다.")
    rows = []
    for row in body["row"]:
        if row.get("ITEM_CODE1") != item_code:
            raise EcosError(f"요청하지 않은 항목이 응답에 있습니다: {row.get('ITEM_CODE1')} (요청 {item_code})")
        raw_time, raw_value = str(row.get("TIME", "")), str(row.get("DATA_VALUE", "")).replace(",", "").strip()
        if not re.fullmatch(r"[0-9]{8}", raw_time):
            raise EcosError(f"기준일 형식 오류: {item_code} {raw_time!r}")
        try:
            base_date = datetime.strptime(raw_time, "%Y%m%d").date()
            rate = Decimal(raw_value)
        except (ValueError, InvalidOperation):
            raise EcosError(f"환율 값 오류: {item_code} {raw_time} {raw_value!r}") from None
        if not rate.is_finite() or rate <= 0:
            raise EcosError(f"0 이하이거나 숫자가 아닌 환율: {item_code} {raw_time} {raw_value!r}")
        rows.append((base_date, rate))
    return int(body.get("list_total_count", len(rows))), rows


def get_json(url, key):
    """일시 오류(타임아웃·접속 오류·5xx·ECOS 서버 오류)만 RETRIES번까지 다시 부릅니다. (응답, 호출 횟수)를 돌려줍니다."""
    for attempt in range(1, RETRIES + 1):
        try:
            response = requests.get(url, timeout=TIMEOUT)
            if response.status_code >= 500:
                raise EcosError(f"ECOS HTTP {response.status_code}", retryable=True)
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, dict) and payload.get("RESULT", {}).get("CODE") in RETRY_CODES:
                raise EcosError(f"ECOS 오류 {payload['RESULT']['CODE']}", payload["RESULT"]["CODE"], True)
            return payload, attempt
        except (requests.Timeout, requests.ConnectionError) as error:
            last = EcosError(f"ECOS 접속 실패: {type(error).__name__}", retryable=True)
        except EcosError as error:
            if not error.retryable:
                raise
            last = error
        except (requests.RequestException, ValueError) as error:  # 4xx·JSON 아님: 다시 불러도 같음
            raise EcosError(f"ECOS 요청 실패: {mask(error, key)}") from None
        if attempt < RETRIES:
            time.sleep(RETRY_WAIT)
    last.args = (f"{last} (재시도 {RETRIES}회 실패)",)
    raise last


def fetch_daily(key, item_code, start, end):
    """item_code 통화의 start~end(양끝 포함) 일별 환율. ([(기준일, 환율)], API 호출 횟수)를 돌려줍니다."""
    if start > end:
        raise EcosError(f"시작일이 종료일보다 늦습니다: {start} > {end}")
    rows, requests_made, first = [], 0, 1
    while True:
        payload, calls = get_json(page_url(key, item_code, start, end, first, first + PAGE_SIZE - 1), key)
        requests_made += calls
        total, page = parse_page(payload, item_code)
        rows += page
        first += PAGE_SIZE
        if not page or first > total:
            break
    dates = [d for d, _ in rows]
    if len(dates) != len(set(dates)):
        raise EcosError(f"같은 기준일이 중복된 응답: {item_code}")
    outside = [d for d in dates if not start <= d <= end]
    if outside:
        raise EcosError(f"조회 기간 밖의 기준일: {item_code} {outside[0]}")
    return sorted(rows), requests_made


def parse_date(value):
    return datetime.strptime(value, "%Y-%m-%d").date()


def main():
    parser = argparse.ArgumentParser(description="ECOS 일별 환율 조회 (DB에 저장하지 않고 화면에만 출력)")
    parser.add_argument("--start", type=parse_date, required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", type=parse_date, default=date.today(), help="YYYY-MM-DD (기본: 오늘)")
    parser.add_argument("--items", default="0000001", help="ECOS 항목 코드, 쉼표로 구분 (기본: 0000001 미국 달러)")
    args = parser.parse_args()
    key = load_key()
    for item in args.items.split(","):
        rows, calls = fetch_daily(key, item.strip(), args.start, args.end)
        print(f"{item}: {len(rows)}일 (API 호출 {calls}회)")
        for base_date, rate in rows[-5:]:
            print(f"  {base_date} {rate}")


if __name__ == "__main__":
    main()
