"""수출 유망 시장 모델의 학습 테이블 생성 (docs/ML_PLAN.md 4·5·7장).

한 행 = (국가, HS6, 기준월 t). 피처는 t 시점에 알 수 있는 값만 쓰고, y는 t+1~t+12 실적으로 만든다.
DB·CSV 읽기(load_data)와 계산(build_dataset)을 나눠, 계산은 가상 데이터로 테스트한다.
"""
import ast
from pathlib import Path

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
    # 관련 품목: 같은 품목군 수출(자기 품목 제외)과 한국의 수입
    "grp4_c_growth_12m", "grp4_c_growth_3m", "grp4_world_growth_12m", "grp2_c_growth_12m", "hs4_unit_trend",
    "imp_hs6_world_growth", "imp_hs4_world_growth", "imp_hs4_from_c_growth", "imp_exp_ratio_hs4",
    "fx_change_12m", "fx_vol_12m",
    "log_gdp", "gdp_growth", "log_population", "log_gdp_pc",
    "mkt_log_import", "mkt_growth", "kor_share", "kor_share_change", "mkt_hs4_growth",
    "news_3m", "news_all_3m", "news_change",
    "news_country_3m", "news_trade_3m", "news_market_3m", "news_invest_3m",
    *CATEGORICAL,
]
SHORTNEWS_DIR = Path(__file__).resolve().parents[1] / "scheduler" / "collectors" / "shortnews" / "shortnews_outputs"
# 단신속보 국가명 중 country.name_ko와 표기가 다른 것
NEWS_COUNTRY_ALIASES = {"도미니카공화국": "DO", "러시아연방": "RU", "보스니아 헤르체고비나": "BA", "부르키나파소": "BF",
                        "아랍에미리트": "AE", "에콰도르": "EC", "체코": "CZ", "포르투갈": "PT"}
# 국가별로 세는 기사 토픽 대분류 (keyword_category.major_name) → 피처 이름
NEWS_TOPIC_FEATURES = {"무역·통상": "news_trade_3m", "경제·시장": "news_market_3m", "기업·투자·진출": "news_invest_3m"}
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


def load_data(external_dir=None, customs_dirs=None):
    """수집기가 내려받은 CSV만 읽어 build_dataset 입력을 만든다 (DB를 읽지 않는다). 파일 위치는 ml/sources.py."""
    from backend.app.ml import sources as S
    country_table = S.countries()
    countries = country_table[country_table.country_role == "COUNTRY"][["country_code", "region_id", "currency_code"]]
    mapping = hs_key_map(S.hs_correlation())
    exports, imports, complete = S.customs(customs_dirs or S.CUSTOMS_DIRS, set(countries.country_code), S.hs2017_codes())
    exports = apply_hs_key(exports, mapping).groupby(["month", "country_code", "hs6"], as_index=False).sum()
    imports = apply_hs_key(imports, mapping).groupby(["month", "country_code", "hs6"], as_index=False).sum()
    # 환율만 예외로 DB(fx_daily, country.currency_code)에서 읽는다. ECOS 수집기가 CSV를 남기지 않기 때문이다.
    fx, currencies = S.fx_from_db()
    countries = countries.drop(columns="currency_code").merge(currencies, on="country_code", how="left")
    news, news_topics, news_start = load_shortnews(SHORTNEWS_DIR, country_table[["country_code", "name_ko"]],
                                                   S.topic_majors())
    external = pp.load_external(external_dir, set(countries.country_code), mapping)
    return {"exports": exports, "imports": imports, "countries": countries, "fx": fx,
            "complete_months": complete,
            "news": news, "news_topics": news_topics, "news_start": news_start, **external}


def _as_list(value):
    if isinstance(value, list):
        return value
    try:
        parsed = ast.literal_eval(value) if isinstance(value, str) and value else []
    except (ValueError, SyntaxError):
        return []
    return parsed if isinstance(parsed, list) else []


def load_shortnews(folder, country_names, topic_major):
    """단신속보 수집 결과(원본 CSV·HS 태깅·토픽)를 월별 기사 수로 센다. DB 적재를 기다리지 않는다.

    news: month, country_code('*'는 전체 국가), hs4, count — 기사에 태깅된 HS4별 고유 기사 수
    news_topics: month, country_code, major, count — 국가별 토픽 대분류 기사 수 ('*' 대분류는 전체 기사)
    news_start: 수집한 기사의 첫 게시 월. 이보다 앞선 달이 창에 들어가는 기준월은 기사 피처를 결측으로 둔다.
    """
    empty = (pd.DataFrame(columns=["month", "country_code", "hs4", "count"]),
             pd.DataFrame(columns=["month", "country_code", "major", "count"]), None)
    raws = sorted(Path(folder).glob("*/shortnews_raw.csv"))
    if not raws:
        return empty
    raw = pd.read_csv(raws[-1], usecols=["nttSn", "nat", "othbcDt"], dtype=str, keep_default_na=False, na_values=[""])
    to_code = {**dict(zip(country_names.name_ko, country_names.country_code)), **NEWS_COUNTRY_ALIASES}
    articles = pd.DataFrame({"id": raw.nttSn, "country_code": raw.nat.map(to_code),
                             "month": pd.to_datetime(raw.othbcDt, errors="coerce").dt.to_period("M").dt.to_timestamp()})
    articles = articles.dropna(subset=["month"]).drop_duplicates("id")
    news = empty[0]
    analysis = Path(folder) / "short_news_analysis.jsonl"
    if analysis.exists():
        an = pd.read_json(analysis, lines=True, dtype={"external_id": str})
        tags = an.assign(hs4=an.hs_codes.map(lambda v: sorted({str(h)[:4] for h in _as_list(v)})))[["external_id", "hs4"]]
        tags = tags.explode("hs4").dropna().merge(articles, left_on="external_id", right_on="id")
        per_country = tags.dropna(subset=["country_code"]).groupby(["month", "country_code", "hs4"]).id.nunique()
        everywhere = tags.groupby(["month", "hs4"]).id.nunique()
        news = pd.concat([per_country.rename("count").reset_index(),
                          everywhere.rename("count").reset_index().assign(country_code="*")], ignore_index=True)
    topics_file = Path(folder) / "short_news_topics.jsonl"
    by_country = articles.dropna(subset=["country_code"])
    news_topics = by_country.groupby(["month", "country_code"]).id.nunique().rename("count").reset_index().assign(major="*")
    if topics_file.exists():
        tp = pd.read_json(topics_file, lines=True, dtype={"external_id": str})
        majors = dict(zip(topic_major.topic, topic_major.major))
        tp = tp.assign(major=tp.picked.map(lambda v: sorted({majors[t] for t in _as_list(v) if t in majors})))
        tp = tp[["external_id", "major"]].explode("major").dropna().merge(by_country, left_on="external_id", right_on="id")
        news_topics = pd.concat([news_topics, tp.groupby(["month", "country_code", "major"]).id.nunique()
                                 .rename("count").reset_index()], ignore_index=True)
    return news, news_topics, articles.month.min()


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


def _key_matrix(df, keys, months):
    """수입처럼 피처에만 쓰는 실적을 키 × 월 합계 행렬로 만든다.

    행이 하나도 없는 달은 수집되지 않은 달로 보고 NaN, 수집된 달에 그 키의 행이 없으면 무역 없음(0)으로 본다.
    """
    index = pd.MultiIndex.from_frame(df[keys].drop_duplicates()) if len(df) else pd.MultiIndex.from_tuples([], names=keys)
    out = np.zeros((len(index), len(months)), dtype=np.float64)
    if len(df):
        g = df.groupby([*keys, "month"]).amount.sum()
        # 키가 하나여도 MultiIndex로 맞춘다 (단일 Index로 찾으면 모두 -1이 되어 마지막 행에 잘못 쓰인다)
        r = index.get_indexer(pd.MultiIndex.from_frame(g.index.to_frame(index=False)[keys]))
        if (r < 0).any():
            raise ValueError("수입 키 행렬 구성 오류")
        out[r, _month_index(g.index.get_level_values("month"), months)] = g.to_numpy(dtype=float)
    seen = set(_month_index(df.month, months)) if len(df) else set()
    out[:, [i for i in range(len(months)) if i not in seen]] = np.nan
    return index, out


def _lookup(index, values, keys):
    """키 행렬의 값을 학습 행 순서로 가져온다. 한 번도 나오지 않은 키는 그 기간 내내 무역 없음(0)."""
    pos = index.get_indexer(pd.MultiIndex.from_arrays(keys)) if len(index) else np.full(len(keys[0]), -1)
    return np.where(pos >= 0, values[np.clip(pos, 0, None)] if len(index) else 0.0, 0.0)


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
    # 수집 완료가 기록된 달(관세청 수집 통계)에 행이 없는 쌍은 확인된 무역 없음이다. 그 달만 0으로 채운다.
    complete = [i for i, m in enumerate(months) if m.to_timestamp() in data.get("complete_months", set())]
    for matrix in (amount, anomaly, weight):
        matrix[:, complete] = np.nan_to_num(matrix[:, complete], nan=0.0)
    _require_observed(amount, rows, months, "amount")
    adjustment = _matrix(exports, "r1_adjustment", rows, months) if "r1_adjustment" in exports else np.zeros_like(amount)
    if "r1_adjustment" in exports:
        _require_observed(adjustment, rows, months, "r1_adjustment")
    r1_only = _matrix(exports, "r1_only_amount", rows, months) if "r1_only_amount" in exports else np.zeros_like(amount)
    corrected = amount + adjustment
    country = rows.get_level_values(0).to_numpy()
    hs6 = rows.get_level_values(1).to_numpy()
    hs4 = np.array([h[:4] for h in hs6])
    country_hs4 = np.char.add(country.astype(str), np.char.add("|", hs4))
    country_hs2 = np.char.add(country.astype(str), np.char.add("|", np.array([h[:2] for h in hs6])))
    imports = data.get("imports")
    if imports is None:
        imports = pd.DataFrame(columns=["month", "country_code", "hs6", "amount"])
    imports = imports.assign(month=pd.to_datetime(imports.month), hs4=imports.hs6.str[:4])
    imp_hs6_index, imp_hs6 = _key_matrix(imports, ["hs6"], months)
    imp_hs4_index, imp_hs4 = _key_matrix(imports, ["hs4"], months)
    imp_c4_index, imp_c4 = _key_matrix(imports, ["country_code", "hs4"], months)
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
        s3, p3 = _window(corrected, t, 3) - adjustment[:, t], _window(corrected, t - 3, 3)
        # 같은 품목군 수출: 자기 품목(행)을 빼서 쌍 실적 피처와 같은 정보를 두 번 넣지 않는다.
        g4c = lambda v: _group_sum(v, country_hs4) - v  # noqa: E731
        g2c = lambda v: _group_sum(v, country_hs2) - v  # noqa: E731
        w4 = lambda v: _group_sum(v, hs4)  # noqa: E731
        unit_ok = np.isfinite(w6) & np.isfinite(pw6) & (w6 > 0) & (pw6 > 0)
        u_a, u_w = np.where(unit_ok, a6, 0.0), np.where(unit_ok, w6, 0.0)
        u_pa, u_pw = np.where(unit_ok, pa6, 0.0), np.where(unit_ok, pw6, 0.0)
        # 창 안에 수입이 수집되지 않은 달이 있으면, 한 번도 나오지 않은 품목도 0이 아니라 결측이다.
        imp_window_ok = not np.isnan(imp_hs6[:, t - 23:t + 1]).any() if len(imp_hs6_index) else False
        imp = lambda index, mat, keys: tuple(  # noqa: E731
            _lookup(index, _window(mat, end, 12), keys) if imp_window_ok else np.full(len(keys[0]), np.nan)
            for end in (t, t - 12))
        i6, pi6 = imp(imp_hs6_index, imp_hs6, [hs6])
        i4, pi4 = imp(imp_hs4_index, imp_hs4, [hs4])
        ic4, pic4 = imp(imp_c4_index, imp_c4, [country, hs4])
        frame = pd.DataFrame({
            "anchor": months[t].to_timestamp(), "country_code": country, "hs6": hs6,
            "exp_12m": s12,
            "log_exp_12m": np.log1p(s12),
            "growth_12m": _growth(s12, p12),
            "growth_3m": _growth(s3, p3),
            "active_months": (last12 > 0).sum(axis=1),
            "cv_12m": _ratio(last12.std(axis=1), last12.mean(axis=1)),
            "anomaly_ratio": _ratio(_window(anomaly, t, 12) - r1_only[:, t], s12),
            "unit_trend": np.log(_ratio(_ratio(a6, w6), _ratio(pa6, pw6))),
            "share_in_country": _ratio(s12, c12),
            "share_in_product": _ratio(s12, h12),
            "country_growth": _growth(c12, cp12),
            "product_growth": _growth(h12, hp12),
            "product_countries": _group_sum((s12 > 0).astype(float), hs6),
            "grp4_c_growth_12m": _growth(g4c(s12), g4c(p12)),
            "grp4_c_growth_3m": _growth(g4c(s3), g4c(p3)),
            "grp4_world_growth_12m": _growth(w4(s12) - h12, w4(p12) - hp12),
            "grp2_c_growth_12m": _growth(g2c(s12), g2c(p12)),
            "hs4_unit_trend": np.log(_ratio(_ratio(w4(u_a), w4(u_w)), _ratio(w4(u_pa), w4(u_pw)))),
            "imp_hs6_world_growth": _growth(i6, pi6),
            "imp_hs4_world_growth": _growth(i4, pi4),
            "imp_hs4_from_c_growth": _growth(ic4, pic4),
            "imp_exp_ratio_hs4": np.log1p(i4) - np.log1p(w4(s12)),
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
    return _add_news(ds, data["news"], data.get("news_topics"), data.get("news_start"))


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
    columns = ["mkt_log_import", "mkt_growth", "kor_share", "kor_share_change"]
    if "mkt_hs4_growth" not in dated:
        dated = dated.assign(mkt_hs4_growth=np.nan)
    ds = join_known(ds, dated, pp.MARKET_KEYS, [*columns, "mkt_hs4_growth"])
    # ponytail: 미래 12개월을 연 단위로 근사 (기준월+6개월이 속한 해 vs 그 전해). 월별 시장 데이터가 생기면 바꾼다.
    future = dated[[*keys, "mkt_growth", "kor_share", "available_from"]].rename(
        columns={"mkt_growth": "future_mkt_growth", "kor_share": "future_share",
                 "available_from": "type_available_from"})
    ds["year"] = (ds.anchor + pd.DateOffset(months=6)).dt.year
    return ds.merge(future, on=keys, how="left").drop(columns="year")


NEWS_COLUMNS = ["news_3m", "news_all_3m", "news_change", "news_country_3m", *NEWS_TOPIC_FEATURES.values()]


def _rolling_counts(df, keys, anchors):
    """키별 월 기사 수를 기준월까지의 최근 3개월·직전 3개월 합계로 바꾼다 (월·키 long 형태)."""
    n = df.assign(month=pd.to_datetime(df.month)).pivot_table(index="month", columns=keys, values="count", aggfunc="sum")
    # 마지막 기사 이후에도 기준월까지 0개월을 이어야 최근/직전 3개월 창이 유지된다.
    start = min(n.index.min(), anchors.min() - pd.DateOffset(months=5))
    n = n.reindex(pd.date_range(start, anchors.max(), freq="MS", name="month")).fillna(0)
    now, before = n.rolling(3, min_periods=1).sum(), n.shift(3).rolling(3, min_periods=1).sum()
    long = lambda x, name: x.stack(keys, future_stack=True).rename(name).reset_index()  # noqa: E731
    return long(now, "now").merge(long(before, "before"), on=["month", *keys])


def _add_news(ds, news, topics=None, start=None):
    """단신속보 기사 수 피처 (최근 3개월 ≈ 90일).

    - news_3m·news_all_3m·news_change: 같은 HS4 기사 수 (그 국가 / 전체 국가), 직전 3개월 대비 증감
    - news_country_3m·news_trade_3m 등: 그 국가 기사 수 전체와 토픽 대분류별 수
    기사 자료가 없거나, 수집 시작 월(start) 전 달이 창에 들어가는 기준월은 0이 아니라 결측이다.
    """
    topics = topics if topics is not None else pd.DataFrame(columns=["month", "country_code", "major", "count"])
    if news.empty and topics.empty:
        return ds.assign(**{c: np.nan for c in NEWS_COLUMNS})
    anchors = pd.to_datetime(ds.anchor)
    if "hs4" not in news and "hs6" in news:  # DB news_product_daily 형식(HS6)도 HS4로 묶어 받는다
        news = news.assign(hs4=news.hs6.str[:4])
    news = news.groupby(["month", "country_code", "hs4"], as_index=False)["count"].sum()
    ds = ds.assign(hs4=ds.hs6.str[:4])
    if len(news):
        feat = _rolling_counts(news, ["country_code", "hs4"], anchors)
        pair = feat[feat.country_code != "*"].rename(columns={"month": "anchor", "now": "news_3m", "before": "news_prev_3m"})
        allc = feat[feat.country_code == "*"].drop(columns=["country_code", "before"]).rename(
            columns={"month": "anchor", "now": "news_all_3m"})
        ds = ds.merge(pair, on=["anchor", "country_code", "hs4"], how="left").merge(allc, on=["anchor", "hs4"], how="left")
        ds[["news_3m", "news_prev_3m", "news_all_3m"]] = ds[["news_3m", "news_prev_3m", "news_all_3m"]].fillna(0)
        ds["news_change"] = _growth(ds.news_3m, ds.news_prev_3m)
        ds = ds.drop(columns="news_prev_3m")
    else:
        ds = ds.assign(news_3m=np.nan, news_all_3m=np.nan, news_change=np.nan)
    names = {"*": "news_country_3m", **NEWS_TOPIC_FEATURES}
    topics = topics[topics.major.isin(names)]
    if len(topics):
        feat = _rolling_counts(topics, ["country_code", "major"], anchors)
        wide = feat.pivot_table(index=["month", "country_code"], columns="major", values="now").rename(columns=names)
        wide = wide.reindex(columns=list(names.values())).reset_index().rename(columns={"month": "anchor"})
        ds = ds.merge(wide, on=["anchor", "country_code"], how="left")
        ds[list(names.values())] = ds[list(names.values())].fillna(0)
    else:
        ds = ds.assign(**{c: np.nan for c in names.values()})
    if start is not None:
        too_early = (pd.to_datetime(ds.anchor) - pd.DateOffset(months=5) < pd.Timestamp(start)).to_numpy()
        ds.loc[too_early, NEWS_COLUMNS] = np.nan
    return ds.drop(columns="hs4")


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

