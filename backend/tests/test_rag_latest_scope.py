"""Public synthetic tests: no evaluation article IDs or Gold decisions."""
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from backend.app.schemas.rag import RagRequest, ALL_SOURCES
from backend.app.services.rag.article_catalog import LocalArticleCatalog
from backend.app.services.rag.service import RagService
from backend.app.services.rag.store import GenerationStore
from backend.app.services.rag.runtime import ExecutionContext
from backend.app.services.rag.vocabulary import Vocabulary
from backend.app.services.rag.retrieval import filter_where
from backend.tests.test_rag_pipeline import FakeGateway, document as base_document, vectors
from backend.tests.rag_fixtures import request_payload


def document(n=1, **changes):
    changes.setdefault('published_date', '2026-09-20')
    return base_document(n, **changes)


class LatestScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = GenerationStore(self.root/'index', dimensions=8)
        self.store.publish([document(999)], ALL_SOURCES, vectors)
        self.gateway = FakeGateway()

    def service(self, docs):
        self.store.publish(docs, ALL_SOURCES, vectors)
        p = self.root/'catalog.sqlite3'
        con = sqlite3.connect(p)
        con.execute('CREATE TABLE articles(source_code,external_id,published_date,payload)')
        con.executemany('INSERT INTO articles VALUES(?,?,?,?)', [(a.source_code.value,a.external_id,a.published_date.isoformat(),a.model_dump_json()) for a in docs]);con.commit();con.close()
        vocab = Vocabulary(products={'HS2022:8507':['배터리'], 'HS2022:3303':['향수'], 'HS2022:3304':['화장품']})
        return RagService(self.store,vocab,self.gateway,catalog=LocalArticleCatalog(p))

    def call(self, service, **kw):
        kw.setdefault('query', '최신 기사 목록')
        req=RagRequest.model_validate(request_payload('chat',order_by='published_date_desc',**kw))
        return service.run(req,ExecutionContext(time.monotonic()+30))

    def test_latest_uses_dates_without_semantic_review_or_refilling(self):
        docs=[document(n,source='KOTRA_MARKET',published_date='2026-09-30' if n<=20 else '2026-09-29') for n in range(1,26)]
        svc=self.service(docs);original=self.gateway.summarize
        self.gateway.summarize=lambda r,c,x: {'sentences':[]} if int(c.article.external_id)<=20 else original(r,c,x)
        result=self.call(svc, conditions=request_payload()['conditions'] | {'geography': {'mode':'all'}})
        self.assertEqual(result.returned_count,5);self.assertEqual(result.status,'ok')
        self.assertEqual({a.external_id for a in result.articles},{'1','10','11','12','13'})
        self.assertTrue(result.latest_scan_complete);self.assertEqual(self.gateway.embed_calls,1)
        self.assertEqual(self.gateway.summary_calls,[])
        self.assertEqual(result.latest_basis,'publication_date')


    def test_context_scope_crosses_country_without_changing_target_or_source_period(self):
        svc=self.service([document(1,countries=['JP']),document(2,published_date='2026-08-31'),document(3,source='FNNEWS')])
        result=self.call(svc,source_codes=['KOTRA_MARKET'])
        self.assertEqual([a.external_id for a in result.articles],['1'])
        self.assertEqual(result.applied_conditions.geography.codes,('US',))
        self.assertEqual(result.articles[0].evidence_role,'direct_evidence')
        self.assertEqual(result.articles[0].summary,())
        self.assertIsNone(result.articles[0].reference_limitation)

    def test_target_tags_do_not_become_vector_filters(self):
        request = RagRequest.model_validate(request_payload('chat'))
        self.assertNotIn('countries', json.dumps(filter_where(request, sources=ALL_SOURCES)))


    def test_missing_body_is_unreviewed_and_outside_the_vector_body_candidates(self):
        svc=self.service([document(1,body='',published_date='2026-09-30'),*[document(n) for n in range(2,7)]])
        result=self.call(svc)
        self.assertEqual(result.returned_count,5);self.assertEqual(result.status,'ok')
        self.assertFalse(result.latest_scan_complete)
        self.assertEqual({a.external_id for a in result.articles},{'2','3','4','5','6'})
        self.assertTrue(all(a.body_status=='available' for a in result.articles))
        self.assertNotIn(document(1).key,self.gateway.summary_calls)

    def test_unknown_keywords_do_not_bypass_block_list(self):
        svc=self.service([document(1,keywords_ready=False),document(2,kw_topic=['차단'])])
        conditions=request_payload()['conditions']|{'blocked_topics':['차단']}
        result=self.call(svc,conditions=conditions)
        self.assertEqual(self.gateway.summary_calls,[]);self.assertFalse(result.latest_scan_complete)

    def test_cache_records_body_policy_and_excludes_request_identity(self):
        r = RagRequest.model_validate(request_payload('chat'))
        data = r.cache_payload()
        self.assertEqual(data['retrieval_policy'], 'body-grounded-v26')
        self.assertNotIn('request_id', data)
        self.assertEqual(r.model_copy(update={'request_id': 'another'}).cache_payload(), data)


    def test_generic_latest_list_uses_vector_candidates_without_summary_inference(self):
        from backend.app.services.rag.runtime import RagInferenceUnavailable
        svc=self.service([document(n) for n in range(1,7)])
        called=[]
        def unavailable(request,candidate,context):
            called.append(candidate.article.key)
            raise RagInferenceUnavailable('offline')
        self.gateway.summarize=unavailable
        result=self.call(svc, conditions=request_payload()['conditions'] | {'geography': {'mode':'all'}})
        self.assertEqual(len(called),0)
        self.assertEqual(result.status,'ok');self.assertTrue(result.latest_scan_complete)
        self.assertEqual(result.failures,())
        self.assertEqual(self.gateway.embed_calls,1)


    def test_context_scope_does_not_drop_market_without_target_HS(self):
        svc=self.service([document(1,source='KOTRA_MARKET',products=[])])
        conditions=request_payload()['conditions']|{'products':[{'hs_code':'8507','hs_ver':'HS2022'}]}
        result=self.call(svc,conditions=conditions)
        self.assertEqual(result.returned_count,1)
        self.assertEqual(result.articles[0].evidence_role,'direct_evidence')

    def test_latest_reuses_existing_index_and_originals_without_a_separate_catalog(self):
        svc=RagService(self.store,Vocabulary(),self.gateway)
        result=self.call(svc)
        self.assertEqual(result.status,'insufficient_results')
        self.assertTrue(result.latest_scan_complete)
        self.assertEqual([a.external_id for a in result.articles], ['999'])
        self.assertEqual(result.latest_scope, 'retrieved_candidates')

    def test_topic_is_checked_before_limit_and_answer_format_is_independent(self):
        docs = [document(n, source='KOTRA_MARKET', countries=['FR'], published_date='2026-09-30',
                         body='다른 주제') for n in range(1, 7)]
        docs += [document(7, source='KOTRA_MARKET', countries=['FR'], published_date='2026-09-20',
                          body='독일산 향수가 프랑스로 수출됐다.'),
                 document(8, source='KOTRA_MARKET', countries=['DE'], published_date='2026-09-19',
                          body='독일산 향수가 수출됐다.')]
        service = self.service(docs)
        original = self.gateway.summarize
        self.gateway.summarize = lambda req, c, ctx: (original(req, c, ctx)
            if '향수' in c.article.body else {'sentences': []})
        self.gateway.latest_intent = lambda req, ctx: ('list' if '제목만' in req.query else 'summary', True)
        conditions = request_payload()['conditions'] | {'geography': {'mode':'selected','codes':['DE']}}
        for query, display in [('독일 향수 수출을 최근 기사로 설명해줘','summary'),
                               ('독일 향수 수출 관련 최신 기사 제목만','list')]:
            result = self.call(service, query=query, conditions=conditions)
            self.assertEqual([a.external_id for a in result.articles], ['7','8'])
            self.assertEqual(result.answer_format, display)
            for article in result.articles:
                self.assertEqual(article.evidence_role, 'direct_evidence')
                self.assertEqual(bool(article.summary and article.evidence), display == 'summary')
            self.assertTrue(result.latest_scan_complete)

    def test_missing_newer_body_is_unreviewed_for_a_topical_summary(self):
        service = self.service([document(1, body='', published_date='2026-09-30'),
                                document(2, published_date='2026-09-29')])
        result = self.call(service, query='최근 미국 배터리 수출을 설명해 줘', answer_format='summary')
        self.assertFalse(result.latest_scan_complete)
        self.assertEqual([a.external_id for a in result.articles], ['2'])
        self.assertEqual(self.gateway.summary_calls, [document(2).key])

    def test_title_list_checks_body_product_for_every_source(self):
        docs = [document(i * 2 + j, source=source, body=body)
                for i, source in enumerate(ALL_SOURCES)
                for j, body in ((1, '의류 수요 기사'), (2, '배터리 수요 기사'))]
        service = self.service(docs)
        original = self.gateway.summarize
        self.gateway.summarize = lambda req, c, ctx: (original(req, c, ctx)
            if '배터리' in c.article.body else {'sentences': []})
        conditions = request_payload()['conditions'] | {'products':[{'hs_code':'8507','hs_ver':'HS2022'}]}
        result = self.call(service, conditions=conditions)
        self.assertEqual({a.source_code for a in result.articles}, set(ALL_SOURCES))
        self.assertEqual({a.external_id for a in result.articles}, {'2','4','6','8'})
        self.assertEqual(result.answer_format, 'list')
        self.assertTrue(all(not a.summary for a in result.articles))


    def test_latest_uses_inclusive_fourteen_days_and_reports_candidate_scope(self):
        service = self.service([document(1,published_date='2026-09-16'),
                                document(2,published_date='2026-09-17'),
                                document(3,published_date='2026-09-30')])
        result = self.call(service)
        self.assertEqual([a.external_id for a in result.articles], ['3','2'])
        self.assertEqual(result.latest_period.start.isoformat(), '2026-09-17')
        self.assertEqual(result.latest_period.end.isoformat(), '2026-09-30')
        self.assertEqual(result.applied_conditions.period.start.isoformat(), '2026-09-01')
        self.assertEqual(result.latest_scope, 'retrieved_candidates')
        self.assertEqual(result.latest_candidate_limit, 40)
        self.assertIn('후보 최대 40건 안', result.latest_scope_description)

    def test_latest_candidate_pool_is_bounded_before_date_sorting(self):
        service = self.service([document(n) for n in range(1,51)])
        request = RagRequest.model_validate(request_payload('chat',query='최신 기사 목록',
            order_by='published_date_desc'))
        context = ExecutionContext(time.monotonic()+30)
        result = service.run(request,context)
        pool = next(e for e in context.events if e['stage']=='candidate_pool')
        self.assertEqual((pool['limit'],pool['count']), (40,40))
        self.assertTrue({a.key for a in result.articles} <=
                        {(a['source_code'],a['external_id']) for a in pool['articles']})
        self.assertEqual([a.published_date for a in result.articles],
                         sorted((a.published_date for a in result.articles),reverse=True))

    def test_latest_intersects_short_requests_and_handles_minimum_calendar_date(self):
        service = self.service([document(1,published_date='2026-09-28'),
                                document(2,published_date='2026-09-30')])
        for start,end,expected in [('2026-09-29','2026-09-30',['2']),
                                   ('0001-01-01','0001-01-01',[])]:
            conditions = request_payload()['conditions'] | {'period':{'start':start,'end':end}}
            result = self.call(service,conditions=conditions)
            self.assertEqual([a.external_id for a in result.articles],expected)
            self.assertEqual(result.latest_period.start.isoformat(), start)
            self.assertEqual(result.latest_period.end.isoformat(), end)

if __name__=='__main__':unittest.main()
