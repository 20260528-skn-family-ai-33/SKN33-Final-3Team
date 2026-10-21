"""외부 자료를 실제 사용 가능 시점 이후에만 연결한다.

availability.csv: dataset(market/wdi), source(BACI/COMTRADE/WDI), year,
available_from. available_from은 현재 파일의 해당 값/버전이 사용 가능했던
날짜다. 원래 연간 통계의 최초 발표일로 수정본의 사용 가능일을 대신하지 않는다.
"""
import pandas as pd


def dated_external(frame, availability, dataset):
    if frame.empty:
        return frame.assign(available_from=pd.Series(dtype="datetime64[ns]"))
    if availability is None:
        raise ValueError("외부 자료의 공개·버전 시점 정보가 필요합니다: data/external/availability.csv")
    required = {"dataset", "source", "year", "available_from"}
    if not required <= set(availability.columns):
        raise ValueError(f"availability 컬럼 누락: {sorted(required - set(availability.columns))}")
    dates = availability[availability.dataset == dataset].copy()
    keys = ["source", "year"]
    if dates.duplicated(keys).any():
        raise ValueError(f"{dataset} availability 중복")
    dates["available_from"] = pd.to_datetime(dates.available_from, errors="raise")
    if dates.available_from.isna().any():
        raise ValueError(f"{dataset} 사용 가능일 누락")
    values = frame.copy()
    if dataset == "wdi":
        values["source"] = "WDI"
    out = values.merge(dates[[*keys, "available_from"]], on=keys, how="left", validate="many_to_one")
    if out.available_from.isna().any():
        raise ValueError(f"{dataset} 일부 연도·출처의 사용 가능일 누락")
    # 전년 대비 변화와 WDI 전년값 보충은 전년 자료의 버전 시점에도 의존한다.
    row_keys = ["country_code", "hs6"] if dataset == "market" else ["country_code"]
    previous = out[["year", *row_keys, "available_from"]].copy()
    previous["year"] += 1
    previous = previous.rename(columns={"available_from": "previous_available_from"})
    out = out.merge(previous, on=["year", *row_keys], how="left", validate="one_to_one")
    needs_previous = out.mkt_growth.notna() if dataset == "market" else out.filled
    if (needs_previous & out.previous_available_from.isna()).any():
        raise ValueError(f"{dataset} 전년 파생값의 사용 가능일 누락")
    out.loc[needs_previous, "available_from"] = out.loc[
        needs_previous, ["available_from", "previous_available_from"]].max(axis=1)
    out = out.drop(columns="previous_available_from")
    return out


def join_known(ds, values, keys, columns):
    """기준월 말까지 사용 가능한 가장 최근 연도의 행을 연결한다."""
    pieces = []
    for anchor, rows in ds.groupby("anchor", sort=False):
        cutoff = pd.Timestamp(anchor) + pd.offsets.MonthEnd(0)
        eligible = values[values.available_from <= cutoff]
        latest = eligible.sort_values(["year", "available_from"]).drop_duplicates(keys, keep="last")
        pieces.append(rows.merge(latest[[*keys, *columns]], on=keys, how="left", validate="many_to_one"))
    return pd.concat(pieces, ignore_index=True) if pieces else ds.assign(**{c: float("nan") for c in columns})
