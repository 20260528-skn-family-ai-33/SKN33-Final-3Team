"""HS 코드 연간 갱신 자동화: 관세청 xlsx 다운로드 → HS 코드 CSV 생성(LLM 요약) → MySQL hs_code 적재.

실행 (저장소 루트에서): python -m backend.app.scheduler.jobs.hs_code_refresh [--date YYYYMMDD]
스케줄: backend/app/scheduler/main.py가 매년 1월 1일 00:15(KST)에 실행합니다.
관세청 10단위(HSK)는 매년 개정되고, 6단위 HS 판(HS2022 → HS2028 …)은 몇 년마다 바뀝니다. 매년 실행에서 둘 다 처리합니다.

1. 공공데이터포털 '관세청_HS부호 단위별 품목명'(15130660)의 최신 xlsx를 받아 reference/hs_names_YYYYMMDD.xlsx로 저장
2. 파일 판 날짜가 올해가 아니면 NOT_READY로 끝냄 → 스케줄러가 다음 날 재시도
   과거 연도(올해 판이 아직 안 올라옴)뿐 아니라 미래 연도(내년 판이 연말에 미리 올라옴, 적용일 전)도 반영하지 않음
3. xlsx로 만들 코드(2·4·6·10단위)가 DB에 모두 있고 이 판(source_edition)으로 적재돼 있으면 ALREADY_LOADED로 끝냄
   새 코드가 있거나 이전 판으로 적재돼 있으면 계속 진행합니다 (요약 캐시가 있으면 새 6단위만 API 요청)
   새 판에서 빠진 이전 코드는 지우지 않습니다 (과거 수출입 데이터 연결용). upsert라 새 코드는 추가, 기존 코드는 이름·판 갱신
4. hs_code_build로 CSV 생성 → 요약 누락이 없으면 hs_code_load로 적재
5. DONE·ALREADY_LOADED이면 올해 판과 직전 판(예: HS2028 ↔ HS2022)의 대응 쌍이 원천과 hs_code_correlation에서 모두 같은지 보고,
   다르면(없음·일부만 있음·원천 변경) hs_correlation_load로 다시 적재합니다. 같으면 확인만 합니다.
   대응표 적재가 실패하면(UNSD에 아직 없음 등) FAILED로 끝내 다음 날 재시도합니다 (hs_code는 이미 적재된 상태로 남음).
결과 상태: DONE · NOT_READY · ALREADY_LOADED · FAILED. 진행과 오류는 hscode_outputs/logs/hs_code_refresh_<시각>.log에 남깁니다.
실행할 때마다 batch_run에 한 건 기록합니다 (job HS_CODE_REFRESH, 대상 hs_code, 기간 = 기준일이 속한 해 1/1~12/31).
DONE·NOT_READY·ALREADY_LOADED는 SUCCESS(작업이 정상적으로 확인·처리됨), FAILED는 FAILED입니다.
"""
import argparse
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from backend.app.db.batch_log import track_batch
from sqlalchemy import select

from backend.app.db.crud import CRUD
from backend.app.db.session import make_engine, session_scope
from backend.app.models.hs_code import HsCodeMst
from backend.app.models.hs_code_correlation import HsCodeCorrelation
from backend.app.scheduler.jobs import hs_code_load, hs_correlation_load

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


def check_xlsx(path):
    """관세청 HS xlsx로 읽히고 10단위 코드가 있는지 확인합니다. 아니면 DownloadError.
    'PK'로 시작해도 zip이 깨졌거나 시트 형식이 다르면 여기서 걸러, 손상 파일이 캐시로 남지 않게 합니다."""
    try:
        ten = hb.codes_by_level(hb.load_names(path))[10]
    except Exception as e:  # zip 손상·시트 형식 오류 등 (openpyxl·zipfile·pandas 예외 종류가 다양함)
        raise DownloadError(f"{Path(path).name}을 관세청 HS xlsx로 읽지 못했습니다: {type(e).__name__}: {e}") from e
    if not ten:
        raise DownloadError(f"{Path(path).name}에 10단위 코드가 없습니다.")


def download_latest(dest_dir=hb.REFERENCE_DIR, run=None):
    """포털 화면의 다운로드 버튼과 같은 순서(파일 정보 조회 → 다운로드 제한 확인 → 다운로드)로 최신 xlsx를 받습니다.
    이미 같은 판 파일이 있고 읽을 수 있으면 다시 받지 않습니다 (읽을 수 없으면 다시 받음).
    받은 내용은 임시 파일에서 check_xlsx를 통과해야만 hs_names_YYYYMMDD.xlsx로 교체합니다.
    자동입력방지(캡차)를 요구하면 우회하지 않고 오류로 끝냅니다."""
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
        try:
            check_xlsx(dest)
            log.info(f"이미 받은 판입니다: {dest.name}")
            return dest
        except DownloadError as e:
            log.warning(f"받아 둔 파일을 읽을 수 없어 다시 받습니다: {e}")

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
    # 임시 파일 이름은 hs_names_*로 시작하지 않게 (중간에 멈춰도 latest_reference()가 고르지 않음)
    tmp = dest.with_name(f"_download_{dest.name}")
    tmp.write_bytes(res.content)
    try:
        check_xlsx(tmp)
    except DownloadError:
        tmp.unlink()
        raise
    tmp.replace(dest)
    log.info(f"다운로드 완료: {dest.name} ({len(res.content):,} bytes)")
    return dest


def loaded_count(hs_ver, engine=None):
    engine = engine or make_engine()
    try:
        with session_scope(engine) as s:
            return CRUD(HsCodeMst).count(s, hs_ver=hs_ver)
    finally:
        engine.dispose()


def loaded_codes(hs_ver, engine=None):
    """DB에 그 HS 판으로 들어 있는 {hs_code: source_edition}."""
    engine = engine or make_engine()
    try:
        with session_scope(engine) as s:
            stmt = select(HsCodeMst.hs_code, HsCodeMst.source_edition).where(HsCodeMst.hs_ver == hs_ver)
            return dict(s.execute(stmt).all())
    finally:
        engine.dispose()


def expected_codes(xlsx):
    """xlsx로 만들 2·4·6·10단위 코드 집합 (LLM 요약 없이 계산)."""
    levels = hb.codes_by_level(hb.load_names(xlsx))
    return {code for codes in levels.values() for code in codes}


def previous_ver(hs_ver):
    """직전 HS 판: 그 판이 시작되기 전날에 적용되던 판 (HS2028 → HS2022). 더 이전 판이 없으면(HS2017) 자기 자신."""
    return hb.hs_ver_for(hb.hs_period(hs_ver)[0] - timedelta(days=1))


def ensure_correlation(new, engine=None):
    """new와 직전 판의 대응표가 원천(UNSD 파일)의 대응 쌍과 DB에서 모두 같은지 보고, 다르면 다시 적재합니다.
    행이 하나라도 있다고 완료로 보지 않습니다 (일부만 들어간 경우·원천 파일이 바뀐 경우도 다시 적재). 0 성공, 1 실패."""
    old = previous_ver(new)
    if old == new:
        return 0
    try:  # 받아 둔 파일이 있으면 다시 받지 않음 (읽을 수 없으면 다시 받음)
        need = {(n, o) for n, o, _ in hs_correlation_load.fetch_pairs(new, old)[0]}
    except (FileNotFoundError, ValueError, requests.RequestException) as e:
        log.error(f"대응표 {new}↔{old} 원천을 확인하지 못했습니다: {e}")
        return 1
    engine = engine or make_engine()
    try:
        with session_scope(engine) as s:
            have = set(s.execute(select(HsCodeCorrelation.new_code, HsCodeCorrelation.old_code).where(
                HsCodeCorrelation.new_ver == new, HsCodeCorrelation.old_ver == old)).all())
    finally:
        engine.dispose()
    if have == need:
        log.info(f"대응표 {new}↔{old} {len(have):,}쌍이 원천과 같습니다.")
        return 0
    log.info(f"대응표 {new}↔{old}가 원천과 달라 다시 적재합니다 (DB {len(have):,}쌍, 원천 {len(need):,}쌍, "
             f"빠짐 {len(need - have):,}, 원천에 없음 {len(have - need):,}).")
    return hs_correlation_load.run(new, old, track=False)  # 이 실행의 batch_run 한 건에 포함


def run_refresh(today=None, download=download_latest, build=hb.run, load=hs_code_load.run, loaded=loaded_codes,
                expected_of=expected_codes, correlate=ensure_correlation,
                log_dir=hb.OUTPUT_DIR / "logs", batch_engine=None):
    """한 번 실행하고 상태(DONE·NOT_READY·ALREADY_LOADED·FAILED)를 돌려줍니다. 단계 함수는 테스트에서 바꿔 넣습니다."""
    today = today or datetime.now(KST).date()
    log_path = setup_log(log_dir)
    expected = hb.hs_ver_for(today.strftime("%Y%m%d"))
    log.info(f"HS 판 갱신 시작 | 기준일 {today} | 기대 판 {expected}")
    year = (date(today.year, 1, 1), date(today.year, 12, 31))
    with track_batch(SOURCE, JOB_NAME, "hs_code", *year, engine=batch_engine, logger=log) as run:
        status = _refresh(today, expected, run, log_path, download, build, load, loaded, expected_of)
        if status in ("DONE", "ALREADY_LOADED") and _correlate(correlate, expected, log_path) != 0:
            status = "FAILED"
        if status == "FAILED":
            run.fail()
    return status


def _correlate(correlate, hs_ver, log_path):
    try:
        rc = correlate(hs_ver)
    except Exception:  # hs_code_correlation 테이블 없음·DB 연결 실패 등
        log.exception(f"FAILED: 대응표 확인·적재 중 오류 | 로그: {log_path}")
        return 1
    if rc != 0:
        log.error(f"FAILED: 대응표 적재 실패. hs_correlation_load 로그를 확인하세요 (hs_code 적재는 유지). | 로그: {log_path}")
    return rc


def _refresh(today, expected, run, log_path, download, build, load, loaded, expected_of):
    try:
        xlsx = download(run=run)
        edition = hb.edition_date(xlsx)
        file_ver = hb.hs_ver_for(edition)
        if int(edition[:4]) > today.year:  # 내년 판이 연말에 미리 올라온 경우: 적용일(1월 1일) 전에는 반영하지 않음
            log.warning(f"NOT_READY: 최신 파일 {xlsx.name}({file_ver})은 {edition[:4]}년 판으로 아직 적용 전입니다. "
                        f"{edition[:4]}년 1월 1일 이후 실행에서 반영합니다. | 로그: {log_path}")
            return "NOT_READY"
        if int(edition[:4]) < today.year or file_ver != expected:
            log.warning(f"NOT_READY: 최신 파일 {xlsx.name}({file_ver})은 {today.year}년 판이 아닙니다. "
                        f"{today.year}년 판({expected})이 아직 올라오지 않았습니다. | 로그: {log_path}")
            return "NOT_READY"
        source = hb.source_for(edition)
        need, have = expected_of(xlsx), loaded(expected)
        added = sorted(need - set(have))
        stale = sorted(c for c in need & set(have) if c not in hb.UN_STATS and have[c] != source)
        kept = sorted(c for c in set(have) - need if len(c) == 10)
        if not added and not stale:
            log.info(f"ALREADY_LOADED: DB에 {expected} 코드 {len(need):,}개가 {edition}판으로 모두 있습니다. "
                     f"다시 만들려면 수동 실행하세요. | 로그: {log_path}")
            return "ALREADY_LOADED"
        log.info(f"{edition}판 반영: 새 코드 {len(added):,}개, 이전 판으로 적재된 코드 {len(stale):,}개 갱신"
                 f"{' (새 코드 예: ' + ', '.join(added[:5]) + ')' if added else ''}")
        if kept:
            log.info(f"새 판에 없는 이전 10단위 코드 {len(kept):,}개는 과거 데이터 연결을 위해 그대로 둡니다 "
                     f"(예: {', '.join(kept[:5])})")
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
