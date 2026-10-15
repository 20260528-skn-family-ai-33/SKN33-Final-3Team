"""UN M49 기준 대륙·하위 지역을 market_region에 넣고, country.region_id를 하위 지역으로 채웁니다.

실행 (저장소 루트에서): python -m backend.app.scheduler.jobs.country_region_load [--db-url URL]
- 원천 (services/country_region.py가 읽음): collectors/region/reference/market_region.csv (region_id = M49 코드), country_region.csv (국가코드 → 하위 지역 이름)
- market_region은 region_id 기준 upsert, country는 CSV에 있는 국가의 region_id만 바꿉니다 (CSV에 없는 국가는 그대로).
- 매핑이 없는 DB 국가(예: AQ 남극)는 경고로 알려 줍니다. 검증 오류가 있으면 아무것도 적재하지 않습니다.
- 한 트랜잭션으로 실행해 중간에 실패하면 전체 롤백됩니다.
"""
import argparse
import logging

from sqlalchemy import select, update

from backend.app.db.crud import CRUD
from backend.app.db.session import make_engine, session_scope
from backend.app.models.region import Country, MarketRegion
from backend.app.services.country_region import load_reference

log = logging.getLogger("country_region_load")


def load(engine, regions, mapping):
    """적재 후 (매핑된 DB 국가 수, 매핑 없는 DB 국가 코드 목록)을 돌려줍니다."""
    with session_scope(engine) as s:
        CRUD(MarketRegion).upsert_many(s, regions)
        db_codes = set(s.scalars(select(Country.country_code)))
        targets = sorted(db_codes & mapping.keys())
        for code in targets:
            s.execute(update(Country).where(Country.country_code == code).values(region_id=mapping[code]))
        return len(targets), sorted(db_codes - mapping.keys())


def run(db_url=None):
    regions, mapping, errors = load_reference()
    if errors:
        for e in errors:
            log.error(e)
        log.error(f"검증 오류 {len(errors)}건: 적재하지 않았습니다.")
        return 1
    engine = make_engine(db_url)
    try:
        updated, unmapped = load(engine, regions, mapping)
    finally:
        engine.dispose()
    log.info(f"market_region {len(regions)}행 upsert | country {updated}개국 region_id 갱신")
    if unmapped:
        log.warning(f"매핑 없는 국가 {len(unmapped)}개 (region_id 그대로): {', '.join(unmapped)}")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="UN M49 대륙·하위 지역을 market_region·country에 적재")
    parser.add_argument("--db-url", help="SQLAlchemy DB URL (기본: .env의 MYSQL_* 값)")
    raise SystemExit(run(parser.parse_args().db_url))
