"""PR regressions for trade gaps, HS6-first chat lookup, slot validation and search recovery."""
import unittest
from datetime import date
from unittest.mock import patch, MagicMock
from sqlalchemy import delete, select, update
from backend.tests import test_chat_review_regressions as previous
from backend.app.api.search_cache import SearchCache
from backend.app.api.config import Settings


class SearchChangesTest(unittest.TestCase):
    setUp=previous.ChatReviewRegressionTest.setUp
    tearDown=previous.ChatReviewRegressionTest.tearDown
    signup=previous.ChatReviewRegressionTest.signup
    post=previous.ChatReviewRegressionTest.post
    ask=previous.ChatReviewRegressionTest.ask
    choose=previous.ChatReviewRegressionTest.choose

    def add_hs(self,code,name):
        with self.engine.begin() as c:
            c.execute(self.meta.tables['hs_code'].insert(),{'hs_code':code,'hs_ver':'HS2022',
                'depth':len(code),'name_ko':name,'source_edition':'HS2022'})

    def seed_changes(self,old_month,current_amount=3000000,old_amount=3000000):
        t=self.meta.tables['export_summary']
        with self.engine.begin() as c:
            c.execute(delete(t).where(t.c.trade_month.in_([old_month,date(2025,12,1)])))
            c.execute(t.insert(),[
                {'trade_month':date(2025,12,1),'country_code':'US','hs6_code':'330499','hs_ver':'HS2022','amount_usd':current_amount},
                {'trade_month':old_month,'country_code':'CN','hs6_code':'330499','hs_ver':'HS2022','amount_usd':old_amount}])

    def test_missing_trade_is_new_or_stopped_for_mom_and_yoy(self):
        self.signup()
        for compare,old_month in [('MOM',date(2025,11,1)),('YOY',date(2024,12,1))]:
            with self.subTest(compare=compare):
                self.seed_changes(old_month)
                response=self.post('analysis/changes',{'month':'2025-12','compare':compare})
                self.assertEqual(response.status_code,200,response.text)
                value=response.json(); self.assertEqual(value['surges'],[])
                self.assertEqual(len(value['new_entries']),1); self.assertEqual(len(value['drops']),1)
                new=value['new_entries'][0]; stopped=value['drops'][0]
                self.assertEqual((new['country_code'],new['baseline_amount_usd'],new['current_amount_usd']),('US',0,3000000))
                self.assertEqual(new['growth_status'],'NEW'); self.assertIsNone(new['growth_pct'])
                self.assertEqual((stopped['country_code'],stopped['current_amount_usd'],stopped['growth_pct']),('CN',0,-100))
                self.assertEqual(value['table']['total_rows'],2)

    def test_unverified_month_never_becomes_zero_trade(self):
        self.signup(); self.seed_changes(date(2025,11,1))
        b=self.meta.tables['batch_run']
        for month in (date(2025,11,1),date(2025,12,1)):
            with self.subTest(month=month):
                with self.engine.begin() as c:
                    result=c.execute(b.insert(),{'source_code':'CUSTOMS','job_name':'HS6_AGGREGATION','target_table':'export_summary',
                        'period_start':month,'period_end':date(month.year,month.month,30 if month.month==11 else 31),
                        'status':'FAILED','detail_json':{}})
                    identity=result.inserted_primary_key[0]
                response=self.post('analysis/changes',{'month':'2025-12','compare':'MOM'})
                self.assertIn(response.status_code,(422,503),response.text)
                with self.engine.begin() as c: c.execute(delete(b).where(b.c.run_id==identity))

    def test_threshold_and_scope_filters_still_apply_to_missing_trades(self):
        self.signup(); self.seed_changes(date(2025,11,1),999999,999999)
        value=self.post('analysis/changes',{'month':'2025-12','compare':'MOM'}).json()
        self.assertEqual(value['new_entries'],[]); self.assertEqual(value['drops'],[])
        self.seed_changes(date(2025,11,1))
        value=self.post('analysis/changes',{'month':'2025-12','compare':'MOM','country_codes':['CN']}).json()
        self.assertEqual(value['new_entries'],[]); self.assertEqual(value['drops'][0]['country_code'],'CN')

    def test_country_short_names_map_to_one_database_country(self):
        self.signup()
        with self.engine.begin() as c:
            c.execute(self.meta.tables['country'].insert(),[
                {'country_code':'CZ','name_ko':'체코공화국','country_role':'COUNTRY'},
                {'country_code':'RU','name_ko':'러시아 연방','country_role':'COUNTRY'},
                {'country_code':'AE','name_ko':'아랍에미리트 연합','country_role':'COUNTRY'}])
        for mention,name,code in [('체코','체코','CZ'),('러시아','러시아','RU'),('UAE','아랍에미리트','AE'),
                                  ('러시아연방','러시아연방','RU')]:
            with self.subTest(mention=mention):
                _,_,job=self.ask(mention+' 수출',countries=[{'mention':mention,'name':name}])
                self.assertEqual(job['answer']['conditions']['country_codes'],[code])

    def test_ambiguous_country_is_not_silently_dropped_with_another_valid_country(self):
        self.signup()
        with self.engine.begin() as c:
            c.execute(self.meta.tables['country'].insert(),[
                {'country_code':'CG','name_ko':'콩고공화국','country_role':'COUNTRY'},
                {'country_code':'CD','name_ko':'콩고민주공화국','country_role':'COUNTRY'}])
        _,_,job=self.ask('미국과 콩고 수출',countries=[{'mention':'미국','name':'미국'},{'mention':'콩고','name':'콩고'}])
        self.assertEqual(job['answer']['answer_status'],'NO_DATA')
        self.assertIn('여러 개',job['answer']['text'])

    def test_exact_country_name_wins_over_longer_partial_match(self):
        self.signup()
        with self.engine.begin() as c:
            c.execute(self.meta.tables['country'].insert(),[
                {'country_code':'IN','name_ko':'인도','country_role':'COUNTRY'},
                {'country_code':'ID','name_ko':'인도네시아','country_role':'COUNTRY'}])
        _,_,job=self.ask('인도 수출',countries=[{'mention':'인도','name':'인도'}])
        self.assertEqual(job['answer']['conditions']['country_codes'],['IN'])

    def test_bare_hs4_and_hs6_codes_are_dictionary_verified(self):
        self.signup(); self.add_hs('8541','반도체')
        for code in ('8541','330499'):
            with self.subTest(code=code):
                _,jid,job=self.ask(code+' 미국 수출',products=[code],all_products=False,
                    countries=[{'mention':'미국','name':'미국'}])
                self.assertEqual(job['status'],'NEEDS_INPUT')
                self.assertEqual(job['clarification']['choices'][0]['hs_item']['hs_code'],code)
                final=self.choose(jid,job,choice_ids=[code])
                self.assertEqual(final['answer']['conditions']['hs_items'][0]['hs_code'],code)

    def test_registered_year_like_code_allowed_but_year_amount_tokens_rejected(self):
        self.signup(); self.add_hs('2001','조제 채소'); self.add_hs('1000','테스트 코드')
        _,_,job=self.ask('2001 수출',products=['2001'],all_products=False)
        self.assertEqual(job['status'],'NEEDS_INPUT')
        for q,code in [('2001년 수출','2001'),('2001 대비 수출','2001'),('1000만 달러 수출','1000'),
                       ('1000.50달러','1000'),('2024년 수출','2024'),('999999 수출','999999')]:
            with self.subTest(q=q):
                _,_,job=self.ask(q,products=[code],all_products=False)
                self.assertEqual(job['answer']['answer_status'],'NO_DATA')

    def test_natural_product_exact_hs6_precedes_hs4_and_completes_chat(self):
        self.signup()
        with patch('backend.app.api.hs_vectors.candidates') as vectors:
            _,jid,job=self.ask('기초화장품 미국 수출',products=['기초화장품'],all_products=False)
        vectors.assert_not_called()
        self.assertEqual([c['hs_item']['hs_code'] for c in job['clarification']['choices']],['330499'])
        job=self.choose(jid,job,choice_ids=['330499'])
        self.assertEqual(job['answer']['answer_status'],'ANSWERED')
        self.assertEqual(self.intent_mock.call_count,1)

    def test_clear_hs6_embedding_is_offered_and_public_search_stays_hs4(self):
        self.signup(); self.add_hs('330410','입술 화장품')
        self.settings.values['API_HS_VECTOR_ENABLED']='true'
        def ranked(query,settings,limit,*,depth):
            return [('330499',.20),('330410',.40)] if depth==6 else [('3304',.50)]
        with patch('backend.app.api.hs_vectors.candidates',side_effect=ranked):
            _,_,job=self.ask('스킨케어 수출',products=['스킨케어'],all_products=False)
            value=self.client.get('/api/v1/reference/hs-codes?q=스킨케어').json()
        self.assertEqual(job['clarification']['choices'][0]['hs_item']['hs_code'],'330499')
        self.assertEqual([r['depth'] for r in value['items']],[4])

    def test_ambiguous_hs6_embedding_falls_back_to_hs4(self):
        self.signup(); self.add_hs('330410','입술 화장품')
        self.settings.values['API_HS_VECTOR_ENABLED']='true'
        def ranked(query,settings,limit,*,depth):
            return [('330499',.40),('330410',.41)] if depth==6 else [('3304',.50)]
        with patch('backend.app.api.hs_vectors.candidates',side_effect=ranked) as vectors:
            _,_,job=self.ask('스킨케어 수출',products=['스킨케어'],all_products=False)
        self.assertEqual([c['hs_item']['hs_code'] for c in job['clarification']['choices']],['3304'])
        self.assertIn('HS4',job['clarification']['message'])
        self.assertEqual([call.kwargs['depth'] for call in vectors.call_args_list],[6,4])

    def test_duplicate_exact_hs6_names_require_hs4_confirmation(self):
        self.signup(); self.add_hs('330410','기초화장품')
        with self.engine.begin() as c:
            c.execute(update(self.meta.tables['hs_code']).where(self.meta.tables['hs_code'].c.hs_code=='3304').values(original_name_ko='기초화장품'))
        _,_,job=self.ask('기초화장품 수출',products=['기초화장품'],all_products=False)
        self.assertEqual([c['choice_id'] for c in job['clarification']['choices']],['3304'])

    def test_embedding_failure_is_not_cached_but_recovery_is_cached(self):
        self.settings.values['API_HS_VECTOR_ENABLED']='true'
        with patch('backend.app.api.hs_vectors.candidates',side_effect=[TimeoutError(),[('3304',.50)]]) as vectors:
            failed=self.client.get('/api/v1/reference/hs-codes?q=스킨케어').json()
            recovered=self.client.get('/api/v1/reference/hs-codes?q=스킨케어').json()
            cached=self.client.get('/api/v1/reference/hs-codes?q=스킨케어').json()
        self.assertTrue(failed['warnings']); self.assertNotIn('warnings',recovered)
        self.assertEqual(recovered['items'][0]['hs_code'],'3304')
        self.assertEqual(cached,recovered); self.assertEqual(vectors.call_count,2)

    def test_subscription_saves_registered_interests_and_rejects_false(self):
        uid=self.signup()
        with self.engine.begin() as c:
            c.execute(self.meta.tables['user_interest'].insert(),{'user_id':int(uid),'country_code':'US'})
        url='/api/v1/users/me/subscriptions'
        version=self.client.get(url).json()['version']
        data={'period_codes':['MONTHLY'],'formats':['PDF'],'use_saved_interests':True,'expected_version':version}
        response=self.client.put(url,json=data,headers={'X-CSRF-Token':self.csrf})
        self.assertEqual(response.status_code,200,response.text)
        with self.engine.connect() as c:
            saved=c.execute(select(self.meta.tables['report_subscription'].c.filters_json)).scalar_one()
        self.assertEqual(saved['countries'],['US'])
        response=self.client.put(url,json={**data,'use_saved_interests':False,'expected_version':response.json()['version']},
            headers={'X-CSRF-Token':self.csrf})
        self.assertEqual(response.status_code,400,response.text)


class VectorDepthTest(unittest.TestCase):
    def test_real_chroma_depth_filter_and_shared_query_embedding(self):
        import tempfile
        import chromadb
        from backend.app.api import hs_vectors
        with tempfile.TemporaryDirectory(dir='tmp/backend_build',ignore_cleanup_errors=True) as folder:
            client=chromadb.PersistentClient(path=folder)
            col=client.create_collection('depth_test',embedding_function=None)
            col.add(ids=['four','six'],embeddings=[[1.,0.],[.9,.1]],metadatas=[
                {'hs_code':'3304','hs_ver':'HS2022','depth':4},
                {'hs_code':'330499','hs_ver':'HS2022','depth':6}])
            api=MagicMock(); api.__enter__.return_value=api
            api.embeddings.create.return_value.data[0].embedding=[1.,0.]
            with patch.object(hs_vectors,'collection',return_value=col),patch.object(hs_vectors,'VECTOR_CACHE',SearchCache()),patch('openai.OpenAI',return_value=api):
                settings=Settings(values={'OPENAI_API_KEY':'test-key'})
                six=hs_vectors.candidates('스킨케어',settings,depth=6)
                four=hs_vectors.candidates('스킨케어',settings,depth=4)
            self.assertEqual([r[0] for r in six],['330499'])
            self.assertEqual([r[0] for r in four],['3304'])
            self.assertEqual(api.embeddings.create.call_count,1)


if __name__=='__main__': unittest.main()
