"""ML 피처용 외부 데이터(BACI·WDI·Comtrade) 월간 갱신. 결과는 DB가 아니라 data/external/ CSV입니다.

실행 (저장소 루트에서): python -m backend.app.scheduler.jobs.external_refresh [--date YYYY-MM-DD]
스케줄: backend/app/scheduler/main.py가 매월 1일 04:00(KST)에 실행합니다.

1. BACI: 올해 판(BACI_HS22_V{올해}01.zip)이 없으면 CEPII에서 받아 2022년 이후를 집계합니다.
   아직 공개 전(404)이면 NOT_READY로 끝내고 다음 달에 다시 시도합니다. 이미 받아 집계했으면 ALREADY입니다.
2. WDI: 2022년~작년 지표를 다시 받습니다 (World Bank가 과거 값도 수정하므로 매번 덮어씀).
3. Comtrade: BACI에 아직 없는 연도(BACI 마지막 연도+1 ~ 작년)를 다시 받습니다. .env에 COMTRADE_API_KEY가 없으면 건너뜁니다.

단계마다 batch_run에 한 건씩 기록합니다 (대상 external_csv, job EXTERNAL_BACI·EXTERNAL_WDI·EXTERNAL_COMTRADE).
진행 로그는 data/external/logs/external_refresh_<시각>.log, 다운로드·수집 실패는 download_failures.log에 따로 쌓습니다.
"""
import argparse
import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
from dotenv import dotenv_values

from backend.app.db.batch_log import Source, track_batch
from backend.app.scheduler.collectors.external import external_collect as ec

KST = ZoneInfo("Asia/Seoul")
START_YEAR = 2022  # HS22판 BACI가 시작하는 해
TARGET = "external_csv"
LOG_DIR = ec.OUT_DIR / "logs"
FAILURE_LOG = LOG_DIR / "download_failures.log"
SOURCES = {
    "BACI": Source("CEPII_BACI", "CEPII BACI 국가별 HS6 교역", {"url": ec.BACI_URL}),
    "WDI": Source("WB_WDI", "World Bank WDI 국가 지표", {"url": ec.WDI_URL}),
    "COMTRADE": Source("UN_COMTRADE", "UN Comtrade 국가별 HS6 수입", {"url": ec.COMTRADE_URL}),
}
log = logging.getLogger("external_refresh")


def setup_log():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"external_refresh_{datetime.now(KST):%Y%m%d_%H%M%S}.log"
    log.setLevel(logging.INFO)
    for h in log.handlers:
        h.close()
    log.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    for h in (logging.StreamHandler(), logging.FileHandler(path, encoding="utf-8")):
        h.setFormatter(fmt)
        log.addHandler(h)
    return path


def log_failure(name, detail, error):
    """실패만 모아 보는 별도 로그. 실행 로그와 달리 실행마다 새로 만들지 않고 이어 쓴다."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(FAILURE_LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now(KST):%Y-%m-%d %H:%M:%S} | {name} | {detail} | {type(error).__name__}: {error}\n")


def step(name, detail, start, end, work, engine=None):
    """work()가 돌려준 건수를 batch_run에 남긴다. 예외는 FAILED로 기록하고 다음 단계는 계속한다."""
    with track_batch(SOURCES[name], f"EXTERNAL_{name}", TARGET, start, end, engine=engine, logger=log) as run:
        try:
            stats = work()
        except Exception as e:
            run.fail()
            log_failure(name, detail, e)
            log.exception(f"{name} FAILED: {detail} | 실패 로그: {FAILURE_LOG}")
            return "FAILED"
        run.request_count += stats.get("requests", 0)
        run.received_count += stats.get("rows", 0)
    status = stats.get("status", "DONE")
    log.info(f"{name} {status}: {detail}")
    return status


def refresh_baci(today):
    version = f"V{today.year}01"
    zip_path = ec.OUT_DIR / "baci" / f"BACI_HS22_{version}.zip"
    csv_path = ec.OUT_DIR / "baci" / "baci_imports_hs22.csv"
    if zip_path.exists() and csv_path.exists() and csv_path.stat().st_mtime >= zip_path.stat().st_mtime:
        return {"status": "ALREADY"}
    if not zip_path.exists() and not ec.download_baci(version, zip_path):
        return {"status": "NOT_READY", "requests": 1}
    return {"requests": 1, **ec.collect_baci(zip_path, START_YEAR)}


def baci_last_year():
    path = ec.OUT_DIR / "baci" / "baci_imports_hs22.csv"
    return int(pd.read_csv(path, usecols=["year"]).year.max()) if path.exists() else START_YEAR - 1


def run(today=None, engine=None):
    """engine: batch_run 기록용 DB. 테스트에서 SQLite를 넘긴다."""
    today = today or datetime.now(KST).date()
    log_path = setup_log()
    last = today.year - 1
    log.info(f"외부 데이터 갱신 시작 | 기준일 {today} | 로그 {log_path}")
    statuses = {"BACI": step("BACI", f"BACI_HS22_V{today.year}01", date(START_YEAR, 1, 1), date(last, 12, 31),
                             lambda: refresh_baci(today), engine)}
    statuses["WDI"] = step("WDI", f"{START_YEAR}~{last}", date(START_YEAR, 1, 1), date(last, 12, 31),
                           lambda: ec.collect_wdi(START_YEAR, last), engine)
    if not dotenv_values(ec.ROOT / ".env").get(ec.ENV_KEY):
        log.warning(f"COMTRADE 건너뜀: .env에 {ec.ENV_KEY}가 없음")
    else:
        for year in range(baci_last_year() + 1, last + 1):
            statuses[f"COMTRADE_{year}"] = step("COMTRADE", str(year), date(year, 1, 1), date(year, 12, 31),
                                                lambda year=year: ec.collect_comtrade(year), engine)
    log.info(f"외부 데이터 갱신 끝 | {statuses}")
    return statuses


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", type=date.fromisoformat, help="기준일 (기본: 오늘)")
    run(parser.parse_args().date)


if __name__ == "__main__":
    main()
