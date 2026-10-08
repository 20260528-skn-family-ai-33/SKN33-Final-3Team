"""단신속보뉴스 API의 원본 CSV·JSON과 수집 통계만 저장합니다."""
import argparse
import csv
import hashlib
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote
import xml.etree.ElementTree as ET

import requests
from dotenv import dotenv_values

BASE = Path(__file__).resolve().parent
# 인증키는 저장소 루트의 .env에서 읽습니다.
ENV_PATH = Path(__file__).resolve().parents[5] / ".env"
API_URL = "https://apis.data.go.kr/B410001/shortBreakingNews/shortBreakingNews"
# 응답 필드명을 유지한 단신속보뉴스 원본 CSV의 기본 컬럼입니다.
RAW_FIELDS = ["kbc", "nttSn", "nttSj", "smmarCn", "regn", "nat",
              "othbcDt", "kotraNewsUrl", "realAtfileInfoList"]


# XML의 중첩 첨부파일 정보와 반복 항목을 원본 구조에 맞게 변환합니다.
def xml_value(node):
    if not len(node):
        return node.text or ""
    result = {}
    for child in node:
        value = xml_value(child)
        if child.tag in result:
            if not isinstance(result[child.tag], list):
                result[child.tag] = [result[child.tag]]
            result[child.tag].append(value)
        else:
            result[child.tag] = value
    return result


# 페이지 응답을 해시로 변환하여 같은 페이지가 반복되는지 확인합니다.
def hash_key(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# JSON·XML 응답의 오류를 확인하고 기사 목록과 전체 건수를 추출합니다.
def parse_response(payload):
    """JSON 요청에 XML 오류 응답이 반환되는 경우도 확인합니다."""
    # UTF-8 BOM과 앞쪽 공백을 제거한 뒤 JSON/XML 형식을 판별합니다.
    payload = payload.lstrip().removeprefix(b"\xef\xbb\xbf").lstrip()
    if payload.startswith(b"<"):
        root = ET.fromstring(payload)
        for node in root.iter():
            node.tag = node.tag.split("}")[-1]
        code = root.findtext(".//resultCode")
        if code is None:
            gateway_code = root.findtext(".//returnReasonCode") or "unknown"
            raise ValueError(f"API gateway error code: {gateway_code}")
        body = root.find(".//body")
        items = [] if body is None else [
            xml_value(item)
            for item in body.findall("./itemList/item")]
        total = root.findtext(".//totalCnt")
    else:
        obj = json.loads(payload)
        response = obj.get("response", {})
        code = str(response.get("header", {}).get("resultCode", "unknown"))
        body = response.get("body") or {}
        total = body.get("totalCnt")
        listing = body.get("itemList")
        # 0건 응답의 빈 값은 허용하지만 잘못된 자료형을 빈 목록으로 바꾸지 않습니다.
        empty_result = str(total).strip() == "0"
        if listing is None or listing == "":
            if not empty_result:
                raise ValueError("API 응답에 기사 목록이 없습니다")
            listing = {}
        if not isinstance(listing, dict):
            raise ValueError("API itemList는 객체여야 합니다")
        items = listing.get("item")
        if items is None or items == "":
            items = []
        elif isinstance(items, dict):
            items = [items]
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise ValueError("API 기사 목록은 객체 목록이어야 합니다")
    if code != "00":
        raise ValueError(f"API resultCode: {code}")
    if not isinstance(items, list) or any(not isinstance(x, dict) for x in items):
        raise ValueError("Unexpected article list structure")
    if total is None:
        raise ValueError("API response is missing totalCnt")
    return items, int(total)


# API 한 페이지를 조회하고 네트워크·일부 HTTP 오류 발생 시 재시도합니다.
def fetch_page(session, params, api_url=API_URL):
    for attempt in range(4):
        try:
            response = session.get(api_url, params=params, timeout=(10, 60))
        except requests.RequestException:
            if attempt == 3:
                raise RuntimeError("API network request failed (URL/key omitted)") from None
            time.sleep(2 ** attempt)
            continue
        if response.status_code == 429 or response.status_code >= 500:
            if attempt < 3:
                time.sleep(2 ** attempt)
                continue
        if response.status_code != 200:
            raise RuntimeError(f"API HTTP status: {response.status_code}")
        try:
            return parse_response(response.content)
        except (json.JSONDecodeError, ET.ParseError):
            raise RuntimeError("API response was not valid JSON/XML") from None


# 한글을 지원하는 UTF-8 BOM 형식으로 CSV를 저장합니다.
def write_csv(path, fields, rows):
    with Path(path).open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


# 인증키가 설정되었는지 확인하고 예시 값이나 공백 키를 거부합니다.
def read_key(env_path):
    key = dotenv_values(env_path).get("shortnews")
    if not key or not key.strip() or key.strip().startswith("YOUR_"):
        raise ValueError(".env에 실제 shortnews 인증키를 입력하세요")
    return key.strip()


# 기사 ID 중복과 전체 건수 변화를 확인하며 수집 결과와 완료 통계를 저장합니다.
def collect(args, directory, key=None):
    key = key or read_key(args.env)
    params = {"serviceKey": unquote(key), "type": args.format, "numOfRows": args.page_size}
    params["search1"] = args.title or ""
    params["search2"] = args.date or ""
    records, seen_ids, totals, warnings = [], set(), [], []
    received = duplicates = missing_ids = 0
    previous_page = None
    with requests.Session() as session:
        page = 1
        while True:
            params["pageNo"] = page
            items, total = fetch_page(session, params)
            totals.append(total)
            if total < 0:
                raise ValueError("API 전체 건수가 음수입니다")
            if total != totals[0] and "수집 중 API 전체 건수가 변경되었습니다" not in warnings:
                warnings.append("수집 중 API 전체 건수가 변경되었습니다")
            signature = hash_key(json.dumps(items, sort_keys=True, ensure_ascii=False))
            if items and signature == previous_page:
                warnings.append("같은 페이지가 반복되어 수집을 중단했습니다")
                break
            previous_page = signature
            received += len(items)
            for item in items:
                value = item.get("nttSn")
                article_id = "" if value is None else str(value).strip()
                if not article_id:
                    missing_ids += 1
                    records.append(item)
                elif article_id in seen_ids:
                    duplicates += 1
                else:
                    seen_ids.add(article_id)
                    records.append(item)
            print(f"페이지 {page}: 수신 {received}건, 고유 ID {len(seen_ids)}건 / 전체 {total}건")
            # 중복을 포함한 수신 행 수로 종료하지 않고 고유 기사 수를 확인합니다.
            if not items or len(seen_ids) >= max(totals):
                break
            if args.max_pages and page >= args.max_pages:
                break
            page += 1
            time.sleep(args.delay)
    complete = bool(totals) and len(set(totals)) == 1 and missing_ids == 0 and len(seen_ids) == totals[0] and not warnings
    if duplicates:
        warnings.append(f"기사 ID가 같은 중복 {duplicates}건을 제외했습니다")
    if missing_ids:
        warnings.append(f"기사 ID 없는 {missing_ids}건은 보존했지만 완료 판정에서 제외했습니다")
    if not complete:
        warnings.append("전체 수집을 확인하지 못했습니다")
    headers = list(dict.fromkeys([*RAW_FIELDS, *(k for item in records for k in item)]))
    rows = [{k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
             for k, v in item.items()} for item in records]
    write_csv(directory / "shortnews_raw.csv", headers, rows)
    (directory / "shortnews_raw.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    report = {"api_total": totals[-1] if totals else None,
              "api_total_initial": totals[0] if totals else None,
              "total_changed": len(set(totals)) > 1,
              "received": received, "collected": len(records),
              "unique_articles": len(seen_ids), "duplicates_removed": duplicates,
              "missing_ids": missing_ids, "collection_complete": complete, "warnings": warnings}
    (directory / "shortnews_collection.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for warning in warnings:
        print(f"경고: {warning}", file=sys.stderr)
    return directory / "shortnews_raw.csv"


# 실행 옵션을 확인하고 단신속보뉴스 원본을 새 폴더에 저장합니다.
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", type=Path, default=ENV_PATH)
    parser.add_argument("--format", choices=["json", "xml"], default="json")
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--max-pages", type=int, default=0, help="0이면 전체 페이지 수집")
    parser.add_argument("--title", help="단신속보뉴스 제목 검색")
    parser.add_argument("--date", help="단신속보뉴스 작성일 검색값")
    parser.add_argument("--delay", type=float, default=0.3)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    # NaN과 무한대는 대기 시간으로 사용할 수 없으므로 실행 전에 거부합니다.
    if not math.isfinite(args.delay) or args.delay < 0:
        parser.error("--delay는 0 이상의 유한한 숫자여야 합니다")
    if not 1 <= args.page_size <= 100 or args.max_pages < 0:
        parser.error("페이지 크기는 1~100, 최대 페이지와 대기 시간은 0 이상이어야 합니다")
    directory = args.output_dir or BASE / "shortnews_outputs" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    # 인증키와 기존 경로를 먼저 검증하여 실패 시 빈 결과 폴더를 만들지 않습니다.
    try:
        key = read_key(args.env)
    except ValueError as exc:
        parser.error(str(exc))
    if directory.exists():
        parser.error(f"출력 폴더가 이미 존재합니다. 새 경로를 지정하세요: {directory}")
    try:
        directory.mkdir(parents=True, exist_ok=False)
    except OSError:
        parser.error(f"출력 폴더를 생성할 수 없습니다: {directory}")
    raw_path = collect(args, directory, key)
    print(f"단신속보뉴스 원본 CSV·JSON 및 수집 통계 저장 완료: {raw_path.parent}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # 인증키가 포함될 수 있는 요청 객체와 URL은 출력하지 않습니다.
        if isinstance(exc, (RuntimeError, ValueError)):
            print(f"오류: {exc}", file=sys.stderr)
        else:
            print(f"수집 실패: {type(exc).__name__}", file=sys.stderr)
        sys.exit(1)
