"""해외시장뉴스 키워드·품목 기사 집계 테이블(FR-906)과 급증 판정(FR-203·204)을 만듭니다.

입력: keyword_clean.py 결과(news_keywords_clean.csv)
- 키워드별 집계: 날짜 × 국가 × 키워드(kw_spike, 불용어 제외) 기사 수
- 품목별 집계: 날짜 × 국가 × 품목, 단위(unit) 칸으로 HS 6단위(요구사항 기본)와 4단위를 함께 저장
  (지금은 KOTRA HS 칸만 — HS 사전 완료 후 키워드·제목 연결분 추가)
- FR-204 주요 이슈: 최근 7일 기사 수 ÷ 직전 8주 주평균 ≥ 3 (평균 0이면 1), 키워드·품목 각각 + KOTRA 통상·규제 기사
- FR-203 급증 기사 알림: 어제 기사 수 ÷ 최근 7일(이틀 전까지) 일평균 ≥ 2.5 (평균 0이면 1),
  사용자 관심 범위(관심 국가 × 관심 품목·키워드) — interest_alerts()
한 기사는 같은 키워드·품목을 1건으로만 셉니다.

실행: python backend/app/services/news_keywords/article_counts.py --date 2026-10-14
"""
import argparse
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent
DATA = Path(__file__).resolve().parents[4] / "data" / "news_keywords"


def _split(s):
    return [x for x in str(s).split("|") if x]


def explode(df, col):
    """기사 × (국가, 값) 행으로 펼칩니다. 같은 기사의 같은 값은 1건."""
    rows = []
    for r in df.itertuples(index=False):
        countries = _split(r.country_all) or ["-"]  # 국가를 알 수 없는 기사(KOTRA 본사 등)는 '-'
        for v in dict.fromkeys(_split(getattr(r, col))):
            for c in countries:
                rows.append((r.bbstxSn, r.othbcDt[:10], c, v))
    return pd.DataFrame(rows, columns=["bbstxSn", "date", "country", "key"])


def build_counts(df, col):
    """FR-906 집계 테이블: 날짜 × 국가 × 키(키워드 또는 품목) 기사 수."""
    e = explode(df, col)
    return (e.drop_duplicates(["bbstxSn", "country", "key"])
             .groupby(["date", "country", "key"], as_index=False).bbstxSn.nunique()
             .rename(columns={"bbstxSn": "articles"}))


def _article_keys(df, col):
    """국가와 상관없이 기사 × 키 (전체 기사 기준 판정용, 나라가 여러 개여도 1건)."""
    return pd.DataFrame([(r.bbstxSn, r.othbcDt[:10], v) for r in df.itertuples(index=False)
                         for v in dict.fromkeys(_split(getattr(r, col)))], columns=["bbstxSn", "date", "key"])


def major_issues(df, col, run_date, ratio=3.0):
    """FR-204: 판정일 기준 최근 7일 기사 수 ÷ 직전 8주 주평균 ≥ ratio (주평균 0이면 1)."""
    d = pd.Timestamp(run_date)
    a = _article_keys(df, col)
    a["date"] = pd.to_datetime(a["date"])
    recent = a[(a.date >= d - pd.Timedelta(days=7)) & (a.date < d)].groupby("key").bbstxSn.nunique()
    prev = a[(a.date >= d - pd.Timedelta(days=63)) & (a.date < d - pd.Timedelta(days=7))].groupby("key").bbstxSn.nunique() / 8
    out = pd.DataFrame({"recent_7d": recent}).join(prev.rename("prev_weekly_avg")).fillna(0)
    out["ratio"] = out.recent_7d / out.prev_weekly_avg.clip(lower=1)
    return out[out.ratio >= ratio].sort_values(["ratio", "recent_7d"], ascending=False).reset_index()


def trade_regulation_articles(raw, run_date):
    """FR-204-03: 판정일 전날 올라온 KOTRA 정보분류 '통상·규제' 기사."""
    d = (pd.Timestamp(run_date) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    return raw[(raw.othbcDt.str[:10] == d) & (raw.infoCl.str.contains("통상·규제"))]


def interest_spike(counts, run_date, country, key, ratio=2.5):
    """FR-203: 어제 기사 수 ÷ 최근 7일(이틀 전까지, 어제와 겹치지 않음) 일평균 ≥ ratio (평균 0이면 1)."""
    d = pd.Timestamp(run_date)
    c = counts[(counts.country == country) & (counts.key == key)].copy()
    c["date"] = pd.to_datetime(c["date"])
    yesterday = c[c.date == d - pd.Timedelta(days=1)].articles.sum()
    week_avg = c[(c.date >= d - pd.Timedelta(days=8)) & (c.date <= d - pd.Timedelta(days=2))].articles.sum() / 7
    r = yesterday / max(week_avg, 1)
    return {"country": country, "key": key, "yesterday": int(yesterday), "week_avg": round(week_avg, 2),
            "ratio": round(r, 2), "spike": bool(r >= ratio)}


def product_counts(df):
    """품목별 집계: HS 6단위(요구사항 기본)와 4단위를 한 표에 unit 칸으로 구분해 저장."""
    parts = [build_counts(df, c).assign(unit=c) for c in ("hs6", "hs4") if c in df.columns]
    return pd.concat(parts, ignore_index=True)


def interest_alerts(kw_counts, prod_counts, run_date, countries, products=(), keywords=(), ratio=2.5):
    """FR-203: 사용자 관심 범위(관심 국가 × 관심 품목·키워드)에서 급증한 것만 돌려줍니다.
    관심 품목은 6단위면 6단위 집계, 4단위면 4단위 집계로 셉니다."""
    out = []
    for c in countries:
        for code in products:
            unit = "hs6" if len(code) >= 6 else "hs4"
            r = interest_spike(prod_counts[prod_counts.unit == unit], run_date, c, code, ratio)
            out.append({**r, "type": f"품목({unit.upper()})"})
        for k in keywords:
            out.append({**interest_spike(kw_counts, run_date, c, k, ratio), "type": "키워드"})
    return [r for r in out if r["spike"]]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", type=Path, default=DATA / "news_keywords_clean.csv")
    parser.add_argument("--date", help="판정일 YYYY-MM-DD (기본: 데이터 마지막 날의 다음 날)")
    parser.add_argument("--output-dir", type=Path, default=DATA)
    args = parser.parse_args()
    df = pd.read_csv(args.input, dtype=str, keep_default_na=False)
    run_date = args.date or (pd.to_datetime(df.othbcDt.str[:10]).max() + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)
    kc = build_counts(df, "kw_spike")
    pc = product_counts(df)
    kc.to_csv(out / "keyword_daily_counts.csv", index=False, encoding="utf-8-sig")
    pc.to_csv(out / "product_daily_counts.csv", index=False, encoding="utf-8-sig")
    mk = major_issues(df, "kw_spike", run_date).assign(type="키워드")
    mp6 = major_issues(df, "hs6", run_date).assign(type="품목(HS6)") if "hs6" in df.columns else pd.DataFrame()
    mp = major_issues(df, "hs4", run_date).assign(type="품목(HS4)")
    issues = pd.concat([mk, mp6, mp], ignore_index=True)
    issues.to_csv(out / f"major_issues_{run_date}.csv", index=False, encoding="utf-8-sig")
    print(f"판정일 {run_date}")
    print(f"키워드별 집계 {len(kc):,}행 / 품목별 집계 {len(pc):,}행")
    print(f"FR-204 주요 이슈: 키워드 {len(mk)}개 {mk.key.head(8).tolist()}")
    print(f"                  품목 6단위 {len(mp6)}개 {mp6.key.head(8).tolist() if len(mp6) else []} / 4단위 {len(mp)}개 {mp.key.head(8).tolist()}")
    if "infoCl" in df.columns:
        tr = trade_regulation_articles(df, run_date)
        print(f"FR-204 통상·규제 기사(전날): {len(tr)}건")
    print(f"결과: {out}")


if __name__ == "__main__":
    main()
