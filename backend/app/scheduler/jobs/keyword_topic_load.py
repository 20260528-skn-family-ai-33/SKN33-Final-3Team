"""키워드 분류 결과를 MySQL keyword_category · keyword_topic에 적재합니다 (상위어를 키워드로 넣는 방식).

실행 (저장소 루트에서): python -m backend.app.scheduler.jobs.keyword_topic_load [--clean 경로] [--counts db|csv] [--dry-run]
- 입력: services/news_keywords의 분류 틀(category_rules.TAX)·분류표·동의어 사전·상위어 사전 + data/news_keywords/news_keywords_clean.csv
- keyword_category: 분류 틀 23쌍 (대분류 > 중분류). 이미 있으면 번호 유지.
- keyword_topic 상태 (팀 결정 2026-10-21)
  · SYNONYM      동의어 표기(synonyms.csv)와 하위어(hypernyms.csv). canonical_text = 최종 키워드 (K뷰티 → 뷰티, 전기차보조금 → 전기차)
  · CLASSIFIED   최종 주제어(kw_topic) 중 분야가 정해진 말 — 코드 안 규칙(category_rules.classify)과 같은 판정
                 규칙(포함)·LLM 판단 추정도 확정 (팀 결정 2026-10-22, 관리자가 DB에서 고칠 수 있음)
  · UNCLASSIFIED 어떤 규칙에도 안 걸리고 기사 PENDING_MIN(3)건 미만인 말
  · PENDING      어떤 규칙에도 안 걸리고 기사 PENDING_MIN건 이상인 말 (관리자 분류 대상)
- 기사 수(PENDING 기준)는 DB article_tag의 키워드별 고유 기사 수(누적)로 셉니다 (--counts db, 기본).
  키워드 결과 CSV에만 있는 키워드(news_load 전에 실행했거나 사전이 바뀐 경우)도 빠지지 않게 CSV 키워드를 더하고,
  둘 다 있으면 큰 값을 씁니다. article_tag가 비어 있으면 CSV로만 셉니다 (--counts csv와 같음).
- 실행 순서: news_load(기사·태그) → keyword_topic_load. 사전(동의어·상위어·분류표)이 없으면 적재하지 않고 멈춥니다.
- 관리자가 DB에서 바꾼 행(synced_at이 NULL이거나 updated_at보다 이름)은 덮어쓰지 않고 건너뜁니다 (csv에 먼저 반영할 것).
- 이번 결과에 없는 키워드(사전·데이터가 바뀌어 사라진 말)는 지웁니다. 단, 관리자가 DB에서 바꾼 행은 남기고 경고합니다.
- 적재한 행은 updated_at = synced_at = 실행 시각으로 맞춥니다. 전체를 한 트랜잭션으로 넣고, 실패하면 모두 롤백합니다.
- DB 적재를 먼저 하고 결과 파일을 씁니다. 결과 파일이 엑셀에 열려 있어 저장하지 못해도 DB 적재는 그대로이고 경고만 남깁니다.
- 결과 표: data/news_keywords/keyword_topic.csv (적재한 값과 같은 내용), 미분류 확인 파일: review/keyword_pending_review.xlsx
  (시트: 개수 · PENDING · UNCLASSIFIED)
- batch_run에는 기록하지 않습니다 (target_table 목록에 키워드 테이블이 없음, news_embed와 같음).
"""
import argparse
import logging
from collections import Counter
from datetime import datetime
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from sqlalchemy import delete, inspect, select
from sqlalchemy.dialects import mysql, sqlite

from backend.app.db.session import make_engine, session_scope
from backend.app.models.keyword import KeywordCategory, KeywordTopic
from backend.app.scheduler.jobs.news_load import keyword_article_counts
from backend.app.services.news_keywords import keyword_clean as kc

ROOT = Path(__file__).resolve().parents[4]
DATA = ROOT / "data" / "news_keywords"
DEFAULT_CLEAN = DATA / "news_keywords_clean.csv"
LOG_DIR = DATA / "logs"
PENDING_MIN = 3
log = logging.getLogger("keyword_topic_load")


class TableMissingError(RuntimeError):
    pass


def setup_log(log_dir):
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"keyword_topic_load_{datetime.now():%Y%m%d_%H%M%S}.log"
    log.setLevel(logging.INFO)
    for h in log.handlers:
        h.close()
    log.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    for h in (logging.StreamHandler(), logging.FileHandler(path, encoding="utf-8")):
        h.setFormatter(fmt)
        log.addHandler(h)
    return path


def topic_counts(clean_path):
    """최종 주제어(kw_topic) → 고유 기사 수."""
    df = pd.read_csv(clean_path, dtype=str, keep_default_na=False, usecols=["bbstxSn", "kw_topic"])
    arts = {}
    for sn, v in zip(df["bbstxSn"], df["kw_topic"]):
        for w in set(x for x in v.split("|") if x):
            arts.setdefault(w, set()).add(sn)
    return Counter({w: len(s) for w, s in arts.items()})


def build_rows(counts, cat, syn, hyp, pending_min=PENDING_MIN):
    """keyword_topic 행 목록 [{keyword_text, canonical_text, field("대>중"|None), status, 기사수, 이유}]."""
    final = lambda w: hyp.get(syn.get(w, w), syn.get(w, w))
    rows = []
    for form in sorted(set(syn) | set(hyp)):
        rows.append({"keyword_text": form, "canonical_text": final(form), "field": None, "status": "SYNONYM",
                     "기사수": 0, "이유": "동의어" if form in syn else "하위어 → 상위어"})
    replaced = {r["keyword_text"] for r in rows}
    for w, n in counts.items():
        if w in replaced:
            continue
        field, _, _ = cat.judge(w)
        if field:
            status, why = "CLASSIFIED", "분류표·규칙"
        elif n >= pending_min:
            status, why = "PENDING", f"규칙 없음, 기사 {pending_min}건 이상"
        else:
            status, why = "UNCLASSIFIED", f"규칙 없음, 기사 {pending_min}건 미만"
        rows.append({"keyword_text": w, "canonical_text": None, "field": field, "status": status, "기사수": n, "이유": why})
    too_long = [r["keyword_text"] for r in rows if len(r["keyword_text"]) > 120]
    if too_long:
        raise ValueError(f"120자를 넘는 키워드: {too_long[:5]}")
    return rows


def check_tables(engine):
    names = set(inspect(engine).get_table_names())
    missing = [t for t in ("keyword_category", "keyword_topic") if t not in names]
    if missing:
        raise TableMissingError(f"{', '.join(missing)} 테이블이 없습니다. 테이블을 먼저 만든 뒤 다시 실행하세요.")


def _upsert(session, table, rows, keys, size=1000):
    dialect = session.get_bind().dialect.name
    for i in range(0, len(rows), size):
        chunk = rows[i:i + size]
        cols = [c for c in chunk[0] if c not in keys]
        if dialect == "mysql":
            stmt = mysql.insert(table).values(chunk)
            stmt = stmt.on_duplicate_key_update({c: stmt.inserted[c] for c in cols})
        else:
            stmt = sqlite.insert(table).values(chunk)
            stmt = stmt.on_conflict_do_update(index_elements=keys, set_={c: stmt.excluded[c] for c in cols})
        session.execute(stmt)


def load(engine, rows, now=None):
    """적재 결과 {"categories", "written", "deleted", "skipped_db_edits", "kept_db_edits"}.
    관리자가 DB에서 바꾼 행은 건너뛰고, 이번 결과에 없는 행은 지움 (관리자가 바꾼 행은 남김)."""
    check_tables(engine)
    now = (now or datetime.now()).replace(microsecond=0)
    with session_scope(engine) as s:
        _upsert(s, KeywordCategory.__table__,
                [{"major_name": big, "minor_name": mid, "updated_at": now, "synced_at": now} for big, mid, _ in kc.cr.TAX],
                ["major_name", "minor_name"])
        ids = {f"{a}>{b}": i for i, a, b in s.execute(select(KeywordCategory.category_id, KeywordCategory.major_name,
                                                                KeywordCategory.minor_name))}
        edited = {k for k, u, sy in s.execute(select(KeywordTopic.keyword_text, KeywordTopic.updated_at,
                                                     KeywordTopic.synced_at)) if sy is None or sy < u}
        unknown = sorted({r["field"] for r in rows if r["field"] and r["field"] not in ids})
        if unknown:
            raise ValueError(f"분류 틀에 없는 분야: {unknown}")
        out = [{"keyword_text": r["keyword_text"], "canonical_text": r["canonical_text"],
                "category_id": ids[r["field"]] if r["field"] else None, "status": r["status"],
                "updated_at": now, "synced_at": now} for r in rows if r["keyword_text"] not in edited]
        _upsert(s, KeywordTopic.__table__, out, ["keyword_text"])
        current = {r["keyword_text"] for r in rows}
        existing = set(s.scalars(select(KeywordTopic.keyword_text)))
        gone = sorted(existing - current - edited)
        for i in range(0, len(gone), 1000):
            s.execute(delete(KeywordTopic).where(KeywordTopic.keyword_text.in_(gone[i:i + 1000])))
    return {"categories": len(ids), "written": len(out), "deleted": len(gone),
            "skipped_db_edits": sorted(edited & current), "kept_db_edits": sorted(edited - current)}


def export(rows, data_dir=DATA, unclassified_csv=None):
    """적재 값 csv + 미분류·PENDING 검토 엑셀 (최근 기사 예시는 keyword_clean의 unclassified_topics.csv에서)."""
    df = pd.DataFrame(rows).rename(columns={"field": "분야"})
    df[["대분류", "중분류"]] = df["분야"].fillna("").str.split(">", n=1, expand=True).reindex(columns=[0, 1]).fillna("")
    df = df[["keyword_text", "status", "canonical_text", "대분류", "중분류", "기사수", "이유"]]
    df.sort_values(["status", "기사수", "keyword_text"], ascending=[True, False, True]).to_csv(
        data_dir / "keyword_topic.csv", index=False, encoding="utf-8-sig")
    un = Path(unclassified_csv or data_dir / "unclassified_topics.csv")
    recent = (dict(zip(*pd.read_csv(un, dtype=str, keep_default_na=False, usecols=["주제어", "최근 기사"]).T.values))
              if un.exists() else {})
    sheets = {}
    for status in ("PENDING", "UNCLASSIFIED"):
        part = df[df["status"] == status].sort_values(["기사수", "keyword_text"], ascending=[False, True])
        sheets[status] = pd.DataFrame({"키워드": part["keyword_text"], "기사수": part["기사수"], "이유": part["이유"],
                                       "최근 기사": part["keyword_text"].map(recent).fillna(""),
                                       "관리자 분류 (대분류>중분류)": ""})
    count = df["status"].value_counts().rename_axis("status").reset_index(name="개수")
    from backend.app.services.news_keywords.build import xlsx_util as xu   # noqa: E402 (엑셀 서식 공용)
    wb = Workbook()
    xu.write_sheet(wb.active, count, [16, 10]); wb.active.title = "개수"
    for status, d in sheets.items():
        xu.write_sheet(wb.create_sheet(status), d, [22, 8, 26, 90, 26], wrap_cols=("최근 기사",))
    path = data_dir / "review" / "keyword_pending_review.xlsx"
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def counts_from(source, engine, clean_path):
    """키워드별 고유 기사 수. source="db"면 article_tag(누적) + 키워드 결과 CSV의 키워드 (둘 다 있으면 큰 값).
    DB만 보면 news_load 전에 실행했을 때 새 키워드가 빠지고 그 행이 지워지므로 CSV 키워드를 더합니다.
    article_tag가 비어 있으면 CSV로만 셈."""
    if source == "db":
        counts = keyword_article_counts(engine)
        if counts:
            csv_counts = topic_counts(clean_path) if Path(clean_path).exists() else Counter()
            only_csv = set(csv_counts) - set(counts)
            if only_csv:
                log.warning(f"키워드 결과 CSV에만 있는 키워드 {len(only_csv):,}개는 CSV 기사 수로 넣습니다 "
                            f"(news_load로 기사를 먼저 적재하면 누적 기준이 됨)")
            log.info(f"기사 수: DB article_tag 기준 (키워드 {len(counts):,}개) + CSV에만 있는 키워드 {len(only_csv):,}개")
            return Counter({k: max(counts.get(k, 0), csv_counts.get(k, 0)) for k in set(counts) | set(csv_counts)})
        log.warning("article_tag에 키워드가 없어 키워드 결과 CSV로 기사 수를 셉니다 (CSV가 전체 기사여야 누적과 같음)")
    log.info(f"기사 수: {Path(clean_path).name} 기준")
    return topic_counts(clean_path)


def dictionaries():
    """(분류표, 동의어 사전, 상위어 사전). 하나라도 없으면 kc.DictionaryMissing으로 멈춤 —
    빈 사전으로 적재하면 DB의 동의어(SYNONYM) 행이 '사라진 키워드'로 지워짐."""
    return (kc.load_categories(kc.require_dict(kc.CATEGORIES_PATH, "분류표", "categories")),
            kc.load_synonyms(kc.require_dict(kc.SYNONYMS_PATH, "동의어", "synonyms")),
            kc.load_hypernyms(kc.require_dict(kc.HYPERNYMS_PATH, "상위어", "hypernyms")))


def run(clean_path=DEFAULT_CLEAN, db_url=None, dry_run=False, log_dir=LOG_DIR, counts="db"):
    log_path = setup_log(log_dir)
    engine = None
    try:
        cat, syn, hyp = dictionaries()
        engine = make_engine(db_url) if (counts == "db" or not dry_run) else None
        rows = build_rows(counts_from(counts, engine, clean_path), cat, syn, hyp)
        status = Counter(r["status"] for r in rows)
        log.info("상태별: " + " · ".join(f"{k} {v:,}" for k, v in sorted(status.items())) + f" | 합계 {len(rows):,}")
        out = None if dry_run else load(engine, rows)
    except kc.DictionaryMissing as e:
        log.error(f"적재 안 함: {e} | 로그: {log_path}")
        return 1
    except Exception:
        log.exception(f"적재 실패 (롤백되어 반영되지 않음) | 로그: {log_path}")
        return 1
    finally:
        if engine is not None:
            engine.dispose()
    try:   # 결과 파일은 DB 적재 뒤에: 엑셀이 열려 있어 저장하지 못해도 DB 적재는 그대로
        log.info(f"검토 파일: {export(rows)}")
    except OSError as e:
        log.warning(f"결과 파일을 저장하지 못했습니다 (엑셀에 열려 있으면 닫고 --dry-run으로 다시 만드세요): {e}")
    if dry_run:
        log.info(f"미리 보기(--dry-run): DB에 넣지 않음 | 로그: {log_path}")
        return 0
    if out["kept_db_edits"]:
        log.warning(f"이번 결과에 없지만 관리자가 DB에서 바꾼 행 {len(out['kept_db_edits']):,}개는 남김: "
                    f"{', '.join(out['kept_db_edits'][:20])}")
    if out["skipped_db_edits"]:
        log.warning(f"DB에서 바꾼 뒤 csv에 반영하지 않은 행 {len(out['skipped_db_edits']):,}개는 건너뜀: "
                    f"{', '.join(out['skipped_db_edits'][:20])}")
    log.info(f"적재 완료: 분류 틀 {out['categories']}쌍 | 키워드 {out['written']:,}행 · 지운 행 {out['deleted']:,} | 로그: {log_path}")
    return 0


def main():
    parser = argparse.ArgumentParser(description="키워드 분류 결과를 keyword_category · keyword_topic에 적재")
    parser.add_argument("--clean", type=Path, default=DEFAULT_CLEAN, help="키워드 결과 CSV (기본: news_keywords_clean.csv)")
    parser.add_argument("--db-url", help="SQLAlchemy DB URL (기본: .env의 MYSQL_* 값)")
    parser.add_argument("--dry-run", action="store_true", help="DB에 넣지 않고 상태별 개수와 검토 파일만")
    parser.add_argument("--counts", choices=("db", "csv"), default="db",
                        help="PENDING 기준 기사 수: db = article_tag 누적(기본), csv = 키워드 결과 CSV")
    args = parser.parse_args()
    raise SystemExit(run(args.clean, args.db_url, args.dry_run, counts=args.counts))


if __name__ == "__main__":
    main()
