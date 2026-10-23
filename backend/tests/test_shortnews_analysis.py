import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from backend.app.services import shortnews_analysis as service, shortnews_keywords as keywords


class AnalysisTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.snapshot = keywords.catalog([
            {'keyword_text': '전기차', 'canonical_text': None, 'status': 'CLASSIFIED', 'category_id': 1}],
            [{'category_id': 1, 'major_name': '산업', 'minor_name': '자동차'}])
        self.article = {'external_id': '001', 'title': '제목', 'body_masked': '가' * 5000 + '끝부분 중요 사실'}
        self.collection = Mock()
        self.collection.query.side_effect = lambda **kwargs: {'ids': [['전기차'] for _ in kwargs['query_embeddings']]}
        self.encode = Mock(side_effect=lambda chunks: [[1.0] for _ in chunks])
        self.request = Mock(return_value={'summary_sentences': ['전기차 관련 기사다.'], 'keywords': ['전기차']})

    def analyze(self):
        return service.analyze(self.article, self.snapshot, self.collection, self.encode, self.request, self.tmp.name)

    def test_full_body_reaches_candidates_and_llm(self):
        self.analyze()
        chunks = [s for call in self.encode.call_args_list for s in call.args[0]]
        self.assertEqual(''.join(chunks), self.article['title'] + '\n' + self.article['body_masked'])
        self.assertEqual(self.request.call_args.args[0]['body_masked'], self.article['body_masked'])

    def test_unchanged_input_reuses_cache_and_body_change_invalidates(self):
        _, cached = self.analyze()
        self.assertFalse(cached)
        _, cached = self.analyze()
        self.assertTrue(cached)
        self.assertEqual(self.request.call_count, 1)
        self.article['body_masked'] += ' 수정'
        self.analyze()
        self.assertEqual(self.request.call_count, 2)

    def test_classification_revision_invalidates_cache(self):
        self.analyze()
        self.snapshot = keywords.Catalog('changed', self.snapshot.rows)
        self.analyze()
        self.assertEqual(self.request.call_count, 2)

    def test_invalid_responses_are_not_cached(self):
        for result in ({'summary_sentences': [], 'keywords': []},
                       {'summary_sentences': ['요약'], 'keywords': ['후보 밖']},
                       {'summary_sentences': ['요약'], 'keywords': ['전기차', '전기차']}):
            self.request.return_value = result
            with self.assertRaises(ValueError): self.analyze()
        self.assertEqual(list(Path(self.tmp.name).glob('*.json')), [])

    def test_corrupted_cache_fails_instead_of_succeeding(self):
        self.analyze()
        path = next(Path(self.tmp.name).glob('*.json'))
        path.write_text('{broken')
        with self.assertRaises(json.JSONDecodeError): self.analyze()


if __name__ == '__main__': unittest.main()
