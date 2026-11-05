"""증감 예상 모델(모델 B): 모델 A와 분리한 Huber 회귀로 예상 증감률과 증가·감소 예상을 낸다.

모델 A(two_stage)의 1단계 성장률은 추천 순위를 위한 값이라 L2 손실·기본 설정을 쓴다. 이 모듈은 화면의
"증가 예상 / 감소 예상" 표시용으로 따로 학습한다. A와 학습 행·피처 후보·v1·v3 구성은 같고 다음 세 가지가 다르다.

- 손실·설정: LightGBM Huber 손실 (HUBER). 끊긴 거래(-100%) 같은 극단 정답에 덜 끌려 예측이 감소 쪽으로 덜 쏠린다.
  학습 절차(정답 분위 절단·범주 처리)는 A 1단계와 같은 two_stage._regressor를 설정만 바꿔 호출한다.
- 피처: 순열 중요도로 고른 16개를 뺀다 (DROP_FEATURES). 나머지는 학습 행에 값이 있는 A 후보 피처를 모두 쓴다.
  v1 20개, v3는 학습 기준월이 2024-08까지면 25개, 2025년 이후 기준월이 들어가면 시장 변화 피처 3개가 더해져 28개
  (2026-11 스냅샷 기준).
- 환율: features.py가 DB fx_daily(ECOS)로 만든 fx_vol_12m을 쓴다 (fx_change_12m은 DROP_FEATURES에 있음).
  환율이 비어 있으면 coverage에서 빠지므로 멈추지 않는다.

입력: 외부 피처(시장·WDI)를 붙인 표로 만든 정답 있는 학습 행 (two_stage.load_frame(external=True) → split_rows).
외부 피처가 없으면 v3가 v1과 같은 피처로 학습되므로 경고하고 빠진 피처를 external_dropped에 남긴다 (two_stage.monthly와 같음).
출력: (기준월, 국가, HS6) + growth_pct(= expm1(v1·v3 평균 로그 성장) × 100) + direction(BOUNDARY_PCT 이상이면 UP, 미만이면 DOWN).
화면 문구("증가 예상" 등)는 쓰는 쪽에서 붙인다.

검증 (main 4299707, 기존 = 2단계 1단계 회귀와 같은 L2 기본 설정):
- Dev 확인 2025-05~09 (254,913건): 방향 정답률 59.72% → 61.12% (모두 감소 56.25%)
- 2025-08 사후 Holdout (학습 2023-12~2024-08, 50,992건, 시드 0·1·2 평균): 59.29% → 60.66%,
  국가 단위 부트스트랩 95% +0.95 ~ +1.78%p, 성장률 MAE 75.66 → 73.19%p, 실제 증가 포착 26.8% → 34.4%
- 사후 재검증이라 독립 시험은 아님. 성장률 숫자 자체는 오차가 커서 화면에는 방향만 보여 주는 것을 전제로 한다.

이 모듈은 학습·예측만 담당한다. 월별 재학습(candidate_models.run)과 조회(candidate_query) 연결은 별도 작업이다.
"""
import logging
import time

import numpy as np

from backend.app.ml import two_stage as T
from backend.app.ml.candidate_models import _runtime
from backend.app.ml.experiments import ExperimentConfig
from backend.app.ml.feature_recovery import MARKET, WDI

HUBER = dict(objective="huber", alpha=1.0, num_leaves=63, n_estimators=1000, learning_rate=.02,
             min_child_samples=300, colsample_bytree=.7, subsample=.8, subsample_freq=1, reg_lambda=5)
DROP_FEATURES = ("gdp_growth", "region_id", "grp2_c_growth_12m", "news_all_3m", "rel_input_imp_growth_3m",
                 "imp_hs4_world_growth", "news_3m", "news_change", "grp4_c_growth_3m", "hs4_unit_trend",
                 "rel_input_imp_growth_12m", "fx_change_12m", "imp_hs4_from_c_growth", "grp4_c_growth_12m",
                 "country_growth", "grp4_world_growth_12m")
BOUNDARY_PCT = 0.0  # 예측 증감률이 이 값 이상이면 UP
UP, DOWN = "UP", "DOWN"  # 코드값. 화면 문구는 쓰는 쪽에서 붙인다
MEMBERS = T.MEMBERS  # {"base": "none"(v1), "ext": "first_release"(v3)}
log = logging.getLogger(__name__)


def feature_columns(rows, external="none"):
    """A 후보 피처에서 DROP_FEATURES를 먼저 뺀 뒤, 학습 행에 값이 있는 것만 남긴 목록.

    먼저 빼므로 제외 대상 열이 없는 입력(16개를 미리 지운 표)도 그대로 받는다.
    """
    return T._available(rows, [c for c in T._candidates(external) if c not in DROP_FEATURES])


def fit(rows, external="none", seed=0, n_jobs=4):
    """rows: 정답(y_growth)이 있는 학습 행. 정답 절단·범주 처리는 A 1단계(two_stage._regressor)와 같다."""
    if rows.empty or not np.isfinite(rows.y_growth).all():
        raise ValueError("학습 행이 없거나 y_growth에 결측·무한값이 있습니다")
    started = time.perf_counter()
    columns = feature_columns(rows, external)
    model = T._regressor(T._x(rows, columns), rows.y_growth, columns, seed, n_jobs, params=HUBER)
    return {"model": model, "features": columns, "external": external, "seed": seed,
            "target_clip": rows.y_growth.quantile(ExperimentConfig().target_clip).tolist(),
            "fit_seconds": time.perf_counter() - started}


def fit_members(rows, seed=0, n_jobs=4):
    """MEMBERS(v1·v3)를 같은 학습 행으로 각각 학습한 묶음 (학습 기간·실행 환경·빠진 외부 피처 포함)."""
    members, dropped = {}, {}
    for name, external in MEMBERS.items():
        members[name] = fit(rows, external, seed, n_jobs)
        if external != "none":
            dropped[name] = [c for c in MARKET + WDI if c not in DROP_FEATURES and c not in members[name]["features"]]
            if not set(members[name]["features"]) & set(MARKET + WDI):
                log.warning("%s: 외부 피처가 모두 비어 v1과 같은 피처로 학습합니다 "
                            "(load_frame(external=True)로 만든 행인지, data/external · availability.csv 확인)", name)
            elif dropped[name]:
                log.info("%s: 비어 있어 학습에서 뺀 외부 피처 %s", name, dropped[name])
    return {"kind": "growth_huber", "members": members, "params": dict(HUBER),
            "drop_features": list(DROP_FEATURES), "boundary_pct": BOUNDARY_PCT,
            "features": {name: m["features"] for name, m in members.items()}, "external_dropped": dropped,
            "train_rows": len(rows), "train_start": str(rows.anchor.min().date()),
            "train_end": str(rows.anchor.max().date()), "seed": seed,
            "fit_seconds": sum(m["fit_seconds"] for m in members.values()), "runtime": _runtime("lightgbm")}


def direction(growth_pct, boundary=BOUNDARY_PCT):
    """반올림 전 예측 증감률(%)을 UP·DOWN으로. 경계값과 같으면 UP."""
    return np.where(np.asarray(growth_pct, dtype=float) >= boundary, UP, DOWN)


def predict(bundle, rows):
    """(기준월, 국가, HS6) + growth_pct + direction. v1·v3 로그 성장 예측을 평균한 뒤 퍼센트로 바꾼다."""
    logs = [m["model"].predict(T._x(rows, m["features"])) for m in bundle["members"].values()]
    pct = np.expm1(np.mean(logs, axis=0)) * 100
    if not np.isfinite(pct).all():
        raise ValueError("예측 증감률에 결측·무한값이 있습니다")
    out = rows[T.KEYS].reset_index(drop=True)
    return out.assign(growth_pct=pct, direction=direction(pct, bundle.get("boundary_pct", BOUNDARY_PCT)))
