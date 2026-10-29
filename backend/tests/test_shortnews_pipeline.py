import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from types import SimpleNamespace
from backend.app.services.rag.store import GenerationStore
from backend.app.services.rag.vocabulary import Vocabulary

import chromadb
from sqlalchemy import select

from backend.tests import test_shortnews_load as fixture
from backend.app.scheduler.jobs import shortnews_pipeline as pipeline
from backend.app.services import shortnews_keywords as keywords
from backend.app.models.news import Article, ArticleTag


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.db = fixture.ShortnewsLoadTest()
        self.db.setUp()
        self.addCleanup(self.db.doCleanups)
        self.addCleanup(self.db.tearDown)
        self.path = self.db.root / 'raw.json'
        self.path.write_text(json.dumps([fixture.raw()]), encoding="utf-8")
        self.real_client = chromadb.PersistentClient
        self.client = chromadb.PersistentClient(path=str(self.db.root / 'chroma'))
        self.addCleanup(self.client.close)
        self.snapshot = keywords.catalog([
            {'keyword_text': 'AI', 'canonical_text': None, 'category_id': 1, 'status': 'CLASSIFIED'}],
            [{'category_id': 1, 'major_name': '산업', 'minor_name': '품목'}])
        self.api = Mock()
        # 공통 색인의 SQL/Chroma는 실제 임시 저장소, 사전만 합성 fixture로 격리합니다.
        vocabulary = Vocabulary(categories=SimpleNamespace(listed=[], lookup=lambda word: None))
        vocabulary.clean = lambda words: {'kw_topic': list(dict.fromkeys(words)), 'kw_sub': [], 'kw_category': []}
        vocabulary.body_subterms = lambda body, topics: []
        for target, value in (
                ('backend.app.services.rag.vocabulary.Vocabulary.load', vocabulary),
                ('backend.app.services.news_keywords.keyword_clean.load_geo', ({'미국': {'code': 'US', 'canonical': '미국', 'type': '국가'}}, {}))):
            p = patch(target, return_value=value); p.start(); self.addCleanup(p.stop)

        for target, value in ((pipeline, 'ROOT'),):
            p = patch.object(target, value, self.db.root);p.start();self.addCleanup(p.stop)
        p = patch.object(pipeline.keywords, 'read_catalog', return_value=self.snapshot)
        p.start();self.addCleanup(p.stop)
        p = patch.object(pipeline.analysis, 'embed', side_effect=lambda api, words, **kwargs: [[1.0] + [0.0] * 3071 for _ in words])
        self.embed = p.start();self.addCleanup(p.stop)
        p = patch.object(pipeline.shared_embedding, 'embed', side_effect=lambda api, words, **kwargs: [[1.0] + [0.0] * 3071 for _ in words])
        self.shared_embed = p.start();self.addCleanup(p.stop)
        p = patch.object(pipeline.analysis, 'ask', return_value={'summary_sentences': ['요약이다.'], 'keywords': ['AI']})
        self.ask = p.start();self.addCleanup(p.stop)

    def body_collection(self):
        snapshot = GenerationStore(self.db.root/'vectors').snapshot()
        self.addCleanup(snapshot.client.close)
        return snapshot.collection('kotra_article')

    def run_pipeline(self):
        return pipeline.run(self.path, db_url=self.db.url, env_path=self.db.root/'absent.env',
                            client=self.client, api=self.api, log_dir=self.db.root/'pipeline', vector_root=self.db.root/'vectors')

    def test_real_sqlite_and_chroma_repeat_is_idempotent(self):
        first = self.run_pipeline()
        self.assertEqual(first['status'], 'SUCCESS', first)
        second = self.run_pipeline()
        self.assertEqual(second['status'], 'SUCCESS', second)
        self.assertEqual(second['analysis_requested'], 0)
        self.assertEqual(second['chroma_result']['body']['embedded'], 0)
        self.assertEqual(self.ask.call_count, 1)
        self.assertEqual(self.body_collection().count(), 1)
        with self.db.engine.connect() as conn:
            self.assertEqual(len(conn.execute(select(Article)).all()), 1)
            self.assertEqual(conn.execute(select(ArticleTag.keyword_text).where(ArticleTag.keyword_text.is_not(None))).scalars().all(), ['AI'])

    def test_chroma_failure_after_commit_is_recoverable(self):
        with patch.object(pipeline.vectors, 'sync_body', side_effect=RuntimeError('failure')):
            failed = self.run_pipeline()
        self.assertEqual((failed['status'], failed['mysql']), ('PARTIAL', 'SUCCESS'))
        fixed = self.run_pipeline()
        self.assertEqual(fixed['status'], 'SUCCESS', fixed)
        self.assertEqual(self.ask.call_count, 1)
        self.assertEqual(self.body_collection().count(), 1)

    def test_analysis_failure_leaves_database_unchanged(self):
        self.ask.side_effect = ValueError('invalid model output')
        result = self.run_pipeline()
        self.assertEqual(result['status'], 'FAILED')
        with self.db.engine.connect() as conn:
            self.assertEqual(conn.execute(select(Article)).all(), [])

    def test_zero_articles_needs_no_llm_or_index(self):
        self.path.write_text('[]', encoding="utf-8")
        result = self.run_pipeline()
        self.assertEqual(result['status'], 'SUCCESS', result)
        self.embed.assert_not_called()
        self.shared_embed.assert_not_called()
        self.ask.assert_not_called()

    def seed_old_index(self):
        old = keywords.catalog([
            {'keyword_text': 'AI', 'canonical_text': None, 'category_id': 1, 'status': 'CLASSIFIED'}],
            [{'category_id': 1, 'major_name': '산업', 'minor_name': '이전분류'}])
        col, _ = keywords.sync_index(old, self.client, lambda words: [[1.0] + [0.0] * 3071 for _ in words])
        return col

    def test_cleanup_waits_for_mysql_chroma_and_keeps_old_index_on_failure(self):
        old = self.seed_old_index()
        with patch.object(pipeline.vectors, 'sync_body', side_effect=RuntimeError('failure')):
            failed = self.run_pipeline()
        self.assertEqual(failed['status'], 'PARTIAL')
        self.assertEqual(old.count(), 1)
        fixed = self.run_pipeline()
        self.assertEqual(fixed['status'], 'SUCCESS', fixed)
        self.assertEqual(fixed['removed_keyword_indexes'], [old.name])
        self.assertEqual({c.name for c in self.client.list_collections()},
                         {keywords.collection_name(self.snapshot)})

    def test_db_revision_change_prevents_cleanup(self):
        old = self.seed_old_index()
        changed = keywords.catalog([
            {'keyword_text': 'AI', 'canonical_text': None, 'category_id': 1, 'status': 'CLASSIFIED'}],
            [{'category_id': 1, 'major_name': '산업', 'minor_name': '변경분류'}])
        with patch.object(keywords, 'read_catalog', side_effect=[self.snapshot, self.snapshot, changed]):
            failed = self.run_pipeline()
        self.assertEqual(failed['status'], 'PARTIAL')
        self.assertEqual(old.count(), 1)

    def test_pipeline_holds_index_lock_through_article_sync_and_cleanup(self):
        from filelock import Timeout
        original = pipeline.vectors.sync_body
        def check(*args, **kwargs):
            with self.assertRaises(Timeout):
                with keywords.index_lock(self.client):
                    pass
            return original(*args, **kwargs)
        with patch.object(pipeline.vectors, 'sync_body', side_effect=check):
            result = self.run_pipeline()
        self.assertEqual(result['status'], 'SUCCESS', result)

    def test_cleanup_failure_can_retry_without_duplicate_articles_or_llm_calls(self):
        old = self.seed_old_index()
        with patch.object(self.client, 'delete_collection', side_effect=RuntimeError('busy')):
            failed = self.run_pipeline()
        self.assertEqual((failed['status'], failed['stage']), ('PARTIAL', 'index_cleanup'))
        self.assertEqual(old.count(), 1)
        fixed = self.run_pipeline()
        self.assertEqual(fixed['status'], 'SUCCESS', fixed)
        self.assertEqual(fixed['analysis_requested'], 0)
        self.assertEqual(fixed['chroma_result']['body']['embedded'], 0)
        self.assertEqual(len(self.db.rows('article')), 1)
        self.assertEqual(self.body_collection().count(), 1)

    def test_index_cli_shares_reader_lock_and_cleans_after_revision_check(self):
        from types import SimpleNamespace
        from filelock import Timeout
        from backend.app.scheduler.jobs import shortnews_keyword_index as cli
        old = self.seed_old_index()
        api = Mock()
        api.embeddings.create.return_value.data = [SimpleNamespace(index=0, embedding=[1.0] + [0.0] * 3071)]
        with patch.object(cli, 'mysql_url', return_value=self.db.url), \
                patch('openai.OpenAI') as factory:
            factory.return_value.__enter__.return_value = api
            with keywords.index_lock(self.client), self.assertRaises(Timeout):
                cli.run(env_path=self.db.root / 'absent.env', chroma_path=self.db.root / 'chroma')
            factory.assert_not_called()
            with patch.object(keywords, 'read_catalog', side_effect=[self.snapshot, Mock(revision='changed')]):
                with self.assertRaisesRegex(ValueError, 'DB 분류'):
                    cli.run(env_path=self.db.root / 'absent.env', chroma_path=self.db.root / 'chroma')
            self.assertEqual(old.count(), 1)
            result = cli.run(env_path=self.db.root / 'absent.env', chroma_path=self.db.root / 'chroma')
        self.assertEqual(result['removed_keyword_indexes'], [old.name])

    def test_owned_clients_are_closed_on_catalog_failure(self):
        api = Mock()
        with patch('openai.OpenAI', return_value=api), \
                patch('chromadb.PersistentClient', return_value=self.client), \
                patch.object(self.client, 'close', wraps=self.client.close) as close, \
                patch.object(keywords, 'read_catalog', side_effect=ValueError('invalid catalog')):
            result = pipeline.run(self.path, db_url=self.db.url, env_path=self.db.root/'absent.env',
                                  chroma_path=self.db.root/'chroma', log_dir=self.db.root/'pipeline', vector_root=self.db.root/'vectors')
        self.assertEqual(result['status'], 'FAILED')
        close.assert_called_once()
        api.close.assert_called_once()

    def test_injected_clients_remain_owned_by_caller(self):
        with patch.object(self.client, 'close', wraps=self.client.close) as close:
            result = self.run_pipeline()
        self.assertEqual(result['status'], 'SUCCESS', result)
        close.assert_not_called()
        self.api.close.assert_not_called()

    def test_close_failure_never_leaves_success_report(self):
        with patch('openai.OpenAI', return_value=Mock()), \
                patch('chromadb.PersistentClient', side_effect=lambda path, **kwargs: self.client if Path(path) == self.db.root/'chroma' else self.real_client(path=path, **kwargs)), \
                patch.object(self.client, 'close', side_effect=RuntimeError('close failed')):
            result = pipeline.run(self.path, db_url=self.db.url, env_path=self.db.root/'absent.env',
                                  chroma_path=self.db.root/'chroma', log_dir=self.db.root/'pipeline', vector_root=self.db.root/'vectors')
        self.assertEqual((result['status'], result['stage']), ('FAILED', 'resource_cleanup'))
        self.assertEqual(result['cleanup_errors'], [{'resource':'chroma', 'error_type':'RuntimeError'}])
        saved = json.loads((self.db.root/'pipeline/pipeline_result.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['status'], 'FAILED')

    def test_close_failure_preserves_original_catalog_error(self):
        with patch('openai.OpenAI', return_value=Mock()), \
                patch('chromadb.PersistentClient', return_value=self.client), \
                patch.object(self.client, 'close', side_effect=RuntimeError('close failed')), \
                patch.object(keywords, 'read_catalog', side_effect=ValueError('catalog failed')):
            result = pipeline.run(self.path, db_url=self.db.url, env_path=self.db.root/'absent.env',
                                  chroma_path=self.db.root/'chroma', log_dir=self.db.root/'pipeline', vector_root=self.db.root/'vectors')
        self.assertEqual((result['status'], result['stage'], result['error_type']),
                         ('FAILED', 'classification_index', 'ValueError'))
        self.assertEqual(result['cleanup_errors'], [{'resource':'chroma', 'error_type':'RuntimeError'}])


if __name__ == '__main__': unittest.main()
