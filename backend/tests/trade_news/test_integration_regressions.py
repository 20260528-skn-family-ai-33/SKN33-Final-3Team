"""C/S 통합 결함 회귀. 합성 SQL·임시 Chroma·fake SDK만 사용합니다."""
from contextlib import closing
from copy import deepcopy
import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, text

from backend.app.schemas.rag import ALL_SOURCES, RagRequest
from backend.app.scheduler.jobs import news_embed as ne, rag_index
from backend.app.services import shortnews_analysis as analysis
from backend.app.services.rag import store as st
from backend.app.services.rag.factory import create_service
from backend.app.services.rag.service import RagService
from backend.app.services.rag.runtime import ExecutionContext, RagDeadlineExceeded
from backend.tests.rag_fixtures import request_payload
from .test_common_news_vector import context
from .test_daily_scheduler import local


def test_two_database_same_root_rejected_before_publication(context, tmp_path, monkeypatch):
    db, root, embed, _ = context
    assert ne.run_sources(ALL_SOURCES, root=root, embed=embed)['status']=='complete'
    pointer=(root/'active.json').read_bytes(); identity=(root/'database.json').read_bytes()
    other=create_engine('sqlite:///'+str(tmp_path/'other.sqlite3'))
    try:
        monkeypatch.setattr(ne,'make_engine',lambda url:other)
        result=ne.run_sources(['HANKYUNG'],root=root,embed=embed)
        assert result['error_code']=='source_database_mismatch'
        assert (root/'active.json').read_bytes()==pointer
        assert (root/'database.json').read_bytes()==identity
        assert b'password' not in identity and str(db.url).encode() not in identity
    finally:other.dispose()


def test_explicit_env_keyword_cli_producer_consumer_and_ownership(context,monkeypatch,tmp_path):
    db,root,embed,_=context
    monkeypatch.setenv('NEWS_VECTOR_ROOT',str(root))
    owned=Mock(side_effect=embed);owned.usage=embed.usage
    monkeypatch.setattr(ne,'openai_embed',lambda:owned)
    assert ne.run(keywords_only=True,log_dir=tmp_path/'logs')==0
    owned.close.assert_called_once()
    assert (root/'keywords/chroma.sqlite3').exists()
    assert ne.search_keywords('무역협정',root=root,embed=embed)==ne.search_keywords('무역협정',embed=embed)
    # 명시 root의 본문 생산도 동일 keyword 저장소를 사용합니다.
    result=ne.run_sources(ALL_SOURCES,root=root,embed=embed)
    assert result['status']=='complete' and result['keywords']['added']==0


@pytest.mark.parametrize('fail',[False,True])
def test_unreported_usage_is_not_zero_confirmed(context,monkeypatch,fail):
    _,root,_,_=context
    def encode(words):
        if fail:raise TimeoutError('synthetic')
        return [[1.0]+[0.0]*3071 for _ in words]
    encode.usage={'tokens':0,'reported_responses':0}
    snapshots=[]
    out=ne.run_sources(['HANKYUNG'],root=root,embed=encode,progress=snapshots.append)
    assert out['usage']['body']['reported'] is False
    assert out['usage']['body']['unreported_calls']==1
    assert out['usage']['body']['estimated_usd'] is None
    assert snapshots[0]['body']['status']=='running'
    assert snapshots[0]['usage']=={}
    assert out['status']==('failed' if fail else 'complete')


@pytest.mark.parametrize('mode',['valid','invalid','missing','timeout'])
def test_sdk_usage_recorded_before_validation(mode):
    ledger=[]; api=Mock()
    usage=None if mode=='missing' else NS(total_tokens=9,prompt_tokens=9,completion_tokens=0)
    api.embeddings.create.return_value=NS(usage=usage,data=[NS(index=0,embedding=[1.0]* (2 if mode=='invalid' else 3072))])
    if mode=='timeout':api.embeddings.create.side_effect=TimeoutError('synthetic')
    if mode in ('invalid','timeout'):
        with pytest.raises((ValueError,TimeoutError)):analysis.embed(api,['합성'],usage_sink=ledger.append)
    else:assert len(analysis.embed(api,['합성'],usage_sink=ledger.append)[0])==3072
    assert len(ledger)==1
    assert ledger[0]['reported']==(mode in ('valid','invalid'))
    assert ledger[0]['tokens']==(9 if mode in ('valid','invalid') else None)


def test_malformed_llm_response_preserves_usage():
    api=Mock();ledger=[]
    api.chat.completions.create.return_value=NS(usage=NS(total_tokens=15,prompt_tokens=10,completion_tokens=5),
        choices=[NS(finish_reason='stop',message=NS(refusal=None,content='not json'))])
    with pytest.raises(ValueError):analysis.ask(api,{'title':'제목','body_masked':'본문'},[{'keyword':'AI'}],usage_sink=ledger.append)
    assert ledger[0]['tokens']==15 and ledger[0]['reported']


def test_db_canonical_newspaper_terms_survive_file_dictionary(context,monkeypatch):
    db,root,embed,_=context
    vocabulary=rag_index.Vocabulary.load()
    vocabulary.clean=lambda words:{'kw_topic':['다른말'],'kw_sub':[],'kw_category':[]}
    out=ne.run_sources(['HANKYUNG','FNNEWS'],root=root,embed=embed)
    assert out['status']=='complete'
    snap=st.GenerationStore(root).snapshot()
    with closing(snap.client):
        rows=snap.collection('total_article').get(include=['metadatas'])
        assert all(row['keywords']==['무역협정'] for row in rows['metadatas'])


def test_four_source_sql_index_search_rag_summary_usage_trace(context,monkeypatch):
    db,root,embed,_=context
    extras={('KOTRA_MARKET','same-id'):{'body_masked':'합성 본문 1','body_available':True,'keywords':['무역협정']}}
    rag_index.refresh(db,ALL_SOURCES,store=st.GenerationStore(root),embed=embed,supplements=extras,raw_market=False)
    # 같은 원문 ID도 출처가 다르면 서로 다른 기사입니다. 검색조건은 top-k 전에 적용합니다.
    found=ne.search('무역',n=20,root=root,embed=embed,country='US',keyword='무역협정',
                    date_from='2026-10-24',date_to='2026-10-24')
    assert {row[3]['source_code'] for row in found}=={s.value for s in ALL_SOURCES}
    assert ne.search('무역',root=root,embed=embed,country='FR')==[]
    api=Mock();api.with_options.return_value=api
    api.embeddings.create.side_effect=lambda **kw:NS(usage=NS(total_tokens=7),data=[NS(index=i,embedding=v) for i,v in enumerate(embed(kw['input']))])
    def completion(**kw):
        content=json.loads(kw['messages'][-1]['content']); chunks=content['chunks']; key=next(iter(chunks))
        payload={'sentences':[{'text':'합성 본문에 근거한 요약.','quote':chunks[key],'chunk_id':key}]}
        return NS(usage=NS(total_tokens=11),choices=[NS(finish_reason='stop',message=NS(refusal=None,content=json.dumps(payload)))])
    api.chat.completions.create.side_effect=completion
    service=create_service(api,root=root)
    payload=request_payload();payload['conditions']['period']={'start':'2026-10-24','end':'2026-10-24'}
    request=RagRequest.model_validate(payload);ctx=ExecutionContext(time.monotonic()+30)
    result=service.run(request,ctx)
    assert result.returned_count==4 and len({a.key for a in result.articles})==4
    assert all(a.summary and a.evidence for a in result.articles)
    assert ctx.model_calls==5 and ctx.tokens==51
    assert len(list((root/'traces').glob('*.json')))==1
    before=api.chat.completions.create.call_count
    service.run(request,ExecutionContext(time.monotonic()+30))
    assert api.chat.completions.create.call_count==before # 요약 캐시는 재청구하지 않습니다.
    payload['conditions']['blocked_topics']=['무역협정']
    blocked=service.run(RagRequest.model_validate(payload),ExecutionContext(time.monotonic()+30))
    assert blocked.returned_count==0 and api.chat.completions.create.call_count==before
    api.close.assert_not_called() # 호출자가 제공한 API의 소유권은 호출자에게 있습니다.


@pytest.mark.parametrize('mode',['success','failure','timeout'])
def test_rag_owned_snapshot_closed_in_every_exit(context,monkeypatch,mode):
    db,root,embed,_=context
    ne.run_sources(ALL_SOURCES,root=root,embed=embed)
    handles=[];original=st.GenerationStore.snapshot
    def snapshot(self):
        result=original(self); result.client.close=Mock(wraps=result.client.close);handles.append(result.client);return result
    monkeypatch.setattr(st.GenerationStore,'snapshot',snapshot)
    from backend.tests.test_rag_pipeline import FakeGateway
    gateway=FakeGateway();gateway.embed=lambda texts,ctx:embed(texts)
    if mode!='success':gateway.embed=Mock(side_effect=RagDeadlineExceeded('synthetic') if mode=='timeout' else RuntimeError('synthetic'))
    service=RagService(st.GenerationStore(root),rag_index.Vocabulary.load(),gateway)
    request=request_payload();request['conditions']['period']={'start':'2026-10-24','end':'2026-10-24'}
    if mode=='timeout':
        with pytest.raises(RagDeadlineExceeded):service.run(request,ExecutionContext(time.monotonic()+30))
    else:service.run(request,ExecutionContext(time.monotonic()+30))
    assert len(handles)==1;handles[0].close.assert_called_once()


def test_admin_latest_admin_view_reapply_keeps_market_scope():
    root=Path(__file__).resolve().parents[3]
    paths=['db/mysql/migrations/20261023_admin_news_erd.sql','db/mysql/migrations/20261025_news_source_views.sql']
    with sqlite3.connect(':memory:') as db:
        db.executescript("CREATE TABLE article(article_id INTEGER,source_code TEXT,published_date TEXT,visibility_status TEXT);CREATE TABLE article_tag(article_id INTEGER,country_code TEXT);")
        for i,s in enumerate(ALL_SOURCES):
            db.execute("INSERT INTO article VALUES (?,?,'2026-10-24','VISIBLE')",(i,s.value))
            db.execute("INSERT INTO article_tag VALUES (?,'US')",(i,))
        for path in [paths[0],paths[1],paths[0]]:
            definitions=re.findall(r'CREATE(?: OR REPLACE)? VIEW (v_article_\w+)\s+AS\s+(.+?);',(root/path).read_text(),re.S)
            assert len(definitions)==6
            for name,select in definitions:
                db.execute('DROP VIEW IF EXISTS '+name);db.execute('CREATE VIEW '+name+' AS '+select)
            assert db.execute('SELECT article_count FROM v_article_day').fetchone()==(1,)
            assert db.execute('SELECT article_count FROM v_article_integrated_day').fetchone()==(2,)
            assert db.execute('SELECT SUM(article_count) FROM v_article_source_day').fetchone()==(4,)


@pytest.mark.parametrize('paper,short',[(False,False),(True,False),(False,True),(True,True)])
def test_independent_scheduler_switches_never_execute(local,monkeypatch,paper,short):
    from backend.app.scheduler import main
    monkeypatch.setenv('NEWS_DAILY_ENABLED',str(paper).lower());monkeypatch.setenv('SHORTNEWS_DAILY_ENABLED',str(short).lower())
    scheduler=main.build_scheduler(env_path=local.env)
    trade=scheduler.get_job('trade_news_daily')
    assert (getattr(trade,'next_run_time','pending') is None)==(not paper)
    assert bool(scheduler.get_job('shortnews_daily'))==short
    assert bool(scheduler.get_job('shortnews_startup'))==short


def test_shortnews_usage_cache_and_partial_retry_do_not_double_bill(tmp_path):
    from backend.tests.test_shortnews_pipeline import PipelineTest
    from backend.app.scheduler.jobs import news_embed
    from unittest.mock import patch
    case=PipelineTest();case.setUp()
    try:
        def encode(api, words, *, usage_sink=None):
            analysis.record_usage(usage_sink,analysis.MODEL,NS(usage=NS(total_tokens=3*len(words),prompt_tokens=3*len(words))))
            return [[1.0]+[0.0]*3071 for _ in words]
        def ask(api,article,choices,*,usage_sink=None):
            analysis.record_usage(usage_sink,analysis.CHAT_MODEL,NS(usage=NS(total_tokens=10,prompt_tokens=8,completion_tokens=2)))
            return {'summary_sentences':['합성 요약.'],'keywords':['AI']}
        case.embed.side_effect=encode;case.ask.side_effect=ask
        first=case.run_pipeline()
        assert first['status']=='SUCCESS'
        assert {'classification_index','candidate_query','analysis_llm','body','keywords'}<=first['usage'].keys()
        assert first['usage']['body']['tokens']==first['chroma_result']['usage']['body']['tokens']
        second=case.run_pipeline()
        assert second['status']=='SUCCESS' and second['analysis_requested']==0 and second['usage']=={}
        assert first['run_id']!=second['run_id']
        from backend.tests.test_shortnews_load import raw
        case.path.write_text(json.dumps([raw(body='변경된 합성 기사 본문')]))
        with patch.object(news_embed,'sync_all_keywords',side_effect=RuntimeError('synthetic')):
            partial=case.run_pipeline()
        assert partial['status']=='PARTIAL' and partial['usage']['analysis_llm']['tokens']==10
        stored=json.loads((case.db.root/'pipeline/pipeline_result.json').read_text())
        assert stored['usage']==partial['usage']
        recovered=case.run_pipeline()
        assert recovered['status']=='SUCCESS' and recovered['analysis_requested']==0 and recovered['usage']=={}
    finally:case.doCleanups()


def test_metrics_exclude_newspapers_and_preserve_shortnews_hs(context):
    from datetime import date
    from backend.app.scheduler.jobs.news_load import rebuild_daily
    from sqlalchemy.orm import Session
    db,root,embed,_=context
    with db.begin() as conn:
        conn.execute(text("INSERT INTO article_tag(article_id,hs_code,hs_ver) VALUES (2,'850760','HS2022')"))
    with Session(db) as session,session.begin():rebuild_daily(session,{date(2026,10,24)})
    with db.connect() as conn:
        assert conn.execute(text("SELECT day_count FROM news_keyword_daily WHERE country_key='*'")).scalar()==2
        assert conn.execute(text('SELECT COUNT(*) FROM news_product_daily')).scalar()==0
        assert conn.execute(text("SELECT COUNT(*) FROM article_tag WHERE article_id=2 AND hs_code='850760'")).scalar()==1


def test_memory_engine_identity_is_stable_but_not_shared():
    from backend.app.db.news_lock import engine_identity
    first=create_engine('sqlite://');second=create_engine('sqlite://')
    try:
        assert engine_identity(first)==engine_identity(first)
        assert engine_identity(first)!=engine_identity(second)
    finally:first.dispose();second.dispose()


def test_mixed_reported_and_missing_usage_retains_known_tokens(context,monkeypatch):
    _,root,_,_=context
    def provider(words):
        if provider.usage['reported_responses']==0:
            provider.usage.update(tokens=7,reported_responses=1)
        return [[1.0]+[0.0]*3071 for _ in words]
    provider.usage={'tokens':0,'reported_responses':0}
    def refresh(*args,embed,**kwargs):
        embed(['first']);embed(['second'])
        return {'embedded':2}
    monkeypatch.setattr(rag_index,'refresh',refresh)
    monkeypatch.setattr(ne,'sync_all_keywords',lambda *a,**kw:{'status':'complete'})
    result=ne.run_sources(['HANKYUNG'],root=root,embed=provider)
    usage=result['usage']['body']
    assert usage['model_calls']==2 and usage['tokens']==7
    assert usage['reported_responses']==1 and usage['unreported_calls']==1
    assert usage['reported'] is False


@pytest.mark.parametrize('default_root',[False,True])
def test_env_and_explicit_root_have_identical_legacy_policy(tmp_path,monkeypatch,default_root):
    default=tmp_path/'default'; custom=default if default_root else tmp_path/'custom'
    legacy=tmp_path/'legacy';legacy.mkdir()
    with sqlite3.connect(legacy/'chroma.sqlite3') as conn:
        conn.execute('CREATE TABLE collections(name TEXT)')
        conn.execute("INSERT INTO collections VALUES ('total_article')")
    monkeypatch.setattr(st,'ROOT',default);monkeypatch.setattr(st,'LEGACY_ROOT',legacy)
    monkeypatch.setenv('NEWS_VECTOR_ROOT',str(custom))
    implicit=st.GenerationStore();explicit=st.GenerationStore(custom)
    assert implicit.root==explicit.root and implicit.legacy_path==explicit.legacy_path
    for store in (implicit,explicit):
        if default_root:
            with pytest.raises(ValueError,match='legacy_direct_index_migration_required'):store.require_active_or_no_legacy()
        else:store.require_active_or_no_legacy()


def test_prepare_cli_preserves_keyword_dictionary_and_source_bytes(tmp_path, monkeypatch):
    import sys
    import prepare
    from .helpers import article
    from news.dictionary import dictionary_fingerprint
    source=tmp_path/'original.sqlite3'; output=tmp_path/'prepared.sqlite3'
    row=article(); dictionary={'countries':[], 'products':[],
        'keywords':[{'term':'반도체','canonical':'전자부품'}]}
    mapping=tmp_path/'dictionary.json';st.atomic_json(mapping,dictionary)
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE article_history(site TEXT,article_id TEXT,payload TEXT)')
        db.execute('INSERT INTO article_history VALUES(?,?,?)',('hankyung',row['source_article_id'],json.dumps(row)))
    before=source.read_bytes()
    monkeypatch.setattr(sys,'argv',['prepare','--database',str(source),'--output-database',str(output),'--dictionary',str(mapping)])
    for _ in range(2):prepare.main()
    with sqlite3.connect(output) as db:
        prepared=json.loads(db.execute('SELECT payload FROM prepared_article').fetchone()[0])
        tags=[json.loads(r[0]) for r in db.execute('SELECT payload FROM prepared_tag')]
        assert [r['keyword_text'] for r in tags if r['kind']=='keyword']==['전자부품']
        assert prepared['tagging']['dictionary_sha256']==dictionary_fingerprint(dictionary)
        assert db.execute('SELECT COUNT(*) FROM raw_version').fetchone()[0]==1
    assert source.read_bytes()==before


def test_common_fixture_autodiscovery_only_reads_temporary_directory(context, monkeypatch):
    from backend.app.scheduler.jobs import news_load
    db,root,embed,_=context
    path=news_load.RAW_DIR
    assert path.parent==root.parent
    original=news_load.latest_raw;seen=[]
    def checked(folder):
        assert folder==path
        seen.append(folder)
        return original(folder)
    monkeypatch.setattr(news_load,'latest_raw',checked)
    result=rag_index.refresh(db,ALL_SOURCES,store=st.GenerationStore(root),embed=embed)
    assert result['status']=='ready' and seen==[path]


def test_current_mysql_fixture_uses_final_views_and_newspapers_remain_separate(tmp_path):
    from . import test_mysql_admin_integration as fixture
    import inspect
    from .helpers import engine
    root=Path(__file__).resolve().parents[3]
    assert '20261025_news_source_views.sql' in inspect.getsource(fixture.local_db.__wrapped__)
    sql=(root/'db/mysql/migrations/20261025_news_source_views.sql').read_text(encoding='utf-8')
    db=engine(tmp_path/'views.sqlite3')
    try:
        with db.begin() as conn:
            conn.execute(text("ALTER TABLE article ADD COLUMN visibility_status TEXT NOT NULL DEFAULT 'VISIBLE'"))
            for query in re.findall(r'CREATE OR REPLACE VIEW .*?;',sql):
                conn.execute(text(query.replace('CREATE OR REPLACE VIEW','CREATE VIEW')))
            for n,source in enumerate(('HANKYUNG','FNNEWS'),1):
                conn.execute(text("INSERT INTO article(article_id,source_code,external_id,title,published_date,source_url,body_masked) VALUES (:n,:s,:i,'합성','2026-10-21','https://example.test','합성 본문')"),{'n':n,'s':source,'i':str(n)})
                conn.execute(text("INSERT INTO article_tag(article_id,country_code) VALUES (:n,'US')"),{'n':n})
            for view in ('v_article_day','v_article_country_day','v_article_integrated_day','v_article_integrated_country_day'):
                assert conn.execute(text('SELECT COALESCE(SUM(article_count),0) FROM '+view)).scalar()==0
            assert set(conn.execute(text('SELECT source_code,article_count FROM v_article_source_day')).all())=={('HANKYUNG',1),('FNNEWS',1)}
            assert conn.execute(text('SELECT SUM(article_count) FROM v_article_source_country_day')).scalar()==2
    finally:db.dispose()


@pytest.mark.parametrize('preserved',[False,True])
@pytest.mark.parametrize('failure',[None,'query','initialize'])
def test_capture_legacy_read_only_compatibility_and_owned_close(tmp_path,monkeypatch,preserved,failure):
    from backend.app.services.rag import evaluation as ev, baseline
    from backend.tests.test_rag_evaluation import dataset_one
    folder=tmp_path/'legacy';folder.mkdir();(folder/'chroma.sqlite3').touch()
    data=tmp_path/'dataset.json';st.atomic_json(data,dataset_one().model_dump(mode='json'))
    col=Mock(metadata={'embedding_model':ne.MODEL})
    col.query.return_value={'ids':[['part']], 'distances':[[.1]],'documents':[['합성 본문']],
        'metadatas':[[{'source_code':'KOTRA_MARKET','external_id':'synthetic-1'}]]}
    client=Mock();client.get_collection.return_value=col
    if failure=='initialize':client.get_collection.side_effect=ValueError('synthetic initialization')
    if failure=='query':col.query.side_effect=ValueError('synthetic query')
    monkeypatch.setattr(st,'persistent_client',lambda p:client)
    provider=Mock(return_value=[[1.]+[0.]*3071]);monkeypatch.setattr(ne,'openai_embed',lambda:provider)
    if preserved:
        def search(query,client,**kw):
            # 보존본에서 사용하던 생성 API도 읽기 전용 조회로 연결됩니다.
            client.get_or_create_collection(ne.ARTICLE_COLLECTION)
            return ne.search(query,client=client,**kw)
        legacy=NS(MODEL=ne.MODEL,CHUNK=ne.CHUNK,OVERLAP=ne.OVERLAP,ARTICLE_COLLECTION=ne.ARTICLE_COLLECTION,
                  get_collection=lambda c,n:c.get_or_create_collection(n),search=search)
        monkeypatch.setattr(baseline,'load_preserved_search',lambda path:(legacy,'test-code-hash'))
    args=['capture-legacy','--dataset',str(data),'--chroma-path',str(folder),
          '--configuration-id','fake','--corpus-revision','fixture','--output',str(tmp_path/'run.json')]
    if preserved:args+=['--snapshot',str(tmp_path/'snapshot')]
    if failure=='initialize':
        with pytest.raises(ValueError):ev.main(args)
    else:
        assert ev.main(args)==0
        result=json.loads((tmp_path/'run.json').read_text(encoding='utf-8'))['results'][0]
        assert result['outcome']==('error' if failure else 'recorded')
        provider.close.assert_called_once()
    client.close.assert_called_once();client.get_or_create_collection.assert_not_called()


@pytest.mark.parametrize('sync_failure',[False,True])
@pytest.mark.parametrize('close_failure',[False,True])
def test_baseline_owned_client_closes_borrowed_embed_survives(tmp_path,monkeypatch,sync_failure,close_failure):
    from backend.app.services.rag import baseline as b
    rows=tmp_path/'rows.json';st.atomic_json(rows,[{'title':'합성 제목'}])
    client=Mock();embed=Mock()
    if close_failure:client.close.side_effect=OSError('synthetic cleanup')
    legacy=NS(ARTICLE_COLLECTION='total_article',get_collection=Mock(return_value=Mock()),sync=Mock(return_value={'articles':1}))
    if sync_failure:legacy.sync.side_effect=ValueError('original sync error')
    monkeypatch.setattr(b,'load_preserved_search',lambda p:(legacy,'hash'))
    monkeypatch.setattr(b,'persistent_client',lambda p:client)
    output=tmp_path/'baseline'
    if sync_failure:
        with pytest.raises(ValueError,match='original sync error'):b.rebuild_legacy(tmp_path,rows,output,embed)
    else:
        result=b.rebuild_legacy(tmp_path,rows,output,embed)
        assert result['status']==('partial' if close_failure else 'complete')
    result=json.loads((output/'reconstruction.json').read_text(encoding='utf-8'))
    assert bool(result.get('cleanup_errors'))==close_failure
    if sync_failure:assert result['error_code']=='ValueError'
    client.close.assert_called_once();embed.close.assert_not_called()


@pytest.mark.parametrize('failure',[False,True])
def test_baseline_cli_closes_owned_embed_even_on_failure(tmp_path,monkeypatch,failure):
    import sys
    from backend.app.services.rag import baseline as b
    provider=Mock();monkeypatch.setattr(ne,'openai_embed',lambda:provider)
    action=Mock(return_value={'status':'complete'})
    if failure:
        action.side_effect=ValueError('original error');provider.close.side_effect=OSError('close error')
    monkeypatch.setattr(b,'rebuild_legacy',action)
    monkeypatch.setattr(sys,'argv',['baseline','--snapshot',str(tmp_path),'--rows',str(tmp_path/'rows.json'),'--output',str(tmp_path/'out')])
    if failure:
        with pytest.raises(ValueError,match='original error'):b.main()
    else:assert b.main()==0
    provider.close.assert_called_once()


def test_rag_utf8_read_contract_and_korean_cache_under_cp949_default(tmp_path,monkeypatch):
    import ast,io,sys
    from backend.app.services.rag import service,evaluate_service,baseline,review,evaluation_drafts
    from backend.tests.test_rag_pipeline import document,vectors,FakeGateway
    from backend.tests.test_rag_evaluation import dataset_one
    original=io.open
    def cp949_default(file,mode='r',buffering=-1,encoding=None,*args,**kwargs):
        if 'b' not in mode and encoding is None:encoding='cp949'
        return original(file,mode,buffering,encoding,*args,**kwargs)
    for module in (service,evaluate_service,baseline,review,evaluation_drafts):
        code=ast.parse(Path(module.__file__).read_text(encoding='utf-8'))
        for node in ast.walk(code):
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='read_text':
                assert any(k.arg=='encoding' and isinstance(k.value,ast.Constant) and k.value.value=='utf-8' for k in node.keywords),module.__name__
    monkeypatch.setattr(io,'open',cp949_default)
    store=st.GenerationStore(tmp_path/'utf8',dimensions=8)
    store.publish([document()],ALL_SOURCES,vectors,vocabulary={'products':{},'countries':{'US':'미국'},'synonyms':{},'hypernyms':{},'categories':{}})
    gateway=FakeGateway();svc=service.RagService(store,None,gateway,cache_dir=tmp_path/'cache')
    for _ in range(2):assert svc.run(request_payload(),ExecutionContext(time.monotonic()+30)).returned_count==1
    assert len(gateway.summary_calls)==1
    data=tmp_path/'dataset.json';st.atomic_json(data,dataset_one().model_dump(mode='json'))
    corpus=tmp_path/'corpus.jsonl';corpus.write_text(json.dumps({'source_code':'KOTRA_MARKET','external_id':'synthetic-1','body':'합성 한글 본문','published_date':'2026-09-15','countries':['US']},ensure_ascii=False)+'\n',encoding='utf-8')
    template=review.prepare_review(data,corpus,tmp_path/'review.json')
    row=template['cases'][0];row.update(decision='approved',conditions_confirmed=True,evidence=[{'source_code':'KOTRA_MARKET','external_id':'synthetic-1','quote':'합성 한글','reason':'합성 검수'}])
    template.update(reviewer='시험 검수자',review_reference='가상 검수');st.atomic_json(tmp_path/'review.json',template)
    assert review.finalize_review(data,tmp_path/'review.json',corpus,tmp_path/'final').reviewed
    captured=[]
    monkeypatch.setattr(evaluation_drafts,'prepare_drafts',lambda rows,path:captured.extend(rows) or {})
    monkeypatch.setattr(sys,'argv',['drafts','--articles',str(corpus),'--output',str(tmp_path/'drafts')])
    evaluation_drafts.main();assert captured[0]['body']=='합성 한글 본문'


def test_rag_publish_keeps_active_and_reports_owned_embed_cleanup(context,monkeypatch):
    from backend.app.db import session
    db,root,embed,_=context
    provider=Mock(side_effect=embed);provider.close.side_effect=RuntimeError('close failure')
    monkeypatch.setattr(ne,'openai_embed',lambda:provider)
    monkeypatch.setattr(session,'make_engine',lambda url:db)
    result=rag_index.run(ALL_SOURCES,root=root,raw_market=False)
    assert result['status']=='partial' and result['index']['status']=='ready'
    assert result['index']['generation']==json.loads((root/'active.json').read_text(encoding='utf-8'))['generation']
    assert result['cleanup_errors']==[{'resource':'embedding','error_type':'RuntimeError'}]
    saved=[json.loads(p.read_text(encoding='utf-8')) for p in (root/'runs').glob('*.json')]
    assert saved==[result];provider.close.assert_called_once()
    with closing(st.GenerationStore(root).snapshot().client):pass


def test_rag_failed_embedding_preserves_original_error_and_cleanup(context,monkeypatch):
    from backend.app.db import session
    db,root,_,_=context
    provider=Mock(side_effect=ValueError('original embedding failure'))
    provider.close.side_effect=OSError('secondary cleanup failure')
    monkeypatch.setattr(ne,'openai_embed',lambda:provider)
    monkeypatch.setattr(session,'make_engine',lambda url:db)
    result=rag_index.run(ALL_SOURCES,root=root,raw_market=False)
    assert result['status']=='failed' and result['error_code']=='ValueError'
    assert result['cleanup_errors']==[{'resource':'embedding','error_type':'OSError'}]
    assert not (root/'active.json').exists()
    provider.close.assert_called_once()
    assert json.loads(next((root/'runs').glob('*.json')).read_text(encoding='utf-8'))==result


@pytest.mark.parametrize('failure',[False,True])
def test_capture_close_failure_preserves_saved_result_or_original_error(tmp_path,monkeypatch,failure):
    from backend.app.services.rag import evaluation as ev
    from backend.tests.test_rag_evaluation import dataset_one
    folder=tmp_path/'legacy';folder.mkdir();(folder/'chroma.sqlite3').touch()
    data=tmp_path/'dataset.json';st.atomic_json(data,dataset_one().model_dump(mode='json'))
    client=Mock();client.close.side_effect=OSError('close failure')
    client.get_collection.return_value.metadata={'embedding_model':ne.MODEL}
    if failure:client.get_collection.side_effect=ValueError('original initialization failure')
    monkeypatch.setattr(st,'persistent_client',lambda p:client)
    monkeypatch.setattr(ne,'search',lambda *a,**k:[])
    args=['capture-legacy','--dataset',str(data),'--chroma-path',str(folder),
          '--configuration-id','fake','--corpus-revision','fixture','--output',str(tmp_path/'run.json')]
    with pytest.raises(ValueError if failure else OSError):ev.main(args)
    client.close.assert_called_once()
    if not failure:assert json.loads((tmp_path/'run.json').read_text(encoding='utf-8'))['results'][0]['outcome']=='recorded'


@pytest.mark.parametrize('refresh_failure',[False,True])
@pytest.mark.parametrize('write_failure',[False,True])
def test_rag_dispose_and_report_failure_do_not_mask_original(tmp_path,monkeypatch,refresh_failure,write_failure):
    from backend.app.db import session
    db=NS(dispose=Mock(side_effect=OSError('cleanup failure')))
    monkeypatch.setattr(session,'make_engine',lambda url:db)
    def refresh(*a,**kw):
        if refresh_failure:
            error=ValueError('publish failure');error.cleanup_errors=[{'resource':'embedding','error_type':'RuntimeError'}];raise error
        return {'status':'ready','generation':'synthetic'}
    monkeypatch.setattr(rag_index,'refresh',refresh)
    if write_failure:monkeypatch.setattr(rag_index,'atomic_json',Mock(side_effect=PermissionError('write failure')))
    result=rag_index.run(root=tmp_path/'index')
    assert result['status']==('failed' if refresh_failure else 'partial')
    assert result['error_code']==('ValueError' if refresh_failure else 'resource_cleanup_failed')
    assert result['cleanup_errors'][-1]=={'resource':'database','error_type':'OSError'}
    if write_failure:assert result['report_error']=='PermissionError'
    else:assert json.loads(next((tmp_path/'index/runs').glob('*.json')).read_text(encoding='utf-8'))==result
    db.dispose.assert_called_once()


@pytest.mark.parametrize('published',[True,False])
def test_default_embed_runs_keywords_after_published_cleanup_failure(monkeypatch,published,tmp_path):
    report={'status':'partial' if published else 'failed','error_code':'resource_cleanup_failed'}
    if published:report['index']={'status':'ready','generation':'synthetic'}
    monkeypatch.setattr(rag_index,'run',lambda *a,**k:report)
    keywords=Mock(return_value=0);monkeypatch.setattr(ne,'run',keywords)
    assert ne._run(log_dir=tmp_path,embed=Mock())==1
    assert keywords.call_count==int(published)
    if published:assert keywords.call_args.kwargs['keywords_only'] is True
