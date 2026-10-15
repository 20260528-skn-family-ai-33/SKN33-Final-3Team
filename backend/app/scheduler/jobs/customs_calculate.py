"""실행: python -m backend.app.scheduler.jobs.customs_calculate --year 2025"""
import argparse
import json
import logging
from pathlib import Path
from time import perf_counter

from backend.app.db.session import ENV_PATH, make_engine, mysql_url
from backend.app.services.customs_metrics import MetricCalculator, configure_rules


def main():
    parser = argparse.ArgumentParser(description="적재된 수출입 원본의 이력·일회성 가능성 계산")
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--env", type=Path, default=ENV_PATH)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--r1-spike-ratio", type=float,
                        help="확정한 R1 급증 배수 N. r1-return-pct와 함께 지정")
    parser.add_argument("--r1-return-pct", type=float,
                        help="확정한 R1 복귀 허용 범위 ±M%%. r1-spike-ratio와 함께 지정")
    parser.add_argument("--r3-unit-ratio", type=float,
                        help="확정한 R3 단가 배수 K. 고가 K배 이상·저가 1/K 이하 판정")
    parser.add_argument("--r2-hs-prefixes", nargs="+",
                        help="팀에서 확정한 R2 HS 목록을 RULE에 등록 (예: 88 89). R1·R3는 유지")
    args = parser.parse_args()
    if not 2 <= args.year <= 9997:
        parser.error("year는 2~9997이어야 합니다")
    if (args.r1_spike_ratio is None) != (args.r1_return_pct is None):
        parser.error("R1은 --r1-spike-ratio와 --r1-return-pct를 함께 지정해야 합니다")
    started = perf_counter()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logger = logging.getLogger(__name__)
    engine = make_engine(mysql_url(args.env))
    try:
        configure_rules(engine, args.r1_spike_ratio, args.r1_return_pct,
                        args.r2_hs_prefixes, args.r3_unit_ratio)
        with engine.connect() as connection:
            result = MetricCalculator(connection, lambda event, **values:
                logger.info("%s %s", event, json.dumps(values, ensure_ascii=False)),
                logger=logger).calculate(args.year)
    finally:
        engine.dispose()
    result["elapsed_seconds"] = round(perf_counter() - started, 3)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
