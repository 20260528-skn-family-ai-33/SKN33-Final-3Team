"""국가코드 → UN M49 하위 지역(market_region.region_id) 매핑. DB 연결 없이 reference CSV만 읽습니다.

    from backend.app.services.country_region import region_id_of
    region_id_of("VN")  # 35 (동남아시아), 매핑이 없으면 None

국가를 새로 넣는 곳(수출입 적재 등)에서 region_id를 함께 채울 때 씁니다.
market_region 행은 country_region_load가 넣으므로, 그 전에 쓰면 FK 오류가 나지 않게 넣는 쪽에서 확인해야 합니다.
"""
import csv
from functools import lru_cache
from pathlib import Path

REF_DIR = Path(__file__).resolve().parents[1] / "scheduler" / "collectors" / "region" / "reference"


def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return [{k: (v or "").strip() for k, v in r.items()} for r in csv.DictReader(f)]


def build(region_rows, country_rows):
    """CSV 행 → (market_region 행 목록, {country_code: region_id}, 오류 목록)."""
    errors, regions = [], []
    for r in region_rows:
        regions.append({"region_id": int(r["region_id"]), "parent_id": int(r["parent_id"]) if r["parent_id"] else None,
                        "region_level": int(r["region_level"]), "name_ko": r["name_ko"]})
    by_id = {r["region_id"]: r for r in regions}
    by_name = {r["name_ko"]: r for r in regions}
    if len(by_id) != len(regions) or len(by_name) != len(regions):
        errors.append("market_region.csv에 region_id 또는 name_ko 중복")
    for r in regions:
        parent = by_id.get(r["parent_id"])
        if r["region_level"] == 1 and r["parent_id"] is not None:
            errors.append(f"{r['name_ko']}: 대륙(1)인데 parent_id가 있음")
        if r["region_level"] == 2 and (parent is None or parent["region_level"] != 1):
            errors.append(f"{r['name_ko']}: 하위 지역(2)의 parent_id {r['parent_id']}가 대륙이 아님")

    mapping = {}
    for r in country_rows:
        code, region = r["country_code"], by_name.get(r["region_name"])
        if code in mapping:
            errors.append(f"{code}: country_region.csv에 중복")
        if region is None or region["region_level"] != 2:
            errors.append(f"{code}: '{r['region_name']}'은 market_region.csv의 하위 지역이 아님")
            continue
        mapping[code] = region["region_id"]
    # 부모가 먼저 들어가야 self FK가 통과하므로 대륙 → 하위 지역 순서
    return sorted(regions, key=lambda r: r["region_level"]), mapping, errors


def load_reference(ref_dir=REF_DIR):
    return build(read_csv(ref_dir / "market_region.csv"), read_csv(ref_dir / "country_region.csv"))


@lru_cache(maxsize=1)
def _mapping():
    _, mapping, errors = load_reference()
    if errors:
        raise ValueError("country_region reference CSV 오류: " + "; ".join(errors))
    return mapping


def region_id_of(country_code):
    """국가코드의 하위 지역 region_id. 매핑이 없으면(예: AQ 남극, 특수 코드) None."""
    return _mapping().get(country_code)
