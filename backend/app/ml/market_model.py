"""수출 유망 시장 모델 학습·평가·예측 (docs/ML_PLAN.md 6·7·8장).

- 모델 A (유망도): 다음 12개월 수출 성장 회귀. 기능 ① 국가→품목 Top 5, ② 품목→국가 Top 5를 같은 점수로 낸다.
- 모델 B (시장 유형): 미래 12개월 실적으로 만든 5개 유형 분류. 기능 ③.

실행 (저장소 루트에서):
    python -m backend.app.ml.market_model train            # DB·외부 CSV로 학습·평가 후 data/ml/에 저장
    python -m backend.app.ml.market_model query --country VN
    python -m backend.app.ml.market_model query --hs6 850760
    python -m backend.app.ml.market_model query --country VN --hs6 850760
"""
import argparse
import json
from datetime import datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.metrics import confusion_matrix, f1_score, ndcg_score

from backend.app.ml import features as F

OUT_DIR = Path(__file__).resolve().parents[3] / "data" / "ml"
TOP_N = 5
MIN_CANDIDATES = 10  # 국가별 순위 평가에 쓰는 최소 후보 품목 수
GAP_MONTHS = F.HORIZON  # 학습 마지막 기준월과 평가 기준월 사이 간격 (y가 12개월 앞을 봐서 겹치지 않게)


def regressor():
    return HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05, categorical_features=F.CATEGORICAL,
                                         random_state=0)


def classifier():
    return HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, categorical_features=F.CATEGORICAL,
                                          class_weight="balanced", random_state=0)


def clip_y(y, low, high):
    return y.clip(low, high)


def split(labeled):
    """평가 기준월 = y가 있는 마지막 기준월, 학습 = 그보다 12개월 이상 앞선 기준월."""
    test_anchor = labeled.anchor.max()
    train = labeled[labeled.anchor <= test_anchor - pd.DateOffset(months=GAP_MONTHS)]
    if train.empty:
        raise ValueError(f"학습 기간 부족: y가 있는 기준월이 {GAP_MONTHS + 1}개월 이상 필요")
    return train, labeled[labeled.anchor == test_anchor]


def rank_metrics(test, score):
    """국가별 Precision@5·NDCG@5 평균. 정답은 실제 y_growth 상위 5개 품목."""
    precision, ndcg = [], []
    for _, g in test.assign(score=score).groupby("country_code"):
        if len(g) < MIN_CANDIDATES:
            continue
        actual = set(g.nlargest(TOP_N, "y_growth").hs6)
        precision.append(len(actual & set(g.nlargest(TOP_N, "score").hs6)) / TOP_N)
        relevance = g.y_growth.rank(pct=True).to_numpy()[None, :]
        ndcg.append(ndcg_score(relevance, g.score.to_numpy()[None, :], k=TOP_N))
    return {"precision_at_5": float(np.mean(precision)) if precision else None,
            "ndcg_at_5": float(np.mean(ndcg)) if ndcg else None, "countries": len(precision)}


def fit_a(rows):
    low, high = rows.y_growth.quantile([0.01, 0.99])  # 극단 성장률은 1%·99%에서 자른다
    model = regressor().fit(rows[F.FEATURES], clip_y(rows.y_growth, low, high))
    return model


def labeled_b(rows, th):
    y = F.future_type(rows, th)
    return rows[~np.isnan(y)], y[~np.isnan(y)].astype(int)


def evaluate(ds):
    """시간 순 분할로 모델 A·B를 평가하고 기준선과 비교한다."""
    train, test = split(ds[ds.y_growth.notna()])
    report = {"train_anchors": [str(train.anchor.min().date()), str(train.anchor.max().date())],
              "test_anchor": str(test.anchor.max().date()), "train_rows": len(train), "test_rows": len(test)}
    model_a = fit_a(train)
    report["model_a"] = rank_metrics(test, model_a.predict(test[F.FEATURES]))
    report["baseline_a"] = rank_metrics(test, test.growth_12m.to_numpy())  # 작년 성장률 상위 5개
    th = F.type_thresholds(train)
    x_tr, y_tr = labeled_b(train, th)
    x_te, y_te = labeled_b(test, th)
    if len(np.unique(y_tr)) < 2 or x_te.empty:
        report["model_b"] = "라벨 부족 (외부 시장 데이터가 없는 연도)"
        return report
    pred = classifier().fit(x_tr[F.FEATURES], y_tr).predict(x_te[F.FEATURES])
    base = F.past_type(x_te, th)
    ok = ~np.isnan(base)
    report["model_b"] = {"macro_f1": float(f1_score(y_te, pred, average="macro")),
                         "confusion": confusion_matrix(y_te, pred, labels=list(F.TYPE_NAMES)).tolist(),
                         "rows": len(y_te)}
    report["baseline_b"] = {"macro_f1": float(f1_score(y_te[ok], base[ok].astype(int), average="macro")),
                            "rows": int(ok.sum())}
    return report


def train(ds, out_dir=OUT_DIR):
    """평가 → 전체 기간으로 다시 학습 → 마지막 기준월 예측을 저장한다."""
    report = evaluate(ds)
    labeled = ds[ds.y_growth.notna()]
    model_a = fit_a(labeled)
    th = F.type_thresholds(labeled)
    x_b, y_b = labeled_b(labeled, th)
    model_b = classifier().fit(x_b[F.FEATURES], y_b) if len(np.unique(y_b)) >= 2 else None
    latest = ds[ds.anchor == ds.anchor.max()]
    pred = latest[["country_code", "hs6"]].assign(score=model_a.predict(latest[F.FEATURES]))
    pred["rank_in_country"] = pred.groupby("country_code").score.rank(ascending=False, method="first").astype(int)
    pred["rank_in_product"] = pred.groupby("hs6").score.rank(ascending=False, method="first").astype(int)
    pred["market_type"] = model_b.predict(latest[F.FEATURES]) if model_b else F.past_type(latest, th)
    pred["market_type_name"] = pred.market_type.map(F.TYPE_NAMES)
    out_dir.mkdir(parents=True, exist_ok=True)
    pred.to_csv(out_dir / "predictions.csv", index=False)
    joblib.dump({"model_a": model_a, "model_b": model_b}, out_dir / "models.joblib")
    meta = {"trained_at": datetime.now().isoformat(timespec="seconds"), "predict_anchor": str(latest.anchor.max().date()),
            "features": F.FEATURES, "thresholds": th, "min_export_usd": F.MIN_EXPORT_USD,
            "model_b": "classifier" if model_b else "rule (라벨 부족)", "evaluation": report}
    (out_dir / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def load_predictions(out_dir=OUT_DIR):
    return pd.read_csv(out_dir / "predictions.csv", dtype={"hs6": str}, keep_default_na=False, na_values=[""])


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
    return None if row.empty else row.iloc[0].market_type_name


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("train", help="DB·외부 CSV로 학습·평가하고 예측을 data/ml/에 저장")
    q = sub.add_parser("query", help="저장된 예측 조회")
    q.add_argument("--country")
    q.add_argument("--hs6")
    args = parser.parse_args()
    if args.cmd == "train":
        meta = train(F.build_dataset(F.load_data()))
        print(json.dumps(meta["evaluation"], ensure_ascii=False, indent=2))
        print(f"저장: {OUT_DIR} (예측 기준월 {meta['predict_anchor']})")
    elif args.country and args.hs6:
        print(market_type_of(args.country, args.hs6) or "예측 대상 아님 (최근 12개월 수출이 기준 미만)")
    elif args.country:
        print(top_products(args.country).to_string(index=False))
    elif args.hs6:
        print(top_countries(args.hs6).to_string(index=False))
    else:
        parser.error("--country 또는 --hs6를 지정")


if __name__ == "__main__":
    main()
