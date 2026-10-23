import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

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
        self.path.write_text(json.dumps([fixture.raw()]))
        self.client = chromadb.PersistentClient(path=str(self.db.root / 'chroma'))
        self.snapshot = keywords.catalog([
            {'keyword_text': 'AI', 'canonical_text': None, 'category_id': 1, 'status': 'CLASSIFIED'}],
            [{'category_id': 1, 'major_name': '산업', 'minor_name': '품목'}])
        self.api = Mock()
        for target, value in ((pipeline, 'ROOT'),):
            p = patch.object(target, value, self.db.root);p.start();self.addCleanup(p.stop)
        p = patch.object(pipeline.keywords, 'read_catalog', return_value=self.snapshot)
        p.start();self.addCleanup(p.stop)
        p = patch.object(pipeline.analysis, 'embed', side_effect=lambda api, words: [[1.0] + [0.0] * 3071 for _ in words])
        self.embed = p.start();self.addCleanup(p.stop)
        p = patch.object(pipeline.analysis, 'ask', return_value={'summary_sentences': ['요약이다.'], 'keywords': ['AI']})
        self.ask = p.start();self.addCleanup(p.stop)

    def run_pipeline(self):
        return pipeline.run(self.path, db_url=self.db.url, env_path=self.db.root/'absent.env',
                            client=self.client, api=self.api, log_dir=self.db.root/'pipeline')

    def test_real_sqlite_and_chroma_repeat_is_idempotent(self):
        first = self.run_pipeline()
        self.assertEqual(first['status'], 'SUCCESS', first)
        second = self.run_pipeline()
        self.assertEqual(second['status'], 'SUCCESS', second)
        self.assertEqual(second['analysis_requested'], 0)
        self.assertEqual(second['chroma_result']['updated'], 0)
        self.assertEqual(self.ask.call_count, 1)
        self.assertEqual(self.client.get_collection('short_news').count(), 1)
        with self.db.engine.connect() as conn:
            self.assertEqual(len(conn.execute(select(Article)).all()), 1)
            self.assertEqual(conn.execute(select(ArticleTag.keyword_text).where(ArticleTag.keyword_text.is_not(None))).scalars().all(), ['AI'])

    def test_chroma_failure_after_commit_is_recoverable(self):
        with patch.object(pipeline, 'sync_articles', side_effect=RuntimeError('failure')):
            failed = self.run_pipeline()
        self.assertEqual((failed['status'], failed['mysql']), ('FAILED', 'SUCCESS'))
        fixed = self.run_pipeline()
        self.assertEqual(fixed['status'], 'SUCCESS', fixed)
        self.assertEqual(self.ask.call_count, 1)
        self.assertEqual(self.client.get_collection('short_news').count(), 1)

    def test_analysis_failure_leaves_database_unchanged(self):
        self.ask.side_effect = ValueError('invalid model output')
        result = self.run_pipeline()
        self.assertEqual(result['status'], 'FAILED')
        with self.db.engine.connect() as conn:
            self.assertEqual(conn.execute(select(Article)).all(), [])

    def test_zero_articles_needs_no_llm_or_index(self):
        self.path.write_text('[]')
        result = self.run_pipeline()
        self.assertEqual(result['status'], 'SUCCESS', result)
        self.embed.assert_not_called()
        self.ask.assert_not_called()


if __name__ == '__main__': unittest.main()
