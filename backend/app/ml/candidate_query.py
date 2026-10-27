"""저장한 후보 모델(.joblib)로 최신 기준월을 예측해 국가 → 유망 품목, 품목 → 유망 국가 Top N을 보여준다.

운영 조회(market_model query)는 운영 HGB가 미리 계산한 predictions.csv를 읽는다.
이 명령은 후보 모델 파일로 피처 스냅샷의 최신 기준월을 그 자리에서 예측한다.
처음 한 번 최신 기준월 행을 작은 캐시 파일로 만들어 두고(1분 안팎), 이후 조회는 그 파일만 읽는다.

실행 (저장소 루트에서):
    python -m backend.app.ml.candidate_query --country 베트남
    python -m backend.app.ml.candidate_query --country VN --top 10
    python -m backend.app.ml.candidate_query --hs6 850760
    python -m backend.app.ml.candidate_query --country 베트남 --hs6 850760   (한 쌍의 예측과 순위)
    python -m backend.app.ml.candidate_query --model data/ml/candidates/lightgbm_v2.joblib --country 미국
"""
import argparse
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from backend.app.ml import sources as S
from backend.app.ml.experiments import ROOT, load_snapshot
from backend.app.ml.feature_recovery import MARKET, WDI, recover_external

DEFAULT_MODEL = ROOT / "data/ml/candidates/lightgbm_v1.joblib"
DEFAULT_SNAPSHOT = ROOT / "data/ml/features_snapshot_py311.pkl"
EXTERNAL_DIR = ROOT / "data/external"


def country_code(text, countries):
    """국가 코드(VN) 또는 한글 국가명(베트남, 일부만 써도 하나로 정해지면 됨)을 코드로 바꾼다."""
    text = text.strip()
    if text.upper() in set(countries.country_code):
        return text.upper()
    for found in (countries[countries.name_ko == text], countries[countries.name_ko.str.contains(text, regex=False)]):
        if len(found) == 1:
            return found.country_code.iloc[0]
        if len(found) > 1:
            raise ValueError(f"'{text}'에 맞는 국가가 여러 개입니다: {', '.join(found.name_ko)}")
    raise ValueError(f"'{text}' 국가를 찾지 못했습니다 (국가 코드 또는 한글 국가명)")


def item_name(hs6, names, width=32):
    """HS6 한글명. 이름이 없거나 '기타'면 HS4 이름을 앞에 붙여 무엇의 기타인지 보이게 한다."""
    name = names.get(hs6, "")
    if name in ("", "기타") and names.get(hs6[:4]):
        name = f"{names[hs6[:4]]} - {name or '(6단위 이름 없음)'}"
    return name if len(name) <= width else name[:width - 1] + "…"


def latest_rows(snapshot_path):
    """최신 기준월 행. 외부 피처가 비어 있으면 현재 공개일표(availability.csv)로 붙인다.

    스냅샷 전체 읽기(약 6초)와 외부 자료 연결(약 50초)을 매번 하지 않도록 결과를 <스냅샷>_latest.pkl(약 30MB)에 두고,
    스냅샷이나 data/external의 CSV(공개일표 포함)가 캐시보다 새로우면 다시 만든다.
    """
    snapshot_path = Path(snapshot_path)
    cache = snapshot_path.with_name(snapshot_path.stem + "_latest.pkl")
    sources = [snapshot_path, *EXTERNAL_DIR.rglob("*.csv")]
    if cache.exists() and cache.stat().st_mtime > max(p.stat().st_mtime for p in sources):
        return pd.read_pickle(cache)
    print(f"최신 기준월 캐시 생성 중 (처음 한 번, 1분 안팎): {cache.name}", flush=True)
    snapshot = load_snapshot(snapshot_path)
    latest = snapshot[snapshot.anchor == snapshot.anchor.max()].copy()
    del snapshot
    if latest[MARKET + WDI].isna().all().all():
        latest, _ = recover_external(latest)
    latest.attrs = {}
    temporary = cache.with_suffix(".pkl.tmp")
    latest.to_pickle(temporary)
    temporary.replace(cache)
    return latest


def predict_latest(bundle, snapshot_path):
    """최신 기준월 전체 쌍의 점수."""
    latest = latest_rows(snapshot_path)
    x = latest[bundle["features"]].replace([np.inf, -np.inf], np.nan)
    return latest.assign(score=bundle["pipeline"].predict(x))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--country", help="국가 코드(VN) 또는 한글 국가명(베트남) → 유망 품목")
    parser.add_argument("--hs6", help="HS 6단위 품목 코드(850760) → 유망 국가. --country와 함께 주면 그 쌍의 예측")
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    args = parser.parse_args()
    if not args.country and not args.hs6:
        parser.error("--country 또는 --hs6를 지정하세요 (둘 다 주면 그 쌍의 예측)")
    countries = S.countries()
    countries = countries[countries.country_role == "COUNTRY"]
    try:
        code = country_code(args.country, countries) if args.country else None
    except ValueError as exc:
        parser.error(str(exc))
    hs6 = args.hs6.strip()[:6] if args.hs6 else None
    if hs6 is not None and not (len(hs6) == 6 and hs6.isdigit()):
        parser.error("--hs6는 6자리 숫자입니다 (예: 850760)")

    bundle = joblib.load(args.model)
    pred = predict_latest(bundle, args.snapshot)
    anchor = pred.anchor.max().date()
    names = S.hs_names()
    pred = pred.assign(growth_pct=np.expm1(pred.score) * 100, export_usd=pred.exp_12m.map("{:,.0f}".format))
    if code and hs6:
        name = countries.set_index("country_code").name_ko[code]
        print(f"모델 {args.model.name} | 기준월 {anchor} | {name}({code}) × {hs6} {item_name(hs6, names, width=60)}")
        pair = pred[(pred.country_code == code) & (pred.hs6 == hs6)]
        if pair.empty:
            print("예측 대상이 아닙니다 (최근 12개월 수출 $100K 이상인 쌍만 예측합니다)")
            return
        row = pair.iloc[0]
        in_country, in_product = pred[pred.country_code == code], pred[pred.hs6 == hs6]
        print(f"예측 점수 {row.score:.3f} | 예측 증감률(약) {row.growth_pct:+.1f}% | 최근 12개월 수출 {row.export_usd} USD")
        print(f"{name} 안 순위 {int((in_country.score > row.score).sum()) + 1} / {len(in_country)}개 품목 | "
              f"{hs6} 안 순위 {int((in_product.score > row.score).sum()) + 1} / {len(in_product)}개국")
        return
    if code:
        rows = pred[pred.country_code == code].nlargest(args.top, "score")
        label = f"{countries.set_index('country_code').name_ko[code]}({code})"
        rows = rows.assign(name=rows.hs6.map(lambda h: item_name(h, names)))[["hs6", "name", "score", "growth_pct", "export_usd"]]
        header = ["HS6", "품목명", "예측 점수", "예측 증감률(%)", "최근 12개월 수출(USD)"]
    else:
        rows = pred[pred.hs6 == hs6].nlargest(args.top, "score")
        label = f"{hs6} {item_name(hs6, names, width=60)}".strip()
        rows = rows.merge(countries[["country_code", "name_ko"]], on="country_code", how="left")
        rows = rows[["country_code", "name_ko", "score", "growth_pct", "export_usd"]]
        header = ["국가코드", "국가명", "예측 점수", "예측 증감률(%)", "최근 12개월 수출(USD)"]
    print(f"모델 {args.model.name} | 기준월 {anchor} | {label} → 다음 12개월 유망 {'품목' if code else '국가'} Top {args.top}")
    if rows.empty:
        print("예측 대상이 없습니다 (최근 12개월 수출 $100K 이상인 쌍만 예측합니다)")
        return
    rows.columns = header
    rows.insert(0, "순위", range(1, len(rows) + 1))
    with pd.option_context("display.unicode.east_asian_width", True, "display.width", 200):
        print(rows.round({"예측 점수": 3, "예측 증감률(%)": 1}).to_string(index=False))


if __name__ == "__main__":
    main()
