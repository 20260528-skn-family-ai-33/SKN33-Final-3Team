"""HS 판 갱신 자동화: 관세청 xlsx 다운로드 → HS 코드 CSV 생성(LLM 요약) → MySQL hs_code 적재.

실행 (저장소 루트에서): python -m backend.app.scheduler.jobs.hs_code_refresh [--date YYYYMMDD]
스케줄: backend/app/scheduler/main.py가 2028년부터 5년마다 1월 1일 00:15(KST)에 실행합니다.

1. 공공데이터포털 '관세청_HS부호 단위별 품목명'(15130660)의 최신 xlsx를 받아 reference/hs_names_YYYYMMDD.xlsx로 저장
2. 파일 판 날짜의 HS 판이 실행일 기준 HS 판과 다르면(새 판이 아직 안 올라옴) NOT_READY로 끝냄 → 스케줄러가 다음 날 재시도
3. DB에 그 판이 이미 있으면 ALREADY_LOADED로 끝냄 (LLM 비용이 드는 재생성 방지)
4. hs_code_build로 CSV 생성 → 요약 누락이 없으면 hs_code_load로 적재
결과 상태: DONE · NOT_READY · ALREADY_LOADED · FAILED. 진행과 오류는 hscode_outputs/logs/hs_code_refresh_<시각>.log에 남깁니다.
실행할 때마다 batch_run에 한 건 기록합니다 (job HS_CODE_REFRESH, 대상 hs_code, 기간 = 기대 HS 판의 적용 기간).
DONE·NOT_READY·ALREADY_LOADED는 SUCCESS(작업이 정상적으로 확인·처리됨), FAILED는 FAILED입니다.
"""
import argparse
import logging
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from backend.app.db.batch_log import track_batch
from backend.app.db.crud import CRUD
from backend.app.db.session import make_engine, session_scope
from backend.app.models.hs_code import HsCodeMst
from backend.app.scheduler.jobs import hs_code_load

KST = ZoneInfo("Asia/Seoul")
PORTAL = "https://www.data.go.kr"
DATASET_ID = "15130660"  # 관세청_HS부호 단위별 품목명 (파일데이터)
TIMEOUT = 120
JOB_NAME = "HS_CODE_REFRESH"
SOURCE = hs_code_load.SOURCE  # service_option의 SOURCE 행 (KCS_HS_NAMES)
log = logging.getLogger("hs_code_refresh")


hb = hs_code_load.hb  # collectors/hscode/hs_code_build.py


class DownloadError(RuntimeError):
    pass


def setup_log(log_dir):
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"hs_code_refresh_{datetime.now(KST):%Y%m%d_%H%M%S}.log"
    log.setLevel(logging.INFO)
    for h in log.handlers:
        h.close()
    log.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    for h in (logging.StreamHandler(), logging.FileHandler(path, encoding="utf-8")):
        h.setFormatter(fmt)
        log.addHandler(h)
    return path


def download_latest(dest_dir=hb.REFERENCE_DIR, run=None):
    """포털 화면의 다운로드 버튼과 같은 순서(파일 정보 조회 → 다운로드 제한 확인 → 다운로드)로 최신 xlsx를 받습니다.
    이미 같은 판 파일이 있으면 다시 받지 않습니다. 자동입력방지(캡차)를 요구하면 우회하지 않고 오류로 끝냅니다."""
    s = requests.Session()
    s.headers["User-Agent"] = "Mozilla/5.0"
    if run is not None:  # 포털에 보낸 요청 수를 batch_run.request_count에 셈
        s.hooks["response"].append(lambda res, *a, **k: setattr(run, "request_count", run.request_count + 1))
    html = s.get(f"{PORTAL}/data/{DATASET_ID}/fileData.do", timeout=TIMEOUT).text
    args = re.search(r"fn_fileDataDown\('(\d+)',\s*'([^']+)',\s*'([^']*)',\s*'(\d+)'", html)
    title = re.search(r"<title>([^<|]+)", html)
    if not args or not title:
        raise DownloadError("포털 데이터셋 화면에서 다운로드 정보를 찾지 못했습니다 (화면 구조 변경 가능성).")
    date = hb.edition_date(title.group(1))
    if not date:
        raise DownloadError(f"데이터셋 제목에서 판 날짜(YYYYMMDD)를 찾지 못했습니다: {title.group(1).strip()}")
    dest = Path(dest_dir) / f"hs_names_{date}.xlsx"
    if dest.exists():
        log.info(f"이미 받은 판입니다: {dest.name}")
        return dest

    info = s.post(f"{PORTAL}/tcs/dss/selectFileDataDownload.do", timeout=TIMEOUT, data={
        "publicDataPk": args[1], "publicDataDetailPk": args[2], "atchFileId": args[3],
        "fileDetailSn": args[4], "publicDataTyCode": "PR0051"}).json()
    if not info.get("status"):
        raise DownloadError(f"파일 정보 조회 실패: {info.get('error') or info}")
    file_args = {"atchFileId": info["atchFileId"], "fileDetailSn": info["fileDetailSn"]}
    if s.post(f"{PORTAL}/cmm/cmm/check-limit.json", data=file_args, timeout=TIMEOUT).json().get("needCaptcha"):
        raise DownloadError("포털이 자동입력방지(캡차)를 요구합니다. 포털에서 직접 받아 reference/에 넣어 주세요.")
    res = s.get(f"{PORTAL}/cmm/cmm/fileDownload.do", timeout=TIMEOUT,
                params={**file_args, "dataNm": info["dataSetFileDetailInfo"]["dataNm"]})
    res.raise_for_status()
    if not res.content.startswith(b"PK"):  # xlsx(zip)가 아니면 오류 화면 등
        raise DownloadError(f"받은 파일이 xlsx가 아닙니다 (Content-Type {res.headers.get('Content-Type')}).")
    dest.write_bytes(res.content)
    log.info(f"다운로드 완료: {dest.name} ({len(res.content):,} bytes)")
    return dest


def loaded_count(hs_ver, engine=None):
    engine = engine or make_engine()
    try:
        with session_scope(engine) as s:
            return CRUD(HsCodeMst).count(s, hs_ver=hs_ver)
    finally:
        engine.dispose()


def run_refresh(today=None, download=download_latest, build=hb.run, load=hs_code_load.run, count=loaded_count,
                log_dir=hb.OUTPUT_DIR / "logs", batch_engine=None):
    """한 번 실행하고 상태(DONE·NOT_READY·ALREADY_LOADED·FAILED)를 돌려줍니다. 단계 함수는 테스트에서 바꿔 넣습니다."""
    today = today or datetime.now(KST).date()
    log_path = setup_log(log_dir)
    expected = hb.hs_ver_for(today.strftime("%Y%m%d"))
    log.info(f"HS 판 갱신 시작 | 기준일 {today} | 기대 판 {expected}")
    with track_batch(SOURCE, JOB_NAME, "hs_code", *hb.hs_period(expected), engine=batch_engine, logger=log) as run:
        status = _refresh(expected, run, log_path, download, build, load, count)
        if status == "FAILED":
            run.fail()
    return status


def _refresh(expected, run, log_path, download, build, load, count):
    try:
        xlsx = download(run=run)
        file_ver = hb.hs_ver_for(hb.edition_date(xlsx))
        if file_ver != expected:
            log.warning(f"NOT_READY: 최신 파일 {xlsx.name}은 {file_ver} 판입니다. {expected} 판이 아직 올라오지 않았습니다. | 로그: {log_path}")
            return "NOT_READY"
        if (n := count(expected)) > 0:
            log.info(f"ALREADY_LOADED: DB에 {expected} {n:,}행이 이미 있습니다. 다시 만들려면 수동 실행하세요. | 로그: {log_path}")
            return "ALREADY_LOADED"
        result = build(xlsx, expected, output_dir=hb.OUTPUT_DIR / expected)
        run.request_count += result["requests"]
        run.received_count = result["received"]
        if result["missing"]:
            log.error(f"FAILED: 요약 누락 {result['missing']}개로 적재하지 않았습니다 (다시 실행하면 누락분만 요청). | 로그: {log_path}")
            return "FAILED"
        if load(result["csv"], track=False) != 0:  # 이 실행의 batch_run 한 건에 포함 (적재만 따로 기록하지 않음)
            log.error(f"FAILED: 적재 실패. hs_code_load 로그를 확인하세요. | 로그: {log_path}")
            return "FAILED"
    except Exception:
        log.exception(f"FAILED: 갱신 중 오류 | 로그: {log_path}")
        return "FAILED"
    log.info(f"DONE: {expected} 원천 {result['received']:,}행 → 적재 {result['rows']:,}행 | CSV {result['csv']} | 로그: {log_path}")
    log.info("  (원천은 xlsx 2~10단위 전체, 적재는 2·4·6·10단위만 넣고 6단위 목록은 10단위 코드로 만들어 수가 다릅니다)")
    return "DONE"


def main():
    parser = argparse.ArgumentParser(description="관세청 HS부호 xlsx 다운로드 → CSV 생성 → DB 적재")
    parser.add_argument("--date", help="기준일 YYYYMMDD (기본: 오늘, KST)")
    args = parser.parse_args()
    today = datetime.strptime(args.date, "%Y%m%d").date() if args.date else None
    status = run_refresh(today)
    raise SystemExit(0 if status in ("DONE", "ALREADY_LOADED") else 1)


if __name__ == "__main__":
    main()
