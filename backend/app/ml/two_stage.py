"""2단계 구성: 1단계 회귀(다음 12개월 성장률) → 2단계 순위 학습(같은 기준월 · 같은 국가 안 품목 순서).

방식
    B (스태킹)  : 1단계 교차 예측값을 피처로 넣은 순위 학습이 국가 안 전체 후보를 정렬한다
    C (필터링 K): 1단계로 국가별 상위 K개만 남기고 그 안을 순위 학습이 다시 정렬한다 (나머지는 1단계 순서로 그 아래)
쓰임
    국가 → 유망 품목 순위 = 2단계 국가 방향 순서(order) / 품목 → 유망 국가 순위 = 2단계 품목 방향 순서(product)
    예측 증감률 = 1단계 성장률(growth). 국가 방향 순위 모델은 국가 묶음으로만 학습해 국가끼리 비교할 수 없으므로
    같은 기준월 · 같은 품목 안 국가 순서를 따로 학습한다 (품목마다 수출국이 적어 후보를 거르지 않는다, 6차 테스트).
교차 예측
    2단계 학습 행의 1단계 값은 (국가, HS6) 쌍 단위로 나눈 교차 예측이다. 같은 행으로 학습한 회귀 예측을 그대로 쓰면
    실제보다 잘 맞는 값이라 2단계가 그 피처를 과신한다. 쌍 단위 값은 같은 기준월의 다른 쌍을 본 모델이 만들어
    실제 예측 때보다 조금 유리하지만, 기준월 단위로 나누면 학습 기준월이 적어 1단계 값이 오히려 크게 약해진다.
    2026-10-28 재측정(미래 기준월 평가, 시드 3개)에서 기준월 단위는 앙상블 2025-09 국가 NDCG@5가 0.7283 → 0.7125,
    Dev 0.7448 → 0.7360으로 떨어져 2단계를 쓰지 않은 회귀(0.7126)와 같아졌으므로 쌍 단위를 쓴다.
파일
    data/ml/candidates/two_stage_<b|cK>_<base|ext>_<버전>.joblib. 딕셔너리에 두 Pipeline · 피처 · 방식과
    학습 시점의 최신 기준월 예측(latest)을 함께 둬 조회는 파일만 읽는다.
    월별 재학습(candidate_models.run → monthly)은 MEMBERS 구성(최종: v1 · v3 앙상블)의 모델과 앙상블 latest를
    data/ml/monthly_models/model_a_two_stage_<YYYYMM>.joblib 하나에 담는다 (query --model 그 파일 하나로 조회).

실행 (저장소 루트에서):
    python -m backend.app.ml.two_stage train --method C --k 100 --external first_release --version v1
    python -m backend.app.ml.two_stage query --model data/ml/candidates/two_stage_c100_ext_v1.joblib --country 베트남
    python -m backend.app.ml.two_stage query --model 파일1 파일2 --country VN --hs6 850760   (여러 개면 앙상블)
    python -m backend.app.ml.two_stage report --registry data/ml/monthly_models/model_a_two_stage_202609.registry.json
    python -m backend.app.ml.two_stage kfold --folds 8 --seeds 0 1 2   (시간 순서 K-fold 검증 → data/ml/reports/*.md · *.json)
"""
import argparse
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import joblib
import numpy as np
import pandas as pd
from sklearn.model_selection import GroupKFold
from threadpoolctl import threadpool_limits

from backend.app.ml import features as F
from backend.app.ml import sources as S
from backend.app.ml.candidate_models import OUT_DIR, _finite, _runtime, needed_columns
from backend.app.ml.candidate_query import item_name, pair_rank, resolve_target
from backend.app.ml.experiment_metrics import evaluate_predictions
from backend.app.ml.experiments import (FEATURE_SETS, ROOT, SNAPSHOT_PY311, ExperimentConfig, ModelSpec, build_model,
                                        load_snapshot, prepare_scenarios)
from backend.app.ml.feature_recovery import MARKET, WDI, coverage, recover_external

KEYS = ["anchor", "country_code", "hs6"]
GROUP = ["anchor", "country_code"]
PRODUCT_GROUP = ["anchor", "hs6"]
GRADES = 30  # 2단계 정답 = 그룹 안 성장 백분위를 0~30 등급으로 (평가 NDCG와 같은 선형 이득)
FOLDS = 3
OFFSET = 1e3  # C 방식에서 후보(2단계 점수)를 후보 밖(1단계 성장률, 약 -21~8)보다 항상 위에 둔다
log = logging.getLogger(__name__)


def load_frame(snapshot_path=SNAPSHOT_PY311, external=True):
    """스냅샷 피처 표. 외부 피처가 비어 있고 external이면 availability.csv 공개일로 붙인다(v3)."""
    snapshot = load_snapshot(snapshot_path)
    frame = snapshot[[*needed_columns(), "exp_12m"]]
    frame.attrs = dict(snapshot.attrs)
    del snapshot
    if external and frame[MARKET + WDI].isna().all().all():
        frame, _ = recover_external(frame)
    return frame


def load_rows(snapshot_path=SNAPSHOT_PY311, external="none"):
    """스냅샷으로 split_rows."""
    return split_rows(load_frame(snapshot_path, external == "first_release"), external)


def _candidates(external):
    """external="none"은 외부 피처 9개를 쓰지 않고(v1), "first_release"는 frame에 붙어 있는 외부 피처를 쓴다(v3)."""
    if external not in ("none", "first_release"):
        raise ValueError(f"external은 none 또는 first_release: {external}")
    return [c for c in FEATURE_SETS["all"] if external != "none" or c not in MARKET + WDI]


def _available(rows, candidates):
    stats = coverage(rows[candidates].replace([np.inf, -np.inf], np.nan), candidates)
    return stats.loc[stats.status == "available", "feature"].tolist()


def split_rows(frame, external="none"):
    """(정답 있는 학습 행, 최신 기준월 행, 1단계 피처). 둘 다 (기준월, 국가, HS6) 키 순서."""
    candidates = _candidates(external)
    frame = prepare_scenarios({"original": frame}, F.HORIZON)["original"]
    labeled = frame.y_growth.notna().to_numpy()
    rows = frame[labeled]
    latest = frame[(frame.anchor == frame.anchor.max()).to_numpy()]
    del frame
    return rows, latest, _available(rows, candidates)


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


def _ranker(x, rows, columns, seed, n_jobs, group=GROUP):
    """같은 group 행이 연속이어야 한다 (rows는 키 순서라 (기준월, 국가) 그룹이 연속)."""
    from lightgbm import LGBMRanker
    model, cat = build_model(ModelSpec("lightgbm", "lightgbm"), columns, seed, n_jobs)
    model.set_params(model=LGBMRanker(objective="lambdarank", n_estimators=300, learning_rate=.05, num_leaves=31,
                                      label_gain=list(range(GRADES + 1)), verbosity=-1, n_jobs=n_jobs, random_state=seed))
    grades = (rows.groupby(group, sort=False).y_growth.rank(pct=True) * GRADES).round().astype(int)
    sizes = rows.groupby(group, sort=False).size().to_numpy()
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
    by_product = np.lexsort((rows.country_code.to_numpy(), rows.hs6.to_numpy(), rows.anchor.to_numpy()))
    stage2_product = _ranker(xs.iloc[by_product], rows.iloc[by_product], stacked, seed, n_jobs, PRODUCT_GROUP)
    return {"stage1": stage1, "stage2": stage2, "stage2_product": stage2_product, "features": columns, "stage2_features": stacked, "method": method,
            "k": k if method == "C" else None, "folds": folds, "seed": seed, "train_rows": len(rows),
            "stage2_rows": int(keep.sum()), "train_start": str(rows.anchor.min().date()),
            "train_end": str(rows.anchor.max().date()), "fit_seconds": time.perf_counter() - started}


def predict(bundle, rows):
    """growth = 1단계 성장률, order = 국가 안 순서 점수, product = 품목 안 순서 점수 (클수록 위).

    품목 방향 모델이 없는 예전 파일은 product = growth (1단계 순서)."""
    x = _x(rows, bundle["features"])
    growth = bundle["stage1"].predict(x)
    keep = _top_k(rows, growth, bundle["k"]) if bundle["method"] == "C" else np.ones(len(rows), bool)
    order = growth.astype(float)
    if keep.any():
        stacked = x.assign(stage1_pred=growth)[keep]
        order[keep] = OFFSET + bundle["stage2"].predict(stacked[bundle["stage2_features"]])
    product = growth
    if "stage2_product" in bundle:
        product = bundle["stage2_product"].predict(x.assign(stage1_pred=growth)[bundle["stage2_features"]])
    return rows[KEYS].assign(growth=growth, order=order, product=product)


def combine(predictions):
    """여러 2단계 모델의 앙상블: 두 방향 순서는 전체 백분위 순위 평균(실험 5와 같은 방식), 성장률은 평균."""
    base = predictions[0][KEYS].reset_index(drop=True)
    for p in predictions[1:]:
        if not p[KEYS].reset_index(drop=True).equals(base):
            raise ValueError("앙상블할 모델의 예측 대상(기준월 · 국가 · HS6)이 다릅니다")
    return base.assign(growth=np.mean([p.growth.to_numpy() for p in predictions], axis=0),
                       order=np.mean([p.order.rank(pct=True).to_numpy() for p in predictions], axis=0),
                       product=np.mean([(p["product"] if "product" in p else p.growth).rank(pct=True).to_numpy()
                                        for p in predictions], axis=0))  # 예전 파일의 latest에는 product가 없다


# 최종 모델 = v1 · v3 피처 2단계 C(K=100) 앙상블. 같은 시드끼리 v3 단독과 비교하면 NDCG@5가 기준월 3개 × 시드 3개
# 9번 모두 같거나 높다 (5차 테스트). v3 단독은 {"ext": "first_release"} (학습 약 1.6배 빠름, Hit@5는 기준월마다 엇갈림).
MEMBERS = {"base": "none", "ext": "first_release"}


def monthly(frame, n_jobs=2, method="C", k=100):
    """candidate_models.run(월별 ML 단계)용: MEMBERS 2단계 모델을 전체 정답 행으로 학습해 한 묶음으로 돌려준다.

    frame은 수집 원본으로 만든 피처 표(외부 피처는 availability.csv 공개일로 붙은 상태).
    Holdout 평가는 candidate_models.holdout_check와 같은 분할(마지막 정답 기준월, 12개월 이상 앞선 기준월로 학습)이다.
    국가 순위 지표는 국가 방향 순서(model), 품목 순위 지표는 품목 방향 순서(model_product), 증감률 지표는 1단계 성장률(model_stage1)로 잰다.
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
                          "model_product": evaluate_predictions(test, scores["product"].to_numpy())[0],
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
    if not all(np.allclose(again[c], bundle["latest"][c], rtol=0, atol=1e-9) for c in ("order", "growth", "product")):
        raise ValueError(f"{temporary}: 다시 읽은 모델의 예측이 다릅니다")
    temporary.replace(path)
    print(f"저장 {path} ({path.stat().st_size / 1e6:.1f}MB, 학습 {bundle['fit_seconds']:.0f}초, "
          f"최신 기준월 {latest.anchor.max().date()} {len(latest):,}쌍 예측 포함)", flush=True)


def query(args, error):
    """error: 잘못된 입력을 알리고 멈추는 함수 (main은 parser.error)."""
    countries = S.countries()
    countries = countries[countries.country_role == "COUNTRY"]
    code, hs6 = resolve_target(args.country, args.hs6, countries, error)
    bundles = [joblib.load(p) for p in args.model]
    pred = combine([b["latest"] for b in bundles]).assign(exp_12m=bundles[0]["latest"].exp_12m.to_numpy())
    pred = pred.assign(growth_pct=np.expm1(pred.growth) * 100)
    names = S.hs_names()
    label = " + ".join(p.name for p in args.model)
    if code and hs6:
        name = countries.set_index("country_code").name_ko[code]
        print(f"{label} | {name}({code}) × {hs6} {item_name(hs6, names, width=60)}")
        rank = pair_rank(pred, code, hs6, country_by="order", product_by="product")
        if rank is None:
            print("예측 대상이 아닙니다 (최근 12개월 수출 $100K 이상인 쌍만 예측합니다)")
            return
        row = rank["row"]
        print(f"예측 증감률(약) {row.growth_pct:+.1f}% | 최근 12개월 수출 {row.exp_12m:,.0f} USD")
        print(f"{name} 안 순위(2단계) {rank['country_rank']} / {rank['country_size']}개 품목 | "
              f"{hs6} 안 순위(품목 방향) {rank['product_rank']} / {rank['product_size']}개국")
        return
    if code:
        rows = pred[pred.country_code == code].nlargest(args.top, "order")
        rows = rows.assign(name=rows.hs6.map(lambda h: item_name(h, names)))[["hs6", "name", "growth_pct", "exp_12m"]]
        header, title = ["HS6", "품목명", "예측 증감률(%)", "최근 12개월 수출(USD)"], f"{countries.set_index('country_code').name_ko[code]}({code}) → 유망 품목"
    else:
        rows = pred[pred.hs6 == hs6].nlargest(args.top, "product")
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


# 리포트 지표: (이름, 평가 블록, 키). 국가 순위는 국가 방향 순서(model), 품목 순위는 품목 방향 순서(model_product),
# 증감률은 1단계 성장률(model_stage1). 품목 방향 모델 이전 기록은 품목 지표를 model_stage1에서 읽는다
REPORT_METRICS = [
    ("국가 NDCG@5 (채택)", "model", "country_ndcg_at_5"), ("국가 Precision@5", "model", "country_precision_at_5"),
    ("국가 Hit@5", "model", "country_hit_at_5"), ("국가 상위 20% F1@5", "model", "country_top20_f1_at_5"),
    ("국가 NDCG@10", "model", "country_ndcg_at_10"), ("품목 NDCG@5", "model_product", "product_ndcg_at_5"),
    ("품목 Precision@5", "model_product", "product_precision_at_5"), ("품목 Hit@5", "model_product", "product_hit_at_5"),
    ("증가 여부 ROC-AUC", "model_stage1", "roc_auc"), ("성장률 RMSE (작을수록 좋음)", "model_stage1", "rmse"),
]


def report(registry_path, out=None):
    """월별 재학습 기록(<이름>.registry.json)을 Markdown 리포트(<이름>.md)로 만든다. 모델 파일은 읽지 않는다."""
    registry_path = Path(registry_path)
    record = json.loads(registry_path.read_text("utf-8"))
    info, ev = record["data_info"], record["evaluation"]
    num = lambda v: "-" if v is None else f"{v:,.4f}" if isinstance(v, float) else f"{v:,}"  # noqa: E731
    features = info.get("features_used") or {}
    features = features if isinstance(features, dict) else {"model": features}
    dropped = "; ".join(f"{k}: {', '.join(v)}" for k, v in (info.get("external_dropped") or {}).items() if v)
    runtime = info.get("runtime") or {}
    lines = [f"# 모델 A 월별 재학습 리포트 · {record['model_name']}", "",
             "| 항목 | 값 |", "|---|---|",
             f"| 수집 월 | {info.get('trade_month', '-')} |",
             f"| 생성 시각 | {record['created_at'][:19]} |",
             f"| 상태 · 학습 방식 | {record['status']} · {record['training_mode']} |",
             f"| 부모 버전 | {record.get('parent_version_id') or '-'} |",
             f"| 학습 기준월 · 행 | {record['train_start']} ~ {record['train_end']} · {num(record['train_rows'])}행 |",
             f"| 구성 · 1단계 피처 | {', '.join(f'{k} {len(v)}개' for k, v in features.items())} |",
             f"| 빠진 외부 피처 | {dropped or '없음'} |",
             f"| 전체 학습 시간 | {info.get('fit_seconds') or 0:,.0f}초 |",
             f"| 파일 | {record['artifact_path']} (sha256 {record['artifact_sha256'][:12]}…) |",
             f"| 실행 환경 | {', '.join(f'{k} {v}' for k, v in runtime.items())} |", ""]
    if not ev.get("model"):
        lines += ["평가 기록이 없습니다."]
    else:
        base = ev.get("baseline_growth_12m", {})
        lines += [f"## Holdout 평가 (평가 기준월 {ev.get('test_anchor', '-')})", "",
                  f"학습 기준월 ~{ev.get('train_end', '-')} {num(ev.get('train_rows'))}행으로 같은 구성을 학습해 "
                  f"평가 행 {num(ev.get('test_rows'))}개로 쟀다. 기준선은 작년 성장률 순서다.", "",
                  "| 지표 | 최종 모델 | 기준선 | 차이 |", "|---|---|---|---|"]
        for label, block, key in REPORT_METRICS:
            value, ref = (ev.get(block) or ev.get("model_stage1", {})).get(key), base.get(key)
            diff = f"{value - ref:+.4f}" if value is not None and ref is not None else "-"
            lines.append(f"| {label} | {num(value)} | {num(ref)} | {diff} |")
        adopted, ref = ev["model"].get("country_ndcg_at_5"), base.get("country_ndcg_at_5")
        lines += ["", "- 국가 지표는 국가 방향 순서, 품목 지표는 품목 방향 순서, ROC-AUC · RMSE는 1단계 성장률로 잰다. RMSE 말고는 모두 클수록 좋다.",
                  "- READY는 학습 파일이 만들어졌다는 뜻이며, 기준선 미달이어도 저장 · 등록은 막지 않는다."]
        if adopted is not None and ref is not None and adopted <= ref:
            lines.append("- **주의: 채택 지표(국가 NDCG@5)가 기준선 이하입니다. 입력 자료와 학습 결과를 확인하세요.**")
    path = Path(out) if out else registry_path.with_suffix("").with_suffix(".md")
    path.write_text("\n".join(lines) + "\n", "utf-8")
    print("\n".join(lines))
    print(f"\n저장: {path}")
    return path


KFOLD_METRICS = [("국가 NDCG@5 (채택)", "country_ndcg_at_5"), ("국가 Precision@5", "country_precision_at_5"),
                 ("국가 Hit@5", "country_hit_at_5"), ("국가 상위 20% F1@5", "country_top20_f1_at_5"),
                 ("국가 NDCG@10", "country_ndcg_at_10"), ("품목 NDCG@5", "product_ndcg_at_5"),
                 ("품목 Precision@5", "product_precision_at_5"), ("품목 Hit@5", "product_hit_at_5")]


def kfold(frame, n_splits=8, seeds=(0, 1, 2), n_jobs=4, method="C", k=100, min_train_anchors=3, min_train_rows=1000):
    """최종 구성(MEMBERS 앙상블)을 시간 순서 K-fold로 검증한다. frame: 외부 피처가 붙은 피처 표.

    fold = 검증 기준월 하나. 학습은 그보다 12개월 이상 앞선 기준월만 쓰는 확장창이고(validation.temporal_folds),
    피처 선택 · 정답 절단도 fold 학습 행에서만 정한다. 국가 지표는 국가 방향 순서, 품목 지표는 품목 방향 순서로 잰다.
    """
    from backend.app.ml.validation import temporal_folds
    rows = split_rows(frame, "first_release")[0]  # 두 구성의 학습 행은 같고 쓰는 피처만 다르다
    baseline = F.A_TARGETS[F.HORIZON][1]
    keys = [key for _, key in KFOLD_METRICS]
    result = {"method": "purged_expanding_time_kfold", "gap_months": F.HORIZON, "members": dict(MEMBERS),
              "model": {"method": method, "k": k}, "seeds": list(seeds), "folds": []}
    folds = temporal_folds(rows, n_splits, min_train_anchors, min_train_rows)
    if not folds:
        raise ValueError("12개월 분리 후 학습할 수 있는 검증 기준월이 2개 미만입니다")
    for number, fold in enumerate(folds, 1):
        train = rows[(rows.anchor <= fold["train_cutoff"]).to_numpy()]
        test = rows[(rows.anchor == fold["validation_anchor"]).to_numpy()]
        columns = {name: _available(train, _candidates(external)) for name, external in MEMBERS.items()}
        by_seed = []
        for seed in seeds:
            bundles = [fit(train, columns[name], method, k, seed=seed, n_jobs=n_jobs) for name in MEMBERS]
            pred = combine([predict(bundle, test) for bundle in bundles])
            country, _ = evaluate_predictions(test, pred.order.to_numpy())
            product, _ = evaluate_predictions(test, pred["product"].to_numpy())
            scores = {**country, **{key: value for key, value in product.items() if key.startswith("product_")}}
            by_seed.append({"seed": seed, "fit_seconds": sum(b["fit_seconds"] for b in bundles), **{key: scores[key] for key in keys}})
            log.info("K-fold %s 검증 %s 시드 %s: 국가 NDCG@5 %.4f", number, fold["validation_anchor"].date(), seed,
                     scores["country_ndcg_at_5"])
        base, _ = evaluate_predictions(test, test[baseline].to_numpy(dtype=float))
        values = {key: [s[key] for s in by_seed if s[key] is not None] for key in keys}
        result["folds"].append(_finite({
            "fold": number, "validation_anchor": str(fold["validation_anchor"].date()),
            "train_start": str(train.anchor.min().date()), "train_end": str(train.anchor.max().date()),
            "train_anchors": int(train.anchor.nunique()), "train_rows": len(train), "validation_rows": len(test),
            "features": {name: len(c) for name, c in columns.items()},
            "fit_seconds": float(np.mean([s["fit_seconds"] for s in by_seed])), "seeds": by_seed,
            "model": {key: float(np.mean(v)) if v else None for key, v in values.items()},
            "seed_std": {key: float(np.std(v)) if len(v) > 1 else None for key, v in values.items()},
            "baseline": {key: base[key] for key in keys}}))
    summary = {}
    for key in keys:
        model = [f["model"][key] for f in result["folds"] if f["model"][key] is not None]
        base = [f["baseline"][key] for f in result["folds"] if f["baseline"][key] is not None]
        wins = sum(f["model"][key] > f["baseline"][key] for f in result["folds"]
                   if f["model"][key] is not None and f["baseline"][key] is not None)
        summary[key] = {"mean": float(np.mean(model)) if model else None,
                        "fold_std": float(np.std(model, ddof=1)) if len(model) > 1 else None,
                        "min": min(model) if model else None, "max": max(model) if model else None,
                        "baseline_mean": float(np.mean(base)) if base else None, "wins": wins, "valid_folds": len(model)}
    result["summary"] = _finite(summary)
    return result


def kfold_report(result, out_dir, snapshot=None):
    """K-fold 결과를 JSON과 Markdown 리포트로 저장하고 경로를 돌려준다."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(ZoneInfo("Asia/Seoul"))
    # 초 단위 시각 + 짧은 무작위 ID: 같은 분(같은 초)에 끝난 실행끼리 리포트를 덮어쓰지 않는다
    base = out_dir / f"model_a_two_stage_kfold_{stamp:%Y%m%d_%H%M%S}_{uuid4().hex[:6]}"
    result = {**result, "created_at": stamp.isoformat(timespec="seconds"), "snapshot": str(snapshot) if snapshot else None}
    base.with_suffix(".json").write_text(json.dumps(result, ensure_ascii=False, indent=1), "utf-8")
    num = lambda v: "-" if v is None else f"{v:.4f}"  # noqa: E731
    folds, summary = result["folds"], result["summary"]
    lines = ["# 모델 A 최종 모델 시간 순서 K-fold 검증", "",
             f"- 구성 : 2단계 LightGBM {result['model']['method']} (K={result['model']['k']}), "
             f"{' · '.join(f'{n}({e})' for n, e in result['members'].items())} 앙상블, 시드 {', '.join(map(str, result['seeds']))}",
             f"- 방식 : 확장창 K-fold {len(folds)}개. 검증 기준월마다 그보다 {result['gap_months']}개월 이상 앞선 기준월만 학습하고, "
             "같은 기준월은 통째로 검증한다. 피처 선택 · 정답 절단은 fold 학습 행에서만 정한다",
             f"- 작성 : {result['created_at'][:16]}" + (f", 입력 {result['snapshot']}" if result["snapshot"] else ""), "",
             "## 요약 (fold 평균)", "", "| 지표 | 평균 | fold 간 표준편차 | 최소 | 최대 | 기준선 평균 | 기준선보다 높은 fold |",
             "|---|---|---|---|---|---|---|"]
    for label, key in KFOLD_METRICS:
        s = summary[key]
        lines.append(f"| {label} | {num(s['mean'])} | {num(s['fold_std'])} | {num(s['min'])} | {num(s['max'])} | "
                     f"{num(s['baseline_mean'])} | {s['wins']} / {s['valid_folds']} |")
    lines += ["", "## fold별 결과", "", "| fold | 검증 기준월 | 학습 기준월 | 학습 행 | 국가 NDCG@5 (± 시드) | 기준선 | P@5 | Hit@5 | 품목 NDCG@5 | 학습 시간 |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for f in folds:
        m = f["model"]
        lines.append(f"| {f['fold']} | {f['validation_anchor'][:7]} | {f['train_start'][:7]} ~ {f['train_end'][:7]} ({f['train_anchors']}개) | "
                     f"{f['train_rows']:,} | {num(m['country_ndcg_at_5'])} ± {num(f['seed_std']['country_ndcg_at_5'])} | "
                     f"{num(f['baseline']['country_ndcg_at_5'])} | {num(m['country_precision_at_5'])} | {num(m['country_hit_at_5'])} | "
                     f"{num(m['product_ndcg_at_5'])} | {f['fit_seconds']:.0f}초 |")
    lines += ["", "- 기준선은 작년 성장률 순서다. 표준편차 말고는 모두 클수록 좋다.",
              "- fold 사이 미래 12개월 정답 창이 겹치므로 평균 · 표준편차는 기술 통계이며 신뢰구간이 아니다.",
              "- 학습 시간은 시드 평균, 두 모델 합이다."]
    base.with_suffix(".md").write_text("\n".join(lines) + "\n", "utf-8")
    print("\n".join(lines))
    print(f"\n저장: {base.with_suffix('.md')} · {base.with_suffix('.json')}")
    return base.with_suffix(".md"), base.with_suffix(".json")


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
    p = sub.add_parser("report", help="월별 재학습 기록(.registry.json)을 Markdown 리포트로 만들기")
    p.add_argument("--registry", type=Path, required=True)
    p.add_argument("--out", type=Path, help="기본값: 기록 파일 옆 <이름>.md")
    f = sub.add_parser("kfold", help="최종 구성을 시간 순서 K-fold로 검증하고 Markdown · JSON 리포트 저장")
    f.add_argument("--folds", type=int, default=8, help="검증 기준월 수 (가능한 기준월 안에서 고르게)")
    f.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    f.add_argument("--n-jobs", type=int, default=4)
    f.add_argument("--snapshot", type=Path, default=SNAPSHOT_PY311)
    f.add_argument("--out-dir", type=Path, default=ROOT / "data/ml/reports")
    args = parser.parse_args()
    if args.cmd == "train":
        train(args)
    elif args.cmd == "report":
        report(args.registry, args.out)
    elif args.cmd == "kfold":
        if args.folds < 2:
            parser.error("--folds는 2 이상입니다")
        kfold_report(kfold(load_frame(args.snapshot), args.folds, args.seeds, args.n_jobs), args.out_dir, args.snapshot)
    elif not args.country and not args.hs6:
        parser.error("--country 또는 --hs6를 지정하세요")
    elif args.top < 1:
        parser.error("--top은 1 이상입니다")
    else:
        query(args, parser.error)


if __name__ == "__main__":
    main()
