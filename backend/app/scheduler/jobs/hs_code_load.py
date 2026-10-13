"""hs_code_build.py가 만든 hs_code_mst.csv를 검증한 뒤 MySQL hs_code 테이블에 적재(upsert)합니다.

실행 (저장소 루트에서): python -m backend.app.scheduler.jobs.hs_code_load [--csv 경로]
- 테이블은 만들지 않습니다. hs_code 테이블이 없으면 오류 로그를 남기고 종료합니다.
- 6단위 name_ko('요약|원문')는 '|'로 나눠 name_ko(요약)와 original_name_ko(원문)에 넣습니다. 다른 단위의 원문은 NULL입니다.
- 같은 (hs_code, hs_ver)가 있으면 name_ko·original_name_ko·source_edition을 갱신합니다.
- 실행 결과와 오류(검증·파일·DB 연결·적재 실패)를 화면과 hscode_outputs/logs/hs_code_load_<시각>.log에 남깁니다.
  예상하지 못한 오류는 traceback까지 기록하고, 적재 중 실패하면 전체 롤백되어 아무것도 반영되지 않습니다.
- 실행할 때마다 batch_run에 한 건 기록합니다 (job HS_CODE_LOAD, 대상 hs_code, 수신 = CSV 행 수, API 호출 0).
  기간은 CSV의 hs_ver 적용 기간입니다. hs_code_refresh 안에서 부를 때는 그쪽 기록에 포함되므로 따로 남기지 않습니다.
- depth는 DB가 hs_code 길이로 계산하므로 CSV 값은 검증에만 씁니다.
- 검증 오류가 하나라도 있으면 아무것도 적재하지 않고 종료합니다.
"""
import argparse
import csv
import importlib.util
import logging
import re
from datetime import datetime
from pathlib import Path

from sqlalchemy import inspect
from sqlalchemy.exc import SQLAlchemyError

from backend.app.db.batch_log import Source, track_batch
from backend.app.db.crud import CRUD
from backend.app.db.session import make_engine, session_scope
from backend.app.models.hs_code import HsCodeMst

BUILD_PATH = Path(__file__).resolve().parents[1] / "collectors" / "hscode" / "hs_code_build.py"


def _load_build():
    """collectors/hscode/hs_code_build.py (패키지가 아닌 독립 실행 스크립트)를 모듈로 불러옵니다."""
    spec = importlib.util.spec_from_file_location("hs_code_build", BUILD_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hb = _load_build()  # HS 판 규칙(hs_ver_for·hs_period)을 한 곳에서만 관리
DEFAULT_CSV = (Path(__file__).resolve().parents[1] / "collectors" / "hscode" / "hscode_outputs" / "hs_code_mst.csv")
LOG_DIR = DEFAULT_CSV.parent / "logs"
COLUMNS = ["hs_code", "depth", "name_ko", "source_edition", "hs_ver"]
SEP = "|"  # hs_code_build.py의 6단위 '요약|원문' 구분기호
log = logging.getLogger("hs_code_load")
JOB_NAME = "HS_CODE_LOAD"
SOURCE = Source("KCS_HS_NAMES", "관세청 HS부호 단위별 품목명",
                {"provider": "관세청", "url": "https://www.data.go.kr/data/15130660/fileData.do", "dataset_id": "15130660",
                 "format": "xlsx"})


class TableMissingError(RuntimeError):
    pass


def setup_log(log_dir):
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"hs_code_load_{datetime.now():%Y%m%d_%H%M%S}.log"
    log.setLevel(logging.DEBUG)
    for h in log.handlers:  # 이전 실행의 로그 파일을 닫고 새로 연결
        h.close()
    log.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    # 화면은 INFO 이상, 파일은 DB 오류 traceback(DEBUG)까지 남깁니다.
    for h, level in ((logging.StreamHandler(), logging.INFO), (logging.FileHandler(path, encoding="utf-8"), logging.DEBUG)):
        h.setFormatter(fmt)
        h.setLevel(level)
        log.addHandler(h)
    return path


def read_rows(path):
    with open(path, encoding="utf-8-sig", newline="") as f:  # 앞자리 0을 지키려고 모두 문자열로 읽습니다
        reader = csv.DictReader(f)
        missing = set(COLUMNS) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path}에 {', '.join(sorted(missing))} 컬럼이 없습니다.")
        return [{k: (r[k] or "").strip() for k in COLUMNS} for r in reader]


def to_db_row(r):
    """CSV 행 → hs_code 테이블 행. 6단위는 '요약|원문'을 나눕니다."""
    name, original = r["name_ko"], None
    if len(r["hs_code"]) == 6 and SEP in name:
        name, original = name.split(SEP, 1)
    return {"hs_code": r["hs_code"], "hs_ver": r["hs_ver"], "name_ko": name.strip(),
            "original_name_ko": original.strip() if original is not None else None, "source_edition": r["source_edition"]}


def validate(rows):
    """테이블 제약(형식·필수값·길이·기본키·6단위 원문)에 맞는지 확인하고 오류 목록을 돌려줍니다."""
    limits = {c.name: c.type.length for c in HsCodeMst.__table__.columns if getattr(c.type, "length", None)}
    errors, seen = [], set()
    for line, r in enumerate(rows, start=2):  # 1행은 헤더
        code, ver = r["hs_code"], r["hs_ver"]
        if not re.fullmatch(r"[0-9]{2}|[0-9]{4}|[0-9]{6}|[0-9]{10}", code):  # isdigit()은 '¹²' 같은 유니코드 숫자도 통과시킴
            errors.append(f"{line}행 hs_code '{code}': 2·4·6·10자리 숫자가 아님")
        if r["depth"] and r["depth"] != str(len(code)):
            errors.append(f"{line}행 {code}: depth {r['depth']} ≠ 코드 길이 {len(code)}")
        if not re.fullmatch(r"[A-Z0-9_]+", ver):
            errors.append(f"{line}행 {code}: hs_ver '{ver}'는 영대문자·숫자·_만 가능")
        if (code, ver) in seen:
            errors.append(f"{line}행 {code}: (hs_code, hs_ver) 중복")
        seen.add((code, ver))
        if not r["source_edition"]:
            errors.append(f"{line}행 {code}: source_edition 비어 있음")
        count = r["name_ko"].count(SEP)
        if len(code) == 6 and count != 1:
            errors.append(f"{line}행 {code}: 6단위 name_ko에 '{SEP}'가 1개여야 함 ({count}개)")
        elif len(code) != 6 and count:
            errors.append(f"{line}행 {code}: 6단위가 아닌데 name_ko에 '{SEP}'가 있음")
        db_row = to_db_row(r)
        if not db_row["name_ko"]:
            errors.append(f"{line}행 {code}: name_ko(요약) 비어 있음")
        if len(code) == 6 and not db_row["original_name_ko"]:
            errors.append(f"{line}행 {code}: original_name_ko(원문) 비어 있음")
        for col, limit in limits.items():
            if db_row.get(col) and len(db_row[col]) > limit:
                errors.append(f"{line}행 {code}: {col} {len(db_row[col])}자 > {limit}자")
    return errors


def load(engine, rows):
    table = HsCodeMst.__tablename__
    if not inspect(engine).has_table(table):
        raise TableMissingError(f"{engine.url.render_as_string(hide_password=True)}에 {table} 테이블이 없습니다. "
                                "테이블을 먼저 생성한 뒤 다시 실행하세요.")
    hs = CRUD(HsCodeMst)
    with session_scope(engine) as s:  # 행 수도 커밋 전에 같은 트랜잭션에서 셈: 어느 단계든 실패하면 전체 롤백
        hs.upsert_many(s, [to_db_row(r) for r in rows])
        return hs.count(s)


def main():
    parser = argparse.ArgumentParser(description="hs_code_mst.csv를 MySQL hs_code 테이블에 적재")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV, help="적재할 CSV (기본: hscode_outputs/hs_code_mst.csv)")
    parser.add_argument("--db-url", help="SQLAlchemy DB URL (기본: .env의 MYSQL_* 값)")
    args = parser.parse_args()

    raise SystemExit(run(args.csv, args.db_url))


def hs_period_of(rows, fallback="HS2022"):
    """CSV에 든 hs_ver들의 적용 기간 (여러 판이면 가장 이른 시작 ~ 가장 늦은 끝)."""
    periods = [hb.hs_period(v) for v in {r["hs_ver"] for r in rows if re.fullmatch(r"HS\d{4}", r["hs_ver"])}]
    periods = periods or [hb.hs_period(fallback)]
    return min(p[0] for p in periods), max(p[1] for p in periods)


def run(csv_path, db_url=None, log_dir=LOG_DIR, track=True):
    """적재를 실행하고 결과·오류를 로그에 남깁니다. 성공 0, 실패 1을 돌려줍니다. track=False면 batch_run 기록을 생략합니다."""
    log_path = setup_log(log_dir)
    log.info(f"HS 코드 적재 시작 | CSV {csv_path}")
    if not track:
        return _run(csv_path, db_url, log_path, None)
    try:
        period = hs_period_of(read_rows(csv_path))
    except Exception:
        period = hb.hs_period("HS2022")  # 파일을 못 읽어도 실패 기록은 남김 (원인은 아래 _run에서 로그)
    batch_engine = make_engine(db_url) if db_url else None  # None이면 track_batch가 .env의 MySQL에 연결
    try:
        with track_batch(SOURCE, JOB_NAME, "hs_code", *period, engine=batch_engine, logger=log) as batch:
            rc = _run(csv_path, db_url, log_path, batch)
            if rc != 0:
                batch.fail()
    finally:
        if batch_engine is not None:
            batch_engine.dispose()
    return rc


def _run(csv_path, db_url, log_path, batch):
    started = datetime.now()
    engine = None
    try:
        rows = read_rows(csv_path)
        if batch is not None:
            batch.received_count = len(rows)
        errors = validate(rows)
        log.info(f"CSV {len(rows):,}행 | 검증 오류 {len(errors)}건")
        if errors:
            for e in errors[:50]:
                log.error(e)
            if len(errors) > 50:
                log.error(f"… 외 {len(errors) - 50:,}건")
            log.error(f"적재 실패: 검증 오류가 있어 적재하지 않았습니다. | 로그: {log_path}")
            return 1
        engine = make_engine(db_url)
        log.info(f"DB {engine.url.render_as_string(hide_password=True)}")
        total = load(engine, rows)
    except (FileNotFoundError, ValueError, TableMissingError) as e:
        log.error(f"적재 실패: {e} | 로그: {log_path}")
        return 1
    except SQLAlchemyError as e:
        log.error(f"적재 실패: DB 오류로 롤백되어 반영되지 않았습니다. {type(e).__name__}: {e.__cause__ or e} | 로그: {log_path}")
        log.debug("DB 오류 상세", exc_info=True)
        return 1
    except Exception:
        log.exception(f"적재 실패: 예상하지 못한 오류 | 로그: {log_path}")
        return 1
    finally:
        if engine is not None:
            engine.dispose()
    log.info(f"적재 완료: {len(rows):,}행 upsert | 테이블 전체 {total:,}행 | "
             f"소요 {str(datetime.now() - started).split('.')[0]} | 로그: {log_path}")
    return 0


if __name__ == "__main__":
    main()
