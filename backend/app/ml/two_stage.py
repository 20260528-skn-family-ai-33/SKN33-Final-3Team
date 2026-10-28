"""2단계 구성: 1단계 회귀(다음 12개월 성장률) → 2단계 순위 학습(같은 기준월 · 같은 국가 안 품목 순서).

방식
    B (스태킹)  : 1단계 교차 예측값을 피처로 넣은 순위 학습이 국가 안 전체 후보를 정렬한다
    C (필터링 K): 1단계로 국가별 상위 K개만 남기고 그 안을 순위 학습이 다시 정렬한다 (나머지는 1단계 순서로 그 아래)
쓰임
    국가 → 유망 품목 순위 = 2단계 순서(order) / 품목 → 유망 국가 순위 · 예측 증감률 = 1단계 성장률(growth)
    2단계는 국가 묶음으로만 학습해 품목 방향 순위에는 맞지 않는다 (4차 테스트).
교차 예측
    2단계 학습 행의 1단계 값은 (국가, HS6) 쌍 단위로 나눈 교차 예측이다. 같은 행으로 학습한 회귀 예측을 그대로 쓰면
    실제보다 잘 맞는 값이라 2단계가 그 피처를 과신한다.
파일
    data/ml/candidates/two_stage_<b|cK>_<base|ext>_<버전>.joblib. 딕셔너리에 두 Pipeline · 피처 · 방식과
    학습 시점의 최신 기준월 예측(latest)을 함께 둬 조회는 파일만 읽는다.
    월별 재학습(candidate_models.run → monthly)은 MEMBERS 구성(최종: v1 · v3 앙상블)의 모델과 앙상블 latest를
    data/ml/monthly_models/two_stage_<YYYYMM>.joblib 하나에 담는다 (query --model 그 파일 하나로 조회).

실행 (저장소 루트에서):
    python -m backend.app.ml.two_stage train --method C --k 100 --external first_release --version v1
    python -m backend.app.ml.two_stage query --model data/ml/candidates/two_stage_c100_ext_v1.joblib --country 베트남
    python -m backend.app.ml.two_stage query --model 파일1 파일2 --country VN --hs6 850760   (여러 개면 앙상블)
"""
import argparse
import logging
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import joblib
import numpy as np
import pandas as pd
from sklearn.model_selection import GroupKFold
from threadpoolctl import threadpool_limits

from backend.app.ml import features as F
from backend.app.ml import sources as S
from backend.app.ml.candidate_models import OUT_DIR, _finite, _runtime, needed_columns
from backend.app.ml.candidate_query import country_code, item_name
from backend.app.ml.experiment_metrics import evaluate_predictions
from backend.app.ml.experiments import (FEATURE_SETS, ROOT, SNAPSHOT_PY311, ExperimentConfig, ModelSpec, build_model,
                                        load_snapshot, prepare_scenarios)
from backend.app.ml.feature_recovery import MARKET, WDI, coverage, recover_external

KEYS = ["anchor", "country_code", "hs6"]
GROUP = ["anchor", "country_code"]
GRADES = 30  # 2단계 정답 = 그룹 안 성장 백분위를 0~30 등급으로 (평가 NDCG와 같은 선형 이득)
FOLDS = 3
OFFSET = 1e3  # C 방식에서 후보(2단계 점수)를 후보 밖(1단계 성장률, 약 -21~8)보다 항상 위에 둔다
log = logging.getLogger(__name__)


def load_rows(snapshot_path=SNAPSHOT_PY311, external="none"):
    """스냅샷으로 split_rows. 스냅샷의 외부 피처가 비어 있으면 first_release일 때 availability.csv 공개일로 붙인다(v3)."""
    snapshot = load_snapshot(snapshot_path)
    frame = snapshot[[*needed_columns(), "exp_12m"]]
    frame.attrs = dict(snapshot.attrs)
    del snapshot
    if external == "first_release" and frame[MARKET + WDI].isna().all().all():
        frame, _ = recover_external(frame)
    return split_rows(frame, external)


def split_rows(frame, external="none"):
    """(정답 있는 학습 행, 최신 기준월 행, 1단계 피처). 둘 다 (기준월, 국가, HS6) 키 순서.

    external="none"은 외부 피처 9개를 쓰지 않고(v1), "first_release"는 frame에 붙어 있는 외부 피처를 쓴다(v3).
    """
    if external not in ("none", "first_release"):
        raise ValueError(f"external은 none 또는 first_release: {external}")
    candidates = [c for c in FEATURE_SETS["all"] if external != "none" or c not in MARKET + WDI]
    frame = prepare_scenarios({"original": frame}, F.HORIZON)["original"]
    labeled = frame.y_growth.notna().to_numpy()
    rows = frame[labeled]
    latest = frame[(frame.anchor == frame.anchor.max()).to_numpy()]
    del frame
    stats = coverage(rows[candidates].replace([np.inf, -np.inf], np.nan), candidates)
    return rows, latest, stats.loc[stats.status == "available", "feature"].tolist()


def _x(rows, columns):
    return rows[columns].replace([np.inf, -np.inf], np.nan)


def _top_k(rows, score, k):
    return (pd.Series(score, index=rows.index).groupby([rows.anchor, rows.country_code])
            .rank(ascending=False, method="first") <= k).to_numpy()


def _regressor(x, y, columns, seed, n_jobs):
    model, cat = build_model(ModelSpec("lightgbm", "lightgbm"), columns, seed, n_jobs)
    clip = y.quantile(ExperimentConfig().target_clip).tolist()
    with threadpool_limits(limits=n_jobs):
        model.fit(x[columns], y.clip(*clip), model__categorical_feature=cat)
    return model


def _ranker(x, rows, columns, seed, n_jobs):
    """rows는 키 순서라 (기준월, 국가) 그룹이 연속이다."""
    from lightgbm import LGBMRanker
    model, cat = build_model(ModelSpec("lightgbm", "lightgbm"), columns, seed, n_jobs)
    model.set_params(model=LGBMRanker(objective="lambdarank", n_estimators=300, learning_rate=.05, num_leaves=31,
                                      label_gain=list(range(GRADES + 1)), verbosity=-1, n_jobs=n_jobs, random_state=seed))
    grades = (rows.groupby(GROUP, sort=False).y_growth.rank(pct=True) * GRADES).round().astype(int)
    sizes = rows.groupby(GROUP, sort=False).size().to_numpy()
    with threadpool_limits(limits=n_jobs):
        model.fit(x[columns], grades, model__group=sizes, model__categorical_feature=cat)
    return model


def fit(rows, columns, method="C", k=100, seed=0, n_jobs=4, folds=FOLDS):
    """rows: 정답 있는 학습 행(키 순서). columns: 1단계 피처."""
    if method not in ("B", "C") or (method == "C" and k < 1):
        raise ValueError("method는 B 또는 C, C의 k는 1 이상")
    started = time.perf_counter()
    x = _x(rows, columns)
    stage1 = _regressor(x, rows.y_growth, columns, seed, n_jobs)
    oof = np.empty(len(rows))
    pairs = (rows.country_code + "_" + rows.hs6).to_numpy()
    for fit_idx, out_idx in GroupKFold(n_splits=folds).split(x, groups=pairs):
        part = _regressor(x.iloc[fit_idx], rows.y_growth.iloc[fit_idx], columns, seed, n_jobs)
        oof[out_idx] = part.predict(x.iloc[out_idx][columns])
    stacked = [*columns, "stage1_pred"]
    xs = x.assign(stage1_pred=oof)
    keep = _top_k(rows, oof, k) if method == "C" else np.ones(len(rows), bool)
    stage2 = _ranker(xs[keep], rows[keep], stacked, seed, n_jobs)
    return {"stage1": stage1, "stage2": stage2, "features": columns, "stage2_features": stacked, "method": method,
            "k": k if method == "C" else None, "folds": folds, "seed": seed, "train_rows": len(rows),
            "stage2_rows": int(keep.sum()), "train_start": str(rows.anchor.min().date()),
            "train_end": str(rows.anchor.max().date()), "fit_seconds": time.perf_counter() - started}


def predict(bundle, rows):
    """growth = 1단계 성장률, order = 국가 안 순서 점수(클수록 위)."""
    x = _x(rows, bundle["features"])
    growth = bundle["stage1"].predict(x)
    keep = _top_k(rows, growth, bundle["k"]) if bundle["method"] == "C" else np.ones(len(rows), bool)
    order = growth.astype(float)
    if keep.any():
        stacked = x.assign(stage1_pred=growth)[keep]
        order[keep] = OFFSET + bundle["stage2"].predict(stacked[bundle["stage2_features"]])
    return rows[KEYS].assign(growth=growth, order=order)


def combine(predictions):
    """여러 2단계 모델의 앙상블: 순서는 전체 백분위 순위 평균(실험 5와 같은 방식), 성장률은 평균."""
    base = predictions[0][KEYS].reset_index(drop=True)
    for p in predictions[1:]:
        if not p[KEYS].reset_index(drop=True).equals(base):
            raise ValueError("앙상블할 모델의 예측 대상(기준월 · 국가 · HS6)이 다릅니다")
    return base.assign(growth=np.mean([p.growth.to_numpy() for p in predictions], axis=0),
                       order=np.mean([p.order.rank(pct=True).to_numpy() for p in predictions], axis=0))


# 최종 모델 = v1 · v3 피처 2단계 C(K=100) 앙상블. 같은 시드끼리 v3 단독과 비교하면 NDCG@5가 기준월 3개 × 시드 3개
# 9번 모두 같거나 높다 (5차 테스트). v3 단독은 {"ext": "first_release"} (학습 약 1.6배 빠름, Hit@5는 기준월마다 엇갈림).
MEMBERS = {"base": "none", "ext": "first_release"}


def monthly(frame, n_jobs=2, method="C", k=100):
    """candidate_models.run(월별 ML 단계)용: MEMBERS 2단계 모델을 전체 정답 행으로 학습해 한 묶음으로 돌려준다.

    frame은 수집 원본으로 만든 피처 표(외부 피처는 availability.csv 공개일로 붙은 상태).
    Holdout 평가는 candidate_models.holdout_check와 같은 분할(마지막 정답 기준월, 12개월 이상 앞선 기준월로 학습)이다.
    국가 순위 지표는 2단계 순서(model), 품목 방향 순위 · 증감률 지표는 1단계 성장률(model_stage1)로 잰다.
    MEMBERS가 둘 이상이면 combine으로 앙상블한다.
    외부 데이터는 연도별로 늦게 들어와 일부가 빌 수 있어 멈추지 않는다. 학습에서 빠진 외부 피처는 external_dropped에 남기고,
    전부 빠지면 그 모델이 v1과 같은 피처로 학습돼 앙상블 효과가 없으므로 경고한다.
    """
    source = frame.attrs.get("source")
    members, latest, tested, holdout_seconds, dropped = {}, [], [], 0.0, {}
    for name, external in MEMBERS.items():
        rows, now, columns = split_rows(frame, external)
        if external != "none":
            dropped[name] = [c for c in MARKET + WDI if c not in columns]
            if len(dropped[name]) == len(MARKET + WDI):
                log.warning("%s: 외부 피처 %d개가 모두 비어 v1과 같은 피처로 학습합니다 (data/external · availability.csv 확인)",
                            name, len(dropped[name]))
            elif dropped[name]:
                log.info("%s: 비어 있어 학습에서 뺀 외부 피처 %s", name, dropped[name])
        test_anchor = rows.anchor.max()
        early = (rows.anchor <= test_anchor - pd.DateOffset(months=F.HORIZON)).to_numpy()
        test = rows[(rows.anchor == test_anchor).to_numpy()]
        part = fit(rows[early], columns, method, k, n_jobs=n_jobs)
        holdout_seconds += part["fit_seconds"]
        tested.append(predict(part, test))
        members[name] = fit(rows, columns, method, k, n_jobs=n_jobs)
        latest.append(predict(members[name], now))
    scores = combine(tested)
    evaluation = _finite({"test_anchor": str(test_anchor.date()), "train_end": str(rows.anchor[early].max().date()),
                          "train_rows": int(early.sum()), "test_rows": len(test), "fit_seconds": holdout_seconds,
                          "model": evaluate_predictions(test, scores.order.to_numpy())[0],
                          "model_stage1": evaluate_predictions(test, scores.growth.to_numpy())[0],
                          "baseline_growth_12m": evaluate_predictions(
                              test, test[F.A_TARGETS[F.HORIZON][1]].to_numpy(dtype=float))[0]})
    bundle = {"members": members, "method": method, "k": k,
              "latest": combine(latest).assign(exp_12m=now.exp_12m.to_numpy()),  # query가 이 파일 하나로 조회
              "features": {name: m["features"] for name, m in members.items()}, "external_dropped": dropped,
              "target_clip": rows.y_growth.quantile(ExperimentConfig().target_clip).tolist(),
              "fit_seconds": sum(m["fit_seconds"] for m in members.values()), "feature_set": "two_stage_" + "_".join(MEMBERS),
              "train_rows": len(rows), "train_start": str(rows.anchor.min().date()),
              "train_end": str(rows.anchor.max().date())}
    return bundle, evaluation, source


def model_name(method, k, external, version):
    return f"two_stage_{'b' if method == 'B' else f'c{k}'}_{'base' if external == 'none' else 'ext'}_{version}"


def train(args):
    path = args.out_dir / f"{model_name(args.method, args.k, args.external, args.version)}.joblib"
    if path.exists():
        raise SystemExit(f"이미 있음, 덮어쓰지 않음: {path}")
    rows, latest, columns = load_rows(args.snapshot, args.external)
    print(f"학습 시작: {rows.anchor.min().date()}~{rows.anchor.max().date()} {len(rows):,}행, 1단계 피처 {len(columns)}개, "
          f"방식 {args.method}{'' if args.method == 'B' else f' (K={args.k})'}", flush=True)
    bundle = fit(rows, columns, args.method, args.k, args.seed, args.n_jobs)
    bundle.update(external=args.external, version=args.version, runtime=_runtime("lightgbm"),
                  trained_at=datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds"),
                  latest=predict(bundle, latest).assign(exp_12m=latest.exp_12m.to_numpy()))
    args.out_dir.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".joblib.tmp")
    joblib.dump(bundle, temporary)
    again = predict(joblib.load(temporary), latest)  # 다시 읽은 파일로 같은 결과가 나오는지 확인
    if not np.allclose(again.order, bundle["latest"].order, rtol=0, atol=1e-9):
        raise ValueError(f"{temporary}: 다시 읽은 모델의 예측이 다릅니다")
    temporary.replace(path)
    print(f"저장 {path} ({path.stat().st_size / 1e6:.1f}MB, 학습 {bundle['fit_seconds']:.0f}초, "
          f"최신 기준월 {latest.anchor.max().date()} {len(latest):,}쌍 예측 포함)", flush=True)


def query(args):
    countries = S.countries()
    countries = countries[countries.country_role == "COUNTRY"]
    code = country_code(args.country, countries) if args.country else None
    hs6 = args.hs6.strip() if args.hs6 else None
    if hs6 is not None and not (len(hs6) == 6 and hs6.isdigit()):
        raise SystemExit("--hs6는 6자리 숫자입니다 (예: 850760)")
    bundles = [joblib.load(p) for p in args.model]
    pred = combine([b["latest"] for b in bundles]).assign(exp_12m=bundles[0]["latest"].exp_12m.to_numpy())
    pred = pred.assign(growth_pct=np.expm1(pred.growth) * 100)
    names = S.hs_names()
    label = " + ".join(p.name for p in args.model)
    if code and hs6:
        name = countries.set_index("country_code").name_ko[code]
        in_country, in_product = pred[pred.country_code == code], pred[pred.hs6 == hs6]
        pair = in_country[in_country.hs6 == hs6]
        print(f"{label} | {name}({code}) × {hs6} {item_name(hs6, names, width=60)}")
        if pair.empty:
            print("예측 대상이 아닙니다 (최근 12개월 수출 $100K 이상인 쌍만 예측합니다)")
            return
        row = pair.iloc[0]
        print(f"예측 증감률(약) {row.growth_pct:+.1f}% | 최근 12개월 수출 {row.exp_12m:,.0f} USD")
        print(f"{name} 안 순위(2단계) {int((in_country.order > row.order).sum()) + 1} / {len(in_country)}개 품목 | "
              f"{hs6} 안 순위(1단계) {int((in_product.growth > row.growth).sum()) + 1} / {len(in_product)}개국")
        return
    if code:
        rows = pred[pred.country_code == code].nlargest(args.top, "order")
        rows = rows.assign(name=rows.hs6.map(lambda h: item_name(h, names)))[["hs6", "name", "growth_pct", "exp_12m"]]
        header, title = ["HS6", "품목명", "예측 증감률(%)", "최근 12개월 수출(USD)"], f"{countries.set_index('country_code').name_ko[code]}({code}) → 유망 품목"
    else:
        rows = pred[pred.hs6 == hs6].nlargest(args.top, "growth")
        rows = rows.merge(countries[["country_code", "name_ko"]], on="country_code", how="left")[
            ["country_code", "name_ko", "growth_pct", "exp_12m"]]
        header, title = ["국가코드", "국가명", "예측 증감률(%)", "최근 12개월 수출(USD)"], f"{hs6} {item_name(hs6, names, width=60)} → 유망 국가"
    print(f"{label} | 기준월 {pred.anchor.max().date()} | {title} Top {args.top}")
    if rows.empty:
        print("예측 대상이 없습니다 (최근 12개월 수출 $100K 이상인 쌍만 예측합니다)")
        return
    rows = rows.assign(exp_12m=rows.exp_12m.map("{:,.0f}".format))
    rows.columns = header
    rows.insert(0, "순위", range(1, len(rows) + 1))
    with pd.option_context("display.unicode.east_asian_width", True, "display.width", 200):
        print(rows.round({"예측 증감률(%)": 1}).to_string(index=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("train", help="전체 정답 행으로 2단계 모델을 학습해 joblib으로 저장")
    t.add_argument("--method", choices=["B", "C"], default="C")
    t.add_argument("--k", type=int, default=100, help="C 방식의 국가별 후보 수")
    t.add_argument("--external", choices=["none", "first_release"], default="none", help="none = v1 피처, first_release = v3 피처")
    t.add_argument("--version", default="v1")
    t.add_argument("--seed", type=int, default=0)
    t.add_argument("--n-jobs", type=int, default=4)
    t.add_argument("--snapshot", type=Path, default=SNAPSHOT_PY311)
    t.add_argument("--out-dir", type=Path, default=OUT_DIR)
    q = sub.add_parser("query", help="저장한 2단계 모델(여러 개면 앙상블)로 국가 · 품목 조회")
    q.add_argument("--model", type=Path, nargs="+", required=True)
    q.add_argument("--country")
    q.add_argument("--hs6")
    q.add_argument("--top", type=int, default=5)
    args = parser.parse_args()
    if args.cmd == "train":
        train(args)
    elif not args.country and not args.hs6:
        parser.error("--country 또는 --hs6를 지정하세요")
    elif args.top < 1:
        parser.error("--top은 1 이상입니다")
    else:
        query(args)


if __name__ == "__main__":
    main()
