"""실험용 피처 복구. 호출할 때만 파일을 읽으며 원본·운영 모델을 수정하지 않는다.

외부 통계는 현재 CSV 또는 사용자가 준비한 과거 버전 피처 표를 시점에 맞게 연결한다.
환율 CSV는 월평균·해당 버전의 사용 가능일·근거를 요구한다. 임의 소급/역방향 보간 없음.
"""
from pathlib import Path
import hashlib

import numpy as np
import pandas as pd

from backend.app.ml import features as F, preprocess as P, sources as S
from backend.app.ml.temporal import dated_external

MARKET = ["mkt_log_import", "mkt_growth", "kor_share", "kor_share_change", "mkt_hs4_growth"]
WDI = ["log_gdp", "gdp_growth", "log_population", "log_gdp_pc"]
FX = ["fx_change_12m", "fx_vol_12m"]
GROUPS = {"market": MARKET, "wdi": WDI, "fx": FX}


def file_info(path):
    path = Path(path).resolve()
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def coverage(frame, columns=F.FEATURES):
    """값 존재와 유효 변동을 구분한다. train/test 각각 호출해 확인한다."""
    rows = []
    for col in columns:
        values = frame[col] if col in frame else pd.Series(np.nan, index=frame.index)
        numeric = pd.to_numeric(values, errors="raise")
        valid = numeric.replace([np.inf, -np.inf], np.nan)
        n = int(valid.notna().sum())
        unique = int(valid.nunique())
        rows.append({"feature": col, "rows": len(frame), "observed": n,
                     "coverage": n / len(frame) if len(frame) else None,
                     "unique": unique, "status": "all_missing" if not n else "constant" if unique == 1 else "available"})
    return pd.DataFrame(rows)


def _history(path, columns, keys):
    frame = pd.read_csv(path, dtype={"country_code": str, "hs6": str},
                        keep_default_na=False, na_values=[""])
    required = {*keys, "year", "available_from", "source", "evidence", *columns}
    if required - set(frame):
        raise ValueError(f"과거 버전 피처 표에 필요한 열: {sorted(required - set(frame))}")
    if frame[[*keys, "source", "evidence"]].isna().any().any():
        raise ValueError("과거 버전의 키·출처·공개/버전 근거를 모두 기록하세요")
    for col in ["source", "evidence"]:
        if frame[col].astype(str).str.strip().eq("").any():
            raise ValueError(f"빈 {col}은 사용 가능일 근거가 아닙니다")
    frame["year"] = pd.to_numeric(frame.year, errors="raise")
    if (frame.year % 1 != 0).any():
        raise ValueError("year는 정수여야 합니다")
    frame["year"] = frame.year.astype(int)
    frame["available_from"] = pd.to_datetime(frame.available_from, errors="raise")
    if frame.available_from.isna().any():
        raise ValueError("available_from 누락")
    if (frame.available_from < pd.to_datetime((frame.year + 1).astype(str) + "-01-01")).any():
        raise ValueError("완료된 연간 값의 사용 가능일이 통계 연도 종료 전입니다")
    if frame.duplicated([*keys, "year", "available_from"]).any():
        raise ValueError("동일 키·연도·사용 가능일의 중복 버전을 정리하세요")
    frame[columns] = frame[columns].apply(pd.to_numeric, errors="raise")
    if np.isinf(frame[columns].to_numpy(dtype=float)).any():
        raise ValueError("외부 피처에 무한대가 있습니다")
    return frame


def _join(frame, values, keys, columns):
    """입력 행 순서/키/정답을 보존한다. 그 시점 최신 통계 연도와 최신 버전을 선택.

    173만 행 표를 통째로 복사하면 8GB PC에서 메모리가 모자라므로, 키 열만으로 값을 찾아 얕은 사본에 열만 바꿔 넣는다.
    """
    small = frame[["anchor", *keys]].assign(_pos=np.arange(len(frame)))
    pieces = []
    for anchor, rows in small.groupby("anchor", sort=False):
        cutoff = pd.Timestamp(anchor) + pd.offsets.MonthEnd(0)
        eligible = values[(values.available_from <= cutoff) & (values.year <= cutoff.year)]
        latest = eligible.sort_values(["year", "available_from"]).drop_duplicates(keys, keep="last")
        pieces.append(rows.merge(latest[[*keys, *columns]], on=keys, how="left", validate="many_to_one")[["_pos", *columns]])
    joined = (pd.concat(pieces).set_index("_pos").reindex(np.arange(len(frame))) if pieces
              else pd.DataFrame(np.nan, index=np.arange(len(frame)), columns=columns))
    result = frame.copy(deep=False)
    for col in columns:
        result[col] = joined[col].to_numpy(dtype=float)
    result.attrs = dict(frame.attrs)
    return result


def _availability_override(path):
    """민감도 실험용 공개일표. 근거(evidence)가 없는 날짜는 받지 않는다."""
    frame = pd.read_csv(path, keep_default_na=False, na_values=[""])
    required = {"dataset", "source", "year", "available_from", "evidence"}
    if required - set(frame):
        raise ValueError(f"공개일표 필수 열: {sorted(required)}")
    if (frame.evidence.isna() | frame.evidence.astype(str).str.strip().eq("")).any():
        raise ValueError("공개일표의 모든 행에 evidence(공개 주기·출처 등 근거)를 적으세요")
    year_end = pd.to_datetime((frame.year.astype(int) + 1).astype(str) + "-01-01")
    if (pd.to_datetime(frame.available_from, errors="raise") < year_end).any():
        raise ValueError("연간 통계의 공개일이 그 해가 끝나기 전입니다")
    return frame


def recover_external(frame, external_dir=None, market_history=None, wdi_history=None, availability=None):
    """기존 9개 외부 피처를 다시 연결한다. 복구된 값 0개도 정직하게 보고한다.

    *_history: 전처리된 과거 버전 CSV (reference/experiments/*_history_template.csv).
    생략한 자료는 external_dir의 현재 원본+availability.csv를 기존 전처리로 읽는다.
    현재 2026-10-22 스냅샷으로 과거 2025년 평가 값을 채우지 않는다.

    availability: 현재 원본에 붙일 다른 공개일표 (reference/experiments/availability_release_estimate.csv).
    현재 값(나중에 수정된 값)을 최초 공개일에 쓴다고 가정하는 **민감도 실험**이다. 수정 반영분만큼 낙관적이므로
    "과거 판을 구해 올 가치가 있나"를 가늠하는 데만 쓰고, Holdout·운영 채택에는 쓰지 않는다.
    """
    before = coverage(frame, MARKET + WDI)
    inputs = []
    external = None
    if market_history is None or wdi_history is None:
        root = Path(external_dir or S.ROOT / "data/external")
        countries = S.countries()
        external = P.load_external(root, set(countries.loc[countries.country_role == "COUNTRY", "country_code"]),
                                   F.hs_key_map(S.hs_correlation()))
        if availability is not None:
            external["availability"] = _availability_override(availability)
        paths = [Path(availability) if availability is not None else root / "availability.csv",
                 root / "baci/baci_imports_hs22.csv"]
        paths += list((root / "comtrade").glob("comtrade_imports_*.csv"))
        paths += sorted((root / "wdi").glob("wdi_*.csv"))[-1:]
        paths += [S.HSCODE_DIR / "correlation/HS2022toHS2017.xlsx"]
        paths += list(S.SEED_DIR.glob("*country*seed*.sql"))
        inputs += [file_info(p) for p in paths if p.is_file()]
    joins = []
    for name, columns, keys, history in [("market", MARKET, ["country_code", "hs6"], market_history),
                                         ("wdi", WDI, ["country_code"], wdi_history)]:
        if history is not None:
            values = _history(history, columns, keys)
            inputs.append(file_info(history))
        else:
            values = dated_external(external[name + "_yearly"], external["availability"], name)
        joins.append((values, keys, columns))
    external = None  # 원본 외부 표는 여기서 놓아 메모리를 돌려준다
    result = frame
    for values, keys, columns in joins:
        result = _join(result, values, keys, columns)
    audit = before.merge(coverage(result, MARKET + WDI), on="feature", suffixes=("_before", "_after"))
    audit["observed_delta"] = audit.observed_after - audit.observed_before
    policy = "available_from_at_or_before_anchor_end"
    if availability is not None and (market_history is None or wdi_history is None):
        policy += "; sensitivity_current_values_at_estimated_release (수정값 선반영, Holdout·운영 사용 금지)"
    result.attrs["recovery"] = [*frame.attrs.get("recovery", []),
                                {"kind": "external", "policy": policy,
                                 "inputs": inputs, "coverage": audit.to_dict("records")}]
    return result, audit


def recover_fx(frame, monthly_csv, currency_csv=None):
    """DB에 접속하지 않고 근거가 있는 월평균 환율 CSV로 2개 피처 재계산.

    필수: month,currency_code,rate,quote_unit,available_from,evidence.
    같은 월의 수정 버전은 available_from을 달리하여 보관할 수 있다.
    currency_csv 생략 시 현재 국가 시드를 쓰며 그 한계를 이력에 기록한다.
    """
    fx = pd.read_csv(monthly_csv, dtype={"currency_code": str}, keep_default_na=False, na_values=[""])
    required = {"month", "currency_code", "rate", "quote_unit", "available_from", "evidence"}
    if required - set(fx):
        raise ValueError(f"환율 CSV 필수 열: {sorted(required)}")
    if fx[list(required)].isna().any().any() or fx.evidence.astype(str).str.strip().eq("").any():
        raise ValueError("환율 값·키·단위·시점·근거에 결측이 있습니다")
    fx["month"] = pd.to_datetime(fx.month, errors="raise").dt.to_period("M").dt.to_timestamp()
    fx["available_from"] = pd.to_datetime(fx.available_from, errors="raise")
    if (fx.available_from < fx.month + pd.offsets.MonthEnd(0)).any():
        raise ValueError("월 전체 평균의 사용 가능일이 해당 월 말 이전입니다")
    for col in ["rate", "quote_unit"]:
        fx[col] = pd.to_numeric(fx[col], errors="raise")
        if (~np.isfinite(fx[col]) | (fx[col] <= 0)).any():
            raise ValueError(f"환율 {col}은 양수 유한 값이어야 합니다")
    if fx.duplicated(["month", "currency_code", "available_from"]).any():
        raise ValueError("환율 월·통화·버전 시점 중복")
    # 통화별 quote_unit 변경도 같은 단위로 정규화해 인위적 변화율을 방지한다.
    fx["rate"] = fx.rate / fx.quote_unit
    if currency_csv is None:
        currencies = S.countries()[["country_code", "currency_code"]]
        inputs = [file_info(p) for p in S.SEED_DIR.glob("*country*seed*.sql")]
    else:
        currencies = pd.read_csv(currency_csv, dtype=str, keep_default_na=False, na_values=[""])
        inputs = [file_info(currency_csv)]
    if not {"country_code", "currency_code"} <= set(currencies):
        raise ValueError("국가 통화 표는 country_code,currency_code가 필요합니다")
    if currencies.country_code.isna().any() or currencies.country_code.duplicated().any():
        raise ValueError("국가 통화 표의 국가 키 결측/중복")
    pieces = []
    base = frame.drop(columns=FX, errors="ignore").copy()
    base["_recovery_order"] = np.arange(len(base))
    for anchor, rows in base.groupby("anchor", sort=False):
        end = pd.Timestamp(anchor) + pd.offsets.MonthEnd(0)
        known = fx[(fx.available_from <= end) & (fx.month <= anchor)]
        known = known.sort_values("available_from").drop_duplicates(["month", "currency_code"], keep="last")
        pieces.append(F._add_fx(rows, known[["month", "currency_code", "rate"]], currencies))
    if not pieces:
        raise ValueError("환율을 연결할 피처 행이 없습니다")
    result = pd.concat(pieces).sort_values("_recovery_order").drop(columns="_recovery_order")
    result.index = frame.index
    result.attrs = dict(frame.attrs)
    audit = coverage(frame, FX).merge(coverage(result, FX), on="feature", suffixes=("_before", "_after"))
    audit["observed_delta"] = audit.observed_after - audit.observed_before
    result.attrs["recovery"] = [*frame.attrs.get("recovery", []),
                                {"kind": "fx", "inputs": [file_info(monthly_csv), *inputs],
                                 "policy": "available_from_at_or_before_anchor_end",
                                 "currency_mapping": "static_current_mapping_not_historical_membership",
                                 "coverage": audit.to_dict("records")}]
    return result, audit
