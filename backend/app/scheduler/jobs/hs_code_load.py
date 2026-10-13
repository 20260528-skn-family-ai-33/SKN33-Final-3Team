"""hs_code_build.py가 만든 hs_code_mst.csv를 검증한 뒤 MySQL hs_code_mst 테이블에 적재(upsert)합니다.

실행 (저장소 루트에서): python -m backend.app.scheduler.jobs.hs_code_load [--csv 경로]
- 테이블은 만들지 않습니다. hs_code_mst 테이블이 없으면 오류 로그를 남기고 종료합니다.
- 같은 hs_code가 있으면 name_ko·source_edition·hs_ver를 갱신합니다.
- 로그는 화면과 CSV 폴더의 logs/hs_code_load_<시각>.log에 함께 남깁니다.
- depth는 DB가 hs_code 길이로 계산하므로 CSV 값은 검증에만 씁니다.
- 검증 오류가 하나라도 있으면 아무것도 적재하지 않고 종료합니다.
"""
import argparse
import csv
import logging
from datetime import datetime
from pathlib import Path

from sqlalchemy import inspect

from backend.app.db.crud import CRUD
from backend.app.db.session import make_engine, session_scope
from backend.app.models.hs_code import HsCodeMst

DEFAULT_CSV = (Path(__file__).resolve().parents[1] / "collectors" / "hscode" / "hscode_outputs" / "hs_code_mst.csv")
COLUMNS = ["hs_code", "depth", "name_ko", "source_edition", "hs_ver"]
log = logging.getLogger("hs_code_load")


class TableMissingError(RuntimeError):
    pass


def setup_log(log_dir):
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"hs_code_load_{datetime.now():%Y%m%d_%H%M%S}.log"
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    for h in (logging.StreamHandler(), logging.FileHandler(path, encoding="utf-8")):
        h.setFormatter(fmt)
        log.addHandler(h)
    return path


def read_rows(path):
    with open(path, encoding="utf-8-sig", newline="") as f:  # 앞자리 0을 지키려고 모두 문자열로 읽습니다
        reader = csv.DictReader(f)
        missing = set(COLUMNS) - set(reader.fieldnames or [])
        if missing:
            raise SystemExit(f"{path}에 {', '.join(sorted(missing))} 컬럼이 없습니다.")
        return [{k: (r[k] or "").strip() for k in COLUMNS} for r in reader]


def validate(rows):
    """테이블 제약(길이·필수값·기본키)에 맞는지 확인하고 오류 목록을 돌려줍니다."""
    limits = {c.name: c.type.length for c in HsCodeMst.__table__.columns if getattr(c.type, "length", None)}
    errors, seen = [], set()
    for line, r in enumerate(rows, start=2):  # 1행은 헤더
        code = r["hs_code"]
        if not code.isdigit() or len(code) not in (2, 4, 6, 10):
            errors.append(f"{line}행 hs_code '{code}': 2·4·6·10자리 숫자가 아님")
        if r["depth"] and r["depth"] != str(len(code)):
            errors.append(f"{line}행 {code}: depth {r['depth']} ≠ 코드 길이 {len(code)}")
        if code in seen:
            errors.append(f"{line}행 {code}: hs_code 중복")
        seen.add(code)
        if not r["name_ko"]:
            errors.append(f"{line}행 {code}: name_ko 비어 있음")
        for col, limit in limits.items():
            if len(r[col]) > limit:
                errors.append(f"{line}행 {code}: {col} {len(r[col])}자 > {limit}자")
    return errors


def load(engine, rows):
    table = HsCodeMst.__tablename__
    if not inspect(engine).has_table(table):
        raise TableMissingError(f"{engine.url.render_as_string(hide_password=True)}에 {table} 테이블이 없습니다. "
                                "테이블을 먼저 생성한 뒤 다시 실행하세요.")
    hs = CRUD(HsCodeMst)
    payload = [{**r, "source_edition": r["source_edition"] or None, "hs_ver": r["hs_ver"] or None} for r in rows]
    with session_scope(engine) as s:
        hs.upsert_many(s, payload)
    with session_scope(engine) as s:
        return hs.count(s)


def main():
    parser = argparse.ArgumentParser(description="hs_code_mst.csv를 MySQL hs_code_mst 테이블에 적재")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV, help="적재할 CSV (기본: hscode_outputs/hs_code_mst.csv)")
    parser.add_argument("--db-url", help="SQLAlchemy DB URL (기본: .env의 MYSQL_* 값)")
    args = parser.parse_args()

    log_path = setup_log(args.csv.parent / "logs")
    rows = read_rows(args.csv)
    errors = validate(rows)
    log.info(f"CSV {args.csv} | {len(rows):,}행 | 검증 오류 {len(errors)}건")
    if errors:
        for e in errors[:20]:
            log.error(e)
        log.error(f"검증 오류가 있어 적재하지 않았습니다. 로그: {log_path}")
        raise SystemExit(1)

    try:
        total = load(make_engine(args.db_url), rows)
    except TableMissingError as e:
        log.error(f"{e} 로그: {log_path}")
        raise SystemExit(1)
    log.info(f"적재 완료: {len(rows):,}행 upsert | 테이블 전체 {total:,}행 | 로그: {log_path}")


if __name__ == "__main__":
    main()
