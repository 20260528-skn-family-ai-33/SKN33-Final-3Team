"""공통 색인 자원 소유권·UTF-8·단신 어댑터 호환 회귀. 임시 자료만 사용."""
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import io
import json

from filelock import FileLock, Timeout
import pytest
from sqlalchemy import insert, select

from backend.app.schemas.rag import ALL_SOURCES, ArticleSource as S
from backend.app.services.rag import store as st
from backend.app.services.rag.ingest import read_articles
from backend.app.services.rag.vocabulary import Vocabulary
from backend.app.scheduler.jobs import news_embed as ne, rag_index
from backend.app.models.news import ArticleTag
from backend.app.models.hs_code import HsCodeMst
from .vector_fixtures import document, vectors
from .test_common_news_vector import context


class OwnedClient:
    def __init__(self, client, fail_close=False):
        self.client, self.fail_close, self.closed = client, fail_close, False
    def __getattr__(self,name):return getattr(self.client,name)
    def close(self):
        self.closed=True
        self.client.close()
        if self.fail_close:raise RuntimeError('synthetic_close_failure')


def test_publish_closes_owned_handles_before_publish_without_closing_external_reader(tmp_path):
    handles=[]
    def factory(path):
        handle=OwnedClient(st.persistent_client(path));handles.append(handle);return handle
    store=st.GenerationStore(tmp_path/'index',dimensions=8,client_factory=factory)
    first=store.publish([document()],ALL_SOURCES,vectors)
    assert handles[0].closed
    reader=store.snapshot()
    reader_client=reader.client
    def verify(stage):
        if stage=='verified':assert all(c.closed for c in handles if c is not reader_client)
    second=store.publish([document(body='새로운 합성 본문')],ALL_SOURCES,vectors,hook=verify)
    assert first['generation']!=second['generation'] and not reader_client.closed
    assert reader.collection('kotra_article').count()==1
    reader_client.close()


@pytest.mark.parametrize('fail_old',[False,True])
def test_close_failure_never_replaces_active_generation(tmp_path,fail_old):
    store=st.GenerationStore(tmp_path/'index',dimensions=8)
    first=store.publish([document()],ALL_SOURCES,vectors)
    active=(store.root/'active.json').read_bytes()
    handles=[]
    def factory(path):
        is_old=first['generation'] in str(path)
        handle=OwnedClient(st.persistent_client(path),fail_close=(is_old==fail_old))
        handles.append(handle);return handle
    store.client_factory=factory
    with pytest.raises(RuntimeError,match='synthetic_close_failure'):
        store.publish([document(body='바뀐 합성 본문')],ALL_SOURCES,vectors)
    assert all(c.closed for c in handles)
    assert (store.root/'active.json').read_bytes()==active
    failed=[json.loads(p.read_text(encoding='utf-8')) for p in (store.root/'generations').glob('*/status.json')]
    assert sum(r['status']=='failed' for r in failed)==1
    store.client_factory=st.persistent_client
    with closing(store.snapshot().client) as client:assert client.get_collection('kotra_article').count()==1


def test_atomic_json_windows_branch_keeps_file_fsync_and_utf8_without_directory_open(tmp_path,monkeypatch):
    original=st.os
    fsync=Mock(wraps=original.fsync)
    no_open=Mock(side_effect=AssertionError('directory open forbidden in Windows branch'))
    monkeypatch.setattr(st,'os',SimpleNamespace(name='nt',fsync=fsync,replace=original.replace,open=no_open))
    path=tmp_path/'상태.json';st.atomic_json(path,{'본문':'합성 기사'})
    assert json.loads(path.read_text(encoding='utf-8'))=={'본문':'합성 기사'}
    assert fsync.call_count==1 and not no_open.called


def test_catalog_pointer_and_status_explicit_utf8(tmp_path,monkeypatch):
    original=io.open;checked=[]
    def guarded(file,*args,**kwargs):
        if isinstance(file,(str,Path)) and Path(file).name in ('active.json','status.json','articles.jsonl','vocabulary.json'):
            mode=args[0] if args else kwargs.get('mode','r')
            if 'b' not in mode:
                encoding=kwargs.get('encoding',args[2] if len(args)>2 else None)
                assert encoding=='utf-8',(file,encoding)
                checked.append(Path(file).name)
        return original(file,*args,**kwargs)
    monkeypatch.setattr(io,'open',guarded)
    store=st.GenerationStore(tmp_path/'index',dimensions=8)
    store.publish([document(title='합성 한글 제목')],ALL_SOURCES,vectors)
    store.publish([document(title='합성 한글 제목')],ALL_SOURCES,vectors)
    with closing(store.snapshot().client):pass
    assert {'active.json','status.json','articles.jsonl'}<=set(checked)


def test_refresh_closes_temporary_read_snapshot(context,monkeypatch):
    db,root,embed,calls=context
    store=st.GenerationStore(root)
    rag_index.refresh(db,ALL_SOURCES,store=store,embed=embed,raw_market=False)
    handles=[]
    def factory(path):
        handle=OwnedClient(st.persistent_client(path));handles.append(handle);return handle
    store.client_factory=factory
    rag_index.refresh(db,ALL_SOURCES,store=store,embed=embed,raw_market=False)
    assert len(handles)==3 and all(c.closed for c in handles)  # 보조정보 읽기 + 이전/새 게시 세대


def test_shortnews_vector_excludes_hs_preserves_sql_manual_tags_and_db_topics(context):
    db,root,embed,calls=context
    with db.begin() as conn:
        for code in ('8507','850760'):
            conn.execute(insert(HsCodeMst),{'hs_code':code,'hs_ver':'HS2022','name_ko':'배터리',
                'source_edition':'synthetic','original_name_ko':'배터리' if len(code)==6 else None})
        for n in range(1,5):
            conn.execute(insert(ArticleTag),{'article_id':n,'hs_code':'850760','hs_ver':'HS2022'})
        before=conn.execute(select(ArticleTag).order_by(ArticleTag.tag_id)).all()
    vocab=Vocabulary(hypernyms={'무역협정':'다른상위어'})
    docs,_=read_articles(db,ALL_SOURCES,vocabulary=vocab)
    for doc in docs:
        if doc.source_code==S.KOTRA_SHORT_NEWS:
            assert not doc.products and doc.kw_topic==('무역협정',)
        else:
            assert {p.hs_code for p in doc.products}=={'8507','850760'}
            assert doc.kw_topic==(('다른상위어',) if doc.source_code==S.KOTRA_MARKET else ('무역협정',))
    with db.connect() as conn:assert conn.execute(select(ArticleTag).order_by(ArticleTag.tag_id)).all()==before


def test_keyword_owned_client_closed_and_close_failure_becomes_partial(context,monkeypatch):
    db,root,embed,calls=context
    original=st.persistent_client;handles=[]
    def factory(path):
        c=OwnedClient(original(path),fail_close=True);handles.append(c);return c
    monkeypatch.setattr(st,'persistent_client',factory)
    result=ne.run_sources(['HANKYUNG','FNNEWS'],db_url='isolated',root=root,embed=embed)
    assert result['status']=='partial' and result['body']['status']=='complete'
    assert result['keywords']['status']=='failed' and handles[0].closed
    assert (root/'active.json').exists()


@pytest.mark.parametrize('entry',['sync_all_keywords','legacy_run'])
def test_keyword_writers_use_same_lock_and_preserve_borrowed_client(context,monkeypatch,entry):
    db,root,embed,calls=context
    path=root/'keyword-store';lock_path=str(path)+'.lock'
    original=ne.keyword_article_counts
    def counts(*args,**kwargs):
        with pytest.raises(Timeout):
            with FileLock(lock_path,timeout=0):pass
        return original(*args,**kwargs)
    monkeypatch.setattr(ne,'keyword_article_counts',counts)
    with closing(st.persistent_client(path)) as borrowed:
        if entry=='sync_all_keywords':ne.sync_all_keywords(db,path,embed)
        else:assert ne.run(db_url='isolated',client=borrowed,embed=embed,keywords_only=True,log_dir=root/'logs')==0
        assert borrowed.get_collection(ne.KEYWORD_COLLECTION).count()==1


@pytest.mark.parametrize('stage',['initialize','close'])
def test_public_run_initialization_and_close_failure_returns_one(context,monkeypatch,stage):
    db,root,embed,calls=context
    if stage=='initialize':
        borrowed=Mock();borrowed.get_settings.side_effect=RuntimeError('synthetic_initialization_failure')
        assert ne.run(client=borrowed,db_url='isolated',embed=embed,log_dir=root/'logs')==1
        borrowed.close.assert_not_called()
    else:
        original=st.persistent_client
        monkeypatch.setenv('NEWS_VECTOR_ROOT', str(root))
        handles=[]
        def factory(path):
            assert Path(path)==root/'keywords'
            handle=OwnedClient(original(path),fail_close=True)
            handles.append(handle)
            return handle
        monkeypatch.setattr(st,'persistent_client',factory)
        assert ne.run(db_url='isolated',keywords_only=True,embed=embed,log_dir=root/'logs')==1
        assert len(handles)==1 and handles[0].closed
