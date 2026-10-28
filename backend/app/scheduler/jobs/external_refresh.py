"""ML 피처용 외부 데이터(BACI·WDI·Comtrade) 월간 갱신. 결과는 DB가 아니라 data/external/ CSV입니다.

실행 (저장소 루트에서): python -m backend.app.scheduler.jobs.external_refresh [--date YYYY-MM-DD]
스케줄: backend/app/scheduler/main.py가 매월 1일 04:00(KST)에 실행합니다.

1. BACI: 올해 판(BACI_HS22_V{올해}01.zip, 수정판 V{올해}01b·c 포함)이 없으면 CEPII에서 받아 2022년 이후를 집계합니다.
   아직 공개 전(404)이면 NOT_READY로 끝내고 다음 달에 다시 시도합니다. 이미 받아 집계했으면 ALREADY이고
   batch_run에 남기지 않습니다.
2. WDI: 2022년~작년 지표를 다시 받습니다 (World Bank가 과거 값도 수정하므로 매번 덮어씀).
3. Comtrade: BACI 마지막 연도(연결 연도) ~ 작년을 받습니다. .env에 UN_COMTRADE_API_KEY가 없으면 건너뜁니다.
   연결 연도는 BACI 다음 해의 성장률을 같은 Comtrade끼리 비교하려고 받습니다 (BACI는 FOB, Comtrade 수입은 CIF).
   BACI가 따라잡아 연결 연도보다 앞선 해의 Comtrade 파일은 지웁니다.
4. availability: ML이 외부 파일 판을 확인하는 availability_snapshot.json의 해시를 새 파일로 다시 기록하고,
   처음 값이 생긴 (출처, 연도)는 availability.csv에 수집일로 추가합니다 (record_availability).

단계마다 batch_run에 한 건씩 기록합니다 (대상 external_csv, job EXTERNAL_BACI·EXTERNAL_WDI·EXTERNAL_COMTRADE).
진행 로그는 data/external/logs/external_refresh_<시각>.log, 다운로드·수집 실패는 download_failures.log에 따로 쌓습니다.
"""
import argparse
import json
import logging
import shutil
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
from dotenv import dotenv_values

from backend.app.db.batch_log import Source, track_batch
from backend.app.ml import preprocess as pp
from backend.app.ml.model_registry import sha256, write_json
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
    # NOT_READY는 batch_run에 수신 0건 SUCCESS로 남는다. 받은 게 있는 실행과는 received_count로 구분한다.
    (log.warning if status == "NOT_READY" else log.info)(f"{name} {status}: {detail}")
    return status


def baci_zip(today):
    """이미 받아 둔 올해 판 zip. 접미사가 붙은 수정판(V202401b 등)이 있으면 그것을 고른다."""
    zips = sorted((ec.OUT_DIR / "baci").glob(f"BACI_HS22_V{today.year}01*.zip"))
    return zips[-1] if zips else None


def baci_done(today):
    """올해 판을 이미 받아 집계했는지. 집계본이 zip보다 나중에 만들어졌으면 끝난 것으로 본다."""
    zip_path = baci_zip(today)
    csv_path = ec.OUT_DIR / "baci" / "baci_imports_hs22.csv"
    return bool(zip_path and csv_path.exists() and csv_path.stat().st_mtime >= zip_path.stat().st_mtime)


def refresh_baci(today):
    zip_path = baci_zip(today)
    requests = 0
    if zip_path is None:
        # CEPII는 같은 해에 수정판을 V{올해}01b처럼 내기도 한다. 최신 수정판부터 찾는다.
        # ponytail: 기본판을 받은 뒤 나온 수정판은 다시 받지 않음. 필요하면 zip을 지우고 실행
        for version in (f"V{today.year}01{s}" for s in ("c", "b", "")):
            requests += 1
            path = ec.OUT_DIR / "baci" / f"BACI_HS22_{version}.zip"
            if ec.download_baci(version, path):
                zip_path = path
                break
        else:
            return {"status": "NOT_READY", "requests": requests}
    stats = ec.collect_baci(zip_path, START_YEAR)
    keep_only(zip_path, "BACI_HS22_*.zip")  # 지난 판 zip(수 GB)은 집계를 마친 뒤 지운다
    return {"requests": requests, **stats}


def refresh_wdi(last):
    stats = ec.collect_wdi(START_YEAR, last)
    keep_only(ec.OUT_DIR / "wdi" / f"wdi_{START_YEAR}_{last}.csv", "wdi_*.csv")
    return stats


def keep_only(keep, pattern):
    """keep과 같은 폴더에서 pattern에 맞는 다른 파일을 지운다."""
    for path in keep.parent.glob(pattern):
        if path != keep:
            path.unlink()
            log.info(f"이전 파일 삭제: {path.name}")


def baci_last_year():
    path = ec.OUT_DIR / "baci" / "baci_imports_hs22.csv"
    return int(pd.read_csv(path, usecols=["year"]).year.max()) if path.exists() else START_YEAR - 1


def drop_covered_comtrade(baci_last):
    """BACI가 따라잡은 연도의 Comtrade CSV·원본 응답을 지운다. BACI 마지막 해는 연결 연도라 남긴다."""
    folder = ec.OUT_DIR / "comtrade"
    for path in [*folder.glob("comtrade_imports_*.csv"), *(folder / "raw").glob("[0-9]*")]:
        if int(path.stem[-4:]) < baci_last:
            shutil.rmtree(path) if path.is_dir() else path.unlink()
            log.info(f"COMTRADE 정리: BACI에 {baci_last}년까지 있어 {path.name} 삭제")


AVAILABILITY_DATASET = {"BACI": "market", "COMTRADE": "market", "WDI": "wdi"}


def years_with_values(path, source):
    """파일에 값이 있는 연도. WDI는 지표가 하나라도 있는 연도만 (발표 전 연도는 빈 행으로 남는다)."""
    frame = pp.read_csv(path)
    if source == "WDI":
        frame = frame[frame.drop(columns=["year", "country_code"], errors="ignore").notna().any(axis=1)]
    return sorted(int(y) for y in frame.year.dropna().unique())


def record_availability(today):
    """availability.csv·availability_snapshot.json을 이번에 수집한 외부 파일에 맞춘다.

    맞추지 않으면 preprocess.check_versions가 해시 불일치로 멈춰 10일 수출입 월별 작업의 ML 단계가 실패한다.
    최초 공개일 정책(first_release_dates)에서만 자동으로 맞춘다. 이 정책의 날짜는 (출처, 연도)마다 정해지고 파일 판과
    무관하므로 기존 날짜는 그대로 두고, 처음 값이 생긴 연도만 수집일(오늘)로 추가한다. 빈 연도에 미리 날짜를 붙이면
    나중에 들어온 값이 그 이른 날짜로 쓰이므로 값이 있는 연도만 추가한다.
    다른 정책은 파일 판마다 날짜를 다시 정해야 해서 손대지 않고 경고만 남긴다.
    """
    csv_path, snapshot_path = ec.OUT_DIR / "availability.csv", ec.OUT_DIR / "availability_snapshot.json"
    if not csv_path.exists() or not snapshot_path.exists():
        return
    snapshot = json.loads(snapshot_path.read_text("utf-8"))
    if snapshot.get("policy") != "first_release_dates":
        log.warning(f"AVAILABILITY 건너뜀: 정책 {snapshot.get('policy')}는 파일 판마다 날짜를 정해야 해 직접 갱신 필요")
        return
    dates = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    known = set(zip(dates.source, dates.year))
    added, files = [], []
    for source, paths in pp.external_files(ec.OUT_DIR).items():
        for path in paths:
            years = years_with_values(path, source)
            for year in years:
                if (source, str(year)) not in known:
                    known.add((source, str(year)))
                    added.append({"dataset": AVAILABILITY_DATASET[source], "source": source, "year": str(year),
                                  "available_from": today.isoformat(),
                                  "evidence": f"external_refresh가 {today}에 처음 수집 (최초 공개일 근거로 바꿀 수 있음)"})
            files.append({"path": "data/external/" + path.relative_to(ec.OUT_DIR).as_posix(), "sha256": sha256(path),
                          "bytes": path.stat().st_size, "years": years})
    if added:
        ec.save_csv(pd.concat([dates, pd.DataFrame(added)], ignore_index=True), csv_path)
        log.warning(f"AVAILABILITY 새 연도를 수집일 {today}로 추가: {[(r['source'], r['year']) for r in added]}")
    write_json(snapshot_path, {**snapshot, "files": files, "refreshed_at": datetime.now(KST).isoformat()})
    log.info(f"AVAILABILITY 파일 해시 {len(files)}개 다시 기록")


def run(today=None, engine=None):
    """engine: batch_run 기록용 DB. 테스트에서 SQLite를 넘긴다."""
    today = today or datetime.now(KST).date()
    log_path = setup_log()
    last = today.year - 1
    log.info(f"외부 데이터 갱신 시작 | 기준일 {today} | 로그 {log_path}")
    if baci_done(today):  # 할 일이 없는 달은 batch_run에 남기지 않는다
        statuses = {"BACI": "ALREADY"}
        log.info(f"BACI ALREADY: {baci_zip(today).name}")
    else:
        statuses = {"BACI": step("BACI", f"BACI_HS22_V{today.year}01", date(START_YEAR, 1, 1),
                                 date(last, 12, 31), lambda: refresh_baci(today), engine)}
    statuses["WDI"] = step("WDI", f"{START_YEAR}~{last}", date(START_YEAR, 1, 1), date(last, 12, 31),
                           lambda: refresh_wdi(last), engine)
    baci_last = baci_last_year()
    drop_covered_comtrade(baci_last)
    if not ec.comtrade_key(dotenv_values(ec.ROOT / ".env")):
        log.warning(f"COMTRADE 건너뜀: .env에 {ec.ENV_KEY}가 없음")
    else:
        # BACI 마지막 해(연결 연도)부터 작년까지. BACI가 너무 오래됐거나 없으면 최근 2년만 받는다:
        # 2022년부터 전부 받으면 무료 키 한도(하루 500회)를 넘는다.
        first = baci_last if baci_last >= last - 2 else last - 1
        for year in range(first, last + 1):
            statuses[f"COMTRADE_{year}"] = step("COMTRADE", str(year), date(year, 1, 1), date(year, 12, 31),
                                                lambda year=year: ec.collect_comtrade(year), engine)
    try:  # 수집이 일부 실패해도 남은 파일은 각각 완성본이라 그 해시로 맞춘다
        record_availability(today)
    except Exception as e:
        log_failure("AVAILABILITY", str(today), e)
        log.exception(f"AVAILABILITY FAILED: ML 단계가 외부 파일 판 확인에서 멈출 수 있음 | 실패 로그: {FAILURE_LOG}")
    log.info(f"외부 데이터 갱신 끝 | {statuses}")
    return statuses


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", type=date.fromisoformat, help="기준일 (기본: 오늘)")
    run(parser.parse_args().date)


if __name__ == "__main__":
    main()
