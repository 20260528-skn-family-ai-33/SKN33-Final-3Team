"""HS 판 간 6단위 대응표를 받아 MySQL hs_code_correlation 테이블에 적재합니다.

실행 (저장소 루트에서): python -m backend.app.scheduler.jobs.hs_correlation_load --new HS2022 --old HS2017 [--xlsx 경로] [--refresh]
- 원천은 UN 통계국(UNSD)의 HS Conversion and Correlation Tables xlsx입니다. WCO 대응표(Table I·II)를 바탕으로 만든 표이며,
  WCO는 원본을 PDF로만 제공해서 같은 내용을 표로 주는 UNSD 파일을 씁니다.
  새 판(HS2028 등) 대응표가 UNSD에 올라오면 같은 명령에 --new HS2028 --old HS2022로 실행합니다.
- 받은 파일은 hscode_outputs/correlation/<new>to<old>.xlsx에 두고, 있으면 다시 받지 않습니다 (--refresh로 다시 받기).
  새로 받은 파일은 파싱 검증을 통과해야만 이 캐시로 교체하고, 받아 둔 파일을 읽을 수 없으면(손상·형식 오류) 다시 받습니다.
- 'Correlations' 시트의 (새 코드, 이전 코드, 관계)를 읽고, 대응으로 이어진 코드 묶음마다 비교 그룹(group_code)을 붙입니다.
  group_code는 묶음 안 가장 작은 새 판 코드라서, 같은 파일이면 실행할 때마다 같은 값이 나옵니다.
- 같은 판 쌍(new_ver, old_ver)의 기존 행을 지우고 새로 넣습니다 (원천에서 빠진 대응이 남지 않게). 한 트랜잭션이라 실패하면 전체 롤백됩니다.
- 실행할 때마다 batch_run에 한 건 기록합니다 (job HS_CORRELATION_LOAD, 대상 hs_code_correlation, 기간 = 새 판 적용 기간).
"""
import argparse
import logging
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests
from sqlalchemy import delete, inspect

from backend.app.db.batch_log import Source, track_batch
from backend.app.db.crud import CRUD
from backend.app.db.session import make_engine, session_scope
from backend.app.models.hs_code_correlation import RELATIONS, HsCodeCorrelation
from backend.app.scheduler.jobs.hs_code_load import hb

URL = "https://unstats.un.org/unsd/classifications/Econ/tables/{new}to{old}ConversionAndCorrelationTables.xlsx"
SAVE_DIR = hb.OUTPUT_DIR / "correlation"
LOG_DIR = hb.OUTPUT_DIR / "logs"
TIMEOUT = 120
# ponytail: 고정 기준. HS2022↔2017은 일반 코드 최대 16개, 허브 157개 이상으로 간격이 커서 100으로 둠. 새 판에서 분포를 보고 조정
HUB_FANOUT = 100
JOB_NAME = "HS_CORRELATION_LOAD"
SOURCE = Source("UNSD_HS_CORR", "UN 통계국 HS 판 간 대응표",
                {"provider": "UN Statistics Division", "url": "https://unstats.un.org/unsd/classifications/Econ",
                 "format": "xlsx", "basis": "WCO HS correlation tables"})
log = logging.getLogger("hs_correlation_load")


def setup_log(log_dir):
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"hs_correlation_load_{datetime.now():%Y%m%d_%H%M%S}.log"
    log.setLevel(logging.INFO)
    for h in log.handlers:
        h.close()
    log.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    for h in (logging.StreamHandler(), logging.FileHandler(path, encoding="utf-8")):
        h.setFormatter(fmt)
        log.addHandler(h)
    return path


def fetch_pairs(new, old, save_dir=SAVE_DIR, refresh=False):
    """(대응 쌍, 새로 받았는지)를 돌려줍니다. 받아 둔 파일을 먼저 쓰고, 읽을 수 없으면 다시 받습니다.
    받은 내용은 임시 파일에서 파싱 검증을 통과해야만 캐시로 교체합니다 (손상 파일이 캐시에 남지 않게)."""
    path = Path(save_dir) / f"{new}to{old}.xlsx"
    if path.exists() and not refresh:
        try:
            pairs = parse(path, new, old)
            log.info(f"이미 받은 대응표: {path}")
            return pairs, False
        except ValueError as e:
            log.warning(f"받아 둔 대응표를 읽을 수 없어 다시 받습니다: {e}")
    res = requests.get(URL.format(new=new, old=old), timeout=TIMEOUT)
    if res.status_code == 404:
        raise FileNotFoundError(f"UNSD에 {new}→{old} 대응표가 아직 없습니다 ({URL.format(new=new, old=old)}).")
    res.raise_for_status()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.stem}.tmp.xlsx")
    tmp.write_bytes(res.content)
    try:
        pairs = parse(tmp, new, old)
    except ValueError:
        tmp.unlink()
        raise
    tmp.replace(path)
    log.info(f"대응표 다운로드: {path.name} ({len(res.content):,} bytes)")
    return pairs, True


def parse(path, new, old):
    """Correlations 시트를 [(새 코드, 이전 코드, 관계)]로 읽습니다. 시트·머리글·값 형식이 다르면 ValueError."""
    try:
        sheets = pd.read_excel(path, sheet_name=None, dtype=str, header=None)
    except Exception as e:  # zip 손상·HTML 오류 화면 등 (openpyxl·zipfile 예외 종류가 다양함)
        raise ValueError(f"{Path(path).name}을 xlsx로 읽지 못했습니다: {type(e).__name__}: {e}") from e
    sheet = next((df for name, df in sheets.items() if "correlation" in name.lower()), None)
    if sheet is None:
        raise ValueError(f"{path.name}에 Correlations 시트가 없습니다: {list(sheets)}")
    if sheet.shape[1] < 3:  # 새 코드·이전 코드·관계. 없으면 아래 sheet[1]에서 KeyError가 나 캐시 복구(ValueError)를 못 탐
        raise ValueError(f"{path.name}의 Correlations 시트 열이 {sheet.shape[1]}개입니다 (새 코드·이전 코드·관계 3개 필요).")
    head = sheet.index[(sheet[0].str.strip() == new) & (sheet[1].str.strip() == old)]
    if head.empty:
        raise ValueError(f"{path.name}에서 '{new} | {old}' 머리글 행을 찾지 못했습니다 (파일 형식 변경 가능성).")
    pairs, errors = [], []
    for i, (n, o, rel) in sheet.iloc[head[0] + 1:, :3].fillna("").iterrows():
        n, o, rel = n.strip(), o.strip(), rel.strip().lower()
        if not (n or o or rel):
            continue
        if not (re.fullmatch(r"[0-9]{6}", n) and re.fullmatch(r"[0-9]{6}", o) and rel in RELATIONS):
            errors.append(f"{i + 1}행: {n!r} {o!r} {rel!r}")
            continue
        pairs.append((n, o, rel))
    if errors or not pairs:
        raise ValueError(f"대응표 형식 오류 {len(errors)}건 (행 {len(pairs)}개): {', '.join(errors[:10])}")
    return pairs


def group_codes(pairs):
    """대응으로 이어진 코드 묶음(연결 요소)마다 가장 작은 새 판 코드를 대표로 붙입니다. {새 코드: group_code}

    이전 코드 HUB_FANOUT개 넘게와 이어진 새 코드(허브)는 묶지 않고 자기 코드를 그룹으로 씁니다.
    HS2022의 신설 호 8549(전자 폐기물)·8524(평판 디스플레이 모듈)처럼 여러 류에서 조금씩 떼어 만든 코드는
    이전 코드 수백~천여 개와 이어져, 그대로 묶으면 85류 대부분이 그룹 하나(1,167개)로 뭉칩니다. 대응 행은 그대로 적재합니다."""
    fanout = Counter(n for n, _, _ in pairs)
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for n, o, _ in pairs:
        if fanout[n] > HUB_FANOUT:
            continue
        a, b = find(("new", n)), find(("old", o))
        if a != b:
            parent[max(a, b)] = min(a, b)  # ('new', …)가 ('old', …)보다 작아 대표는 항상 새 판 코드 중 가장 작은 값
    return {n: find(("new", n))[1] for n, _, _ in pairs}


def to_rows(pairs, new, old, source_edition):
    groups = group_codes(pairs)
    return [{"new_ver": new, "new_code": n, "old_ver": old, "old_code": o, "relation": rel,
             "group_code": groups[n], "source_edition": source_edition} for n, o, rel in pairs]


def load(engine, rows, new, old):
    table = HsCodeCorrelation.__tablename__
    if not inspect(engine).has_table(table):
        raise LookupError(f"{table} 테이블이 없습니다. db/mysql/init/02_hs_code_correlation.sql로 먼저 만드세요.")
    corr = CRUD(HsCodeCorrelation)
    with session_scope(engine) as s:  # 지우고 넣기를 한 트랜잭션에서: 실패하면 기존 대응표가 그대로 남음
        s.execute(delete(HsCodeCorrelation).where(HsCodeCorrelation.new_ver == new, HsCodeCorrelation.old_ver == old))
        corr.upsert_many(s, rows)
        return corr.count(s, new_ver=new, old_ver=old)


def run(new, old, xlsx=None, db_url=None, refresh=False, log_dir=LOG_DIR, save_dir=SAVE_DIR, track=True):
    """성공 0, 실패 1을 돌려줍니다. track=False면 batch_run 기록을 생략합니다(테스트용)."""
    log_path = setup_log(log_dir)
    log.info(f"HS 대응표 적재 시작 | {new} ↔ {old}")
    if not track:
        return _run(new, old, xlsx, db_url, refresh, save_dir, log_path, None)
    batch_engine = make_engine(db_url) if db_url else None
    try:
        with track_batch(SOURCE, JOB_NAME, HsCodeCorrelation.__tablename__, *hb.hs_period(new),
                         engine=batch_engine, logger=log) as batch:
            rc = _run(new, old, xlsx, db_url, refresh, save_dir, log_path, batch)
            if rc != 0:
                batch.fail()
    finally:
        if batch_engine is not None:
            batch_engine.dispose()
    return rc


def _run(new, old, xlsx, db_url, refresh, save_dir, log_path, batch):
    engine = None
    try:
        if xlsx:
            name, pairs = Path(xlsx).name, parse(Path(xlsx), new, old)
        else:
            name, (pairs, downloaded) = f"{new}to{old}.xlsx", fetch_pairs(new, old, save_dir, refresh)
            if batch is not None and downloaded:
                batch.request_count += 1
        if batch is not None:
            batch.received_count = len(pairs)
        rows = to_rows(pairs, new, old, f"UNSD {new}to{old} Correlations ({name})")
        counts = pd.Series([r["relation"] for r in rows]).value_counts().to_dict()
        log.info(f"대응 {len(rows):,}쌍 {counts} | 비교 그룹 {len({r['group_code'] for r in rows}):,}개")
        engine = make_engine(db_url)
        total = load(engine, rows, new, old)
    except (FileNotFoundError, ValueError, LookupError, requests.RequestException) as e:
        log.error(f"적재 실패: {e} | 로그: {log_path}")
        return 1
    except Exception:
        log.exception(f"적재 실패: 예상하지 못한 오류 (DB 작업은 롤백) | 로그: {log_path}")
        return 1
    finally:
        if engine is not None:
            engine.dispose()
    log.info(f"적재 완료: {new}↔{old} {total:,}행 | 로그: {log_path}")
    return 0


def main():
    parser = argparse.ArgumentParser(description="UNSD HS 판 간 대응표를 hs_code_correlation 테이블에 적재")
    parser.add_argument("--new", default="HS2022", help="새 HS 판 (기본 HS2022)")
    parser.add_argument("--old", default="HS2017", help="이전 HS 판 (기본 HS2017)")
    parser.add_argument("--xlsx", type=Path, help="이미 받은 대응표 xlsx (주지 않으면 UNSD에서 받음)")
    parser.add_argument("--refresh", action="store_true", help="받아 둔 파일이 있어도 다시 받기")
    parser.add_argument("--db-url", help="SQLAlchemy DB URL (기본: .env의 MYSQL_* 값)")
    args = parser.parse_args()
    raise SystemExit(run(args.new, args.old, args.xlsx, args.db_url, args.refresh))


if __name__ == "__main__":
    main()
