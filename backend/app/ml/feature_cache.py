"""월별 피처 캐시: 원본은 다시 확인하되 영향이 없는 기준월 계산은 재사용한다."""
from pathlib import Path
from uuid import uuid4

import pandas as pd
from filelock import FileLock

from backend.app.ml import features as F
from backend.app.ml.model_registry import read_json, sha256, write_json
from backend.app.ml.runtime import ensure_artifact_runtime, runtime_versions

ROOT = Path(__file__).resolve().parents[3]
MONTHLY = ("exports", "imports", "fx", "news", "news_topics")


def signature(data):
    from backend.app.ml.artifacts import _frame_digest
    monthly, static = {}, {}
    for name, value in data.items():
        if isinstance(value, pd.DataFrame):
            if name in MONTHLY and "month" in value:
                monthly[name] = {str(m.date()): _frame_digest(g) for m, g in value.groupby(
                    pd.to_datetime(value.month).dt.to_period("M").dt.to_timestamp())}
            else:
                static[name] = _frame_digest(value)
        elif name == "complete_months":
            monthly[name] = {str(pd.Timestamp(m).date()): True for m in value}
        else:
            static[name] = str(value)
    static["code"] = {p: sha256(ROOT / "backend/app/ml" / p) for p in
                      ("features.py", "preprocess.py", "temporal.py", "related_groups.py", "feature_cache.py")}
    static["parameters"] = [F.HISTORY, F.HORIZON, F.MIN_EXPORT_USD, F.FEATURES]
    static["start"] = str(pd.to_datetime(data["exports"].month).min().date())
    return {"monthly": monthly, "static": static}


def refresh_plan(current, previous, anchors):
    """정답은 미래 12개월을 보므로 변경 월의 12개월 전부터 다시 계산한다."""
    if not previous or current["static"] != previous["static"]:
        return {"mode": "FULL", "reason": "최초 캐시 또는 기준정보·코드 변경", "anchors": anchors}
    changed = []
    for name in set(current["monthly"]) | set(previous["monthly"]):
        now, old = current["monthly"].get(name, {}), previous["monthly"].get(name, {})
        changed.extend(m for m in set(now) | set(old) if now.get(m) != old.get(m))
    if not changed:
        return {"mode": "REUSED", "reason": "입력 값 변경 없음", "anchors": []}
    start = pd.Timestamp(min(changed)) - pd.DateOffset(months=F.HORIZON)
    return {"mode": "PARTIAL", "reason": "변경 월의 미래 정답·이후 피처 갱신",
            "anchors": [a for a in anchors if a >= start]}


def build_cached(data, directory):
    """완성된 캐시만 포인터로 공개한다. 캐시는 학습 성공 여부와 독립적이다."""
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    with FileLock(str(directory / ".cache.lock")):
        current = signature(data)
        first = pd.to_datetime(data["exports"].month).min() + pd.DateOffset(months=F.HISTORY - 1)
        last = pd.to_datetime(data["exports"].month).max()
        anchors = list(pd.date_range(first, last, freq="MS"))
        meta = read_json(directory / "metadata.json", {})
        previous = None
        if meta:
            ensure_artifact_runtime(directory)
            path = (directory / meta["cache_file"]).resolve()
            if not path.is_relative_to(directory) or sha256(path) != meta["sha256"]:
                raise ValueError("피처 캐시 경로·해시 불일치")
            previous = pd.read_pickle(path)
        plan = refresh_plan(current, meta.get("signature"), anchors)
        if plan["mode"] == "FULL":
            ds = F.build_dataset(data)
            reused = 0
        elif plan["mode"] == "REUSED":
            ds, reused = previous, len(previous)
        else:
            retained = previous[previous.anchor.isin(set(anchors) - set(plan["anchors"]))]
            fresh = F.build_dataset(data, anchors=plan["anchors"]) if plan["anchors"] else previous.iloc[:0]
            ds, reused = pd.concat([retained, fresh], ignore_index=True), len(retained)
        details = {"mode": plan["mode"], "reason": plan["reason"], "reused_rows": reused,
                   "recomputed_anchors": [str(a.date()) for a in plan["anchors"]]}
        if plan["mode"] != "REUSED":
            filename = "features_" + uuid4().hex + ".pkl"
            ds.to_pickle(directory / filename)
            write_json(directory / "metadata.json", {"cache_file": filename,
                "sha256": sha256(directory / filename), "runtime": runtime_versions(), "signature": current})
        return ds, details
