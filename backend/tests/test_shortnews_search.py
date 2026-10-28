"""실제 임시 SQL → 공통 세대 → 공개 검색. 네트워크/실제 사전/운영 색인 사용 없음."""
import json
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from sqlalchemy import text

from backend.tests import test_shortnews_vectors_integration as fixture
from backend.app.scheduler.jobs import news_embed
from backend.app.services.rag import store


class PublicSearchTest(unittest.TestCase):
    def setUp(self):
        self.case=fixture.IntegrationTest();self.case.setUp();self.addCleanup(self.case.doCleanups)
        self.pipeline=self.case.case;self.root=self.case.root;self.db=self.case.db
        self.query='반도체 수출'
        def embed(words):
            values=[]
            for word in words:
                # 신문은 질의와 정확히 같은 벡터, 관련 단신은 조금 떨어진 벡터입니다.
                if '신문' in word or word==self.query: pair=[1.,0.]
                elif '반도체' in word: pair=[0.9,0.1]
                else: pair=[0.,1.]
                values.append(pair+[0.]*3070)
            return values
        self.embed=embed
        self.pipeline.embed.side_effect=lambda api,words,**kwargs:embed(words)
        self.pipeline.shared_embed.side_effect=lambda api,words,**kwargs:embed(words)
        self.pipeline.path.write_text(json.dumps([
            fixture.fixture.fixture.raw('001',body='반도체 수출 관련 합성 본문'),
            fixture.fixture.fixture.raw('002',body='체육 대회 안내 합성 본문'),
        ]),encoding='utf-8')

    def seed(self, paper=False):
        out=self.pipeline.run_pipeline();self.assertEqual(out['status'],'SUCCESS',out)
        if paper:
            self.case.seed_other()
            out=news_embed.run_sources(['HANKYUNG'],db_url=self.db.url,root=self.root,embed=self.embed)
            self.assertEqual(out['status'],'complete',out)

    def search(self, **kwargs):
        return news_embed.search(self.query,root=self.root,embed=self.embed,source_codes=['KOTRA_SHORT_NEWS'],**kwargs)

    def test_sql_to_public_search_source_filter_precedes_top_k_without_hs(self):
        self.seed(paper=True)
        all_hits=news_embed.search(self.query,n=1,root=self.root,embed=self.embed)
        self.assertEqual(all_hits[0][3]['source_code'],'HANKYUNG')
        hits=self.search(n=1)
        self.assertEqual(len(hits),1)
        self.assertEqual((hits[0][3]['source_code'],hits[0][3]['external_id']),('KOTRA_SHORT_NEWS','001'))
        self.assertNotIn('hs_keys',hits[0][3])
        self.assertIn('반도체 수출 관련 합성 본문',hits[0][2])
        self.assertNotIn('002',[h[3]['external_id'] for h in hits])

    def test_helper_returns_nearest_chunks_without_claiming_relevance_rejection(self):
        self.seed()
        # n=2이면 무관한 기사도 반환합니다. 사용자별 RAG 검수라고 보고하지 않습니다.
        hits=self.search(n=2)
        self.assertEqual([h[3]['external_id'] for h in hits],['001','002'])

    def test_country_keyword_and_date_filters_are_applied_in_active_generation(self):
        self.seed()
        self.assertEqual(len(self.search(n=5,country='US',keyword='AI',date_from='2026-10-01',date_to='2026-10-01')),2)
        for options in ({'country':'FR'},{'keyword':'미등록'},{'date_from':'2026-10-02'},{'date_to':'2026-09-30'}):
            self.assertEqual(self.search(**options),[])

    def test_hidden_article_is_removed_on_refresh_not_live_db_check(self):
        self.seed()
        with self.db.engine.begin() as conn:
            conn.execute(text("UPDATE article SET visibility_status='HIDDEN' WHERE external_id='001'"))
        self.assertEqual(self.search(n=1)[0][3]['external_id'],'001')
        out=news_embed.run_sources(['KOTRA_SHORT_NEWS'],db_url=self.db.url,root=self.root,embed=self.embed)
        self.assertEqual(out['status'],'complete',out)
        self.assertEqual([h[3]['external_id'] for h in self.search(n=5)],['002'])

    def test_metadata_only_missing_body_record_is_not_searchable(self):
        self.seed()
        with self.db.engine.begin() as conn:
            conn.execute(text("UPDATE article SET body_masked='' WHERE external_id='001'"))
        out=news_embed.run_sources(['KOTRA_SHORT_NEWS'],db_url=self.db.url,root=self.root,embed=self.embed)
        self.assertEqual(out['status'],'complete',out)
        self.assertEqual([h[3]['external_id'] for h in self.search(n=5)],['002'])

    def test_unprepared_or_invalid_source_is_error_before_embedding(self):
        self.seed();encode=Mock(side_effect=AssertionError('should not embed'))
        for scope in ([],['BREAKING'],['FNNEWS']):
            with self.assertRaises(ValueError):
                news_embed.search(self.query,root=self.root,source_codes=scope,embed=encode)
        encode.assert_not_called()

    def test_missing_generation_does_not_create_empty_collection(self):
        encode=Mock()
        with self.assertRaises(FileNotFoundError):news_embed.search(self.query,root=self.root,embed=encode)
        encode.assert_not_called();self.assertFalse(self.root.exists())

    def test_legacy_direct_index_is_explicit_transition_failure(self):
        legacy=self.db.root/'legacy';legacy.mkdir()
        with sqlite3.connect(legacy/'chroma.sqlite3') as conn:
            conn.execute('CREATE TABLE collections(name TEXT)');conn.execute("INSERT INTO collections VALUES ('kotra_article')")
        before=(legacy/'chroma.sqlite3').read_bytes();encode=Mock()
        with patch.object(store,'ROOT',self.root),patch.object(store,'LEGACY_ROOT',legacy):
            with self.assertRaisesRegex(ValueError,'legacy_direct_index_migration_required'):
                news_embed.search(self.query,embed=encode)
        encode.assert_not_called();self.assertEqual((legacy/'chroma.sqlite3').read_bytes(),before)
        self.assertFalse(self.root.exists())

    def test_wrong_generation_contract_is_not_empty_success(self):
        self.seed();gen=json.loads((self.root/'active.json').read_text(encoding='utf-8'))['generation']
        p=self.root/'generations'/gen/'status.json';value=json.loads(p.read_text(encoding='utf-8'))
        value['collections']['short_news']=1;p.write_text(json.dumps(value),encoding='utf-8');encode=Mock()
        with self.assertRaisesRegex(ValueError,'single_collection_migration_required'):
            news_embed.search(self.query,root=self.root,embed=encode)
        encode.assert_not_called()

    def test_owned_snapshot_closed_on_success_and_query_failure(self):
        self.seed();original=store.GenerationStore.snapshot
        for fail in (False,True):
            owned=[]
            def tracked(instance):
                value=original(instance);real=value.client.close
                value.client.close=Mock(wraps=real);owned.append(value.client)
                return value
            with patch.object(store.GenerationStore,'snapshot',tracked):
                if fail:
                    with self.assertRaises(RuntimeError):
                        news_embed.search(self.query,root=self.root,embed=Mock(side_effect=RuntimeError('fake query')))
                else:self.assertEqual(len(self.search(n=1)),1)
            self.assertEqual(len(owned),1);owned[0].close.assert_called_once()

    def test_injected_legacy_client_remains_open_and_empty_is_not_created(self):
        import chromadb
        client=chromadb.PersistentClient(path=str(self.db.root/'explicit_legacy'));self.addCleanup(client.close)
        with patch.object(client,'close',wraps=client.close) as close:
            with self.assertRaises(Exception):news_embed.search(self.query,client=client,embed=self.embed)
            close.assert_not_called()
        self.assertEqual(client.list_collections(),[])


if __name__=='__main__':unittest.main()
