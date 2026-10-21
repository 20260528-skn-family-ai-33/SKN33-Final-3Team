"""학습 행에서만 극단값 경계를 추정하는 DataFrame 변환기."""
import numpy as np
from sklearn.base import BaseEstimator, TransformerMixin


class TrainingClipper(TransformerMixin, BaseEstimator):
    def __init__(self, columns=("mkt_growth", "kor_share_change", "gdp_growth"), quantiles=(0.01, 0.99)):
        self.columns = columns
        self.quantiles = quantiles

    def fit(self, X, y=None):
        self.bounds_ = {c: tuple(X[c].quantile(self.quantiles)) for c in self.columns}
        return self

    def transform(self, X):
        out = X.copy()
        for col, (low, high) in self.bounds_.items():
            if np.isfinite(low) and np.isfinite(high):
                out[col] = out[col].clip(low, high)
        return out
