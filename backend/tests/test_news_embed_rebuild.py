"""기본 CLI 경로의 재구축: 임시 SQLite/Chroma와 가짜 임베딩만 사용합니다."""
import hashlib
import json
import os
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from sqlalchemy import text

from backend.app.scheduler.jobs import news_embed, rag_index
from backend.app.services.rag import store
from backend.app.services.rag.keyword_store import active_path as keyword_path
from backend.app.services import news_embedding, shortnews_analysis
from backend.tests import test_shortnews_pipeline as fixture

COMMON_EMBED = news_embedding.embed
CLASSIFICATION_EMBED = shortnews_analysis.embed


class RebuildTest(unittest.TestCase):
    def setUp(self):
        self.case = fixture.PipelineTest()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.db = self.case.db
        self.root = self.db.root / 'vectors'
        self.encode = Mock(side_effect=lambda texts: [[1.0] + [0.0] * 3071 for _ in texts])
        self.assertEqual(self.case.run_pipeline()['status'], 'SUCCESS')
        self.env = patch.dict(os.environ, {'NEWS_VECTOR_ROOT': str(self.root)})
        self.env.start()
        self.addCleanup(self.env.stop)

    def run_default(self, **kwargs):
        return news_embed.run(self.db.url, embed=self.encode, raw_market=False, **kwargs)

    def add_article(self, source='KOTRA_MARKET', external='market001', body='시장 본문. ' * 220):
        from backend.app.db.batch_log import ensure_source, Source
        from backend.app.db.session import session_scope
        from backend.app.scheduler.jobs.shortnews_load import reflect
        with session_scope(self.db.engine) as session:
            ensure_source(session, Source(source, '합성 출처', {}))
            row = {k: v for k, v in self.db.rows('article')[0].items() if k != 'article_id'}
            row.update(source_code=source, external_id=external, title='합성 기사', body_masked=body)
            session.execute(reflect(self.db.engine, 'article').insert().values(**row))
        article = next(r for r in self.db.rows('article') if r['external_id'] == external)
        with self.db.engine.begin() as connection:
            connection.execute(text('INSERT INTO article_tag (article_id, keyword_text) VALUES (:id, :word)'),
                               {'id': article['article_id'], 'word': external})
        return article

    def seed_market(self):
        row = self.add_article()
        rag_index.refresh(self.db.engine, ['KOTRA_MARKET'], store=store.GenerationStore(self.root),
                          embed=self.encode, raw_market=False,
                          supplements={('KOTRA_MARKET', row['external_id']): {
                              'body_masked': row['body_masked'], 'body_available': True}})
        self.encode.reset_mock()
        return row

    def catalog(self):
        return store.GenerationStore(self.root).catalog()

    def digest(self, folder=None):
        folder = folder or self.root
        return {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in folder.rglob('*') if p.is_file()}

    def test_default_rebuild_reembeds_all_body_and_keywords_then_normal_run_reuses(self):
        self.seed_market()
        self.assertEqual(self.run_default(), 0)
        self.encode.reset_mock()
        old, old_docs = self.catalog()
        old_files = self.digest(self.root / 'generations' / old['generation'])
        with self.assertLogs(news_embed.log, level='INFO') as logs:
            self.assertEqual(self.run_default(rebuild=True), 0)
        rebuilt, documents = self.catalog()
        self.assertNotEqual(old['generation'], rebuilt['generation'])
        self.assertEqual(rebuilt['reused'], 0)
        self.assertEqual(rebuilt['embedded'], rebuilt['chunks'])
        self.assertEqual(rebuilt['sources'], old['sources'])
        self.assertEqual([d.model_dump() for d in documents], [d.model_dump() for d in old_docs])
        inputs = [t for call in self.encode.call_args_list for t in call.args[0]]
        expected = [t for doc in documents for _, _, _, t in store.article_records(doc) if t is not None]
        self.assertCountEqual(inputs, expected + ['AI', 'market001'])
        self.assertEqual(old_files, self.digest(self.root / 'generations' / old['generation']))
        self.assertTrue(any('키워드 색인' in line for line in logs.output))
        self.encode.reset_mock()
        self.assertEqual(self.run_default(), 0)
        self.encode.assert_not_called()

    def test_rebuild_dry_run_counts_equal_apply_and_changes_no_files_or_db(self):
        self.seed_market()
        before = self.digest()
        rows = self.db.rows('article')
        with self.assertLogs(news_embed.log, level='INFO') as logs, \
                patch('chromadb.PersistentClient', side_effect=AssertionError('dry-run cannot open Chroma')):
            self.assertEqual(self.run_default(rebuild=True, dry_run=True), 0)
        self.encode.assert_not_called()
        self.assertEqual(before, self.digest())
        self.assertEqual(rows, self.db.rows('article'))
        self.assertTrue(any("'rebuild': True" in line and "'reused': 0" in line for line in logs.output))
        self.assertTrue(any("'distinct_keywords': 2" in line and "'embedded': 2" in line for line in logs.output))
        plan = rag_index.refresh(self.db.engine, ['KOTRA_MARKET'], store=store.GenerationStore(self.root),
                                 embed=self.encode, raw_market=False, dry_run=True, rebuild=True)
        self.assertEqual(self.run_default(rebuild=True), 0)
        actual, _ = self.catalog()
        for key in ('articles', 'chunks', 'embedded', 'reused', 'embedding_chars', 'sources'):
            self.assertEqual(plan[key], actual[key], key)

    def test_rebuild_dry_run_new_root_creates_no_directory(self):
        empty = self.db.root / 'not-created'
        with patch.dict(os.environ, {'NEWS_VECTOR_ROOT': str(empty)}):
            self.assertEqual(self.run_default(rebuild=True, dry_run=True), 0)
        self.assertFalse(empty.exists())
        self.encode.assert_not_called()

    def test_new_model_and_dimensions_reembed_preserved_sources_without_opening_old_vectors(self):
        self.seed_market()
        self.assertEqual(self.run_default(), 0)
        previous, documents = self.catalog()
        old_files = self.digest(self.root / 'generations' / previous['generation'])
        encoder = Mock(side_effect=lambda texts: [[0.0, 1.0] + [0.0] * 6 for _ in texts])
        with patch.object(store, 'MODEL', 'fake-new-model'), patch.object(store, 'DIMENSIONS', 8), \
                patch.object(news_embed, 'MODEL', 'fake-new-model'), patch.object(news_embed, 'DIMENSIONS', 8):
            self.assertEqual(news_embed.run(self.db.url, embed=encoder, raw_market=False), 1)
            encoder.assert_not_called()
            with patch.object(store.GenerationStore, 'snapshot', side_effect=AssertionError('old vectors forbidden')):
                self.assertEqual(news_embed.run(self.db.url, rebuild=True, dry_run=True,
                                                embed=encoder, raw_market=False), 0)
                encoder.assert_not_called()
                self.assertEqual(news_embed.run(self.db.url, rebuild=True, embed=encoder, raw_market=False), 0)
            current, after = self.catalog()
            self.assertEqual(current['dimensions'], 8)
            self.assertEqual(current['embedding_model'], 'fake-new-model')
            self.assertEqual(current['reused'], 0)
            self.assertEqual(current['sources'], previous['sources'])
            self.assertEqual([d.model_dump() for d in after], [d.model_dump() for d in documents])
            snapshot = store.GenerationStore(self.root).snapshot()
            try:
                col = snapshot.collection('kotra_article')
                data = store.read_all(col, ['embeddings'])
                self.assertTrue(all(len(v) == 8 and v[1] == 1 for v in data['embeddings']))
            finally:
                snapshot.client.close()
            client = store.persistent_client(keyword_path(self.root / 'keywords'))
            try:
                col = client.get_collection('news_keyword', embedding_function=None)
                self.assertEqual(col.metadata['embedding_model'], 'fake-new-model')
                self.assertTrue(all(len(v) == 8 for v in col.get(include=['embeddings'])['embeddings']))
            finally:
                client.close()
        self.assertEqual(old_files, self.digest(self.root / 'generations' / previous['generation']))

    def test_same_dimension_model_change_also_requires_rebuild(self):
        self.seed_market()
        before = (self.root / 'active.json').read_bytes()
        with patch.object(store, 'MODEL', 'fake-model-same-dimensions'), \
                patch.object(news_embed, 'MODEL', 'fake-model-same-dimensions'):
            self.assertEqual(self.run_default(), 1)
            self.assertEqual(before, (self.root / 'active.json').read_bytes())
            self.encode.assert_not_called()
            self.assertEqual(self.run_default(rebuild=True), 0)

    def test_all_four_sources_are_preserved_and_reembedded(self):
        self.seed_market()
        self.add_article(source='HANKYUNG', external='paper001')
        self.add_article(source='FNNEWS', external='paper002')
        rag_index.refresh(self.db.engine, ['HANKYUNG', 'FNNEWS'], store=store.GenerationStore(self.root),
                          embed=self.encode, raw_market=False)
        before, documents = self.catalog()
        self.encode.reset_mock()
        self.assertEqual(self.run_default(rebuild=True), 0)
        after, rebuilt = self.catalog()
        self.assertEqual(set(after['sources']), {'KOTRA_MARKET', 'KOTRA_SHORT_NEWS', 'HANKYUNG', 'FNNEWS'})
        self.assertEqual(after['sources'], before['sources'])
        self.assertEqual(after['reused'], 0)
        self.assertEqual(after['embedded'], after['chunks'])
        self.assertEqual([d.model_dump() for d in rebuilt], [d.model_dump() for d in documents])

    def test_hidden_and_deleted_market_articles_do_not_return_during_rebuild(self):
        row = self.seed_market()
        deleted = self.add_article(external='removed001')
        hidden = self.add_article(external='hidden001')
        supplements = {('KOTRA_MARKET', r['external_id']): {
            'body_masked': r['body_masked'], 'body_available': True} for r in (row, deleted, hidden)}
        rag_index.refresh(self.db.engine, ['KOTRA_MARKET'], store=store.GenerationStore(self.root),
                          embed=self.encode, raw_market=False, supplements=supplements)
        with self.db.engine.begin() as connection:
            connection.execute(text('DELETE FROM article_tag WHERE article_id=:id'), {'id': deleted['article_id']})
            connection.execute(text('DELETE FROM article WHERE article_id=:id'), {'id': deleted['article_id']})
            connection.execute(text("UPDATE article SET visibility_status='HIDDEN' WHERE article_id=:id"),
                               {'id': hidden['article_id']})
        self.assertEqual(self.run_default(rebuild=True), 0)
        _, documents = self.catalog()
        self.assertEqual({d.external_id for d in documents}, {'001', 'market001'})

    def test_catalog_loss_is_failure_instead_of_dropping_other_sources(self):
        self.seed_market()
        report, documents = self.catalog()
        catalog = self.root / 'generations' / report['generation'] / 'articles.jsonl'
        catalog.write_text(documents[0].model_dump_json() + '\n', encoding='utf-8')
        before = (self.root / 'active.json').read_bytes()
        for dry_run in (True, False):
            self.assertEqual(self.run_default(rebuild=True, dry_run=dry_run), 1)
        self.encode.assert_not_called()
        self.assertEqual(before, (self.root / 'active.json').read_bytes())

    def test_missing_body_marker_is_not_counted_as_an_embedding(self):
        self.add_article(external='unknown-body')
        self.encode.reset_mock()
        plan = rag_index.refresh(self.db.engine, ['KOTRA_MARKET'], store=store.GenerationStore(self.root),
                                 embed=self.encode, raw_market=False, dry_run=True, rebuild=True)
        self.assertEqual(plan['missing_body'], 1)
        actual = rag_index.refresh(self.db.engine, ['KOTRA_MARKET'], store=store.GenerationStore(self.root),
                                   embed=self.encode, raw_market=False, rebuild=True)
        self.assertEqual(plan['embedded'], actual['embedded'])
        self.assertEqual(plan['chunks'], actual['chunks'])
        self.assertEqual(len([t for call in self.encode.call_args_list for t in call.args[0]]), plan['embedded'])

    def test_embedding_failure_keeps_active_generation_and_does_not_start_keywords(self):
        self.seed_market()
        before = (self.root / 'active.json').read_bytes()
        self.encode.side_effect = RuntimeError('synthetic embedding failure')
        with patch.object(news_embed, 'sync_all_keywords', wraps=news_embed.sync_all_keywords) as keywords:
            self.assertEqual(self.run_default(rebuild=True), 1)
            keywords.assert_not_called()
        self.assertEqual(before, (self.root / 'active.json').read_bytes())

    def test_bad_new_vector_does_not_publish(self):
        self.seed_market()
        before = (self.root / 'active.json').read_bytes()
        self.encode.side_effect = lambda texts: [[0.0] * 3072 for _ in texts]
        self.assertEqual(self.run_default(rebuild=True), 1)
        self.assertEqual(before, (self.root / 'active.json').read_bytes())

    def test_keyword_failure_reports_partial_completion_and_returns_failure(self):
        self.seed_market()
        before = (self.root / 'active.json').read_bytes()
        with patch.object(news_embed, 'sync_all_keywords', side_effect=RuntimeError('synthetic keyword failure')), \
                self.assertLogs(news_embed.log, level='INFO') as logs:
            self.assertEqual(self.run_default(rebuild=True), 1)
        self.assertNotEqual(before, (self.root / 'active.json').read_bytes())
        self.assertTrue(any('부분 완료' in line for line in logs.output))

    def test_keywords_only_rebuild_leaves_body_pointer_unchanged(self):
        before = (self.root / 'active.json').read_bytes()
        self.assertEqual(self.run_default(keywords_only=True, rebuild=True), 0)
        self.assertEqual(before, (self.root / 'active.json').read_bytes())
        self.assertEqual(self.encode.call_args_list[0].args[0], ['AI'])

    def test_empty_keyword_rebuild_removes_old_keywords_without_an_embedding_call(self):
        with self.db.engine.begin() as connection:
            connection.execute(text('DELETE FROM article_tag WHERE keyword_text IS NOT NULL'))
        self.assertEqual(self.run_default(keywords_only=True, rebuild=True), 0)
        self.encode.assert_not_called()
        client = store.persistent_client(keyword_path(self.root / 'keywords'))
        try:
            self.assertEqual(client.get_collection('news_keyword', embedding_function=None).count(), 0)
        finally:
            client.close()

    def test_rebuild_cannot_adopt_another_database(self):
        identity = self.root / 'database.json'
        identity.write_text(json.dumps({'version': 1, 'database_id': 'a' * 64}), encoding='utf-8')
        before = (self.root / 'active.json').read_bytes()
        for dry_run in (True, False):
            self.assertEqual(self.run_default(rebuild=True, dry_run=dry_run), 1)
        self.encode.assert_not_called()
        self.assertEqual(before, (self.root / 'active.json').read_bytes())

    def test_cli_forwards_rebuild_and_dry_run(self):
        with patch('sys.argv', ['news_embed', '--rebuild', '--dry-run']), \
                patch.object(news_embed, 'run', return_value=0) as run:
            with self.assertRaises(SystemExit) as exit:
                news_embed.main()
        self.assertEqual(exit.exception.code, 0)
        run.assert_called_once_with(None, True, True, keywords_only=False)


class StoreRebuildTest(unittest.TestCase):
    def test_embedding_provider_requests_configured_model_and_dimensions(self):
        from types import SimpleNamespace
        api = Mock()
        api.embeddings.create.return_value = SimpleNamespace(
            data=[SimpleNamespace(index=0, embedding=[1.0] + [0.0] * 7)],
            usage=SimpleNamespace(total_tokens=2))
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'synthetic-test-key'}), \
                patch.object(news_embed, 'OpenAI', return_value=api), \
                patch.object(news_embed, 'MODEL', 'fake-new-model'), patch.object(news_embed, 'DIMENSIONS', 8):
            provider = news_embed.openai_embed()
            try:
                self.assertEqual(len(provider(['합성 입력'])[0]), 8)
                api.embeddings.create.assert_called_once_with(
                    model='fake-new-model', dimensions=8, input=['합성 입력'])
            finally:
                provider.close()
        api.close.assert_called_once()

    def test_rebuild_failure_after_pointer_write_restores_previous_without_old_snapshot(self):
        import tempfile
        from pathlib import Path
        from backend.tests.news_index_fixtures import document, vectors
        with tempfile.TemporaryDirectory() as directory:
            index = store.GenerationStore(Path(directory) / 'index', dimensions=8)
            article = document(source='KOTRA_MARKET')
            index.publish([article], ['KOTRA_MARKET'], vectors)
            before = (index.root / 'active.json').read_bytes()
            original = store.atomic_json
            def write_then_fail(path, value):
                original(path, value)
                if path == index.root / 'active.json' and value['generation'] != json.loads(before)['generation']:
                    raise RuntimeError('synthetic publish failure')
            with patch.object(store, 'atomic_json', side_effect=write_then_fail), \
                    self.assertRaisesRegex(RuntimeError, 'publish failure'):
                index.publish([article], ['KOTRA_MARKET'], vectors, rebuild=True)
            self.assertEqual(before, (index.root / 'active.json').read_bytes())


class ModelTransitionTest(unittest.TestCase):
    def test_pipeline_after_model_change_uses_separate_models_and_dimensions_for_body_and_new_keyword(self):
        from types import SimpleNamespace as NS
        for dimensions in (3072, 8):
            with self.subTest(dimensions=dimensions):
                case = RebuildTest()
                case.setUp()
                try:
                    case.seed_market()
                    case.encode.side_effect = lambda texts: [[0.0, 1.0] + [0.0]*(dimensions-2) for _ in texts]
                    with patch.object(store, 'MODEL', 'new-common-model'), patch.object(store, 'DIMENSIONS', dimensions), \
                            patch.object(news_embed, 'MODEL', 'new-common-model'), patch.object(news_embed, 'DIMENSIONS', dimensions), \
                            patch.object(news_embedding, 'MODEL', 'new-common-model'), patch.object(news_embedding, 'DIMENSIONS', dimensions):
                        self.assertEqual(case.run_default(rebuild=True), 0)
                        old_catalog = {d.key: d.model_dump() for d in case.catalog()[1] if d.source_code.value == 'KOTRA_MARKET'}
                        # 가짜 API 응답만 사용하고 실제 두 제공자의 요청 모델·차원·usage 처리를 실행합니다.
                        def response(*, model, dimensions, input):
                            pair = [0.0, 1.0] if model == 'new-common-model' else [1.0, 0.0]
                            return NS(data=[NS(index=i, embedding=pair+[0.0]*(dimensions-2)) for i in range(len(input))],
                                      usage=NS(total_tokens=3*len(input), prompt_tokens=3*len(input)))
                        case.case.api.embeddings.create.side_effect = response
                        case.case.embed.side_effect = CLASSIFICATION_EMBED
                        case.case.shared_embed.side_effect = COMMON_EMBED
                        candidates = fixture.keywords.catalog([
                            {'keyword_text': word, 'canonical_text': None, 'category_id': 1, 'status': 'CLASSIFIED'}
                            for word in ('AI', '새대표어')],
                            [{'category_id': 1, 'major_name': '산업', 'minor_name': '품목'}])
                        case.case.ask.return_value = {'summary_sentences': ['새 요약'], 'keywords': ['새대표어']}
                        case.case.path.write_text(json.dumps([fixture.fixture.raw(body='모델 전환 후 갱신된 본문')]), encoding='utf-8')
                        with patch.object(fixture.pipeline.keywords, 'read_catalog', return_value=candidates):
                            result = case.case.run_pipeline()
                            self.assertEqual(result['status'], 'SUCCESS', result)
                            api_calls = list(case.case.api.embeddings.create.call_args_list)
                            self.assertTrue(any(c.kwargs['model'] == shortnews_analysis.MODEL and c.kwargs['dimensions'] == 3072
                                                for c in api_calls))
                            self.assertTrue(any(c.kwargs['model'] == 'new-common-model' and c.kwargs['dimensions'] == dimensions
                                                for c in api_calls))
                            body = store.GenerationStore(case.root).snapshot()
                            try:
                                col = body.collection(news_embed.ARTICLE_COLLECTION)
                                rows = col.get(where={'source_code': 'KOTRA_SHORT_NEWS'}, include=['embeddings'])
                                self.assertTrue(all(len(v) == dimensions and list(v[:2]) == [0.0, 1.0] for v in rows['embeddings']))
                                self.assertEqual(col.metadata['embedding_model'], 'new-common-model')
                            finally:
                                body.client.close()
                            client = store.persistent_client(keyword_path(case.root/'keywords'))
                            try:
                                col = client.get_collection('news_keyword', embedding_function=None)
                                row = col.get(ids=['새대표어'], include=['embeddings'])
                                self.assertEqual(list(row['embeddings'][0][:2]), [0.0, 1.0])
                                self.assertEqual(len(row['embeddings'][0]), dimensions)
                            finally:
                                client.close()
                            self.assertEqual(result['usage']['body']['model'], 'new-common-model')
                            self.assertEqual(result['usage']['keywords']['model'], 'new-common-model')
                            self.assertEqual(result['usage']['candidate_query']['model'], shortnews_analysis.MODEL)
                            self.assertEqual(result['usage']['body']['tokens'], result['chroma_result']['usage']['body']['tokens'])
                            self.assertEqual(old_catalog, {d.key: d.model_dump() for d in case.catalog()[1]
                                                          if d.source_code.value == 'KOTRA_MARKET'})
                            count = case.case.api.embeddings.create.call_count
                            self.assertEqual(case.case.run_pipeline()['status'], 'SUCCESS')
                            self.assertEqual(case.case.api.embeddings.create.call_count, count)
                finally:
                    case.doCleanups()


class KeywordRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.case = RebuildTest()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.case.seed_market()
        self.assertEqual(self.case.run_default(), 0)
        self.root = self.case.root
        self.base = self.root/'keywords'
        self.case.encode.reset_mock()

    def records(self):
        client = store.persistent_client(keyword_path(self.base))
        try:
            col = client.get_collection('news_keyword', embedding_function=None)
            data = store.read_all(col, ['documents', 'metadatas', 'embeddings'])
            return dict(zip(data['ids'], [(d, m, list(v)) for d, m, v in
                zip(data['documents'], data['metadatas'], data['embeddings'])]))
        finally:
            client.close()

    def rebuild(self):
        return self.case.run_default(keywords_only=True, rebuild=True)

    def test_full_rebuild_real_keyword_api_failure_keeps_old_keywords_and_reports_partial(self):
        before = self.records()
        body = (self.root/'active.json').read_bytes()
        def encode(texts):
            if any(not text.startswith('[') for text in texts):
                raise RuntimeError('synthetic keyword API failure')
            return [[1.0]+[0.0]*3071 for _ in texts]
        self.case.encode.side_effect = encode
        with self.assertLogs(news_embed.log, level='INFO') as logs:
            self.assertEqual(self.case.run_default(rebuild=True), 1)
        self.assertTrue(any('부분 완료' in row for row in logs.output))
        self.assertNotEqual(body, (self.root/'active.json').read_bytes())
        self.assertEqual(before, self.records())
        self.case.encode.side_effect = lambda texts: [[1.0]+[0.0]*3071 for _ in texts]
        self.assertEqual(self.rebuild(), 0)
        self.assertEqual(before, self.records())

    def test_late_batch_failure_preserves_existing_keyword_generation(self):
        self.assertEqual(self.rebuild(), 0)
        pointer = (self.base/'active.json').read_bytes()
        before = self.records()
        counts = {f'keyword-{n:04d}': 1 for n in range(501)}
        def encode(texts):
            if 'keyword-0500' in texts:
                raise RuntimeError('synthetic second batch failure')
            return [[1.0]+[0.0]*3071 for _ in texts]
        self.case.encode.side_effect = encode
        with patch.object(news_embed, 'keyword_article_counts', return_value=counts):
            self.assertEqual(self.rebuild(), 1)
        self.assertEqual(pointer, (self.base/'active.json').read_bytes())
        self.assertEqual(before, self.records())

    def test_verification_failure_keeps_existing_keywords(self):
        from backend.app.services.rag import keyword_store
        before = self.records()
        with patch.object(keyword_store, 'verify', side_effect=ValueError('synthetic stored data mismatch')):
            self.assertEqual(self.rebuild(), 1)
        self.assertEqual(before, self.records())

    def test_invalid_keyword_vectors_do_not_replace_existing_keywords(self):
        before = self.records()
        for vector in ([0.0]*3072, [float('nan')]+[0.0]*3071, [1.0, 0.0]):
            with self.subTest(vector_length=len(vector)):
                self.case.encode.side_effect = lambda texts: [vector for _ in texts]
                self.assertEqual(self.rebuild(), 1)
                self.assertEqual(before, self.records())

    def test_close_failure_before_publish_keeps_existing_keywords(self):
        before = self.records()
        original = store.persistent_client
        def factory(path):
            real = original(path)
            client = Mock(wraps=real)
            def close():
                real.close()
                raise RuntimeError('synthetic keyword close failure')
            client.close.side_effect = close
            return client
        with patch.object(store, 'persistent_client', side_effect=factory):
            self.assertEqual(self.rebuild(), 1)
        self.assertEqual(before, self.records())

    def test_pointer_error_after_replace_restores_legacy_and_generation_paths(self):
        from backend.app.services.rag import keyword_store
        for existing_pointer in (False, True):
            with self.subTest(existing_pointer=existing_pointer):
                if existing_pointer:
                    self.assertEqual(self.rebuild(), 0)
                before = self.records()
                pointer = self.base/'active.json'
                previous = pointer.read_bytes() if pointer.exists() else None
                original = keyword_store.atomic_json
                raised = []
                def write_then_fail(path, value):
                    original(path, value)
                    if path == pointer and not raised:
                        raised.append(True)
                        raise OSError('synthetic failure after pointer replace')
                with patch.object(keyword_store, 'atomic_json', side_effect=write_then_fail):
                    self.assertEqual(self.rebuild(), 1)
                self.assertEqual(previous, pointer.read_bytes() if pointer.exists() else None)
                self.assertEqual(before, self.records())

    def test_new_model_failure_keeps_old_model_data_until_successful_retry(self):
        before = self.records()
        with patch.object(news_embed, 'MODEL', 'new-keyword-model'), patch.object(news_embed, 'DIMENSIONS', 8):
            self.case.encode.side_effect = RuntimeError('synthetic API failure')
            self.assertEqual(self.rebuild(), 1)
            self.assertEqual(before, self.records())
            self.case.encode.side_effect = lambda texts: [[0.0,1.0]+[0.0]*6 for _ in texts]
            self.assertEqual(self.rebuild(), 0)
            rows = self.records()
            self.assertEqual(set(before), set(rows))
            self.assertTrue(all(len(v) == 8 and v[:2] == [0.0,1.0] for _, _, v in rows.values()))

    def test_public_search_and_normal_sync_follow_new_keyword_pointer(self):
        self.assertEqual(self.rebuild(), 0)
        self.case.encode.reset_mock()
        self.assertEqual(self.case.run_default(keywords_only=True), 0)
        self.case.encode.assert_not_called()
        hits = news_embed.search_keywords('AI', n=1, root=self.root, embed=self.case.encode)
        self.assertEqual(len(hits), 1)
        self.assertIn(hits[0][0], self.records())

    def test_empty_rebuild_publishes_empty_generation_and_preserves_previous_files(self):
        previous_path = keyword_path(self.base)
        before = self.case.digest(previous_path)
        with patch.object(news_embed, 'keyword_article_counts', return_value={}):
            self.assertEqual(self.rebuild(), 0)
        self.case.encode.assert_not_called()
        self.assertEqual(self.records(), {})
        after = self.case.digest(previous_path)
        self.assertEqual(before, {name: after[name] for name in before})

    def test_readers_keep_old_keywords_while_building_and_after_pointer_swap(self):
        from contextlib import closing
        old_path = keyword_path(self.base)
        with closing(store.persistent_client(old_path)) as reader:
            old = reader.get_collection('news_keyword', embedding_function=None)
            old_ids = set(old.get(include=[])['ids'])
            counts = {k: 1 for k in [*old_ids, '새키워드']}
            def encode(texts):
                self.assertEqual(set(old.get(include=[])['ids']), old_ids)
                self.assertEqual(keyword_path(self.base), old_path)
                self.assertTrue(news_embed.search_keywords('AI', root=self.root, n=1,
                    embed=lambda words: [[1.0]+[0.0]*3071 for _ in words]))
                return [[1.0]+[0.0]*3071 for _ in texts]
            self.case.encode.side_effect = encode
            with patch.object(news_embed, 'keyword_article_counts', return_value=counts):
                self.assertEqual(self.rebuild(), 0)
            self.assertNotEqual(keyword_path(self.base), old_path)
            self.assertEqual(set(old.get(include=[])['ids']), old_ids)
            self.assertEqual(set(self.records()), set(counts))


class BorrowedKeywordRecoveryTest(unittest.TestCase):
    def test_legacy_client_rebuild_failure_and_name_swap_failure_keep_old_collection(self):
        from backend.tests import test_news_embed as legacy
        from backend.app.services.rag import keyword_store
        case = legacy.NewsEmbedTest()
        case.setUp()
        # 공유 메모리 클라이언트의 이전 시험 상태가 복구 검증에 섞이지 않게 격리합니다.
        case.client = store.persistent_client(Path(case.tmp.name) / 'borrowed-keyword-recovery')
        try:
            self.assertEqual(case.run_sync(legacy.FakeEmbed()), 0)
            old = case.client.get_collection('news_keyword', embedding_function=None)
            self.assertEqual(old.metadata['dimensions'], 8)
            original_id = old.id
            before = old.get(include=['documents', 'metadatas'])
            def failure(texts):
                raise RuntimeError('synthetic embedding error')
            self.assertEqual(case.run_sync(failure,
                                           keywords_only=True, rebuild=True), 1)
            self.assertEqual(case.client.get_collection('news_keyword').get(include=['documents', 'metadatas']), before)
            modify = type(old).modify
            failed = []
            def write_then_fail(collection, **kwargs):
                modify(collection, **kwargs)
                if kwargs.get('name') == 'news_keyword' and collection.id != original_id and not failed:
                    failed.append(True)
                    raise RuntimeError('synthetic keyword name swap error')
            with patch.object(type(old), 'modify', new=write_then_fail):
                self.assertEqual(case.run_sync(legacy.FakeEmbed(), keywords_only=True, rebuild=True), 1)
            self.assertEqual(case.client.get_collection('news_keyword').id, original_id)
            self.assertEqual(case.client.get_collection('news_keyword').get(include=['documents', 'metadatas']), before)
            self.assertEqual(case.run_sync(legacy.FakeEmbed(), keywords_only=True, rebuild=True), 0)
            self.assertEqual(case.client.get_collection('news_keyword').metadata['dimensions'], 8)
            backups = [c.name for c in case.client.list_collections() if c.name.startswith('news_keyword_previous_')]
            self.assertEqual(len(backups), 1)
            self.assertEqual(case.client.get_collection(backups[0]).id, original_id)
            self.assertEqual(case.client.get_collection('news_keyword').count(), old.count())
        finally:
            case.client.close()
            case.tearDown()
            case.doCleanups()


class KeywordDimensionsTest(unittest.TestCase):
    def setUp(self):
        self.case = RebuildTest()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.case.seed_market()
        self.assertEqual(self.case.run_default(), 0)
        self.root = self.case.root
        self.base = self.root/'keywords'
        self.case.encode.reset_mock()

    def inspect_keywords(self, path=None):
        client = store.persistent_client(path or keyword_path(self.base))
        try:
            col = client.get_collection('news_keyword', embedding_function=None)
            rows = store.read_all(col, ['documents', 'metadatas', 'embeddings'])
            return col.metadata, dict(zip(rows['ids'], [(d, m, list(v)) for d, m, v in
                zip(rows['documents'], rows['metadatas'], rows['embeddings'])]))
        finally:
            client.close()

    def make_legacy(self, *, empty=False):
        client = store.persistent_client(keyword_path(self.base))
        try:
            col = client.get_collection('news_keyword', embedding_function=None)
            # Chroma의 불변 거리 설정은 modify에 다시 전달하지 않습니다.
            col.modify(metadata={k: v for k, v in col.metadata.items() if k not in ('dimensions', 'hnsw:space')})
            if empty:
                col.delete(ids=col.get(include=[])['ids'])
        finally:
            client.close()

    def test_new_normal_and_rebuilt_collections_record_dimensions(self):
        metadata, rows = self.inspect_keywords()
        self.assertEqual(metadata['dimensions'], 3072)
        self.assertTrue(all(len(v) == metadata['dimensions'] for _, _, v in rows.values()))
        self.assertEqual(self.case.run_default(keywords_only=True, rebuild=True), 0)
        metadata, rows = self.inspect_keywords()
        self.assertEqual(metadata['dimensions'], 3072)
        self.assertTrue(all(len(v) == metadata['dimensions'] for _, _, v in rows.values()))

    def test_compatible_legacy_collection_is_read_without_reembedding_or_metadata_rewrite(self):
        self.make_legacy()
        before = self.inspect_keywords()
        self.assertNotIn('dimensions', before[0])
        self.assertEqual(self.case.run_default(keywords_only=True), 0)
        self.case.encode.assert_not_called()
        self.assertTrue(news_embed.search_keywords('AI', root=self.root, embed=self.case.encode))
        self.assertEqual(before, self.inspect_keywords())

    def test_legacy_wrong_dimensions_fail_sync_and_search_before_provider_calls(self):
        self.make_legacy()
        before = self.inspect_keywords()
        with patch.object(news_embed, 'DIMENSIONS', 8), \
                patch.object(news_embed, 'openai_embed', side_effect=AssertionError('provider must not be created')) as provider:
            self.assertEqual(self.case.run_default(keywords_only=True), 1)
            self.case.encode.assert_not_called()
            for injected in (True, False):
                with self.assertRaisesRegex(ValueError, 'incompatible_keyword_embedding'):
                    news_embed.search_keywords('AI', root=self.root,
                        **({'embed': self.case.encode} if injected else {}))
            provider.assert_not_called()
            self.case.encode.assert_not_called()
        self.assertEqual(before, self.inspect_keywords())

    def test_declared_current_dimensions_cannot_hide_wrong_actual_vectors(self):
        client = store.persistent_client(keyword_path(self.base))
        try:
            col = client.get_collection('news_keyword', embedding_function=None)
            col.modify(metadata={**{k: v for k, v in col.metadata.items() if k != 'hnsw:space'}, 'dimensions': 8})
        finally:
            client.close()
        before = self.inspect_keywords()
        with patch.object(news_embed, 'DIMENSIONS', 8):
            self.assertEqual(self.case.run_default(keywords_only=True), 1)
            with self.assertRaisesRegex(ValueError, 'incompatible_keyword_embedding'):
                news_embed.search_keywords('AI', root=self.root, embed=self.case.encode)
        self.case.encode.assert_not_called()
        self.assertEqual(before, self.inspect_keywords())

    def test_empty_legacy_collection_retains_old_chroma_dimension_binding(self):
        self.make_legacy(empty=True)
        before = self.inspect_keywords()
        self.assertEqual(before[1], {})
        with patch.object(news_embed, 'DIMENSIONS', 8):
            self.assertEqual(self.case.run_default(keywords_only=True), 1)
            with self.assertRaisesRegex(ValueError, 'incompatible_keyword_embedding'):
                news_embed.search_keywords('AI', root=self.root, embed=self.case.encode)
        self.case.encode.assert_not_called()
        self.assertEqual(before, self.inspect_keywords())

    def test_unverifiable_empty_collection_is_not_assumed_to_match_configuration(self):
        from types import SimpleNamespace
        from backend.app.services.rag.keyword_store import check_compatibility
        col = SimpleNamespace(metadata={'embedding_model': news_embed.MODEL},
            get=lambda **kw: {'ids': [], 'embeddings': []}, count=lambda: 0)
        with self.assertRaisesRegex(ValueError, 'incompatible_keyword_embedding'):
            check_compatibility(col, model=news_embed.MODEL, dimensions=3072)

    def test_dimension_only_rebuild_failure_pipeline_and_search_fail_until_keyword_retry(self):
        from types import SimpleNamespace as NS
        self.make_legacy()
        before = self.inspect_keywords()
        old_path = keyword_path(self.base)
        with patch.object(store, 'DIMENSIONS', 8), patch.object(news_embed, 'DIMENSIONS', 8), \
                patch.object(news_embedding, 'DIMENSIONS', 8):
            def encode(texts):
                if any(not t.startswith('[') for t in texts):
                    raise RuntimeError('synthetic keyword API failure')
                return [[0.0, 1.0]+[0.0]*6 for _ in texts]
            self.case.encode.side_effect = encode
            self.assertEqual(self.case.run_default(rebuild=True), 1)
            self.assertEqual(before, self.inspect_keywords())
            self.assertEqual(keyword_path(self.base), old_path)
            def response(*, model, dimensions, input):
                pair = [0.0, 1.0] if dimensions == 8 else [1.0, 0.0]
                return NS(data=[NS(index=i, embedding=pair+[0.0]*(dimensions-2)) for i in range(len(input))],
                          usage=NS(total_tokens=len(input), prompt_tokens=len(input)))
            self.case.case.api.embeddings.create.side_effect = response
            self.case.case.shared_embed.side_effect = COMMON_EMBED
            self.case.case.embed.side_effect = CLASSIFICATION_EMBED
            result = self.case.case.run_pipeline()
            self.assertEqual(result['status'], 'PARTIAL', result)
            self.assertEqual(result['chroma_result']['body']['status'], 'complete')
            self.assertEqual(result['chroma_result']['keywords']['status'], 'failed')
            self.assertEqual(result['chroma_result']['error_code'], 'incompatible_keyword_embedding')
            self.assertEqual(before, self.inspect_keywords())
            self.case.encode.reset_mock()
            self.assertEqual(self.case.run_default(keywords_only=True), 1)
            with self.assertRaisesRegex(ValueError, 'incompatible_keyword_embedding'):
                news_embed.search_keywords('AI', root=self.root, embed=self.case.encode)
            self.case.encode.assert_not_called()

            self.case.encode.side_effect = lambda texts: [[0.0, 1.0]+[0.0]*6 for _ in texts]
            self.assertEqual(self.case.run_default(keywords_only=True, rebuild=True), 0)
            metadata, rows = self.inspect_keywords()
            self.assertEqual(metadata['dimensions'], 8)
            self.assertTrue(all(len(v) == 8 for _, _, v in rows.values()))
            self.assertEqual(before, self.inspect_keywords(old_path))
            self.assertTrue(news_embed.search_keywords('AI', root=self.root, embed=self.case.encode))
            self.assertEqual(self.case.case.run_pipeline()['status'], 'SUCCESS')
            self.assertEqual(self.case.run_default(keywords_only=True), 0)
