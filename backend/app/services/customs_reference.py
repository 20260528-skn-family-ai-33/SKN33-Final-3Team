"""국가 사전 조회와 HS 판본 검증. 유효한 미등록 HS10만 실적에서 보완한다."""
import re

from backend.app.db.batch_log import Source
from backend.app.services.customs_stage import ValidationError

CUSTOMS_SOURCE = Source("CUSTOMS", "관세청",
    {"url": "https://apis.data.go.kr/1220000/nitemtrade/getNitemtradeList"})


def load_country_references(connection):
    """COUNTRY·SPECIAL만 DB 사전에서 조회한다. 미매핑 NULL은 그대로 유지한다."""
    rows = connection.exec_driver_sql("""SELECT c.country_code,c.name_ko,c.country_role,
        c.region_id,c.currency_code,r.region_id AS matched_region_id,r.region_level,
        r.name_ko AS region_name_ko,p.region_id AS continent_id,p.region_level AS continent_level,
        p.name_ko AS continent_name_ko,u.currency_code AS matched_currency_code,
        u.name_ko AS currency_name_ko
        FROM country c LEFT JOIN market_region r ON r.region_id=c.region_id
        LEFT JOIN market_region p ON p.region_id=r.parent_id
        LEFT JOIN currency u ON u.currency_code=c.currency_code
        WHERE c.country_role IN ('COUNTRY','SPECIAL') ORDER BY c.country_code""").mappings().all()
    if not rows:
        raise ValidationError("국가 사전이 비어 있음; market_region.sql·country.sql 기준정보를 먼저 준비해야 함")
    references = {}
    for raw in rows:
        row = dict(raw)
        if row["region_id"] is not None and (row["matched_region_id"] is None or
                row["region_level"] != 2 or row["continent_id"] is None or row["continent_level"] != 1):
            raise ValidationError(f"국가의 하위 지역·대륙 연결 오류: {row['country_code']}")
        if row["currency_code"] is not None and row["matched_currency_code"] is None:
            raise ValidationError(f"국가의 통화 사전 연결 오류: {row['country_code']}")
        references[row["country_code"]] = row
    return references


def resolve_hs_versions(hs10_codes, available_pairs, requested, *, allow_new=False):
    """명시 판본 또는 사전에서 유일한 판본을 연결한다. 연도 추정·코드 변환은 하지 않는다.

    기존 HS10이 여러 판본에 있으면 임의로 고르지 않는다. 같은 판본 HS6가 반드시 필요하다.
    allow_new는 미등록 코드에 한해 상위 HS2·HS4·HS6가 모두 있는 판을 후보로 사용한다.
    """
    if requested != "auto" and not re.fullmatch(r"[A-Z0-9_]{1,10}", requested or ""):
        raise ValidationError("hs_ver: 대문자·숫자·밑줄 1~10자 또는 auto 필요")
    pairs = set(available_pairs)
    by_code = {}
    for code, version in pairs:
        by_code.setdefault(code, set()).add(version)
    versions, missing = {}, {}
    for code in sorted(hs10_codes):
        if not re.fullmatch(r"[0-9]{10}", code):
            missing[code] = "10자리 숫자 HS 코드가 아님"
            continue
        candidates = by_code.get(code, set()) if requested == "auto" else (
            {requested} if (code, requested) in pairs else set())
        if not candidates and allow_new:
            parent_versions = [by_code.get(code[:n], set()) for n in (2, 4, 6)]
            candidates = set.intersection(*parent_versions)
            if requested != "auto":
                candidates &= {requested}
        if not candidates:
            missing[code] = "HS10 사전 누락 또는 동일 판본 상위 HS2·HS4·HS6 누락"
        elif len(candidates) != 1:
            missing[code] = "HS10 판본이 복수임; 원천 판본을 명시해야 함: " + ", ".join(sorted(candidates))
        else:
            version = next(iter(candidates))
            if (code[:6], version) not in pairs:
                missing[code] = f"동일 판본 HS6 사전 누락: {version}"
            else:
                versions[code] = version
    return versions, missing


def plan_hs_references(stage, available_pairs, requested):
    """쓰기 없는 사전 보완 계획. 기존 HS10의 이름·출처는 읽거나 갱신하지 않는다."""
    pairs = set(available_pairs)
    _, codes = stage.references()
    versions, missing = resolve_hs_versions(
        {c for c in codes if len(c) == 10}, pairs, requested, allow_new=True)
    sources = stage.hs_sources()
    additions = {}
    for code, version in list(versions.items()):
        if (code, version) in pairs:
            continue
        if code not in sources:
            missing[code] = "미등록 HS10의 유효한 statKor가 없거나 품목명이 서로 충돌함"
            del versions[code]
            continue
        name, month = sources[code]
        source = f"관세청 수출입실적 {month[:4]}년 {int(month[5:7])}월"
        additions[code] = (code, version, name, source)
    return versions, missing, additions


def insert_hs_additions(connection, additions):
    """호출자의 월별 트랜잭션 안에서 실행. PK 경쟁 시 기존 행의 모든 값을 보존한다."""
    ensured = 0
    for code, version, name, source in additions:
        parents = tuple(code[:n] for n in (2, 4, 6))
        # 전체 PK의 동등 조건으로 각각 잠근다. IN 범위 잠금은 다른 HS10 INSERT와 교착할 수 있다.
        for parent in parents:
            row = connection.exec_driver_sql(
                "SELECT hs_code FROM hs_code WHERE hs_code=%s AND hs_ver=%s FOR SHARE",
                (parent, version)).fetchone()
            if row is None:
                raise ValidationError(f"HS10 추가 직전 상위 코드 변경/누락: {code}/{version}")
        connection.exec_driver_sql("""INSERT INTO hs_code
            (hs_code,hs_ver,name_ko,original_name_ko,source_edition)
            VALUES (%s,%s,%s,NULL,%s) ON DUPLICATE KEY UPDATE hs_code=hs_code""",
            (code, version, name, source))
        ensured += 1
    return ensured
