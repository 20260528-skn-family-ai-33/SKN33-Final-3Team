"""구형/관리자 ERD 공통 기사 적재 메타데이터와 최초 수신 정책 스냅샷."""
import hashlib
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import MetaData, Table, inspect, select
from sqlalchemy.engine import Connection

ROOT = Path(__file__).resolve().parents[3]
SNAPSHOT_DIR = ROOT / "data" / "news" / "classification_snapshots"


def reflect(bind, name):
    return Table(name, MetaData(), autoload_with=bind, resolve_fks=False)


def now():
    return datetime.now(ZoneInfo("Asia/Seoul")).replace(tzinfo=None, microsecond=0)


def classification_snapshot(session):
    """적재 시점의 정책 코드·사전·DB 정책을 내용과 함께 보관합니다. 기사 원문은 포함하지 않습니다.

    외부 정제 파일을 과거에 생성한 정책을 소급 추정하지 않습니다. 분류 실행은 별도이며
    신규 기사의 PENDING 상태에 연결할 현재 정책 자료를 고정합니다.
    """
    policy_dir = ROOT / "backend" / "app" / "services" / "news_keywords"
    paths = sorted(policy_dir.glob("*.py")) + sorted((policy_dir / "build").glob("*.py"))
    paths += [ROOT / "data" / "news_keywords" / name for name in
              ("synonyms.csv", "hypernyms.csv", "topic_categories.csv")]
    files = {p.relative_to(ROOT).as_posix(): p.read_text(encoding="utf-8-sig") if p.exists() else None
             for p in paths}
    # ORM Session과 기사 적재기의 Core Connection 모두 기존 트랜잭션을 사용합니다.
    connection = session if isinstance(session, Connection) else session.connection()
    tables = {}
    for name in ("keyword_category", "keyword_topic"):
        if inspect(connection).has_table(name):
            table = reflect(connection, name)
            tables[name] = [dict(r) for r in session.execute(
                select(table).order_by(*table.primary_key.columns)).mappings()]
        else:
            tables[name] = None
    document = {"schema_version": 1, "captured_at_stage": "article_ingestion",
                "files": files, "tables": tables}
    payload = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                         default=lambda value: value.isoformat()).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    directory = Path(SNAPSHOT_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / (digest + ".json")
    # 완성한 파일을 원자적으로 공개하고, 같은 해시의 기존 스냅샷은 덮어쓰지 않습니다.
    with tempfile.NamedTemporaryFile(dir=directory, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        try:
            os.link(temporary, destination)
        except FileExistsError:
            if destination.read_bytes() != payload:
                raise ValueError("정책 스냅샷의 파일 내용과 SHA-256이 일치하지 않습니다") from None
    finally:
        temporary.unlink(missing_ok=True)
    resolved = destination.resolve()
    reference = resolved.relative_to(ROOT).as_posix() if resolved.is_relative_to(ROOT) else resolved.as_posix()
    return {"snapshot_ref": reference, "sha256": digest}


def admin_defaults(snapshot):
    return {"schema_version": 1, "visibility": {"reason": None, "changed_by": None, "changed_at": None},
            "country_tags_manual": False, "hs_tags_manual": False, "classification_input": snapshot,
            "classification_state": {"status": "PENDING", "revision": 0}}


def article_insert_rows(session, table, rows):
    """INSERT 후보에 신규 ERD 필수값 공급. UPDATE 컬럼에는 관리자 값을 포함하지 않습니다."""
    rows = [dict(row) for row in rows]
    snapshot = None
    if "admin_meta_json" in table.c and any("admin_meta_json" not in row for row in rows):
        snapshot = classification_snapshot(session)
    inserted_at = now()
    for row in rows:
        if "visibility_status" in table.c:
            row.setdefault("visibility_status", "VISIBLE")
        if "first_ingested_at" in table.c:
            row.setdefault("first_ingested_at", inserted_at)
        if "admin_meta_json" in table.c:
            row.setdefault("admin_meta_json", admin_defaults(snapshot))
    return rows


def article_metadata(session, table, ids):
    if "admin_meta_json" not in table.c:
        return {}
    ids = list(ids)
    result = {}
    for start in range(0, len(ids), 1000):
        result.update(session.execute(select(table.c.article_id, table.c.admin_meta_json)
                                      .where(table.c.article_id.in_(ids[start:start + 1000]))).all())
    return result
