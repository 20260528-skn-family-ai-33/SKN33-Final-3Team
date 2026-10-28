"""중지한 합성 legacy 디렉터리만 사용합니다. 실제 API·DB·원문 접근 없음."""
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from filelock import FileLock, Timeout

from backend.app.services.rag import legacy_migration as migration
from backend.app.services.rag.documents import ArticleDocument
from backend.app.services.rag.store import GenerationStore, MODEL, persistent_client, read_all
from backend.app.scheduler.jobs import news_embed, rag_index

DB_ID = 'a' * 64
VOCAB = {k:{} for k in ['products','countries','synonyms','hypernyms','categories']}


def encode(texts):
    return [[1.0] + [0.0]*3071 for _ in texts]


def article(n, source):
    return ArticleDocument(article_id=n,source_code=source,external_id=f'id{n}',title=f'합성 기사 {n}',
        published_date='2026-10-01',source_url=f'https://example.org/{n}',body='합성 본문. '*180,
        countries=['KR'],kw_topic=['수출'],keywords_ready=True,metadata_missing=['original_kw_sub'])


@pytest.fixture
def legacy(tmp_path):
    source=tmp_path/'legacy';root=tmp_path/'target';catalog=tmp_path/'catalog.json'
    docs=[article(1,'KOTRA_MARKET'),article(2,'KOTRA_SHORT_NEWS')]
    client=persistent_client(source)
    col=client.create_collection('total_article',embedding_function=None,
        metadata={'embedding_model':MODEL,'hnsw:space':'cosine'})
    for doc in docs:
        rows=news_embed.build_docs({'article_id':doc.article_id,'external_id':doc.external_id,
            'source_code':doc.source_code.value,'title':doc.title,'published_date':doc.published_date,
            'category':doc.category,'body_masked':doc.body,'countries':list(doc.countries),'keywords':list(doc.kw_topic)})
        col.add(ids=list(rows),documents=[d for d,m in rows.values()],
                metadatas=[m|{'custom_note':'kept'} for d,m in rows.values()],embeddings=encode(list(rows)))
    client.close()
    catalog.write_text(json.dumps({'version':1,'database_id':DB_ID,'vocabulary':VOCAB,
        'articles':[d.model_dump(mode='json')|{'visibility_status':'VISIBLE'} for d in docs]},ensure_ascii=False),encoding='utf-8')
    return source,root,catalog,docs


def run(legacy,**kw):
    source,root,catalog,_=legacy
    return migration.migrate(source,root,catalog,DB_ID,source_stopped=True,batch_size=1,**kw)


def rows(root):
    snap=GenerationStore(root).snapshot()
    try:return read_all(snap.collection('total_article'),['documents','metadatas','embeddings'])
    finally:snap.client.close()


def test_dry_run_has_zero_source_and_target_writes_and_no_embeddings(legacy,monkeypatch):
    before=migration.fingerprint(legacy[0]);monkeypatch.setattr(news_embed,'openai_embed',Mock(side_effect=AssertionError('no API')))
    out=run(legacy)
    assert out['status']=='planned' and out['embedded']==0
    assert not legacy[1].exists() and migration.fingerprint(legacy[0])==before


def test_copy_preserves_all_ids_text_vectors_metadata_and_repeats(legacy,monkeypatch):
    before=migration.fingerprint(legacy[0]);opened=[]
    def factory(path):opened.append(Path(path));return persistent_client(path)
    monkeypatch.setattr(news_embed,'openai_embed',Mock(side_effect=AssertionError('no API')))
    out=run(legacy,apply=True,client_factory=factory)
    assert out['status']=='ready' and out['embedded']==0 and legacy[0] not in opened
    data=rows(legacy[1]);assert set(data['ids'])==set(migration.expected_records(legacy[3]))
    assert all(m['custom_note']=='kept' and m['record_type']=='body' for m in data['metadatas'])
    assert all(list(v)==encode([''])[0] for v in data['embeddings'])
    assert migration.fingerprint(legacy[0])==before
    target_before=migration.fingerprint(legacy[1]);assert run(legacy,apply=True)['status']=='already_complete'
    assert migration.fingerprint(legacy[1])==target_before
    hits=news_embed.search('합성',root=legacy[1],embed=encode,source_codes=['KOTRA_MARKET'])
    assert hits and all(h[3]['source_code']=='KOTRA_MARKET' for h in hits)


def test_subsequent_refresh_preserves_other_source_ids_vectors_and_unknown_keywords(legacy,monkeypatch):
    run(legacy,apply=True);before=rows(legacy[1]);store=GenerationStore(legacy[1])
    out=store.publish([legacy[3][1]],['KOTRA_SHORT_NEWS'],Mock(side_effect=AssertionError('reuse expected')),vocabulary=VOCAB)
    assert out['embedded']==0;after=rows(legacy[1]);assert set(after['ids'])==set(before['ids'])
    assert all(m['custom_note']=='kept' for m in after['metadatas'])
    from types import SimpleNamespace
    fake_vocab=SimpleNamespace(products={},countries={},synonyms={},hypernyms={},categories=SimpleNamespace(listed=[]))
    def read(engine,sources,*,vocabulary,supplements):
        extra=supplements[legacy[3][0].key][0]
        assert extra['body_available'] and extra['body_masked']==legacy[3][0].body and 'keywords' not in extra
        return [legacy[3][0]],fake_vocab
    monkeypatch.setattr(rag_index.Vocabulary,'load',lambda:fake_vocab)
    monkeypatch.setattr(rag_index,'read_articles',read)
    out=rag_index._refresh(None,['KOTRA_MARKET'],store=store,embed=Mock(side_effect=AssertionError('reuse')),supplements=None,raw_market=False,dry_run=False)
    assert out['embedded']==0 and set(rows(legacy[1])['ids'])==set(before['ids'])


def test_changed_text_not_reused_and_removed_tags_not_retained(legacy):
    run(legacy,apply=True);store=GenerationStore(legacy[1]);new=legacy[3][1].model_copy(update={'kw_topic':()})
    store.publish([new],['KOTRA_SHORT_NEWS'],Mock(side_effect=AssertionError('same text')),vocabulary=VOCAB)
    assert all('keywords' not in m for m in rows(legacy[1])['metadatas'] if m['source_code']=='KOTRA_SHORT_NEWS')
    provider=Mock(side_effect=encode);new=new.model_copy(update={'body':'내용이 달라진 합성 기사.'})
    out=store.publish([new],['KOTRA_SHORT_NEWS'],provider,vocabulary=VOCAB)
    assert out['embedded']==1 and provider.call_count==1
    data=rows(legacy[1]);assert all(k.startswith('1:') for k,m in zip(data['ids'],data['metadatas']) if m['source_code']=='KOTRA_MARKET')
    assert all(not k.startswith('2:') for k,m in zip(data['ids'],data['metadatas']) if m['source_code']=='KOTRA_SHORT_NEWS')


@pytest.mark.parametrize('failure',['batch_written','verified','before_publish'])
def test_failure_preserves_binding_and_retry_starts_fresh(legacy,failure):
    before=migration.fingerprint(legacy[0]);root=legacy[1]
    def fail(stage):
        if stage==failure:raise RuntimeError('synthetic interruption')
    with pytest.raises(RuntimeError):run(legacy,apply=True,hook=fail)
    assert not(root/'active.json').exists() and not(root/'database.json').exists()
    assert migration.fingerprint(legacy[0])==before
    assert run(legacy,apply=True)['status']=='ready'
    assert len(rows(root)['ids'])==len(migration.expected_records(legacy[3]))


@pytest.mark.parametrize('field,value',[('generation','../escape'),('dimensions',8),('sources',['FNNEWS']),('chunks',0)])
def test_corrupt_ready_status_fails_without_writing(legacy,field,value):
    out=run(legacy,apply=True);p=legacy[1]/'generations'/out['generation']/'status.json';d=json.loads(p.read_text(encoding='utf-8'));d[field]=value;p.write_text(json.dumps(d),encoding='utf-8');before=migration.fingerprint(legacy[1])
    with pytest.raises(ValueError):run(legacy,apply=True)
    assert migration.fingerprint(legacy[1])==before


def test_existing_collection_contract_checked(legacy):
    out=run(legacy,apply=True);client=persistent_client(legacy[1]/'generations'/out['generation']/'chroma')
    col=client.get_collection('total_article',embedding_function=None);col.modify(metadata={k:v for k,v in col.metadata.items() if k!='hnsw:space'}|{'dimensions':8});client.close();before=migration.fingerprint(legacy[1])
    with pytest.raises(ValueError,match='existing_collection'):run(legacy,apply=True)
    assert migration.fingerprint(legacy[1])==before


def test_existing_active_and_database_mismatch_preserved(legacy):
    run(legacy,apply=True);before=migration.fingerprint(legacy[1]);catalog=legacy[2];d=json.loads(catalog.read_text(encoding='utf-8'));d['articles'][0]['kw_topic']=['다른 대표어'];catalog.write_text(json.dumps(d),encoding='utf-8')
    with pytest.raises(ValueError,match='already_active'):run(legacy,apply=True)
    assert migration.fingerprint(legacy[1])==before
    (legacy[1]/'database.json').write_text(json.dumps({'version':1,'database_id':'b'*64}),encoding='utf-8');before=migration.fingerprint(legacy[1])
    with pytest.raises(ValueError,match='database_mismatch'):run(legacy,apply=True)
    assert migration.fingerprint(legacy[1])==before


def test_model_and_unknown_body_catalog_fail_closed(legacy):
    c=persistent_client(legacy[0]);old=c.get_collection('total_article',embedding_function=None);data=read_all(old,['documents','metadatas','embeddings']);c.delete_collection('total_article');col=c.create_collection('total_article',embedding_function=None,metadata={'hnsw:space':'cosine'});col.add(**data);c.close()
    with pytest.raises(ValueError,match='provenance_required'):run(legacy)
    assert run(legacy,legacy_model=MODEL)['status']=='planned'
    d=json.loads(legacy[2].read_text(encoding='utf-8'));d['articles'][0]['metadata_missing']=['body_origin'];legacy[2].write_text(json.dumps(d),encoding='utf-8')
    with pytest.raises(ValueError,match='verified_body'):run(legacy,legacy_model=MODEL)


def test_overlap_symlink_and_writer_confirmation(legacy):
    source,root,catalog,_=legacy
    with pytest.raises(ValueError,match='stop_all'):migration.migrate(source,root,catalog,DB_ID)
    with pytest.raises(ValueError,match='overlap'):migration.migrate(source,source/'child',catalog,DB_ID,source_stopped=True)
    link=source.parent/'link';link.symlink_to(source,target_is_directory=True)
    with pytest.raises(ValueError,match='symlink'):migration.migrate(link,root,catalog,DB_ID,source_stopped=True)


def test_lock_contention_does_not_modify_existing_files(legacy):
    root=legacy[1];root.mkdir();lock=FileLock(str(root/'publish.lock'))
    with lock:
        before=migration.fingerprint(root)
        with pytest.raises(Timeout):run(legacy,apply=True)
        assert migration.fingerprint(root)==before


def test_source_changed_during_clone_is_rejected(legacy,monkeypatch):
    original=migration.shutil.copytree
    def changed(src,dst,*args,**kw):
        result=original(src,dst,*args,**kw);(Path(src)/'writer-was-active').write_text('synthetic');return result
    monkeypatch.setattr(migration.shutil,'copytree',changed)
    with pytest.raises(ValueError,match='changed_during_copy'):run(legacy)
    assert not legacy[1].exists()


def test_source_and_target_owned_clients_close_before_publish(legacy):
    states=[]
    class Owned:
        def __init__(self,path):self.client=persistent_client(path);self.closed=False;states.append(self)
        def __getattr__(self,name):return getattr(self.client,name)
        def close(self):self.client.close();self.closed=True
    def hook(stage):
        if stage=='verified':assert states and all(x.closed for x in states)
    run(legacy,apply=True,client_factory=Owned,hook=hook)
    assert all(x.closed for x in states)


def test_utf8_sql_reads_are_explicit(monkeypatch):
    import importlib.util
    from backend.tests.test_admin_news_views import ArticleViewsTest
    original=Path.read_text
    def strict(path,*args,**kwargs):
        if path.suffix=='.sql':assert kwargs.get('encoding')=='utf-8'
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'read_text',strict)
    case=ArticleViewsTest();case.check_views(False);case.check_views(True);case.doCleanups()
    root=Path(__file__).resolve().parents[2];spec=importlib.util.spec_from_file_location('utf8_admin_migration',root/'db/mysql/migrations/apply_admin_news_erd.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    assert module.steps()


@pytest.mark.parametrize('which',['source','target'])
def test_close_failure_never_publishes_and_can_retry(legacy,which):
    states=[]
    class Owned:
        def __init__(self,path):self.path=Path(path);self.client=persistent_client(path);self.closed=False;states.append(self)
        def __getattr__(self,name):return getattr(self.client,name)
        def close(self):
            self.client.close();self.closed=True
            target='generations' in self.path.parts
            if target==(which=='target'):raise RuntimeError('synthetic close failure')
    with pytest.raises(RuntimeError,match='close failure'):run(legacy,apply=True,client_factory=Owned)
    assert all(x.closed for x in states)
    assert not(legacy[1]/'active.json').exists() and not(legacy[1]/'database.json').exists()
    statuses=[json.loads(x.read_text(encoding='utf-8'))['status'] for x in (legacy[1]/'generations').glob('*/status.json')]
    assert statuses and all(s in ('building','failed') for s in statuses)
    assert run(legacy,apply=True)['status']=='ready'


def test_failure_after_pointer_replace_rolls_back_new_binding(legacy,monkeypatch):
    original=migration.atomic_json
    def fail(path,data):
        original(path,data)
        if Path(path).name=='active.json':raise OSError('synthetic fsync failure after rename')
    monkeypatch.setattr(migration,'atomic_json',fail)
    with pytest.raises(OSError):run(legacy,apply=True)
    assert not(legacy[1]/'active.json').exists() and not(legacy[1]/'database.json').exists()
    monkeypatch.setattr(migration,'atomic_json',original)
    assert run(legacy,apply=True)['status']=='ready'


@pytest.mark.parametrize('change',['source','external_id','body','duplicate','unknown_visibility'])
def test_inconsistent_or_incomplete_catalog_is_not_guessed(legacy,change):
    d=json.loads(legacy[2].read_text(encoding='utf-8'))
    if change=='source':d['articles'][0]['source_code']='FNNEWS'
    elif change=='external_id':d['articles'][0]['external_id']='wrong'
    elif change=='body':d['articles'][0]['body']='다른 본문'
    elif change=='duplicate':d['articles'].append(d['articles'][0])
    else:del d['articles'][0]['visibility_status']
    legacy[2].write_text(json.dumps(d),encoding='utf-8')
    with pytest.raises(ValueError):run(legacy)
    assert not legacy[1].exists()


def test_cli_does_not_print_catalog_values_in_validation_errors(legacy,capsys):
    from backend.app.scheduler.jobs.news_index_migrate import main
    d=json.loads(legacy[2].read_text(encoding='utf-8'));sentinel='PRIVATE_SYNTHETIC_SENTINEL';d['articles'][0]['article_id']=sentinel;legacy[2].write_text(json.dumps(d),encoding='utf-8')
    assert main(['--source',str(legacy[0]),'--root',str(legacy[1]),'--catalog',str(legacy[2]),'--database-id',DB_ID,'--source-stopped'])==1
    output=capsys.readouterr().out;assert sentinel not in output and 'migration_validation_failed' in output


def test_real_sql_adapter_to_migrated_generation_and_public_refresh_without_reembedding(tmp_path):
    from backend.tests.test_shortnews_pipeline import PipelineTest
    from backend.app.services.rag.ingest import read_articles
    from backend.app.services.rag.vocabulary import Vocabulary
    from backend.app.db.news_lock import engine_identity
    from backend.app.db.batch_log import ensure_source, Source
    from backend.app.db.session import session_scope
    from backend.app.scheduler.jobs.shortnews_load import reflect
    case=PipelineTest();case.setUp()
    try:
        assert case.run_pipeline()['status']=='SUCCESS'
        with session_scope(case.db.engine) as session:
            ensure_source(session,Source('KOTRA_MARKET','합성 시장뉴스',{}))
            row={k:v for k,v in case.db.rows('article')[0].items() if k!='article_id'}
            row.update(source_code='KOTRA_MARKET',external_id='market1',title='합성 시장 기사')
            session.execute(reflect(case.db.engine,'article').insert().values(**row))
        article_rows=case.db.rows('article');market_row=next(r for r in article_rows if r['source_code']=='KOTRA_MARKET')
        with session_scope(case.db.engine) as session:
            session.execute(reflect(case.db.engine,'article_tag').insert(),[
                {'article_id':market_row['article_id'],'country_code':'US','keyword_text':None},
                {'article_id':market_row['article_id'],'country_code':None,'keyword_text':'AI'}])
        supplements={('KOTRA_MARKET','market1'):{'body_masked':market_row['body_masked'],'body_available':True}}
        docs,vocab=read_articles(case.db.engine,['KOTRA_MARKET','KOTRA_SHORT_NEWS'],vocabulary=Vocabulary.load(),supplements=supplements)
        assert next(d for d in docs if d.source_code.value=='KOTRA_MARKET').raw_keywords is None
        exported={'products':vocab.products,'countries':vocab.countries,'synonyms':vocab.synonyms,
                  'hypernyms':vocab.hypernyms,'categories':{t:vocab.categories.lookup(t) for t in vocab.categories.listed}}
        source=tmp_path/'legacy_sql';target=tmp_path/'migrated_sql';catalog=tmp_path/'sql_catalog.json'
        c=persistent_client(source);col=c.create_collection('total_article',embedding_function=None,metadata={'embedding_model':MODEL,'hnsw:space':'cosine'})
        for doc in docs:
            data=news_embed.build_docs({'article_id':doc.article_id,'external_id':doc.external_id,'source_code':doc.source_code.value,
                'title':doc.title,'published_date':doc.published_date,'category':doc.category,'body_masked':doc.body,
                'countries':list(doc.countries),'keywords':list(doc.kw_topic)})
            col.add(ids=list(data),documents=[d for d,m in data.values()],metadatas=[m for d,m in data.values()],embeddings=encode(list(data)))
        c.close();identity=engine_identity(case.db.engine)
        catalog.write_text(json.dumps({'version':1,'database_id':identity,'vocabulary':exported,
            'articles':[d.model_dump(mode='json')|{'visibility_status':'VISIBLE'} for d in docs]}),encoding='utf-8')
        migration.migrate(source,target,catalog,identity,source_stopped=True,apply=True)
        before=rows(target);provider=Mock(side_effect=encode, usage={})
        out=news_embed.run_sources(['KOTRA_MARKET'],db_url=case.db.url,root=target,embed=provider)
        assert out['status']=='complete' and out['body']['embedded']==0,out
        assert all(call.args[0]==['AI'] for call in provider.call_args_list)  # Only new global keyword needs a vector.
        assert set(rows(target)['ids'])==set(before['ids'])
        assert news_embed.keyword_article_counts(case.db.engine)['AI']==2
        out=news_embed.run_sources(['KOTRA_SHORT_NEWS'],db_url=case.db.url,root=target,embed=Mock(side_effect=AssertionError('no duplicate embeddings')))
        assert out['status']=='complete' and out['body']['embedded']==0,out
        assert set(rows(target)['ids'])==set(before['ids'])
        hits=news_embed.search('합성',root=target,embed=encode,source_codes=['KOTRA_MARKET'])
        assert hits and all(row[3]['source_code']=='KOTRA_MARKET' for row in hits)
    finally:
        case.doCleanups()


def test_target_symlink_and_scratch_overlap_fail_closed(legacy,monkeypatch):
    source,root,_,_=legacy;root.mkdir();(root/'generations').symlink_to(source,target_is_directory=True)
    with pytest.raises(ValueError,match='symlink'):run(legacy)
    (root/'generations').unlink();before=migration.fingerprint(root)
    monkeypatch.setattr(migration,'gettempdir',lambda:str(root))
    with pytest.raises(ValueError,match='scratch_overlap'):run(legacy)
    assert migration.fingerprint(root)==before


@pytest.mark.parametrize('bad',['zero','nan','dimension'])
def test_bad_vector_never_publishes(legacy,bad):
    class Collection:
        def __init__(self,col):self.col=col
        def __getattr__(self,key):return getattr(self.col,key)
        def get(self,**kw):
            page=self.col.get(**kw)
            if page['ids']:
                vec=[0.0]*3072 if bad=='zero' else [float('nan')]+[0.0]*3071 if bad=='nan' else [1.0]*8
                page['embeddings']=[vec for _ in page['ids']]
            return page
    class Client:
        def __init__(self,path):self.client=persistent_client(path)
        def __getattr__(self,key):return getattr(self.client,key)
        def get_collection(self,*a,**kw):return Collection(self.client.get_collection(*a,**kw))
    with pytest.raises(ValueError,match='invalid_embedding'):run(legacy,apply=True,client_factory=Client)
    assert not(legacy[1]/'active.json').exists() and not(legacy[1]/'database.json').exists()


def test_readonly_clone_copies_wal_shm_and_hnsw_without_opening_source(tmp_path):
    source=tmp_path/'synthetic_source';source.mkdir()
    (source/'segment').mkdir()
    for name in ('chroma.sqlite3','chroma.sqlite3-wal','chroma.sqlite3-shm','segment/data_level0.bin','segment/header.bin'):
        (source/name).write_bytes(('synthetic '+name).encode())
    before=migration.fingerprint(source);opened=[];client=Mock()
    def factory(path):
        assert path != source and migration.fingerprint(path)==before
        opened.append(path)
        return client
    with migration.readonly_clone(source,factory) as (copied,files):
        assert copied is client and files==before
    client.close.assert_called_once()
    assert migration.fingerprint(source)==before and not opened[0].exists()


def test_source_change_before_publication_does_not_publish(legacy):
    def hook(stage):
        if stage=='before_publish':(legacy[0]/'late_writer').write_bytes(b'synthetic mutation')
    with pytest.raises(ValueError,match='migration_source_changed_during_copy'):
        run(legacy,apply=True,hook=hook)
    assert not (legacy[1]/'active.json').exists()
    assert not (legacy[1]/'database.json').exists()


def test_explicit_different_legacy_model_is_rejected_without_state_changes(legacy,capsys):
    from backend.app.scheduler.jobs.news_index_migrate import main
    source,root,catalog,_=legacy
    wrong_model='text-embedding-3-small'
    source_before=migration.fingerprint(source);catalog_before=catalog.read_bytes()
    # Direct function input reaches model validation; the source declares MODEL.
    with pytest.raises(ValueError,match='^migration_model_or_metric_provenance_required$'):
        run(legacy,legacy_model=wrong_model)
    assert migration.fingerprint(source)==source_before and not root.exists()
    assert catalog.read_bytes()==catalog_before
    # Also protect a previously published destination when --apply is requested.
    assert run(legacy,apply=True)['status']=='ready'
    target_before=migration.fingerprint(root)
    for apply in (False,True):
        with pytest.raises(ValueError,match='^migration_model_or_metric_provenance_required$'):
            run(legacy,legacy_model=wrong_model,apply=apply)
        assert migration.fingerprint(source)==source_before
        assert migration.fingerprint(root)==target_before
        assert catalog.read_bytes()==catalog_before
    # CLI choices reject the unsupported name before calling the function.
    with pytest.raises(SystemExit) as rejected:
        main(['--source',str(source),'--root',str(root),'--catalog',str(catalog),
              '--database-id',DB_ID,'--source-stopped','--legacy-model',wrong_model,'--apply'])
    assert rejected.value.code==2
    assert wrong_model in capsys.readouterr().err
    assert migration.fingerprint(source)==source_before
    assert migration.fingerprint(root)==target_before
    assert catalog.read_bytes()==catalog_before


def test_cli_catalog_database_id_mismatch_is_rejected_without_state_changes(legacy,capsys):
    from backend.app.scheduler.jobs.news_index_migrate import main
    source,root,catalog,_=legacy
    wrong_database_id='b'*64
    assert json.loads(catalog.read_text(encoding='utf-8'))['database_id']==DB_ID
    source_before=migration.fingerprint(source);catalog_before=catalog.read_bytes()
    args=['--source',str(source),'--root',str(root),'--catalog',str(catalog),
          '--database-id',wrong_database_id,'--source-stopped']
    # Exercise actual argparse arguments, including --apply, with no target binding.
    for flags in ([],['--apply']):
        assert main(args+flags)==1
        assert json.loads(capsys.readouterr().out)=={
            'status':'failed','error_type':'ValueError','error_code':'migration_catalog_database_mismatch'}
        assert not root.exists()
        assert migration.fingerprint(source)==source_before
        assert catalog.read_bytes()==catalog_before
    assert run(legacy,apply=True)['status']=='ready'
    target_before=migration.fingerprint(root)
    for flags in ([],['--apply']):
        assert main(args+flags)==1
        assert json.loads(capsys.readouterr().out)['error_code']=='migration_catalog_database_mismatch'
        assert migration.fingerprint(source)==source_before
        assert migration.fingerprint(root)==target_before
        assert catalog.read_bytes()==catalog_before


def test_persisted_non_axis_vectors_are_copied_exactly_without_reinsertion(tmp_path, monkeypatch):
    import numpy as np
    source=tmp_path/'non_axis_legacy';root=tmp_path/'target';catalog=tmp_path/'catalog.json'
    docs=[article(i+1,'KOTRA_MARKET').model_copy(update={'body':'합성 원문 '+str(i),'kw_topic':()}) for i in range(1001)]
    expected=migration.expected_records(docs)
    vectors=np.random.default_rng(27).normal(size=(len(expected),3072)).astype('float32')
    vectors/=np.linalg.norm(vectors,axis=1)[:,None]
    client=persistent_client(source)
    col=client.create_collection('total_article',embedding_function=None,
        metadata={'embedding_model':MODEL,'hnsw:space':'cosine'})
    ids=list(expected)
    col.add(ids=ids,documents=[expected[k][0] for k in ids],
            metadatas=[{'article_id':expected[k][2].article_id,'chunk':0,'keywords':['2027년']} for k in ids],
            embeddings=vectors.tolist())
    other=client.create_collection('news_keyword',embedding_function=None)
    other.add(ids=['kept'],documents=['다른 컬렉션'],metadatas=[{'kept':True}],embeddings=[[1.,0.]])
    client.close()
    before=migration.fingerprint(source)
    catalog.write_text(json.dumps({'version':1,'database_id':DB_ID,'vocabulary':VOCAB,
        'articles':[d.model_dump(mode='json')|{'visibility_status':'VISIBLE'} for d in docs]},ensure_ascii=False),encoding='utf-8')
    # Explicitly forbid vector/document insertion on the owned migration target.
    class TargetCollection:
        def __init__(self,col):self.col=col
        def __getattr__(self,name):return getattr(self.col,name)
        def add(self,**kw):raise AssertionError('vector reinsertion forbidden')
        def update(self,**kw):
            assert set(kw)=={'ids','metadatas'}
            return self.col.update(**kw)
    class Client:
        def __init__(self,path):self.client=persistent_client(path);self.target='generations' in Path(path).parts
        def __getattr__(self,name):return getattr(self.client,name)
        def get_collection(self,*args,**kw):
            col=self.client.get_collection(*args,**kw)
            return TargetCollection(col) if self.target else col
    out=migration.migrate(source,root,catalog,DB_ID,apply=True,source_stopped=True,client_factory=Client,batch_size=256)
    assert out['status']=='ready' and out['embedded']==0 and out['keyword_changed_chunks']==1001
    assert migration.fingerprint(source)==before
    with migration.readonly_clone(source,persistent_client) as (reader,_):
        old=read_all(reader.get_collection('total_article',embedding_function=None),['documents','embeddings'])
    new=rows(root)
    original={k:(d,list(v)) for k,d,v in zip(old['ids'],old['documents'],old['embeddings'])}
    assert set(original)==set(new['ids'])
    for key,document,meta,vector in zip(new['ids'],new['documents'],new['metadatas'],new['embeddings']):
        assert (document,list(vector))==original[key]
        assert 'keywords' not in meta and json.loads(meta['payload'])['kw_topic']==[]
    snap=GenerationStore(root).snapshot()
    try:assert [c.name for c in snap.client.list_collections()]==['total_article']
    finally:snap.client.close()
    target_before=migration.fingerprint(root)
    assert migration.migrate(source,root,catalog,DB_ID,apply=True,source_stopped=True)['status']=='already_complete'
    assert migration.fingerprint(root)==target_before and migration.fingerprint(source)==before
