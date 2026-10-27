import tempfile
import unittest
from unittest.mock import Mock

import chromadb

from backend.app.services import shortnews_keywords as sk


class KeywordCatalogTest(unittest.TestCase):
    def setUp(self):
        self.categories = [{"category_id": 1, "major_name": "산업", "minor_name": "품목"}]
        self.topics = [
            {"keyword_text": "전기차", "canonical_text": None, "category_id": 1, "status": "CLASSIFIED"},
            {"keyword_text": "EV", "canonical_text": "전기차", "category_id": None, "status": "SYNONYM"},
            {"keyword_text": "대기어", "canonical_text": None, "category_id": None, "status": "PENDING"},
            {"keyword_text": "미분류어", "canonical_text": None, "category_id": None, "status": "UNCLASSIFIED"},
        ]

    def snapshot(self):
        return sk.catalog(self.topics, self.categories)

    def test_preserves_every_status_and_resolves_alias(self):
        rows = {r['keyword']: r for r in self.snapshot().rows}
        self.assertEqual(set(rows), {r['keyword_text'] for r in self.topics})
        self.assertEqual(rows['EV']['canonical'], '전기차')
        self.assertEqual(rows['EV']['minor'], '품목')

    def test_revision_stable_for_order_but_changes_for_classification(self):
        first = self.snapshot()
        self.topics.reverse()
        self.assertEqual(first.revision, self.snapshot().revision)
        self.categories[0]['minor_name'] = '자동차'
        self.assertNotEqual(first.revision, self.snapshot().revision)

    def test_missing_canonical_cycle_and_missing_category_fail(self):
        for target in ('없는말', 'EV'):
            self.topics[1]['canonical_text'] = target
            with self.assertRaises(ValueError): self.snapshot()
        self.topics[1]['canonical_text'] = '전기차'
        self.categories.clear()
        with self.assertRaises(ValueError): self.snapshot()

    def test_unknown_status_and_duplicate_identifier_fail(self):
        self.topics.append(dict(self.topics[0]))
        with self.assertRaises(ValueError): self.snapshot()
        self.topics.pop()
        self.topics[0]['status'] = 'INVALID'
        with self.assertRaises(ValueError): self.snapshot()


class KeywordIndexTest(KeywordCatalogTest):
    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.client = chromadb.PersistentClient(path=self.temp.name)
        self.addCleanup(self.client.close)
        self.embed = Mock(side_effect=lambda words: [[1.0] + [0.0] * (sk.DIMENSIONS - 1) for _ in words])

    def test_repeat_has_no_api_calls_and_uses_separate_collection(self):
        self.client.get_or_create_collection('news_keyword', embedding_function=None)
        snapshot = self.snapshot()
        col, result = sk.sync_index(snapshot, self.client, self.embed)
        self.assertEqual((col.count(), result['embedded']), (4, 4))
        self.embed.reset_mock()
        _, result = sk.sync_index(snapshot, self.client, self.embed)
        self.embed.assert_not_called()
        self.assertEqual(result['embedded'], 0)
        self.assertEqual(self.client.get_collection('news_keyword').count(), 0)

    def test_failed_batch_resumes_only_missing_rows(self):
        good = [1.0] + [0.0] * (sk.DIMENSIONS - 1)
        failing = Mock(side_effect=[[good, good], RuntimeError('network failure')])
        snapshot = self.snapshot()
        with self.assertRaises(RuntimeError): sk.sync_index(snapshot, self.client, failing, batch_size=2)
        col, result = sk.sync_index(snapshot, self.client, self.embed, batch_size=2)
        self.assertEqual((col.count(), result['embedded'], result['existing']), (4, 2, 2))

    def test_invalid_embedding_does_not_write_batch(self):
        snapshot = self.snapshot()
        for bad in ([[1.0]], [[float('nan')] * sk.DIMENSIONS] * 4):
            with self.assertRaises(ValueError): sk.sync_index(snapshot, self.client, lambda words: bad)
        self.assertEqual(self.client.get_collection(sk.collection_name(snapshot)).count(), 0)

    def test_classification_change_creates_new_revision(self):
        old, _ = sk.sync_index(self.snapshot(), self.client, self.embed)
        self.categories[0]['minor_name'] = '자동차'
        new, _ = sk.sync_index(self.snapshot(), self.client, self.embed)
        self.assertNotEqual(old.name, new.name)
        self.assertEqual(old.get(ids=['EV'])['metadatas'][0]['minor'], '품목')
        self.assertEqual(new.get(ids=['EV'])['metadatas'][0]['minor'], '자동차')

    def test_cleanup_only_removes_owned_old_versions_after_valid_replacement(self):
        old, _ = sk.sync_index(self.snapshot(), self.client, self.embed)
        untouched = ['news_keyword', 'short_news', 'short_news_keywords_backup',
                     'short_news_keywords_' + 'f' * 32]
        for name in untouched:
            self.client.get_or_create_collection(name, embedding_function=None)
        self.categories[0]['minor_name'] = '자동차'
        snapshot = self.snapshot()
        sk.sync_index(snapshot, self.client, self.embed)
        with sk.index_lock(self.client):
            self.assertEqual(sk.cleanup_indexes(snapshot, self.client), [old.name])
            self.assertEqual(sk.cleanup_indexes(snapshot, self.client), [])
        self.assertEqual({c.name for c in self.client.list_collections()},
                         set(untouched) | {sk.collection_name(snapshot)})

    def test_partial_new_index_never_allows_old_index_deletion(self):
        old, _ = sk.sync_index(self.snapshot(), self.client, self.embed)
        self.categories[0]['minor_name'] = '자동차'
        snapshot = self.snapshot()
        with self.assertRaises(RuntimeError):
            sk.sync_index(snapshot, self.client, Mock(side_effect=RuntimeError('network')))
        with sk.index_lock(self.client), self.assertRaises(ValueError):
            sk.cleanup_indexes(snapshot, self.client)
        self.assertEqual(old.count(), 4)


if __name__ == '__main__': unittest.main()
