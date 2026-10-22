"""ML 입력을 수집기가 내려받은 CSV(·xlsx)에서 읽는다. 환율만 DB에서 읽는다.

| 입력 | 파일 |
|---|---|
| 관세청 수출·수입 | 연도별 일괄 수집 collectors/customs/customs_outputs/*/customs_raw_*.csv (+ customs_collection_*.json) |
|  | 매월 정기 수집 data/customs_load_outputs/monthly/<YYYYMM>/<collection_dir>/customs_raw_<YYYYMM>.csv (+ collection.json) |
|  | state.json의 collection_dir가 가리키는 수집본만 사용한다 |
|  | 같은 달이 두 곳에 있으면 정기 수집본(뒤 폴더)을 쓴다 |
| HS2017 잔존 코드 | collectors/hscode/hscode_outputs/HS2017/hs_code_hs2017.csv |
| HS 판 대응표 | collectors/hscode/hscode_outputs/correlation/HS2022toHS2017.xlsx (UNSD 원본) |
| HS 품목명 | collectors/hscode/reference/hs_names_*.xlsx (관세청 원본) |
| 국가·지역·통화 | db/mysql/init/*country*seed*.sql (저장소의 국가 기준 파일) |
| 키워드 대분류 | data/news_keywords/keyword_dictionary_view.csv |
| 환율 (예외: DB) | fx_daily, country.currency_code — ECOS 수집기가 CSV를 남기지 않아 DB에서 읽는다 |
"""
import json
import logging
import re
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
COLLECTORS = ROOT / "backend" / "app" / "scheduler" / "collectors"
CUSTOMS_DIR = COLLECTORS / "customs" / "customs_outputs"  # 연도별 일괄 수집 (customs_collect)
MONTHLY_DIR = ROOT / "data" / "customs_load_outputs" / "monthly"  # 매월 정기 수집 (jobs/customs_monthly)
CUSTOMS_DIRS = (CUSTOMS_DIR, MONTHLY_DIR)  # 뒤 폴더가 우선
HSCODE_DIR = COLLECTORS / "hscode" / "hscode_outputs"
SEED_DIR = ROOT / "db" / "mysql" / "init"
KEYWORD_CSV = ROOT / "data" / "news_keywords" / "keyword_dictionary_view.csv"
SEED_ROW = re.compile(r"^\('([A-Z0-9]+)','([^']*)','(COUNTRY|SPECIAL|TOTAL)',(\d+|NULL),(?:'([A-Z0-9]+)'|NULL)\)", re.M)


log = logging.getLogger("ml.sources")


def fx_from_db(engine=None):
    """환율만 DB에서 읽는다: (월평균 환율 month·currency_code·rate, 국가별 대표 통화 country_code·currency_code).
    DB에 접속하지 못하면 경고만 남기고 빈 표를 돌려준다 (환율 피처만 결측, 학습은 계속)."""
    empty = (pd.DataFrame(columns=["month", "currency_code", "rate"]), pd.DataFrame(columns=["country_code", "currency_code"]))
    try:
        from sqlalchemy import text
        from backend.app.db.session import make_engine
        engine = engine or make_engine()
        with engine.connect() as conn:
            fx = pd.read_sql(text("""SELECT DATE_FORMAT(base_date, '%Y-%m-01') AS month, currency_code, AVG(rate_krw) AS rate
                                     FROM fx_daily GROUP BY 1, 2"""), conn)
            currencies = pd.read_sql(text("SELECT country_code, currency_code FROM country WHERE currency_code IS NOT NULL"), conn)
        return fx, currencies
    except Exception as exc:  # 접속 정보 없음·DB 중단 등: 환율은 보조 피처라 멈추지 않는다
        log.warning(f"환율을 DB에서 읽지 못해 비워 둡니다: {type(exc).__name__}: {exc}")
        return empty


def countries():
    """국가 시드 SQL의 행: country_code, name_ko, country_role, region_id, currency_code. 뒤 파일이 앞 파일을 덮는다."""
    rows = {}
    for path in sorted(SEED_DIR.glob("*country*seed*.sql")):
        for code, name, role, region, currency in SEED_ROW.findall(path.read_text("utf-8")):
            rows[code] = (code, name, role, None if region == "NULL" else int(region), currency or None)
    return pd.DataFrame(rows.values(), columns=["country_code", "name_ko", "country_role", "region_id", "currency_code"])


def hs_correlation(path=HSCODE_DIR / "correlation" / "HS2022toHS2017.xlsx"):
    """UNSD 대응표 원본에서 DB hs_code_correlation과 같은 묶음 대표 코드(group_code)를 만든다."""
    from backend.app.scheduler.jobs import hs_correlation_load as hcl
    pairs = hcl.parse(Path(path), "HS2022", "HS2017")
    return pd.DataFrame(hcl.to_rows(pairs, "HS2022", "HS2017", Path(path).name))[["new_code", "old_code", "group_code"]]


def hs2017_codes(path=HSCODE_DIR / "HS2017" / "hs_code_hs2017.csv"):
    """관세청 실적에 남아 있는 HS2017 10단위 코드. 나머지 코드는 HS2022로 본다 (DB 적재와 같은 구분)."""
    df = pd.read_csv(path, dtype=str, encoding="utf-8-sig")
    return set(df.loc[df.depth == "10", "hs_code"])


def hs_names():
    """관세청 HS 품목명 원본 xlsx의 {코드: 한글명} (2·4·6·10단위)."""
    from backend.app.scheduler.collectors.hscode import hs_code_build as hb
    return hb.load_names(hb.latest_reference())


def topic_majors(path=KEYWORD_CSV):
    """키워드 사전의 주제어 → 대분류. 대분류가 빈 동의어는 대표어의 대분류를 쓴다."""
    k = pd.read_csv(path, dtype=str, encoding="utf-8-sig", keep_default_na=False)
    major = dict(zip(k["주제어"], k["대분류"]))
    rows = [(topic, major.get(topic) or major.get(canon, "")) for topic, canon in zip(k["주제어"], k["대표어"])]
    return pd.DataFrame([r for r in rows if r[1]], columns=["topic", "major"])


def _collection_stats(csv_path):
    """CSV 옆의 수집 완료 기록. 연도별 일괄 수집은 customs_collection_*.json, 정기 수집은 collection.json."""
    found = sorted(csv_path.parent.glob("customs_collection_*.json")) or         [p for p in (csv_path.parent / "collection.json",) if p.exists()]
    return json.loads(found[0].read_text("utf-8")) if found else None


def _is_complete(stats):
    """실패 국가·빠진 달·거래 없음 미확인 국가가 없고 collection_complete인 기록만 완료로 본다."""
    return bool(stats and stats.get("collection_complete") and not stats.get("failed_countries")
                and not stats.get("missing_months") and not stats.get("unconfirmed_empty_countries"))


def _customs_files(folder):
    """연도별·구형 월별 CSV와 체크포인트가 선택한 정기 수집본. 과거 재수집 폴더는 섞지 않는다."""
    folder = Path(folder)
    files = list(folder.glob("*/customs_raw_*.csv"))
    for month_dir in sorted(folder.glob("*")):
        if not month_dir.is_dir() or not re.fullmatch(r"\d{6}", month_dir.name):
            continue
        state_path = month_dir / "state.json"
        if not state_path.exists():
            if any(month_dir.glob("collection_*/customs_raw_*.csv")):
                raise ValueError(f"정기 수집본을 선택할 state.json이 없습니다: {month_dir}")
            continue  # 이전 형식: 월 폴더 바로 아래의 CSV
        state = json.loads(state_path.read_text("utf-8"))
        collection_dir = state.get("collection_dir") if isinstance(state, dict) else None
        if not isinstance(collection_dir, str) or not re.fullmatch(r"collection_[A-Za-z0-9_]+", collection_dir):
            raise ValueError(f"정기 수집 체크포인트의 collection_dir가 잘못됐습니다: {state_path}")
        if not isinstance(state.get("completed"), list) or "collect" not in state["completed"]:
            raise ValueError(f"정기 수집이 아직 완료되지 않았습니다: {state_path}")
        selected = month_dir / collection_dir / f"customs_raw_{month_dir.name}.csv"
        if not selected.is_file():
            raise FileNotFoundError(f"체크포인트가 가리키는 관세청 CSV가 없습니다: {selected}")
        files = [path for path in files if path.parent != month_dir]
        files.append(selected)
    return sorted(files)


def raw_customs(folders=CUSTOMS_DIRS):
    """관세청 수집 원본 행(총계·형식 오류 행 제외)과 수집 완료가 기록된 달.

    같은 달이 여러 파일에 있으면 뒤 폴더·뒤 파일(최신 수집)의 행만 쓴다. 같은 달을 두 번 더하지 않게 하기 위해서다.
    완료 여부도 실제로 쓴 그 파일의 기록으로만 판단한다.
    """
    files = [f for folder in folders for f in _customs_files(folder)]
    frames = []
    for priority, path in enumerate(files):
        d = pd.read_csv(path, usecols=["year", "statCd", "hsCd", "expDlr", "expWgt", "impDlr"],
                        dtype={"year": str, "statCd": str, "hsCd": str}, encoding="utf-8-sig",
                        keep_default_na=False, na_values=[""])
        d = d[d.year.str.fullmatch(r"\d{4}\.\d{2}", na=False) & d.hsCd.str.fullmatch(r"\d{10}", na=False)]
        for col in ("expDlr", "expWgt", "impDlr"):
            d[col] = pd.to_numeric(d[col], errors="raise")
        bad = ~np.isfinite(d[["expDlr", "impDlr"]].to_numpy(dtype=float)).all(axis=1)
        if bad.any():
            examples = d.loc[bad, ["year", "statCd", "hsCd"]].head(5).to_dict("records")
            raise ValueError(f"관세청 수출입 금액 결측 또는 비유한 값: {path} ({examples})")
        frames.append(d.assign(month=pd.to_datetime(d.year.str.replace(".", "-", regex=False) + "-01"),
                               _priority=priority))
    if not frames:
        raise FileNotFoundError(f"관세청 수집 원본이 없습니다: {[str(f) for f in folders]} 아래 */customs_raw_*.csv")
    raw = pd.concat(frames, ignore_index=True)
    winner = raw.groupby("month")._priority.max()
    raw = raw[raw._priority.to_numpy() == winner.reindex(raw.month).to_numpy()]
    complete = set()
    for priority, path in enumerate(files):
        stats = _collection_stats(path)
        if _is_complete(stats):
            months = {pd.Timestamp(m.replace(".", "-") + "-01") for m in stats.get("months", [])}
            complete |= {m for m in months if winner.get(m) == priority}
    return raw.drop(columns="_priority").reset_index(drop=True), complete


def customs(folders=CUSTOMS_DIRS, country_codes=None, hs2017=None):
    """관세청 수집 원본을 (월, 국가, HS6, 판) 수출·수입 합계로 읽는다.

    exports: month, country_code, hs6, hs_ver, amount, weight, anomaly_amount
    imports: month, country_code, hs6, hs_ver, amount
    complete: 수집 완료가 기록된 달 (그 달에 행이 없는 쌍은 확인된 무역 없음)
    R1(일회성 거래) 보정은 팀에서 임계값을 정하지 않아 적용하지 않는다(anomaly_amount=0, 원금액 사용).
    """
    # ponytail: R1 보정 없음. 임계값이 정해지면 services.customs_metrics.calculate_series로 r1_adjustment를 만든다.
    raw, complete = raw_customs(folders)
    if country_codes is not None:
        raw = raw[raw.statCd.isin(country_codes)]
    hs2017 = hs2017 if hs2017 is not None else set()
    raw = raw.assign(country_code=raw.statCd, hs6=raw.hsCd.str[:6],
                     hs_ver=raw.hsCd.isin(hs2017).map({True: "HS2017", False: "HS2022"}))
    keys = ["month", "country_code", "hs6", "hs_ver"]
    exports = raw.groupby(keys, as_index=False).agg(amount=("expDlr", "sum"), weight=("expWgt", "sum"))
    unknown_weight = pd.MultiIndex.from_frame(raw.loc[raw.expWgt.isna(), keys])
    if len(unknown_weight):
        exports.loc[pd.MultiIndex.from_frame(exports[keys]).isin(unknown_weight), "weight"] = np.nan
    imports = raw[raw.impDlr != 0].groupby(keys, as_index=False).agg(amount=("impDlr", "sum"))
    return exports.assign(anomaly_amount=0.0), imports, complete
