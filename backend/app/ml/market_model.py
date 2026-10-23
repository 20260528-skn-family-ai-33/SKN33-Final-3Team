"""수출 유망 시장 모델 학습·평가·예측 (docs/ML_PLAN.md 6·7·8장).

- 모델 A (유망도): 다음 12개월 수출 성장 회귀. 기능 ① 국가→품목 Top 5, ② 품목→국가 Top 5를 같은 점수로 낸다.
  evaluate --horizon 6은 정답을 "다음 6개월 vs 1년 전 같은 6개월"로 바꿔 평가만 한다(저장 안 함).
- 모델 B (시장 유형): 미래 12개월 실적으로 만든 5개 유형 분류. 기능 ③. horizon과 무관하게 12개월.

실행 (저장소 루트에서):
    python -m backend.app.ml.market_model train --promotion-metric country_ndcg_at_5
    python -m backend.app.ml.market_model evaluate --horizon 6
    python -m backend.app.ml.market_model query --country VN
    python -m backend.app.ml.market_model query --hs6 850760
    python -m backend.app.ml.market_model query --country VN --hs6 850760
"""
import argparse
import json
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.metrics import (accuracy_score, confusion_matrix, f1_score, mean_absolute_error, mean_squared_error,
                             ndcg_score, precision_score, r2_score, recall_score, roc_auc_score)
from sklearn.pipeline import make_pipeline

from backend.app.ml import features as F
from backend.app.ml.transforms import DropEmptyColumns, TrainingClipper

OUT_DIR = Path(__file__).resolve().parents[3] / "data" / "ml"
TOP_N = 5
MIN_CANDIDATES = 10  # 국가별 순위 평가에 쓰는 최소 후보 품목 수
GAP_MONTHS = F.HORIZON  # 학습 마지막 기준월과 평가 기준월 사이 간격 (y가 12개월 앞을 봐서 겹치지 않게)
TOP_SHARE = 0.2  # 보고서 추가 지표의 "유망" 정답: 그룹 안 실제 성장 상위 20% (계획서 7장의 이진 분류 정의)
log = logging.getLogger(__name__)


def regressor():
    return make_pipeline(DropEmptyColumns(), TrainingClipper(), HistGradientBoostingRegressor(
        max_iter=300, learning_rate=0.05, categorical_features=F.CATEGORICAL, random_state=0))


def classifier():
    return make_pipeline(DropEmptyColumns(), TrainingClipper(), HistGradientBoostingClassifier(
        max_iter=300, learning_rate=0.05, categorical_features=F.CATEGORICAL,
        class_weight="balanced", random_state=0))


def clip_y(y, low, high):
    return y.clip(low, high)


def split(labeled, gap=GAP_MONTHS):
    """평가 기준월 = y가 있는 마지막 기준월, 학습 = 그보다 gap(정답 기간)개월 이상 앞선 기준월."""
    test_anchor = labeled.anchor.max()
    train = labeled[labeled.anchor <= test_anchor - pd.DateOffset(months=gap)]
    if train.empty:
        raise ValueError(f"학습 기간 부족: y가 있는 기준월이 {gap + 1}개월 이상 필요")
    return train, labeled[labeled.anchor == test_anchor]


def rank_metrics(test, score, direction="country"):
    """국가별 품목 또는 품목별 국가 순위. 기존 최소 후보 수 기준은 그대로 적용한다."""
    group, item = ("country_code", "hs6") if direction == "country" else ("hs6", "country_code")
    precision, ndcg = [], []
    for _, g in test.assign(score=score).groupby(group):
        if len(g) < MIN_CANDIDATES:
            continue
        actual = set(g.nlargest(TOP_N, "y_growth")[item])
        precision.append(len(actual & set(g.nlargest(TOP_N, "score")[item])) / TOP_N)
        relevance = g.y_growth.rank(pct=True).to_numpy()[None, :]
        ndcg.append(ndcg_score(relevance, g.score.to_numpy()[None, :], k=TOP_N))
    return {"precision_at_5": float(np.mean(precision)) if precision else None,
            "ndcg_at_5": float(np.mean(ndcg)) if ndcg else None,
            "countries" if direction == "country" else "products": len(precision)}


def _finite(value):
    return float(value) if value is not None and np.isfinite(value) else None


def detail_metrics(test, score):
    """보고서용 추가 지표. 모델 채택은 rank_metrics의 지표 하나로만 정한다.

    - 순위(국가별·품목별 그룹 평균): Hit@5, 상위 20% 정답 기준 Precision·Recall·F1@5, 추천 5개 중 실제 증가 비율,
      NDCG@10, Spearman 순위상관
    - 증가 여부 분류(전체 행): 실제 y>0을 점수>0으로 맞혔는지. Accuracy·Precision·Recall·F1·ROC-AUC
    - 회귀(전체 행): 로그 성장 예측값 자체의 MAE·RMSE·R²·Spearman
    """
    t = test.assign(score=score)
    y, s = t.y_growth.to_numpy(), t.score.to_numpy()
    grew, called = y > 0, s > 0
    report = {
        "regression": {"mae": _finite(mean_absolute_error(y, s)), "rmse": _finite(np.sqrt(mean_squared_error(y, s))),
                       "r2": _finite(r2_score(y, s)), "spearman": _finite(t.y_growth.corr(t.score, method="spearman"))},
        "growth_direction": {"actual_rate": _finite(grew.mean()), "accuracy": _finite(accuracy_score(grew, called)),
                             "precision": _finite(precision_score(grew, called, zero_division=0)),
                             "recall": _finite(recall_score(grew, called, zero_division=0)),
                             "f1": _finite(f1_score(grew, called, zero_division=0)),
                             "roc_auc": _finite(roc_auc_score(grew, s)) if len(np.unique(grew)) == 2 else None}}
    for direction, (group, item) in (("country", ("country_code", "hs6")), ("product", ("hs6", "country_code"))):
        rows = []
        for _, g in t.groupby(group):
            if len(g) < MIN_CANDIDATES:
                continue
            top = g.nlargest(TOP_N, "score")
            picked = set(top[item])
            relevant = set(g.nlargest(int(np.ceil(len(g) * TOP_SHARE)), "y_growth")[item])
            hits = len(picked & relevant)
            p, r = hits / TOP_N, hits / len(relevant)
            rows.append({"hit_at_5": float(bool(picked & set(g.nlargest(TOP_N, "y_growth")[item]))),
                         "top20_precision_at_5": p, "top20_recall_at_5": r,
                         "top20_f1_at_5": 2 * p * r / (p + r) if hits else 0.0,
                         "grew_share_at_5": float((top.y_growth > 0).mean()),
                         "ndcg_at_10": ndcg_score(g.y_growth.rank(pct=True).to_numpy()[None, :],
                                                  g.score.to_numpy()[None, :], k=10),
                         "spearman": g.y_growth.corr(g.score, method="spearman")})
        report[direction] = {k: _finite(v) for k, v in pd.DataFrame(rows).mean().items()} if rows else {}
        report[direction]["groups"] = len(rows)
    return report


def available_b(rows, cutoff):
    """해당 학습 시점까지 공개된 미래 시장 정답만 사용한다."""
    if "type_available_from" not in rows:
        raise ValueError("시장 유형 정답의 사용 가능일(type_available_from)이 없습니다")
    cutoff = pd.Timestamp(cutoff) + pd.offsets.MonthEnd(0)
    return rows[pd.to_datetime(rows.type_available_from) <= cutoff].dropna(
        subset=["y_growth", "future_cv", "future_mkt_growth", "future_share"])


def fit_a(rows, features=None):
    features = F.FEATURES if features is None else features
    log.info("모델 A 학습 시작: %s행, 후보 피처 %s개", f"{len(rows):,}", len(features))
    low, high = rows.y_growth.quantile([0.01, 0.99])  # 극단 성장률은 1%·99%에서 자른다
    model = regressor().fit(rows[features], clip_y(rows.y_growth, low, high))
    log.info("모델 A 학습 완료: 사용 피처 %s개", len(model[0].keep_))
    return model


def labeled_b(rows, th):
    y = F.future_type(rows, th)
    return rows[~np.isnan(y)], y[~np.isnan(y)].astype(int)


def evaluate(ds, horizon=F.HORIZON):
    """시간 순 분할로 모델 A·B를 평가하고 기준선과 비교한다. horizon은 모델 A의 정답 기간(개월)."""
    target, base = F.A_TARGETS[horizon]
    a = ds if horizon == F.HORIZON else ds.assign(y_growth=ds[target])
    train, test = split(a[a.y_growth.notna()], horizon)
    report = {"horizon_months": horizon, "baseline_rule": base,
              "train_anchors": [str(train.anchor.min().date()), str(train.anchor.max().date())],
              "test_anchor": str(test.anchor.max().date()), "train_rows": len(train), "test_rows": len(test)}
    model_a = fit_a(train)
    score = model_a.predict(test[F.FEATURES])
    report["features_used"] = model_a[0].keep_
    report["missing_training_features"] = [c for c in F.FEATURES if c not in model_a[0].keep_]
    report["model_a"] = rank_metrics(test, score)
    report["baseline_a"] = rank_metrics(test, test[base].to_numpy())  # 12개월: 작년 성장률 상위 5개
    report["model_a_product"] = rank_metrics(test, score, "product")
    report["baseline_a_product"] = rank_metrics(test, test[base].to_numpy(), "product")
    report["detail"] = {"model_a": detail_metrics(test, score), "baseline_a": detail_metrics(test, test[base].to_numpy())}
    without_score = None
    if set(F.RELATED_FEATURES) & set(report["features_used"]):
        without = fit_a(train, [c for c in F.FEATURES if c not in F.RELATED_FEATURES])
        without_score = without.predict(test[F.FEATURES])
        report["without_related"] = {"model_a": rank_metrics(test, without_score),
            "model_a_product": rank_metrics(test, without_score, "product"),
            "features_used": without[0].keep_, "detail": detail_metrics(test, without_score)}
    semiconductor = test.hs6.str[:4].isin(["8541", "8542"]).to_numpy()
    report["segments"] = {}
    for name, mask in (("semiconductor", semiconductor), ("other", ~semiconductor)):
        rows = test.loc[mask]
        report["segments"][name] = {"rows": len(rows),
            "model_a": rank_metrics(rows, score[mask]),
            "baseline_a": rank_metrics(rows, rows[base].to_numpy()),
            "model_a_product": rank_metrics(rows, score[mask], "product"),
            "baseline_a_product": rank_metrics(rows, rows[base].to_numpy(), "product")}
        if without_score is not None:
            report["segments"][name]["without_related"] = {
                "model_a": rank_metrics(rows, without_score[mask]),
                "model_a_product": rank_metrics(rows, without_score[mask], "product")}
    if horizon != F.HORIZON:
        train, test = split(ds[ds.y_growth.notna()])  # 모델 B는 12개월 정답·간격 그대로
    known_train = available_b(train, test.anchor.max())
    th = F.type_thresholds(known_train)
    x_tr, y_tr = labeled_b(known_train, th)
    x_te, y_te = labeled_b(available_b(test, ds.anchor.max()), th)
    if len(np.unique(y_tr)) < 2 or x_te.empty:
        report["model_b"] = "라벨 부족 (외부 시장 데이터가 없는 연도)"
        return report
    pred = classifier().fit(x_tr[F.FEATURES], y_tr).predict(x_te[F.FEATURES])
    base = F.past_type(x_te, th)
    ok = ~np.isnan(base)
    labels = list(F.TYPE_NAMES)
    report["model_b_all"] = {"macro_f1": float(f1_score(y_te, pred, labels=labels, average="macro", zero_division=0)),
                         "confusion": confusion_matrix(y_te, pred, labels=list(F.TYPE_NAMES)).tolist(),
                         "rows": len(y_te)}
    report["model_b"] = {"macro_f1": float(f1_score(y_te[ok], pred[ok], labels=labels, average="macro", zero_division=0)) if ok.any() else None,
                         "rows": int(ok.sum())}
    report["baseline_b"] = {"macro_f1": float(f1_score(y_te[ok], base[ok].astype(int), labels=labels, average="macro", zero_division=0)) if ok.any() else None,
                            "rows": int(ok.sum())}
    return report


PROMOTION_METRICS = {
    f"{direction}_{metric}": ("model_a" + suffix, "baseline_a" + suffix, metric)
    for direction, suffix in (("country", ""), ("product", "_product"))
    for metric in ("precision_at_5", "ndcg_at_5")
}


class PromotionRejected(ValueError):
    def __init__(self, message, report):
        super().__init__(message)
        self.report = report


def require_promotion(report, promotion_metric):
    if promotion_metric not in PROMOTION_METRICS:
        raise PromotionRejected("팀에서 채택 지표를 정한 뒤 promotion_metric을 지정하세요", report)
    model_key, base_key, metric = PROMOTION_METRICS[promotion_metric]
    model, base = report[model_key][metric], report[base_key][metric]
    if model is None or base is None or not np.isfinite([model, base]).all() or model < base:
        raise PromotionRejected(f"모델 저장 중단: {promotion_metric} 미평가 또는 기준선 미달", report)


def select_a_features(report, promotion_metric):
    """같은 평가 행에서 관련 품목 피처를 뺀 모델이 채택 지표를 더 잘 맞히면 그 모델을 쓴다."""
    report["model_a_variant"] = "with_related"
    comparison = report.get("without_related")
    if promotion_metric not in PROMOTION_METRICS or not comparison:
        return F.FEATURES
    model_key, _, metric = PROMOTION_METRICS[promotion_metric]
    full, reduced = report[model_key][metric], comparison[model_key][metric]
    if reduced is None or not np.isfinite(reduced):
        return F.FEATURES
    if full is not None and np.isfinite(full) and reduced <= full:
        return F.FEATURES
    report["with_related"] = {key: report[key] for key in ("model_a", "model_a_product", "features_used")}
    for key in ("model_a", "model_a_product", "features_used"):
        report[key] = comparison[key]
    if "detail" in comparison:
        report["detail"]["model_a"] = comparison["detail"]
    report["missing_training_features"] = [c for c in F.FEATURES if c not in report["features_used"]]
    for segment in report.get("segments", {}).values():
        segment["with_related"] = {key: segment[key] for key in ("model_a", "model_a_product")}
        segment.update(segment["without_related"])
    report["model_a_variant"] = "without_related"
    return [c for c in F.FEATURES if c not in F.RELATED_FEATURES]


def use_classifier(report):
    model, base = report.get("model_b"), report.get("baseline_b")
    return (isinstance(model, dict) and isinstance(base, dict)
            and model["macro_f1"] is not None and base["macro_f1"] is not None
            and np.isfinite([model["macro_f1"], base["macro_f1"]]).all()
            and model["macro_f1"] >= base["macro_f1"])


def train(ds, out_dir=OUT_DIR, promotion_metric=None, data_info=None, registry_engine=None, register=True):
    """평가 → 전체 기간으로 다시 학습 → 마지막 기준월 예측을 저장한다."""
    report = evaluate(ds)
    features = select_a_features(report, promotion_metric)
    require_promotion(report, promotion_metric)  # 저장 전 검사: 기존 산출물을 덮어쓰지 않는다.
    labeled = ds[ds.y_growth.notna()]
    model_a = fit_a(labeled, features)
    known = available_b(labeled, ds.anchor.max())
    th = F.type_thresholds(known)
    x_b, y_b = labeled_b(known, th)
    model_b = classifier().fit(x_b[F.FEATURES], y_b) if use_classifier(report) and len(np.unique(y_b)) >= 2 else None
    latest = ds[ds.anchor == ds.anchor.max()]
    pred = latest[["country_code", "hs6"]].assign(score=model_a.predict(latest[F.FEATURES]))
    pred["rank_in_country"] = pred.groupby("country_code").score.rank(ascending=False, method="first").astype(int)
    pred["rank_in_product"] = pred.groupby("hs6").score.rank(ascending=False, method="first").astype(int)
    pred["market_type"] = model_b.predict(latest[F.FEATURES]) if model_b else F.past_type(latest, th)
    pred["market_type_name"] = pred.market_type.map(F.TYPE_NAMES)
    meta = {"trained_at": datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="microseconds"),
            "predict_anchor": str(latest.anchor.max().date()),
            "features": F.FEATURES, "features_used": model_a[0].keep_, "thresholds": {k: v if np.isfinite(v) else None for k, v in th.items()},
            "min_export_usd": F.MIN_EXPORT_USD,
            "model_b": "classifier" if model_b else "rule (라벨·비교 평가 부족 또는 기준선 미달)",
            "model_a_variant": report["model_a_variant"],
            "promotion_metric": promotion_metric, "evaluation": report,
            "training_target_clip": dict(zip(("low", "high"), labeled.y_growth.quantile([.01, .99]).tolist()))}
    from backend.app.ml.artifacts import save_version
    b_rows = x_b.assign(training_target_b=y_b) if model_b is not None else x_b.iloc[:0]
    return save_version({"model_a": model_a, "model_b": model_b}, pred, meta,
                        {"model_a": labeled, "model_b": b_rows}, out_dir,
                        data_info=data_info, eligible_b_rows=len(x_b), registry_engine=registry_engine, register=register)


def load_predictions(out_dir=OUT_DIR, version_id=None):
    from backend.app.ml.artifacts import artifact_path
    return pd.read_csv(artifact_path(out_dir, "predictions.csv", version_id=version_id), dtype={"hs6": str},
                       keep_default_na=False, na_values=[""])


def load_models(out_dir=OUT_DIR, version_id=None):
    from backend.app.ml.artifacts import artifact_path
    from backend.app.ml.runtime import ensure_artifact_runtime
    ensure_artifact_runtime(out_dir, version_id)
    return joblib.load(artifact_path(out_dir, "models.joblib", version_id=version_id))


def top_products(country_code, n=TOP_N, out_dir=OUT_DIR):
    """기능 ①: 국가에서 유망한 품목 Top n."""
    p = load_predictions(out_dir)
    return p[p.country_code == country_code].nsmallest(n, "rank_in_country")


def top_countries(hs6, n=TOP_N, out_dir=OUT_DIR):
    """기능 ②: 품목이 유망한 국가 Top n."""
    p = load_predictions(out_dir)
    return p[p.hs6 == hs6].nsmallest(n, "rank_in_product")


def market_type_of(country_code, hs6, out_dir=OUT_DIR):
    """기능 ③: 국가·품목 시장 유형. 최근 12개월 수출이 기준 미만이라 예측 대상이 아니면 None."""
    p = load_predictions(out_dir)
    row = p[(p.country_code == country_code) & (p.hs6 == hs6)]
    return None if row.empty or pd.isna(row.iloc[0].market_type_name) else row.iloc[0].market_type_name


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("train", help="수집 파일과 환율 DB로 학습·평가하고 예측을 data/ml/에 저장")
    t.add_argument("--promotion-metric", required=True, choices=list(PROMOTION_METRICS), help="팀이 채택한 모델 A 비교 지표")
    t.add_argument("--feature-cache-dir", type=Path, help="월별 피처 캐시를 재사용하고 영향받은 기준월만 갱신")
    e = sub.add_parser("evaluate", help="저장 없이 학습·평가만 해서 보고서 출력")
    e.add_argument("--horizon", type=int, default=F.HORIZON, choices=sorted(F.A_TARGETS), help="모델 A 정답 기간(개월)")
    q = sub.add_parser("query", help="저장된 예측 조회")
    q.add_argument("--country")
    q.add_argument("--hs6")
    args = parser.parse_args()
    if args.cmd == "train":
        try:
            from backend.app.ml.artifacts import load_training_data
            ds, data, info = load_training_data(cache_dir=args.feature_cache_dir)
            del data
            meta = train(ds, promotion_metric=args.promotion_metric, data_info=info)
        except PromotionRejected as exc:
            print(json.dumps(exc.report, ensure_ascii=False, indent=2))
            parser.exit(1, str(exc) + "\n")
        print(json.dumps(meta["evaluation"], ensure_ascii=False, indent=2))
        print(f"저장: {OUT_DIR} (예측 기준월 {meta['predict_anchor']})")
    elif args.cmd == "evaluate":
        from backend.app.ml.artifacts import load_training_data
        ds, _, _ = load_training_data()
        print(json.dumps(evaluate(ds, args.horizon), ensure_ascii=False, indent=2))
    elif args.country and args.hs6:
        print(market_type_of(args.country, args.hs6) or "시장 유형 미판정 또는 예측 대상 아님")
    elif args.country:
        print(top_products(args.country).to_string(index=False))
    elif args.hs6:
        print(top_countries(args.hs6).to_string(index=False))
    else:
        parser.error("--country 또는 --hs6를 지정")


if __name__ == "__main__":
    main()

