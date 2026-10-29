"""별도 세대 작성·검증 후 참조 교체. 독자는 한 요청 동안 같은 세대를 사용합니다."""
from dataclasses import dataclass
from contextlib import ExitStack
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
from backend.app.services.news_embedding import MODEL, DIMENSIONS
from backend.app.services.rag.resources import closing_owned

ARTICLE_COLLECTION = 'kotra_article'
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
        return conn.execute("SELECT 1 FROM collections WHERE name=? LIMIT 1", (ARTICLE_COLLECTION,)).fetchone() is not None



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


def collection_distance(collection):
    """Read the immutable index metric without requesting a metric change on modify."""
    meta = collection.metadata or {}
    configuration = getattr(collection, 'configuration', None) or {}
    hnsw = configuration.get('hnsw') or {}
    values = [value for value in (meta.get('hnsw:space'), meta.get('distance_metric'), hnsw.get('space'))
              if value is not None]
    return values[0] if values and all(value == values[0] for value in values) else None


def read_all(collection, include):
    output = {'ids': [], **{key: [] for key in include}}
    for offset in range(0, collection.count(), 500):
        page = collection.get(limit=500, offset=offset, include=include)
        for key in output:
            output[key].extend(page[key])
    return output


def article_records(article):
    """계획과 게시에서 같은 문서·조각·입력 길이를 사용합니다."""
    if not article.body.strip() or 'body_origin' in article.metadata_missing:
        key = hashlib.sha256((article.identity + ':missing_body').encode()).hexdigest()
        yield key, '', article.metadata(0, '') | {'record_type': 'missing_body'}, None
        return
    from backend.app.scheduler.jobs.news_embed import chunk_text, format_document, CHUNK, OVERLAP
    for number, chunk in enumerate(chunk_text(article.body, size=CHUNK, overlap=OVERLAP)):
        text = format_document({'title': article.title, 'published_date': article.published_date,
                                'countries': article.countries, 'category': article.category}, chunk)
        digest = hashlib.sha256(text.encode()).hexdigest()
        key = hashlib.sha256((article.identity + ':' + str(number) + ':' + digest).encode()).hexdigest()
        yield key, text, article.metadata(number, digest), text


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
                or collection_distance(col) != 'cosine'):
            raise ValueError('incompatible_article_embedding')
        return col


class GenerationStore:
    def __init__(self, root=None, *, client_factory=persistent_client, dimensions=None, legacy_path=None):
        # factory는 호출마다 새 소유 핸들을 반환해야 합니다. 빌린 client 재사용 함수는 전달하지 않습니다.
        self.root = resolve_root(root)
        # 같은 최종 root면 인자/환경변수 선택 방식과 무관하게 같은 이관 검사를 합니다.
        # 별도 root는 저장소 기본 구형 색인을 자동 인수하거나 조회하지 않습니다.
        self.legacy_path = Path(legacy_path) if legacy_path is not None else (LEGACY_ROOT if self.root == resolve_root(ROOT) else None)
        self.client_factory = client_factory
        self.dimensions = DIMENSIONS if dimensions is None else dimensions
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
        elif ((self.root / 'active.json').exists() or (self.root / 'keywords/chroma.sqlite3').exists()
              or (self.root / 'keywords/active.json').exists()):
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

    def catalog(self):
        """벡터를 열지 않는 이전 세대 입력. 모델·차원 변경 시에도 재구축에 사용합니다."""
        self.require_active_or_no_legacy()
        if not (self.root / 'active.json').exists():
            return None, []
        generation = json.loads((self.root / 'active.json').read_text(encoding='utf-8'))['generation']
        folder = self.root / 'generations' / generation
        report = json.loads((folder / 'status.json').read_text(encoding='utf-8'))
        if report['status'] != 'ready':
            raise ValueError('generation_not_ready')
        validate_generation_contract(report)
        documents = [ArticleDocument.model_validate_json(line)
                     for line in (folder / 'articles.jsonl').read_text(encoding='utf-8').splitlines()]
        if len(documents) != report['articles']:
            raise ValueError('invalid_generation_catalog')
        if len({d.key for d in documents}) != len(documents):
            raise ValueError('duplicate_article')
        ready = {ArticleSource(s) for s in report['sources']}
        if any(d.source_code not in ready for d in documents):
            raise ValueError('source_scope_mismatch')
        return {**report, 'generation': generation}, documents

    def prepare(self, documents, sources, vocabulary=None):
        """지정 출처 입력을 교체하고 나머지 출처의 검증된 카탈로그를 보존합니다."""
        documents = list(documents)
        previous, catalog = self.catalog()
        ready_sources = frozenset(ArticleSource(s) for s in sources)
        if previous:
            old_sources = frozenset(ArticleSource(s) for s in previous['sources'])
            old_vocabulary = self.root / 'generations' / previous['generation'] / 'vocabulary.json'
            if (vocabulary is not None and old_vocabulary.exists() and old_sources - ready_sources
                    and json.loads(old_vocabulary.read_text(encoding='utf-8')) != vocabulary):
                raise ValueError('dictionary_changed_refresh_all_ready_sources')
            documents += [d for d in catalog if d.source_code not in ready_sources]
            ready_sources |= old_sources
        if len({d.key for d in documents}) != len(documents):
            raise ValueError('duplicate_article')
        if len({d.article_id for d in documents}) != len(documents):
            raise ValueError('duplicate_internal_article_id')
        return previous, documents, ready_sources

    def plan_rebuild(self, documents, sources, vocabulary=None):
        _, documents, ready_sources = self.prepare(documents, sources, vocabulary)
        chunks = chars = missing = 0
        for article in documents:
            for _, text, _, input_text in article_records(article):
                if input_text is None:
                    missing += 1
                else:
                    chunks += 1
                    chars += len(text)
        return {'status': 'planned', 'rebuild': True, 'articles': len(documents),
                'sources': sorted(s.value for s in ready_sources), 'chunks': chunks,
                'embedding_chars': chars, 'embedded': chunks, 'reused': 0,
                'missing_body': missing, 'missing_metadata': sum(bool(d.metadata_missing) for d in documents),
                'embedding_model': MODEL, 'dimensions': self.dimensions}

    def publish(self, documents, sources, embed, *, vocabulary=None, hook=lambda stage: None, rebuild=False):
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
            previous, documents, ready_sources = self.prepare(documents, sources, vocabulary)
            # 재구축은 이전 Chroma를 열지 않으므로 모델 변경·벡터 손상에도 카탈로그를 보존합니다.
            old = self.snapshot() if previous and not rebuild else None
            if old:
                resources.enter_context(closing_owned(old.client, name='previous_body_store'))
            generation = uuid4().hex
            folder = self.root / 'generations' / generation
            folder.mkdir(parents=True)
            report = {'generation': generation, 'status': 'building', 'stage': 'prepare', 'dimensions': self.dimensions,
                      'embedding_model': MODEL, 'rebuild': rebuild, 'embedding_chars': 0,
                      'sources': sorted(s.value for s in ready_sources), 'created_at': datetime.now(timezone.utc).isoformat(),
                      **document_contract(), 'articles': len(documents), 'chunks': 0, 'embedded': 0, 'reused': 0, 'collections': {}}
            atomic_json(folder / 'status.json', report)
            ready_persisted = False
            try:
                client = resources.enter_context(closing_owned(self.client_factory(folder / 'chroma'), name='body_store'))
                for name in COLLECTIONS:
                    old_rows = read_all(old.collection(name), ['documents', 'metadatas', 'embeddings']) if old else None
                    reuse = {i: (doc, meta, vector) for i, doc, meta, vector in zip(
                        old_rows['ids'], old_rows['documents'], old_rows['metadatas'], old_rows['embeddings'])} if old_rows else {}
                    # Only explicitly migrated, internally consistent legacy IDs are reusable.
                    legacy_keys = {}
                    for old_key, (old_text, old_meta, _) in reuse.items():
                        if old_meta.get('migration_legacy_id') is not True:
                            continue
                        number = old_meta.get('chunk')
                        article_id = old_meta.get('article_id')
                        identity = old_meta.get('article_key')
                        text_hash = hashlib.sha256(old_text.encode()).hexdigest()
                        if (type(number) is not int or number < 0 or type(article_id) is not int
                                or old_key != f'{article_id}:{number}' or old_meta.get('legacy_record_id') != old_key
                                or old_meta.get('text_hash') != text_hash):
                            raise ValueError('invalid_migrated_record_identity')
                        identity_parts = json.loads(identity)
                        if identity_parts != [old_meta.get('source_code'), old_meta.get('external_id')]:
                            raise ValueError('invalid_migrated_record_identity')
                        canonical_key = hashlib.sha256((identity + ':' + str(number) + ':' + text_hash).encode()).hexdigest()
                        if canonical_key in legacy_keys:
                            raise ValueError('duplicate_migrated_record_identity')
                        legacy_keys[canonical_key] = old_key
                    col = client.create_collection(name, embedding_function=None,
                        metadata={'embedding_model': MODEL, 'dimensions': self.dimensions, 'hnsw:space': 'cosine', **document_contract()})
                    expected = {}
                    for article in sorted(documents, key=lambda d: d.key):
                        if COLLECTION_BY_SOURCE[article.source_code] != name:
                            continue
                        for key, text, metadata, input_text in article_records(article):
                            number = metadata['chunk']
                            old_key = legacy_keys.get(key)
                            if old_key is not None:
                                old_text, old_meta, _ = reuse[old_key]
                                if (old_text == text and old_meta['article_id'] == article.article_id
                                        and old_meta['source_code'] == article.source_code.value
                                        and old_meta['article_key'] == article.identity and old_meta['chunk'] == number):
                                    key = old_key
                                    # Retain custom legacy fields, but do not retain removed canonical tags.
                                    reserved = set(metadata) | {'countries', 'keywords', 'hs_keys', 'body_hash', 'category'}
                                    extra = {k:v for k,v in old_meta.items() if k not in reserved}
                                    if 'body_hash' in old_meta:
                                        from backend.app.scheduler.jobs.news_embed import body_hash
                                        extra['body_hash'] = body_hash({'title':article.title, 'body_masked':article.body})
                                    if article.category is not None:
                                        extra['category'] = article.category
                                    metadata = extra | metadata
                            if key in expected:
                                raise ValueError('duplicate_article_record_id')
                            expected[key] = (text, metadata, input_text)
                    keys = list(expected)
                    for start in range(0, len(keys), 64):
                        report.update(stage='embedding', current_collection=name, batch_offset=start)
                        atomic_json(folder / 'status.json', report)
                        part = keys[start:start + 64]
                        missing = [key for key in part if expected[key][2] is not None
                                   and (key not in reuse or reuse[key][0] != expected[key][0])]
                        fresh = list(embed([expected[key][2] for key in missing])) if missing else []
                        report['embedding_chars'] += sum(len(expected[key][2]) for key in missing)
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
                elif previous and (self.root / 'generations' / previous['generation'] / 'vocabulary.json').exists():
                    atomic_json(folder / 'vocabulary.json', json.loads(
                        (self.root / 'generations' / previous['generation'] / 'vocabulary.json').read_text(encoding='utf-8')))
                # 게시 전에 이 호출에서 연 읽기/쓰기 핸들을 닫습니다. 종료 실패도 게시 실패입니다.
                resources.close()
                hook('verified')
                report['status'] = 'ready'
                report.update(stage='publish', ready_at=datetime.now(timezone.utc).isoformat())
                atomic_json(folder / 'status.json', report)
                ready_persisted = True
                hook('before_publish')
                atomic_json(self.root / 'active.json', {'generation': generation})
                return report
            except BaseException as exc:
                active = self.root / 'active.json'
                rollback_failed = False
                try:
                    if active.exists() and json.loads(active.read_text(encoding='utf-8')).get('generation') == generation:
                        if previous:
                            atomic_json(active, {'generation': previous['generation']})
                        else:
                            active.unlink()
                except BaseException as rollback:
                    rollback_failed = True
                    exc.add_note(f'body_pointer_rollback_failed:{type(rollback).__name__}')
                if rollback_failed and ready_persisted:
                    # 포인터가 새 세대를 계속 가리킬 수 있습니다. 검증·종료·ready 저장이
                    # 끝난 세대를 failed로 바꾸면 검색뿐 아니라 카탈로그 기반 재시도도 막힙니다.
                    exc.add_note('body_ready_generation_preserved_after_rollback_failure')
                else:
                    report.update(status='failed', error_code=type(exc).__name__)
                    try:
                        atomic_json(folder / 'status.json', report)
                    except BaseException as cleanup:
                        exc.add_note(f'body_status_write_failed:{type(cleanup).__name__}')
                raise
