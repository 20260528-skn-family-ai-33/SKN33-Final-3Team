"""수출 유망 시장 모델의 학습 테이블 생성 (docs/ML_PLAN.md 4·5·7장).

한 행 = (국가, HS6, 기준월 t). 피처는 t 시점에 알 수 있는 값만 쓰고, y는 t+1~t+12 실적으로 만든다.
DB·CSV 읽기(load_data)와 계산(build_dataset)을 나눠, 계산은 가상 데이터로 테스트한다.
"""
import numpy as np
import pandas as pd

from backend.app.ml import preprocess as pp
from backend.app.ml.temporal import dated_external, join_known

MIN_EXPORT_USD = 100_000  # 최근 12개월 수출이 이보다 작은 쌍은 노이즈라 뺀다
HISTORY = 24  # growth_12m(최근 12개월 vs 직전 12개월)에 필요한 과거 월 수
HORIZON = 12  # y를 보는 미래 월 수
CATEGORICAL = ["region_id", "hs2"]
FEATURES = [
    "log_exp_12m", "growth_12m", "growth_3m", "active_months", "cv_12m", "anomaly_ratio", "unit_trend",
    "share_in_country", "share_in_product", "country_growth", "product_growth", "product_countries",
    "fx_change_12m", "fx_vol_12m",
    "log_gdp", "gdp_growth", "log_population", "log_gdp_pc",
    "mkt_log_import", "mkt_growth", "kor_share", "kor_share_change",
    "news_3m", "news_all_3m", "news_change",
    *CATEGORICAL,
]
TYPE_NAMES = {0: "고성장·선점형", 1: "고성장·진입형", 2: "성숙·유지형", 3: "정체·축소형", 4: "변동·일회성형"}


def hs_key_map(corr):
    """(판, 6단위 코드) → 비교용 HS2022 코드. 판이 바뀌며 나뉘거나 합쳐진 코드는 대응 묶음 대표 코드(group_code)로 모은다.
    corr: hs_code_correlation의 HS2022↔HS2017 행 (new_code, old_code, group_code)."""
    mapping = {("HS2022", r.new_code): r.group_code for r in corr.itertuples()}
    mapping.update({("HS2017", r.old_code): r.group_code for r in corr.itertuples()})
    return mapping


def apply_hs_key(df, mapping, ver_col="hs_ver", code_col="hs6"):
    keys = [mapping.get((v, c), c) for v, c in zip(df[ver_col], df[code_col])]
    return df.assign(**{code_col: keys}).drop(columns=ver_col)


def load_data(engine=None, external_dir=None):
    """DB 실적·기준정보와 data/external CSV를 build_dataset 입력 형식으로 읽는다."""
    from sqlalchemy import text
    from backend.app.db.session import make_engine
    engine = engine or make_engine()
    with engine.connect() as conn:
        read = lambda sql: pd.read_sql(text(sql), conn)  # noqa: E731
        # R1은 다음 달 자료가 필요하다. 원금액과 보정량을 분리하고 기준월별로 적용한다.
        exports = read("""
            SELECT m.trade_month AS month, m.country_code, m.hs6_code AS hs6, m.hs_ver,
                   SUM(m.amount_usd) AS amount,
                   SUM(CASE WHEN m.anomaly_code LIKE 'R1%' AND m.baseline_amount_usd IS NOT NULL
                            THEN m.baseline_amount_usd - m.amount_usd ELSE 0 END) AS r1_adjustment,
                   SUM(CASE WHEN m.anomaly_code = 'R1' THEN m.amount_usd ELSE 0 END) AS r1_only_amount,
                   SUM(CASE WHEN m.anomaly_code NOT IN ('NONE') THEN m.amount_usd ELSE 0 END) AS anomaly_amount,
                   SUM(m.weight_kg) AS weight
            FROM export_monthly m JOIN country c ON c.country_code = m.country_code
            WHERE c.country_role = 'COUNTRY'
            GROUP BY 1, 2, 3, 4""")
        corr = read("SELECT new_code, old_code, group_code FROM hs_code_correlation"
                    " WHERE new_ver = 'HS2022' AND old_ver = 'HS2017'")
        countries = read("SELECT country_code, region_id, currency_code FROM country WHERE country_role = 'COUNTRY'")
        fx = read("""SELECT DATE_FORMAT(base_date, '%Y-%m-01') AS month, currency_code, AVG(rate_krw) AS rate
                     FROM fx_daily GROUP BY 1, 2""")
        news = read("""SELECT DATE_FORMAT(metric_date, '%Y-%m-01') AS month, hs_code AS hs6, hs_ver, country_key AS country_code,
                              SUM(day_count) AS count
                       FROM news_product_daily WHERE CHAR_LENGTH(hs_code) = 6 GROUP BY 1, 2, 3, 4""")
    mapping = hs_key_map(corr)
    exports = apply_hs_key(exports, mapping).groupby(["month", "country_code", "hs6"], as_index=False).agg(
        lambda values: values.sum(skipna=False))
    news = apply_hs_key(news, mapping).groupby(["month", "country_code", "hs6"], as_index=False).sum()
    external = pp.load_external(external_dir, set(countries.country_code), mapping)
    return {"exports": exports, "countries": countries, "fx": fx, "news": news, **external}


def _month_index(series, months):
    return pd.PeriodIndex(pd.to_datetime(series), freq="M").map({m: i for i, m in enumerate(months)})


def _matrix(df, value, rows, months):
    """(국가, HS6) × 월 행렬. 행 부재·원본 결측은 미관측(NaN)으로 보존한다."""
    out = np.full((len(rows), len(months)), np.nan, dtype=np.float64)
    r = rows.get_indexer(pd.MultiIndex.from_arrays([df.country_code, df.hs6]))
    out[r, _month_index(df.month, months)] = pd.to_numeric(df[value], errors="raise").to_numpy(dtype=float)
    return out


def _require_observed(matrix, rows, months, column):
    """누락을 무역 없음으로 바꾸거나 일부 후보를 조용히 제외하지 않는다."""
    missing = np.argwhere(~np.isfinite(matrix))
    if len(missing):
        examples = ", ".join(f"{rows[r][0]}/{rows[r][1]}/{months[m]}" for r, m in missing[:5])
        raise ValueError(f"수출 실적 미관측 또는 비유한 값: {column} {len(missing)}건 ({examples}). "
                         "수집 범위·완료 여부를 확인하세요. 확인된 무역 없음만 명시적인 0 행으로 제공해야 합니다.")


def _window(a, end, length):
    """end월(포함)까지 length개월 합계. end는 열 번호."""
    return a[:, end - length + 1:end + 1].sum(axis=1)


def _ratio(num, den):
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(den > 0, num / den, np.nan)


def _growth(now, before):
    return np.log1p(now) - np.log1p(before)


def _group_sum(values, groups):
    """같은 그룹(국가 또는 품목)끼리 더한 값을 각 행에 돌려준다."""
    codes, inverse = np.unique(groups, return_inverse=True)
    return np.bincount(inverse, weights=values, minlength=len(codes))[inverse]


def build_dataset(data, min_export=MIN_EXPORT_USD):
    """관측 범위 내부 누락은 오류. 범위 이후의 미래 실적이 없는 최근 기준월은 y=NaN(예측 대상)."""
    exports = data["exports"]
    if exports.empty:
        raise ValueError("수출 실적이 없습니다")
    monthly_keys = exports[["country_code", "hs6"]].assign(
        month=pd.to_datetime(exports.month).dt.to_period("M"))
    if monthly_keys.isna().any().any() or monthly_keys.duplicated().any():
        raise ValueError("수출 실적의 국가·HS6·월 키에 결측 또는 중복이 있습니다")
    months = pd.period_range(pd.to_datetime(exports.month).min(), pd.to_datetime(exports.month).max(), freq="M")
    rows = pd.MultiIndex.from_frame(exports[["country_code", "hs6"]].drop_duplicates())
    amount, anomaly, weight = (_matrix(exports, col, rows, months) for col in ("amount", "anomaly_amount", "weight"))
    _require_observed(amount, rows, months, "amount")
    adjustment = _matrix(exports, "r1_adjustment", rows, months) if "r1_adjustment" in exports else np.zeros_like(amount)
    if "r1_adjustment" in exports:
        _require_observed(adjustment, rows, months, "r1_adjustment")
    r1_only = _matrix(exports, "r1_only_amount", rows, months) if "r1_only_amount" in exports else np.zeros_like(amount)
    corrected = amount + adjustment
    country = rows.get_level_values(0).to_numpy()
    hs6 = rows.get_level_values(1).to_numpy()
    frames = []
    for t in range(HISTORY - 1, len(months)):
        # t월 R1은 t+1월에야 확정된다. t 이전 월만 보정하고 t월은 원금액을 쓴다.
        s12 = _window(corrected, t, 12) - adjustment[:, t]
        p12 = _window(corrected, t - 12, 12)
        keep = s12 >= min_export
        if not keep.any():
            continue
        last12 = corrected[:, t - 11:t + 1].copy()
        last12[:, -1] = amount[:, t]
        a6, w6 = _window(corrected, t, 6) - adjustment[:, t], _window(weight, t, 6)
        pa6, pw6 = _window(corrected, t - 6, 6), _window(weight, t - 6, 6)
        c12, cp12 = _group_sum(s12, country), _group_sum(p12, country)
        h12, hp12 = _group_sum(s12, hs6), _group_sum(p12, hs6)
        frame = pd.DataFrame({
            "anchor": months[t].to_timestamp(), "country_code": country, "hs6": hs6,
            "exp_12m": s12,
            "log_exp_12m": np.log1p(s12),
            "growth_12m": _growth(s12, p12),
            "growth_3m": _growth(_window(corrected, t, 3) - adjustment[:, t], _window(corrected, t - 3, 3)),
            "active_months": (last12 > 0).sum(axis=1),
            "cv_12m": _ratio(last12.std(axis=1), last12.mean(axis=1)),
            "anomaly_ratio": _ratio(_window(anomaly, t, 12) - r1_only[:, t], s12),
            "unit_trend": np.log(_ratio(_ratio(a6, w6), _ratio(pa6, pw6))),
            "share_in_country": _ratio(s12, c12),
            "share_in_product": _ratio(s12, h12),
            "country_growth": _growth(c12, cp12),
            "product_growth": _growth(h12, hp12),
            "product_countries": _group_sum((s12 > 0).astype(float), hs6),
        })
        if t + HORIZON < len(months):
            future = corrected[:, t + 1:t + 1 + HORIZON].copy()
            # 정답도 미래 12개월의 마지막 월 당시 확정 가능한 R1까지만 보정한다.
            future[:, -1] = amount[:, t + HORIZON]
            frame["y_growth"] = _growth(future.sum(axis=1), s12)
            frame["future_cv"] = _ratio(future.std(axis=1), future.mean(axis=1))
        else:
            frame["y_growth"] = frame["future_cv"] = np.nan
        frames.append(frame[keep])
    if not frames:
        raise ValueError(f"학습 행이 없음: 실적이 최소 {HISTORY}개월 필요 (현재 {len(months)}개월)")
    ds = pd.concat(frames, ignore_index=True)
    ds["hs2"] = ds.hs6.str[:2].astype(int)
    ds = ds.merge(data["countries"][["country_code", "region_id"]], on="country_code", how="left")
    ds = _add_fx(ds, data["fx"], data["countries"])
    ds = _add_wdi(ds, data["wdi_yearly"], data.get("availability"))
    ds = _add_market(ds, data["market_yearly"], data.get("availability"))
    return _add_news(ds, data["news"])


def _add_fx(ds, fx, countries):
    """국가 대표 통화의 월평균 환율 12개월 변화율과 월 변화율의 표준편차. 통화가 없으면 NaN."""
    if fx.empty:
        return ds.assign(fx_change_12m=np.nan, fx_vol_12m=np.nan)
    rate = fx.assign(month=pd.to_datetime(fx.month)).pivot_table(index="month", columns="currency_code", values="rate")
    rate = rate.asfreq("MS")
    change = rate / rate.shift(12) - 1
    vol = rate.pct_change(fill_method=None).rolling(12, min_periods=6).std()
    stack = lambda df, name: df.stack().rename(name).reset_index()  # noqa: E731
    fx_feat = stack(change, "fx_change_12m").merge(stack(vol, "fx_vol_12m"), on=["month", "currency_code"], how="outer")
    cur = countries[["country_code", "currency_code"]]
    return (ds.merge(cur, on="country_code", how="left")
              .merge(fx_feat, left_on=["anchor", "currency_code"], right_on=["month", "currency_code"], how="left")
              .drop(columns=["currency_code", "month"]))


def _add_wdi(ds, wdi_yearly, availability=None):
    w = dated_external(wdi_yearly, availability, "wdi")
    return join_known(ds, w, ["country_code"], ["log_gdp", "gdp_growth", "log_population", "log_gdp_pc"])


def _add_market(ds, market_yearly, availability=None):
    """공개된 시장 피처와 미래 정답을 구분한다. 미래 정답의 사용 가능일도 보존한다."""
    keys = ["year", *pp.MARKET_KEYS]
    dated = dated_external(market_yearly, availability, "market")
    ds = join_known(ds, dated, pp.MARKET_KEYS, ["mkt_log_import", "mkt_growth", "kor_share", "kor_share_change"])
    # ponytail: 미래 12개월을 연 단위로 근사 (기준월+6개월이 속한 해 vs 그 전해). 월별 시장 데이터가 생기면 바꾼다.
    future = dated[[*keys, "mkt_growth", "kor_share", "available_from"]].rename(
        columns={"mkt_growth": "future_mkt_growth", "kor_share": "future_share",
                 "available_from": "type_available_from"})
    ds["year"] = (ds.anchor + pd.DateOffset(months=6)).dt.year
    return ds.merge(future, on=keys, how="left").drop(columns="year")


def _add_news(ds, news):
    """최근 3개월(≈90일) 기사 수: 국가·품목별, 품목 전체(*), 직전 3개월 대비 증감."""
    if news.empty:
        return ds.assign(news_3m=0.0, news_all_3m=0.0, news_change=0.0)
    n = news.assign(month=pd.to_datetime(news.month)).pivot_table(
        index="month", columns=["country_code", "hs6"], values="count", aggfunc="sum")
    # 마지막 기사 이후에도 기준월까지 0개월을 이어야 최근/직전 3개월 창이 유지된다.
    start = min(n.index.min(), pd.Timestamp(ds.anchor.min()) - pd.DateOffset(months=5))
    n = n.reindex(pd.date_range(start, ds.anchor.max(), freq="MS", name="month")).fillna(0)
    now, before = n.rolling(3, min_periods=1).sum(), n.shift(3).rolling(3, min_periods=1).sum()
    long = lambda df, name: df.stack(["country_code", "hs6"], future_stack=True).rename(name).reset_index()  # noqa: E731
    feat = long(now, "news_3m").merge(long(before, "news_prev_3m"), on=["month", "country_code", "hs6"])
    pair = feat[feat.country_code != "*"]
    allc = feat[feat.country_code == "*"].drop(columns=["country_code", "news_prev_3m"]).rename(
        columns={"news_3m": "news_all_3m"})
    ds = (ds.merge(pair, left_on=["anchor", "country_code", "hs6"], right_on=["month", "country_code", "hs6"], how="left")
            .drop(columns="month")
            .merge(allc, left_on=["anchor", "hs6"], right_on=["month", "hs6"], how="left").drop(columns="month"))
    ds[["news_3m", "news_prev_3m", "news_all_3m"]] = ds[["news_3m", "news_prev_3m", "news_all_3m"]].fillna(0)
    ds["news_change"] = _growth(ds.news_3m, ds.news_prev_3m)
    return ds.drop(columns="news_prev_3m")


def type_thresholds(rows):
    """시장 유형 기준값. 학습 행의 분포로 정한다 (시장 성장·점유율 상위 40%, 변동성 상위 20%)."""
    return {"mkt_growth_hi": float(rows.future_mkt_growth.quantile(0.6)),
            "share_hi": float(rows.future_share.quantile(0.6)),
            "cv_hi": float(rows.future_cv.quantile(0.8))}


def market_type(kor_growth, mkt_growth, share, cv, th):
    """docs/ML_PLAN.md 7장 규칙. 입력에 NaN이 있으면 NaN(라벨 없음)."""
    kor_growth, mkt_growth, share, cv = (np.asarray(v, dtype=float) for v in (kor_growth, mkt_growth, share, cv))
    if not all(np.isfinite(v) for v in th.values()):
        return np.full(kor_growth.shape, np.nan)
    label = np.select(
        [cv >= th["cv_hi"],
         (mkt_growth >= th["mkt_growth_hi"]) & (share >= th["share_hi"]),
         mkt_growth >= th["mkt_growth_hi"],
         (mkt_growth < 0) & (kor_growth < 0)],
        [4, 0, 1, 3], default=2).astype(float)
    label[np.isnan(kor_growth) | np.isnan(mkt_growth) | np.isnan(share) | np.isnan(cv)] = np.nan
    return label


def future_type(ds, th):
    """라벨: 미래 12개월 실적에 규칙을 적용한 유형."""
    return market_type(ds.y_growth, ds.future_mkt_growth, ds.future_share, ds.future_cv, th)


def past_type(ds, th):
    """기준선: 같은 규칙을 지난 12개월 실적에 적용한 유형 (모델 없이 지금 상태로 판단)."""
    return market_type(ds.growth_12m, ds.mkt_growth, ds.kor_share, ds.cv_12m, th)

