"""격리 SQLite/Chroma와 fake embed만 사용하는 공통 색인 계약 회귀."""
import json
from pathlib import Path
from unittest.mock import Mock
from datetime import date

import pytest
from sqlalchemy import create_engine, insert, text
from backend.app.models.base import Base
from backend.app.models.news import Article, ArticleTag, COUNTRY
from backend.app.models.hs_code import HsCodeMst
from backend.app.schemas.rag import ALL_SOURCES
from backend.app.scheduler.jobs import news_embed as ne, rag_index
from backend.app.services.rag.store import GenerationStore
from backend.app.services.rag.vocabulary import Vocabulary
from backend.app.services.news_keywords.keyword_clean import Categories
from .vector_fixtures import document
from .test_daily_scheduler import local
from news.project_tags import read_project_dictionary, tag_for_load
from .helpers import prepared


@pytest.fixture
def context(tmp_path, monkeypatch):
    db = create_engine('sqlite:///' + str(tmp_path/'test.sqlite3'))
    Base.metadata.create_all(db)
    with db.begin() as conn:
        conn.execute(insert(COUNTRY), {'country_code':'US'})
        for n, source in enumerate(ALL_SOURCES, 1):
            conn.execute(insert(Article), {'article_id':n, 'source_code':source.value, 'external_id':'same-id',
                'title':f'합성 제목 {n}', 'body_masked':f'합성 본문 {n}', 'source_url':f'https://example.test/{n}',
                'published_date':date(2026,10,24)})
            conn.execute(insert(ArticleTag), {'article_id':n, 'country_code':'US'})
            conn.execute(insert(ArticleTag), {'article_id':n, 'keyword_text':'무역협정'})
    # 기본 원본 탐색도 합성 시험 폴더에 한정합니다. 운영 CSV는 읽지 않습니다.
    from backend.app.scheduler.jobs import news_load
    monkeypatch.setattr(news_load, 'RAW_DIR', tmp_path/'no_raw')
    monkeypatch.setattr(ne, 'make_engine', lambda url: db)
    vocab = Vocabulary(categories=Categories(rules=False))
    monkeypatch.setattr(rag_index.Vocabulary, 'load', lambda: vocab)
    monkeypatch.setattr(ne, 'openai_embed', Mock(side_effect=AssertionError('paid API forbidden')))
    calls=[]
    def embed(texts):
        calls.extend(texts)
        embed.usage['tokens'] += len(texts)*3
        return [[1.0]+[0.0]*3071 for _ in texts]
    embed.usage={'tokens':0}
    yield db, tmp_path/'index', embed, calls
    db.dispose()


def run(ctx, sources=('HANKYUNG','FNNEWS'), **kw):
    db, root, embed, calls=ctx
    return ne.run_sources(sources, db_url='isolated', root=root, embed=embed, **kw)


def test_single_collection_scope_metadata_reuse_and_document_equals_input(context):
    db, root, embed, calls=context
    first=run(context, ALL_SOURCES)
    assert first['status']=='complete', first
    before=GenerationStore(root).snapshot()
    rows=before.collection('total_article').get(include=['documents','metadatas'])
    preserved={i:(d,m) for i,d,m in zip(rows['ids'],rows['documents'],rows['metadatas']) if m['source_code'].startswith('KOTRA')}
    assert len(rows['ids'])==4 and len(set(rows['ids']))==4
    assert all(d in calls for d,m in zip(rows['documents'],rows['metadatas']) if m['record_type']=='body')
    assert first['body']['collections']=={'total_article':4}
    calls.clear()
    with db.begin() as conn:
        conn.execute(insert(ArticleTag), {'article_id':3,'keyword_text':'새 대표어'})
    second=run(context)
    assert second['status']=='complete' and second['body']['embedded']==0
    assert calls==['새 대표어']  # 본문 메타데이터 변경은 재임베딩하지 않음.
    got=GenerationStore(root).snapshot().collection('total_article').get(include=['documents','metadatas'])
    assert {i:(d,m) for i,d,m in zip(got['ids'],got['documents'],got['metadatas']) if m['source_code'].startswith('KOTRA')}==preserved
    assert second['keywords']['scope']=='all_database_sources'
    assert second['usage']['keywords']['tokens']==3


def test_hidden_deleted_source_scoped_removal_and_failure_preserve_pointer(context):
    db, root, embed, calls=context
    assert run(context,ALL_SOURCES)['status']=='complete'
    with db.begin() as conn:
        conn.execute(text("ALTER TABLE article ADD COLUMN visibility_status TEXT DEFAULT 'VISIBLE'"))
        conn.execute(text("UPDATE article SET visibility_status='HIDDEN' WHERE article_id=3"))
        conn.execute(text('DELETE FROM article_tag WHERE article_id=4'))
        conn.execute(text('DELETE FROM article WHERE article_id=4'))
    result=run(context)
    assert result['status']=='complete'
    rows=GenerationStore(root).snapshot().collection('total_article').get(include=['metadatas'])
    assert {m['source_code'] for m in rows['metadatas']}=={'KOTRA_MARKET','KOTRA_SHORT_NEWS'}
    before=(root/'active.json').read_bytes()
    with db.begin() as conn:
        conn.execute(text("UPDATE article SET visibility_status='VISIBLE',body_masked='changed' WHERE article_id=3"))
    result=ne.run_sources(['HANKYUNG'], db_url='isolated', root=root, embed=Mock(side_effect=RuntimeError('fake')))
    assert result['status']=='failed' and (root/'active.json').read_bytes()==before


def test_partial_chunks_repaired_without_other_source_changes(context):
    assert run(context)['status']=='complete'
    db, root, embed, calls=context
    col=GenerationStore(root).snapshot().collection('total_article')
    ids=col.get(where={'source_code':'HANKYUNG'})['ids']; col.delete(ids=ids)
    result=run(context)
    assert result['body']['embedded']==1 and result['status']=='complete'


def test_dry_run_no_storage_or_paid_calls_even_existing_generation(context,monkeypatch):
    db, root, embed, calls=context
    result=run(context,dry_run=True)
    assert result['status']=='planned' and not root.exists() and not calls
    assert run(context)['status']=='complete'
    before={str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    monkeypatch.setattr(GenerationStore,'snapshot',Mock(side_effect=AssertionError('no Chroma open in dry-run')))
    calls.clear()
    assert run(context,dry_run=True)['status']=='planned'
    assert before=={str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()} and not calls


def test_keyword_failure_partial_not_empty_or_success(context,monkeypatch):
    monkeypatch.setattr(ne,'keyword_article_counts',Mock(side_effect=RuntimeError('query failed')))
    result=run(context)
    assert result['status']=='partial' and result['body']['status']=='complete'
    assert result['keywords']['status']=='failed' and not (context[1]/'keywords').exists()


def test_missing_key_only_needed_for_actual_embedding(context,monkeypatch):
    db, root, embed, calls=context
    monkeypatch.setattr(ne,'openai_embed',Mock(side_effect=ne.MissingEmbeddingKey('missing')))
    result=ne.run_sources(['HANKYUNG'],db_url='isolated',root=root)
    assert result['status']=='failed' and result['error_code']=='MissingEmbeddingKey'
    assert not (root/'active.json').exists()


@pytest.mark.parametrize('sources',[[],['BAD'],['BREAKING']])
def test_invalid_scope_rejected_before_storage(context,sources):
    with pytest.raises(ValueError):run(context,sources)
    assert not context[1].exists()


def test_database_failure_does_not_publish(context,monkeypatch):
    monkeypatch.setattr(ne,'make_engine',Mock(side_effect=RuntimeError('db unavailable')))
    result=run(context)
    assert result['status']=='failed' and result['keywords']['status']=='not_started'
    assert not (context[1]/'active.json').exists()


def test_final_keywords_and_article_date_hs(local):
    with local.db.begin() as c:
        c.execute(text("INSERT INTO hs_code VALUES ('854200','HS2017','이전 판본')"))
    with local.db.connect() as c:dictionary=read_project_dictionary(c)
    p=prepared(); p['body_masked']='자유무역협정과 농산물 수출. HS 854200이다.'
    tags=tag_for_load(p,dictionary)['tagging']['tags']
    assert {t['keyword_text'] for t in tags if t['kind']=='keyword'}=={'무역협정','농업','수출'}
    assert [(t['hs_code'],t['hs_ver']) for t in tags if t['kind']=='hs']==[('854200','HS2022')]
    p['published_date']='2020-01-01'
    assert [t['hs_ver'] for t in tag_for_load(p,dictionary)['tagging']['tags'] if t['kind']=='hs']==['HS2017']


@pytest.mark.parametrize('canonical',['missing','자유무역협정'])
def test_broken_synonym_dictionary_rejected(local,canonical):
    with local.db.begin() as c:c.execute(text("UPDATE keyword_topic SET canonical_text=:v WHERE keyword_text='자유무역협정'"),{'v':canonical})
    with local.db.connect() as c, pytest.raises(ValueError):read_project_dictionary(c)


def test_zero_selected_preserves_other_sources_and_empty_keywords(context):
    db,root,embed,calls=context
    assert run(context,ALL_SOURCES)['status']=='complete'
    with db.begin() as conn:
        conn.execute(text("DELETE FROM article_tag WHERE article_id IN (3,4)"))
        conn.execute(text("DELETE FROM article WHERE article_id IN (3,4)"))
    result=run(context)
    assert result['status']=='complete' and result['body']['selected_articles']==0
    assert result['body']['total_articles']==2
    assert result['keywords']['distinct_keywords']==1
    with db.begin() as conn:conn.execute(text('DELETE FROM article_tag'))
    result=run(context)
    assert result['keywords']['empty_policy']=='preserve_existing' and result['keywords']['deleted']==0


def test_existing_keyword_entrypoint_uses_same_locked_helper(context,monkeypatch):
    db,root,embed,calls=context
    helper=Mock(return_value={'status':'complete'})
    monkeypatch.setattr(ne,'sync_all_keywords',helper)
    monkeypatch.setenv('NEWS_VECTOR_ROOT',str(root))
    assert ne.run(db_url='isolated',keywords_only=True,embed=embed)==0
    assert helper.call_args.args[:2]==(db,root/'keywords')


def test_concurrent_source_calls_serialize_complete_scope(context,monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    db,root,embed,calls=context
    entered=threading.Event(); release=threading.Event(); names=[]
    original=rag_index.refresh
    def controlled(*args,**kw):
        names.append(tuple(str(x.value) for x in args[1]))
        if len(names)==1:
            entered.set()
            assert release.wait(5)
        return original(*args,**kw)
    monkeypatch.setattr(rag_index,'refresh',controlled)
    with ThreadPoolExecutor(2) as pool:
        a=pool.submit(run,context,['HANKYUNG'])
        assert entered.wait(5)
        b=pool.submit(run,context,['FNNEWS'])
        assert not b.done() and len(names)==1
        release.set()
        assert a.result(20)['status']==b.result(20)['status']=='complete'
    col=GenerationStore(root).snapshot().collection('total_article')
    assert {m['source_code'] for m in col.get(include=['metadatas'])['metadatas']}=={'HANKYUNG','FNNEWS'}


def test_source_views_keep_market_screen_and_distinct_integrated_counts(context):
    import re
    db,root,embed,calls=context
    sql=(Path(__file__).resolve().parents[3]/'db/mysql/init/01_schema.sql').read_text()
    with db.begin() as conn:
        for statement in re.findall(r'CREATE VIEW v_article_.*?;',sql):conn.execute(text(statement))
        assert conn.execute(text('SELECT article_count FROM v_article_day')).scalar()==1
        assert conn.execute(text('SELECT article_count FROM v_article_integrated_day')).scalar()==2
        assert conn.execute(text('SELECT article_count FROM v_article_integrated_country_day')).scalar()==2
        assert dict(conn.execute(text('SELECT source_code,article_count FROM v_article_source_day')).all())=={s.value:1 for s in ALL_SOURCES}


def test_breaking_alias_is_only_api_adapter():
    from backend.app.services.rag.source_adapter import normalize_article_type
    assert normalize_article_type('BREAKING')=='SHORT_NEWS'
    assert 'KOTRA_SHORT_NEWS' in [s.value for s in ALL_SOURCES]


def test_followup_view_migration_leaves_old_admin_contract_unchanged(context):
    import re
    db,root,embed,calls=context
    base=Path(__file__).resolve().parents[3]
    sql=(base/'db/mysql/migrations/20261025_news_source_views.sql').read_text()
    with db.begin() as conn:
        conn.execute(text("ALTER TABLE article ADD COLUMN visibility_status TEXT DEFAULT 'VISIBLE'"))
        for statement in re.findall(r'CREATE OR REPLACE VIEW v_article_.*?;',sql):
            conn.execute(text(statement.replace('CREATE OR REPLACE VIEW','CREATE VIEW')))
        assert conn.execute(text('SELECT article_count FROM v_article_day')).scalar()==1
        assert conn.execute(text('SELECT article_count FROM v_article_integrated_day')).scalar()==2
        conn.execute(text("UPDATE article SET visibility_status='HIDDEN' WHERE article_id=1"))
        assert conn.execute(text('SELECT COUNT(*) FROM v_article_day')).scalar()==0


def test_legacy_keyword_rebuild_forwarded_and_dry_run_never_opens_collection(context,monkeypatch):
    db,root,embed,calls=context
    monkeypatch.setenv('NEWS_VECTOR_ROOT',str(root))
    helper=Mock(return_value={'status':'complete'})
    monkeypatch.setattr(ne,'sync_all_keywords',helper)
    assert ne.run(db_url='isolated',keywords_only=True,rebuild=True,embed=embed)==0
    assert helper.call_args.kwargs['rebuild'] is True
    assert ne.run(db_url='isolated',keywords_only=True,rebuild=True,dry_run=True,embed=embed)==0
    assert helper.call_args.kwargs['dry_run'] is True


def test_collector_imports_without_repository_pythonpath(tmp_path):
    import os,sys,subprocess
    collector=Path(__file__).resolve().parents[3]/'backend/app/scheduler/collectors/trade_news'
    code="import sys;sys.path.insert(0,sys.argv[1]);from news.preparation import prepare_article;from backend.app.services.news_keywords.rule_data import GEO_DICT;assert GEO_DICT"
    result=subprocess.run([sys.executable,'-I','-c',code,str(collector)],cwd=tmp_path,capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stderr


def test_missing_key_never_constructs_paid_client(monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    monkeypatch.setattr(ne,'dotenv_values',lambda _: {})
    client=Mock(side_effect=AssertionError('paid client forbidden'))
    monkeypatch.setattr(ne,'OpenAI',client)
    with pytest.raises(ne.MissingEmbeddingKey):ne.openai_embed('synthetic-unused-path')
    client.assert_not_called()
