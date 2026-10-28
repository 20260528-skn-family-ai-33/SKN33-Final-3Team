from datetime import date
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

from sqlalchemy import create_engine, insert

from backend.app.models.base import Base
from backend.app.models.news import Article, ArticleTag, COUNTRY
from backend.app.models.hs_code import HsCodeMst
from backend.app.schemas.rag import ALL_SOURCES
from backend.app.services.rag.ingest import read_articles
from backend.app.services.rag.vocabulary import Vocabulary
from backend.app.scheduler.jobs import market_rag_pipeline


class IngestTest(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine('sqlite://')
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        with self.engine.begin() as c:
            c.execute(insert(COUNTRY),[{'country_code':'US'}])
            c.execute(insert(HsCodeMst),[{'hs_code':code,'hs_ver':'HS2022','name_ko':'배터리',
                'original_name_ko':'배터리' if len(code)==6 else None,'source_edition':'synthetic'} for code in ('8507','850760')])
            c.execute(insert(Article),[{'article_id':i,'source_code':source.value,'external_id':str(i),'title':f'기사{i}',
                'body_masked':'실제 본문','source_url':f'https://example.org/{i}','published_date':date(2026,9,1)}
                for i,source in enumerate(ALL_SOURCES,1)])
            c.execute(insert(ArticleTag),[{'article_id':i,'country_code':'US'} for i in range(1,5)])
            c.execute(insert(ArticleTag),[{'article_id':i,'keyword_text':'전기오토바이'} for i in range(1,5)])
            c.execute(insert(ArticleTag),[{'article_id':1,'hs_code':'850760','hs_ver':'HS2022'}])
        self.vocabulary=Vocabulary(hypernyms={'전기오토바이':'전기차'})

    def test_four_source_metadata_preserves_subterms_and_validated_hs(self):
        docs,vocab=read_articles(self.engine,ALL_SOURCES,vocabulary=self.vocabulary,
            supplements={('KOTRA_MARKET','1'):{'body_masked':'실제 본문','body_available':True,'keywords':['전기오토바이']}})
        by_source={d.source_code.value:d for d in docs}
        market=by_source['KOTRA_MARKET']
        self.assertEqual(market.kw_topic,('전기차',))
        self.assertEqual(market.kw_sub,('전기오토바이',))
        self.assertEqual({p.hs_code for p in market.products},{'8507','850760'})
        self.assertEqual(by_source['HANKYUNG'].kw_sub,('전기오토바이',))
        self.assertIn('country_role:mentioned',by_source['FNNEWS'].provenance)
        self.assertIn('original_kw_sub',by_source['KOTRA_SHORT_NEWS'].metadata_missing)
        self.assertEqual(vocab.products['HS2022:850760'],['배터리'])

    def test_stale_supplement_is_not_applied(self):
        docs,_=read_articles(self.engine,['KOTRA_MARKET'],vocabulary=self.vocabulary,
            supplements={('KOTRA_MARKET','1'):{'body_masked':'이전 본문','keywords':['다른 품목']}})
        self.assertEqual(docs[0].kw_topic,('전기차',))
        self.assertIn('stale_keyword_supplement',docs[0].metadata_missing)

    def test_original_body_missing_is_not_replaced_with_title_or_summary(self):
        docs,_=read_articles(self.engine,['KOTRA_MARKET'],vocabulary=self.vocabulary,
            supplements={('KOTRA_MARKET','1'):{'body_masked':'실제 본문','body_available':False,'keywords':[]}})
        self.assertEqual(docs[0].body,'')

    def test_unknown_hs_pair_is_not_created(self):
        with self.engine.begin() as c:
            c.execute(insert(ArticleTag),{'article_id':2,'hs_code':'999999','hs_ver':'HS2099'})
        docs,_=read_articles(self.engine,['KOTRA_SHORT_NEWS'],vocabulary=self.vocabulary)
        self.assertEqual(docs[0].products,())

    def test_market_body_origin_is_unconfirmed_without_matching_raw(self):
        docs,_=read_articles(self.engine,['KOTRA_MARKET'],vocabulary=self.vocabulary)
        self.assertEqual(docs[0].body,'실제 본문')  # 입력은 보존, 본문 여부를 추정하지 않음
        self.assertIn('body_origin', docs[0].metadata_missing)

    def test_current_topics_override_previous_keywords_without_changing_body(self):
        from sqlalchemy import update
        with self.engine.begin() as c:
            c.execute(update(ArticleTag).where(ArticleTag.keyword_text.is_not(None)).values(keyword_text='배터리'))
        extras = {(source.value, str(i)): {'body_masked':'실제 본문', 'body_available':True,
                                          'keywords':['전기오토바이']} for i, source in enumerate(ALL_SOURCES, 1)}
        docs, _ = read_articles(self.engine, ALL_SOURCES, vocabulary=self.vocabulary, supplements=extras)
        for doc in docs:
            self.assertEqual(doc.kw_topic, ('배터리',))
            self.assertNotIn('전기오토바이', doc.kw_sub)

    def test_matching_previous_body_is_preserved_when_raw_supplement_is_stale(self):
        extras = {('KOTRA_MARKET','1'): [
            {'body_masked':'이전 원본', 'body_available':False, 'keywords':[]},
            {'body_masked':'실제 본문', 'body_available':True, 'keywords':['전기오토바이']}]}
        docs, _ = read_articles(self.engine, ['KOTRA_MARKET'], vocabulary=self.vocabulary, supplements=extras)
        self.assertEqual(docs[0].body, '실제 본문')
        self.assertNotIn('body_origin', docs[0].metadata_missing)
        self.assertEqual(docs[0].kw_sub, ('전기오토바이',))

    def test_market_processing_stops_before_index_on_failure(self):
        calls=[]
        with tempfile.TemporaryDirectory() as folder:
            result=market_rag_pipeline.run('raw','clean',log_dir=folder,
                stages=[('articles',lambda: calls.append('articles') or 1),('article_index',lambda:calls.append('index') or 0)])
        self.assertEqual(result['status'],'failed')
        self.assertEqual(calls,['articles'])

    def test_newspaper_daily_wrapper_delegates_inside_bounded_job(self):
        from backend.app.scheduler.jobs import trade_news_daily as job
        with patch.object(job,'run',return_value={'status':'complete'}) as run:
            self.assertEqual(job.run_with_rag()['status'],'complete')
            self.assertTrue(run.call_args.kwargs['include_vector'])

    def test_refresh_publishes_dictionary_and_reuses_vectors(self):
        from backend.app.scheduler.jobs.rag_index import refresh
        from backend.app.services.rag.store import GenerationStore
        from backend.app.services.news_keywords.keyword_clean import Categories
        from backend.tests.test_rag_pipeline import vectors
        from backend.app.services.rag.vocabulary import Vocabulary
        import json
        with tempfile.TemporaryDirectory() as folder:
            store=GenerationStore(Path(folder)/'index',dimensions=8)
            vocab=Vocabulary(categories=Categories(rules=False))
            with patch('backend.app.scheduler.jobs.rag_index.Vocabulary.load',return_value=vocab):
                first=refresh(self.engine,['HANKYUNG','FNNEWS'],store=store,embed=vectors)
                second=refresh(self.engine,['HANKYUNG','FNNEWS'],store=store,embed=vectors)
            self.assertEqual(first['embedded'],2)
            self.assertEqual(second['embedded'],0)
            saved=json.loads((store.root/'generations'/second['generation']/'vocabulary.json').read_text())
            self.assertIn('HS2022:850760',saved['products'])



if __name__=='__main__':unittest.main()
