"""검토한 전처리 CSV를 미리 생성된 MySQL 데이터베이스에 적재합니다."""
import argparse
import csv
import json
import os
import re
import sys
from pathlib import Path
from dotenv import dotenv_values
# 직접 실행과 패키지 실행에서 모두 같은 전처리 모듈을 참조합니다.
if __package__:
    from .news_pipeline import BASE, ENV_PATH, FIELDS, MAP, hash_key
else:
    from news_pipeline import BASE, ENV_PATH, FIELDS, MAP, hash_key


# 새 값이 비어 있으면 기존 기사의 값을 사용하여 병합 과정에서도 정보를 유지합니다.
def fill_missing_fields(row, saved):
    for field in ["source", *MAP]:
        if not row[field].strip() and saved.get(field) is not None and str(saved[field]).strip():
            row[field] = saved[field]


# CSV를 검증한 뒤 MySQL에 적재하고 중복 기사와 원본 JSON을 병합합니다.
def load(csv_path, env_path):
    import mysql.connector
    config = {**dotenv_values(env_path), **os.environ}
    required = ["MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_DATABASE"]
    missing = [name for name in required if not config.get(name)]
    if missing:
        raise ValueError("Missing configuration: " + ", ".join(missing))
    # 데이터베이스 연결과 테이블 생성 전에 입력 CSV를 검증합니다.
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        missing_fields = set(FIELDS) - set(reader.fieldnames or [])
        if missing_fields:
            raise ValueError("CSV is missing: " + ", ".join(sorted(missing_fields)))
        rows = list(reader)
    if not rows:
        raise ValueError("Clean CSV has no articles")
    for number, row in enumerate(rows, 2):
        if None in row or any(v is None for v in row.values()):
            raise ValueError(f"Malformed CSV row {number}")
        keys = json.loads(row["identity_keys"])
        if (not isinstance(keys, list) or not keys or row["news_id"] not in keys
                or any(not isinstance(k, str) or not re.fullmatch(r"[0-9a-f]{64}", k) for k in keys)):
            raise ValueError(f"Invalid identity in CSV row {number}")
        if not isinstance(json.loads(row["raw_json"]), list):
            raise ValueError(f"Invalid raw_json in CSV row {number}")
        if not row["title"] and not row["content"]:
            raise ValueError(f"Empty title and body in CSV row {number}")

    conn = mysql.connector.connect(
        host=config.get("MYSQL_HOST", "localhost"),
        port=int(config.get("MYSQL_PORT", "3306")),
        user=config["MYSQL_USER"], password=config["MYSQL_PASSWORD"],
        database=config["MYSQL_DATABASE"], charset="utf8mb4",
        autocommit=False, connection_timeout=15)
    cursor = conn.cursor()
    locked = False
    lock_name = "kotra_news:" + hash_key(config["MYSQL_DATABASE"])[:40]
    try:
        cursor.execute("SELECT GET_LOCK(%s, 30)", (lock_name,))
        locked = cursor.fetchone()[0] == 1
        if not locked:
            raise RuntimeError("Another news loader is running")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS kotra_news (
                news_id CHAR(64) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
                source VARCHAR(100) NOT NULL,
                original_id TEXT, title TEXT, content LONGTEXT,
                published_at TEXT, country TEXT, url TEXT, summary TEXT,
                keywords TEXT, author TEXT, region TEXT, industry TEXT,
                copyright_type TEXT, raw_json JSON NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS kotra_news_identity (
                identity_key CHAR(64) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
                news_id CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
                FOREIGN KEY (news_id) REFERENCES kotra_news(news_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        # 테이블 생성은 자동 커밋되며, 이후 기사 변경은 하나의 트랜잭션으로 처리합니다.
        conn.commit()
        conn.start_transaction()
        columns = ["news_id", "source", *MAP, "raw_json"]
        inserted = updated = merged = 0
        for row in rows:
            keys = sorted(set(json.loads(row["identity_keys"])))
            placeholders = ",".join(["%s"] * len(keys))
            cursor.execute(
                f"SELECT DISTINCT news_id FROM kotra_news_identity WHERE identity_key IN ({placeholders})",
                tuple(keys))
            matches = [item[0] for item in cursor.fetchall()]
            target = min(matches) if matches else row["news_id"]
            raw_records = json.loads(row["raw_json"])
            # 대표 기사부터 빈 필드를 보충하고 나머지 중복 기사의 정보도 유지합니다.
            for existing_id in sorted(matches):
                cursor.execute("SELECT " + ", ".join(columns[1:]) +
                               " FROM kotra_news WHERE news_id=%s", (existing_id,))
                saved = cursor.fetchone()
                if saved:
                    saved = dict(zip(columns[1:], saved))
                    fill_missing_fields(row, saved)
                    raw_records.extend(json.loads(saved["raw_json"]))
            # 이전 원본을 보존하면서 반복 적재로 동일한 원본이 늘어나지 않게 합니다.
            originals = {json.dumps(item, ensure_ascii=False, sort_keys=True): item
                         for item in raw_records}
            row["raw_json"] = json.dumps(list(originals.values()), ensure_ascii=False)
            for old in matches:
                if old != target:
                    cursor.execute("UPDATE kotra_news_identity SET news_id=%s WHERE news_id=%s", (target, old))
                    cursor.execute("DELETE FROM kotra_news WHERE news_id=%s", (old,))
                    merged += 1
            values = (target,) + tuple(row[field] if row[field].strip() else None for field in columns[1:])
            # 빈 새 값으로 기존 값을 지우지 않으며 원본 JSON은 병합된 값으로 갱신합니다.
            assignments = ", ".join(
                f"{field}=%s" if field == "raw_json" else
                f"{field}=COALESCE(NULLIF(%s, ''), {field})"
                for field in columns[1:])
            cursor.execute(
                "INSERT INTO kotra_news (" + ", ".join(columns) + ") VALUES (" +
                ", ".join(["%s"] * len(columns)) + ") ON DUPLICATE KEY UPDATE " + assignments,
                values + values[1:])
            for key in keys:
                cursor.execute(
                    "INSERT INTO kotra_news_identity (identity_key, news_id) VALUES (%s,%s) "
                    "ON DUPLICATE KEY UPDATE news_id=%s", (key, target, target))
            if matches:
                updated += 1
            else:
                inserted += 1
        cursor.execute("SELECT COUNT(*) FROM kotra_news")
        total = cursor.fetchone()[0]
        conn.commit()
        print(f"Inserted={inserted}, updated={updated}, merged={merged}, total={total}")
    except Exception:
        conn.rollback()
        raise
    finally:
        try:
            if locked:
                cursor.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
                cursor.fetchone()
        finally:
            cursor.close()
            conn.close()


# 검토한 CSV 경로와 .env 경로를 받아 MySQL 적재를 실행합니다.
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, required=True,
                        help="Path of the cleaned CSV you have reviewed")
    parser.add_argument("--env", type=Path, default=ENV_PATH)
    args = parser.parse_args()
    load(args.csv, args.env)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        if isinstance(exc, ValueError):
            print(f"Error: {exc}", file=sys.stderr)
        elif isinstance(exc, ModuleNotFoundError):
            print("Install news_requirements.txt first", file=sys.stderr)
        else:
            print(f"MySQL load failed: {type(exc).__name__}; article transaction rolled back", file=sys.stderr)
        sys.exit(1)
