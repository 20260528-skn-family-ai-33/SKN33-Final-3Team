import copy
import json
from pathlib import Path
import tempfile
import unittest

from backend.app.services.rag.review import prepare_review, finalize_review
from backend.tests.rag_fixtures import evaluation_payload


class HumanReviewWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.dataset=self.root/'draft.json';self.corpus=self.root/'articles.jsonl';self.review=self.root/'review.json'
        payload=evaluation_payload();self.dataset.write_text(json.dumps(payload))
        self.corpus.write_text('\n'.join(json.dumps({'source_code':c['relevant_articles'][0]['source_code'],
            'external_id':c['relevant_articles'][0]['external_id'],'body':'검수용 합성 원문입니다.',
            'published_date':'2026-09-15','countries':['US']}) for c in payload['cases']))
        self.template=prepare_review(self.dataset,self.corpus,self.review)

    def approved(self):
        value=copy.deepcopy(self.template);value.update(reviewer='test-reviewer',review_reference='test-only')
        payload=json.loads(self.dataset.read_text())
        for case,original in zip(value['cases'],payload['cases']):
            case.update(decision='approved',conditions_confirmed=True,evidence=[{
                **original['relevant_articles'][0],'quote':'검수용 합성 원문','reason':'테스트용 관련성 판정'}])
        return value

    def finalize(self,value):
        self.review.write_text(json.dumps(value))
        return finalize_review(self.dataset,self.review,self.corpus,self.root/'confirmed')

    def test_pending_templates_cannot_be_marked_reviewed(self):
        with self.assertRaises(ValueError):self.finalize(self.template)
        self.assertFalse((self.root/'confirmed').exists())

    def test_confirmed_answers_and_audit_are_written_without_changing_draft(self):
        before=self.dataset.read_bytes()
        confirmed=self.finalize(self.approved())
        self.assertTrue(confirmed.reviewed)
        self.assertTrue(confirmed.synthetic)
        self.assertEqual(self.dataset.read_bytes(),before)
        self.assertTrue((self.root/'confirmed/review_audit.json').exists())
        with self.assertRaises(FileExistsError):self.finalize(self.approved())

    def test_wrong_quote_country_and_unknown_article_are_rejected(self):
        for change in ({'quote':'원문에 없는 발췌'},{'external_id':'missing'}):
            value=self.approved();value['cases'][0]['evidence'][0].update(change)
            with self.assertRaises(ValueError):self.finalize(value)
        value=self.approved();value['cases'][0]['request']['conditions']['geography']['codes']=['PL']
        with self.assertRaisesRegex(ValueError,'outside_countries'):self.finalize(value)

    def test_changed_corpus_or_draft_cannot_reuse_old_review(self):
        value=self.approved();self.corpus.write_text(self.corpus.read_text()+'\n')
        with self.assertRaisesRegex(ValueError,'corpus_changed'):self.finalize(value)

    def test_no_answer_cases_are_separate_and_require_explanation(self):
        value=self.approved();value['cases'][0].update(decision='approved_no_answer',evidence=[],no_answer_reason='검수용 정답 없음')
        confirmed=self.finalize(value)
        self.assertEqual(confirmed.cases[0].relevant_articles,())


if __name__=='__main__':unittest.main()
