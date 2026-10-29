"""Review regressions with isolated SQLite and mocked external extraction only."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from sqlalchemy import delete, select, text, update
from backend.tests import test_backend_api as base
from backend.tests import test_chat_review_regressions as chat_fixtures
from backend.app.api import jobs, news
from backend.app.api.common import ApiError
from backend.app.api.database import Store
from backend.app.api.results import result_table
from backend.app.services.report_pdf import font_css
from backend.scripts.migrate_integration import plan


class FollowupRegressionTest(unittest.TestCase):
    setUp=base.BackendApiTest.setUp
    tearDown=base.BackendApiTest.tearDown
    signup=base.BackendApiTest.signup
    post=base.BackendApiTest.post
    ask=chat_fixtures.ChatReviewRegressionTest.ask
    choose=chat_fixtures.ChatReviewRegressionTest.choose

    def test_missing_notification_row_rejects_keyword_save(self):
        uid=int(self.signup())
        with self.engine.begin() as c:
            c.execute(delete(self.meta.tables['notification_setting']))
            store=Store(self.app.state.database,c)
            version=news.blocks(store,uid)['settings']['version']
            with self.assertRaises(ApiError) as caught: news.save_blocks(store,uid,[],version)
            self.assertEqual((caught.exception.status,caught.exception.code),(503,'FEATURE_NOT_READY'))
            self.assertIsNone(c.scalar(select(self.meta.tables['notification_setting'].c.user_id)))

    def test_existing_keyword_setting_is_saved_and_stale_version_rejected(self):
        uid=int(self.signup())
        with self.engine.begin() as c:
            c.execute(self.meta.tables['keyword_topic'].insert(),{'keyword_text':'전기자동차','status':'UNCLASSIFIED'})
            store=Store(self.app.state.database,c)
            version=news.blocks(store,uid)['settings']['version']
            result=news.save_blocks(store,uid,['전기자동차'],version)
            self.assertEqual(result['settings']['items'][0]['kw_topic'],'전기자동차')
            with self.assertRaises(ApiError) as caught: news.save_blocks(store,uid,[],version)
            self.assertEqual(caught.exception.code,'VERSION_CONFLICT')

    def test_multi_item_comparison_does_not_depend_on_item_order(self):
        self.signup()
        with self.engine.begin() as c:
            c.execute(self.meta.tables['hs_code'].insert(),{'hs_code':'190230','hs_ver':'HS2022',
                'depth':6,'name_ko':'면류','source_edition':'HS2022'})
        def trends(store,uid,start,end,*,hs,countries,flow):
            row={'month':'2025-12','export_amount_usd':200,'import_amount_usd':300,
                 'previous_year_export_usd':100 if hs[0]['hs_code']=='330499' else None,
                 'previous_year_import_usd':150 if hs[0]['hs_code']=='330499' else None}
            table=result_table(store,uid,[row],{})
            return {'items':[row],'result_id':table['result_id']}
        with patch('backend.app.api.trade.trends',side_effect=trends):
            for flow in ('EXPORT','IMPORT'):
                texts=[]
                for order in (['330499','190230'],['190230','330499']):
                    _,jid,job=self.ask(f'HS {",".join(order)} {flow} 비교',products=order,all_products=False,trade_flow=flow)
                    for code in order:
                        self.assertEqual(job['status'],'NEEDS_INPUT',job)
                        job=self.choose(jid,job,choice_ids=[code])
                    answer=job['answer']
                    self.assertEqual(answer['answer_status'],'ANSWERED')
                    self.assertIsNone(answer['conditions']['compare_period'])
                    self.assertEqual(len(answer['tables']),2)
                    self.assertEqual(answer['text'].count('증감률'),1)
                    self.assertTrue(answer['warnings'])
                    texts.append(sorted(answer['text'].splitlines()))
                self.assertEqual(texts[0],texts[1])

    def test_chat_job_is_evaluated_once_per_message(self):
        self.signup()
        sid=self.post('chat/sessions',{}).json()['session']['session_id']
        self.post(f'chat/sessions/{sid}/messages',{'question':'미국 수출','client_message_id':'count'})
        with patch('backend.app.api.jobs._chat_job',wraps=jobs._chat_job) as mapped:
            r=self.client.get(f'/api/v1/chat/sessions/{sid}/messages')
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(mapped.call_count,len(r.json()['items']))
        self.assertEqual(r.json()['items'][0]['job']['status'],'QUEUED')

    def test_automatic_comparison_does_not_become_required_in_followup(self):
        from datetime import date
        self.signup()
        sid,_,first=self.ask('수출 추이')
        self.assertIsNotNone(first['answer']['conditions']['compare_period'])
        with self.engine.begin() as c:
            c.execute(self.meta.tables['batch_run'].insert(),{'source_code':'CUSTOMS','job_name':'HS6_AGGREGATION',
                'target_table':'import_summary','period_start':date(2024,12,1),
                'period_end':date(2024,12,31),'status':'FAILED','detail_json':{}})
        _,_,second=self.ask('그럼 수입은?',sid,period=None,all_products=False,trade_flow='IMPORT')
        self.assertEqual(second['answer']['answer_status'],'ANSWERED')
        self.assertIsNone(second['answer']['conditions']['compare_period'])
        self.assertTrue(second['answer']['warnings'])

    def test_explicit_comparison_is_preserved_in_followup_context(self):
        self.signup()
        comparison={'start':'2024-12-01','end':'2024-12-31'}
        sid,_,first=self.ask('전년 12월과 수출 비교',compare_period=comparison)
        self.assertEqual(first['answer']['conditions']['compare_period'],comparison)
        saved=self.client.get(f'/api/v1/chat/sessions/{sid}/messages').json()['session']['context']
        self.assertEqual(saved['compare_period'],comparison)
        _,_,second=self.ask('그럼 수입은?',sid,period=None,all_products=False,trade_flow='IMPORT')
        self.assertEqual(second['answer']['conditions']['compare_period'],comparison)
        self.assertEqual(second['answer']['answer_status'],'ANSWERED')

    def test_article_answer_does_not_mislabel_other_sources_as_kotra(self):
        self.signup(); base.BackendApiTest.seed_review_articles(self)
        with self.engine.begin() as c:
            c.execute(update(self.meta.tables['article']).values(source_code='HANKYUNG'))
        _,_,job=self.ask('12월 기사',intent='LOCAL_ISSUES')
        self.assertTrue(job['answer']['articles'])
        self.assertTrue(all(a['source_code']=='HANKYUNG' for a in job['answer']['articles']))
        self.assertNotIn('KOTRA',job['answer']['text'])

    def test_legacy_migration_stops_without_touching_rows(self):
        with self.engine.begin() as c:
            c.execute(text('CREATE TABLE api_user_profile (user_id INTEGER PRIMARY KEY, name TEXT)'))
            c.execute(text("INSERT INTO api_user_profile VALUES (1,'preserve')"))
            with self.assertRaisesRegex(RuntimeError,'036-to-038'): plan(c)
            self.assertEqual(c.scalar(text('SELECT name FROM api_user_profile')),'preserve')

    def test_admin_sql_view_still_requires_debug_flag_and_result_ownership(self):
        uid=int(self.signup())
        with self.engine.begin() as c:
            store=Store(self.app.state.database,c)
            own=result_table(store,uid,[{'amount_usd':1}],{},sql=['SELECT 1'])['result_id']
        path=f'/api/v1/debug/results/{own}/sql'
        self.settings.values['API_DEBUG_SQL']='true'
        self.assertEqual(self.client.get(path).status_code,403)
        with self.engine.begin() as c:
            c.execute(update(self.meta.tables['users']).where(self.meta.tables['users'].c.user_id==uid).values(role='ADMIN'))
        self.settings.values['API_DEBUG_SQL']='false'
        self.assertEqual(self.client.get(path).status_code,403)
        self.settings.values['API_DEBUG_SQL']='true'
        self.assertEqual(self.client.get(path).json()['sql'],'SELECT 1')
        other=int(self.signup('other@example.org'))
        with self.engine.begin() as c:
            c.execute(update(self.meta.tables['users']).where(self.meta.tables['users'].c.user_id==other).values(role='ADMIN'))
        self.assertEqual(self.client.get(path).status_code,404)


class FontCacheTest(unittest.TestCase):
    def test_font_cached_and_invalidated_when_file_changes(self):
        font_css.cache_clear()
        self.addCleanup(font_css.cache_clear)
        with tempfile.TemporaryDirectory(dir='tmp') as directory:
            font=Path(directory)/'font.ttf'; font.write_bytes(b'first')
            original=Path.read_bytes
            with patch.object(Path,'read_bytes',autospec=True,side_effect=original) as read:
                stamp=font.stat(); args=(str(font),stamp.st_mtime_ns,stamp.st_size)
                first=font_css(*args)
                self.assertEqual(font_css(*args),first)
                self.assertEqual(read.call_count,1)
                font.write_bytes(b'second version')
                stamp=font.stat()
                self.assertNotEqual(font_css(str(font),stamp.st_mtime_ns,stamp.st_size),first)
                self.assertEqual(read.call_count,2)


if __name__=='__main__': unittest.main()
