"""ML 피처용 외부 데이터(World Bank WDI·CEPII BACI·UN Comtrade)를 받아 data/external/에 CSV로 저장합니다.

DB에 적재하지 않습니다. 연 1회 갱신하는 정적 입력이라 학습·배치 예측에서 CSV를 직접 읽습니다.
국가 키는 관세청 country_code와 같은 ISO2입니다.
"""
import argparse
import io
import json
import re
import sys
import time
import zipfile
from pathlib import Path

import pandas as pd
import requests
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[5]
OUT_DIR = ROOT / "data" / "external"
SEED_SQL = ROOT / "db" / "mysql" / "init" / "04_country_seed.sql"

WDI_URL = "https://api.worldbank.org/v2"
# WDI 지표 코드 → 저장 컬럼명
WDI_INDICATORS = {
    "NY.GDP.MKTP.CD": "gdp_usd",
    "NY.GDP.MKTP.KD.ZG": "gdp_growth_pct",
    "SP.POP.TOTL": "population",
    "NY.GDP.PCAP.CD": "gdp_per_capita_usd",
}
BACI_URL = "https://www.cepii.fr/DATA_DOWNLOAD/baci/data"
BACI_FILE = re.compile(r"BACI_HS22_Y(\d{4})_V\d+\.csv$")
BACI_COUNTRY_FILE = re.compile(r"country_codes_V\d+\.csv$")
KOREA_ISO3 = "KOR"
# BACI 국가 파일에서 ISO2가 빈칸인 국가. 대만은 490 "Other Asia, nes"로 표기된다.
BACI_ISO2_FIX = {490: "TW", 516: "NA"}
TIMEOUT = 60

COMTRADE_URL = "https://comtradeapi.un.org"
COMTRADE_KOREA = 410
ENV_KEY = "UN_COMTRADE_API_KEY"
LEGACY_ENV_KEY = "COMTRADE_API_KEY"  # 2026-10-28 이전 이름. 기존 서버 .env에서 Comtrade가 조용히 건너뛰어지지 않게 함께 읽는다


def comtrade_key(values):
    """.env 값에서 Comtrade 키. 새 이름이 없으면 예전 이름을 쓴다."""
    return values.get(ENV_KEY) or values.get(LEGACY_ENV_KEY)


def wb_get(path, **params):
    """World Bank API v2의 한 페이지 JSON 응답에서 데이터 목록만 돌려준다."""
    res = requests.get(f"{WDI_URL}/{path}", params={"format": "json", "per_page": 20000, **params},
                       timeout=TIMEOUT)
    res.raise_for_status()
    body = res.json()
    if len(body) < 2 or body[0].get("pages", 1) > 1:
        raise RuntimeError(f"WDI 응답 형식 오류 또는 페이지 초과: {path}")
    return body[1] or []


def wdi_frame(countries, values):
    """countries: /country 응답, values: 지표별 /indicator 응답 목록. 지역 집계 행은 뺀다."""
    real = {c["iso2Code"] for c in countries if c["region"]["value"] != "Aggregates"}
    rows = [{"year": int(v["date"]), "country_code": v["country"]["id"],
             WDI_INDICATORS[v["indicator"]["id"]]: v["value"]}
            for v in values if v["country"]["id"] in real]
    df = pd.DataFrame(rows).groupby(["year", "country_code"], as_index=False).first()
    # 값이 하나도 없는 지표도 빈 컬럼으로 남긴다.
    return df.reindex(columns=["year", "country_code", *WDI_INDICATORS.values()]).sort_values(
        ["country_code", "year"])


def save_csv(df, path):
    """임시 파일에 다 쓴 뒤에만 이름을 바꿔, 저장 중 실패한 반쪽 CSV가 완료본으로 남지 않게 한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(".part")
    df.to_csv(part, index=False)
    part.replace(path)


def collect_wdi(from_year, to_year):
    countries = wb_get("country")
    values = [v for code in WDI_INDICATORS
              for v in wb_get(f"country/all/indicator/{code}", date=f"{from_year}:{to_year}")]
    df = wdi_frame(countries, values)
    path = OUT_DIR / "wdi" / f"wdi_{from_year}_{to_year}.csv"
    save_csv(df, path)
    print(f"WDI 저장: {path} ({len(df)}행, 국가 {df.country_code.nunique()}개)")
    return {"requests": 1 + len(WDI_INDICATORS), "rows": len(values)}


def baci_imports(trade, codes):
    """BACI 원본(t,i,j,k,v 천 달러)을 수입국·HS6별 전세계 수입액과 한국발 수입액으로 집계한다."""
    iso2 = codes.set_index("country_code")["country_iso2"]
    iso2 = iso2.where(iso2 != "").fillna(pd.Series(BACI_ISO2_FIX))  # 나머지 빈칸은 지역 묶음 코드라 버린다
    korea = codes.loc[codes.country_iso3 == KOREA_ISO3, "country_code"].item()
    trade = trade.assign(v=trade.v * 1000, kor=(trade.i == korea) * trade.v * 1000)
    out = trade.groupby(["t", "j", "k"], as_index=False).agg(
        import_world_usd=("v", "sum"), import_from_kor_usd=("kor", "sum"))
    out["country_code"] = out.j.map(iso2)
    out["hs6"] = out.k.astype(str).str.zfill(6)
    out = out.rename(columns={"t": "year"}).dropna(subset=["country_code"])
    return out[["year", "country_code", "hs6", "import_world_usd", "import_from_kor_usd"]]


def download_baci(version, dest):
    """CEPII에서 BACI_HS22_{version}.zip을 받는다. 아직 공개 전(404)이면 False. 로그인은 필요 없다."""
    url = f"{BACI_URL}/BACI_HS22_{version}.zip"
    with requests.get(url, stream=True, timeout=TIMEOUT) as res:
        if res.status_code == 404:
            return False
        res.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_suffix(".part")
        with open(part, "wb") as f:
            for chunk in res.iter_content(1 << 20):
                f.write(chunk)
    if not zipfile.is_zipfile(part):
        part.unlink()
        raise RuntimeError(f"받은 파일이 zip이 아님: {url}")
    part.replace(dest)  # 다 받은 뒤에만 이름을 바꿔, 중간에 끊긴 파일이 완료본으로 남지 않게 한다
    print(f"BACI 다운로드: {dest} ({dest.stat().st_size:,} bytes)")
    return True


def collect_baci(zip_path, from_year):
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        code_name = next((n for n in names if BACI_COUNTRY_FILE.search(n)), None)
        if code_name is None:
            raise RuntimeError("zip 안에 country_codes_V*.csv가 없음")
        codes = pd.read_csv(z.open(code_name), dtype={"country_iso2": str}, keep_default_na=False)
        missing = {"country_code", "country_iso2", "country_iso3"} - set(codes.columns)
        if missing:
            raise RuntimeError(f"BACI 국가 코드 파일에 컬럼 없음: {sorted(missing)}")
        years = sorted((int(m.group(1)), n) for n in names if (m := BACI_FILE.search(n)))
        frames, rows = [], 0
        for year, name in years:
            if year < from_year:
                continue
            trade = pd.read_csv(io.TextIOWrapper(z.open(name)), usecols=["t", "i", "j", "k", "v"],
                                dtype={"k": str}, skipinitialspace=True)
            frames.append(baci_imports(trade, codes))
            rows += len(trade)
            print(f"BACI {year}: 원본 {len(trade):,}행 → 집계 {len(frames[-1]):,}행")
    if not frames:
        raise RuntimeError(f"zip 안에 {from_year}년 이후 BACI_HS22_Y*.csv가 없음")
    df = pd.concat(frames, ignore_index=True)
    path = OUT_DIR / "baci" / "baci_imports_hs22.csv"
    save_csv(df, path)
    print(f"BACI 저장: {path} ({len(df):,}행, {df.year.min()}~{df.year.max()})")
    return {"rows": rows}


def comtrade_get(path, key, **params):
    """Comtrade 호출. 429(호출 한도)·5xx·연결 오류는 잠시 쉬고 3번까지 다시 시도한다."""
    for wait in (10, 20, None):  # 마지막 시도 뒤에는 쉬지 않고 바로 실패 처리
        try:
            res = requests.get(f"{COMTRADE_URL}/{path}", params=params, timeout=TIMEOUT * 2,
                               headers={"Ocp-Apim-Subscription-Key": key})
        except (requests.ConnectionError, requests.Timeout):
            if wait is None:
                raise
        else:
            if res.status_code != 429 and res.status_code < 500 or wait is None:
                break
        time.sleep(wait)
    res.raise_for_status()
    body = res.json()
    if body.get("error"):
        raise RuntimeError(f"Comtrade 오류: {body['error']}")
    return body.get("data") or []


def comtrade_imports(records, iso2_by_code):
    """수입국·HS6별 전세계(partner 0)와 한국발(partner 410) 수입액. HS2022(H6)로 신고한 행만 쓴다."""
    columns = ["year", "country_code", "hs6", "import_world_usd", "import_from_kor_usd"]
    df = pd.DataFrame(records)
    if df.empty:
        return pd.DataFrame(columns=columns)
    df = df[(df.classificationCode == "H6") & df.partnerCode.isin([0, COMTRADE_KOREA])]
    df = df.assign(country_code=df.reporterCode.map(iso2_by_code)).dropna(subset=["country_code"])
    if df.empty:
        return pd.DataFrame(columns=columns)
    # sum은 원본 null을 0으로 바꾼다. 집계 전에 실제 응답 행의 금액을 검증한다.
    values = pd.to_numeric(df["primaryValue"], errors="coerce")
    invalid = values.isna() | values.isin([float("inf"), float("-inf")])
    if invalid.any():
        keys = df.loc[invalid, ["refYear", "reporterCode", "partnerCode", "cmdCode"]].head(5).to_dict("records")
        raise ValueError(f"Comtrade primaryValue 결측·비수치·비유한 값 {int(invalid.sum())}건: {keys}")
    df = df.assign(primaryValue=values)
    out = df.pivot_table(index=["refYear", "country_code", "cmdCode"], columns="partnerCode",
                         values="primaryValue", aggfunc="sum").reindex(columns=[0, COMTRADE_KOREA])
    out = out.fillna({COMTRADE_KOREA: 0}).dropna(subset=[0]).reset_index()
    out.columns = columns
    return out


def collect_comtrade(year):
    key = comtrade_key(dotenv_values(ROOT / ".env"))
    if not key:
        raise RuntimeError(f".env에 {ENV_KEY} 값이 없음")
    res = requests.get(f"{COMTRADE_URL}/files/v1/app/reference/Reporters.json", timeout=TIMEOUT)
    res.raise_for_status()
    reporters = res.json()["results"]
    iso2_by_code = {r["reporterCode"]: r["reporterCodeIsoAlpha2"] for r in reporters
                    if not r.get("isGroup") and r.get("reporterCodeIsoAlpha2")}
    available = comtrade_get("data/v1/getDA/C/A/HS", key, period=year)
    codes = sorted({d["reporterCode"] for d in available} & iso2_by_code.keys())
    print(f"Comtrade {year}: 신고 국가 {len(codes)}개 (EU 등 묶음 제외)")
    if not codes:
        return {"requests": 2, "rows": 0}
    # 신고국별 응답을 파일로 남겨, 한도 초과·오류로 끊겨도 다음 실행은 못 받은 나라만 받는다.
    # ponytail: 한 번 받은 나라는 다시 받지 않음. 수정 신고까지 반영하려면 raw/<연도>/를 지우고 실행
    raw = OUT_DIR / "comtrade" / "raw" / str(year)
    raw.mkdir(parents=True, exist_ok=True)
    records, fetched = [], 0
    for n, code in enumerate(codes, 1):
        cache = raw / f"{code}.json"
        if not cache.exists():
            data = comtrade_get("data/v1/get/C/A/HS", key, reporterCode=code, period=year,
                                partnerCode=f"0,{COMTRADE_KOREA}", flowCode="M", cmdCode="AG6",
                                customsCode="C00", motCode="0", partner2Code="0", includeDesc="false")
            part = cache.with_suffix(".part")
            part.write_text(json.dumps(data), "utf-8")
            part.replace(cache)
            fetched += 1
            time.sleep(1)  # ponytail: 고정 1초 간격, 무료 키 한도(하루 500회)를 넘기면 다음 날 이어서 실행
        records += json.loads(cache.read_text("utf-8"))
        print(f"  {n}/{len(codes)} {iso2_by_code[code]}", end="\r")
    df = comtrade_imports(records, iso2_by_code)
    path = OUT_DIR / "comtrade" / f"comtrade_imports_{year}.csv"
    save_csv(df, path)
    print()
    print(f"Comtrade 저장: {path} ({len(df):,}행, 국가 {df.country_code.nunique()}개)")
    return {"requests": 2 + fetched, "rows": len(records)}


def seed_countries():
    """관세청 국가 사전(04_country_seed.sql)의 COUNTRY 코드."""
    return set(re.findall(r"^\('([A-Z0-9]+)','[^']*','COUNTRY'", SEED_SQL.read_text("utf-8"), re.M))


def check_frame(df, countries, keys, money, min_year, coverage=0.95):
    """CSV 하나의 연도·키 중복·국가 매핑률·금액 범위를 검사해 문제 목록을 돌려준다."""
    problems = []
    missing = set(keys + money) - set(df.columns)
    if missing:
        return [f"필수 컬럼 누락: {sorted(missing)}"]
    if df.empty:
        return ["CSV가 비어 있음"]
    if df[keys].isna().any().any():
        problems.append("키 결측")
    if df.year.min() != min_year:
        problems.append(f"시작 연도 {df.year.min()} != {min_year}")
    if df.duplicated(keys).any():
        problems.append(f"키 중복 {int(df.duplicated(keys).sum())}행")
    mapped = countries & set(df.country_code)
    if len(mapped) < coverage * len(countries):
        problems.append(f"국가 매핑률 {len(mapped)}/{len(countries)} < {coverage:.0%}")
    for col in money:
        if (df[col].dropna() < 0).any():
            problems.append(f"{col}에 음수")
    print(f"  매핑 안 된 국가: {' '.join(sorted(countries - mapped)) or '없음'}")
    return problems


def check_baci(df):
    problems = []
    required = {"hs6", "import_world_usd", "import_from_kor_usd"}
    if not required <= set(df.columns):
        return [f"무역 컬럼 누락: {sorted(required - set(df.columns))}"]
    if df[list(required)].isna().any().any():
        problems.append("무역 코드·금액 결측 (0과 구분 필요)")
    if not df.hs6.str.fullmatch(r"\d{6}").all():
        problems.append("6자리 숫자가 아닌 hs6")
    if (df.import_from_kor_usd > df.import_world_usd).any():
        problems.append("한국 점유율 > 1")
    return problems


def check_db(baci, compare=("VN", "US", "CN"), tolerance=0.2):
    """DB의 HS2022 사전과 export_summary 연간 합계로 BACI를 교차 검증한다."""
    from sqlalchemy import bindparam, text
    sys.path.insert(0, str(ROOT))
    from backend.app.db.session import make_engine
    engine = make_engine()
    try:
        with engine.connect() as conn:
            hs6 = set(conn.execute(text(
                "SELECT hs_code FROM hs_code WHERE hs_ver='HS2022' AND depth=6")).scalars())
            sums = conn.execute(text(
                "SELECT YEAR(trade_month), country_code, SUM(amount_usd) FROM export_summary"
                " WHERE country_code IN :c GROUP BY 1, 2"
                " HAVING COUNT(DISTINCT trade_month) = 12").bindparams(
                    bindparam("c", list(compare), expanding=True))).all()  # 연간 비교라 12개월이 다 있는 해만
    finally:
        engine.dispose()
    problems = []
    unknown = ~baci.hs6.isin(hs6)
    print(f"  HS2022 사전에 없는 hs6 비율: {unknown.mean():.2%} ({baci.hs6[unknown].nunique()}개 코드)")
    kor = baci.groupby(["year", "country_code"]).import_from_kor_usd.sum()
    if not sums:
        print("  교차 검증 건너뜀: export_summary에 12개월이 모두 적재된 해가 없음")
    for year, country, ours in sums:
        if (year, country) not in kor:
            continue
        diff = kor[(year, country)] / float(ours) - 1
        print(f"  {year} 한국→{country}: BACI {kor[(year, country)]:,.0f} / 관세청 {float(ours):,.0f} ({diff:+.1%})")
        if abs(diff) > tolerance:
            problems.append(f"{year} {country} 교차 검증 차이 {diff:+.1%}")
    return problems


def check(with_db):
    countries = seed_countries()
    problems = []
    wdi_files = sorted((OUT_DIR / "wdi").glob("wdi_*.csv"), key=lambda p: p.stat().st_mtime)
    baci_path = OUT_DIR / "baci" / "baci_imports_hs22.csv"
    comtrade_files = sorted((OUT_DIR / "comtrade").glob("comtrade_imports_*.csv"))
    if not wdi_files and not baci_path.exists() and not comtrade_files:
        print("실패: 검사할 CSV가 없음")
        return False
    for path in wdi_files[-1:]:
        print(f"[WDI] {path.name}")
        df = pd.read_csv(path, keep_default_na=False, na_values=[""])
        problems += check_frame(df, countries, ["year", "country_code"], ["gdp_usd", "population"], 2022,
                                 coverage=0.85)  # WDI는 대만·소규모 속령을 발표하지 않는다
    if baci_path.exists():
        print(f"[BACI] {baci_path.name}")
        df = pd.read_csv(baci_path, dtype={"hs6": str}, keep_default_na=False, na_values=[""])
        money = ["import_world_usd", "import_from_kor_usd"]
        # BACI는 레위니옹·푸에르토리코·리히텐슈타인 등을 프랑스·미국·스위스에 합쳐 발표한다.
        problems += check_frame(df, countries, ["year", "country_code", "hs6"], money, 2022, coverage=0.9)
        problems += check_baci(df)
        if with_db:
            problems += check_db(df)
    for path in comtrade_files:
        print(f"[Comtrade] {path.name}")
        df = pd.read_csv(path, dtype={"hs6": str}, keep_default_na=False, na_values=[""])
        money = ["import_world_usd", "import_from_kor_usd"]
        # 최신 연도는 신고를 마친 국가만 있어 매핑률 기준을 두지 않는다.
        problems += check_frame(df, countries, ["year", "country_code", "hs6"], money,
                                int(path.stem[-4:]), coverage=0)
        problems += check_baci(df)
    print("\n".join(f"실패: {p}" for p in problems) or "발견된 CSV의 검사 통과 (전체 수집 완료를 뜻하지 않음)")
    return not problems


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    wdi = sub.add_parser("wdi", help="World Bank WDI 지표를 API로 받아 저장")
    wdi.add_argument("--from-year", type=int, default=2022)
    wdi.add_argument("--to-year", type=int, default=2025)
    baci = sub.add_parser("baci", help="CEPII에서 받은 BACI HS22 zip을 수입국·HS6별로 집계해 저장")
    baci.add_argument("zip", type=Path)
    baci.add_argument("--from-year", type=int, default=2022)
    ct = sub.add_parser("comtrade", help="UN Comtrade API로 BACI에 없는 연도의 수입액을 받아 저장 (.env 키 필요)")
    ct.add_argument("--year", type=int, default=2025)
    chk = sub.add_parser("check", help="저장한 CSV 검사 (--db면 DB 교차 검증 포함)")
    chk.add_argument("--db", action="store_true")
    args = parser.parse_args()
    if args.cmd == "wdi":
        collect_wdi(args.from_year, args.to_year)
    elif args.cmd == "baci":
        collect_baci(args.zip, args.from_year)
    elif args.cmd == "comtrade":
        collect_comtrade(args.year)
    else:
        sys.exit(0 if check(args.db) else 1)


if __name__ == "__main__":
    main()

