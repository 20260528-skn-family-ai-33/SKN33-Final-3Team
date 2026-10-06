"""KOTRA API를 수집해 원본 CSV, 전처리 CSV와 검토 파일을 저장합니다."""
import argparse
import csv
import hashlib
import json
import re
import sys
import time
import unicodedata
from collections import Counter
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote
import xml.etree.ElementTree as ET

import requests
from dotenv import dotenv_values

BASE = Path(__file__).resolve().parent
# 인증키는 저장소 루트의 .env에서 읽습니다.
ENV_PATH = Path(__file__).resolve().parents[5] / ".env"
API_URL = "https://apis.data.go.kr/B410001/kotra_overseasMarketNews/ovseaMrktNews/ovseaMrktNews"
MAP = {"original_id": "bbstxSn", "title": "newsTitl", "content": "newsBdt",
       "published_at": "othbcDt", "country": "natn", "url": "kotraNewsUrl",
       "summary": "cntntSumar", "keywords": "kwrd", "author": "newsWrterNm",
       "region": "regn", "industry": "indstCl", "copyright_type": "dataType"}
FIELDS = ["news_id", "source", *MAP, "identity_keys", "raw_json"]
csv.field_size_limit(min(sys.maxsize, 2147483647))


class TextParser(HTMLParser):
    # HTML에서 추출할 텍스트와 숨김 태그 상태를 초기화합니다.
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    # script·style 내용을 숨기고 문단 시작 부분에 공백을 넣습니다.
    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        elif not self.hidden and tag in {"p", "br", "div", "li", "td", "tr", "h1", "h2", "h3"}:
            self.parts.append(" ")

    # 숨김 태그의 종료를 처리하고 문단 사이를 공백으로 구분합니다.
    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        elif not self.hidden and tag in {"p", "div", "li", "td", "tr", "h1", "h2", "h3"}:
            self.parts.append(" ")

    # 숨김 태그 밖에 있는 텍스트만 추출합니다.
    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


# 결측값을 빈 문자열로 바꾸고 HTML과 불필요한 공백을 정리합니다.
def clean(value, html=False):
    text = "" if value is None else str(value)
    if html:
        parser = TextParser()
        parser.feed(text)
        parser.close()
        text = "".join(parser.parts)
    text = unicodedata.normalize("NFC", text)
    text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)
    return re.sub(r"\s+", " ", text).strip()


# 문자열을 SHA-256 해시로 바꾸어 식별 키를 만듭니다.
def hash_key(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# 기사 ID와 URL로 중복 식별 키를 만들고, 둘 다 없으면 기사 내용을 사용합니다.
def article_keys(row):
    keys = []
    if row["original_id"]:
        keys.append(hash_key("kotra_market:id:" + row["original_id"]))
    if row["url"]:
        keys.append(hash_key("kotra_market:url:" + row["url"]))
    if not keys:
        keys.append(hash_key("kotra_market:text:" + json.dumps(
            [row["title"], row["published_at"], row["content"]], ensure_ascii=False)))
    return keys


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
            {child.tag: child.text or "" for child in item}
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
def fetch_page(session, params):
    for attempt in range(4):
        try:
            response = session.get(API_URL, params=params, timeout=(10, 60))
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
    key = config.get("news")
    if not key:
        raise ValueError("The specified .env must contain news=YOUR_API_KEY")
    # 인코딩된 인증키를 한 번 해제하고 요청 시 requests가 다시 인코딩하게 합니다.
    params = {"serviceKey": unquote(key), "type": args.format,
              "numOfRows": args.page_size, "search8": "Y"}
    if args.start_date:
        params["search4"] = args.start_date
    if args.end_date:
        params["search7"] = args.end_date
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
    headers = list(dict.fromkeys([*MAP.values(), *(k for item in records for k in item)]))
    raw_rows = [{k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list))
                 else v for k, v in item.items()} for item in records]
    write_csv(directory / "news_raw.csv", headers, raw_rows)
    # 원본의 null과 자료형을 유지하기 위해 기사 객체를 JSON으로도 보존합니다.
    (directory / "news_raw.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    # 수집 통계는 별도로 보존하여 CSV 재전처리 시에도 수집 범위를 확인합니다.
    (directory / "news_collection.json").write_text(json.dumps({
        "api_total": total, "collected": len(records),
        "collection_complete": len(records) >= total,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return directory / "news_raw.csv"


# 저장된 원본 CSV를 읽고 필수 컬럼과 행 구조를 확인합니다.
def read_raw_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        missing = {MAP["title"], MAP["content"]} - set(reader.fieldnames or [])
        if missing:
            raise ValueError("원본 CSV에 필요한 컬럼이 없습니다: " + ", ".join(sorted(missing)))
        records = []
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"원본 CSV {reader.line_num}행의 구조가 잘못되었습니다")
            records.append(row)
    return records


# 결측값·HTML·중복을 처리하고 전처리 CSV, 행별 검토 내역과 통계를 저장합니다.
def preprocess(records, directory, total=None, collection=None):
    prepared, parents, owners = [], [], {}
    audit, missing_before, missing_after = [], Counter(), Counter()

    # 중복으로 연결된 기사들이 속한 대표 그룹을 찾습니다.
    def find(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i

    for number, raw in enumerate(records, 1):
        row = {field: clean(raw.get(api_field), html=field in {"content", "summary"})
               for field, api_field in MAP.items()}
        changes, missing = [], []
        for field, api_field in MAP.items():
            original = raw.get(api_field)
            if original is None or not str(original).strip():
                missing_before[field] += 1
            if not row[field]:
                missing_after[field] += 1
                missing.append(field)
            if ("" if original is None else str(original)) != row[field]:
                changes.append(field)
        status = "candidate"
        if not row["title"] and not row["content"]:
            status = "excluded_empty"
        audit.append({"raw_row": number, "original_id": row["original_id"],
                      "status": status, "missing_fields": ",".join(missing),
                      "changed_fields": ",".join(changes), "kept_raw_row": ""})
        if status == "excluded_empty":
            continue
        row["source"] = "kotra_market_news"
        keys = article_keys(row)
        i = len(prepared)
        prepared.append((row, raw, keys, number))
        parents.append(i)
        for key in keys:
            if key in owners:
                parents[find(owners[key])] = find(i)
            owners[key] = i

    groups = {}
    for i, item in enumerate(prepared):
        groups.setdefault(find(i), []).append(item)
    cleaned = []
    for members in groups.values():
        # 본문·제목 유무와 본문 길이로 대표 기사를 고르고, 동률이면 뒤의 행을 선택합니다.
        winner = max(members, key=lambda x: (bool(x[0]["content"]),
                     bool(x[0]["title"]), len(x[0]["content"]), x[3]))
        row = dict(winner[0])
        keys = sorted({key for item in members for key in item[2]})
        row["news_id"] = keys[0]
        row["identity_keys"] = json.dumps(keys)
        row["raw_json"] = json.dumps([item[1] for item in members], ensure_ascii=False)
        cleaned.append(row)
        for item in members:
            entry = audit[item[3] - 1]
            entry["status"] = "kept" if item[3] == winner[3] else "excluded_duplicate"
            entry["kept_raw_row"] = winner[3]
    write_csv(directory / "news_clean.csv", FIELDS, cleaned)
    write_csv(directory / "news_review.csv", ["raw_row", "original_id", "status",
              "missing_fields", "changed_fields", "kept_raw_row"], audit)
    report = {
        "api_total_at_last_page": total, "collected": len(records),
        "collection_complete": collection.get("collection_complete") if collection else None,
        "api_collected": collection.get("collected") if collection else None,
        "retained": len(cleaned),
        "excluded_empty": sum(x["status"] == "excluded_empty" for x in audit),
        "excluded_duplicate": sum(x["status"] == "excluded_duplicate" for x in audit),
        "missing_before": {k: missing_before[k] for k in MAP},
        "missing_after_before_dedup": {k: missing_after[k] for k in MAP},
        "missing_in_clean_csv": {k: sum(not r[k] for r in cleaned) for k in MAP},
        "policy": "원본 CSV를 읽어 전처리합니다. 결측값은 유지하고 제목과 본문이 모두 빈 행만 제외합니다. 날짜는 원문을 유지합니다."
    }
    (directory / "news_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


# 실행 옵션에 따라 원본 CSV 수집 또는 저장된 CSV의 전처리를 수행합니다.
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", type=Path, default=ENV_PATH)
    parser.add_argument("--format", choices=["json", "xml"], default="json")
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--max-pages", type=int, default=0, help="0 = all pages")
    parser.add_argument("--start-date", help="YYYYMMDD")
    parser.add_argument("--end-date", help="YYYYMMDD; requires --start-date")
    parser.add_argument("--delay", type=float, default=0.3)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--raw-csv", type=Path, help="API 호출 없이 기존 원본 CSV를 전처리")
    parser.add_argument("--collect-only", action="store_true", help="원본만 저장하고 전처리는 별도로 실행")
    args = parser.parse_args()
    if args.raw_csv and args.collect_only:
        parser.error("--raw-csv와 --collect-only는 함께 사용할 수 없습니다")
    if args.raw_csv and (args.start_date or args.end_date or args.max_pages):
        parser.error("--raw-csv 모드에서는 API 수집 옵션을 사용하지 마세요")
    if not 1 <= args.page_size <= 100 or args.max_pages < 0 or args.delay < 0:
        parser.error("Invalid page-size, max-pages or delay")
    if args.end_date and not args.start_date:
        parser.error("--end-date requires --start-date")
    for value in (args.start_date, args.end_date):
        if value:
            if not re.fullmatch(r"\d{8}", value):
                parser.error("Dates must be YYYYMMDD")
            datetime.strptime(value, "%Y%m%d")
    if args.start_date and args.end_date and args.start_date > args.end_date:
        parser.error("Start date must be before end date")
    directory = args.output_dir or BASE / "news_outputs" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    directory.mkdir(parents=True, exist_ok=False)
    if args.raw_csv:
        raw_path = args.raw_csv.resolve()
    else:
        raw_path = collect(args, directory)
        if args.collect_only:
            print(f"원본 CSV 저장 완료. 확인 후 --raw-csv로 전처리하세요: {raw_path}")
            return
    # 전처리 입력은 API 메모리 데이터가 아니라 실제로 저장된 CSV입니다.
    records = read_raw_csv(raw_path)
    metadata_path = raw_path.parent / "news_collection.json"
    collection = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else None
    total = collection.get("api_total") if collection else None
    preprocess(records, directory, total, collection)
    print(f"Review CSVs before loading MySQL. Output directory: {directory}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # 인증키가 노출되지 않도록 요청 객체와 요청 URL을 출력하지 않습니다.
        if isinstance(exc, (RuntimeError, ValueError)):
            print(f"Error: {exc}", file=sys.stderr)
        else:
            print(f"Error: {type(exc).__name__}; collection did not complete", file=sys.stderr)
        sys.exit(1)
