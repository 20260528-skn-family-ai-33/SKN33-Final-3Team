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
from backend.tests.test_rag_pipeline import FakeGateway, document, vectors
from backend.tests.rag_fixtures import request_payload


class LatestScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = GenerationStore(self.root/'index', dimensions=8)
        self.store.publish([document(999)], ALL_SOURCES, vectors)
        self.gateway = FakeGateway()

    def service(self, docs):
        p = self.root/'catalog.sqlite3'
        con = sqlite3.connect(p)
        con.execute('CREATE TABLE articles(source_code,external_id,published_date,payload)')
        con.executemany('INSERT INTO articles VALUES(?,?,?,?)', [(a.source_code.value,a.external_id,a.published_date.isoformat(),a.model_dump_json()) for a in docs]);con.commit();con.close()
        return RagService(self.store,Vocabulary(),self.gateway,catalog=LocalArticleCatalog(p))

    def call(self, service, **kw):
        req=RagRequest.model_validate(request_payload('chat',order_by='published_date_desc',**kw))
        return service.run(req,ExecutionContext(time.monotonic()+30))

    def test_latest_uses_dates_without_semantic_review_or_refilling(self):
        docs=[document(n,source='KOTRA_MARKET',published_date='2026-09-30' if n<=20 else '2026-09-29') for n in range(1,26)]
        svc=self.service(docs);original=self.gateway.summarize
        self.gateway.summarize=lambda r,c,x: {'sentences':[]} if int(c.article.external_id)<=20 else original(r,c,x)
        result=self.call(svc)
        self.assertEqual(result.returned_count,5);self.assertEqual(result.status,'ok')
        self.assertEqual({a.external_id for a in result.articles},{'1','10','11','12','13'})
        self.assertTrue(result.latest_scan_complete);self.assertEqual(self.gateway.embed_calls,0)
        self.assertEqual(self.gateway.summary_calls,[])
        self.assertEqual(result.latest_basis,'publication_date')

    def test_context_scope_crosses_country_without_changing_target_or_source_period(self):
        svc=self.service([document(1,countries=['JP']),document(2,published_date='2026-08-31'),document(3,source='FNNEWS')])
        result=self.call(svc,reference_scope='direct_and_context',source_codes=['KOTRA_MARKET'])
        self.assertEqual([a.external_id for a in result.articles],['1'])
        self.assertEqual(result.applied_conditions.geography.codes,('US',))
        self.assertEqual(result.articles[0].evidence_role,'unclassified')
        self.assertEqual(result.articles[0].summary,())
        self.assertIsNotNone(result.articles[0].reference_limitation)

    def test_strict_target_scope_remains_strict(self):
        svc=self.service([document(1,countries=['JP']),document(2)])
        self.assertEqual([a.external_id for a in self.call(svc).articles],['2'])
        strict=RagRequest.model_validate(request_payload('chat'))
        broad=strict.model_copy(update={'reference_scope':'direct_and_context'})
        self.assertIn('countries',json.dumps(filter_where(strict,sources=ALL_SOURCES)))
        self.assertNotIn('countries',json.dumps(filter_where(broad,sources=ALL_SOURCES)))

    def test_missing_newer_body_does_not_change_publication_recency(self):
        svc=self.service([document(1,body='',published_date='2026-09-30'),*[document(n) for n in range(2,7)]])
        result=self.call(svc)
        self.assertEqual(result.returned_count,5);self.assertEqual(result.status,'ok')
        self.assertTrue(result.latest_scan_complete)
        self.assertEqual(result.articles[0].external_id,'1')
        self.assertEqual(result.articles[0].body_status,'missing')
        self.assertNotIn(document(1).key,self.gateway.summary_calls)

    def test_unknown_keywords_do_not_bypass_block_list(self):
        svc=self.service([document(1,keywords_ready=False),document(2,kw_topic=['차단'])])
        conditions=request_payload()['conditions']|{'blocked_topics':['차단']}
        result=self.call(svc,conditions=conditions)
        self.assertEqual(self.gateway.summary_calls,[]);self.assertFalse(result.latest_scan_complete)

    def test_default_cache_identity_is_legacy_compatible(self):
        p=request_payload('chat');r=RagRequest.model_validate(p)
        data=r.cache_payload();self.assertNotIn('order_by',data);self.assertNotIn('reference_scope',data)
        broad=r.model_copy(update={'reference_scope':'direct_and_context'})
        self.assertNotEqual(broad.cache_payload(),data)

    def test_latest_needs_neither_inference_nor_vector_snapshot(self):
        from backend.app.services.rag.runtime import RagInferenceUnavailable
        svc=self.service([document(n) for n in range(1,7)])
        called=[]
        def unavailable(request,candidate,context):
            called.append(candidate.article.key)
            raise RagInferenceUnavailable('offline')
        self.gateway.summarize=unavailable
        svc.store.snapshot=lambda: (_ for _ in ()).throw(AssertionError('vector snapshot forbidden'))
        result=self.call(svc)
        self.assertEqual(len(called),0)
        self.assertEqual(result.status,'ok');self.assertTrue(result.latest_scan_complete)
        self.assertEqual(result.failures,())

    def test_context_scope_does_not_drop_market_without_target_HS(self):
        svc=self.service([document(1,source='KOTRA_MARKET',products=[])])
        conditions=request_payload()['conditions']|{'products':[{'hs_code':'8507','hs_ver':'HS2022'}]}
        result=self.call(svc,conditions=conditions,reference_scope='direct_and_context')
        self.assertEqual(result.returned_count,1)
        self.assertEqual(result.articles[0].evidence_role,'unclassified')
        self.assertEqual(self.gateway.product_calls,[])

    def test_latest_without_catalog_fails_explicitly(self):
        svc=RagService(self.store,Vocabulary(),self.gateway)
        result=self.call(svc)
        self.assertEqual(result.status,'failed');self.assertFalse(result.latest_scan_complete)

if __name__=='__main__':unittest.main()
