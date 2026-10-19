"""공용 옵션 manifest 검증·보존 적재. DB 생성, 배치 기록, 수집·재계산은 하지 않는다.

커밋 결과와 정리 오류를 구분하기 위해 세션 종료를 직접 관리한다. 연결과 쓰기는
기존 db.session의 mysql_url/make_engine 및 CRUD(ServiceOption)를 사용한다.
"""
import hashlib
import json
import math
import os
import re
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import bindparam, inspect, or_, select, text
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Session

from backend.app.db.crud import CRUD
from backend.app.db.session import ENV_PATH, make_engine, mysql_url
from backend.app.models.batch import OPTION_TYPES, TARGET_TABLES, ServiceOption

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SEED = ROOT / "backend/app/db/seeds/service_option.json"
DEFAULT_OUTPUT = ROOT / "data/service_option_load_outputs"
KST = timezone(timedelta(hours=9), "Asia/Seoul")
FIELDS = ("option_code", "option_type", "label_ko", "value_json", "sort_order", "enabled")
BASE_CODES = frozenset(("CUSTOMS", "KCS_HS_NAMES", "UNSD_HS_CORR", "WEEKLY", "MONTHLY",
                       "QUARTERLY", "RPT_WEEKLY", "RPT_MONTHLY", "RPT_QUARTERLY",
                       "RPT_ON_DEMAND", "CUSTOMS_ANOMALY", "INTEREST_SCOPE"))
CONDITIONAL_CODES = frozenset(("UN_COMTRADE_SOURCE", "KOTRA_MARKET_NEWS", "KOTRA_SHORT_NEWS",
                              "ECOS_FX", "TRADE_CHANGE_RULE", "NEWS_SPIKE_RULE"))
FK_TYPES = {("batch_run", "source_code"): "SOURCE", ("article", "source_code"): "SOURCE",
            ("report_subscription", "period_code"): "REPORT_PERIOD",
            ("report", "report_type"): "REPORT_TYPE",
            ("user_consent", "terms_version"): "NOTIFICATION_TERMS"}
TRANSIENT_DB_CODES = {1040, 1042, 1047, 1158, 1159, 1160, 1161, 1205, 1213,
                      2002, 2003, 2006, 2013}
SECRET_KEYS = {"key", "apikey", "servicekey", "accesskey", "secret", "token", "accesstoken",
               "refreshtoken", "password", "passwd", "authorization", "credential", "signature"}
# 설정 등록용 계약이다. 문자열로 함수를 import/실행하거나 스케줄러를 등록하지 않는다.
# 가이드 및 기존 처리기의 작업별 쓰기 범위를 명시한다. 새 작업은 이 목록과 소비 코드의
# 대응을 검토한 뒤 추가한다. 등록 가능하다는 사실이 실행기 연동 완료를 뜻하지 않는다.
@dataclass(frozen=True)
class JobTargetContract:
    required: frozenset
    optional: frozenset = frozenset()


JOB_TARGETS = {
    "TRADE_COLLECTION": JobTargetContract(frozenset(("export_monthly", "import_monthly"))),
    "CUSTOMS_CSV_LOAD": JobTargetContract(frozenset(("export_monthly", "import_monthly"))),
    "HS6_AGGREGATION": JobTargetContract(frozenset(("export_summary", "import_summary"))),
    "CUSTOMS_METRICS": JobTargetContract(frozenset(("export_monthly", "import_monthly"))),
    "NEWS_COLLECTION": JobTargetContract(frozenset(("article",)), frozenset(("article_tag",))),
    "ARTICLE_TAGGING": JobTargetContract(frozenset(("article_tag",))),
    "NEWS_DAILY_AGGREGATION": JobTargetContract(frozenset(("news_product_daily", "news_keyword_daily"))),
    "FX_COLLECTION": JobTargetContract(frozenset(("fx_daily",))),
    "HS_CODE_LOAD": JobTargetContract(frozenset(("hs_code",))),
    "HS_CODE_REFRESH": JobTargetContract(frozenset(("hs_code",)), frozenset(("hs_code_correlation",))),
    "HS_CORRELATION_LOAD": JobTargetContract(frozenset(("hs_code_correlation",))),
    "UN_FILE_IMPORT": JobTargetContract(frozenset(("un_data",))),
    "UN_DATA_LOAD": JobTargetContract(frozenset(("un_data",))),
    "UN_DATA_REFRESH": JobTargetContract(frozenset(("un_data",))),
}
CONFIG_TARGET_TABLES = frozenset(TARGET_TABLES) | {"un_data"}
DATA_PERIODS = frozenset(("DAILY", "MONTHLY", "ANNUAL", "MONTHLY_AND_ANNUAL", "ARTICLE"))
SOURCE_METADATA = ("license", "daily_limit", "data_period", "source_update_frequency", "jobs")


class ContractError(ValueError):
    """본문에 사용자 값이나 접속 정보를 넣지 않는 안전한 계약 오류."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def require(condition, code, message):
    if not condition:
        raise ContractError(code, message)


def now():
    return datetime.now(KST).isoformat(timespec="microseconds")


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "DUPLICATE_JSON_KEY", "JSON 객체에 중복 키가 있습니다.")
        result[key] = value
    return result


def _constant(_value):
    raise ContractError("INVALID_JSON_NUMBER", "NaN과 Infinity는 허용하지 않습니다.")


def read_json(path, limit=5 * 1024 * 1024):
    # stat 결과만 믿지 않고 실제 읽기도 제한한다. plan에도 같은 한도를 적용한다.
    with Path(path).open("rb") as stream:
        raw = stream.read(limit + 1)
    require(len(raw) <= limit, "FILE_TOO_LARGE", "JSON 파일 크기 제한을 초과했습니다.")
    try:
        value = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_pairs, parse_constant=_constant)
    except ContractError:
        raise
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ContractError("INVALID_JSON", "UTF-8 JSON 형식이 올바르지 않습니다.") from exc
    return value, hashlib.sha256(raw).hexdigest()


def semantic(value):
    """bool과 수를 구분하고 3 == 3.0인, 해시에도 사용 가능한 정규 표현."""
    if value is None:
        return ["null"]
    if type(value) is bool:
        return ["bool", value]
    if type(value) in (int, float):
        require(type(value) is int or math.isfinite(value), "INVALID_JSON_NUMBER", "유한수가 필요합니다.")
        number = Decimal(str(value))
        digits = format(number, "f")
        if "." in digits:
            digits = digits.rstrip("0").rstrip(".")
        return ["number", digits if number else "0"]
    if isinstance(value, str):
        return ["str", value]
    if isinstance(value, list):
        return ["list", [semantic(v) for v in value]]
    if isinstance(value, dict):
        return ["object", [[k, semantic(value[k])] for k in sorted(value)]]
    raise ContractError("INVALID_VALUE", "지원하지 않는 JSON 값입니다.")


def same(left, right):
    return semantic(left) == semantic(right)


def state_hash(value):
    return hashlib.sha256(json.dumps(semantic(value), ensure_ascii=False,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def _keys(value, required, optional=()):
    require(isinstance(value, dict) and set(required) <= value.keys()
            and not value.keys() - set(required) - set(optional),
            "INVALID_FIELDS", "JSON 객체의 필수 필드 또는 허용 필드를 확인하세요.")


def _string(value):
    require(isinstance(value, str) and bool(value.strip()) and value == value.strip(),
            "INVALID_STRING", "문자열은 비어 있거나 앞뒤 공백을 포함할 수 없습니다.")


def _integer(value, minimum, maximum=None):
    require(type(value) is int and value >= minimum and (maximum is None or value <= maximum),
            "INVALID_INTEGER", "정수 타입과 허용 범위를 확인하세요.")


def _number(value, minimum, maximum=None, exclusive=False):
    require(type(value) in (int, float) and (type(value) is int or math.isfinite(value))
            and (value > minimum if exclusive else value >= minimum)
            and (maximum is None or value <= maximum),
            "INVALID_NUMBER", "숫자 타입과 허용 범위를 확인하세요.")


def _no_secrets(value):
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = re.sub(r"[^a-z]", "", key.lower())
            require(normalized not in SECRET_KEYS, "SECRET_IN_MANIFEST", "인증정보는 환경변수로 관리하세요.")
            _no_secrets(child)
    elif isinstance(value, list):
        for child in value:
            _no_secrets(child)


def _url(value):
    _string(value)
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme in ("http", "https") and bool(parsed.hostname)
                 and parsed.username is None and parsed.password is None
                 and not any(ord(c) < 33 for c in value))
        parsed.port  # 잘못된 포트도 거부한다.
        sensitive = any(any(secret in re.sub(r"[^a-z]", "", key.lower()) for secret in SECRET_KEYS)
                        for key, _ in parse_qsl(parsed.query))
    except ValueError as exc:
        raise ContractError("INVALID_URL", "출처 URL 형식이 올바르지 않습니다.") from exc
    require(valid and not sensitive and not parsed.fragment, "INVALID_URL",
            "출처는 인증정보가 없는 http/https URL이어야 합니다.")


def _timezone(value):
    _string(value)
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ContractError("INVALID_TIMEZONE", "등록된 IANA 시간대를 사용하세요.") from exc


def _time(value):
    require(isinstance(value, str) and re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value),
            "INVALID_SCHEDULE", "시각은 유효한 HH:MM이어야 합니다.")


def _date(value):
    require(isinstance(value, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value),
            "INVALID_COLLECTION_SCOPE", "수집 기간은 YYYY-MM-DD 형식이어야 합니다.")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ContractError("INVALID_COLLECTION_SCOPE", "유효하지 않은 수집 날짜입니다.") from exc


def _strings(value, code):
    require(isinstance(value, list) and bool(value), code, "비어 있지 않은 문자열 배열이 필요합니다.")
    for item in value:
        _string(item)
    require(len(value) == len(set(value)), code, "문자열 배열에 중복 값이 있습니다.")


def validate_schedule(schedule):
    require(isinstance(schedule, dict), "INVALID_SCHEDULE", "schedule은 객체여야 합니다.")
    kind = schedule.get("type")
    if kind == "DAILY":
        _keys(schedule, ("type", "time", "timezone"))
        _time(schedule["time"])
        _timezone(schedule["timezone"])
    elif kind == "INTERVAL":
        _keys(schedule, ("type", "every_minutes"))
        _integer(schedule["every_minutes"], 1)
    elif kind == "MANUAL":
        # UN 통합 DB설계서의 MANUAL + timezone 형식도 보존한다.
        _keys(schedule, ("type",), ("timezone",))
        if "timezone" in schedule:
            _timezone(schedule["timezone"])
    elif kind == "AFTER_JOB":
        _keys(schedule, ("type", "after_job_name"))
        _string(schedule["after_job_name"])
    else:
        raise ContractError("INVALID_SCHEDULE", "DAILY/INTERVAL/MANUAL/AFTER_JOB 중 하나가 필요합니다.")


def validate_scope(scope, schedule):
    require(isinstance(scope, dict), "INVALID_COLLECTION_SCOPE", "collection_scope는 객체여야 합니다.")
    mode = scope.get("mode")
    if mode == "RECENT_MONTHS":
        _keys(scope, ("mode", "anchor", "months"))
        require(scope["anchor"] == "LATEST_AVAILABLE_MONTH", "INVALID_COLLECTION_SCOPE",
                "최근 월의 기준은 실제 공개가 확인된 LATEST_AVAILABLE_MONTH여야 합니다.")
        _integer(scope["months"], 1)
    elif mode == "RECENT_DAYS":
        _keys(scope, ("mode", "days"), ("timezone",))
        _integer(scope["days"], 1)
        _timezone(scope.get("timezone", schedule.get("timezone")))
    elif mode == "EXPLICIT_PERIOD":
        _keys(scope, ("mode", "start", "end"))
        require(_date(scope["start"]) <= _date(scope["end"]), "INVALID_COLLECTION_SCOPE",
                "수집 시작일은 종료일보다 늦을 수 없습니다.")
    else:
        raise ContractError("INVALID_COLLECTION_SCOPE", "RECENT_MONTHS/RECENT_DAYS/EXPLICIT_PERIOD 중 하나가 필요합니다.")


def validate_source(value):
    # 기존 URL-only 설정도 보존·재입력 가능하다. 미구성 메타데이터는 readiness로 구분한다.
    _keys(value, ("url",), (*SOURCE_METADATA, "provider", "format", "dataset_id", "basis"))
    _url(value["url"])
    for key in ("provider", "format", "dataset_id", "basis"):
        if key in value:
            _string(value[key])
    for key in ("license", "source_update_frequency"):
        if key in value and value[key] is not None:
            _string(value[key])
    if "daily_limit" in value and value["daily_limit"] is not None:
        _integer(value["daily_limit"], 1)
    if "data_period" in value and value["data_period"] is not None:
        require(isinstance(value["data_period"], str) and value["data_period"] in DATA_PERIODS,
                "INVALID_DATA_PERIOD", "지원하는 자료 단위를 확인하세요.")
    if "jobs" not in value:
        return
    jobs = value["jobs"]
    require(isinstance(jobs, list) and len(jobs) <= 1000, "INVALID_JOBS", "jobs는 최대 1000개 작업의 배열이어야 합니다.")
    # []는 미구성 상태이며 실행 가능한 설정으로 보고하지 않는다. 예시 일정은 심지 않는다.
    if not jobs:
        return
    require(value.get("data_period") in DATA_PERIODS and "source_update_frequency" in value,
            "INCOMPLETE_SOURCE_METADATA", "작업 등록 시 자료 단위와 원천 갱신 주기(null 가능)를 명시하세요.")
    by_name = {}
    for job in jobs:
        _keys(job, ("job_name", "schedule", "collects", "collection_scope", "target_tables"))
        name = job["job_name"]
        require(isinstance(name, str) and re.fullmatch(r"[A-Z0-9_]{1,60}", name),
                "INVALID_JOB_NAME", "작업 코드는 대문자·숫자·밑줄 1~60자여야 합니다.")
        require(name not in by_name, "DUPLICATE_JOB_NAME", "같은 출처 안의 job_name이 중복됩니다.")
        require(name in JOB_TARGETS, "UNKNOWN_JOB", "처리 대상 계약이 등록되지 않은 작업입니다.")
        by_name[name] = job
        validate_schedule(job["schedule"])
        _strings(job["collects"], "INVALID_COLLECTS")
        validate_scope(job["collection_scope"], job["schedule"])
        _strings(job["target_tables"], "INVALID_TARGET_TABLES")
        targets = set(job["target_tables"])
        contract = JOB_TARGETS[name]
        require(contract.required <= targets <= (contract.required | contract.optional)
                and targets <= CONFIG_TARGET_TABLES,
                "JOB_TARGET_MISMATCH", "작업의 필수 대상이 누락되었거나 허용하지 않은 대상이 있습니다.")
    # 각 작업은 선행 작업을 최대 하나 참조한다. 재귀 대신 위상 정렬로 긴 연쇄도 검사한다.
    children = {name: [] for name in by_name}
    incoming = {name: 0 for name in by_name}
    for name, job in by_name.items():
        schedule = job["schedule"]
        if schedule["type"] == "AFTER_JOB":
            parent = schedule["after_job_name"]
            require(parent in by_name, "MISSING_PREDECESSOR", "같은 출처에 선행 작업이 없습니다.")
            children[parent].append(name)
            incoming[name] += 1
    ready = [name for name, count in incoming.items() if count == 0]
    visited = 0
    while ready:
        name = ready.pop()
        visited += 1
        for child in children[name]:
            incoming[child] -= 1
            if incoming[child] == 0:
                ready.append(child)
    require(visited == len(by_name), "CYCLIC_JOBS", "작업에 자기 참조 또는 순환 의존성이 있습니다.")


def validate_value(row):
    code, kind, value = row["option_code"], row["option_type"], row["value_json"]
    require(isinstance(value, dict), "INVALID_VALUE_JSON", "value_json은 객체여야 합니다.")
    semantic(value)  # 1e999와 중첩된 비유한수도 거부한다.
    _no_secrets(value)
    if kind == "SOURCE":
        validate_source(value)
    elif kind == "RULE":
        if code == "CUSTOMS_ANOMALY":
            _keys(value, (), ("r1", "r2", "r3"))
            require(bool(value), "INVALID_RULE", "하나 이상의 수출입 규칙이 필요합니다.")
            if "r1" in value:
                _keys(value["r1"], ("spike_ratio", "return_pct"))
                _number(value["r1"]["spike_ratio"], 1, exclusive=True)
                _number(value["r1"]["return_pct"], 0, 100)
            if "r2" in value:
                _keys(value["r2"], ("hs_prefixes",))
                prefixes = value["r2"]["hs_prefixes"]
                require(isinstance(prefixes, list) and bool(prefixes) and all(
                    isinstance(p, str) and re.fullmatch(r"(?:[0-9]{2}|[0-9]{4}|[0-9]{6}|[0-9]{10})", p)
                    for p in prefixes), "INVALID_HS_PREFIX", "HS 접두사는 2·4·6·10자리 ASCII 숫자 문자열이어야 합니다.")
                require(len(prefixes) == len(set(prefixes)), "DUPLICATE_HS_PREFIX", "HS 접두사가 중복됩니다.")
            if "r3" in value:
                _keys(value["r3"], ("unit_ratio",))
                _number(value["r3"]["unit_ratio"], 1, exclusive=True)
            # 기존 계산기의 계약을 최종 확인한다. configure_rules는 별도 커밋하므로 호출하지 않는다.
            from backend.app.services.customs_metrics import validate_rules
            validate_rules(value)
        elif code == "INTEREST_SCOPE":
            _keys(value, ("include_linked_countries", "top_country_count", "lookback_months"))
            require(type(value["include_linked_countries"]) is bool, "INVALID_BOOLEAN", "JSON bool이 필요합니다.")
            _integer(value["top_country_count"], 1)
            _integer(value["lookback_months"], 1)
        elif code == "TRADE_CHANGE_RULE":
            _keys(value, ("size_min_usd", "growth_min_pct", "growth_delta_usd", "drop_max_pct",
                          "drop_delta_usd", "large_growth_min_pct"))
            for key in ("size_min_usd", "growth_min_pct", "growth_delta_usd", "large_growth_min_pct"):
                _number(value[key], 0, exclusive=True)
            _number(value["drop_max_pct"], -100, 0)
            _number(value["drop_delta_usd"], -math.inf, 0)
            require(value["drop_max_pct"] < 0 and value["drop_delta_usd"] < 0
                    and value["large_growth_min_pct"] >= value["growth_min_pct"],
                    "INVALID_TRADE_RULE", "급감 기준은 음수이고 대폭 증가 기준은 급증 기준 이상이어야 합니다.")
        elif code == "NEWS_SPIKE_RULE":
            _keys(value, ("interest_ratio", "major_ratio", "zero_denominator"))
            for key in value:
                _number(value[key], 0, exclusive=True)
        else:
            raise ContractError("UNKNOWN_RULE", "검증 계약이 등록되지 않은 RULE 코드입니다.")
    elif kind == "REPORT_PERIOD":
        if not value:
            require(row["enabled"] is False, "ACTIVE_PERIOD_NOT_CONFIGURED",
                    "활성 발행 주기는 필수 설정이 필요합니다. 일정 미확정 옵션은 비활성으로 등록하세요.")
            return
        if code == "WEEKLY":
            _keys(value, ("trigger", "weekday", "time", "timezone", "news_only", "report_type_code"))
            _integer(value["weekday"], 1, 7)
            _time(value["time"])
            require(value["trigger"] == "WEEKLY_SCHEDULE" and value["timezone"] == "Asia/Seoul"
                    and value["news_only"] is True and value["report_type_code"] == "RPT_WEEKLY",
                    "INVALID_SCHEDULE", "주간 발행 설정 계약을 확인하세요.")
        elif code in ("MONTHLY", "QUARTERLY"):
            _keys(value, ("trigger", "news_only", "report_type_code"))
            trigger = "VALIDATED_MONTH_STATS" if code == "MONTHLY" else "VALIDATED_QUARTER_STATS"
            require(value["trigger"] == trigger and value["news_only"] is False
                    and value["report_type_code"] == "RPT_" + code, "INVALID_SCHEDULE", "통계 발행 설정 계약을 확인하세요.")
        else:
            raise ContractError("UNCONFIRMED_SCHEDULE", "확정되지 않은 발행 설정입니다. 단순 목록은 빈 객체를 사용하세요.")
    elif kind == "REPORT_TYPE":
        require(code.startswith("RPT_") and not value, "INVALID_REPORT_TYPE", "리포트 종류는 RPT_ 코드와 빈 객체를 사용하세요.")
    elif kind == "NOTIFICATION_TERMS":
        _keys(value, ("body", "effective_at"))
        require(code.startswith("TERMS_"), "INVALID_TERMS_CODE", "약관은 TERMS_ 버전 코드가 필요합니다.")
        require(isinstance(value["body"], str) and bool(value["body"].strip()),
                "INVALID_TERMS_BODY", "비어 있지 않은 실제 약관 본문이 필요합니다.")
        _terms_time(value["effective_at"])


def _terms_time(value):
    require(isinstance(value, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}", value),
            "INVALID_TERMS_TIME", "약관 적용 시각은 YYYY-MM-DD HH:MM:SS이어야 합니다.")
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
    except ValueError as exc:
        raise ContractError("INVALID_TERMS_TIME", "유효하지 않은 약관 적용 시각입니다.") from exc


def validate_record(row):
    _keys(row, FIELDS)
    require(isinstance(row["option_code"], str) and re.fullmatch(r"[A-Z0-9_]{1,40}", row["option_code"]),
            "INVALID_CODE", "옵션 코드는 대문자·숫자·밑줄 1~40자여야 합니다.")
    require(isinstance(row["option_type"], str) and row["option_type"] in OPTION_TYPES,
            "INVALID_OPTION_TYPE", "지원하지 않는 옵션 종류입니다.")
    _string(row["label_ko"])
    require(len(row["label_ko"]) <= 120, "INVALID_LABEL", "표시명은 120자 이하여야 합니다.")
    _integer(row["sort_order"], 0, 65535)
    require(type(row["enabled"]) is bool, "INVALID_BOOLEAN", "enabled는 JSON true/false여야 합니다.")
    validate_value(row)


def validate_relations(rows, *, external=False, relevant=None):
    times = set()
    for row in rows.values():
        code, kind, value = row["option_code"], row["option_type"], row["value_json"]
        if kind == "NOTIFICATION_TERMS" and row["enabled"]:
            require(isinstance(value, dict), "INVALID_VALUE_JSON", "약관 JSON 객체가 필요합니다.")
            instant = _terms_time(value.get("effective_at"))
            require(instant not in times, "AMBIGUOUS_TERMS", "동일 적용 시각의 활성 약관이 두 개 이상입니다.")
            times.add(instant)
        # 기본 보존으로 남는 기존 활성 빈 설정도 사후 기대 상태에서 거부한다.
        # 무관한 기존 옵션까지 수정 대상으로 삼지는 않는다.
        parent = value.get("report_type_code") if isinstance(value, dict) else None
        in_scope = relevant is None or code in relevant or (isinstance(parent, str) and parent in relevant)
        if kind == "REPORT_PERIOD" and row["enabled"] and in_scope:
            validate_value(row)
        if kind != "REPORT_PERIOD" or not isinstance(value, dict) or "report_type_code" not in value:
            continue
        parent = value["report_type_code"]
        require(isinstance(parent, str), "INVALID_REFERENCE", "리포트 참조 코드는 문자열이어야 합니다.")
        if relevant is not None and code not in relevant and parent not in relevant:
            continue
        if parent not in rows and not external:
            continue  # DB 외부 참조는 check/load에서 검사한다.
        target = rows.get(parent)
        require(target is not None and target["option_type"] == "REPORT_TYPE",
                "INVALID_REFERENCE", "참조 리포트 종류가 없거나 종류가 다릅니다.")
        require(not row["enabled"] or target["enabled"], "DISABLED_REFERENCE", "활성 주기가 비활성 리포트 종류를 참조합니다.")


@dataclass(frozen=True)
class Manifest:
    path: Path
    sha256: str
    version: str
    records: list


def read_manifest(path=DEFAULT_SEED):
    path = Path(path).resolve()
    obj, digest = read_json(path)
    _keys(obj, ("schema_version", "seed_version", "records", "provenance"))
    require(type(obj["schema_version"]) is int and obj["schema_version"] == 1,
            "UNSUPPORTED_SEED_VERSION", "지원하는 manifest schema_version은 1입니다.")
    _string(obj["seed_version"])
    rows = obj["records"]
    require(isinstance(rows, list) and 1 <= len(rows) <= 1000, "INVALID_RECORD_COUNT", "옵션은 1~1000개여야 합니다.")
    codes = set()
    for row in rows:
        validate_record(row)
        require(row["option_code"] not in codes, "DUPLICATE_CODE", "동일한 옵션 코드가 중복됩니다.")
        codes.add(row["option_code"])
    require(isinstance(obj["provenance"], dict) and set(obj["provenance"]) == codes,
            "INVALID_PROVENANCE", "모든 입력 코드의 근거가 정확히 하나씩 필요합니다.")
    for evidence in obj["provenance"].values():
        require(isinstance(evidence, dict) and bool(evidence), "INVALID_PROVENANCE", "근거는 비어 있지 않은 객체여야 합니다.")
    _no_secrets(obj["provenance"])
    if path == DEFAULT_SEED.resolve():
        require(codes == BASE_CODES, "INVALID_BASE_SEED", "기본 파일에는 초기 12개 코드가 정확히 필요합니다.")
    validate_relations({r["option_code"]: r for r in rows})
    return Manifest(path, digest, obj["seed_version"], rows)


def build_plan(seed, existing, target_db):
    rows = []
    for desired in sorted(seed.records, key=lambda r: r["option_code"]):
        code = desired["option_code"]
        before = existing.get(code)
        if before is not None:
            require(before["option_type"] == desired["option_type"], "TYPE_CONFLICT", "기존 옵션의 종류를 변경할 수 없습니다.")
            if desired["option_type"] == "NOTIFICATION_TERMS":
                require(same(before["value_json"], desired["value_json"]), "IMMUTABLE_TERMS_CONFLICT",
                        "기존 약관 본문·적용 시각은 변경할 수 없습니다. 새 버전을 등록하세요.")
        differences = {f: {"before": before[f] if before else None, "desired": desired[f]}
                       for f in FIELDS if before is None or not same(before[f], desired[f])}
        action = "INSERT" if before is None else "PRESERVE" if differences else "UNCHANGED"
        rows.append({"option_code": code, "before": deepcopy(before), "desired": deepcopy(desired),
                     "differences": differences, "before_hash": state_hash(before),
                     "proposed_action": action, "apply": False})
    return {"plan_schema_version": 1, "seed_sha256": seed.sha256, "seed_version": seed.version,
            "target_db": target_db, "generated_at": now(), "records": rows}


def approve_plan(plan, supplied):
    """기존 차이 행의 apply만 변경 가능하며 나머지는 현재 상태에서 재생성해 대조한다."""
    _keys(supplied, plan.keys())
    require(type(supplied["plan_schema_version"]) is int, "INVALID_PLAN", "변경 계획 버전이 올바르지 않습니다.")
    for key in ("plan_schema_version", "seed_sha256", "seed_version", "target_db"):
        require(same(plan[key], supplied[key]), "STALE_PLAN", "입력 또는 대상 DB가 바뀌었습니다. check를 다시 실행하세요.")
    require(isinstance(supplied["generated_at"], str), "INVALID_PLAN", "변경 계획 생성 시각이 필요합니다.")
    proposed = supplied["records"]
    require(isinstance(proposed, list) and len(proposed) == len(plan["records"]), "INVALID_PLAN", "변경 계획의 코드 집합이 다릅니다.")
    by_code = {}
    for row in proposed:
        _keys(row, plan["records"][0].keys())
        code = row["option_code"]
        require(isinstance(code, str) and code not in by_code, "INVALID_PLAN", "변경 계획에 잘못되거나 중복된 코드가 있습니다.")
        by_code[code] = row
    require(set(by_code) == {r["option_code"] for r in plan["records"]}, "INVALID_PLAN", "변경 계획의 코드 집합이 다릅니다.")
    for row in plan["records"]:
        supplied_row = by_code[row["option_code"]]
        require(type(supplied_row["apply"]) is bool, "INVALID_PLAN", "apply는 JSON bool이어야 합니다.")
        for key in row.keys() - {"apply"}:
            require(same(row[key], supplied_row[key]), "STALE_PLAN", "기존 상태 또는 계획이 바뀌었습니다. check를 다시 실행하세요.")
        if supplied_row["apply"]:
            require(row["proposed_action"] == "PRESERVE", "INVALID_PLAN", "기존 차이 행만 명시 갱신할 수 있습니다.")
            row["apply"] = True


def expected_rows(plan, existing):
    result = deepcopy(existing)
    for row in plan["records"]:
        if row["before"] is None or row["apply"]:
            result[row["option_code"]] = deepcopy(row["desired"])
    return result


def inspect_schema(connection):
    """대상 테이블과 존재하는 관련 참조만 검사한다. 테이블을 만들지 않는다."""
    require(connection.dialect.name == "mysql", "UNSUPPORTED_SCHEMA", "실제 적재는 MySQL에서만 지원합니다.")
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    require("service_option" in tables, "UNSUPPORTED_SCHEMA", "service_option 테이블이 없습니다.")
    options = inspector.get_table_options("service_option")
    require(options.get("mysql_engine", "").upper() == "INNODB", "UNSUPPORTED_SCHEMA", "InnoDB 테이블이 필요합니다.")
    columns = {c["name"]: c for c in inspector.get_columns("service_option")}
    require(set(columns) == set(FIELDS) and not any(c["nullable"] for c in columns.values()),
            "UNSUPPORTED_SCHEMA", "service_option 컬럼 구성이 명세와 다릅니다.")
    types = {k: c["type"] for k, c in columns.items()}
    require(isinstance(types["option_code"], mysql.VARCHAR) and types["option_code"].length == 40
            and isinstance(types["label_ko"], mysql.VARCHAR) and types["label_ko"].length == 120
            and isinstance(types["option_type"], mysql.ENUM) and tuple(types["option_type"].enums) == OPTION_TYPES
            and isinstance(types["value_json"], mysql.JSON)
            and isinstance(types["sort_order"], mysql.SMALLINT) and types["sort_order"].unsigned
            and isinstance(types["enabled"], mysql.TINYINT) and types["enabled"].display_width == 1,
            "UNSUPPORTED_SCHEMA", "service_option 컬럼 타입이 명세와 다릅니다.")
    require(inspector.get_pk_constraint("service_option")["constrained_columns"] == ["option_code"],
            "UNSUPPORTED_SCHEMA", "옵션 PK가 명세와 다릅니다.")
    require(any(index["name"] == "ix_option_selection" and index["column_names"] ==
                ["option_type", "enabled", "sort_order", "option_code"]
                for index in inspector.get_indexes("service_option")), "UNSUPPORTED_SCHEMA", "옵션 선택 인덱스를 확인하세요.")
    fk_checks, sql_mode = connection.execute(text("SELECT @@SESSION.foreign_key_checks, @@SESSION.sql_mode")).one()
    require(fk_checks == 1 and bool({"STRICT_TRANS_TABLES", "STRICT_ALL_TABLES"} & set(sql_mode.split(","))),
            "UNSUPPORTED_SCHEMA", "FK 검사와 strict SQL 모드가 필요합니다.")
    references = []
    for (table, column), kind in FK_TYPES.items():
        if table not in tables:
            continue
        require(any(fk["constrained_columns"] == [column] and fk["referred_table"] == "service_option"
                    and fk["referred_columns"] == ["option_code"] for fk in inspector.get_foreign_keys(table)),
                "UNSUPPORTED_SCHEMA", "공용 옵션 참조 FK가 명세와 다릅니다.")
        references.append((table, column, kind))
    un_batch = "MISSING"
    batch_targets = []
    if "batch_run" in tables:
        target = next((c["type"] for c in inspector.get_columns("batch_run") if c["name"] == "target_table"), None)
        batch_targets = list(target.enums) if isinstance(target, mysql.ENUM) else []
        un_batch = "PRESENT" if isinstance(target, mysql.ENUM) and "un_data" in target.enums else "MISSING"
    return {"tables": tables, "references": references, "un_batch_enum": un_batch, "batch_targets": batch_targets}


def read_options(session, codes, *, lock=False):
    """입력·기본 코드와 교차 검증 대상(주기·약관)을 한 번에 조회한다."""
    table = ServiceOption.__table__
    stmt = select(table).where(or_(table.c.option_code.in_(sorted(set(codes) | BASE_CODES | CONDITIONAL_CODES)),
                                  table.c.option_type.in_(("REPORT_PERIOD", "NOTIFICATION_TERMS"))))
    stmt = stmt.order_by(table.c.option_code)
    if lock:
        stmt = stmt.with_for_update()
    rows = {r["option_code"]: dict(r) for r in session.execute(stmt).mappings()}
    parents = {r["value_json"]["report_type_code"] for r in rows.values()
               if isinstance(r["value_json"], dict) and isinstance(r["value_json"].get("report_type_code"), str)}
    missing = parents - rows.keys()
    if missing:
        stmt = select(table).where(table.c.option_code.in_(sorted(missing))).order_by(table.c.option_code)
        if lock:
            stmt = stmt.with_for_update()
        rows.update({r["option_code"]: dict(r) for r in session.execute(stmt).mappings()})
    # DB 문자 비교에 걸린 다른 실제 식별자를 신규 코드로 다루지 않는다.
    # 준비 상태 조회용 코드의 문제로 무관한 확장 seed까지 실패시키지는 않는다.
    # casefold로 MySQL의 악센트·공백 비교 규칙을 흉내 내지 않고 DB에서 직접 대조한다.
    unmatched = (set(codes) | parents) - rows.keys()
    if unmatched:
        stmt = select(table.c.option_code).where(table.c.option_code.in_(sorted(unmatched)))
        require(not session.execute(stmt).first(), "CODE_COLLATION_CONFLICT", "DB 문자 비교에서 충돌하는 비표준 코드가 있습니다.")
    return rows


def validate_db_relations(session, expected, seed, schema):
    relevant = {r["option_code"] for r in seed.records}
    validate_relations(expected, external=True, relevant=relevant)
    references = schema["references"]
    if references:
        # 식별자는 고정된 FK_TYPES 목록에서만 가져온다. 값은 expanding 바인딩한다.
        query = " UNION ALL ".join(
            f"SELECT DISTINCT `{column}` AS code, '{kind}' AS kind FROM `{table}` WHERE `{column}` IN :codes"
            for table, column, kind in references)
        stmt = text(query).bindparams(bindparam("codes", expanding=True))
        for code, kind in session.execute(stmt, {"codes": sorted(relevant)}):
            target = expected.get(code)
            require(target is not None and target["option_type"] == kind, "FK_TYPE_CONFLICT", "기존 참조의 옵션 종류가 올바르지 않습니다.")


class Artifacts:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.pending = set()

    def write(self, name, value):
        temporary = self.directory / f".{name}.{uuid4().hex}.tmp"
        self.pending.add(temporary)
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.directory / name)
        self.pending.remove(temporary)

    def event(self, event, **values):
        with (self.directory / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"at": now(), "event": event, **values}, ensure_ascii=False, allow_nan=False) + "\n")
            stream.flush()


def safe_error(exc, stage, commit_attempted=False):
    original = getattr(exc, "orig", exc)
    args = getattr(original, "args", ())
    is_db_error = getattr(exc, "orig", None) is not None or type(original).__module__.startswith("pymysql")
    code = args[0] if is_db_error and args and type(args[0]) is int else None
    return {"error_type": type(exc).__name__, "code": getattr(exc, "code", None),
            "db_error_code": code, "stage": stage, "commit_attempted": commit_attempted,
            "cause_retryable": code in TRANSIENT_DB_CODES,
            "message": str(exc) if isinstance(exc, ContractError) else "실패 단계의 파일·권한·DB 설정 및 서버 로그를 확인하세요."}


def cleanup(report, name, operation):
    try:
        operation()
        return True
    except Exception as exc:
        report["cleanup_errors"].append(safe_error(exc, name, report["commit_state"] != "NOT_ATTEMPTED"))
        report["warnings"].append({"code": "CLEANUP_FAILED", "stage": name})
        return False


def source_readiness(row, schema=None):
    """등록 형식, 미확정 값, DB 배포와 실제 실행기 연동을 별도로 보고한다."""
    value = row["value_json"]
    result = {"enabled": row["enabled"], "configuration": "NOT_CONFIGURED", "jobs": [],
              "consumer_integration": "NOT_CHECKED", "execution_performed": False}
    if not isinstance(value, dict):
        result["configuration"] = "INVALID_LEGACY_METADATA"
        return result
    result["unconfirmed_metadata"] = [key for key in SOURCE_METADATA if key != "jobs" and value.get(key) is None]
    try:
        validate_source(value)
    except ContractError as exc:
        result.update(configuration="INVALID_LEGACY_METADATA", validation_code=exc.code)
        return result
    jobs = value.get("jobs", [])
    if jobs:
        result["configuration"] = "REGISTERED"
    for job in jobs:
        targets = job["target_tables"]
        result["jobs"].append({"job_name": job["job_name"], "schedule_type": job["schedule"]["type"],
            "collection_scope_mode": job["collection_scope"]["mode"], "target_tables": targets,
            "missing_db_tables": sorted(set(targets) - schema["tables"]) if schema is not None else None,
            "missing_batch_targets": sorted(set(targets) - set(schema.get("batch_targets", []))) if schema is not None else None,
            "consumer_integration": "NOT_CHECKED"})
    return result


def readiness(rows, schema=None):
    kinds = {r["option_type"] for r in rows.values() if r["enabled"]}
    periods = [r for r in rows.values() if r["option_type"] == "REPORT_PERIOD" and r["enabled"]]
    return {"base_codes_present": sorted(BASE_CODES & rows.keys()),
            "base_codes_missing": sorted(BASE_CODES - rows.keys()),
            "conditional_codes_present": sorted(CONDITIONAL_CODES & rows.keys()),
            "conditional_codes_missing": sorted(CONDITIONAL_CODES - rows.keys()),
            "nonstandard_codes": sorted(c for c in rows if not re.fullmatch(r"[A-Z0-9_]{1,40}", c)),
            "notification_terms": "REGISTERED" if "NOTIFICATION_TERMS" in kinds else "NOT_CONFIGURED",
            "report_schedule": "NOT_CONFIGURED" if any(not r["value_json"] for r in periods) or not periods
                               else "REQUIRES_CONSUMER_VALIDATION",
            "publication_complete": "NOT_CHECKED",
            "sources": {code: source_readiness(row, schema) for code, row in sorted(rows.items())
                        if row["option_type"] == "SOURCE"},
            "report_periods": {row["option_code"]: "REGISTERED" if row["value_json"] else "NOT_CONFIGURED"
                               for row in rows.values() if row["option_type"] == "REPORT_PERIOD"},
            "un_source": "REGISTERED" if "UN_COMTRADE_SOURCE" in rows else "NOT_CONFIGURED",
            "un_table": ("PRESENT" if "un_data" in schema["tables"] else "MISSING") if schema else "NOT_CHECKED",
            "un_batch_enum": schema.get("un_batch_enum", "NOT_CHECKED") if schema else "NOT_CHECKED",
            "un_executor": "NOT_CHECKED", "un_ready": False}


def _counts(report, plan):
    report["planned_insert_count"] = sum(r["before"] is None for r in plan["records"])
    report["planned_update_count"] = sum(r["apply"] for r in plan["records"])
    report["unchanged_count"] = sum(r["proposed_action"] == "UNCHANGED" for r in plan["records"])
    report["preserved_count"] = sum(r["proposed_action"] == "PRESERVE" and not r["apply"] for r in plan["records"])
    report["differences"] = [{"option_code": r["option_code"], "differences": r["differences"], "apply": r["apply"]}
                             for r in plan["records"] if r["before"] is not None and r["differences"]]
    if report["preserved_count"]:
        report["warnings"].append({"code": "PRESERVED_DIFFERENCE", "count": report["preserved_count"]})


def _verify(actual, expected, codes):
    require(all(code in actual and same(actual[code], expected[code]) for code in codes),
            "VERIFY_MISMATCH", "실제 저장값이 이번 실행의 예상 상태와 다릅니다.")


def requires_recalculation(plan):
    for row in plan["records"]:
        if row["option_code"] != "CUSTOMS_ANOMALY":
            continue
        before, desired = row["before"], row["desired"]
        if before is None:
            return desired["enabled"]
        if row["apply"]:
            return "enabled" in row["differences"] or (
                "value_json" in row["differences"] and (before["enabled"] or desired["enabled"]))
    return False


def run(*, mode="validate", seed_path=DEFAULT_SEED, output=DEFAULT_OUTPUT, env_path=ENV_PATH,
        apply_plan=None, engine=None):
    """반환값은 비밀정보를 제거한 report. engine 주입은 테스트용이며 소유권은 호출자에게 있다."""
    run_id = uuid4().hex
    directory = Path(output).resolve() / f"{datetime.now(KST):%Y%m%d_%H%M%S_%f}_{run_id}"
    report = {"run_id": run_id, "mode": mode, "status": "RUNNING", "started_at": now(), "finished_at": None,
              "seed_path": str(Path(seed_path).resolve()), "seed_sha256": None, "seed_version": None,
              "target_db": None, "input_count": 0, "planned_insert_count": 0, "planned_update_count": 0,
              "inserted_count": 0, "updated_count": 0, "unchanged_count": 0, "preserved_count": 0,
              "rolled_back": False, "rollback_state": "NOT_NEEDED", "commit_state": "NOT_ATTEMPTED",
              "retryable": False, "action_required": False, "verification": {"file": "NOT_CHECKED",
              "database": "NOT_CHECKED", "transaction": "NOT_CHECKED", "public": "NOT_CHECKED"},
              "errors": [], "cleanup_errors": [], "warnings": [], "recalculation_required": False,
              "configuration_only": True, "source_jobs_executed": False, "batch_run_written": False,
              "output_dir": str(directory), "exit_code": 0}
    artifacts = Artifacts(directory)
    session = None
    owned_engine = False
    writes_attempted = False
    stage = "initialize_outputs"
    try:
        directory.mkdir(parents=True, exist_ok=False)
        artifacts.event("started", run_id=run_id, mode=mode)
        artifacts.write("report.json", report)
        # DB 쓰기 전에 모든 필수 출력 파일의 저장 가능 여부를 확인한다.
        artifacts.write("plan.json", {"status": "NOT_PLANNED"})
        if mode == "load":
            artifacts.write("before_snapshot.json", {"status": "NOT_CAPTURED"})
        stage = "validate"
        require(mode in ("validate", "check", "load"), "INVALID_MODE", "validate/check/load만 지원합니다.")
        require(apply_plan is None or mode == "load", "INVALID_MODE", "변경 계획은 load에서만 적용할 수 있습니다.")
        seed = read_manifest(seed_path)
        report.update(seed_sha256=seed.sha256, seed_version=seed.version, input_count=len(seed.records))
        report["verification"]["file"] = "PASSED"
        # 계획은 before/desired/차이를 포함하므로 같은 seed보다 클 수 있다.
        supplied = read_json(apply_plan, limit=32 * 1024 * 1024)[0] if apply_plan is not None else None
        codes = {r["option_code"] for r in seed.records}
        codes.update(r["value_json"]["report_type_code"] for r in seed.records
                     if "report_type_code" in r["value_json"])
        if mode == "validate":
            plan = build_plan(seed, {}, None)
            for row in plan["records"]:
                row["proposed_action"] = "NOT_CHECKED"
                row["before_hash"] = None
            artifacts.write("plan.json", plan)
            report["readiness"] = readiness({r["option_code"]: r for r in seed.records})
            report["readiness"]["database"] = "NOT_CHECKED"
            report["status"] = "VALIDATED"
        else:
            stage = "connect"
            if engine is None:
                engine = make_engine(mysql_url(env_path))
                owned_engine = True
            url = engine.url
            report["target_db"] = {"host": url.host, "port": url.port or 3306, "database": url.database}
            session = Session(bind=engine)
            stage = "schema"
            schema = inspect_schema(session.connection())
            stage = "read_current"
            # load의 조회는 처음부터 잠금 읽기다. check는 SELECT만 수행한다.
            existing = read_options(session, codes, lock=mode == "load")
            plan = build_plan(seed, existing, report["target_db"])
            if supplied is not None:
                approve_plan(plan, supplied)
            expected = expected_rows(plan, existing)
            # 보존한 기존 상태가 계약 위반이어도 정정용 검토 계획은 남긴다.
            # 계획 작성 자체는 승인·DB 쓰기가 아니며 load에서 전부 재검증한다.
            stage = "write_plan"
            artifacts.write("plan.json", plan)
            stage = "references"
            validate_db_relations(session, expected, seed, schema)
            _counts(report, plan)
            for row in plan["records"]:
                before = row["before"]
                if before is not None and before["option_type"] == "SOURCE" and (
                        not isinstance(before["value_json"], dict) or not before["value_json"].get("url")):
                    report["warnings"].append({"code": "LEGACY_METADATA_INCOMPLETE", "option_code": row["option_code"]})
            report["verification"]["database"] = "PASSED"
            report["readiness"] = readiness(existing, schema)
            if mode == "check":
                report["planned_readiness"] = readiness(expected, schema)
                report["status"] = "CHECKED"
            else:
                stage = "snapshot"
                artifacts.write("before_snapshot.json", {"run_id": run_id, "captured_at": now(),
                    "target_db": report["target_db"], "seed_sha256": seed.sha256,
                    "existing": [r["before"] for r in plan["records"] if r["before"] is not None],
                    "absent_codes": [r["option_code"] for r in plan["records"] if r["before"] is None]})
                artifacts.event("planned", inserts=report["planned_insert_count"], updates=report["planned_update_count"])
                stage = "write"
                crud = CRUD(ServiceOption)
                for row in plan["records"]:
                    if row["before"] is None:
                        writes_attempted = True
                        crud.create(session, **deepcopy(row["desired"]))
                    elif row["apply"]:
                        writes_attempted = True
                        values = {k: deepcopy(v) for k, v in row["desired"].items() if k not in ("option_code", "option_type")}
                        crud.update(session, row["option_code"], **values)
                stage = "transaction_verify"
                actual = read_options(session, codes, lock=True)
                _verify(actual, expected, {r["option_code"] for r in seed.records})
                validate_db_relations(session, actual, seed, schema)
                report["verification"]["transaction"] = "PASSED"
                artifacts.event("before_commit")
                artifacts.write("report.json", report)
                stage = "commit"
                report["commit_state"] = "ATTEMPTED"
                session.commit()
                report["commit_state"] = "CONFIRMED"
                report["inserted_count"] = report["planned_insert_count"]
                report["updated_count"] = report["planned_update_count"]
                report["recalculation_required"] = requires_recalculation(plan)
                stage = "public_verify"
                # 커밋 뒤 별도 세션에서 공개된 실제 값을 확인한다.
                public_session = Session(bind=engine)
                try:
                    public_rows = read_options(public_session, codes)
                    _verify(public_rows, expected, {r["option_code"] for r in seed.records})
                    validate_db_relations(public_session, public_rows, seed, schema)
                    report["readiness"] = readiness(public_rows, schema)
                    report["verification"]["public"] = "PASSED"
                finally:
                    if not cleanup(report, "public_session_close", public_session.close):
                        report["action_required"] = True
                report["status"] = "LOADED"
        artifacts.event("completed", status=report["status"], commit_state=report["commit_state"])
    except Exception as exc:
        error = safe_error(exc, stage, report["commit_state"] != "NOT_ATTEMPTED")
        report["errors"].append(error)
        report["status"] = "FAILED"
        report["action_required"] = True
        report["exit_code"] = 1 if isinstance(exc, ContractError) and error["code"] != "UNSUPPORTED_SCHEMA" else 2
        if report["commit_state"] == "ATTEMPTED":
            if error["db_error_code"] in {1205, 1213}:
                report["commit_state"] = "NOT_COMMITTED"
            else:
                report["commit_state"] = "UNKNOWN"
                report["status"] = "COMMIT_UNKNOWN"
                report["exit_code"] = 3
        elif report["commit_state"] == "CONFIRMED":
            report["status"] = "COMMITTED_VERIFY_FAILED" if stage == "public_verify" else "COMMITTED_REPORT_FAILED"
            report["exit_code"] = 3
        if session is not None and report["commit_state"] != "CONFIRMED":
            rolled_back = cleanup(report, "rollback", session.rollback)
            if writes_attempted:
                confirmed = rolled_back and report["commit_state"] != "UNKNOWN"
                report["rolled_back"] = True if confirmed else None
                report["rollback_state"] = "CONFIRMED" if confirmed else "UNKNOWN"
        if report["commit_state"] in ("NOT_ATTEMPTED", "NOT_COMMITTED") and report["rollback_state"] != "UNKNOWN":
            report["retryable"] = error["cause_retryable"] and not report["cleanup_errors"]
            report["action_required"] = not report["retryable"]
        cleanup(report, "failure_event", lambda: artifacts.event("failed", **error))
    finally:
        if session is not None and not cleanup(report, "session_close", session.close):
            report["action_required"] = True
            report["retryable"] = False
        if owned_engine and not cleanup(report, "engine_dispose", engine.dispose):
            report["action_required"] = True
            report["retryable"] = False
        for temporary in list(artifacts.pending):
            cleanup(report, "temporary_file_cleanup", lambda p=temporary: p.unlink(missing_ok=True))
        if report["warnings"] and report["status"] in ("VALIDATED", "CHECKED", "LOADED"):
            report["status"] += "_WITH_WARNINGS"
        report["finished_at"] = now()
        try:
            artifacts.write("report.json", report)
        except Exception as exc:
            report["errors"].append(safe_error(exc, "final_report", report["commit_state"] != "NOT_ATTEMPTED"))
            report["status"] = "COMMITTED_REPORT_FAILED" if report["commit_state"] == "CONFIRMED" else report["status"]
            if report["status"] in ("VALIDATED", "CHECKED", "LOADED", "VALIDATED_WITH_WARNINGS", "CHECKED_WITH_WARNINGS"):
                report["status"] = "FAILED"
            report["exit_code"] = 3 if report["commit_state"] in ("CONFIRMED", "UNKNOWN") else 2
            report["retryable"] = False
            report["action_required"] = True
            cleanup(report, "report_failure_event", lambda: artifacts.event("report_failed", status=report["status"],
                                                                            commit_state=report["commit_state"]))
            for temporary in list(artifacts.pending):
                cleanup(report, "temporary_file_cleanup", lambda p=temporary: p.unlink(missing_ok=True))
    return report
