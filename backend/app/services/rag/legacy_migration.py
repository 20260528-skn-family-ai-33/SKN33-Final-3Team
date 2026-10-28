"""구형 본문 벡터의 검증된 무재임베딩 이관. 원본은 파일 읽기만 수행합니다."""
from contextlib import contextmanager, nullcontext
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
from tempfile import TemporaryDirectory, gettempdir
from uuid import uuid4

from backend.app.services.rag.documents import ArticleDocument
from backend.app.services.rag.resources import close_owned
from backend.app.services.rag.store import (
    GenerationStore, MODEL, DIMENSIONS, atomic_json, document_contract,
    persistent_client, validate_generation_contract, collection_distance,
)

from backend.app.services.news_collections import ARTICLE_COLLECTION as COLLECTION


def fingerprint(root):
    root = Path(root)
    if root.is_symlink() or any(p.is_symlink() for p in root.rglob('*')):
        raise ValueError('migration_symlink_not_allowed')
    if not root.is_dir():
        raise ValueError('migration_directory_missing')
    result = {}
    for path in sorted(root.rglob('*')):
        if path.is_file():
            digest = hashlib.sha256()
            with path.open('rb') as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(block)
            result[path.relative_to(root).as_posix()] = digest.hexdigest()
        elif not path.is_dir():
            raise ValueError('migration_special_file_not_allowed')
    return result


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def clean_path(path):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('migration_symlink_not_allowed')
    return path.resolve()


@contextmanager
def readonly_clone(source, factory, *, forbidden_root=None):
    """Chroma는 복제본만 엽니다. writer 중지는 호출자가 확인해야 합니다."""
    before = fingerprint(source)
    if 'chroma.sqlite3' not in before:
        raise ValueError('legacy_chroma_database_missing')
    scratch_parent = Path(gettempdir()).resolve()
    if scratch_parent.is_relative_to(source) or (forbidden_root is not None and scratch_parent.is_relative_to(forbidden_root)):
        raise ValueError('migration_scratch_overlap')
    with TemporaryDirectory(prefix='news-legacy-read-') as temporary:
        clone = Path(temporary) / 'chroma'
        if clone.is_relative_to(source) or source.is_relative_to(clone):
            raise ValueError('migration_scratch_overlap')
        shutil.copytree(source, clone, symlinks=True)
        if fingerprint(clone) != before or fingerprint(source) != before:
            raise ValueError('migration_source_changed_during_copy')
        client = factory(clone)
        try:
            yield client, before
        finally:
            close_owned([('legacy_clone', client.close)], primary=sys.exc_info()[1])


def load_catalog(path, database_id):
    return validate_catalog(json.loads(Path(path).read_text(encoding='utf-8')), database_id)


def validate_catalog(data, database_id):
    if not re.fullmatch(r'[0-9a-f]{64}', database_id):
        raise ValueError('invalid_source_database_identity')
    if data.get('version') != 1 or data.get('database_id') != database_id:
        raise ValueError('migration_catalog_database_mismatch')
    vocabulary = data.get('vocabulary')
    fields = {'products', 'countries', 'synonyms', 'hypernyms', 'categories'}
    if not isinstance(vocabulary, dict) or set(vocabulary) != fields or not all(isinstance(v, dict) for v in vocabulary.values()):
        raise ValueError('migration_vocabulary_required')
    documents = []
    required = {'article_id', 'source_code', 'external_id', 'title', 'published_date',
                'source_url', 'body', 'countries', 'kw_topic', 'keywords_ready', 'metadata_missing', 'provenance'}
    for row in data.get('articles', []):
        if not required <= row.keys() or row.get('visibility_status') != 'VISIBLE':
            raise ValueError('migration_explicit_article_catalog_required')
        article = ArticleDocument.model_validate({k:v for k,v in row.items() if k != 'visibility_status'})
        if not article.body.strip() or 'body_origin' in article.metadata_missing:
            raise ValueError('migration_verified_body_required')
        fallback = 'body:summary_fallback' in article.provenance
        if (('body' in article.metadata_missing) != fallback
                or (fallback and article.source_code.value != 'KOTRA_MARKET')):
            raise ValueError('migration_invalid_summary_fallback')
        if not article.source_url and 'source_url' not in article.metadata_missing:
            raise ValueError('migration_missing_url_must_be_declared')
        if article.source_code.value == 'KOTRA_SHORT_NEWS' and article.products:
            raise ValueError('migration_shortnews_hs_not_allowed')
        documents.append(article)
    if not documents or len({a.article_id for a in documents}) != len(documents) or len({a.key for a in documents}) != len(documents):
        raise ValueError('migration_empty_or_duplicate_article_catalog')
    return documents, vocabulary


def expected_records(documents):
    from backend.app.scheduler.jobs.news_embed import chunk_text, format_document
    expected = {}
    for article in documents:
        for number, chunk in enumerate(chunk_text(article.body)):
            text = format_document({'published_date':article.published_date, 'countries':article.countries,
                                    'category':article.category, 'title':article.title}, chunk)
            key = f'{article.article_id}:{number}'
            meta = article.metadata(number, hashlib.sha256(text.encode()).hexdigest())
            expected[key] = (text, meta, article)
    return expected



def preserve_legacy_country_order(collection, articles, batch_size, expected):
    """국가의 구성은 엄격히 검증하고, 저장 문서와 일치하는 순서만 카탈로그에 보존합니다."""
    seen, adapted, records = set(), {}, {}
    for offset in range(0, collection.count(), batch_size):
        page = collection.get(limit=batch_size, offset=offset, include=['documents', 'metadatas'])
        ids = page['ids']
        if not ids or len(ids) != len(set(ids)) or seen.intersection(ids):
            raise ValueError('migration_duplicate_or_missing_page')
        if any(len(page[field]) != len(ids) for field in ('documents', 'metadatas')):
            raise ValueError('migration_page_length_mismatch')
        for key, text, metadata in zip(ids, page['documents'], page['metadatas']):
            if key not in expected:
                raise ValueError('migration_record_not_in_catalog')
            if not isinstance(metadata, dict):
                raise ValueError('migration_metadata_missing')
            article = expected[key][2]
            countries = metadata.get('countries', list(article.countries))
            if (not isinstance(countries, list) or any(not isinstance(c, str) for c in countries)
                    or len(countries) != len(set(countries))
                    or len(countries) != len(article.countries) or set(countries) != set(article.countries)):
                raise ValueError('migration_catalog_metadata_mismatch')
            if article.article_id not in adapted:
                legacy = article.model_copy(update={'countries': tuple(countries)})
                adapted[article.article_id] = legacy
                records[article.article_id] = expected_records([legacy])
            elif tuple(countries) != adapted[article.article_id].countries:
                raise ValueError('migration_inconsistent_article_country_order')
            # Reconstruct the complete expected document. Only country order can differ;
            # date, title, category, body, chunk boundaries and every country stay exact.
            if text != records[article.article_id][key][0]:
                raise ValueError('migration_legacy_document_mismatch')
        seen.update(ids)
    if seen != set(expected):
        raise ValueError('migration_catalog_coverage_mismatch')
    return [adapted[article.article_id] for article in articles]


def validate_row(key, document, metadata, vector, expected, dimensions, *, keyword_changes=None):
    if key not in expected:
        raise ValueError('migration_record_not_in_catalog')
    text, canonical, article = expected[key]
    if document != text:
        raise ValueError('migration_legacy_document_mismatch')
    if not isinstance(metadata, dict):
        raise ValueError('migration_metadata_missing')
    required = {'article_id':article.article_id, 'chunk':canonical['chunk']}
    if any(type(metadata.get(k)) is not int or metadata[k] != v for k,v in required.items()):
        raise ValueError('migration_article_id_or_chunk_mismatch')
    from backend.app.scheduler.jobs.news_embed import body_hash
    comparable = {'body_hash':body_hash({'title':article.title, 'body_masked':article.body}),
                  'migration_legacy_id':True, 'legacy_record_id':key, 'source_code':article.source_code.value, 'external_id':article.external_id,
                  'title':article.title, 'published_date':article.published_date.isoformat(),
                  'countries':list(article.countries), 'keywords':list(article.kw_topic),
                  'category':article.category, **canonical}
    # Keywords are derived metadata, not part of the embedded document. Their
    # explicit transition must not weaken identity/text/model/vector validation.
    comparable.pop('keywords', None)
    before = metadata.get('keywords', [])
    if not isinstance(before, list) or any(not isinstance(value, str) for value in before):
        raise ValueError('migration_invalid_keyword_metadata')
    if any(k in metadata and metadata[k] != v for k,v in comparable.items()):
        raise ValueError('migration_catalog_metadata_mismatch')
    if vector is None or len(vector) != dimensions or not all(math.isfinite(float(v)) for v in vector) or not any(float(v) for v in vector):
        raise ValueError('migration_invalid_embedding')
    after = list(article.kw_topic)
    if before != after and keyword_changes is not None:
        keyword_changes.append({'article_id': article.article_id, 'record_id': key,
                                'before': before, 'after': after})
    # Omitted canonical keywords mean an empty set, not permission to retain old tags.
    preserved = {name: value for name, value in metadata.items() if name != 'keywords'}
    return {**preserved, **canonical, 'migration_legacy_id':True, 'legacy_record_id':key}


def verify_page(collection, ids, documents, metadatas, embeddings):
    page = collection.get(ids=ids, include=['documents', 'metadatas', 'embeddings'])
    found = {key:(doc, meta, vec) for key,doc,meta,vec in zip(page['ids'],page['documents'],page['metadatas'],page['embeddings'])}
    if set(found) != set(ids):
        raise ValueError('migration_copied_ids_mismatch')
    for key,doc,meta,vector in zip(ids,documents,metadatas,embeddings):
        saved_doc,saved_meta,saved_vector = found[key]
        if saved_doc != doc or saved_meta != meta or len(saved_vector) != len(vector) or any(float(a) != float(b) for a,b in zip(saved_vector,vector)):
            raise ValueError('migration_copied_content_mismatch')


def migrate(source, root, catalog, database_id, *, apply=False, source_stopped=False,
            legacy_model=None, client_factory=persistent_client, dimensions=DIMENSIONS,
            batch_size=256, hook=lambda stage: None):
    """기본 dry-run. 기존 active는 동일 이관의 검증된 재실행만 허용합니다."""
    if not source_stopped:
        raise ValueError('migration_stop_all_source_writers_required')
    if type(batch_size) is not int or not 1 <= batch_size <= 1000:
        raise ValueError('migration_invalid_batch_size')
    source, root = clean_path(source), clean_path(root)
    if source == root or source.is_relative_to(root) or root.is_relative_to(source):
        raise ValueError('migration_source_target_overlap')
    if root.exists() and any(p.is_symlink() for p in root.rglob('*')):
        raise ValueError('migration_symlink_not_allowed')
    articles, vocabulary = load_catalog(catalog, database_id)
    expected = expected_records(articles)
    store = GenerationStore(root, client_factory=client_factory, dimensions=dimensions, legacy_path=source)
    # Dry-run creates no target directory, database binding or lock file.
    if apply:
        root.mkdir(parents=True, exist_ok=True)
    with (store.lock.acquire(timeout=0) if apply else nullcontext()):
        store.bind_database(database_id, dry_run=True)
        with readonly_clone(source, client_factory, forbidden_root=root) as (reader, source_files):
            col = reader.get_collection(COLLECTION, embedding_function=None)
            info = col.metadata or {}
            model = info.get('embedding_model', legacy_model)
            if model != MODEL or (legacy_model is not None and legacy_model != MODEL) or collection_distance(col) != 'cosine':
                raise ValueError('migration_model_or_metric_provenance_required')
            if info.get('dimensions', dimensions) != dimensions or col.count() != len(expected):
                raise ValueError('migration_dimension_or_count_mismatch')
            original_orders = {a.article_id: a.countries for a in articles}
            articles = preserve_legacy_country_order(col, articles, batch_size, expected)
            expected = expected_records(articles)
            country_order_changes = sum(a.countries != original_orders[a.article_id] for a in articles)
            # Hash, payload, content_version and persisted catalog all describe the
            # actual legacy text. Other sources keep it until their SQL refresh.
            migration_id = digest({'source_files':source_files,'articles':[a.model_dump(mode='json') for a in articles],
                                   'vocabulary':vocabulary,'database_id':database_id,'model':model,'dimensions':dimensions})
            active = root/'active.json'
            existing = None
            if active.exists():
                generation = json.loads(active.read_text(encoding='utf-8'))['generation']
                if not re.fullmatch(r'[0-9a-f]{32}', generation):
                    raise ValueError('invalid_generation')
                folder = root/'generations'/generation
                existing = json.loads((folder/'status.json').read_text(encoding='utf-8'))
                validate_generation_contract(existing)
                if existing.get('migration_id') != migration_id or existing.get('status') != 'ready':
                    raise ValueError('migration_target_already_active')
                if (existing.get('generation') != generation or existing.get('dimensions') != dimensions
                        or existing.get('sources') != sorted({a.source_code.value for a in articles})
                        or existing.get('articles') != len(articles) or existing.get('chunks') != len(expected)
                        or existing.get('collections') != {COLLECTION:len(expected)}):
                    raise ValueError('migration_existing_status_mismatch')
                if (folder/'articles.jsonl').read_text(encoding='utf-8') != ''.join(a.model_dump_json()+'\n' for a in articles) or json.loads((folder/'vocabulary.json').read_text(encoding='utf-8')) != vocabulary:
                    raise ValueError('migration_existing_catalog_mismatch')
            generation = existing['generation'] if existing else uuid4().hex
            folder = root/'generations'/generation
            report = {'generation':generation, 'migration_id':migration_id, 'status':'building',
                      'dimensions':dimensions, 'sources':sorted({a.source_code.value for a in articles}),
                      'articles':len(articles), 'chunks':len(expected), 'embedded':0, 'reused':len(expected),
                      'country_order_preserved_articles':country_order_changes,
                      'collections':{COLLECTION:len(expected)}, **document_contract()}
            fallback_ids = sorted(a.article_id for a in articles if 'body:summary_fallback' in a.provenance)
            report.update(summary_fallback_articles=len(fallback_ids), summary_fallback_article_ids=fallback_ids,
                          summary_fallback_chunks=sum('body:summary_fallback' in a.provenance for _, _, a in expected.values()),
                          keyword_changes=[], keyword_changed_articles=0, keyword_changed_chunks=0)
            writer = None
            try:
                if apply and not existing:
                    folder.mkdir(parents=True)
                    atomic_json(folder/'status.json', report)
                    # Re-adding cosine vectors can normalize their float32 values again.
                    # Clone the stopped database, then change metadata only: keep the
                    # original persisted vectors and documents bit-for-bit.
                    shutil.copytree(source, folder/'chroma', symlinks=True)
                    if fingerprint(folder/'chroma') != source_files or fingerprint(source) != source_files:
                        raise ValueError('migration_source_changed_during_copy')
                    writer = client_factory(folder/'chroma')
                    for collection in writer.list_collections():
                        if collection.name != COLLECTION:
                            writer.delete_collection(collection.name)  # Owned target copy only.
                    target = writer.get_collection(COLLECTION, embedding_function=None)
                    target.modify(metadata={**{key: value for key, value in info.items() if key != 'hnsw:space'},
                                            'embedding_model':MODEL,'dimensions':dimensions,
                                            'distance_metric':'cosine',**document_contract()})
                    if collection_distance(target) != 'cosine':
                        raise ValueError('migration_copied_metric_mismatch')
                with (readonly_clone(folder/'chroma',client_factory, forbidden_root=root) if existing else nullcontext(None)) as old:
                    target_read = old[0].get_collection(COLLECTION,embedding_function=None) if old else None
                    if target_read is not None:
                        target_info = target_read.metadata or {}
                        if (target_read.count() != len(expected) or target_info.get('embedding_model') != MODEL
                                or target_info.get('dimensions') != dimensions or collection_distance(target_read) != 'cosine'
                                or any(target_info.get(k) != v for k,v in document_contract().items())):
                            raise ValueError('migration_existing_collection_mismatch')
                    seen = set()
                    for offset in range(0, col.count(), batch_size):
                        page = col.get(limit=batch_size,offset=offset,include=['documents','metadatas','embeddings'])
                        ids = page['ids']
                        if not ids or len(set(ids)) != len(ids) or seen.intersection(ids):
                            raise ValueError('migration_duplicate_or_missing_page')
                        if any(len(page[k]) != len(ids) for k in ['documents','metadatas','embeddings']):
                            raise ValueError('migration_page_length_mismatch')
                        metas = [validate_row(key,doc,meta,vec,expected,dimensions,keyword_changes=report['keyword_changes']) for key,doc,meta,vec in
                                 zip(ids,page['documents'],page['metadatas'],page['embeddings'])]
                        seen.update(ids)
                        if writer:
                            updates = [meta | {key: None for key in old_meta if key not in meta}
                                       for meta, old_meta in zip(metas, page['metadatas'])]
                            target.update(ids=ids, metadatas=updates)
                            verify_page(target,ids,page['documents'],metas,page['embeddings'])
                            hook('batch_written')
                        if target_read is not None:
                            verify_page(target_read,ids,page['documents'],metas,page['embeddings'])
                    if seen != set(expected):
                        raise ValueError('migration_catalog_coverage_mismatch')
                report['keyword_changed_chunks'] = len(report['keyword_changes'])
                report['keyword_changed_articles'] = len({change['article_id'] for change in report['keyword_changes']})
                report['keyword_changes'].sort(key=lambda change: (change['article_id'], change['record_id']))
                if fingerprint(source) != source_files:
                    raise ValueError('migration_source_changed_during_copy')
                if writer:
                    if target.count() != len(expected):
                        raise ValueError('migration_final_count_mismatch')
                    with (folder/'articles.jsonl').open('x',encoding='utf-8') as stream:
                        for article in articles:
                            stream.write(article.model_dump_json()+'\n')
                        stream.flush()
                        os.fsync(stream.fileno())
                    atomic_json(folder/'vocabulary.json',vocabulary)
            finally:
                if writer is not None:
                    close_owned([('migration_target',writer.close)],primary=sys.exc_info()[1])
        # Both copied-source and target clients are closed before publishing.
        if not apply or existing:
            return {**report,'status':'already_complete' if existing else 'planned'}
        binding = root/'database.json'
        old_binding = binding.read_bytes() if binding.exists() else None
        try:
            hook('verified')
            report['status'] = 'ready'
            atomic_json(folder/'status.json',report)
            if old_binding is None:
                atomic_json(binding,{'version':1,'database_id':database_id})
            hook('before_publish')
            if fingerprint(source) != source_files:
                raise ValueError('migration_source_changed_during_copy')
            atomic_json(active,{'generation':generation})
        except BaseException:
            if active.exists() and json.loads(active.read_text(encoding='utf-8')).get('generation') == generation:
                active.unlink()
            if old_binding is None and binding.exists() and json.loads(binding.read_text(encoding='utf-8')) == {'version':1,'database_id':database_id}:
                binding.unlink()
            report['status'] = 'failed'
            atomic_json(folder/'status.json',report)
            raise
        return report
