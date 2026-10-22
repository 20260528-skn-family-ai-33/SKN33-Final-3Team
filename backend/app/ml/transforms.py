"""학습 행에서만 추정하는 DataFrame 변환기."""
import numpy as np
from sklearn.base import BaseEstimator, TransformerMixin


class DropEmptyColumns(TransformerMixin, BaseEstimator):
    """학습 행에서 값이 하나도 없는 열(예: 아직 수집하지 않은 환율)을 뺀다. 남긴 열은 keep_에 저장한다."""

    def fit(self, X, y=None):
        self.keep_ = [c for c in X.columns if X[c].notna().any()]
        return self

    def transform(self, X):
        return X[self.keep_]


class TrainingClipper(TransformerMixin, BaseEstimator):
    def __init__(self, columns=("mkt_growth", "mkt_hs4_growth", "kor_share_change", "gdp_growth"),
                 quantiles=(0.01, 0.99)):
        self.columns = columns
        self.quantiles = quantiles

    def fit(self, X, y=None):
        self.bounds_ = {c: tuple(X[c].quantile(self.quantiles)) for c in self.columns if c in X}
        return self

    def transform(self, X):
        out = X.copy()
        for col, (low, high) in self.bounds_.items():
            if np.isfinite(low) and np.isfinite(high):
                out[col] = out[col].clip(low, high)
        return out
