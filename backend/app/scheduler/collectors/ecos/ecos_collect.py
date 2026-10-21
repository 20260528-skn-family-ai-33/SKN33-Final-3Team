"""한국은행 ECOS 일별 환율(731Y001 주요국 통화의 대원화환율)을 조회합니다. 호출과 파싱·검증만 담당하고 DB에는 쓰지 않습니다.

실행 (저장소 루트에서): python -m backend.app.scheduler.collectors.ecos.ecos_collect --start 2026-10-01 --end 2026-10-07 [--items 0000001,0000002]
- 인증키는 ecos 또는 ECOS_API_KEY 값을 씁니다 (대소문자 구분 없음). 환경변수를 먼저 보고, 없을 때만 저장소 루트 .env를 봅니다.
- 여러 페이지로 받을 때 첫 페이지의 전체 건수(list_total_count)와 실제로 받은 건수가 다르거나
  중간 페이지가 비어 있으면 EcosError로 멈춥니다 (일부만 받은 기간을 정상 수집으로 저장하지 않게).
- 반환값은 (기준일, 환율) 목록입니다. 기준일은 응답의 TIME(원천 영업일)이며 실행한 날짜가 아닙니다.
- 환율은 Decimal 그대로 돌려줍니다. JPY처럼 100단위 통화도 나누지 않습니다 (currency.quote_unit이 단위를 뜻함).
- 조회 기간에 자료가 없으면(주말·휴일만 조회, INFO-200) 빈 목록입니다. 인증·요청 오류는 EcosError로 알립니다.
- 타임아웃·접속 오류·5xx·ECOS 서버 오류와 함께, HTTP 200인데 JSON이 아닌 응답(점검·안내 HTML 페이지 등)도 일시 오류로 보고
  RETRIES번까지 다시 부릅니다. 인증키 오류·4xx처럼 다시 불러도 같은 오류는 바로 멈춥니다.
- stats(dict)를 넘기면 실제로 보낸 요청 수(requests)와 응답에 들어 있던 원본 행 수(received)를 누적합니다.
  중간에 실패해도 그때까지의 값이 남으므로 batch_run에 실제 호출 횟수를 기록할 수 있습니다.
- 금액 단위도 행마다 확인합니다. 단위(UNIT_NAME)가 '원'이 아니거나, 항목명(ITEM_NAME1)의 기준 수량(예: '원/일본엔(100엔)'의 100)이
  호출한 쪽이 준 quote_unit(currency.quote_unit)과 다르면 EcosError로 멈춥니다 (잘못된 단위의 값을 저장하지 않게).
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
UNIT_NAME = "원"  # 731Y001 값의 금액 단위. 응답 단위가 다르면 저장하지 않음
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
    """환경변수에서 두 별칭(ecos·ECOS_API_KEY)을 먼저 찾고, 없을 때만 .env에서 찾습니다.
    (.env의 ecos가 서버 환경변수 ECOS_API_KEY를 이기면 키를 교체해도 옛 키를 계속 쓰게 됨)"""
    for source in (os.environ, dotenv_values(env_path)):
        values = {k.lower(): (v or "").strip() for k, v in source.items()}
        key = next((values[name] for name in ENV_KEYS if values.get(name)), "")
        if key:
            return key
    raise EcosError(f"환경변수와 {env_path}에 ECOS 인증키(ecos 또는 ECOS_API_KEY)가 없습니다.")


def mask(text, key):
    return str(text).replace(key, "***") if key else str(text)


def quote_unit_of(item_name):
    """항목명 괄호 안의 기준 외화 수량. '원/일본엔(100엔)' → 100, 수량이 없으면('원/유로') 1."""
    found = re.search(r"\(([0-9][0-9,]*)", item_name or "")
    return int(found.group(1).replace(",", "")) if found else 1


def page_url(key, item_code, start, end, first, last):
    return (f"{API_BASE}/StatisticSearch/{key}/json/kr/{first}/{last}/{STAT_CODE}/{CYCLE}/"
            f"{start:%Y%m%d}/{end:%Y%m%d}/{item_code}")


def parse_page(payload, item_code, quote_unit=None):
    """한 페이지 응답을 (전체 건수, [(기준일, 환율)])로 바꿉니다. 자료 없음은 (0, [])입니다.
    quote_unit을 주면 행마다 금액 단위('원')와 기준 수량이 그 값과 같은지 확인합니다."""
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
        if row.get("UNIT_NAME") != UNIT_NAME:
            raise EcosError(f"금액 단위가 '{UNIT_NAME}'이 아닙니다: {item_code} {row.get('UNIT_NAME')!r}")
        if quote_unit is not None and quote_unit_of(row.get("ITEM_NAME1")) != quote_unit:
            raise EcosError(f"기준 수량이 currency.quote_unit({quote_unit})과 다릅니다: {item_code} {row.get('ITEM_NAME1')!r}")
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


def get_json(url, key, stats=None):
    """일시 오류(타임아웃·접속 오류·5xx·ECOS 서버 오류·JSON이 아닌 안내 페이지)만 RETRIES번까지 다시 부릅니다.
    (응답, 호출 횟수)를 돌려줍니다. stats를 주면 요청을 보낼 때마다 stats["requests"]를 1 늘립니다 (실패해도 남음)."""
    for attempt in range(1, RETRIES + 1):
        if stats is not None:
            stats["requests"] = stats.get("requests", 0) + 1
        try:
            response = requests.get(url, timeout=TIMEOUT)
            if response.status_code >= 500:
                raise EcosError(f"ECOS HTTP {response.status_code}", retryable=True)
            response.raise_for_status()
            try:
                payload = response.json()
            except ValueError:  # HTTP 200인데 JSON이 아님: 점검·안내 페이지 등 일시적인 비정상 응답
                raise EcosError("ECOS 응답이 JSON이 아닙니다 (점검·안내 페이지 등)", retryable=True) from None
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


def fetch_daily(key, item_code, start, end, quote_unit=None, stats=None):
    """item_code 통화의 start~end(양끝 포함) 일별 환율. ([(기준일, 환율)], API 호출 횟수)를 돌려줍니다.
    quote_unit(예: JPY 100)을 주면 응답의 금액 단위·기준 수량이 다를 때 EcosError로 멈춥니다.
    stats(dict)를 주면 요청 수(requests)와 응답의 원본 행 수(received)를 누적합니다. 예외로 끝나도 그때까지 값이 남습니다."""
    if start > end:
        raise EcosError(f"시작일이 종료일보다 늦습니다: {start} > {end}")
    rows, requests_made, first, expected = [], 0, 1, None
    while True:
        payload, calls = get_json(page_url(key, item_code, start, end, first, first + PAGE_SIZE - 1), key, stats)
        requests_made += calls
        if stats is not None:  # 검증 전에 응답에 들어 있던 행 수 (DB 반영 건수와 다름)
            body = payload.get("StatisticSearch") if isinstance(payload, dict) else None
            raw = body.get("row") if isinstance(body, dict) else None
            stats["received"] = stats.get("received", 0) + (len(raw) if isinstance(raw, list) else 0)
        total, page = parse_page(payload, item_code, quote_unit)
        if expected is None:
            expected = total  # 첫 페이지가 알려준 전체 건수. 자료 없음(INFO-200)이면 0
        if not page:
            if rows:  # 앞 페이지는 받았는데 다음 페이지가 비어 있음: 일부 누락이므로 저장하지 않음
                raise EcosError(f"다음 페이지가 비어 있습니다: {item_code} {len(rows)}/{expected}건", retryable=True)
            break
        if total != expected:  # 받는 도중 원천 건수가 바뀜
            raise EcosError(f"페이지마다 전체 건수가 다릅니다: {item_code} {expected} → {total}건", retryable=True)
        rows += page
        first += PAGE_SIZE
        if first > expected:
            break
    if len(rows) != expected:
        raise EcosError(f"받은 건수가 전체 건수와 다릅니다: {item_code} {len(rows)}/{expected}건", retryable=True)
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
