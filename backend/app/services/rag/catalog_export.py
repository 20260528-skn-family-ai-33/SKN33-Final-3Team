"""DB의 읽기 스냅샷과 검증된 수집 원본으로 이관 카탈로그를 생성합니다."""
from contextlib import contextmanager
import hashlib
from pathlib import Path

from sqlalchemy import MetaData, Table, select

from backend.app.db.news_lock import engine_identity
from backend.app.schemas.rag import ArticleSource
from backend.app.services.rag.ingest import market_supplements, read_articles
from backend.app.services.rag.legacy_migration import validate_catalog
from backend.app.services.rag.store import atomic_json
from backend.app.services.rag.vocabulary import Vocabulary
from backend.app.services.news_keywords import keyword_clean as kc


def file_hash(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


class SnapshotReader:
    """read_articles가 exporter의 동일 연결/트랜잭션을 사용하게 합니다."""
    def __init__(self, connection):
        self.connection = connection

    @contextmanager
    def connect(self):
        yield self.connection


def export_catalog(engine, output, *, raw_paths=(), sources=('KOTRA_MARKET',), dictionary_dir=None):
    sources = tuple(dict.fromkeys(ArticleSource(source).value for source in sources))
    if not sources:
        raise ValueError('catalog_empty_source_scope')
    output = Path(output).expanduser().resolve()
    raw_paths = tuple(dict.fromkeys(Path(path).expanduser().resolve() for path in raw_paths))
    if 'KOTRA_MARKET' in sources and not raw_paths:
        raise ValueError('catalog_market_raw_required')
    dictionary_dir = Path(dictionary_dir) if dictionary_dir is not None else kc.OUT_DIR
    dictionaries = tuple((dictionary_dir / name).resolve() for name in ('synonyms.csv', 'hypernyms.csv', 'topic_categories.csv'))
    inputs = (*raw_paths, *dictionaries)
    if output in inputs:
        raise ValueError('catalog_output_overlaps_input')
    if any(not path.is_file() for path in inputs):
        raise ValueError('catalog_input_file_missing')
    fingerprints = {path: file_hash(path) for path in inputs}
    vocabulary = Vocabulary(kc.load_synonyms(dictionaries[0]), kc.load_hypernyms(dictionaries[1]), kc.load_categories(dictionaries[2]))
    candidates = {}
    for path in raw_paths:
        for key, values in market_supplements(path, preserve_candidates=True).items():
            candidates.setdefault(key, []).extend(values)
    identity = engine_identity(engine)
    with engine.connect() as conn:
        if conn.dialect.name in ('mysql', 'mariadb'):
            conn = conn.execution_options(isolation_level='REPEATABLE READ')
        with conn.begin():
            if conn.dialect.name == 'sqlite':
                conn.exec_driver_sql('BEGIN')  # Python SQLite also snapshots SELECT-only reads.
            article = Table('article', MetaData(), autoload_with=conn)
            if 'visibility_status' not in article.c:
                raise ValueError('catalog_visibility_column_required')
            rows = list(conn.execute(select(article).where(
                article.c.source_code.in_(sources), article.c.visibility_status == 'VISIBLE')).mappings())
            supplements = {}
            for row in rows:
                if row['source_code'] != 'KOTRA_MARKET':
                    continue
                key = (row['source_code'], row['external_id'])
                matches = []
                for candidate in candidates.get(key, []):
                    if candidate['body_masked'] == row['body_masked'] and candidate['body_available']:
                        if candidate not in matches:
                            matches.append(candidate)
                if not matches:
                    raise ValueError('catalog_verified_market_body_required')
                if len(matches) != 1:
                    raise ValueError('catalog_conflicting_raw_candidates')
                supplements[key] = matches[0]
            documents, vocabulary = read_articles(SnapshotReader(conn), sources,
                vocabulary=vocabulary, supplements=supplements)
            expected_ids = {row['article_id'] for row in rows}
            if {doc.article_id for doc in documents} != expected_ids or len(documents) != len(rows):
                raise ValueError('catalog_visible_article_coverage_mismatch')
    documents.sort(key=lambda doc: doc.key)
    data = {'version': 1, 'database_id': identity,
            'vocabulary': {'products': vocabulary.products, 'countries': vocabulary.countries,
                'synonyms': vocabulary.synonyms, 'hypernyms': vocabulary.hypernyms,
                'categories': {term: vocabulary.categories.lookup(term) for term in vocabulary.categories.listed}},
            'articles': [doc.model_dump(mode='json') | {'visibility_status': 'VISIBLE'} for doc in documents]}
    validate_catalog(data, identity)
    if any(file_hash(path) != value for path, value in fingerprints.items()):
        raise ValueError('catalog_input_changed_during_export')
    atomic_json(output, data)
    return {'status': 'exported', 'database_id': identity, 'articles': len(documents),
            'sources': list(sources), 'raw_files': len(raw_paths), 'output': str(output)}
