import unittest
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch, MagicMock
from sqlalchemy import select
from backend.tests import test_backend_api as base_tests
from backend.tests.intent_fixtures import BASE, DEC
from backend.app.api.worker import run_once
from backend.app.api.search_cache import SearchCache


class IntentSearchTest(unittest.TestCase):
    setUp=base_tests.BackendApiTest.setUp
    tearDown=base_tests.BackendApiTest.tearDown
    signup=base_tests.BackendApiTest.signup
    post=base_tests.BackendApiTest.post

    def ask(self,question,**slots):
        self.intent_mock.side_effect=None
        self.intent_mock.return_value=deepcopy({**BASE,'period':DEC,**slots})
        sid=self.post('chat/sessions',{}).json()['session']['session_id']
        jid=self.post(f'chat/sessions/{sid}/messages',{'question':question,'client_message_id':sid}).json()['job']['job_id']
        run_once(self.app.state.database,self.settings)
        return jid,self.client.get(f'/api/v1/chat/jobs/{jid}').json()['job']

    def test_ambiguous_country_abbreviations_are_not_trusted(self):
        self.signup()
        with self.engine.begin() as c:
            c.execute(self.meta.tables['country'].insert(),[
                {'country_code':'SK','name_ko':'슬로바키아','country_role':'COUNTRY'},
                {'country_code':'TV','name_ko':'투발루','country_role':'COUNTRY'}])
        for mention,name in [('SK','슬로바키아'),('TV','투발루')]:
            _,job=self.ask(f'{mention} 수출',countries=[{'mention':mention,'name':name}])
            self.assertEqual(job['status'],'SUCCEEDED')
            self.assertEqual(job['answer']['answer_status'],'ANSWERED')
            self.assertEqual(job['answer']['conditions']['country_codes'],[])
            self.assertTrue(job['answer']['warnings'])

    def test_year_product_invalid_country_and_period_do_not_fail_jobs(self):
        self.signup()
        for slots in [
            {'products':['2024'],'all_products':False},
            {'countries':[{'mention':'없는나라','name':'없는나라'}]},
            {'period':{'start':'2025-13-01','end':'2025-13-31'}},
            {'period':{'start':'2030-01-01','end':'2030-12-31'}},
            {'recent_months':999,'period':None},
        ]:
            with self.subTest(slots=slots):
                _,job=self.ask('없는나라 2024 대비 수출',**slots)
                self.assertEqual(job['status'],'SUCCEEDED')
                self.assertEqual(job['answer']['answer_status'],'NO_DATA')

    def test_slots_with_fabricated_source_evidence_are_rejected(self):
        self.signup()
        _,job=self.ask('미국 수출',products=['라면'],all_products=False)
        self.assertEqual(job['answer']['answer_status'],'NO_DATA')

    def test_product_late_in_question_and_manual_selection_reuses_intent(self):
        self.signup()
        with self.engine.begin() as c:
            c.execute(self.meta.tables['hs_code'].insert(),{'hs_code':'190230','hs_ver':'HS2022','depth':6,'name_ko':'면류','source_edition':'HS2022'})
        candidate={'hs_code':'3304','hs_ver':'HS2022','name_ko':'예시 후보','depth':4}
        with patch('backend.app.api.reference.hs_search',return_value={'items':[candidate]}) as search:
            jid,job=self.ask('미국 시장에서 요즘 많이 팔리는 라면 수출 추이를 알려줘',
                products=['라면'],all_products=False,countries=[{'mention':'미국','name':'미국'}])
        self.assertEqual(search.call_args.args[1],'라면')
        self.assertEqual(job['status'],'NEEDS_INPUT')
        self.assertIn('/ROOT/',job['clarification']['browse_url'])
        result=self.post(f'chat/jobs/{jid}/clarifications',{'clarification_id':job['clarification']['clarification_id'],
            'hs_item':{'hs_code':'190230','hs_ver':'HS2022'}})
        self.assertEqual(result.status_code,202,result.text)
        run_once(self.app.state.database,self.settings)
        answer=self.client.get(f'/api/v1/chat/jobs/{jid}').json()['job']['answer']
        self.assertEqual(answer['conditions']['hs_items'][0]['hs_code'],'190230')
        self.assertEqual(answer['conditions']['country_codes'],['US'])
        self.assertEqual(self.intent_mock.call_count,1)

    def test_invalid_manual_hs_cannot_resume_job(self):
        self.signup()
        jid,job=self.ask('화장품 수출',products=['화장품'],all_products=False)
        result=self.post(f'chat/jobs/{jid}/clarifications',{'clarification_id':job['clarification']['clarification_id'],
            'hs_item':{'hs_code':'999999','hs_ver':'HS2022'}})
        self.assertEqual(result.status_code,400)
        self.assertEqual(self.client.get(f'/api/v1/chat/jobs/{jid}').json()['job']['status'],'NEEDS_INPUT')

    def test_llm_intent_controls_dispatch_without_keyword_rules(self):
        self.signup()
        _,job=self.ask('Which country leads?',intent='RANKING',trade_flow='IMPORT',ranking_group='COUNTRY')
        self.assertEqual(job['answer']['answer_status'],'ANSWERED')
        self.assertEqual(job['answer']['conditions']['trade_flow'],'IMPORT')
        self.assertIn('순위',job['answer']['text'])

    def test_no_candidates_still_allows_hierarchy_selection(self):
        self.signup()
        jid,job=self.ask('김치 수출',products=['김치'],all_products=False)
        self.assertEqual(job['status'],'NEEDS_INPUT'); self.assertEqual(job['clarification']['choices'],[])
        response=self.post(f'chat/jobs/{jid}/clarifications',{'clarification_id':job['clarification']['clarification_id'],
            'hs_item':{'hs_code':'330499','hs_ver':'HS2022'}})
        self.assertEqual(response.status_code,202,response.text)

    def test_explicit_hs6_is_verified_in_database(self):
        self.signup()
        _,job=self.ask('HS 330499 수출',products=['330499'],all_products=False)
        self.assertEqual(job['clarification']['choices'][0]['hs_item']['hs_code'],'330499')

    def test_explicit_code_list_is_source_validated(self):
        self.signup()
        _,job=self.ask('HS 3304,330499 수출',products=['3304','330499'],all_products=False)
        self.assertEqual(job['status'],'NEEDS_INPUT')
        self.assertEqual({x['hs_item']['hs_code'] for x in job['clarification']['choices']},{'3304'})
        self.assertIn('남은 품목 2개',job['clarification']['message'])

    def test_malformed_provider_output_requests_rephrase(self):
        self.signup()
        self.intent_mock.side_effect=ValueError('invalid structured output')
        sid=self.post('chat/sessions',{}).json()['session']['session_id']
        jid=self.post(f'chat/sessions/{sid}/messages',{'question':'수출','client_message_id':'bad-slots'}).json()['job']['job_id']
        run_once(self.app.state.database,self.settings)
        job=self.client.get(f'/api/v1/chat/jobs/{jid}').json()['job']
        self.assertEqual(job['status'],'SUCCEEDED'); self.assertEqual(job['answer']['answer_status'],'NO_DATA')

    def test_search_cache_and_public_ip_limit(self):
        self.settings.values.update(API_HS_VECTOR_ENABLED='true',API_HS_IP_LIMIT='2')
        with patch('backend.app.api.hs_vectors.candidates',return_value=[('3304',.60)]) as embed:
            first=self.client.get('/api/v1/reference/hs-codes?q=스킨케어')
            second=self.client.get('/api/v1/reference/hs-codes?q=%20스킨케어%20')
            blocked=self.client.get('/api/v1/reference/hs-codes?q=스킨케어',headers={'X-Forwarded-For':'203.0.113.99'})
        self.assertEqual(first.status_code,200); self.assertEqual(second.status_code,200)
        self.assertEqual(first.json(),second.json()); self.assertEqual(embed.call_count,1)
        self.assertEqual(blocked.status_code,429)

    def test_short_and_long_queries_never_embed(self):
        with patch('backend.app.api.hs_vectors.candidates') as embed:
            self.assertEqual(self.client.get('/api/v1/reference/hs-codes?q=라').json()['items'],[])
            self.assertEqual(self.client.get('/api/v1/reference/hs-codes',params={'q':'가'*121}).status_code,400)
            embed.assert_not_called()

    def test_close_distances_are_only_candidates_and_offer_hierarchy(self):
        self.settings.values['API_HS_VECTOR_ENABLED']='true'
        with patch('backend.app.api.hs_vectors.candidates',return_value=[('3304',.40),('9999',.41)]):
            value=self.client.get('/api/v1/reference/hs-codes?q=라면').json()
        self.assertEqual(value['confidence'],'LOW'); self.assertEqual(value['match_type'],'SIMILAR')
        self.assertTrue(value['requires_selection']); self.assertIn('직접 선택',value['message'])
        with self.engine.begin() as c:
            c.execute(self.meta.tables['hs_code'].insert(),{'hs_code':'33','hs_ver':'HS2022','depth':2,'name_ko':'화장품류','source_edition':'HS2022'})
        for parent,code in [('ROOT','33'),('33','3304'),('3304','330499')]:
            response=self.client.get(f'/api/v1/reference/hs-codes/{parent}/children?hs_ver=HS2022')
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['items'][0]['hs_code'],code)

    def test_exact_search_does_not_embed(self):
        with patch('backend.app.api.hs_vectors.candidates') as embed:
            value=self.client.get('/api/v1/reference/hs-codes?q=3304').json()
        self.assertEqual(value['confidence'],'EXACT'); embed.assert_not_called()


class SearchInfrastructureTest(unittest.TestCase):
    def test_cache_coalesces_concurrent_requests_and_returns_copies(self):
        cache=SearchCache(); calls=[]
        def loader(): calls.append(1); return {'items':['ok']}
        with ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(lambda _:cache.get_or_load('same',loader),range(16)))
        self.assertEqual(len(calls),1)
        results[0]['items'].clear()
        self.assertEqual(cache.get_or_load('same',loader)['items'],['ok'])

    def test_cache_expiry_eviction_and_errors(self):
        tick=[0]; cache=SearchCache(maximum=2,ttl=10,clock=lambda:tick[0]); calls=[]
        def loader(): calls.append(1); return len(calls)
        self.assertEqual(cache.get_or_load('a',loader),1)
        tick[0]=11; self.assertEqual(cache.get_or_load('a',loader),2)
        cache.get_or_load('b',loader); cache.get_or_load('c',loader)
        self.assertEqual(len(cache.entries),2); self.assertNotIn('a',cache.entries)
        with self.assertRaises(ValueError): cache.get_or_load('error',lambda:(_ for _ in ()).throw(ValueError()))
        self.assertNotIn('error',cache.entries)

    def test_embedding_is_reused_when_result_limit_changes(self):
        from backend.app.api import hs_vectors
        from backend.app.api.config import Settings
        client=MagicMock(); client.__enter__.return_value=client
        client.embeddings.create.return_value.data[0].embedding=[.1,.2]
        col=MagicMock(); col.count.return_value=2
        col.query.return_value={'metadatas':[[{'hs_code':'3304'}]],'distances':[[.6]]}
        with patch.object(hs_vectors,'VECTOR_CACHE',SearchCache()), patch.object(hs_vectors,'collection',return_value=col), patch('openai.OpenAI',return_value=client) as factory:
            settings=Settings(values={'OPENAI_API_KEY':'test-key'})
            hs_vectors.candidates('화장품',settings,1); hs_vectors.candidates('화장품',settings,2)
        self.assertEqual(client.embeddings.create.call_count,1)
        self.assertEqual(factory.call_args.kwargs['max_retries'],0)

    def test_provider_uses_single_structured_call_and_no_storage(self):
        from backend.app.api.intent import OpenAIIntentProvider, IntentSlots
        from backend.app.api.config import Settings
        client=MagicMock(); client.__enter__.return_value=client
        client.responses.parse.return_value.output_parsed=IntentSlots.model_validate(BASE)
        with patch('openai.OpenAI',return_value=client) as factory:
            result=OpenAIIntentProvider(Settings(values={'OPENAI_API_KEY':'test-key'})).extract(question='수출',previous_conditions={})
        self.assertEqual(result['intent'],'STATISTICS')
        client.responses.parse.assert_called_once()
        self.assertFalse(client.responses.parse.call_args.kwargs['store'])
        self.assertEqual(factory.call_args.kwargs['max_retries'],0)
