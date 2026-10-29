"""수출 산업별 관련 품목 묶음표(reference/related_hs4.csv) 검증과 데이터 기반 후보 찾기.

묶음표 한 행 = (묶음, HS4, 역할, 출처, 메모)
- 역할: product(그 산업의 수출 제품) · input(한국이 들여오는 장비·소재, 선행 신호) · related(함께 움직이는 연관·수요 품목)
- 출처: curated(사람이 정함) · data(suggest --apply로 데이터에서 찾아 추가)

실행 (저장소 루트에서):
    python -m backend.app.ml.related_groups validate                 # 형식·중복·HS 품목명 원본에 있는지 검사
    python -m backend.app.ml.related_groups suggest --until 2025-09  # 관세청 수집 CSV로 후보 찾기 → reference/related_hs4_candidates.csv
    python -m backend.app.ml.related_groups suggest --until 2025-09 --apply   # 엄격한 기준을 넘은 후보를 묶음표에 data 출처로 추가

DB를 읽지 않는다. 실적은 관세청 수집 원본 CSV, 품목명은 관세청 HS 품목명 원본 xlsx(ml/sources.py).

후보 찾기는 --until까지의 실적만 쓴다. 평가·예측 기간의 실적으로 묶음을 고르면 그 기간 성능이 부풀기 때문이다.
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

REFERENCE_DIR = Path(__file__).resolve().parent / "reference"
GROUPS_CSV = REFERENCE_DIR / "related_hs4.csv"
CANDIDATES_CSV = REFERENCE_DIR / "related_hs4_candidates.csv"
COLUMNS = ["group", "hs4", "role", "source", "note"]
ROLES = {"product", "input", "related"}
LAGS = (0, 3, 6)  # 후보 품목이 산업 제품 수출보다 몇 개월 앞서 움직이는지
MIN_POINTS = 18  # 상관을 계산할 최소 월 수
MIN_ANNUAL_USD = 1e8  # 연평균 $100M 미만 HS4는 잡음이 커서 후보에서 뺀다
# --apply 기준. 33개월 안팎의 전년 대비 증가율은 서로 매끄럽게 이어져 우연히도 상관이 높게 나온다
# (2026-10-22 실제 실행: 상관 0.6 이상이 280개 중 197개, 반도체↔도자기 세면대 0.87 등).
# 그래서 높은 상관이면서 3개월 이상 먼저 움직이는(예측에 쓸 수 있는) 품목만 넣고, 나머지는 사람이 후보 파일을 보고 고른다.
MIN_CORR = 0.8
MIN_APPLY_LAG = 3
TOP_N = 10  # 묶음·흐름별로 남기는 후보 수


def load_groups(path=GROUPS_CSV):
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def validate(groups, hs4_names=None):
    """문제 목록을 돌려준다. hs4_names(HS2022 4단위 코드 → 이름)를 주면 사전에 없는 코드도 찾는다."""
    problems = []
    if list(groups.columns) != COLUMNS:
        return [f"컬럼은 {COLUMNS} 순서여야 함: {list(groups.columns)}"]
    bad = groups[~groups.hs4.str.fullmatch(r"\d{4}")]
    problems += [f"4자리 숫자가 아닌 HS4: {r.group} {r.hs4}" for r in bad.itertuples()]
    problems += [f"알 수 없는 역할: {r.group} {r.hs4} {r.role}" for r in groups[~groups.role.isin(ROLES)].itertuples()]
    problems += [f"알 수 없는 출처: {r.group} {r.hs4} {r.source}"
                 for r in groups[~groups.source.isin({"curated", "data"})].itertuples()]
    dup = groups[groups.duplicated(["group", "hs4"], keep=False)]
    problems += [f"한 묶음에 같은 HS4가 두 번: {g} {h}" for g, h in dup[["group", "hs4"]].drop_duplicates().itertuples(index=False)]
    products = groups[groups.role == "product"]
    many = products[products.duplicated("hs4", keep=False)]
    problems += [f"HS4 {h}가 여러 묶음의 product: {', '.join(sorted(many[many.hs4 == h].group))}" for h in sorted(set(many.hs4))]
    no_product = set(groups.group) - set(products.group)
    problems += [f"product가 없는 묶음: {g}" for g in sorted(no_product)]
    if hs4_names is not None:
        missing = sorted(set(groups.hs4) - set(hs4_names))
        problems += [f"HS2022 사전에 없는 HS4: {h}" for h in missing]
    return problems


def _yoy(frame):
    """월별 금액 → 전년 같은 달 대비 log 증가율."""
    return np.log1p(frame) - np.log1p(frame.shift(12))


def suggest(series, groups, until, names=None):
    """관세청 HS4 월별 합계(전 세계)로 묶음마다 관련 품목 후보를 찾는다.

    series: month, flow('export'|'import'), hs4, amount  (한국 전 세계 수출·수입, HS4별)
    산업 제품 수출의 전년 대비 증가율과 후보 HS4 증가율(lag개월 앞선 값)의 상관을 본다.
    두 증가율 모두 한국 전체 수출(수입) 증가율을 빼고 비교해, 경기 때문에 다 같이 움직이는 것을 관련으로 착각하지 않게 한다.
    """
    s = series.assign(month=pd.to_datetime(series.month))
    s = s[s.month <= pd.Timestamp(until)]
    wide = s.pivot_table(index="month", columns=["flow", "hs4"], values="amount", aggfunc="sum").sort_index()
    wide = wide.reindex(pd.date_range(wide.index.min(), wide.index.max(), freq="MS")).fillna(0)
    years = len(wide) / 12
    big = wide.columns[(wide.sum() / years >= MIN_ANNUAL_USD).to_numpy()]
    growth = _yoy(wide[big])
    total = {flow: _yoy(wide[flow].sum(axis=1)) for flow in ("export", "import") if flow in wide.columns.get_level_values(0)}
    out = []
    for group, rows in groups.groupby("group"):
        products = [h for h in rows.loc[rows.role == "product", "hs4"] if ("export", h) in wide.columns]
        if not products:
            continue
        target = _yoy(wide["export"][products].sum(axis=1)) - total["export"]
        members = set(rows.hs4)
        for (flow, hs4), g in growth.items():
            if hs4 in members:
                continue
            rel = g - total[flow]
            best = None
            for lag in LAGS:
                pair = pd.concat([target, rel.shift(lag)], axis=1).dropna()
                if len(pair) < MIN_POINTS or pair.iloc[:, 1].std() == 0:
                    continue
                corr = pair.iloc[:, 0].corr(pair.iloc[:, 1])
                if np.isfinite(corr) and (best is None or corr > best[0]):
                    best = (corr, lag, len(pair))
            if best:
                out.append({"group": group, "hs4": hs4, "flow": flow, "corr": round(best[0], 3), "lag": best[1],
                            "months": best[2], "role": "input" if flow == "import" else "related",
                            "name": (names or {}).get(hs4, "")})
    cand = pd.DataFrame(out, columns=["group", "hs4", "flow", "corr", "lag", "months", "role", "name"])
    if cand.empty:
        return cand
    cand = cand.sort_values(["group", "flow", "corr"], ascending=[True, True, False])
    return cand.groupby(["group", "flow"], group_keys=False).head(TOP_N).reset_index(drop=True)


def apply_candidates(groups, cand, until, min_corr=MIN_CORR, min_lag=MIN_APPLY_LAG):
    """상관이 min_corr 이상이고 min_lag개월 이상 선행하는 후보만 data 출처로 묶음표에 더한다.
    이미 있는 (묶음, HS4)는 건너뛴다."""
    have = set(zip(groups.group, groups.hs4))
    fresh = np.array([(g, h) not in have for g, h in zip(cand.group, cand.hs4)], dtype=bool)
    new = cand[(cand["corr"] >= min_corr).to_numpy() & (cand.lag >= min_lag).to_numpy() & fresh]
    new = new.drop_duplicates(["group", "hs4"])
    rows = pd.DataFrame({"group": new.group, "hs4": new.hs4, "role": new.role, "source": "data",
                         "note": [f"{f} 상관 {c:.2f}, {l}개월 선행 (~{until}) {n}".strip()
                                  for f, c, l, n in zip(new.flow, new["corr"], new.lag, new.name)]})
    return pd.concat([groups, rows], ignore_index=True)


def series_from_raw(folders=None):
    """관세청 수집 원본(연도별 일괄 + 매월 정기)에서 HS4 월별 전 세계 수출·수입 합계."""
    from backend.app.ml import sources as S
    raw, _ = S.raw_customs(folders or S.CUSTOMS_DIRS)
    g = raw.assign(hs4=raw.hsCd.str[:4]).groupby(["month", "hs4"])[["expDlr", "impDlr"]].sum()
    return (g.rename(columns={"expDlr": "export", "impDlr": "import"}).stack().rename("amount").reset_index()
            .rename(columns={"level_2": "flow"}))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("validate", help="묶음표 형식·중복·HS2022 사전 존재 검사")
    sg = sub.add_parser("suggest", help="관세청 실적으로 관련 품목 후보 찾기")
    sg.add_argument("--until", required=True, help="이 달(YYYY-MM)까지의 실적만 사용 (학습 기간 끝)")
    sg.add_argument("--customs-dir", type=Path, nargs="+",
                    help="관세청 수집 원본 폴더 (기본: 연도별 일괄 customs_outputs + 매월 정기 customs_load_outputs/monthly)")
    sg.add_argument("--apply", action="store_true", help=f"상관 {MIN_CORR} 이상·{MIN_APPLY_LAG}개월 이상 선행 후보를 묶음표에 추가")
    sg.add_argument("--min-corr", type=float, default=MIN_CORR)
    args = parser.parse_args()
    from backend.app.ml import sources as S
    names = {code: name for code, name in S.hs_names().items() if len(code) == 4}
    groups = load_groups()
    if args.cmd == "validate":
        problems = validate(groups, names)
        for g, rows in groups.groupby("group", sort=False):
            print(f"[{g}] " + ", ".join(f"{r.hs4}({r.role})" for r in rows.itertuples()))
        print("\n".join(f"문제: {p}" for p in problems) or f"검사 통과: {groups.group.nunique()}개 묶음, {len(groups)}행")
        raise SystemExit(1 if problems else 0)
    series = series_from_raw(args.customs_dir)
    cand = suggest(series, groups, args.until + "-01", names)
    cand.to_csv(CANDIDATES_CSV, index=False)
    print(cand.to_string(index=False) if len(cand) else "후보 없음")
    print(f"저장: {CANDIDATES_CSV}")
    if args.apply:
        merged = apply_candidates(groups, cand, args.until, args.min_corr)
        problems = validate(merged, names)
        if problems:
            raise SystemExit("묶음표에 추가하지 않음:\n" + "\n".join(problems))
        merged.to_csv(GROUPS_CSV, index=False)
        print(f"묶음표에 {len(merged) - len(groups)}행 추가: {GROUPS_CSV}")


if __name__ == "__main__":
    main()
