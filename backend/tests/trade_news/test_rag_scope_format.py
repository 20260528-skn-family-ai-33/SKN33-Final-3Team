"""요청 출처·본문 포맷·active 경로 계약: 합성 기사와 임시 Chroma만 사용."""
import json
import sqlite3
import time
import re
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from backend.app.schemas.rag import ALL_SOURCES, ArticleSource as S, RagRequest, RagResponse
from backend.app.services.rag import store as st
from backend.app.services.rag.retrieval import search
from backend.app.services.rag.runtime import ExecutionContext
from backend.app.services.rag.service import RagService
from backend.app.services.rag.source_adapter import sources_for_article_type
from backend.app.services.rag.vocabulary import Vocabulary
from backend.app.scheduler.jobs import news_embed as ne, rag_index
from backend.tests.rag_fixtures import request_payload
from backend.tests.test_rag_pipeline import document, vectors, FakeGateway
from .test_common_news_vector import context


def request(sources='ALL', use_case='local_issue', products=False):
    payload = request_payload()
    payload.update(source_codes=sources, use_case=use_case)
    if products:
        payload['conditions']['products'] = [{'hs_code':'8507','hs_ver':'HS2022'}]
    return RagRequest.model_validate(payload)


def call(store, req, gateway=None):
    return RagService(store, Vocabulary(products={'HS2022:8507':['배터리']}), gateway or FakeGateway()).run(
        req, ExecutionContext(time.monotonic()+30))


@pytest.mark.parametrize('sources', [('KOTRA_SHORT_NEWS',), ('HANKYUNG','FNNEWS'), 'ALL'])
def test_source_scope_filters_before_topk_and_queries_shared_collection_once(tmp_path, sources):
    store = st.GenerationStore(tmp_path/'index', dimensions=8)
    # 타 출처 고득점 문서가 40개 있어도 요청한 출처의 top-k 슬롯을 차지하지 않습니다.
    docs = [document(n, source=S.KOTRA_MARKET) for n in range(1,41)]
    docs += [document(n, source=s) for n,s in enumerate((S.KOTRA_SHORT_NEWS,S.HANKYUNG,S.FNNEWS),41)]
    def embed(texts):
        return [[1.,int(re.search(r'합성 기사 (\d+)',t)[1])/50.,0.,0.,0.,0.,0.,0.] for t in texts]
    store.publish(docs, ALL_SOURCES, embed)
    snapshot = store.snapshot()
    real = snapshot.collection('total_article')
    col = Mock(wraps=real)
    fake_snapshot = Mock(sources=snapshot.sources, dimensions=8)
    fake_snapshot.collection.return_value = col
    gateway = FakeGateway()
    gateway.embed = lambda texts, ctx: [[1.,0.,0.,0.,0.,0.,0.,0.]]
    result = search(request(sources), fake_snapshot, Vocabulary(), gateway, ExecutionContext(time.monotonic()+30))
    assert not result.failures
    expected = set(ALL_SOURCES) if sources == 'ALL' else {S(s) for s in sources}
    assert {c.article.source_code for c in result.candidates} <= expected
    assert len(result.candidates) == (5 if sources == 'ALL' else len(sources))
    assert col.query.call_count == 1 and fake_snapshot.collection.call_count == 1
    where = col.query.call_args.kwargs['where']['$and']
    assert next(c['source_code']['$in'] for c in where if 'source_code' in c)
    assert set(next(c['source_code']['$in'] for c in where if 'source_code' in c)) == {s.value for s in expected}


def test_all_uses_ready_sources_explicit_missing_is_failure_and_response_scope_validated(tmp_path):
    store = st.GenerationStore(tmp_path/'index', dimensions=8)
    store.publish([document(2)], [S.KOTRA_SHORT_NEWS], vectors)
    result = call(store, request())
    assert result.applied_source_codes == (S.KOTRA_SHORT_NEWS,)
    assert result.status == 'insufficient_results' and not result.failures
    missing = call(store, request(('KOTRA_SHORT_NEWS','FNNEWS')))
    assert missing.status == 'partial' and missing.failures[0].code == 'source_not_indexed'
    payload = result.model_dump()
    payload.update(requested_source_codes=('FNNEWS',), applied_source_codes=('FNNEWS',))
    with pytest.raises(ValidationError): RagResponse.model_validate(payload)


def test_tie_expansion_keeps_source_filter_bounds_and_unique_articles(tmp_path):
    store = st.GenerationStore(tmp_path/'index', dimensions=8)
    docs=[document(n,source=S.KOTRA_SHORT_NEWS) for n in range(1,41)]
    docs += [document(n,source=S.HANKYUNG) for n in range(41,61)]
    same=lambda texts:[[1.,0.,0.,0.,0.,0.,0.,0.] for t in texts]
    store.publish(docs,ALL_SOURCES,same)
    snapshot=store.snapshot(); col=Mock(wraps=snapshot.collection('total_article'))
    fake=Mock(sources=snapshot.sources,dimensions=8); fake.collection.return_value=col
    gateway=FakeGateway(); gateway.embed=lambda texts,ctx:same(texts)
    result=search(request(('KOTRA_SHORT_NEWS',)),fake,Vocabulary(),gateway,ExecutionContext(time.monotonic()+30))
    assert not result.failures and len(result.candidates)==5
    assert len({c.article.key for c in result.candidates})==5
    assert fake.collection.call_count==1 and 1<col.query.call_count<=3  # 15→30→60
    for query in col.query.call_args_list:
        clause=next(c['source_code'] for c in query.kwargs['where']['$and'] if 'source_code' in c)
        assert clause=={'$in':['KOTRA_SHORT_NEWS']}
    assert all(c.article.source_code==S.KOTRA_SHORT_NEWS for c in result.candidates)


@pytest.mark.parametrize('scope', [[], ['BREAKING'], ['KOTRA_SHORT_NEWS','KOTRA_SHORT_NEWS']])
def test_invalid_source_scope_stops_at_request_boundary(scope):
    with pytest.raises(ValidationError): request(scope)
    assert sources_for_article_type('BREAKING') == ('KOTRA_SHORT_NEWS',)


@pytest.mark.parametrize('use_case', ['chat','local_issue'])
def test_hsless_short_relevant_retained_unrelated_excluded_and_blocked_not_sent(tmp_path, use_case):
    store = st.GenerationStore(tmp_path/'index', dimensions=8)
    docs = [document(1,source=S.KOTRA_SHORT_NEWS, body='배터리 수출 계약 본문'),
            document(2,source=S.KOTRA_SHORT_NEWS, body='커피 축제 본문',kw_topic=['커피']),
            document(3,source=S.KOTRA_SHORT_NEWS, body='배터리 차단 본문',kw_topic=['차단']),
            document(4,source=S.KOTRA_MARKET)]
    store.publish(docs, ALL_SOURCES, vectors)
    req = request(use_case=use_case, products=True)
    payload = req.model_dump(); payload['conditions']['blocked_topics'] = ['차단']
    gateway = FakeGateway()
    result = call(store, RagRequest.model_validate(payload), gateway)
    assert [a.external_id for a in result.articles] == ['1']
    assert len(gateway.product_calls) == 2
    assert all('차단 본문' not in c for chunks in gateway.product_calls for c in chunks.values())
    assert gateway.summary_calls == [docs[0].key]


def test_shared_header_chunking_embedding_input_and_metadata_reuse(tmp_path):
    store = st.GenerationStore(tmp_path/'index', dimensions=8)
    body = '본문 가'*350 + '\n' + '본문 나'*350 + '\n마지막 문단'
    doc = document(2, body=body, category='원천분류', countries=['US','JP'])
    row = dict(article_id=doc.article_id, external_id=doc.external_id, source_code=doc.source_code.value,
               title=doc.title, published_date=doc.published_date, category=doc.category,
               body_masked=doc.body, countries=list(doc.countries), keywords=[])
    expected = [text for text, meta in ne.build_docs(row).values()]
    calls = []
    def embed(texts): calls.extend(texts); return vectors(texts)
    first = store.publish([doc], [S.KOTRA_SHORT_NEWS], embed)
    rows = store.snapshot().collection('total_article').get(include=['documents','metadatas'])
    actual = [d for d,m in sorted(zip(rows['documents'],rows['metadatas']),key=lambda x:x[1]['chunk'])]
    assert actual == expected == calls
    assert all(t.startswith('[2026-09-15 | US,JP | 원천분류] 합성 기사 2\n') for t in actual)
    assert len(actual) > 1 and (ne.CHUNK,ne.OVERLAP) == (1000,150)
    calls.clear()
    metadata_only = doc.model_copy(update={'kw_topic':('새대표어',), 'source_url':'https://example.org/changed'})
    second = store.publish([metadata_only], [S.KOTRA_SHORT_NEWS], embed)
    assert not calls and second['embedded']==0 and second['reused']==first['chunks']
    for change in ({'category':'변경분류'}, {'countries':('KR',)}, {'published_date':doc.published_date.replace(day=16)}, {'title':'새 제목'}):
        calls.clear()
        result = store.publish([metadata_only.model_copy(update=change)], [S.KOTRA_SHORT_NEWS], embed)
        assert result['embedded'] == len(actual) == len(calls)


@pytest.mark.parametrize('legacy', ['title_only','three_collections'])
def test_old_format_or_collection_layout_fails_closed_without_embedding(tmp_path, legacy):
    store = st.GenerationStore(tmp_path/'index', dimensions=8)
    store.publish([document()], ALL_SOURCES, vectors)
    before = (store.root/'active.json').read_bytes()
    status = store.root/'generations'/store.snapshot().generation/'status.json'
    report = json.loads(status.read_text())
    if legacy == 'title_only': report.pop('document_format')
    else: report['collections'] = {'total_article':1,'hankyung_article':0,'fnnews_article':0}
    status.write_text(json.dumps(report))
    embed = Mock(side_effect=AssertionError('must not reembed'))
    code = 'article_document_migration_required' if legacy=='title_only' else 'single_collection_migration_required'
    with pytest.raises(ValueError,match=code): store.snapshot()
    with pytest.raises(ValueError,match=code): store.publish([document()],ALL_SOURCES,embed)
    with pytest.raises(ValueError,match=code):
        rag_index.refresh(None,ALL_SOURCES,store=store,embed=embed,raw_market=False,dry_run=True)
    assert not embed.called and (store.root/'active.json').read_bytes()==before


def legacy_fixture(path, name):
    path.mkdir()
    with sqlite3.connect(path/'chroma.sqlite3') as db:
        db.execute('CREATE TABLE collections (name TEXT)')
        db.execute('INSERT INTO collections VALUES (?)',(name,))


def test_legacy_direct_requires_migration_no_mutation_no_fallback(tmp_path):
    legacy = tmp_path/'legacy'; legacy_fixture(legacy,'total_article')
    before = (legacy/'chroma.sqlite3').read_bytes()
    store = st.GenerationStore(tmp_path/'new', legacy_path=legacy, dimensions=8)
    embed=Mock(side_effect=AssertionError('no reembedding'))
    with pytest.raises(ValueError,match='legacy_direct_index_migration_required'): store.snapshot()
    with pytest.raises(ValueError,match='legacy_direct_index_migration_required'): store.publish([],ALL_SOURCES,embed)
    assert not store.root.exists() and not embed.called and (legacy/'chroma.sqlite3').read_bytes()==before


def test_keyword_only_legacy_does_not_block_body_store(tmp_path):
    legacy = tmp_path/'legacy'; legacy_fixture(legacy,'news_keyword')
    store=st.GenerationStore(tmp_path/'new',legacy_path=legacy,dimensions=8)
    store.publish([document()],ALL_SOURCES,vectors)
    assert store.snapshot().collection('total_article').count()==1


def test_index_and_search_resolve_same_active_root(context, monkeypatch, tmp_path):
    db, root, embed, calls = context
    monkeypatch.setenv('NEWS_VECTOR_ROOT',str(root))
    monkeypatch.setattr(st,'LEGACY_ROOT',tmp_path/'empty_legacy')
    monkeypatch.setattr(ne,'CHROMA_PATH',tmp_path/'keywords')
    result=ne.run_sources(['HANKYUNG','FNNEWS'],db_url='isolated',embed=embed)
    assert result['status']=='complete',result
    found=ne.search('합성',embed=embed,source_codes=['FNNEWS'])
    assert len(found)==1 and found[0][3]['source_code']=='FNNEWS'
    assert st.GenerationStore().root==root==st.resolve_root(root)
    # 다른 명시 root로 검색하면 기본 root로 조용히 대체하지 않습니다.
    with pytest.raises(FileNotFoundError): ne.search('합성',root=tmp_path/'other',embed=embed)


def test_dry_run_legacy_transition_guard_before_storage_or_provider(context, tmp_path):
    db, root, embed, calls=context
    legacy=tmp_path/'legacy'; legacy_fixture(legacy,'total_article')
    store=st.GenerationStore(root,legacy_path=legacy)
    before=(legacy/'chroma.sqlite3').read_bytes()
    with pytest.raises(ValueError,match='legacy_direct_index_migration_required'):
        rag_index.refresh(db,['HANKYUNG'],store=store,embed=embed,raw_market=False,dry_run=True)
    assert not root.exists() and not calls and before==(legacy/'chroma.sqlite3').read_bytes()
