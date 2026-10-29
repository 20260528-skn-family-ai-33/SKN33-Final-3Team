"""보고서용 산출물 검증과 기준월 단위 시간 순서 K-fold. 원본 수집 정책은 변경하지 않는다."""
import gc
import hashlib
import importlib.metadata
import json
import logging
import platform
from pathlib import Path

import numpy as np
import pandas as pd

from backend.app.ml import features as F, market_model as M, sources as S
from backend.app.ml.runtime import (ArtifactEnvironmentError, ensure_artifact_runtime, environment_message,
                                    expected_runtime, runtime_versions)

ROOT = Path(__file__).resolve().parents[3]
log = logging.getLogger(__name__)


def read_json(path, default=None):
    path = Path(path)
    return json.loads(path.read_text("utf-8")) if path.exists() else default


def file_sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def feature_summary(ds, complete_months=None):
    labeled = ds[ds.y_growth.notna()]
    return {"runtime": runtime_versions(), "rows": len(ds), "anchors": int(ds.anchor.nunique()),
            "first_anchor": str(ds.anchor.min().date()), "last_anchor": str(ds.anchor.max().date()),
            "labeled_rows": len(labeled), "labeled_anchors": int(labeled.anchor.nunique()),
            "customs_complete_months": complete_months,
            "feature_nonmissing": {c: int(ds[c].notna().sum()) for c in F.FEATURES}}


def read_feature_cache(path, data_dir):
    """버전이 다른 pickle을 읽기 전에 원인과 복구 방법을 안내한다."""
    ensure_artifact_runtime(data_dir)
    try:
        return pd.read_pickle(path)
    except ImportError as exc:
        raise ArtifactEnvironmentError(environment_message(expected_runtime(data_dir), runtime_versions(),
            f"피처 캐시 읽기 실패: {type(exc).__name__}: {exc}")) from exc


def require_validation_success(payload):
    """PDF 구조 검사와 전체 검증을 구분하고 마지막에도 실제 실패 원인을 표시한다."""
    problems = list(payload.get("errors", []))
    checks = payload.get("checks", [])
    problems += [f"{c['name']}: {c['detail']}" for c in checks if not c["passed"]]
    if not checks:
        problems.append("산출물 검사가 실행되지 않았습니다.")
    if not payload.get("unit_tests", {}).get("passed"):
        problems.append("단위 테스트 실패 또는 미실행")
    if not payload.get("pdf_check", {}).get("passed"):
        problems.append("PDF 구조 검사 실패 또는 미실행")
    if problems:
        raise RuntimeError("검증 실패 (PDF 생성 성공과 별도):\n" + "\n".join(problems))


def load_context(data_dir, external_dir=None):
    data_dir = Path(data_dir)
    metadata = read_json(data_dir / "metadata.json")
    if metadata is None:
        raise FileNotFoundError(f"학습 결과가 없습니다: {data_dir / 'metadata.json'}")
    external_dir = Path(external_dir or ROOT / "data/external")
    predictions = M.load_predictions(data_dir)
    attempt_path = data_dir / "report_training_attempt.json"
    attempt = read_json(attempt_path, {})
    if attempt and attempt_path.stat().st_mtime < (data_dir / "metadata.json").stat().st_mtime:
        attempt = {}  # 별도 train 명령으로 더 최근에 저장한 모델에는 과거 실패를 붙이지 않는다.
    rejected = attempt.get("status") in ("rejected", "failed")
    evaluation = attempt.get("evaluation", {}) if rejected else metadata["evaluation"]
    summary = read_json(data_dir / "features_snapshot_summary.json", {})
    source_files = S._customs_files(S.CUSTOMS_DIR) + S._customs_files(S.MONTHLY_DIR)
    months = set()
    for path in source_files:
        stats = S._collection_stats(path) or {}
        months.update(stats.get("months", []))
    news_files = sorted(F.SHORTNEWS_DIR.glob("*/shortnews_raw.csv"))
    news_info = {}
    if news_files:
        news = pd.read_csv(news_files[-1], usecols=["nttSn", "nat", "othbcDt"],
                           dtype=str, keep_default_na=False, na_values=[""])
        dates = pd.to_datetime(news.othbcDt, errors="coerce")
        news_info = {"rows": len(news), "countries": int(news.nat.nunique()),
                     "first": str(dates.min().date()), "last": str(dates.max().date())}
    un_info = {}
    un_path = ROOT / "data/un_data/un_data.csv"
    if un_path.exists():
        un = pd.read_csv(un_path, dtype={"hs6_code": str}, keep_default_na=False, na_values=[""])
        un = un[un.freq_code == "M"]
        un_info = {"rows": len(un), "products": int(un.hs6_code.nunique()),
                   "countries": int(un.country_code.nunique()), "first": un.period_start.min(),
                   "last": un.period_start.max()}
    external_info = {}
    external_files = {"baci": [external_dir / "baci/baci_imports_hs22.csv"],
                      "comtrade": sorted((external_dir / "comtrade").glob("comtrade_imports_*.csv")),
                      "wdi": sorted((external_dir / "wdi").glob("wdi_*.csv"))[-1:]}
    for kind, paths in external_files.items():
        years, rows = set(), 0
        for path in paths:
            if path.is_file():
                for chunk in pd.read_csv(path, usecols=["year"], chunksize=300_000):
                    years.update(int(y) for y in chunk.year.dropna().unique())
                    rows += len(chunk)
        external_info[kind] = {"years": sorted(years), "rows": rows}
    names = S.hs_names()
    country_table = S.countries()
    countries = dict(zip(country_table.country_code, country_table.name_ko))
    sample_country = "VN" if (predictions.country_code == "VN").any() else (
        predictions.country_code.iloc[0] if len(predictions) else "")
    preview = predictions[predictions.country_code == sample_country].nsmallest(5, "rank_in_country").copy()
    preview["product_name"] = preview.hs6.map(lambda code: names.get(code, "묶음 대표 코드"))
    groups = pd.read_csv(ROOT / "backend/app/ml/reference/related_hs4.csv", dtype=str)
    return {"data_dir": str(data_dir.resolve()), "metadata": metadata, "evaluation": evaluation,
            "training_attempt": attempt, "run_status": attempt.get("status", "accepted"),
            "error": attempt.get("error", "") if rejected else "",
            "feature_summary": summary,
            "snapshot": read_json(external_dir / "availability_snapshot.json", {}),
            "input_inventory": {"customs_files": len(source_files), "customs_months": len(months),
                "customs_first": min(months) if months else None, "customs_last": max(months) if months else None,
                "news": news_info, "un": un_info, "external": external_info,
                "related_groups": int(groups.group.nunique()),
                "related_rows": len(groups)},
            "predictions_summary": {"rows": len(predictions), "countries": int(predictions.country_code.nunique()),
                "products": int(predictions.hs6.nunique()), "types_known": int(predictions.market_type.notna().sum())},
            "sample_country": sample_country, "sample_country_name": countries.get(sample_country, sample_country),
            "preview": preview.fillna("").to_dict("records"),
            "runtime": {"python": platform.python_version(), **{p: importlib.metadata.version(p)
                for p in ("numpy", "pandas", "scikit-learn", "joblib")}}}


def validate_saved_outputs(context, ds):
    """학습 결과와 현재 캐시·CSV가 같은 실행을 가리키는지 검사한다. 각 검사는 보고서에 남긴다."""
    checks = []

    def check(name, operation):
        try:
            detail = operation()
            checks.append({"name": name, "passed": True, "detail": str(detail or "통과")})
        except Exception as exc:
            checks.append({"name": name, "passed": False, "detail": f"{type(exc).__name__}: {exc}"})

    data_dir = Path(context["data_dir"])
    meta = context["metadata"]
    pred = M.load_predictions(data_dir)

    def keys_and_scores():
        assert not pred.duplicated(["country_code", "hs6"]).any(), "예측 키 중복"
        assert pred.hs6.str.fullmatch(r"\d{6}").all(), "HS6 형식 오류"
        assert np.isfinite(pred.score).all(), "예측 점수 결측·무한대"
        return f"예측 {len(pred):,}행, 고유 키·6자리 HS·유한 점수"

    def rank_order():
        for group, rank in (("country_code", "rank_in_country"), ("hs6", "rank_in_product")):
            ordered = pred.sort_values([group, rank])
            expected = ordered.groupby(group).cumcount().to_numpy() + 1
            np.testing.assert_array_equal(ordered[rank].to_numpy(), expected)
            assert not (ordered.groupby(group).score.diff() > 1e-12).any(), "순위와 점수 정렬 불일치"
        return "국가별·품목별 순위가 1부터 연속이며 점수 내림차순"

    def feature_values():
        assert not np.isinf(ds[F.FEATURES].select_dtypes("number").to_numpy()).any(), "무한대 피처"
        assert (ds.exp_12m >= meta["min_export_usd"]).all(), "최소 수출 필터 위반"
        assert len(ds) == context["feature_summary"].get("rows", len(ds)), "피처 요약 행 수 불일치"
        assert str(ds.anchor.max().date()) == meta["predict_anchor"], "예측 기준월 불일치"
        for name, digest in context["feature_summary"].get("source_sha256", {}).items():
            path = ROOT / name.replace("\\", "/")
            assert file_sha256(path) == digest, f"피처 생성 후 코드 변경: {name}. --train으로 재생성 필요"
        return f"피처 {len(ds):,}행, 무한대 없음, 기간·최소 수출 기준 일치"

    def round_trip():
        latest = ds[ds.anchor == ds.anchor.max()]
        merged = latest.merge(pred[["country_code", "hs6", "score"]], on=["country_code", "hs6"],
                              validate="one_to_one", how="inner")
        assert len(merged) == len(latest) == len(pred), "예측 대상 쌍 불일치"
        model = M.load_models(data_dir)["model_a"]
        values = model.predict(merged[F.FEATURES])
        np.testing.assert_allclose(values, merged.score.to_numpy(), rtol=1e-10, atol=1e-10)
        return f"저장 모델 재로딩 점수와 CSV {len(merged):,}행 일치"

    def promotion():
        assert context["run_status"] == "accepted", (
            f"최근 학습 시도 상태: {context['run_status']}. 완료·채택 여부를 확인할 수 없습니다. "
            f"현재 저장 모델 학습 시각: {meta.get('trained_at', '미확인')}")
        M.require_promotion(context["evaluation"], meta["promotion_metric"])
        return f"{meta['promotion_metric']}: 기준선 이상"

    check("예측 키·점수", keys_and_scores)
    check("양방향 순위", rank_order)
    check("피처·기간·수출 필터", feature_values)
    check("모델·CSV 점수 일치", round_trip)
    check("채택 지표", promotion)
    return checks


def temporal_folds(ds, n_splits=3, min_train_anchors=3, min_train_rows=1000):
    """확장창 K-fold: 같은 기준월은 통째로 검증, train의 정답 끝 <= 검증 기준월."""
    if n_splits < 2 or min_train_anchors < 1 or min_train_rows < 1:
        raise ValueError("K-fold는 최소 2개 fold, 학습 기준월·행 최소값은 1 이상이어야 합니다")
    labeled = ds[ds.y_growth.notna()]
    anchors = pd.DatetimeIndex(sorted(pd.to_datetime(labeled.anchor).unique()))
    candidates = []
    for anchor in anchors:
        train = labeled[labeled.anchor <= anchor - pd.DateOffset(months=F.HORIZON)]
        if train.anchor.nunique() >= min_train_anchors and len(train) >= min_train_rows:
            candidates.append(anchor)
    if len(candidates) < 2:
        return []  # 없는 과거를 만들거나 무작위 K-fold로 바꾸지 않는다.
    chosen = np.linspace(0, len(candidates) - 1, min(n_splits, len(candidates)), dtype=int)
    return [{"validation_anchor": candidates[i],
             "train_cutoff": candidates[i] - pd.DateOffset(months=F.HORIZON)} for i in chosen]


def temporal_cv(ds, metadata, mode="auto", n_splits=3, min_train_anchors=3,
                min_train_rows=1000, low_anchor_threshold=12):
    """시계열 모수가 작으면 시간 순서 K-fold. 기존 채택 모델을 덮어쓰지 않는 보조 평가다."""
    if mode not in ("auto", "on", "off"):
        raise ValueError("cv는 auto/on/off 중 하나여야 합니다")
    if n_splits < 2 or min_train_anchors < 1 or min_train_rows < 1:
        raise ValueError("folds는 2 이상, 최소 학습 기준월·행은 1 이상이어야 합니다")
    labeled = ds[ds.y_growth.notna()]
    try:
        primary_train, _ = M.split(labeled)
    except ValueError:
        return {"method": "purged_expanding_time_kfold", "mode": mode,
                "status": "not_requested" if mode == "off" else "insufficient_history",
                "folds": [], "reason": "12개월 분리 후 학습 이력이 없음"}
    train_anchors = int(primary_train.anchor.nunique())
    needed = train_anchors < low_anchor_threshold or len(primary_train) < min_train_rows
    result = {"method": "purged_expanding_time_kfold", "mode": mode,
              "primary_train_anchors": train_anchors, "low_anchor_threshold": low_anchor_threshold,
              "min_train_rows": min_train_rows, "gap_months": F.HORIZON, "folds": [],
              "reason": f"학습 기준월 {train_anchors}개 (자동 실행 기준 {low_anchor_threshold}개 미만)"}
    if mode == "off" or (mode == "auto" and not needed):
        return {**result, "status": "not_requested" if mode == "off" else "not_needed"}
    folds = temporal_folds(ds, n_splits, min_train_anchors, min_train_rows)
    if not folds:
        return {**result, "status": "insufficient_history", "reason": "12개월 분리 후 학습 가능한 검증 기준월이 2개 미만"}
    features = metadata["features_used"]
    for number, fold in enumerate(folds, 1):
        anchor = fold["validation_anchor"]
        train = labeled[labeled.anchor <= fold["train_cutoff"]]
        test = labeled[labeled.anchor == anchor]
        assert train.anchor.max() + pd.DateOffset(months=F.HORIZON) <= anchor
        log.info("시간 K-fold %s/%s: 학습 %s행, 검증 기준월 %s", number, len(folds), len(train), anchor.date())
        model = M.fit_a(train, features)
        score = model.predict(test[F.FEATURES])
        result["folds"].append({"fold": number, "train_start": str(train.anchor.min().date()),
            "train_end": str(train.anchor.max().date()), "validation_anchor": str(anchor.date()),
            "target_start": str((anchor + pd.DateOffset(months=1)).date()),
            "target_end": str((anchor + pd.DateOffset(months=F.HORIZON)).date()),
            "train_anchors": int(train.anchor.nunique()), "train_rows": len(train), "validation_rows": len(test),
            "features_used": model[0].keep_,
            "model_a": M.rank_metrics(test, score), "baseline_a": M.rank_metrics(test, test.growth_12m.to_numpy()),
            "model_a_product": M.rank_metrics(test, score, "product"),
            "baseline_a_product": M.rank_metrics(test, test.growth_12m.to_numpy(), "product")})
        del model, train, test
        gc.collect()
    aggregate = {}
    for key in ("model_a", "baseline_a", "model_a_product", "baseline_a_product"):
        aggregate[key] = {}
        for metric in ("precision_at_5", "ndcg_at_5"):
            values = [f[key][metric] for f in result["folds"] if f[key][metric] is not None]
            aggregate[key][metric] = {"mean": float(np.mean(values)) if values else None,
                "std": float(np.std(values, ddof=1)) if len(values) > 1 else None, "valid_folds": len(values)}
    windows = [(pd.Timestamp(f["target_start"]), pd.Timestamp(f["target_end"])) for f in result["folds"]]
    overlap = any(left[1] >= right[0] for left, right in zip(windows, windows[1:]))
    return {**result, "status": "completed", "aggregate": aggregate,
            "overlapping_validation_targets": overlap,
            "interpretation": "각 fold의 학습/검증 정답은 분리됨. fold 사이 미래 정답 창은 겹칠 수 있어 평균·표준편차는 기술 통계이며 신뢰구간이 아님. 기존 설정의 후향적 보조 검증이며 독립 최종 Holdout을 대체하지 않음."}
