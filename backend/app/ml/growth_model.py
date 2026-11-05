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

이 모듈은 학습·예측과 채팅 에이전트용 파일 저장(save·build)을 담당한다. 월별 재학습은 candidate_models.run이 모델 A 다음에
monthly()로 학습해 model_b_growth_<YYYYMM>.joblib과 레지스트리(model_key=model_b)를 남긴다(모델 A와 같은 절차).

채팅 에이전트(agents/market.py)는 build가 만든 파일 하나만 읽는다(모델 A 월별 파일과 같은 방식):
    data/ml/monthly_models/model_b_growth_<YYYYMM>.joblib   # YYYYMM = 최신 기준월
파일에는 최신 기준월 전체 쌍의 예측(latest: anchor, country_code, hs6, growth_pct, direction)이 들어 있어
워커가 피처 스냅샷·외부 데이터를 다시 읽지 않는다. 에이전트는 모델 A 월별 파일과 기준월이 같을 때만 쓰므로
모델 A와 같은 월별 피처 표로 만든다(모델 A 월별 학습과 같은 수집 원본·피처 캐시):
    python -m backend.app.ml.growth_model --month 2026-09
--month 없이 --snapshot만 주면 스냅샷 피처 표로 만든다(스냅샷은 매월 갱신되지 않아 기준월이 다를 수 있다).
"""
import argparse
import logging
import time
from pathlib import Path

import joblib
import numpy as np

from backend.app.ml import features as F
from backend.app.ml import two_stage as T
from backend.app.ml.candidate_models import _finite, _runtime
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


def monthly(frame, n_jobs=4, seed=0):
    """candidate_models.run(월별 ML 단계)용: two_stage.monthly와 같은 절차로 모델 B를 학습한다.

    Holdout: 마지막 정답 기준월을 평가월로, 그보다 12개월 이상 앞선 기준월로 학습해 방향 정답률을 잰다(모두 감소 기준선과 비교).
    전체: 정답 있는 모든 행으로 v1·v3를 학습하고 최신 기준월 전체 쌍의 예측(latest)을 담는다.
    반환: (bundle, evaluation, source). bundle은 fit_members 결과 + latest·anchor.
    """
    import pandas as pd
    source = frame.attrs.get("source")
    rows, latest, _ = T.split_rows(frame, "first_release")
    test_anchor = rows.anchor.max()
    early = (rows.anchor <= test_anchor - pd.DateOffset(months=F.HORIZON)).to_numpy()
    test = rows[(rows.anchor == test_anchor).to_numpy()]
    part = fit_members(rows[early], seed=seed, n_jobs=n_jobs)
    tested = predict(part, test)
    actual = np.expm1(test.y_growth.to_numpy(dtype=float)) * 100
    truth = direction(actual, part["boundary_pct"])
    evaluation = _finite({"test_anchor": str(test_anchor.date()), "train_end": str(rows.anchor[early].max().date()),
                          "train_rows": int(early.sum()), "test_rows": len(test), "fit_seconds": part["fit_seconds"],
                          "direction_accuracy": float(np.mean(tested.direction.to_numpy() == truth)),
                          "baseline_all_down": float(np.mean(truth == DOWN)),
                          "growth_mae_pp": float(np.mean(np.abs(tested.growth_pct.to_numpy() - actual)))})
    bundle = fit_members(rows, seed=seed, n_jobs=n_jobs)
    now = predict(bundle, latest)
    if now.empty or now.anchor.nunique() != 1:
        raise ValueError("최신 기준월 예측이 비었거나 기준월이 여러 개입니다")
    bundle.update(latest=now, anchor=str(now.anchor.iloc[0].date()), holdout_seconds=part["fit_seconds"])
    return bundle, evaluation, source


OUT_DIR = T.ROOT / "data/ml/monthly_models"   # 모델 A 월별 파일과 같은 폴더. 에이전트가 여기서 가장 최근 파일을 읽는다


def save(bundle, latest_rows, path):
    """bundle(fit_members)에 최신 기준월 예측(latest)을 담아 저장한다. 다시 읽어 같은 예측이 나오는지 확인한 뒤 바꿔 놓는다.

    latest_rows: 정답이 없는 최신 기준월 행(two_stage.split_rows의 두 번째 값). 기준월은 하나여야 한다.
    """
    path = Path(path)
    latest = predict(bundle, latest_rows)
    if latest.empty or latest.anchor.nunique() != 1:
        raise ValueError("최신 기준월 예측이 비었거나 기준월이 여러 개입니다")
    saved = {**bundle, "latest": latest, "anchor": str(latest.anchor.iloc[0].date())}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".joblib.tmp")
    joblib.dump(saved, temporary)
    again = predict(joblib.load(temporary), latest_rows)
    if not np.allclose(again.growth_pct, latest.growth_pct, rtol=0, atol=1e-9):
        temporary.unlink(missing_ok=True)
        raise ValueError(f"{temporary}: 다시 읽은 모델의 예측이 다릅니다")
    temporary.replace(path)
    return path


def build(snapshot_path=T.SNAPSHOT_PY311, out_dir=OUT_DIR, seed=0, n_jobs=4, month=None):
    """v1·v3를 학습하고 model_b_growth_<YYYYMM>.joblib로 저장한다.

    month(YYYY-MM)를 주면 모델 A 월별 학습(candidate_models._month_frame)과 같은 피처 표를 그대로 쓴다(외부 피처를 따로
    채우지 않는다. 월별 모델 B·모델 A와 같은 피처로 학습한다). 없으면 스냅샷 피처 표(two_stage.load_frame, 외부 피처 포함)다.
    """
    if month:
        import pandas as pd
        from backend.app.ml.candidate_models import FEATURE_CACHE, _month_frame
        frame = _month_frame(pd.Timestamp(f"{month}-01"), FEATURE_CACHE)
    else:
        frame = T.load_frame(snapshot_path, external=True)
    rows, latest, _ = T.split_rows(frame, "first_release")
    bundle = fit_members(rows, seed=seed, n_jobs=n_jobs)
    anchor = latest.anchor.max()
    path = Path(out_dir) / f"model_b_growth_{anchor:%Y%m}.joblib"
    if path.exists():
        raise FileExistsError(f"이미 있음, 덮어쓰지 않음: {path}")
    return save(bundle, latest, path)


def main(argv=None):
    parser = argparse.ArgumentParser(description="증감 예상 모델(모델 B)을 학습해 채팅 에이전트용 파일로 저장")
    parser.add_argument("--month", help="모델 A 월별 파일과 같은 기준월(YYYY-MM). 주면 월별 피처 표로 학습한다")
    parser.add_argument("--snapshot", type=Path, default=T.SNAPSHOT_PY311)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--n-jobs", type=int, default=4)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    print(f"저장 {build(args.snapshot, args.out_dir, args.seed, args.n_jobs, args.month)}")


if __name__ == "__main__":
    main()
