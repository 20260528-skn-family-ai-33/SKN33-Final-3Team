"""Validated master CSV -> HS seed and summary-only MySQL UPDATE SQL (no DB connection)."""
import argparse
import csv
import hashlib
import importlib.util
import re
from collections import Counter
from pathlib import Path

spec = importlib.util.spec_from_file_location("hs_code_build", Path(__file__).with_name("hs_code_build.py"))
hb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hb)


def sql_text(value):
    """SQL literal independent of NO_BACKSLASH_ESCAPES; keep Korean readable when possible."""
    if value is None:
        return "NULL"
    if "\\" in value or any(ord(c) < 32 for c in value):
        return f"CONVERT(X'{value.encode('utf-8').hex()}' USING utf8mb4)"
    return "'" + value.replace("'", "''") + "'"


def read_master(path):
    with open(path, encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if not rows:
        raise ValueError("Empty master CSV")
    seen, summaries = set(), set()
    for row in rows:
        code, ver = row["hs_code"], row["hs_ver"]
        if not re.fullmatch(r"(?:[0-9]{2}|[0-9]{4}|[0-9]{6}|[0-9]{10})", code):
            raise ValueError(f"Invalid code: {code}")
        if not re.fullmatch(r"HS[0-9]{4}", ver) or row["depth"] != str(len(code)):
            raise ValueError(f"Invalid version/depth: {code}")
        if (code, ver) in seen:
            raise ValueError(f"Duplicate key: {code}/{ver}")
        seen.add((code, ver))
        if row["name_ko"].count(hb.SEP) != (len(code) == 6):
            raise ValueError(f"Invalid original delimiter: {code}")
        name, _, original = row["name_ko"].partition(hb.SEP)
        if not name.strip() or len(name) > hb.NAME_MAX or not row["source_edition"]:
            raise ValueError(f"Invalid name/source: {code}")
        if len(code) == 6:
            if not original.strip() or not hb.valid_summary(name, original) or (ver, name) in summaries:
                raise ValueError(f"Invalid or duplicate summary: {code}/{ver}: {name}")
            summaries.add((ver, name))
        row["summary"] = name
        row["original"] = original if len(code) == 6 else None
    if not summaries:
        raise ValueError("No six-digit summaries")
    return rows


def insert_batches(table, columns, values, suffix="", size=200):
    statements = []
    for start in range(0, len(values), size):
        batch = values[start:start + size]
        statements.append(f"INSERT INTO {table} ({', '.join(columns)}) VALUES\n" +
                          ",\n".join("(" + ", ".join(sql_text(v) for v in row) + ")" for row in batch) + suffix + ";\n")
    return "\n".join(statements)


def generate(csv_path, seed_path, update_path):
    rows = read_master(csv_path)
    six = [r for r in rows if len(r["hs_code"]) == 6]
    digest = hashlib.sha256(Path(csv_path).read_bytes()).hexdigest()
    header = ("-- Issue #46: HS2022 품목 요약 (공백 포함 20자 이하, 판별 요약 중복 없음).\n"
              f"-- 원본 CSV SHA256: {digest}\n"
              "-- 생성 도구: hs_code_seed.py. 대상 DB를 선택한 뒤 UTF-8 연결로 실행합니다.\n"
              "SET NAMES utf8mb4;\n\n")
    seed = header + (f"-- 총 {len(rows):,}행. 신규 행은 원문·출처와 함께 추가합니다.\n"
                     "-- 기존 행은 HS6 요약명만 갱신합니다. HS2·HS4·HS10 기존 이름과 원문·출처는 유지합니다.\n"
                     "START TRANSACTION;\n\n")
    seed += insert_batches("hs_code", ["hs_code", "hs_ver", "name_ko", "original_name_ko", "source_edition"],
                           [[r[k] for k in ("hs_code", "hs_ver", "summary", "original", "source_edition")] for r in rows],
                           "\nAS new ON DUPLICATE KEY UPDATE name_ko = IF(CHAR_LENGTH(hs_code) = 6, new.name_ko, name_ko)")
    seed += "\nCOMMIT;\n"
    update = header + f"""-- 기존 RDS 전용: HS6 {len(six):,}개의 name_ko만 변경합니다. INSERT/DELETE는 하지 않습니다.
-- original_name_ko, source_edition, HS2017 등 다른 판본과 나머지 단위는 변경하지 않습니다.
-- 모든 대상 행이 있어야 갱신합니다. missing_rows > 0이면 시드/판본을 확인하세요.
-- tmp_hs46_before에는 이 연결에서 복원할 수 있는 변경 전 이름을 보관합니다.
-- 재실행 시 임시 테이블은 새로 만듭니다. 실행 결과의 updated_rows는 이미 동일한 행을 제외합니다.
DROP TEMPORARY TABLE IF EXISTS tmp_hs46_names;
CREATE TEMPORARY TABLE tmp_hs46_names (
  hs_code VARCHAR(10) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  hs_ver VARCHAR(10) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  name_ko VARCHAR(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
  PRIMARY KEY (hs_code, hs_ver)
) ENGINE=InnoDB;

"""
    update += insert_batches("tmp_hs46_names", ["hs_code", "hs_ver", "name_ko"],
                             [[r[k] for k in ("hs_code", "hs_ver", "summary")] for r in six])
    update += """
SELECT COUNT(*) INTO @hs46_missing
FROM tmp_hs46_names AS n LEFT JOIN hs_code AS h
  ON h.hs_code = n.hs_code AND h.hs_ver = n.hs_ver
WHERE h.hs_code IS NULL;
SELECT COUNT(*) INTO @hs46_bad_names FROM tmp_hs46_names
WHERE CHAR_LENGTH(name_ko) NOT BETWEEN 1 AND 20;
SELECT COUNT(*) INTO @hs46_duplicates FROM (
  SELECT hs_ver, BINARY name_ko FROM tmp_hs46_names
  GROUP BY hs_ver, BINARY name_ko HAVING COUNT(*) > 1
) AS duplicates;
-- 먼저 매핑에 없는 같은 판의 행만 추립니다. 전체 이름을 서로 교차 비교하지 않습니다.
-- MySQL 임시 테이블을 한 쿼리에서 두 번 열지 않도록 판본 목록을 분리합니다.
DROP TEMPORARY TABLE IF EXISTS tmp_hs46_versions;
CREATE TEMPORARY TABLE tmp_hs46_versions AS
SELECT DISTINCT hs_ver FROM tmp_hs46_names;
DROP TEMPORARY TABLE IF EXISTS tmp_hs46_external_names;
CREATE TEMPORARY TABLE tmp_hs46_external_names AS
SELECT h.hs_ver, h.name_ko FROM hs_code AS h
JOIN tmp_hs46_versions AS v ON h.hs_ver = v.hs_ver
LEFT JOIN tmp_hs46_names AS own
  ON h.hs_code = own.hs_code AND h.hs_ver = own.hs_ver
WHERE h.depth = 6 AND own.hs_code IS NULL;
SELECT COUNT(*) INTO @hs46_external_duplicates
FROM tmp_hs46_external_names AS h JOIN tmp_hs46_names AS n
  ON h.hs_ver = n.hs_ver AND BINARY h.name_ko = BINARY n.name_ko;
SELECT @hs46_missing AS missing_rows, @hs46_bad_names AS invalid_names,
       @hs46_duplicates AS duplicate_names,
       @hs46_external_duplicates AS collisions_with_other_existing_rows;

DROP TEMPORARY TABLE IF EXISTS tmp_hs46_before;
CREATE TEMPORARY TABLE tmp_hs46_before AS
SELECT h.hs_code, h.hs_ver, h.name_ko
FROM hs_code AS h JOIN tmp_hs46_names AS n
  ON h.hs_code = n.hs_code AND h.hs_ver = n.hs_ver;

START TRANSACTION;
UPDATE hs_code AS h JOIN tmp_hs46_names AS n
  ON h.hs_code = n.hs_code AND h.hs_ver = n.hs_ver
SET h.name_ko = n.name_ko
WHERE h.depth = 6 AND @hs46_missing = 0
  AND @hs46_bad_names = 0 AND @hs46_duplicates = 0
  AND @hs46_external_duplicates = 0
  AND BINARY h.name_ko <> BINARY n.name_ko;
SELECT ROW_COUNT() AS updated_rows;
SELECT COUNT(*) AS remaining_mismatches
FROM hs_code AS h JOIN tmp_hs46_names AS n
  ON h.hs_code = n.hs_code AND h.hs_ver = n.hs_ver
WHERE BINARY h.name_ko <> BINARY n.name_ko;
COMMIT;

-- 같은 연결에서 복원이 필요한 경우에만 아래 구문을 별도로 실행하세요.
-- START TRANSACTION;
-- UPDATE hs_code AS h JOIN tmp_hs46_before AS b
--   ON h.hs_code = b.hs_code AND h.hs_ver = b.hs_ver
-- SET h.name_ko = b.name_ko;
-- COMMIT;
"""
    for path, content in ((seed_path, seed), (update_path, update)):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return {"rows": len(rows), "summaries": len(six), "levels": dict(Counter(len(r["hs_code"]) for r in rows)), "csv_sha256": digest}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, type=Path)
    parser.add_argument("--seed", required=True, type=Path)
    parser.add_argument("--update", required=True, type=Path)
    args = parser.parse_args()
    print(generate(args.csv, args.seed, args.update))
