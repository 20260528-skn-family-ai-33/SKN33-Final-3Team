import copy
import tempfile
from pathlib import Path
import unittest

from backend.app.schemas.rag import ALL_SOURCES
from backend.app.services.rag.evaluation import EvaluationDataset, evaluate
from backend.app.services.rag.evaluation_drafts import prepare_drafts
from backend.tests.test_rag_evaluation import case_result, run_for
from backend.tests.rag_fixtures import evaluation_payload


class AcceptanceTest(unittest.TestCase):
    def dataset(self):
        payload=evaluation_payload()
        template=payload['cases']
        cases=[]
        for n in range(50):
            row=copy.deepcopy(template[n%4])
            row['case_id']=f'case-{n}'
            row['request']['request_id']=f'request-{n}'
            cases.append(row)
        payload.update(cases=cases,synthetic=False,split='holdout',reviewed=True,review_reference='user-reviewed-test-record')
        return EvaluationDataset.model_validate(payload)

    def test_final_recall_threshold_is_macro_with_errors_counted_as_zero(self):
        dataset=self.dataset()
        for hits,expected in [(35,'passed'),(34,'failed')]:
            results=[case_result(case,retrieved=case.relevant_articles if n<hits else ()) for n,case in enumerate(dataset.cases)]
            report=evaluate(dataset,run_for(dataset,results,finalized=True,
                collections=['total_article']))
            self.assertEqual(report['acceptance_status'],expected)
            self.assertEqual(report['macro_recall_at_5'],hits/50)

    def test_draft_synthetic_or_incomplete_run_cannot_pass(self):
        dataset=self.dataset()
        for change in ({'reviewed':False},{'synthetic':True},{}):
            payload=dataset.model_dump(mode='json');payload.update(change)
            changed=EvaluationDataset.model_validate(payload)
            results=[case_result(c,retrieved=c.relevant_articles) for c in changed.cases]
            if not change:results.pop()
            report=evaluate(changed,run_for(changed,results,finalized=True,
                collections=['total_article']))
            self.assertEqual(report['acceptance_status'],'not_assessed')

    def test_reviewed_final_questions_are_not_restricted_to_chat_or_source_quota(self):
        payload = self.dataset().model_dump(mode='json')
        for case in payload['cases']:
            case['request']['use_case'] = 'local_issue'
            case['relevant_articles'] = [{'source_code':'KOTRA_MARKET', 'external_id':'reviewed-gold'}]
        dataset = EvaluationDataset.model_validate(payload)
        results = [case_result(c, retrieved=c.relevant_articles) for c in dataset.cases]
        report = evaluate(dataset, run_for(dataset, results, finalized=True,
                          collections=['total_article']))
        self.assertEqual(report['acceptance_status'], 'passed')
        self.assertEqual(report['gold_sources'], ['KOTRA_MARKET'])

    def test_legacy_collections_do_not_pass_current_acceptance(self):
        dataset=self.dataset()
        results=[case_result(c,retrieved=c.relevant_articles) for c in dataset.cases]
        report=evaluate(dataset,run_for(dataset,results,finalized=True,
            collections=['total_article','short_news','newspaper_article']))
        self.assertEqual(report['acceptance_status'],'not_assessed')

    def test_case_count_split_and_review_gates_are_not_relaxed(self):
        for change in ({'cases':self.dataset().cases[:49]}, {'split':'dev'}, {'synthetic':True}, {'reviewed':False}):
            data=self.dataset().model_copy(update=change)
            rows=[case_result(c,retrieved=c.relevant_articles) for c in data.cases]
            report=evaluate(data,run_for(data,rows,finalized=True,collections=['total_article']))
            self.assertEqual(report['acceptance_status'],'not_assessed')
        from backend.app.services.rag.evaluation import RunConfiguration
        from backend.tests.rag_fixtures import configuration_payload
        data=self.dataset()
        with self.assertRaises(ValueError):
            evaluate(data,run_for(data,[],finalized=False,collections=['total_article']))

    def test_recorded_service_run_can_reach_gate_with_current_collection(self):
        # 합성 구현 시험: 실제 정답셋이나 품질 합격을 주장하지 않습니다.
        from types import SimpleNamespace
        from unittest.mock import Mock,patch
        from backend.app.services.rag.evaluate_service import record_retrieval
        from backend.app.services.rag.store import COLLECTIONS,MODEL
        from backend.app.services.rag.evaluation import RunConfiguration
        from backend.tests.rag_fixtures import configuration_payload
        data=self.dataset()
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);p=root/'generations/revision/vocabulary.json';p.parent.mkdir(parents=True)
            p.write_text('{}',encoding='utf-8')
            client=Mock();snapshot=SimpleNamespace(generation='revision',client=client)
            store=SimpleNamespace(root=root,snapshot=lambda:snapshot)
            config=RunConfiguration(**configuration_payload(finalized=True,embedding_model=MODEL,
                corpus_revision='revision',collections=list(COLLECTIONS)))
            responses=[SimpleNamespace(failures=[],candidates=[SimpleNamespace(article=c.relevant_articles[0])]) for c in data.cases]
            with patch('backend.app.services.rag.evaluate_service.search',side_effect=responses):
                run=record_retrieval(data,store,Mock(),config,seconds_per_request=30)
            client.close.assert_called_once()
            self.assertEqual(evaluate(data,run)['acceptance_status'],'passed')

    def test_real_article_drafts_keep_answers_unjudged_and_disjoint(self):
        import json
        articles=[{'source_code':ALL_SOURCES[n%4].value,'external_id':str(n),'title':f'실제 입력 형식 {n}',
                   'published_date':'2026-09-15','source_url':f'https://example.org/{n}',
                   'body':f'검수 도구 테스트용 본문 {n}','countries':['US']} for n in range(60)]
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'draft'
            manifest=prepare_drafts(articles,output)
            self.assertEqual(manifest['missing_sources'],[])
            review=json.loads((output/'review.json').read_text())
            self.assertEqual(len(review),60)
            for split in ('dev','holdout'):
                dataset=EvaluationDataset.model_validate_json((output/(split+'.json')).read_text())
                self.assertFalse(dataset.reviewed)
                self.assertTrue(all(c.relevant_articles is None for c in dataset.cases))
            dev={x['candidate_article']['external_id'] for x in review if x['split']=='dev'}
            final={x['candidate_article']['external_id'] for x in review if x['split']=='holdout'}
            self.assertFalse(dev&final)


if __name__=='__main__':unittest.main()
