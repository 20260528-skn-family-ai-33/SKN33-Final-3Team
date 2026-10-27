"""단신속보 원본을 공통 article 행으로 변환합니다. DB 연결·기사 선별은 하지 않습니다."""
import csv
import html
import json
import re
import sys
from datetime import date
from pathlib import Path

from backend.app.services.news_load import html_to_text, mask, plain_title
from backend.app.services.news_keywords.rule_data import GEO_DICT

SOURCE_CODE = "KOTRA_SHORT_NEWS"
RAW_COLUMNS = ("nttSn", "nttSj", "nttCn", "body", "othbcDt", "kotraNewsUrl", "nat")
REQUIRED_RAW_COLUMNS = ("nttSn", "othbcDt")
CLEAN_COLUMNS = ("nttSn", "kw_topic", "country_all")


def read_records(path, columns=RAW_COLUMNS):
    """수집기의 UTF-8 BOM CSV 또는 원본 객체 목록 JSON. ID의 앞자리 0을 유지합니다."""
    path = Path(path)
    required = REQUIRED_RAW_COLUMNS if columns == RAW_COLUMNS else columns
    with path.open(encoding="utf-8-sig", newline="") as stream:
        if path.suffix.lower() == ".json":
            rows = json.load(stream)
            if not isinstance(rows, list):
                raise ValueError("원본 JSON은 기사 객체 목록이어야 합니다")
        elif path.suffix.lower() == ".csv":
            csv.field_size_limit(2 ** 31 - 1 if sys.maxsize >= 2 ** 31 else sys.maxsize)
            reader = csv.DictReader(stream)
            if not set(required).issubset(reader.fieldnames or []):
                raise ValueError("CSV 필수 컬럼 누락: " + ", ".join(sorted(set(required) - set(reader.fieldnames or []))))
            rows = list(reader)
        else:
            raise ValueError("입력 파일은 .csv 또는 .json이어야 합니다")
    for number, row in enumerate(rows, 1):
        if not isinstance(row, dict) or not set(required).issubset(row):
            raise ValueError(f"입력 {number}번째 행의 객체 형식 또는 필수 컬럼 오류")
        for column in columns:
            if row.get(column) is None:
                row[column] = ""
    return rows


def text(value):
    return "" if value is None else str(value).strip()


def article_id(value):
    value = text(value)
    if not value or len(value) > 100 or not value.isascii():
        raise ValueError("nttSn은 비어 있지 않은 100자 이하 ASCII 기사 ID여야 합니다")
    return value


def published_date(value):
    value = text(value)
    if re.fullmatch(r"\d{8}", value):
        return date(int(value[:4]), int(value[4:6]), int(value[6:8]))
    # 기존 뉴스의 날짜/시간 형식과 ISO 날짜를 지원합니다. 발행일은 수집일로 대체하지 않습니다.
    return date.fromisoformat(value[:10])


def article_row(raw):
    # plain_title은 600자로 자르므로 정제 후 길이는 먼저 별도로 검증합니다.
    original_title = text(raw.get("nttSj"))
    normalized_title = re.sub(r"\s+", " ", html.unescape(html.unescape(original_title))).strip()
    if len(normalized_title) > 600:
        raise ValueError("nttSj은 600자를 초과할 수 없습니다")
    title, url = plain_title(original_title), text(raw.get("kotraNewsUrl"))
    # 팀원 적재기와 동일하게 이미 텍스트인 body를 우선하고, 없으면 nttCn HTML을 변환합니다.
    body = text(raw.get("body")) or html_to_text(raw.get("nttCn"))
    if not body:
        raise ValueError("본문(body 또는 nttCn)이 비어 있습니다. 제목으로 대체하지 않습니다")
    for name, value, limit in (("nttSj", title, 600), ("kotraNewsUrl", url, 2048)):
        if len(value) > limit:
            raise ValueError(f"{name}이 ERD 길이 {limit}자를 초과합니다")
    return {"source_code": SOURCE_CODE, "external_id": article_id(raw.get("nttSn")),
            "title": title, "published_date": published_date(raw.get("othbcDt")),
            "source_url": url, "body_masked": mask(body),
            "category": None}  # regn은 지역이고 기사 분야가 아니므로 category로 넣지 않습니다.


def country_lookup(rows):
    """실제 country의 코드/이름과 기존 지역 사전의 국가 별칭만 연결합니다 (부분 문자열 추정 없음)."""
    codes = {r["country_code"] for r in rows}
    lookup = {code.casefold(): code for code in codes}
    for row in rows:
        if row.get("name_ko"):
            lookup[text(row["name_ko"]).casefold()] = row["country_code"]
    for name, kind, code, *_ in GEO_DICT:
        if kind == "국가" and code in codes:
            lookup.setdefault(name.casefold(), code)
    return lookup


def country_codes(value, lookup):
    """원천 nat의 국가를 매핑합니다. 미매핑 값은 별도 보고하고 기사는 보존합니다."""
    codes, unknown = [], []
    for name in re.split(r"[|,;]", text(value)):
        name = name.strip()
        if not name:
            continue
        code = lookup.get(name.casefold())
        if code:
            if code not in codes:
                codes.append(code)
        else:
            unknown.append(name)
    return codes, unknown
