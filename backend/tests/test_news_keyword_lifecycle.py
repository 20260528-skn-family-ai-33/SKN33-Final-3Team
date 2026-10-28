"""실제 임시 SQL/Chroma에서 키워드 writer·취소·복구 계약을 검증합니다."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from threading import Event, get_ident
from types import SimpleNamespace as NS
from unittest.mock import Mock

from filelock import FileLock
import pytest
from sqlalchemy import text

from backend.app.scheduler.jobs import news_embed as ne
from backend.app.services.rag import keyword_store as ks, store
from backend.tests import test_news_embed_rebuild as fixture


@pytest.fixture
def keyword_case():
    case = fixture.RebuildTest()
    case.setUp()
    try:
        case.seed_market()
        assert case.run_default() == 0
        case.encode.reset_mock()
        case.encode.usage = None
        yield case
    finally:
        case.doCleanups()


def keyword_rows(base):
    client = store.persistent_client(ks.active_path(base))
    try:
        col = client.get_collection('news_keyword', embedding_function=None)
        rows = store.read_all(col, ['documents', 'metadatas', 'embeddings'])
        return {key: (doc, meta, list(vector)) for key, doc, meta, vector in
                zip(rows['ids'], rows['documents'], rows['metadatas'], rows['embeddings'])}
    finally:
        client.close()


def add_keyword(case, word):
    with case.db.engine.begin() as conn:
        conn.execute(text('INSERT INTO article_tag(article_id,keyword_text) VALUES (:id,:word)'),
                     {'id': case.db.rows('article')[0]['article_id'], 'word': word})


@pytest.mark.parametrize('generation', [False, True])
def test_borrowed_writer_waits_for_logical_lock_then_reads_fresh_counts(keyword_case, monkeypatch, generation):
    case = keyword_case
    base = case.root/'keywords'
    if generation:
        assert case.run_default(keywords_only=True, rebuild=True) == 0
    physical = ks.active_path(base)
    client = store.persistent_client(physical)
    original_counts = ne.keyword_article_counts
    attempting, finished = Event(), Event()
    first, futures, lock_paths, escaped = [True], [], [], []
    owner = get_ident()

    class ObservedLock(FileLock):
        def acquire(self, *args, **kwargs):
            if get_ident() != owner:
                lock_paths.append(self.lock_file)
                attempting.set()
            return super().acquire(*args, **kwargs)

    def borrowed():
        try:
            return ne.run(case.db.url, client=client, keywords_only=True,
                          embed=lambda words: [[1.0]+[0.0]*3071 for _ in words],
                          log_dir=case.db.root/'borrowed_logs')
        finally:
            finished.set()

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            def counts(engine, **kwargs):
                snapshot = original_counts(engine, **kwargs)
                if first[0]:
                    first[0] = False
                    add_keyword(case, 'concurrent-new-keyword')
                    futures.append(pool.submit(borrowed))
                    assert attempting.wait(5)
                    escaped.append(finished.wait(0.15))
                return snapshot

            with monkeypatch.context() as patch:
                patch.setattr('filelock.FileLock', ObservedLock)
                patch.setattr(ne, 'keyword_article_counts', counts)
                assert ne.sync_all_keywords(case.db.engine, base, case.encode)['status'] == 'complete'
                assert futures[0].result(timeout=5) == 0
        assert escaped == [False]
        assert lock_paths and set(lock_paths) == {str(base)+'.lock'}
        assert set(keyword_rows(base)) == set(original_counts(case.db.engine))
        assert 'concurrent-new-keyword' in keyword_rows(base)
    finally:
        client.close()


@pytest.mark.parametrize('legacy', [False, True])
def test_borrowed_client_rechecks_pointer_after_wait_and_requires_reopen(keyword_case, monkeypatch, legacy):
    case = keyword_case
    base = case.root/'keywords'
    if not legacy:
        assert case.run_default(keywords_only=True, rebuild=True) == 0
    old_path = ks.active_path(base)
    client = store.persistent_client(old_path)
    before = keyword_rows(base)
    attempting = Event()

    class ObservedLock(FileLock):
        def acquire(self, *args, **kwargs):
            attempting.set()
            return super().acquire(*args, **kwargs)

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with FileLock(str(base)+'.lock'):
                with monkeypatch.context() as patch:
                    patch.setattr('filelock.FileLock', ObservedLock)
                    future = pool.submit(ne.run, case.db.url, client=client, keywords_only=True,
                                         embed=case.encode, log_dir=case.db.root/'borrowed_logs')
                    assert attempting.wait(5)
                ks.rebuild(base, ne.keyword_article_counts(case.db.engine), case.encode,
                           model=ne.MODEL, dimensions=ne.DIMENSIONS)
            case.encode.reset_mock()
            with monkeypatch.context() as patch:
                counts = Mock(side_effect=AssertionError('stale client must not query DB'))
                patch.setattr(ne, 'keyword_article_counts', counts)
                assert future.result(timeout=5) == 1
                counts.assert_not_called()
        assert ks.active_path(base) != old_path
        assert keyword_rows(base) == before
        case.encode.assert_not_called()
        reopened = store.persistent_client(ks.active_path(base))
        try:
            assert ne.run(case.db.url, client=reopened, keywords_only=True, embed=lambda words: case.encode(words),
                          log_dir=case.db.root/'reopened_logs') == 0
        finally:
            reopened.close()
        assert ne.search_keywords('AI', root=case.root, embed=case.encode)
    finally:
        client.close()


@pytest.mark.parametrize('rebuild', [False, True])
@pytest.mark.parametrize('error_type', [asyncio.CancelledError, KeyboardInterrupt])
@pytest.mark.parametrize('close_fails', [False, True])
def test_keyword_interruption_preserves_primary_and_previous_data(keyword_case, monkeypatch, rebuild, error_type, close_fails):
    case = keyword_case
    base = case.root/'keywords'
    assert case.run_default(keywords_only=True, rebuild=True) == 0
    before, pointer = keyword_rows(base), (base/'active.json').read_bytes()
    add_keyword(case, 'new-after-cancel')
    primary = error_type('synthetic original interruption')
    original_client = store.persistent_client
    closes = []

    class Client:
        def __init__(self, path):
            self.real = original_client(path)

        def __getattr__(self, name):
            return getattr(self.real, name)

        def close(self):
            closes.append(True)
            self.real.close()
            if close_fails:
                raise OSError('synthetic secondary close')

    with monkeypatch.context() as patch:
        patch.setattr(store, 'persistent_client', Client)
        encode = Mock(side_effect=primary)
        with pytest.raises(error_type) as caught:
            ne.sync_all_keywords(case.db.engine, base, encode, rebuild=rebuild)
        assert caught.value is primary
        encode.assert_called_once()
        assert closes == [True]
    assert bool(getattr(primary, '__notes__', [])) == close_fails
    assert pointer == (base/'active.json').read_bytes()
    assert before == keyword_rows(base)
    if rebuild:
        statuses = [json.loads(p.read_text()) for p in (base/'generations').glob('*/status.json')]
        assert any(s.get('error_code') == error_type.__name__ and s['status'] == 'failed' for s in statuses)
    assert case.run_default(keywords_only=True, rebuild=rebuild) == 0
    assert 'new-after-cancel' in keyword_rows(base)
    assert ne.search_keywords('AI', root=case.root, embed=case.encode)
    assert case.case.run_pipeline()['status'] == 'SUCCESS'


@pytest.mark.parametrize('borrowed_provider', [False, True])
@pytest.mark.parametrize('borrowed_client', [False, True])
def test_cli_cancellation_survives_database_and_provider_cleanup(keyword_case, monkeypatch, borrowed_provider, borrowed_client):
    case = keyword_case
    primary = asyncio.CancelledError('synthetic original interruption')
    provider = Mock(side_effect=primary)
    provider.usage = None
    provider.close.side_effect = OSError('synthetic provider close')
    dispose = Mock(side_effect=OSError('synthetic database dispose'))
    add_keyword(case, 'new-after-cancel')
    client = store.persistent_client(ks.active_path(case.root/'keywords')) if borrowed_client else None
    try:
        with monkeypatch.context() as patch:
            patch.setattr(ne, 'make_engine', lambda *a: case.db.engine)
            patch.setattr(case.db.engine, 'dispose', dispose)
            patch.setattr(ne, 'openai_embed', lambda: provider)
            with pytest.raises(asyncio.CancelledError) as caught:
                ne.run(case.db.url, client=client, keywords_only=True,
                       embed=provider if borrowed_provider else None, log_dir=case.db.root/'cancel_logs')
            assert caught.value is primary
            dispose.assert_called_once()
            assert provider.close.call_count == int(not borrowed_provider)
            notes = getattr(primary, '__notes__', [])
            assert 'cleanup_failed:database:OSError' in notes
            assert ('cleanup_failed:embedding:OSError' in notes) == (not borrowed_provider)
        assert case.run_default(keywords_only=True) == 0
    finally:
        if client is not None:
            # 빌린 client는 취소 뒤에도 호출자가 계속 사용할 수 있습니다.
            assert client.get_collection('news_keyword').count() > 0
            client.close()


@pytest.mark.parametrize('rebuild', [False, True])
@pytest.mark.parametrize('close_fails', [False, True])
@pytest.mark.parametrize('status_fails', [False, True])
def test_body_cancellation_keeps_pointer_and_primary_through_cleanup(keyword_case, monkeypatch, rebuild, close_fails, status_fails):
    case = keyword_case
    index = store.GenerationStore(case.root)
    before, documents = index.catalog()
    documents[0] = documents[0].model_copy(update={'body': documents[0].body+' 새 본문.'})
    pointer = (case.root/'active.json').read_bytes()
    primary = asyncio.CancelledError('synthetic body cancellation')
    handles = []
    atomic = store.atomic_json

    class Client:
        def __init__(self, path):
            self.real = store.persistent_client(path)
            self.closed = 0
            handles.append(self)

        def __getattr__(self, name):
            return getattr(self.real, name)

        def close(self):
            self.closed += 1
            self.real.close()
            if close_fails:
                raise OSError('synthetic body close')

    def write(path, payload):
        if status_fails and path.name == 'status.json' and payload.get('status') == 'failed':
            raise OSError('synthetic failed status write')
        atomic(path, payload)

    with monkeypatch.context() as patch:
        patch.setattr(store, 'atomic_json', write)
        with pytest.raises(asyncio.CancelledError) as caught:
            store.GenerationStore(case.root, client_factory=Client).publish(
                documents, before['sources'], Mock(side_effect=primary), rebuild=rebuild)
        assert caught.value is primary
    assert len(handles) == (1 if rebuild else 2)
    assert all(h.closed == 1 for h in handles)
    notes = getattr(primary, '__notes__', [])
    assert any(n.startswith('cleanup_failed:body_store:') for n in notes) == close_fails
    assert ('body_status_write_failed:OSError' in notes) == status_fails
    assert pointer == (case.root/'active.json').read_bytes()
    result = index.publish(documents, before['sources'], case.encode, rebuild=rebuild)
    assert result['status'] == 'ready'
    assert ne.search('합성', root=case.root, embed=case.encode)
    assert case.case.run_pipeline()['status'] == 'SUCCESS'


@pytest.mark.parametrize('report_fails', [False, True])
def test_shortnews_cancellation_survives_dispose_and_final_report_failure(keyword_case, monkeypatch, report_fails):
    from backend.app.scheduler.jobs import shortnews_pipeline as pipeline
    case = keyword_case
    primary = asyncio.CancelledError('synthetic vector cancellation')
    original_engine, original_write = pipeline.make_engine, pipeline.analysis.atomic_json
    disposed = []

    def engine(*args, **kwargs):
        real = original_engine(*args, **kwargs)
        close = real.dispose
        def dispose():
            disposed.append(True)
            close()
            raise OSError('synthetic pipeline dispose')
        real.dispose = dispose
        return real

    def write(path, payload):
        if report_fails and path.name == 'pipeline_result.json':
            raise OSError('synthetic pipeline report write')
        original_write(path, payload)

    with monkeypatch.context() as patch:
        patch.setattr(pipeline, 'make_engine', engine)
        patch.setattr(pipeline.analysis, 'atomic_json', write)
        patch.setattr(pipeline.vectors, 'sync_body', Mock(side_effect=primary))
        with pytest.raises(asyncio.CancelledError) as caught:
            case.case.run_pipeline()
        assert caught.value is primary
    assert disposed == [True]
    assert 'cleanup_failed:database:OSError' in primary.__notes__
    assert ('pipeline_report_write_failed:OSError' in primary.__notes__) == report_fails
    if not report_fails:
        report = json.loads((case.db.root/'pipeline/pipeline_result.json').read_text())
        assert report['status'] == 'PARTIAL' and report['error_type'] == 'CancelledError'
    assert case.case.run_pipeline()['status'] == 'SUCCESS'
    assert ne.search_keywords('AI', root=case.root, embed=case.encode)


@pytest.mark.parametrize('failure', ['add_before', 'add_after', 'verify', 'close', 'ready_status', 'pointer_before', 'pointer_after'])
def test_keyword_publication_failure_preserves_state_and_retry(keyword_case, monkeypatch, failure):
    case = keyword_case
    base = case.root/'keywords'
    assert case.run_default(keywords_only=True, rebuild=True) == 0
    before, pointer = keyword_rows(base), (base/'active.json').read_bytes()
    original_client, original_write = store.persistent_client, ks.atomic_json
    pointer_writes = []

    class Client:
        def __init__(self, path):
            self.real = original_client(path)

        def __getattr__(self, name):
            return getattr(self.real, name)

        def create_collection(self, *args, **kwargs):
            col = self.real.create_collection(*args, **kwargs)
            if failure not in ('add_before', 'add_after'):
                return col
            class Collection:
                def __getattr__(self, name):
                    return getattr(col, name)

                def add(self, **values):
                    if failure == 'add_after':
                        col.add(**values)
                    raise OSError('synthetic batch write failure')
            return Collection()

        def close(self):
            self.real.close()
            if failure == 'close':
                raise OSError('synthetic client close failure')

    def write(path, payload):
        if failure == 'ready_status' and path.name == 'status.json' and payload.get('status') == 'ready':
            raise OSError('synthetic ready status failure')
        if path == base/'active.json' and not pointer_writes:
            pointer_writes.append(True)
            if failure == 'pointer_before':
                raise OSError('synthetic pre-publish failure')
            original_write(path, payload)
            if failure == 'pointer_after':
                raise OSError('synthetic post-publish failure')
        else:
            original_write(path, payload)

    with monkeypatch.context() as patch:
        patch.setattr(store, 'persistent_client', Client)
        patch.setattr(ks, 'atomic_json', write)
        if failure == 'verify':
            patch.setattr(ks, 'verify', Mock(side_effect=ValueError('synthetic verification failure')))
        assert case.run_default(keywords_only=True, rebuild=True) == 1
    assert pointer == (base/'active.json').read_bytes()
    assert before == keyword_rows(base)
    assert case.run_default(keywords_only=True, rebuild=True) == 0
    assert ne.search_keywords('AI', root=case.root, embed=case.encode)
    assert case.case.run_pipeline()['status'] == 'SUCCESS'


@pytest.mark.parametrize('kind', ['common_embed', 'classification_embed', 'analysis_llm'])
@pytest.mark.parametrize('error_type', [None, TimeoutError, asyncio.CancelledError])
def test_usage_sink_failure_does_not_mask_provider_error(kind, error_type):
    from backend.app.services import news_embedding, shortnews_analysis
    api = Mock()
    response = NS(data=[NS(index=0, embedding=[1.0]+[0.0]*3071)], usage=NS(total_tokens=3),
                  choices=[NS(finish_reason='stop', message=NS(refusal=None,
                      content=json.dumps({'summary_sentences': ['합성 요약.'], 'keywords': ['AI']})))])
    create = api.chat.completions.create if kind == 'analysis_llm' else api.embeddings.create
    create.return_value = response
    primary = error_type('synthetic original provider error') if error_type else None
    create.side_effect = primary
    sink_error = OSError('synthetic usage sink failure')
    sink = Mock(side_effect=sink_error)
    with pytest.raises(error_type or OSError) as caught:
        if kind == 'analysis_llm':
            shortnews_analysis.ask(api, {'title': '제목', 'body_masked': '합성 본문'},
                                   [{'keyword': 'AI'}], usage_sink=sink)
        else:
            embedding = news_embedding.embed if kind == 'common_embed' else shortnews_analysis.embed
            embedding(api, ['합성 본문'], usage_sink=sink)
    assert caught.value is (primary if primary is not None else sink_error)
    sink.assert_called_once()
    api.close.assert_not_called()


@pytest.mark.parametrize('stage', ['body', 'keywords'])
def test_common_sync_progress_failure_does_not_mask_cancellation(keyword_case, stage):
    case = keyword_case
    before = keyword_rows(case.root/'keywords')
    if stage == 'body':
        with case.db.engine.begin() as conn:
            conn.execute(text("UPDATE article SET body_masked='합성 변경 본문.' WHERE source_code='KOTRA_SHORT_NEWS'"))
    else:
        add_keyword(case, 'new-after-cancel')
    primary = asyncio.CancelledError('synthetic provider cancellation')
    encode = Mock(side_effect=primary)
    encode.usage = {'tokens': 0, 'reported_responses': 0}
    def progress(report):
        if report['usage'].get(stage, {}).get('model_calls'):
            raise OSError('synthetic progress write failure')
    with pytest.raises(asyncio.CancelledError) as caught:
        ne.run_sources(['KOTRA_SHORT_NEWS'], db_url=case.db.url, root=case.root,
                       embed=encode, progress=progress)
    assert caught.value is primary
    assert before == keyword_rows(case.root/'keywords')
    assert case.case.run_pipeline()['status'] == 'SUCCESS'


def test_outer_handled_exception_does_not_hide_new_cleanup_failure(keyword_case, monkeypatch):
    from backend.app.services.rag.resources import closing_owned
    close_error = OSError('synthetic cleanup failure')
    resource = Mock()
    resource.close.side_effect = close_error
    try:
        raise ValueError('unrelated exception in caller')
    except ValueError:
        with pytest.raises(OSError) as caught:
            with closing_owned(resource, name='test'):
                pass
        assert caught.value is close_error
        case = keyword_case
        with monkeypatch.context() as patch:
            patch.setattr(ne, 'make_engine', lambda *a: case.db.engine)
            patch.setattr(case.db.engine, 'dispose', Mock(side_effect=close_error))
            patch.setattr(ne, 'sync_all_keywords', Mock(return_value={'status': 'complete'}))
            assert case.run_default(keywords_only=True) == 1
        assert case.case.run_pipeline()['status'] == 'SUCCESS'


@pytest.mark.parametrize('stage', ['body', 'keywords'])
def test_shortnews_real_vector_path_preserves_cancel_when_all_reports_fail(keyword_case, monkeypatch, stage):
    from backend.app.scheduler.jobs import shortnews_pipeline as pipeline
    from backend.tests.test_shortnews_load import raw
    case = keyword_case
    if stage == 'body':
        case.case.path.write_text(json.dumps([raw(body='취소 후 재시도할 합성 본문.')]))
    else:
        market = next(r for r in case.db.rows('article') if r['external_id'] == 'market001')
        with case.db.engine.begin() as conn:
            conn.execute(text('INSERT INTO article_tag(article_id,keyword_text) VALUES (:id,:word)'),
                         {'id': market['article_id'], 'word': 'new-after-cancel'})
    primary = asyncio.CancelledError('synthetic common SDK cancellation')
    pointer = (case.root/'active.json').read_bytes()
    before = keyword_rows(case.root/'keywords')
    original_write = pipeline.analysis.atomic_json
    def write(path, payload):
        if path.name == 'pipeline_result.json' and payload.get('usage', {}).get(stage, {}).get('model_calls'):
            raise OSError('synthetic usage/progress/final report write failure')
        original_write(path, payload)
    with monkeypatch.context() as patch:
        patch.setattr(case.case.shared_embed, 'side_effect', fixture.COMMON_EMBED)
        patch.setattr(case.case.api.embeddings.create, 'side_effect', primary)
        patch.setattr(pipeline.analysis, 'atomic_json', write)
        with pytest.raises(asyncio.CancelledError) as caught:
            case.case.run_pipeline()
        assert caught.value is primary
    assert 'usage_record_failed:OSError' in primary.__notes__
    assert 'embedding_progress_failed:OSError' in primary.__notes__
    assert 'pipeline_report_write_failed:OSError' in primary.__notes__
    assert before == keyword_rows(case.root/'keywords')
    if stage == 'body':
        assert pointer == (case.root/'active.json').read_bytes()
    assert case.case.run_pipeline()['status'] == 'SUCCESS'
    assert ne.search('합성', root=case.root, embed=case.encode)
    assert ne.search_keywords('AI', root=case.root, embed=case.encode)


@pytest.mark.parametrize('model_changed,rebuild', [(False, False), (False, True), (True, True)])
@pytest.mark.parametrize('rollback_failure', ['before_replace', 'after_replace', 'pointer_read'])
def test_body_rollback_failure_keeps_ready_catalog_and_allows_rebuild(keyword_case, monkeypatch, model_changed, rebuild, rollback_failure):
    from backend.app.services import news_embedding
    case = keyword_case
    pointer = case.root/'active.json'
    previous, documents = store.GenerationStore(case.root).catalog()
    old_documents = {d.key: d.model_dump() for d in documents}
    old_keywords = keyword_rows(case.root/'keywords')
    original_write, original_read = store.atomic_json, type(pointer).read_text
    publication_error = OSError('synthetic post-replace failure')
    writes, read_failed = [], []
    if model_changed:
        for module in (store, ne, news_embedding):
            monkeypatch.setattr(module, 'MODEL', 'rollback-review-new-model')
            monkeypatch.setattr(module, 'DIMENSIONS', 8)
        case.encode.side_effect = lambda words: [[1.0]+[0.0]*7 for _ in words]
        case.case.shared_embed.side_effect = lambda api, words, **kwargs: [[1.0]+[0.0]*7 for _ in words]

    def write(path, payload):
        if path == pointer:
            writes.append(payload['generation'])
            if len(writes) == 1:
                original_write(path, payload)
                raise publication_error
            if rollback_failure == 'after_replace':
                original_write(path, payload)
            raise OSError('synthetic pointer restore failure')
        original_write(path, payload)

    def read(path, *args, **kwargs):
        if path == pointer and writes and not read_failed:
            read_failed.append(True)
            raise OSError('synthetic rollback pointer read failure')
        return original_read(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(store, 'atomic_json', write)
        if rollback_failure == 'pointer_read':
            patch.setattr(type(pointer), 'read_text', read)
        assert case.run_default(rebuild=rebuild) == 1
    assert 'body_pointer_rollback_failed:OSError' in publication_error.__notes__
    assert 'body_ready_generation_preserved_after_rollback_failure' in publication_error.__notes__
    staged = json.loads((case.root/'generations'/writes[0]/'status.json').read_text())
    assert staged['status'] == 'ready'
    active, after_failure = store.GenerationStore(case.root).catalog()
    expected = previous['generation'] if rollback_failure == 'after_replace' else writes[0]
    assert active['generation'] == expected
    assert {d.key: d.model_dump() for d in after_failure} == old_documents
    assert old_keywords == keyword_rows(case.root/'keywords')
    if model_changed and rollback_failure == 'after_replace':
        with pytest.raises(ValueError, match='incompatible_dimensions'):
            ne.search('합성', root=case.root, embed=case.encode)
    else:
        assert ne.search('합성', root=case.root, embed=case.encode)
    case.encode.reset_mock()
    assert case.run_default(rebuild=True, dry_run=True) == 0
    case.encode.assert_not_called()
    assert case.run_default(rebuild=True) == 0
    rebuilt, after_retry = store.GenerationStore(case.root).catalog()
    assert rebuilt['status'] == 'ready' and rebuilt['reused'] == 0
    assert {d.key: d.model_dump() for d in after_retry} == old_documents
    assert ne.search('합성', root=case.root, embed=case.encode)
    assert ne.search_keywords('AI', root=case.root, embed=case.encode)
    assert case.case.run_pipeline()['status'] == 'SUCCESS'
    assert case.run_default() == 0


@pytest.mark.parametrize('unlink_applied', [False, True])
def test_first_body_publication_rollback_unlink_failure_allows_retry(tmp_path, monkeypatch, unlink_applied):
    from backend.tests.news_index_fixtures import document, vectors
    index = store.GenerationStore(tmp_path/'index', dimensions=8)
    article = document(source='KOTRA_MARKET')
    pointer = index.root/'active.json'
    original_write, original_unlink = store.atomic_json, type(pointer).unlink
    publication_error = OSError('synthetic first publication failure')
    generation = []

    def write(path, payload):
        original_write(path, payload)
        if path == pointer:
            generation.append(payload['generation'])
            raise publication_error

    def unlink(path, *args, **kwargs):
        if path == pointer:
            if unlink_applied:
                original_unlink(path, *args, **kwargs)
            raise OSError('synthetic initial pointer unlink failure')
        return original_unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(store, 'atomic_json', write)
        patch.setattr(type(pointer), 'unlink', unlink)
        with pytest.raises(OSError) as caught:
            index.publish([article], ['KOTRA_MARKET'], vectors, rebuild=True)
        assert caught.value is publication_error
    assert json.loads((index.root/'generations'/generation[0]/'status.json').read_text())['status'] == 'ready'
    if not unlink_applied:
        assert index.catalog()[0]['generation'] == generation[0]
    else:
        assert index.catalog() == (None, [])
    assert index.publish([article], ['KOTRA_MARKET'], vectors, rebuild=True)['status'] == 'ready'


@pytest.mark.parametrize('failure', ['embedding', 'ready_before_write', 'ready_after_write'])
def test_body_failure_before_ready_persisted_still_marks_staged_generation_failed(keyword_case, monkeypatch, failure):
    case = keyword_case
    pointer = case.root/'active.json'
    before_pointer = pointer.read_bytes()
    before_generations = set((case.root/'generations').iterdir())
    primary = OSError('synthetic failure before ready persistence completed')
    original_write, original_read = store.atomic_json, type(pointer).read_text
    failed, read_failed = [], []

    def encode(words):
        failed.append(True)
        raise primary

    def write(path, payload):
        if path.name == 'status.json' and payload.get('status') == 'ready':
            failed.append(True)
            if failure == 'ready_after_write':
                original_write(path, payload)
            raise primary
        original_write(path, payload)

    def read(path, *args, **kwargs):
        if path == pointer and failed and not read_failed:
            read_failed.append(True)
            raise OSError('synthetic rollback read failure')
        return original_read(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(type(pointer), 'read_text', read)
        if failure == 'embedding':
            patch.setattr(case.encode, 'side_effect', encode)
        else:
            patch.setattr(store, 'atomic_json', write)
        assert case.run_default(rebuild=True) == 1
    assert read_failed
    assert 'body_pointer_rollback_failed:OSError' in primary.__notes__
    assert 'body_ready_generation_preserved_after_rollback_failure' not in primary.__notes__
    staged = set((case.root/'generations').iterdir()) - before_generations
    assert len(staged) == 1
    assert json.loads((staged.pop()/'status.json').read_text())['status'] == 'failed'
    assert before_pointer == pointer.read_bytes()
    assert ne.search('합성', root=case.root, embed=case.encode)
    assert case.run_default(rebuild=True) == 0


@pytest.mark.parametrize('kind',['article','keyword'])
@pytest.mark.parametrize('borrowed_client',[False,True])
@pytest.mark.parametrize('borrowed_provider',[False,True])
@pytest.mark.parametrize('close_fails',[False,True])
@pytest.mark.parametrize('error',[TimeoutError,asyncio.CancelledError])
def test_public_search_preserves_interruption_and_closes_only_owned(tmp_path,monkeypatch,error,borrowed_client,borrowed_provider,close_fails,kind):
    from backend.app.schemas.rag import ALL_SOURCES
    primary=error('original interruption');provider=Mock(side_effect=primary)
    client=Mock();col=client.get_collection.return_value
    col.metadata={'embedding_model':ne.MODEL,'dimensions':ne.DIMENSIONS}
    col.get_model.return_value=NS(dimension=ne.DIMENSIONS)
    col.get.return_value={'ids':['AI'],'embeddings':[[1.0]+[0.0]*(ne.DIMENSIONS-1)]}
    if close_fails:
        client.close.side_effect=OSError('secondary client close')
        provider.close.side_effect=OSError('secondary provider close')
    snapshot=NS(client=client,sources=set(ALL_SOURCES),collection=client.get_collection)
    monkeypatch.setattr(store,'GenerationStore',lambda *a,**kw:NS(snapshot=lambda:snapshot))
    monkeypatch.setattr(ne,'openai_embed',lambda:provider)
    (tmp_path/'chroma.sqlite3').touch()
    monkeypatch.setattr(store,'resolve_keyword_root',lambda *a:tmp_path)
    monkeypatch.setattr(store,'persistent_client',lambda *a:client)
    call=ne.search if kind=='article' else ne.search_keywords
    with pytest.raises(error) as caught:
        call('합성 질의',client=client if borrowed_client else None,embed=provider if borrowed_provider else None)
    assert caught.value is primary
    assert client.close.call_count==int(not borrowed_client)
    assert provider.close.call_count==int(not borrowed_provider)
    if close_fails:
        assert len(getattr(primary,'__notes__',[]))==int(not borrowed_client)+int(not borrowed_provider)

@pytest.mark.parametrize('published',[True,False])
def test_default_embed_runs_keywords_after_published_cleanup_failure(monkeypatch,published,tmp_path):
    from backend.app.scheduler.jobs import rag_index
    report={'status':'partial' if published else 'failed','error_code':'resource_cleanup_failed'}
    if published:report['index']={'status':'ready','generation':'synthetic'}
    monkeypatch.setattr(rag_index,'run',lambda *a,**k:report)
    monkeypatch.setenv('NEWS_VECTOR_ROOT',str(tmp_path/'index'))
    keywords=Mock(return_value=0);monkeypatch.setattr(ne,'_run_keywords',keywords)
    assert ne._run(log_dir=tmp_path,embed=Mock())==1
    assert keywords.call_count==int(published)
    if published:
        assert keywords.call_args.args[1]==tmp_path/'index'
        assert keywords.call_args.kwargs=={'rebuild':False,'dry_run':False}


@pytest.mark.parametrize('restore_applied', [False, True])
def test_keyword_failed_rollback_keeps_verified_generation_readable(keyword_case, monkeypatch, restore_applied):
    case = keyword_case
    base = case.root / 'keywords'
    assert case.run_default(keywords_only=True, rebuild=True) == 0
    before = keyword_rows(base)
    original_write = ks.atomic_json
    publication_error = OSError('synthetic publication failure')
    writes = []

    def write(path, payload):
        if path == base / 'active.json':
            writes.append(payload['generation'])
            if len(writes) == 1:
                original_write(path, payload)
                raise publication_error
            if restore_applied:
                original_write(path, payload)
            raise OSError('synthetic rollback failure')
        original_write(path, payload)

    with monkeypatch.context() as patch:
        patch.setattr(ks, 'atomic_json', write)
        assert case.run_default(keywords_only=True, rebuild=True) == 1
    assert 'keyword_pointer_rollback_failed:OSError' in publication_error.__notes__
    assert 'keyword_ready_generation_preserved_after_rollback_failure' in publication_error.__notes__
    assert json.loads((base / 'generations' / writes[0] / 'status.json').read_text())['status'] == 'ready'
    assert keyword_rows(base) == before
    assert ne.search_keywords('AI', root=case.root, embed=case.encode)
    assert case.run_default(keywords_only=True, rebuild=True) == 0
    assert case.case.run_pipeline()['status'] == 'SUCCESS'
