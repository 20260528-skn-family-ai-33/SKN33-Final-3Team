"""외부 CSV(BACI·Comtrade·WDI) 전처리 → 연 단위 시장·국가 피처 표 (docs/ML_PLAN.md 4장).

실행 (저장소 루트에서):
    python -m backend.app.ml.preprocess          # CSV만으로 실행 (국가 = 시드 SQL의 COUNTRY, HS 묶음 통일 없음)
    python -m backend.app.ml.preprocess --db     # DB의 country·hs_code_correlation으로 국가 필터·HS 묶음 통일

결과: data/ml/market_yearly.csv (연도·국가·HS6), data/ml/wdi_yearly.csv (연도·국가)
features.build_dataset도 같은 함수(market_yearly·wdi_yearly)로 만든 표를 쓴다.
"""
import argparse
import re
from pathlib import Path

import numpy as np
import pandas as pd

from backend.app.scheduler.collectors.external import external_collect as ec

OUT_DIR = Path(__file__).resolve().parents[3] / "data" / "ml"
CLIP_Q = (0.01, 0.99)  # 증가율·점유율 변화의 극단값을 자르는 분위수
WDI_FILL_YEARS = 1  # 지표가 아직 발표 전이면 직전 해 값을 이만큼까지 이어 쓴다
MARKET_KEYS = ["country_code", "hs6"]


def read_csv(path, **kw):
    return pd.read_csv(path, keep_default_na=False, na_values=[""], **kw)  # 나미비아 NA 보존


def read_market(external_dir):
    """BACI 집계본과 Comtrade 파일 전부. 출처를 source에 남긴다 (어느 출처를 쓸지는 market_yearly가 고른다)."""
    frames = []
    baci = external_dir / "baci" / "baci_imports_hs22.csv"
    if baci.exists():
        frames.append(read_csv(baci, dtype={"hs6": str}).assign(source="BACI"))
    for path in sorted((external_dir / "comtrade").glob("comtrade_imports_*.csv")):
        frames.append(read_csv(path, dtype={"hs6": str}).assign(source="COMTRADE"))
    columns = ["year", "country_code", "hs6", "import_world_usd", "import_from_kor_usd", "source"]
    return pd.concat(frames, ignore_index=True)[columns] if frames else pd.DataFrame(columns=columns)


def read_wdi(external_dir):
    files = sorted((external_dir / "wdi").glob("wdi_*.csv"))
    columns = ["year", "country_code", "gdp_usd", "gdp_growth_pct", "population", "gdp_per_capita_usd"]
    return read_csv(files[-1]) if files else pd.DataFrame(columns=columns)


def seed_countries():
    """DB 없이 쓸 국가 목록: 시드 SQL(04·07 …)의 COUNTRY 행."""
    pattern = re.compile(r"^\('([A-Z0-9]+)','[^']*','COUNTRY'", re.M)
    return {c for path in ec.SEED_SQL.parent.glob("*country*seed*.sql") for c in pattern.findall(path.read_text("utf-8"))}


def clip(s):
    low, high = s.quantile(CLIP_Q)
    return s.clip(low, high)


def _growth(now, before):
    return np.log1p(now) - np.log1p(before)


def market_yearly(market, countries=None):
    """연도·국가·HS6별 시장 규모·성장·한국 점유율. market의 hs6는 이미 비교용 코드(HS2022)여야 한다.

    - 대상 국가(countries)만 남기고, 전세계 수입액이 0 이하인 행은 시장이 없는 것으로 보고 뺀다.
    - 같은 (국가, HS6)이 여러 코드에서 오면 합친다 (HS 묶음 통일 뒤 생기는 중복).
    - BACI가 있는 해는 BACI, 그 뒤 해는 Comtrade를 쓴다.
    - 성장·점유율 변화는 **같은 출처의** 바로 전해 값과 비교한다. BACI는 FOB, Comtrade 수입은 CIF라
      섞어 비교하면 운임·보험료만큼 성장률이 부풀기 때문이다. 그래서 BACI 다음 해의 성장률은
      BACI 마지막 해에 받아 둔 Comtrade(연결 연도)와 비교한다. 같은 출처 전해 값이 없으면 결측.
    - 자르기는 모델 학습 단계에서 수행한다.
    """
    m = market if countries is None else market[market.country_code.isin(countries)]
    m = (m[m.import_world_usd > 0]
         .groupby(["year", *MARKET_KEYS, "source"], as_index=False)
         .agg(import_world_usd=("import_world_usd", "sum"), import_from_kor_usd=("import_from_kor_usd", "sum")))
    m["kor_share"] = (m.import_from_kor_usd / m.import_world_usd).clip(0, 1)
    prev = m[["year", *MARKET_KEYS, "source", "import_world_usd", "kor_share"]].assign(year=m.year + 1)
    m = m.merge(prev, on=["year", *MARKET_KEYS, "source"], how="left", suffixes=("", "_prev"))
    baci_years = set(m.loc[m.source == "BACI", "year"])
    m = m[m.source == np.where(m.year.isin(baci_years), "BACI", "COMTRADE")]  # 연결 연도 Comtrade 행은 비교에만 쓰고 뺀다
    m["mkt_log_import"] = np.log1p(m.import_world_usd)
    # 분할 이전에는 원래 값을 유지한다. 분위수 경계는 모델의 학습 행에서만 추정한다.
    m["mkt_growth"] = _growth(m.import_world_usd, m.import_world_usd_prev)
    m["kor_share_change"] = m.kor_share - m.kor_share_prev
    return m.drop(columns=["import_world_usd_prev", "kor_share_prev"]).sort_values(["year", *MARKET_KEYS],
                                                                                   ignore_index=True)


def wdi_yearly(wdi, countries=None):
    """연도·국가별 거시 지표 (로그 변환).

    GDP는 인구보다 늦게 발표되므로, 지표마다 비어 있으면 직전 해 값을 WDI_FILL_YEARS년까지 이어 쓰고 filled=True로 표시한다.
    값이 하나도 없는 (국가, 연도)는 만들지 않는다.
    """
    w = wdi if countries is None else wdi[wdi.country_code.isin(countries)]
    with np.errstate(divide="ignore"):
        w = pd.DataFrame({"year": w.year.astype(int), "country_code": w.country_code,
                          "log_gdp": np.log(w.gdp_usd.astype(float)),
                          "gdp_growth": w.gdp_growth_pct.astype(float),
                          "log_population": np.log(w.population.astype(float)),
                          "log_gdp_pc": np.log(w.gdp_per_capita_usd.astype(float))})
    values = ["log_gdp", "gdp_growth", "log_population", "log_gdp_pc"]
    w = w.replace([np.inf, -np.inf], np.nan)
    years = range(w.year.min(), w.year.max() + 1) if len(w) else []
    full = pd.MultiIndex.from_product([sorted(w.country_code.unique()), years], names=["country_code", "year"])
    w = w.set_index(["country_code", "year"]).reindex(full)[values]
    filled = w.groupby(level="country_code").ffill(limit=WDI_FILL_YEARS)
    filled["filled"] = (w.isna() & filled.notna()).any(axis=1)
    return filled.dropna(how="all", subset=values).reset_index()[["year", "country_code", *values, "filled"]]


def load_external(external_dir=None, countries=None, hs_mapping=None):
    """외부 CSV를 읽어 전처리한 두 표를 돌려준다. hs_mapping은 features.hs_key_map 결과 (없으면 코드 그대로)."""
    external_dir = external_dir or ec.OUT_DIR
    market = read_market(external_dir)
    if hs_mapping:
        market = market.assign(hs6=[hs_mapping.get(("HS2022", c), c) for c in market.hs6])
    availability_path = external_dir / "availability.csv"
    availability = read_csv(availability_path) if availability_path.exists() else None
    return {"market_yearly": market_yearly(market, countries),
            "wdi_yearly": wdi_yearly(read_wdi(external_dir), countries),
            "availability": availability}


def summarize(name, df, keys):
    print(f"[{name}] {len(df):,}행, 연도 {df.year.min()}~{df.year.max()}, 국가 {df.country_code.nunique()}개")
    print(df.groupby("year").size().rename("행 수").to_string())
    na = df.drop(columns=keys).isna().mean()
    print("결측 비율: " + (", ".join(f"{c} {v:.1%}" for c, v in na.items() if v > 0) or "없음"))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", action="store_true", help="DB의 country·hs_code_correlation 사용")
    parser.add_argument("--external-dir", type=Path, default=ec.OUT_DIR)
    args = parser.parse_args()
    countries, mapping = seed_countries(), None
    if args.db:
        from sqlalchemy import text
        from backend.app.db.session import make_engine
        from backend.app.ml.features import hs_key_map
        with make_engine().connect() as conn:
            countries = set(conn.execute(text("SELECT country_code FROM country WHERE country_role='COUNTRY'")).scalars())
            mapping = hs_key_map(pd.read_sql(text("SELECT new_code, old_code, group_code FROM hs_code_correlation"
                                                  " WHERE new_ver='HS2022' AND old_ver='HS2017'"), conn))
    out = load_external(args.external_dir, countries, mapping)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, keys in (("market_yearly", ["year", *MARKET_KEYS]), ("wdi_yearly", ["year", "country_code"])):
        out[name].to_csv(OUT_DIR / f"{name}.csv", index=False)
        summarize(name, out[name], keys)
    print(f"저장: {OUT_DIR}")


if __name__ == "__main__":
    main()

