"""최신 DB 분류의 읽기 전용 스냅샷과 단신속보용 키워드 후보 인덱스.

기존 news_keyword 컬렉션과 사전 생성·DB 적재 흐름은 변경하지 않습니다.
분류 상태에 따른 제외 없이 모든 DB 키워드를 색인하고 동의어의 최종 대표어를 기록합니다.
분류 내용이 바뀌면 별도 컬렉션을 사용하므로 미완료 색인이 기존 색인을 덮어쓰지 않습니다.
"""
import hashlib
import json
import math
from dataclasses import dataclass

from sqlalchemy import text

MODEL = "text-embedding-3-large"
DIMENSIONS = 3072
STATUSES = {"PENDING", "CLASSIFIED", "UNCLASSIFIED", "SYNONYM"}


@dataclass(frozen=True)
class Catalog:
    revision: str
    rows: tuple


def catalog(topics, categories):
    """시각 메타데이터를 제외한 실제 분류 내용을 검증하고 버전을 계산합니다."""
    by_word = {r["keyword_text"]: r for r in topics}
    by_category = {r["category_id"]: r for r in categories}
    if not topics or len(by_word) != len(topics) or len(by_category) != len(categories):
        raise ValueError("키워드 분류가 비어 있거나 식별자가 중복됩니다")
    rows = []
    for word, row in sorted(by_word.items()):
        if not isinstance(word, str) or not word.strip() or len(word) > 120:
            raise ValueError("키워드 형식 오류")
        status = row["status"]
        if status not in STATUSES:
            raise ValueError("지원하지 않는 분류 상태")
        if (status == "SYNONYM") != (row.get("canonical_text") is not None):
            raise ValueError("동의어 상태와 대표어 불일치")
        if (status == "CLASSIFIED") != (row.get("category_id") is not None):
            raise ValueError("분류 상태와 분야 불일치")
        current, seen = row, {word}
        while current["status"] == "SYNONYM":
            target = current.get("canonical_text")
            if target in seen or target not in by_word:
                raise ValueError("대표어가 없거나 동의어 관계가 순환합니다")
            seen.add(target)
            current = by_word[target]
        category_id = current.get("category_id")
        if category_id is not None and category_id not in by_category:
            raise ValueError("DB 분류에 없는 분야 참조")
        category = by_category.get(category_id, {})
        rows.append({"keyword": word, "canonical": current["keyword_text"], "status": status,
                     "canonical_status": current["status"],
                     "major": category.get("major_name", ""), "minor": category.get("minor_name", "")})
    payload = {"model": MODEL, "dimensions": DIMENSIONS, "rows": rows}
    revision = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return Catalog(revision, tuple(rows))


def read_catalog(engine):
    # MySQL의 반복 읽기 트랜잭션에서 두 테이블이 같은 시점의 분류를 나타내게 합니다.
    with engine.connect() as conn:
        if engine.dialect.name == "mysql":
            conn = conn.execution_options(isolation_level="REPEATABLE READ")
        with conn.begin():
            topics = list(conn.execute(text("SELECT keyword_text,canonical_text,category_id,status FROM keyword_topic ORDER BY keyword_text")).mappings())
            categories = list(conn.execute(text("SELECT category_id,major_name,minor_name FROM keyword_category ORDER BY category_id")).mappings())
    return catalog(topics, categories)


def collection_name(snapshot):
    return "short_news_keywords_" + snapshot.revision[:32]


def sync_index(snapshot, client, embed, *, batch_size=128):
    """미완료 배치부터 재개합니다. 성공 시에만 완성된 후보 인덱스를 반환합니다."""
    if not 1 <= batch_size <= 128:
        raise ValueError("키워드 임베딩 배치 크기는 1~128이어야 합니다")
    metadata = {"hnsw:space": "cosine", "embedding_model": MODEL,
                "dimensions": DIMENSIONS, "classification_revision": snapshot.revision}
    col = client.get_or_create_collection(collection_name(snapshot), embedding_function=None, metadata=metadata)
    if any((col.metadata or {}).get(k) != v for k, v in metadata.items()):
        raise ValueError("키워드 인덱스의 모델·분류 버전 불일치")
    expected = {r["keyword"]: r for r in snapshot.rows}
    got = col.get(include=["documents", "metadatas"])
    if set(got["ids"]) - set(expected):
        raise ValueError("분류 버전에 없는 키워드가 인덱스에 있습니다")
    have = {key for key, doc, meta in zip(got["ids"], got["documents"], got["metadatas"])
            if doc == key and meta == expected[key]}
    todo = sorted(set(expected) - have)
    for start in range(0, len(todo), batch_size):
        words = todo[start:start + batch_size]
        vectors = embed(words)
        if len(vectors) != len(words) or any(len(v) != DIMENSIONS or
                not all(math.isfinite(x) for x in v) for v in vectors):
            raise ValueError("키워드 임베딩 응답 개수·차원·수치 오류")
        col.upsert(ids=words, documents=words, metadatas=[expected[w] for w in words], embeddings=vectors)
    # 일부분만 들어간 상태를 성공으로 반환하지 않습니다.
    saved = col.get(include=["documents", "metadatas"])
    actual = {key: (doc, meta) for key, doc, meta in zip(saved["ids"], saved["documents"], saved["metadatas"])}
    if actual != {key: (key, value) for key, value in expected.items()}:
        raise ValueError("키워드 인덱스 검증 실패")
    return col, {"total": len(expected), "embedded": len(todo), "existing": len(have),
                 "revision": snapshot.revision, "collection": collection_name(snapshot)}
