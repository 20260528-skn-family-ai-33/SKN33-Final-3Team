"""관세청 품목별 국가별 수출입실적 API의 원본 CSV와 수집 통계를 저장합니다 (1년·한 달·12개월 이내 구간)."""
import argparse
import csv
import json
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

import requests
from dotenv import dotenv_values

BASE = Path(__file__).resolve().parent
# 인증키는 저장소 루트의 .env에서 읽습니다.
ENV_PATH = Path(__file__).resolve().parents[5] / ".env"
ENV_KEY = "customs"
API_URL = "https://apis.data.go.kr/1220000/nitemtrade/getNitemtradeList"
# 응답 필드명을 유지한 원본 CSV의 기본 컬럼입니다. 추가 응답 필드가 있으면 뒤에 붙여 저장합니다.
RAW_FIELDS = ["year", "statCd", "statCdCntnKor1", "hsCd", "statKor",
              "expDlr", "expWgt", "impDlr", "impWgt", "balPayments"]
RETRIES = 3
RETRY_WAIT = 5
TIMEOUT = 300

# ISO 3166-1 alpha-2 국가 코드 (관세청 국가코드와 같은 체계). 1회 호출 = 국가 1개 × 1년.
COUNTRIES = """AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS
BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET
FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO
IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH
MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM
PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG
TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW""".split()


class Tee:
    """print 출력을 화면과 로그 파일에 동시에 씁니다."""

    def __init__(self, path):
        self.file = open(path, "w", encoding="utf-8")
        self.console = sys.stdout

    def write(self, text):
        self.console.write(text)
        self.file.write(text)

    def flush(self):
        self.console.flush()
        self.file.flush()


def load_key(env_path):
    values = {k.lower(): v for k, v in dotenv_values(env_path).items()}  # customs·CUSTOMS 모두 허용
    key = (values.get(ENV_KEY) or "").strip()
    if not key:
        raise SystemExit(f"{env_path}에 {ENV_KEY} 인증키가 없습니다.")
    return unquote(key)  # Encoding 키도 Decoding 키로 바꿔 사용 (requests가 다시 인코딩함)


# XML 응답의 오류를 확인하고 품목 목록을 원본 값 그대로 추출합니다.
def parse_response(payload):
    root = ET.fromstring(payload)
    code = root.findtext(".//resultCode")
    if code is None:
        gateway_code = root.findtext(".//returnReasonCode") or "unknown"
        raise ValueError(f"API gateway error code: {gateway_code}")
    if code != "00":
        raise ValueError(f"API resultCode: {code} {root.findtext('.//resultMsg')}")
    return [{child.tag: child.text or "" for child in item} for item in root.iter("item")]


def month_range(start, end):
    """YYYYMM 두 값 사이의 달을 API 응답 형식(YYYY.MM)으로 나열합니다."""
    y, m = int(start[:4]), int(start[4:])
    months = []
    while (y, m) <= (int(end[:4]), int(end[4:])):
        months.append(f"{y}.{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return months


# --year / --month / --start·--end 옵션을 (시작 YYYYMM, 끝 YYYYMM, 폴더 이름)으로 바꿉니다.
def resolve_period(year=None, month=None, start=None, end=None):
    if month:
        start = end = month
    elif start or end:
        if not (start and end):
            raise SystemExit("--start와 --end는 함께 지정합니다.")
    else:
        start, end = f"{year}01", f"{year}12"
    for value in (start, end):
        if not (len(value) == 6 and value.isdigit() and 1 <= int(value[4:]) <= 12):
            raise SystemExit(f"YYYYMM 형식이 아닙니다: {value}")
    if start > end:
        raise SystemExit("시작 월이 끝 월보다 늦습니다.")
    if len(month_range(start, end)) > 12:
        raise SystemExit("조회 기간은 12개월 이내입니다 (API 제한).")
    if start[:4] == end[:4] and start[4:] == "01" and end[4:] == "12":
        label = start[:4]  # 1년 전체는 기존처럼 연도 폴더
    else:
        label = start if start == end else f"{start}_{end}"
    return start, end, label


def fetch(key, country, start, end):
    params = dict(serviceKey=key, strtYymm=start, endYymm=end, cntyCd=country)
    for attempt in range(1, RETRIES + 1):
        try:
            response = requests.get(API_URL, params=params, timeout=TIMEOUT)
            return parse_response(response.content)
        except (requests.RequestException, ET.ParseError, ValueError) as error:
            print(f"  재시도 {attempt}/{RETRIES} {country} {start}~{end}: {type(error).__name__} {error}")
            time.sleep(RETRY_WAIT)
    raise RuntimeError(f"{country} {start}~{end} 수집 실패")


def field_order(rows):
    extra = sorted({k for row in rows for k in row} - set(RAW_FIELDS))
    return RAW_FIELDS + extra


def write_csv(path, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=field_order(rows))
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


# 국가별 원본을 캐시에 저장합니다. 이미 받은 국가는 건너뛰어 중단 후 다시 실행하면 이어받습니다.
def collect(start, end, countries, cache_dir, key):
    failed = []
    for i, country in enumerate(countries, 1):
        path = cache_dir / f"{country}.csv"
        if path.exists():
            print(f"[{i}/{len(countries)}] {country}: {len(read_csv(path)):,}행 (이전에 받은 파일 사용)")
            continue
        try:
            rows = fetch(key, country, start, end)
        except RuntimeError as error:
            print(f"  [실패] {error}")
            failed.append(country)
            continue
        if not rows:  # 미공개 달이나 거래 없는 국가는 저장하지 않아 다음 실행 때 다시 조회
            print(f"[{i}/{len(countries)}] {country}: 0행 (저장 안 함, 다음 실행 때 다시 조회)")
            continue
        write_csv(path, rows)
        print(f"[{i}/{len(countries)}] {country}: {len(rows):,}행")
    return failed


# 국가별 원본을 받은 순서 그대로 하나의 CSV로 합칩니다. 값과 총계 행은 손대지 않습니다.
def merge(countries, cache_dir, out_path):
    rows = []
    for country in countries:
        path = cache_dir / f"{country}.csv"
        if path.exists():
            rows.extend(read_csv(path))
    write_csv(out_path, rows)
    return rows


def summarize(rows, start, end, countries, failed):
    data = [r for r in rows if r.get("year") != "총계"]
    months = sorted({r["year"] for r in data})
    expected = month_range(start, end)
    keys = [(r["year"], r["statCd"], r["hsCd"]) for r in data]
    to_int = lambda v: int(v) if v and v.lstrip("-").isdigit() else 0
    return {
        "data": "관세청 품목별 국가별 수출입실적",
        "period": {"start": start, "end": end},
        "requested_countries": len(countries),
        "failed_countries": failed,
        "rows_total": len(rows),
        "rows_total_line": len(rows) - len(data),
        "rows_data": len(data),
        "countries_with_data": len({r["statCd"] for r in data}),
        "hs10_codes": len({r["hsCd"] for r in data}),
        "months": months,
        "missing_months": [m for m in expected if m not in months],
        "duplicate_keys": len(keys) - len(set(keys)),
        "out_of_period_rows": sum(r["year"] not in expected for r in data),
        "export_usd": sum(to_int(r.get("expDlr")) for r in data),
        "import_usd": sum(to_int(r.get("impDlr")) for r in data),
        "collection_complete": not failed,
    }


def main():
    parser = argparse.ArgumentParser(description="관세청 수출입실적 원본을 CSV로 수집 (1년·한 달·12개월 이내 구간)")
    parser.add_argument("--year", type=int, default=2025, help="1년(1~12월) 수집 연도 (기본 2025)")
    parser.add_argument("--month", help="한 달만 수집 (YYYYMM, 예: 202503)")
    parser.add_argument("--start", help="구간 시작 월 (YYYYMM, --end와 함께)")
    parser.add_argument("--end", help="구간 끝 월 (YYYYMM, 시작부터 12개월 이내)")
    parser.add_argument("--countries", help="쉼표로 구분한 국가 코드 (기본: 전체 249개)")
    parser.add_argument("--env", type=Path, default=ENV_PATH, help="인증키 .env 경로 (기본: 저장소 루트)")
    parser.add_argument("--output-dir", type=Path, default=BASE / "customs_outputs", help="결과 상위 폴더")
    args = parser.parse_args()
    countries = [c.strip().upper() for c in args.countries.split(",")] if args.countries else COUNTRIES
    start, end, label = resolve_period(args.year, args.month, args.start, args.end)

    out_dir = args.output_dir / label
    cache_dir = out_dir / "_cache"
    log_dir = out_dir / "logs"
    cache_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    started = datetime.now()
    log_path = log_dir / f"customs_{label}_{started:%Y%m%d_%H%M%S}.log"
    sys.stdout = Tee(log_path)
    print(f"관세청 수출입실적 수집 | 기간 {start}~{end} | 국가 {len(countries)}개 | 시작 {started:%Y-%m-%d %H:%M:%S}")

    failed = collect(start, end, countries, cache_dir, load_key(args.env))
    raw_path = out_dir / f"customs_raw_{label}.csv"
    rows = merge(countries, cache_dir, raw_path)
    stats = summarize(rows, start, end, countries, failed)
    stats_path = out_dir / f"customs_collection_{label}.json"
    stats_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")

    months = stats["months"]
    print("\n── 검증 ──")
    print(f"전체 {stats['rows_total']:,}행 (데이터 {stats['rows_data']:,} + 총계 {stats['rows_total_line']:,})"
          f" | 국가 {stats['countries_with_data']} | HS10 {stats['hs10_codes']:,}"
          f" | 월 {months[0] if months else '-'}~{months[-1] if months else '-'} ({len(months)}개월)")
    print(f"키(year·statCd·hsCd) 중복 {stats['duplicate_keys']}건 | 기간 밖 행 {stats['out_of_period_rows']}건")
    if stats["missing_months"]:
        print(f"※ 데이터가 없는 달: {', '.join(stats['missing_months'])} (아직 공개 전이거나 거래 없음)")
    print(f"수출 합계 {stats['export_usd'] / 1e8:,.1f}억 달러 | 수입 합계 {stats['import_usd'] / 1e8:,.1f}억 달러 (총계 행 제외)")
    print(f"\n원본 CSV: {raw_path}\n수집 통계: {stats_path}")
    if failed:
        print(f"실패 국가 {len(failed)}개: {', '.join(failed)} → 다시 실행하면 실패분만 재시도합니다.")
    ended = datetime.now()
    print(f"종료 {ended:%Y-%m-%d %H:%M:%S} | 소요 {str(ended - started).split('.')[0]}")
    print(f"로그: {log_path}")


if __name__ == "__main__":
    main()
