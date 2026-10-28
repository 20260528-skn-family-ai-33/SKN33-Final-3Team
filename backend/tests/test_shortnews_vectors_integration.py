"""합성 사전·임시 SQLite/Chroma로 공통 색인 연결을 검증합니다. 외부 호출 없음."""
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from sqlalchemy import text
from backend.tests import test_shortnews_pipeline as fixture
from backend.app.scheduler.jobs import news_embed, rag_index, shortnews_pipeline
from backend.app.services import shortnews_vectors
from backend.app.services.rag.store import GenerationStore, read_all


class IntegrationTest(unittest.TestCase):
    def setUp(self):
        self.case = fixture.PipelineTest(); self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.root = self.case.db.root/'vectors'
        self.db = self.case.db
        self.encode = lambda words: [[1.0]+[0.0]*3071 for _ in words]

    def run_vectors(self, **kwargs):
        return news_embed.run_sources(['KOTRA_SHORT_NEWS'], db_url=self.db.url,
                                     root=self.root, embed=self.encode, **kwargs)

    def seed_other(self):
        from backend.app.db.batch_log import ensure_source, Source
        from backend.app.db.session import session_scope
        from backend.app.scheduler.jobs.shortnews_load import reflect
        with session_scope(self.db.engine) as session:
            ensure_source(session, Source('HANKYUNG', '합성 신문', {}))
            row = {k:v for k,v in self.db.rows('article')[0].items() if k != 'article_id'}
            row.update(source_code='HANKYUNG', external_id='paper001', title='신문 제목', body_masked='신문 합성 본문')
            session.execute(reflect(self.db.engine, 'article').insert().values(**row))

    def test_only_destination_common_modules_are_imported(self):
        from backend.app.services.rag import store, ingest, documents, vocabulary
        from backend.app.schemas import rag
        repo=Path(__file__).resolve().parents[2]
        for module in (news_embed, rag_index, store, ingest, documents, vocabulary, rag, shortnews_vectors):
            self.assertTrue(Path(module.__file__).resolve().is_relative_to(repo), module.__file__)

    def test_document_embedding_input_and_lists_and_summary_preserved(self):
        self.case.path.write_text(json.dumps([fixture.fixture.raw(body='반도체 수출 전망. '*250)]),encoding='utf-8')
        out=self.case.run_pipeline(); self.assertEqual(out['status'],'SUCCESS',out)
        col=self.case.body_collection(); data=read_all(col,['documents','metadatas'])
        texts=[t for call in self.case.shared_embed.call_args_list for t in call.args[1]]
        self.assertGreater(len(data['ids']),1)
        self.assertTrue(all(d in texts for d in data['documents']))
        self.assertTrue(all(d.startswith('[2026-10-01 | US] 단신 제목\n') for d in data['documents']))
        self.assertTrue(all(m['countries']==['US'] and m['keywords']==['AI'] and 'hs_keys' not in m for m in data['metadatas']))
        self.assertEqual(out['chroma_result']['body']['chunk_chars'],1000)
        self.assertEqual(out['chroma_result']['body']['overlap_chars'],150)
        summaries=json.loads(Path(out['analysis_result_file']).read_text(encoding='utf-8'))
        self.assertEqual(summaries['articles']['001']['summary_sentences'],['요약이다.'])
        self.assertFalse(any(c.name=='short_news' for c in self.case.client.list_collections()))
        second=self.case.run_pipeline()
        self.assertEqual(second['chroma_result']['body']['embedded'],0)
        self.assertEqual(json.loads(Path(second['analysis_result_file']).read_text(encoding='utf-8')),summaries)

    def test_other_sources_and_candidate_index_survive_shortnews_refresh(self):
        self.assertEqual(self.case.run_pipeline()['status'],'SUCCESS')
        candidates=self.case.client.get_collection(fixture.keywords.collection_name(self.case.snapshot)).get(include=['metadatas','documents'])
        self.seed_other()
        out=news_embed.run_sources(['HANKYUNG'],db_url=self.db.url,root=self.root,embed=self.encode)
        self.assertEqual(out['status'],'complete',out)
        before=self.case.body_collection().get(where={'source_code':'HANKYUNG'},include=['documents','metadatas','embeddings'])
        out=self.run_vectors();self.assertEqual(out['body']['selected_articles'],1);self.assertEqual(out['body']['total_articles'],2)
        after=self.case.body_collection().get(where={'source_code':'HANKYUNG'},include=['documents','metadatas','embeddings'])
        self.assertEqual(before['ids'],after['ids']);self.assertEqual(before['documents'],after['documents']);self.assertEqual(before['metadatas'],after['metadatas'])
        self.assertTrue((before['embeddings']==after['embeddings']).all())
        self.assertEqual(candidates,self.case.client.get_collection(fixture.keywords.collection_name(self.case.snapshot)).get(include=['metadatas','documents']))

    def test_keyword_failure_after_body_publishes_partial_then_reuses_body(self):
        with patch.object(news_embed,'sync_all_keywords',side_effect=RuntimeError('fake failure')):
            out=self.case.run_pipeline()
        self.assertEqual((out['status'],out['mysql'],out['chroma']),('PARTIAL','SUCCESS','PARTIAL'))
        self.assertEqual(out['chroma_result']['body']['status'],'complete')
        out=self.case.run_pipeline();self.assertEqual(out['status'],'SUCCESS',out)
        self.assertEqual(out['chroma_result']['body']['embedded'],0)
        self.assertEqual(out['analysis_requested'],0);self.assertEqual(len(self.db.rows('article')),1)

    def test_body_failure_preserves_active_generation_and_sql_then_retry(self):
        self.assertEqual(self.case.run_pipeline()['status'],'SUCCESS')
        before=(self.root/'active.json').read_bytes()
        self.case.path.write_text(json.dumps([fixture.fixture.raw(body='수정된 합성 본문')]),encoding='utf-8')
        from backend.app.services.rag.store import GenerationStore
        with patch.object(GenerationStore,'publish',side_effect=RuntimeError('fake failure')):
            out=self.case.run_pipeline()
        self.assertEqual((out['status'],out['mysql']),('PARTIAL','SUCCESS'))
        self.assertEqual((self.root/'active.json').read_bytes(),before)
        calls=self.case.ask.call_count
        out=self.case.run_pipeline();self.assertEqual(out['status'],'SUCCESS',out)
        self.assertEqual(self.case.ask.call_count,calls);self.assertEqual(len(self.db.rows('article')),1)

    def test_dry_run_changes_no_files_or_db_and_never_embeds(self):
        self.assertEqual(self.case.run_pipeline()['status'],'SUCCESS')
        def digest():return {str(p.relative_to(self.root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob('*') if p.is_file()}
        before=digest();rows=self.db.rows('article'); embed=Mock(side_effect=AssertionError('no embeddings'))
        out=news_embed.run_sources(['KOTRA_SHORT_NEWS'],db_url=self.db.url,root=self.root,embed=embed,dry_run=True)
        self.assertEqual(out['status'],'planned',out);self.assertEqual(digest(),before);self.assertEqual(self.db.rows('article'),rows);embed.assert_not_called()

    def test_missing_dictionary_is_failure_not_empty_success(self):
        with patch('backend.app.services.rag.vocabulary.Vocabulary.load',side_effect=FileNotFoundError('missing')):
            out=self.case.run_pipeline()
        self.assertEqual((out['status'],out['mysql']),('PARTIAL','SUCCESS'))
        self.assertEqual(out['chroma_result']['error_code'],'FileNotFoundError')
        self.assertFalse((self.root/'active.json').exists())

    def test_legacy_generation_is_transition_error_in_actual_and_dry_run(self):
        self.assertEqual(self.case.run_pipeline()['status'],'SUCCESS')
        generation=json.loads((self.root/'active.json').read_text(encoding='utf-8'))['generation']
        path=self.root/'generations'/generation/'status.json'
        value=json.loads(path.read_text(encoding='utf-8'));value['collections']['short_news']=1
        path.write_text(json.dumps(value),encoding='utf-8');before=path.read_bytes()
        for dry in (True,False):
            out=self.run_vectors(dry_run=dry)
            self.assertEqual(out['status'],'failed');self.assertEqual(out['error_code'],'single_collection_migration_required')
        self.assertEqual(path.read_bytes(),before)

    def test_progress_snapshots_are_independent_and_final_return_is_authoritative(self):
        self.assertEqual(self.case.run_pipeline()['status'],'SUCCESS')
        captured=[]
        out=shortnews_vectors.sync_body(db_url=self.db.url,root=self.root,embed=self.encode,progress=captured.append)
        self.assertEqual(out['status'],'complete');self.assertEqual(captured[0]['body']['status'],'running')
        self.assertNotEqual(captured[-1]['status'],'complete')
        self.assertIsNot(captured[-1]['body'],out['body'])

    def test_callback_failure_is_not_reported_success(self):
        self.assertEqual(self.case.run_pipeline()['status'],'SUCCESS')
        out=shortnews_vectors.sync_body(db_url=self.db.url,root=self.root,embed=self.encode,progress=Mock(side_effect=RuntimeError('callback')))
        self.assertEqual(out['status'],'failed')

    def test_invalid_source_rejected_before_creating_storage(self):
        for source in ([],['BREAKING'],['KOTRA_BREAKING']):
            with self.assertRaises(ValueError):news_embed.run_sources(source,db_url=self.db.url,root=self.root,embed=self.encode)
        self.assertFalse(self.root.exists())

    def test_missing_key_fails_closed_without_network(self):
        import os
        with patch.dict(os.environ,{},clear=True),patch.object(news_embed,'dotenv_values',return_value={}):
            with self.assertRaises(news_embed.MissingEmbeddingKey):news_embed.openai_embed(env=self.db.root/'missing.env')

    def test_failed_db_query_cannot_delete_global_keywords(self):
        self.assertEqual(self.case.run_pipeline()['status'],'SUCCESS')
        from backend.app.services.rag.store import persistent_client
        client=persistent_client(self.root/'keywords');self.addCleanup(client.close)
        before=client.get_collection('news_keyword').get(include=['metadatas'])
        with patch.object(news_embed,'keyword_article_counts',side_effect=RuntimeError('query failed')):
            out=self.run_vectors()
        self.assertEqual(out['status'],'partial')
        self.assertEqual(client.get_collection('news_keyword').get(include=['metadatas']),before)

    def test_confirmed_db_keywords_survive_a_conflicting_file_dictionary(self):
        from backend.app.services.rag.vocabulary import Vocabulary
        vocabulary=Vocabulary.load()
        vocabulary.clean=lambda words: {'kw_topic':['파일사전의다른말'], 'kw_sub':[], 'kw_category':[]}
        out=self.case.run_pipeline();self.assertEqual(out['status'],'SUCCESS',out)
        metas=self.case.body_collection().get(include=['metadatas'])['metadatas']
        self.assertTrue(all(m['keywords']==['AI'] for m in metas))
        self.assertEqual([r['keyword_text'] for r in self.db.rows('article_tag') if r['keyword_text']],['AI'])

    def test_legacy_shortnews_hs_is_preserved_in_sql_but_never_indexed(self):
        from backend.app.db.session import session_scope
        from backend.app.models.news import ArticleTag
        self.assertEqual(self.case.run_pipeline()['status'],'SUCCESS')
        with session_scope(self.db.engine) as session:
            session.add(ArticleTag(article_id=self.db.rows('article')[0]['article_id'],hs_code='854143',hs_ver='HS2022'))
        out=self.run_vectors();self.assertEqual(out['status'],'complete',out)
        for meta in self.case.body_collection().get(include=['metadatas'])['metadatas']:
            self.assertNotIn('hs_keys',meta);self.assertEqual(json.loads(meta['payload'])['products'],[])
        self.assertEqual([r['hs_code'] for r in self.db.rows('article_tag') if r['hs_code']],['854143'])
        self.assertEqual(self.db.rows('news_product_daily'),[])

    def test_old_news_and_new_shared_keyword_jobs_hold_identical_lock(self):
        from filelock import FileLock, Timeout
        from backend.app.services.rag.store import persistent_client
        self.assertEqual(self.case.run_pipeline()['status'],'SUCCESS')
        path=self.root/'keywords'; client=persistent_client(path);self.addCleanup(client.close)
        def held(*args,**kwargs):
            with self.assertRaises(Timeout):
                with FileLock(str(path)+'.lock',timeout=0):pass
            return 0
        with patch.object(news_embed,'_run',side_effect=held):
            self.assertEqual(news_embed.run(self.db.url,client=client,embed=self.encode,log_dir=self.db.root/'legacy'),0)
        original=news_embed.keyword_article_counts
        def counts(*args,**kwargs):held();return original(*args,**kwargs)
        with patch.object(news_embed,'keyword_article_counts',side_effect=counts):
            self.assertEqual(self.run_vectors()['status'],'complete')

    def test_windows_atomic_write_skips_directory_open_and_retains_unicode(self):
        from types import SimpleNamespace
        import os
        from backend.app.services.rag import store
        path=self.db.root/'한글.json'
        fake_os=SimpleNamespace(**{**vars(os),'name':'nt'})
        with patch.object(store,'os',fake_os),patch.object(fake_os,'open',side_effect=AssertionError('directory open forbidden')):
            store.atomic_json(path,{'국가':'日本·España'})
        self.assertEqual(json.loads(path.read_text(encoding='utf-8')),{'국가':'日本·España'})

    def test_close_failure_before_publish_preserves_old_active(self):
        from backend.app.services.rag.store import persistent_client
        from backend.app.services.rag.documents import ArticleDocument
        self.assertEqual(self.case.run_pipeline()['status'],'SUCCESS')
        before=(self.root/'active.json').read_bytes();gen=json.loads(before)['generation']
        docs=[ArticleDocument.model_validate_json(line) for line in (self.root/'generations'/gen/'articles.jsonl').read_text(encoding='utf-8').splitlines()]
        def factory(path):
            real=persistent_client(path); client=Mock(wraps=real)
            def close():real.close();raise RuntimeError('fake close failure')
            client.close.side_effect=close
            return client
        store=GenerationStore(self.root,client_factory=factory)
        with self.assertRaisesRegex(RuntimeError,'fake close failure'):
            store.publish(docs,['KOTRA_SHORT_NEWS'],self.encode)
        self.assertEqual((self.root/'active.json').read_bytes(),before)
        failures=[json.loads(p.read_text(encoding='utf-8')) for p in (self.root/'generations').glob('*/status.json')]
        self.assertEqual(sum(r['status']=='failed' for r in failures),1)

    def test_existing_news_initialization_and_close_failure_keep_return_code(self):
        import os
        self.assertEqual(self.case.run_pipeline()['status'],'SUCCESS')
        with patch.dict(os.environ,{'NEWS_VECTOR_ROOT':str(self.root/'ownership')}):
            with patch.object(news_embed,'make_engine',side_effect=RuntimeError('fake initialization')):
                self.assertEqual(news_embed.run(self.db.url,keywords_only=True,embed=self.encode),1)
            provider=Mock(side_effect=self.encode);provider.close.side_effect=RuntimeError('fake close failure')
            with patch.object(news_embed,'openai_embed',return_value=provider):
                self.assertEqual(news_embed.run(self.db.url,keywords_only=True),1)
            provider.close.assert_called_once()



if __name__=='__main__':unittest.main()
