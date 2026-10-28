"""Conversation and search regressions: real API/worker, SQLite, mocked extraction only."""
import unittest
from copy import deepcopy
from datetime import date
from unittest.mock import patch
from sqlalchemy import update, delete
from backend.tests import test_backend_api as base
from backend.tests.intent_fixtures import BASE, DEC
from backend.app.api.worker import run_once


class ChatReviewRegressionTest(unittest.TestCase):
    setUp=base.BackendApiTest.setUp
    tearDown=base.BackendApiTest.tearDown
    signup=base.BackendApiTest.signup
    post=base.BackendApiTest.post

    def ask(self,question,sid=None,**slots):
        self.intent_mock.side_effect=None
        self.intent_mock.return_value=deepcopy({**BASE,'period':DEC,**slots})
        if sid is None: sid=self.post('chat/sessions',{}).json()['session']['session_id']
        response=self.post(f'chat/sessions/{sid}/messages',{'question':question,'client_message_id':question})
        self.assertEqual(response.status_code,202,response.text)
        jid=response.json()['job']['job_id']
        run_once(self.app.state.database,self.settings)
        return sid,jid,self.client.get(f'/api/v1/chat/jobs/{jid}').json()['job']

    def choose(self,jid,job,**selection):
        response=self.post(f'chat/jobs/{jid}/clarifications',{
            'clarification_id':job['clarification']['clarification_id'],**selection})
        self.assertEqual(response.status_code,202,response.text)
        run_once(self.app.state.database,self.settings)
        return self.client.get(f'/api/v1/chat/jobs/{jid}').json()['job']

    def test_repair_preserves_previous_context(self):
        self.signup()
        sid,_,first=self.ask('미국 수출',countries=[{'mention':'미국','name':'미국'}])
        self.assertEqual(first['answer']['conditions']['country_codes'],['US'])
        _,_,repair=self.ask('2030년 수출',sid,period={'start':'2030-01-01','end':'2030-12-31'})
        self.assertEqual(repair['answer']['answer_status'],'NO_DATA')
        _,_,answer=self.ask('그럼 수입은?',sid,period=None,trade_flow='IMPORT',all_products=False)
        self.assertEqual(answer['answer']['conditions']['country_codes'],['US'])
        self.assertEqual(answer['answer']['conditions']['period'],DEC)

    def test_provider_failure_preserves_context_and_explicit_reset_still_works(self):
        self.signup()
        sid,_,_=self.ask('미국 수출',countries=[{'mention':'미국','name':'미국'}])
        self.intent_mock.side_effect=TimeoutError('test timeout')
        jid=self.post(f'chat/sessions/{sid}/messages',{'question':'잠깐','client_message_id':'timeout'}).json()['job']['job_id']
        run_once(self.app.state.database,self.settings)
        self.assertEqual(self.client.get(f'/api/v1/chat/jobs/{jid}').json()['job']['answer']['answer_status'],'NO_DATA')
        _,_,answer=self.ask('그럼 수입?',sid,period=None,trade_flow='IMPORT',all_products=False)
        self.assertEqual(answer['answer']['conditions']['country_codes'],['US'])
        _,_,answer=self.ask('전체 국가 수입',sid,all_countries=True,trade_flow='IMPORT')
        self.assertEqual(answer['answer']['conditions']['country_codes'],[])

    def test_multiple_products_are_selected_separately_and_get_separate_tables(self):
        self.signup()
        with self.engine.begin() as c:
            c.execute(self.meta.tables['hs_code'].insert(),{'hs_code':'190230','hs_ver':'HS2022','depth':6,'name_ko':'면류','source_edition':'HS2022'})
        _,jid,job=self.ask('HS 330499,190230 수출 비교',products=['330499','190230'],all_products=False)
        old_id=job['clarification']['clarification_id']
        self.assertEqual([x['choice_id'] for x in job['clarification']['choices']],['330499'])
        job=self.choose(jid,job,choice_ids=['330499'])
        self.assertEqual(job['status'],'NEEDS_INPUT')
        self.assertEqual([x['choice_id'] for x in job['clarification']['choices']],['190230'])
        self.assertEqual(self.post(f'chat/jobs/{jid}/clarifications',{'clarification_id':old_id,'choice_ids':['330499']}).status_code,409)
        job=self.choose(jid,job,choice_ids=['190230'])
        self.assertEqual(job['answer']['answer_status'],'ANSWERED')
        self.assertEqual({x['hs_code'] for x in job['answer']['conditions']['hs_items']},{'330499','190230'})
        self.assertEqual([x['hs_item']['hs_code'] for x in job['answer']['tables']],['330499','190230'])
        self.assertEqual(job['answer']['tables'][0]['rows'][0]['export_amount_usd'],3460000)
        self.assertEqual(job['answer']['tables'][1]['rows'][0]['export_amount_usd'],0)
        self.assertEqual(self.intent_mock.call_count,1)

    def test_manual_second_selection_preserves_first_and_empty_candidates(self):
        self.signup()
        with self.engine.begin() as c:
            c.execute(self.meta.tables['hs_code'].insert(),{'hs_code':'190230','hs_ver':'HS2022','depth':6,'name_ko':'면류','source_edition':'HS2022'})
        _,jid,job=self.ask('화장품과 라면 수출',products=['화장품','라면'],all_products=False)
        job=self.choose(jid,job,choice_ids=['3304'])
        self.assertEqual(job['status'],'NEEDS_INPUT')
        self.assertEqual(job['clarification']['choices'],[])
        job=self.choose(jid,job,hs_item={'hs_code':'190230','hs_ver':'HS2022'})
        self.assertEqual([x['hs_code'] for x in job['answer']['conditions']['hs_items']],['3304','190230'])
        self.assertEqual(self.intent_mock.call_count,1)

    def test_product_and_country_rankings_are_distinct(self):
        self.signup()
        for group in ('HS6','COUNTRY'):
            with self.subTest(group=group):
                _,_,job=self.ask(f'2025년 12월 {group} 순위',intent='RANKING',ranking_group=group)
                self.assertEqual(job['answer']['answer_status'],'ANSWERED')
                rows=job['answer']['tables'][0]['rows']
                self.assertTrue(rows)
                self.assertTrue(all((r['hs_item'] is not None)==(group=='HS6') for r in rows))

    def test_ambiguous_ranking_requests_target_instead_of_assuming_country(self):
        self.signup()
        _,_,job=self.ask('순위 알려줘',intent='RANKING')
        self.assertEqual(job['answer']['answer_status'],'NO_DATA')
        self.assertIn('어떤 순위',job['answer']['text'])

    def test_import_comparison_uses_prior_import_values_and_growth(self):
        self.signup()
        t=self.meta.tables['import_summary']
        with self.engine.begin() as c:
            c.execute(update(t).values(amount_usd=100))
            c.execute(update(t).where(t.c.trade_month==date(2025,12,1)).values(amount_usd=150))
        _,_,job=self.ask('2025년 12월 수입을 전년 동월과 비교해줘',trade_flow='IMPORT',
            compare_period={'start':'2024-12-01','end':'2024-12-31'})
        self.assertEqual(job['answer']['answer_status'],'ANSWERED')
        row=job['answer']['tables'][0]['rows'][0]
        self.assertEqual(row['previous_year_import_usd'],200)
        self.assertEqual(row['import_amount_usd'],300)
        self.assertIn('전년 동기 200.00 USD',job['answer']['text'])
        self.assertIn('50.00%',job['answer']['text'])

    def test_verified_missing_import_is_zero_but_unverified_prior_month_is_null(self):
        self.signup()
        t=self.meta.tables['import_summary']; b=self.meta.tables['batch_run']
        with self.engine.begin() as c: c.execute(delete(t).where(t.c.trade_month==date(2024,12,1)))
        _,_,job=self.ask('전년 대비 수입',trade_flow='IMPORT',compare_period={'start':'2024-12-01','end':'2024-12-31'})
        self.assertEqual(job['answer']['tables'][0]['rows'][0]['previous_year_import_usd'],0)
        self.assertIn('증감률은 계산하지 않습니다',job['answer']['text'])
        with self.engine.begin() as c:
            c.execute(b.insert(),{'source_code':'CUSTOMS','job_name':'HS6_AGGREGATION','target_table':'import_summary',
                'period_start':date(2024,12,1),'period_end':date(2024,12,31),'status':'FAILED','detail_json':{}})
        response=self.client.get('/api/v1/trade/trends?period_start=2025-12&period_end=2025-12')
        self.assertEqual(response.status_code,200,response.text)
        self.assertIsNone(response.json()['items'][0]['previous_year_import_usd'])
        _,_,job=self.ask('전년 대비 수입 다시',trade_flow='IMPORT',compare_period={'start':'2024-12-01','end':'2024-12-31'})
        self.assertEqual(job['answer']['answer_status'],'NO_DATA')

    def test_invalid_vector_winner_does_not_boost_other_candidate(self):
        self.settings.values['API_HS_VECTOR_ENABLED']='true'
        with patch('backend.app.api.hs_vectors.candidates',return_value=[('9999',.10),('3304',.65)]):
            value=self.client.get('/api/v1/reference/hs-codes?q=스킨케어').json()
        self.assertEqual(value['items'][0]['hs_code'],'3304')
        self.assertEqual(value['confidence'],'LOW')

    def test_confidence_uses_valid_distinct_codes(self):
        self.settings.values['API_HS_VECTOR_ENABLED']='true'
        with self.engine.begin() as c:
            c.execute(self.meta.tables['hs_code'].insert(),{'hs_code':'1902','hs_ver':'HS2022','depth':4,'name_ko':'면류','source_edition':'HS2022'})
        with patch('backend.app.api.hs_vectors.candidates',return_value=[('9999',.01),('3304',.20),('3304',.21),('1902',.40)]):
            value=self.client.get('/api/v1/reference/hs-codes?q=스킨케어').json()
        self.assertEqual(value['confidence'],'MEDIUM')
        self.assertEqual([r['hs_code'] for r in value['items']],['3304','1902'])


if __name__=='__main__': unittest.main()
