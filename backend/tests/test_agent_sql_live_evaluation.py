"""Offline tests for scoring/provenance only; never run MySQL or LLM here."""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from backend.scripts.evaluate_sql_live import (
    MANIFEST, QUESTIONS, load_questions, local_engine, rows_equal, safe_statement,
    Recorder, EvaluationStopped, score_case, summarize_live,
)
from backend.scripts.evaluate_sql_visualization import digest_bytes
from backend.scripts.score_sql_live_report import rescore


class LiveEvaluationContractTest(unittest.TestCase):
    def test_timeout_reaches_agent_retry_but_connection_failure_stops_evaluation(self):
        import httpx
        from openai import APITimeoutError, APIConnectionError
        request = httpx.Request('POST', 'https://example.invalid')
        for error, expected in ((APITimeoutError(request=request), APITimeoutError),
                                (APIConnectionError(request=request), EvaluationStopped)):
            recorder = Recorder(1)
            provider = MagicMock()
            provider.responses.parse.side_effect = error
            with self.subTest(error=type(error).__name__), patch('openai.OpenAI', return_value=provider), \
                    patch('httpx.Client'), recorder.factory(api_key='offline-test-only') as client:
                with self.assertRaises(expected):
                    client.responses.parse(model='gpt-4.1',input='test',instructions='test',max_output_tokens=10)
            self.assertEqual(recorder.calls[0]['error_type'],type(error).__name__)
            self.assertNotIn('estimated_cost_usd',recorder.calls[0])
            provider.close.assert_called_once()

    def test_saved_evidence_rescoring_does_not_count_schema_failure_as_explicit_refusal(self):
        cases=load_questions()
        report={'questions_sha256':digest_bytes(QUESTIONS.read_bytes()),'cases':[
            {'id':'Q33','category':'unsupported','expected_outcome':'reject','status':'FAILED',
             'correct':True,'refusal_correct':True,'observed_sql':[],
             'calls':[{'error_type':'ValidationError'}],'latency_seconds':1}]}
        scored=rescore(report,cases)
        self.assertEqual(scored['summary']['safe_nonexecution_rate']['passed'],1)
        self.assertEqual(scored['summary']['correct_refusal_rate']['passed'],0)
        scored['questions_sha256']='modified'
        with self.assertRaises(ValueError):
            rescore(scored,cases)

    def test_raw_response_usage_survives_failed_structured_parsing(self):
        entry={'error_type':'ValidationError'}
        Recorder.record_usage(entry,{'id':'synthetic-response','model':'gpt-4.1','status':'completed',
            'usage':{'input_tokens':1000,'input_tokens_details':{'cached_tokens':400},'output_tokens':100}})
        self.assertEqual(entry['error_type'],'ValidationError')
        self.assertEqual(entry['usage']['input_tokens'],1000)
        self.assertAlmostEqual(entry['estimated_cost_usd'],0.0022)

    def test_unordered_rows_preserve_multiplicity_and_numeric_tolerance(self):
        self.assertTrue(rows_equal([['CN',2.0001],['US',1]], [['US',1],['CN',2]]))
        self.assertFalse(rows_equal([['US',1],['US',1]], [['US',1],['CN',1]]))
        self.assertFalse(rows_equal([[None]], [[0]]))
        self.assertFalse(rows_equal([[True]], [[1]]))
        self.assertFalse(rows_equal([[2],[1]], [[1],[2]], ordered=True))
        # Matching must reassign an overlapping tolerance candidate, not use greedy removal.
        self.assertTrue(rows_equal([[0.9],[0]], [[0],[1.8]], absolute=1, relative=0))
        self.assertFalse(rows_equal([[float('nan')]], [[0]]))

    def test_wrong_units_or_extra_rows_fail_answer_accuracy(self):
        case={'expected_outcome':'answer','expected_rows':[[100]],'ordered':False,
              'absolute_tolerance':0.001,'relative_tolerance':0.000001,
              'units':['KG'],'expected_chart_type':None}
        analysis=SimpleNamespace(result=SimpleNamespace(status='OK'),columns=[{'key':'value','unit':'USD'}],rows=[{'value':100}])
        self.assertFalse(score_case(case,analysis,[{'completed':True}],None)['correct'])
        analysis.columns[0]['unit']='KG'
        self.assertTrue(score_case(case,analysis,[{'completed':True}],None)['correct'])
        analysis.rows.append({'value':100})
        self.assertFalse(score_case(case,analysis,[{'completed':True}],None)['correct'])

    def test_refusal_cannot_pass_after_sql_execution(self):
        case={'expected_outcome':'reject'}
        analysis=SimpleNamespace(result=SimpleNamespace(status='NO_DATA'))
        self.assertTrue(score_case(case,analysis,[],None,declared_refusal=True)['refusal_correct'])
        self.assertFalse(score_case(case,analysis,[{'completed':True}],None,declared_refusal=True)['refusal_correct'])
        analysis.result.status='FAILED'
        scores=score_case(case,analysis,[],None)
        self.assertTrue(scores['safe_nonexecution_correct'])
        self.assertFalse(scores['refusal_correct'])

    def test_supported_and_refusal_denominators_stay_separate(self):
        cases=[{'category':'metric'},{'category':'ambiguous'}]
        rows=[{'expected_outcome':'answer','category':'metric','correct':False,'execution_success':True,
               'chart_correct':True,'latency_seconds':1},
              {'expected_outcome':'reject','category':'ambiguous','correct':True,'refusal_correct':True,
               'chart_correct':True,'latency_seconds':2}]
        summary=summarize_live(rows,cases)
        self.assertEqual(summary['result_accuracy'],{'passed':0,'total':1,'rate_pct':0})
        self.assertEqual(summary['correct_refusal_rate'],{'passed':1,'total':1,'rate_pct':100})
        self.assertEqual(summary['overall_outcome_accuracy']['rate_pct'],50)

    def test_snapshot_connection_is_local_and_read_only_account(self):
        engine=local_engine(3308)
        self.addCleanup(engine.dispose)
        self.assertEqual((engine.url.host,engine.url.database,engine.url.username),
                         ('127.0.0.1','sql_agent_eval','eval_reader'))

    def test_independent_audit_rejects_forbidden_statements(self):
        self.assertTrue(safe_statement('SELECT country_code FROM country'))
        for statement in ('DELETE FROM export_summary','SELECT * FROM users',
                          'SELECT body_masked FROM article',"SELECT LOAD_FILE('/etc/passwd')",
                          'SELECT SLEEP(1)','SELECT @@version',
                          'SELECT 1; DELETE FROM country','SELECT country_code FROM country FOR UPDATE'):
            with self.subTest(statement=statement):
                self.assertFalse(safe_statement(statement))

    def test_dataset_is_frozen_and_review_status_is_not_fabricated(self):
        cases=load_questions()
        self.assertEqual(len(cases),40)
        self.assertEqual(sum(c['expected_outcome']=='answer' for c in cases),32)
        self.assertEqual(sum(c['expected_outcome']=='reject' for c in cases),8)
        self.assertTrue(all('expected_rows' in c for c in cases))
        manifest=json.loads(MANIFEST.read_text(encoding='utf-8'))
        self.assertEqual(manifest['questions_sha256'],digest_bytes(QUESTIONS.read_bytes()))
        if manifest['human_review']['status']=='approved':
            self.assertTrue(manifest['human_review']['reviewer'])
            self.assertEqual(manifest['human_review']['approved_questions_sha256'],manifest['questions_sha256'])
        else:
            self.assertEqual(manifest['human_review']['status'],'pending')


if __name__=='__main__':
    unittest.main()
