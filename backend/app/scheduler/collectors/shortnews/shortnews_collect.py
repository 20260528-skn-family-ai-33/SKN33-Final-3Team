"""단신속보뉴스 API의 원본 CSV·JSON과 수집 통계만 저장합니다."""
import argparse
import csv
import hashlib
import json
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
    if payload.lstrip().startswith(b"<"):
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
        listing = body.get("itemList") or {}
        items = listing.get("item", []) if isinstance(listing, dict) else []
        if isinstance(items, dict):
            items = [items]
        total = body.get("totalCnt")
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


# .env 인증키로 기사를 페이지별 수집하고 원본 CSV와 JSON을 저장합니다.
def collect(args, directory):
    config = dotenv_values(args.env)
    source = "shortnews"
    key = config.get(source)
    if not key:
        raise ValueError(f".env에 {source}=인증키 항목이 필요합니다")
    # 인코딩된 인증키를 한 번 해제하고 요청 시 requests가 다시 인코딩하게 합니다.
    params = {"serviceKey": unquote(key), "type": args.format,
              "numOfRows": args.page_size}
    # 제목과 작성일 검색값이 없으면 빈 조건으로 전체 목록을 요청합니다.
    params["search1"] = args.title or ""
    params["search2"] = args.date or ""
    records = []
    total = 0
    previous_page = None
    with requests.Session() as session:
        page = 1
        while True:
            params["pageNo"] = page
            items, total = fetch_page(session, params)
            signature = hash_key(json.dumps(items, sort_keys=True, ensure_ascii=False))
            if items and signature == previous_page:
                raise ValueError("API repeated the previous page; collection stopped")
            previous_page = signature
            if not items and len(records) < total:
                raise ValueError("API returned an empty page before totalCnt was reached")
            records.extend(items)
            print(f"Page {page}: {len(items)} articles; collected {len(records)}/{total}")
            if len(records) >= total or not items:
                break
            if args.max_pages and page >= args.max_pages:
                break
            page += 1
            time.sleep(args.delay)
    headers = list(dict.fromkeys([*RAW_FIELDS, *(k for item in records for k in item)]))
    raw_rows = [{k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list))
                 else v for k, v in item.items()} for item in records]
    write_csv(directory / f"{source}_raw.csv", headers, raw_rows)
    # 원본의 null과 자료형을 유지하기 위해 기사 객체를 JSON으로도 보존합니다.
    (directory / f"{source}_raw.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    # 전체 건수와 수집 건수, 전체 수집 여부를 별도로 저장합니다.
    (directory / f"{source}_collection.json").write_text(json.dumps({
        "source": source,
        "api_total": total, "collected": len(records),
        "collection_complete": len(records) >= total,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return directory / f"{source}_raw.csv"


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
    if not 1 <= args.page_size <= 100 or args.max_pages < 0 or args.delay < 0:
        parser.error("페이지 크기는 1~100, 최대 페이지와 대기 시간은 0 이상이어야 합니다")
    directory = args.output_dir or BASE / "shortnews_outputs" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    directory.mkdir(parents=True, exist_ok=False)
    raw_path = collect(args, directory)
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
