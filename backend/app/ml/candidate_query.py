"""저장한 후보 모델(.joblib)로 최신 기준월을 예측해 국가 → 유망 품목, 품목 → 유망 국가 Top N을 보여준다.

운영 조회(market_model query)는 운영 HGB가 미리 계산한 predictions.csv를 읽는다.
이 명령은 후보 모델 파일로 피처 스냅샷의 최신 기준월을 그 자리에서 예측한다.
처음 한 번 최신 기준월 행을 작은 캐시 파일로 만들어 두고(수 초), 이후 조회는 그 파일만 읽는다.
--hs6는 HS2022 코드를 받아, 판이 바뀌며 묶인 코드는 스냅샷의 비교용 대표 코드로 바꿔 찾는다.

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
from backend.app.ml.experiments import ROOT, SNAPSHOT_PY311, load_snapshot
from backend.app.ml.features import hs_key_map

DEFAULT_MODEL = ROOT / "data/ml/candidates/lightgbm_v1.joblib"
LATEST_CACHE_VERSION = 2  # v1(_latest.pkl)은 외부 값을 복구했으므로 원본 그대로인 v2와 재사용하지 않는다


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
    """최신 기준월 행. 후보 모델이 학습한 스냅샷 값 그대로 쓴다 (예측 행에만 외부 피처를 붙이면 학습과 입력이 어긋난다).

    스냅샷 전체 읽기(약 6초)를 매번 하지 않도록 결과를 <스냅샷>_latest_v2.pkl(약 30MB)에 두고, 스냅샷이 캐시보다 새로우면 다시 만든다.
    """
    snapshot_path = Path(snapshot_path)
    cache = snapshot_path.with_name(f"{snapshot_path.stem}_latest_v{LATEST_CACHE_VERSION}.pkl")
    if cache.exists() and cache.stat().st_mtime > snapshot_path.stat().st_mtime:
        return pd.read_pickle(cache)
    print(f"최신 기준월 캐시 생성 중 (처음 한 번, 수 초): {cache.name}", flush=True)
    snapshot = load_snapshot(snapshot_path)
    latest = snapshot[snapshot.anchor == snapshot.anchor.max()].copy()
    del snapshot
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


def comparison_hs6(hs6, mapping=None):
    """HS2022 코드 → 스냅샷의 비교용 HS6. 판이 바뀌며 나뉘거나 합쳐진 코드는 묶음 대표 코드(group_code)로 저장돼 있다."""
    mapping = mapping if mapping is not None else hs_key_map(S.hs_correlation())
    return mapping.get(("HS2022", hs6), hs6)


def pair_rank(pred, code, hs6, country_by="score", product_by="score"):
    """한 쌍의 예측 행과 국가 안·품목 안 순위 (점수가 더 높은 쌍 수 + 1). 예측 대상이 아니면 None.

    country_by·product_by: 국가 안·품목 안 순위에 쓰는 점수 열 (2단계 모델은 order·growth).
    """
    pair = pred[(pred.country_code == code) & (pred.hs6 == hs6)]
    if pair.empty:
        return None
    row = pair.iloc[0]
    in_country, in_product = pred[pred.country_code == code], pred[pred.hs6 == hs6]
    return {"row": row, "country_rank": int((in_country[country_by] > row[country_by]).sum()) + 1,
            "country_size": len(in_country),
            "product_rank": int((in_product[product_by] > row[product_by]).sum()) + 1, "product_size": len(in_product)}


def resolve_target(country, hs6, countries, error):
    """--country·--hs6 입력을 (국가 코드, 비교용 HS6)로. 잘못된 입력은 error(안내 문구)로 멈춘다 (보통 parser.error)."""
    try:
        code = country_code(country, countries) if country else None
    except ValueError as exc:
        error(str(exc))
    if hs6 is not None:
        hs6 = hs6.strip()
        if not (len(hs6) == 6 and hs6.isdigit()):  # 10자리 코드를 조용히 잘라 다른 품목을 조회하지 않게 한다
            error("--hs6는 HS 6단위 6자리 숫자입니다 (예: 850760)")
        key = comparison_hs6(hs6)
        if key != hs6:
            print(f"{hs6}는 판 변경으로 묶인 코드라 비교용 대표 코드 {key}로 조회합니다", flush=True)
        hs6 = key
    return code, hs6


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--country", help="국가 코드(VN) 또는 한글 국가명(베트남) → 유망 품목")
    parser.add_argument("--hs6", help="HS 6단위 품목 코드(850760) → 유망 국가. --country와 함께 주면 그 쌍의 예측")
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--snapshot", type=Path, default=SNAPSHOT_PY311)
    args = parser.parse_args()
    if not args.country and not args.hs6:
        parser.error("--country 또는 --hs6를 지정하세요 (둘 다 주면 그 쌍의 예측)")
    if args.top < 1:
        parser.error("--top은 1 이상입니다")
    countries = S.countries()
    countries = countries[countries.country_role == "COUNTRY"]
    code, hs6 = resolve_target(args.country, args.hs6, countries, parser.error)

    bundle = joblib.load(args.model)
    pred = predict_latest(bundle, args.snapshot)
    anchor = pred.anchor.max().date()
    names = S.hs_names()
    pred = pred.assign(growth_pct=np.expm1(pred.score) * 100, export_usd=pred.exp_12m.map("{:,.0f}".format))
    if code and hs6:
        name = countries.set_index("country_code").name_ko[code]
        print(f"모델 {args.model.name} | 기준월 {anchor} | {name}({code}) × {hs6} {item_name(hs6, names, width=60)}")
        rank = pair_rank(pred, code, hs6)
        if rank is None:
            print("예측 대상이 아닙니다 (최근 12개월 수출 $100K 이상인 쌍만 예측합니다)")
            return
        row = rank["row"]
        print(f"예측 점수 {row.score:.3f} | 예측 증감률(약) {row.growth_pct:+.1f}% | 최근 12개월 수출 {row.export_usd} USD")
        print(f"{name} 안 순위 {rank['country_rank']} / {rank['country_size']}개 품목 | "
              f"{hs6} 안 순위 {rank['product_rank']} / {rank['product_size']}개국")
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
