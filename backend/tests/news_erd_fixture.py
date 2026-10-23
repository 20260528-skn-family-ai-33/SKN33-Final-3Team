"""2026-10-22 ERD의 기사 신규 수신 제약을 SQLite로 재현합니다 (MySQL 실행 검증은 별도)."""
from sqlalchemy import Column, DateTime, JSON, String


def extend_metadata(metadata):
    article = metadata.tables["article"]
    article.append_column(Column("visibility_status", String(10), nullable=False, server_default="VISIBLE"))
    article.append_column(Column("first_ingested_at", DateTime))
    article.append_column(Column("admin_meta_json", JSON, nullable=False))
    metadata.tables["batch_run"].append_column(Column("detail_json", JSON, nullable=False))


def install_trigger(engine):
    # 부속 01_create_full_schema.sql의 trg_article_insert_v3 + JSON CHECK 핵심 조건.
    with engine.begin() as conn:
        conn.exec_driver_sql("""CREATE TRIGGER trg_article_insert_v3 BEFORE INSERT ON article
        WHEN NEW.first_ingested_at IS NULL
          OR json_type(NEW.admin_meta_json, '$.classification_input') IS NOT 'object'
          OR (length(json_extract(NEW.admin_meta_json, '$.classification_input.snapshot_ref')) > 0) IS NOT TRUE
          OR length(json_extract(NEW.admin_meta_json, '$.classification_input.sha256')) IS NOT 64
          OR json_extract(NEW.admin_meta_json, '$.classification_input.sha256') GLOB '*[^0-9a-f]*'
        BEGIN SELECT RAISE(ABORT, 'New articles require first_ingested_at and a classification snapshot'); END""")
