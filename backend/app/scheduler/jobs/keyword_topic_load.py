"""키워드 분류 결과를 MySQL keyword_category · keyword_topic에 적재합니다 (상위어를 키워드로 넣는 방식).

실행 (저장소 루트에서): python -m backend.app.scheduler.jobs.keyword_topic_load [--clean 경로] [--dry-run]
- 입력: services/news_keywords의 분류 틀(category_rules.TAX)·분류표·동의어 사전·상위어 사전 + data/news_keywords/news_keywords_clean.csv
- keyword_category: 분류 틀 23쌍 (대분류 > 중분류). 이미 있으면 번호 유지.
- keyword_topic 상태 (팀 결정 2026-10-21)
  · SYNONYM      동의어 표기(synonyms.csv)와 하위어(hypernyms.csv). canonical_text = 최종 키워드 (K뷰티 → 뷰티, 전기차보조금 → 전기차)
  · CLASSIFIED   최종 주제어(kw_topic) 중 분야가 정해진 말 — 분류표 · 규칙(일치) · 규칙 수정 · 팀 검수
                 + 규칙(포함) 추정도 확정 (팀 검수 정확도 약 89%, 관리자가 DB에서 고칠 수 있음)
  · 팀이 분류표에서 미분류로 정한 말도 규칙을 적용 (팀 결정 2026-10-21): 규칙 결과(LLM 판단·규칙 수정 등)가 있으면 CLASSIFIED,
    없으면 아래 기사 수 기준 (keyword_clean의 기사 분야 kw_category는 팀 미분류를 그대로 따름)
  · UNCLASSIFIED 어떤 규칙에도 안 걸리고 기사 PENDING_MIN(3)건 미만인 말
  · PENDING      어떤 규칙에도 안 걸리고 기사 PENDING_MIN건 이상인 말 (관리자 분류 대상)
- 관리자가 DB에서 바꾼 행(synced_at이 NULL이거나 updated_at보다 이름)은 덮어쓰지 않고 건너뜁니다 (csv에 먼저 반영할 것).
- 적재한 행은 updated_at = synced_at = 실행 시각으로 맞춥니다. 전체를 한 트랜잭션으로 넣고, 실패하면 모두 롤백합니다.
- 결과 표: data/news_keywords/keyword_topic.csv (적재한 값과 같은 내용), 미분류 검토 파일: review/키워드_미분류_PENDING.xlsx
  (시트: 개수 · 팀 미분류 재분류 · PENDING · UNCLASSIFIED)
- batch_run에는 기록하지 않습니다 (target_table 목록에 키워드 테이블이 없음, news_embed와 같음).
"""
import argparse
import logging
from collections import Counter
from datetime import datetime
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from sqlalchemy import inspect, select
from sqlalchemy.dialects import mysql, sqlite

from backend.app.db.session import make_engine, session_scope
from backend.app.models.keyword import KeywordCategory, KeywordTopic
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


def rule_field(word, lists=None):
    """팀 판정을 빼고 규칙만으로 본 분야 ("대>중", 방법) 또는 (None, "")."""
    fixes, llm = lists or kc.cr.load_lists()
    big, mid, how, _ = kc.cr.assign(word, fixes, llm, None)
    return (f"{big}>{mid}", how) if big != "미분류" else (None, "")


def build_rows(counts, cat, syn, hyp, pending_min=PENDING_MIN, rule=None):
    """keyword_topic 행 목록 [{keyword_text, canonical_text, field("대>중"|None), status, 기사수, 이유}].
    rule(word) → (분야|None, 방법): 팀이 미분류로 정한 말에 쓸 규칙 결과 (기본: rule_field)."""
    if rule is None:
        lists = kc.cr.load_lists()
        rule = lambda w: rule_field(w, lists)
    final = lambda w: hyp.get(syn.get(w, w), syn.get(w, w))
    rows = []
    for form in sorted(set(syn) | set(hyp)):
        rows.append({"keyword_text": form, "canonical_text": final(form), "field": None, "status": "SYNONYM",
                     "기사수": 0, "이유": "동의어" if form in syn else "하위어 → 상위어"})
    replaced = {r["keyword_text"] for r in rows}
    for w, n in counts.items():
        if w in replaced:
            continue
        field, why, cand = cat.judge(w)
        team_field, team_how = rule(w) if not field and not cand and w in cat.listed else (None, "")
        if field:
            status, field, why = "CLASSIFIED", field, "분류표·규칙"
        elif cand:
            status, field, why = "CLASSIFIED", cand, "규칙(포함) 추정 확정"
        elif team_field:
            status, field, why = "CLASSIFIED", team_field, f"팀 미분류 → 규칙 적용 ({team_how})"
        elif n >= pending_min:
            status, why = "PENDING", f"규칙 없음, 기사 {pending_min}건 이상"
        else:
            status, why = "UNCLASSIFIED", f"규칙 없음, 기사 {pending_min}건 미만"
        if w in cat.listed and not field and not cand and not team_field:
            why = "팀 미분류 → " + why
        rows.append({"keyword_text": w, "canonical_text": None, "field": field if status == "CLASSIFIED" else None,
                     "status": status, "기사수": n, "이유": why})
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
    """적재 결과 {"categories", "written", "skipped_db_edits"}. 관리자가 DB에서 바꾼 행은 건너뜀."""
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
    return {"categories": len(ids), "written": len(out), "skipped_db_edits": sorted(edited & {r["keyword_text"] for r in rows})}


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
    team = df[df["이유"].str.startswith("팀 미분류")].sort_values(["status", "기사수"], ascending=[True, False])
    team = pd.DataFrame({"키워드": team["keyword_text"], "바뀐 상태": team["status"], "대분류": team["대분류"],
                         "중분류": team["중분류"], "기사수": team["기사수"], "이유": team["이유"],
                         "최근 기사": team["keyword_text"].map(recent).fillna("")})
    from backend.app.services.news_keywords.build import review_files as rf   # noqa: E402 (엑셀 서식 공용)
    wb = Workbook()
    rf.write_sheet(wb.active, count, [16, 10]); wb.active.title = "개수"
    rf.write_sheet(wb.create_sheet("팀 미분류 재분류"), team, [16, 12, 16, 26, 8, 34, 90], wrap_cols=("최근 기사",))
    for status, d in sheets.items():
        rf.write_sheet(wb.create_sheet(status), d, [22, 8, 26, 90, 26], wrap_cols=("최근 기사",))
    path = data_dir / "review" / "키워드_미분류_PENDING.xlsx"
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def run(clean_path=DEFAULT_CLEAN, db_url=None, dry_run=False, log_dir=LOG_DIR):
    log_path = setup_log(log_dir)
    try:
        rows = build_rows(topic_counts(clean_path), kc.load_categories(), kc.load_synonyms(), kc.load_hypernyms())
        status = Counter(r["status"] for r in rows)
        log.info("상태별: " + " · ".join(f"{k} {v:,}" for k, v in sorted(status.items())) + f" | 합계 {len(rows):,}")
        log.info(f"검토 파일: {export(rows)}")
        if dry_run:
            log.info(f"미리 보기(--dry-run): DB에 넣지 않음 | 로그: {log_path}")
            return 0
        engine = make_engine(db_url)
        try:
            out = load(engine, rows)
        finally:
            engine.dispose()
    except Exception:
        log.exception(f"적재 실패 (롤백되어 반영되지 않음) | 로그: {log_path}")
        return 1
    if out["skipped_db_edits"]:
        log.warning(f"DB에서 바꾼 뒤 csv에 반영하지 않은 행 {len(out['skipped_db_edits']):,}개는 건너뜀: "
                    f"{', '.join(out['skipped_db_edits'][:20])}")
    log.info(f"적재 완료: 분류 틀 {out['categories']}쌍 | 키워드 {out['written']:,}행 | 로그: {log_path}")
    return 0


def main():
    parser = argparse.ArgumentParser(description="키워드 분류 결과를 keyword_category · keyword_topic에 적재")
    parser.add_argument("--clean", type=Path, default=DEFAULT_CLEAN, help="키워드 결과 CSV (기본: news_keywords_clean.csv)")
    parser.add_argument("--db-url", help="SQLAlchemy DB URL (기본: .env의 MYSQL_* 값)")
    parser.add_argument("--dry-run", action="store_true", help="DB에 넣지 않고 상태별 개수와 검토 파일만")
    args = parser.parse_args()
    raise SystemExit(run(args.clean, args.db_url, args.dry_run))


if __name__ == "__main__":
    main()
