"""모델 A 비교 지표. 회귀 점수를 확률로 취급하지 않는다."""
import warnings

import numpy as np
import pandas as pd
from scipy.stats import ConstantInputWarning, rankdata, spearmanr
from sklearn.metrics import (accuracy_score, average_precision_score, balanced_accuracy_score,
                             confusion_matrix, explained_variance_score, f1_score,
                             matthews_corrcoef, mean_absolute_error, mean_squared_error,
                             median_absolute_error, ndcg_score, precision_score, r2_score,
                             recall_score, roc_auc_score)


def finite(value):
    return float(value) if value is not None and np.isfinite(value) else None


try:  # ndcg_score에서 입력 검사만 뺀 같은 계산. 그룹 수천 개를 채점할 때 검사가 시간의 대부분이다.
    from sklearn.metrics._ranking import _ndcg_sample_scores

    def _ndcg(relevance, score, k):
        return float(np.average(_ndcg_sample_scores(relevance, score, k=k, ignore_ties=False)))
except ImportError:  # 내부 함수가 없는 scikit-learn 버전이면 공개 함수를 쓴다
    def _ndcg(relevance, score, k):
        return float(ndcg_score(relevance, score, k=k))


def _spearman(a, b):
    """pandas Series.corr(method="spearman")가 내부에서 쓰는 scipy spearmanr와 같은 값.
    점수나 정답이 모두 같으면 정의되지 않아 미평가(None)로 두고, 그때 나는 경고는 숨긴다."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ConstantInputWarning)
        return finite(spearmanr(a, b)[0])


def evaluate_predictions(rows, score, ks=(5, 10), min_candidates=10, threshold=0.0):
    """공통 평가 행의 원래 y_growth로 측정. 그룹 최소 수를 못 채우면 미평가."""
    y = rows.y_growth.to_numpy(dtype=float)
    score = np.asarray(score, dtype=float)
    if score.shape != y.shape or not len(y) or not np.isfinite(y).all() or not np.isfinite(score).all():
        raise ValueError("정답과 예측은 같은 길이의 비어 있지 않은 유한 벡터여야 합니다")
    grew, called = y > 0, score > threshold
    both = len(np.unique(grew)) == 2
    tn, fp, fn, tp = confusion_matrix(grew, called, labels=[False, True]).ravel()
    summary = {
        "rows": len(y), "mae": float(mean_absolute_error(y, score)),
        "rmse": float(np.sqrt(mean_squared_error(y, score))),
        "median_ae": float(median_absolute_error(y, score)),
        "r2": finite(r2_score(y, score, force_finite=False)) if len(y) > 1 else None,
        "explained_variance": finite(explained_variance_score(y, score, force_finite=False)) if len(y) > 1 else None,
        "pearson": finite(pd.Series(y).corr(pd.Series(score))),
        "spearman": _spearman(y, score),
        "actual_growth_rate": float(grew.mean()), "predicted_growth_rate": float(called.mean()),
        "direction_threshold": threshold, "accuracy": float(accuracy_score(grew, called)),
        "balanced_accuracy": float(balanced_accuracy_score(grew, called)) if both else None,
        "precision": float(precision_score(grew, called, zero_division=0)),
        "recall": float(recall_score(grew, called, zero_division=0)),
        "f1": float(f1_score(grew, called, zero_division=0)),
        "mcc": float(matthews_corrcoef(grew, called)) if both else None,
        "roc_auc": float(roc_auc_score(grew, score)) if both else None,
        "average_precision": float(average_precision_score(grew, score)) if both else None,
        "tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp),
    }
    details = []
    min_size = max(min_candidates, min(ks))
    for direction, group, item in [("country", "country_code", "hs6"), ("product", "hs6", "country_code")]:
        # 그룹마다 DataFrame을 만들고 정렬하던 방식(조합당 1분 이상)을 배열 한 번 정렬로 바꾼다. 결과는 같다.
        # 순서: 그룹 키 오름차순(groupby sort=True와 같음), 그룹 안에서는 식별자 오름차순.
        g_codes, g_names = pd.factorize(rows[group].to_numpy(), sort=True)
        i_codes = pd.factorize(rows[item].to_numpy(), sort=True)[0]
        order = np.lexsort((i_codes, g_codes))
        ys, ss, items = y[order], score[order], rows[item].to_numpy()[order]
        codes, starts, counts = np.unique(g_codes[order], return_index=True, return_counts=True)
        for code, start, n in zip(codes, starts, counts):
            # 결측 키(-1)는 groupby처럼 빼고, 모든 K에서 미평가인 작은 그룹은 계산하지 않는다
            if code < 0 or n < min_size:
                continue
            yy, sc, it = ys[start:start + n], ss[start:start + n], items[start:start + n]
            # 동점은 식별자 순서. 내림차순 안정 정렬은 pandas sort_values(ascending=False, kind="stable")와 같다.
            actual = np.argsort(-yy, kind="stable")
            predicted = np.argsort(-sc, kind="stable")
            relevance = (rankdata(yy) / n)[None, :]  # pandas rank(pct=True): 평균 순위 / 개수
            correlation = _spearman(yy, sc)
            for k in ks:
                if n < max(min_candidates, k):
                    continue
                top = predicted[:k]
                picked = set(it[top])
                top_k = set(it[actual[:k]])
                top20 = set(it[actual[:max(1, int(np.ceil(n * .2)))]])
                hits = len(picked & top20)
                p, r = hits / k, hits / len(top20)
                details.append({"direction": direction, "group": str(g_names[code]), "k": k, "candidates": int(n),
                                "precision_at_k": len(picked & top_k) / k,
                                "hit_at_k": float(bool(picked & top_k)),
                                "ndcg_at_k": _ndcg(relevance, sc[None, :], k),
                                "top20_precision_at_k": p, "top20_recall_at_k": r,
                                "top20_f1_at_k": 2*p*r/(p+r) if p+r else 0.0,
                                "grew_share_at_k": float((yy[top] > 0).mean()),
                                "mean_actual_growth_at_k": float(yy[top].mean()),
                                "spearman": correlation})
        for k in ks:
            selected = [d for d in details if d["direction"] == direction and d["k"] == k]
            prefix = f"{direction}_"
            summary[f"{prefix}groups_at_{k}"] = len(selected)
            for name in ["precision_at_k", "hit_at_k", "ndcg_at_k", "top20_precision_at_k", "top20_recall_at_k",
                         "top20_f1_at_k", "grew_share_at_k", "mean_actual_growth_at_k", "spearman"]:
                values = [d[name] for d in selected if d[name] is not None]
                label = name.replace("_at_k", f"_at_{k}") if name != "spearman" else f"spearman_at_{k}"
                summary[prefix + label] = float(np.mean(values)) if values else None
    return summary, pd.DataFrame(details)


METRIC_GUIDE = {
    "mae / rmse / median_ae": "원래 로그 성장 정답의 오차. 낮을수록 좋음. 금액 오차 아님.",
    "r2 / explained_variance": "회귀 설명력. 음수 가능. 정답 상수/표본 부족이면 미평가.",
    "spearman / pearson": "전체 행의 순위/선형 상관. 상수이면 미평가.",
    "accuracy / balanced_accuracy / precision / recall / f1 / mcc": "실제 y>0, 예측 score>설정 임계값(기본 0)의 증가 여부. 전체 행 기준.",
    "roc_auc / average_precision": "증가 여부를 연속 score로 평가. Average precision은 AP이며 사다리꼴 PR-AUC가 아님. 한 클래스면 미평가.",
    "country / product": "각각 국가별 품목 추천 / 품목별 국가 추천. 그룹 동일 가중 평균.",
    "precision_at_K / hit_at_K": "실제 Top K와 추천 Top K의 교집합/K / 하나라도 적중한 그룹 비율.",
    "ndcg_at_K": "실제 로그 성장값의 그룹 내 백분위 순위를 relevance로 사용. 정확도·성공 확률 아님.",
    "top20_precision / recall / f1": "실제 상위 ceil(후보수×20%)를 정답으로 보고 추천 K개를 평가.",
    "grew_share / mean_actual_growth": "추천 K개 중 실제 증가 비율 / 실제 로그 성장 평균.",
    "groups_at_K": "후보수 >= max(min_candidates,K) 조건을 만족한 그룹 수. 그룹 없으면 지표 미평가.",
    "fold 평균 / 표준편차": "기준월과 seed별 기술 통계. 겹치는 미래 정답 창이 있어 독립 표본 신뢰구간 아님.",
    "tn / fp / fn / tp": "증가 여부 혼동행렬. 예측 score는 확률이 아니므로 log-loss/Brier는 계산하지 않음.",
}
