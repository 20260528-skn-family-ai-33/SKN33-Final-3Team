"""별도 세대 작성·검증 후 참조 교체. 독자는 한 요청 동안 같은 세대를 사용합니다."""
from dataclasses import dataclass
from contextlib import ExitStack, closing
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
from uuid import uuid4

from filelock import FileLock

from backend.app.schemas.rag import ArticleSource, COLLECTION_BY_SOURCE
from backend.app.services.rag.documents import ArticleDocument

MODEL = 'text-embedding-3-large'
DIMENSIONS = 3072
COLLECTIONS = tuple(dict.fromkeys(COLLECTION_BY_SOURCE.values()))
REPOSITORY = Path(__file__).resolve().parents[4]
ROOT = REPOSITORY / 'data/rag/index'
LEGACY_ROOT = REPOSITORY / 'chroma_data'


def resolve_root(root=None):
    value = Path(root or os.environ.get('NEWS_VECTOR_ROOT') or ROOT).expanduser()
    return (value if value.is_absolute() else REPOSITORY / value).resolve()


def resolve_keyword_root(root=None):
    """본문과 전역 키워드의 생산자·조회자가 같은 root 규칙을 사용합니다."""
    return resolve_root(root) / 'keywords'


def document_contract():
    from backend.app.scheduler.jobs.news_embed import DOCUMENT_FORMAT, CHUNK, OVERLAP
    return {'document_format':DOCUMENT_FORMAT, 'chunk_chars':CHUNK, 'overlap_chars':OVERLAP}


def validate_document_contract(record):
    if any(record.get(k) != v for k, v in document_contract().items()):
        raise ValueError('article_document_migration_required')


def validate_generation_contract(record):
    validate_document_contract(record)
    if set(record.get('collections', {})) != set(COLLECTIONS):
        raise ValueError('single_collection_migration_required')


def has_legacy_body(path):
    # Chroma 클라이언트를 만들지 않고 구형 단독 컬렉션 존재 여부만 읽습니다.
    database = Path(path) / 'chroma.sqlite3'
    if not database.is_file():
        return False
    with sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True) as conn:
        return conn.execute("SELECT 1 FROM collections WHERE name='kotra_article' LIMIT 1").fetchone() is not None



def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    try:
        with temp.open('x', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, sort_keys=True, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        if os.name != 'nt':  # Windows는 일반 os.open으로 디렉터리를 열 수 없습니다.
            fd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    finally:
        temp.unlink(missing_ok=True)


def persistent_client(path):
    import chromadb
    from chromadb.config import Settings
    return chromadb.PersistentClient(path=str(path), settings=Settings(anonymized_telemetry=False))


def read_all(collection, include):
    output = {'ids': [], **{key: [] for key in include}}
    for offset in range(0, collection.count(), 500):
        page = collection.get(limit=500, offset=offset, include=include)
        for key in output:
            output[key].extend(page[key])
    return output


@dataclass(frozen=True)
class IndexSnapshot:
    generation: str
    client: object
    sources: frozenset
    dimensions: int

    def collection(self, name):
        if name not in COLLECTIONS:
            raise ValueError('unknown_article_collection')
        col = self.client.get_collection(name, embedding_function=None)
        meta = col.metadata or {}
        validate_document_contract(meta)
        if (meta.get('embedding_model') != MODEL or meta.get('dimensions') != self.dimensions
                or meta.get('hnsw:space') != 'cosine'):
            raise ValueError('incompatible_article_embedding')
        return col


class GenerationStore:
    def __init__(self, root=None, *, client_factory=persistent_client, dimensions=DIMENSIONS, legacy_path=None):
        # factory는 호출마다 새 소유 핸들을 반환해야 합니다. 빌린 client 재사용 함수는 전달하지 않습니다.
        self.root = resolve_root(root)
        # 같은 최종 root면 인자/환경변수 선택 방식과 무관하게 같은 이관 검사를 합니다.
        # 별도 root는 저장소 기본 구형 색인을 자동 인수하거나 조회하지 않습니다.
        self.legacy_path = Path(legacy_path) if legacy_path is not None else (LEGACY_ROOT if self.root == resolve_root(ROOT) else None)
        self.client_factory = client_factory
        self.dimensions = dimensions
        self.lock = FileLock(str(self.root / 'publish.lock'))

    def bind_database(self, identity, *, dry_run=False):
        """publish.lock 안에서 DB 해시를 확인합니다. 이전 미식별 색인은 자동 인수하지 않습니다."""
        if not re.fullmatch(r'[0-9a-f]{64}', identity):
            raise ValueError('invalid_source_database_identity')
        path = self.root / 'database.json'
        if path.exists():
            saved = json.loads(path.read_text(encoding='utf-8'))
            if saved != {'version': 1, 'database_id': identity}:
                raise ValueError('source_database_mismatch')
        elif (self.root / 'active.json').exists() or (self.root / 'keywords/chroma.sqlite3').exists():
            raise ValueError('source_database_unbound')
        elif not dry_run:
            atomic_json(path, {'version': 1, 'database_id': identity})

    def require_active_or_no_legacy(self):
        pointer = self.root / 'active.json'
        if pointer.exists():
            generation = json.loads(pointer.read_text(encoding='utf-8'))['generation']
            if not re.fullmatch(r'[0-9a-f]{32}', generation):
                raise ValueError('invalid_generation')
            validate_generation_contract(json.loads((self.root / 'generations' / generation / 'status.json').read_text(encoding='utf-8')))
        if not (self.root / 'active.json').exists() and self.legacy_path is not None and has_legacy_body(self.legacy_path):
            raise ValueError('legacy_direct_index_migration_required')

    def snapshot(self):
        """호출자가 사용 후 client.close()할 읽기 핸들. 다른 snapshot과 별도 참조입니다."""
        self.require_active_or_no_legacy()
        pointer = json.loads((self.root / 'active.json').read_text(encoding='utf-8'))
        generation = pointer['generation']
        if not re.fullmatch(r'[0-9a-f]{32}', generation):
            raise ValueError('invalid_generation')
        folder = self.root / 'generations' / generation
        report = json.loads((folder / 'status.json').read_text(encoding='utf-8'))
        if report['status'] != 'ready' or not (folder / 'chroma/chroma.sqlite3').exists():
            raise ValueError('generation_not_ready')
        validate_generation_contract(report)
        if report['dimensions'] != self.dimensions:
            raise ValueError('incompatible_dimensions')
        return IndexSnapshot(generation, self.client_factory(folder / 'chroma'),
                             frozenset(ArticleSource(s) for s in report['sources']), self.dimensions)

    def publish(self, documents, sources, embed, *, vocabulary=None, hook=lambda stage: None):
        """sources의 전체 현재 입력을 교체. 다른 출처는 이전 세대에서 보존합니다."""
        sources = frozenset(ArticleSource(s) for s in sources)
        if not sources:
            raise ValueError('empty_source_scope')
        documents = list(documents)
        if any(not isinstance(d, ArticleDocument) or d.source_code not in sources for d in documents):
            raise ValueError('source_scope_mismatch')
        if len({d.key for d in documents}) != len(documents):
            raise ValueError('duplicate_article')
        self.require_active_or_no_legacy()
        self.root.mkdir(parents=True, exist_ok=True)
        with self.lock, ExitStack() as resources:
            for status_path in (self.root / 'generations').glob('*/status.json'):
                stale_report = json.loads(status_path.read_text(encoding='utf-8'))
                if stale_report['status'] == 'building':
                    stale_report.update(status='interrupted', error_code='previous_writer_interrupted')
                    atomic_json(status_path, stale_report)
            old = self.snapshot() if (self.root / 'active.json').exists() else None
            if old:
                resources.enter_context(closing(old.client))
                previous_status = json.loads((self.root / 'generations' / old.generation / 'status.json').read_text(encoding='utf-8'))
                if set(previous_status.get('collections', {})) != set(COLLECTIONS):
                    raise ValueError('single_collection_migration_required')
                old_vocabulary = self.root / 'generations' / old.generation / 'vocabulary.json'
                if (vocabulary is not None and old_vocabulary.exists() and old.sources - sources
                        and json.loads(old_vocabulary.read_text(encoding='utf-8')) != vocabulary):
                    raise ValueError('dictionary_changed_refresh_all_ready_sources')
                catalog = self.root / 'generations' / old.generation / 'articles.jsonl'
                documents += [d for d in (ArticleDocument.model_validate_json(line) for line in catalog.read_text(encoding='utf-8').splitlines())
                              if d.source_code not in sources]
            if len({d.article_id for d in documents}) != len(documents):
                raise ValueError('duplicate_internal_article_id')
            ready_sources = sources | (old.sources if old else frozenset())
            generation = uuid4().hex
            folder = self.root / 'generations' / generation
            folder.mkdir(parents=True)
            report = {'generation': generation, 'status': 'building', 'stage': 'prepare', 'dimensions': self.dimensions,
                      'sources': sorted(s.value for s in ready_sources), 'created_at': datetime.now(timezone.utc).isoformat(),
                      **document_contract(), 'articles': len(documents), 'chunks': 0, 'embedded': 0, 'reused': 0, 'collections': {}}
            atomic_json(folder / 'status.json', report)
            try:
                client = resources.enter_context(closing(self.client_factory(folder / 'chroma')))
                for name in COLLECTIONS:
                    old_rows = read_all(old.collection(name), ['documents', 'metadatas', 'embeddings']) if old else None
                    reuse = {i: (doc, meta, vector) for i, doc, meta, vector in zip(
                        old_rows['ids'], old_rows['documents'], old_rows['metadatas'], old_rows['embeddings'])} if old_rows else {}
                    col = client.create_collection(name, embedding_function=None,
                        metadata={'embedding_model': MODEL, 'dimensions': self.dimensions, 'hnsw:space': 'cosine', **document_contract()})
                    expected = {}
                    from backend.app.scheduler.jobs.news_embed import chunk_text, format_document, CHUNK, OVERLAP
                    for article in sorted(documents, key=lambda d: d.key):
                        if COLLECTION_BY_SOURCE[article.source_code] != name:
                            continue
                        if not article.body.strip() or 'body_origin' in article.metadata_missing:
                            key = hashlib.sha256((article.identity + ':missing_body').encode()).hexdigest()
                            meta = article.metadata(0, '') | {'record_type': 'missing_body'}
                            expected[key] = ('', meta, None)
                            continue
                        for number, chunk in enumerate(chunk_text(article.body, size=CHUNK, overlap=OVERLAP)):
                            text = format_document({'title':article.title, 'published_date':article.published_date,
                                'countries':article.countries, 'category':article.category}, chunk)
                            digest = hashlib.sha256(text.encode()).hexdigest()
                            key = hashlib.sha256((article.identity + ':' + str(number) + ':' + digest).encode()).hexdigest()
                            expected[key] = (text, article.metadata(number, digest), text)
                    keys = list(expected)
                    for start in range(0, len(keys), 64):
                        report.update(stage='embedding', current_collection=name, batch_offset=start)
                        atomic_json(folder / 'status.json', report)
                        part = keys[start:start + 64]
                        missing = [key for key in part if expected[key][2] is not None
                                   and (key not in reuse or reuse[key][0] != expected[key][0])]
                        fresh = list(embed([expected[key][2] for key in missing])) if missing else []
                        if len(fresh) != len(missing):
                            raise ValueError('embedding_count_mismatch')
                        vectors = dict(zip(missing, fresh))
                        for key in part:
                            if key not in vectors:
                                vectors[key] = reuse[key][2] if key in reuse else [0.0] * self.dimensions
                            vector = list(vectors[key])
                            if len(vector) != self.dimensions or not all(math.isfinite(float(v)) for v in vector):
                                raise ValueError('invalid_embedding')
                            if expected[key][2] is not None and not any(float(v) for v in vector):
                                raise ValueError('zero_body_embedding')
                            vectors[key] = vector
                        report['stage'] = 'storage'
                        col.add(ids=part, documents=[expected[k][0] for k in part],
                                metadatas=[expected[k][1] for k in part], embeddings=[vectors[k] for k in part])
                        report['embedded'] += len(missing)
                        report['reused'] += sum(key in reuse and expected[key][2] is not None for key in part if key not in missing)
                        atomic_json(folder / 'status.json', report)
                        hook('batch_written')
                        report['stage'] = 'verification'
                        stored = col.get(ids=part, include=['embeddings'])
                        if set(stored['ids']) != set(part):
                            raise ValueError('stored_embedding_count_mismatch')
                        for key, saved_vector in zip(stored['ids'], stored['embeddings']):
                            if len(saved_vector) != self.dimensions or any(
                                    not math.isclose(float(a), float(b), rel_tol=1e-6, abs_tol=1e-8)
                                    for a, b in zip(saved_vector, vectors[key])):
                                raise ValueError('stored_embedding_content_mismatch')
                    got = read_all(col, ['documents', 'metadatas', 'embeddings'])
                    actual = {i: (d, m) for i, d, m in zip(got['ids'], got['documents'], got['metadatas'])}
                    if actual != {i: (d, m) for i, (d, m, _) in expected.items()}:
                        raise ValueError('index_verification_failed')
                    for key, vector in zip(got['ids'], got['embeddings']):
                        if len(vector) != self.dimensions or not all(math.isfinite(float(v)) for v in vector):
                            raise ValueError('stored_embedding_verification_failed')
                        if expected[key][2] is not None and not any(float(v) for v in vector):
                            raise ValueError('stored_body_embedding_missing')
                    report['collections'][name] = len(expected)
                    report['chunks'] += sum(m['record_type'] == 'body' for d, m, t in expected.values())
                with (folder / 'articles.jsonl').open('x', encoding='utf-8') as stream:
                    for article in sorted(documents, key=lambda d: d.key):
                        stream.write(article.model_dump_json() + '\n')
                    stream.flush()
                    os.fsync(stream.fileno())
                if vocabulary is not None:
                    atomic_json(folder / 'vocabulary.json', vocabulary)
                elif old and (self.root / 'generations' / old.generation / 'vocabulary.json').exists():
                    atomic_json(folder / 'vocabulary.json', json.loads(
                        (self.root / 'generations' / old.generation / 'vocabulary.json').read_text(encoding='utf-8')))
                # 게시 전에 이 호출에서 연 읽기/쓰기 핸들을 닫습니다. 종료 실패도 게시 실패입니다.
                resources.close()
                hook('verified')
                report['status'] = 'ready'
                report.update(stage='publish', ready_at=datetime.now(timezone.utc).isoformat())
                atomic_json(folder / 'status.json', report)
                hook('before_publish')
                atomic_json(self.root / 'active.json', {'generation': generation})
                return report
            except BaseException as exc:
                active = self.root / 'active.json'
                if active.exists() and json.loads(active.read_text(encoding='utf-8')).get('generation') == generation:
                    if old:
                        atomic_json(active, {'generation': old.generation})
                    else:
                        active.unlink()
                report.update(status='failed', error_code=type(exc).__name__)
                atomic_json(folder / 'status.json', report)
                raise
