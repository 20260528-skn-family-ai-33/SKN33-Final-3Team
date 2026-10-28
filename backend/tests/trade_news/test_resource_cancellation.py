"""가짜 API·시험 SQLite만 사용한 timeout/cancel 자원 소유권 회귀."""
import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from backend.app.schemas.rag import ALL_SOURCES
from backend.app.scheduler.jobs import news_embed as ne
from backend.app.services.rag import store as st, service, evaluate_service
from backend.app.services.rag.gateway import ModelGateway
from backend.app.services.rag.runtime import ExecutionContext, RagDeadlineExceeded
from backend.app.services.rag.vocabulary import Vocabulary
from backend.app.services.news_keywords.keyword_clean import Categories
from backend.tests.rag_fixtures import request_payload, configuration_payload
from backend.tests.test_rag_evaluation import dataset_one
from backend.app.services.rag.evaluation import RunConfiguration
from .test_common_news_vector import context


@pytest.mark.parametrize('stage',['body','keywords'])
@pytest.mark.parametrize('owned',[True,False])
@pytest.mark.parametrize('error',[TimeoutError,asyncio.CancelledError])
def test_sdk_provider_ownership_on_timeout_or_cancel(context,monkeypatch,stage,owned,error):
    db,root,_,_=context
    api=Mock();factory=Mock(return_value=api)
    monkeypatch.setattr(ne,'OpenAI',factory)
    monkeypatch.setenv('OPENAI_API_KEY','synthetic-not-a-real-key')
    # context 기본 방어를 시험용 SDK 생성기로만 교체합니다. 실제 SDK는 생성하지 않습니다.
    implementation=__import__('backend.app.scheduler.jobs.news_embed',fromlist=['openai_embed'])
    from importlib.util import spec_from_file_location,module_from_spec
    spec=spec_from_file_location('isolated_embed_source',implementation.__file__)
    source=module_from_spec(spec);spec.loader.exec_module(source)
    monkeypatch.setattr(source,'OpenAI',factory)
    monkeypatch.setattr(ne,'openai_embed',source.openai_embed)
    calls=[]
    def create(**kwargs):
        calls.append(kwargs)
        if stage=='body' or len(calls)==2:raise error('synthetic interruption')
        return NS(usage=NS(total_tokens=3,prompt_tokens=3,completion_tokens=0),
                  data=[NS(index=i,embedding=[1.]+[0.]*3071) for i,_ in enumerate(kwargs['input'])])
    api.embeddings.create.side_effect=create
    provider=None if owned else ne.openai_embed()
    disposed=Mock(wraps=db.dispose);monkeypatch.setattr(db,'dispose',disposed)
    try:
        if error is asyncio.CancelledError:
            with pytest.raises(asyncio.CancelledError):ne.run_sources(['HANKYUNG'],root=root,embed=provider)
        else:
            result=ne.run_sources(['HANKYUNG'],root=root,embed=provider)
            assert result['status']==('failed' if stage=='body' else 'partial')
            assert result[stage]['error_code']=='TimeoutError'
        assert len(calls)==(1 if stage=='body' else 2)
        assert api.close.call_count==int(owned)
        disposed.assert_called_once()
        factory.assert_called_once_with(api_key='synthetic-not-a-real-key',max_retries=0)
    finally:
        if provider is not None:provider.close()


@pytest.mark.parametrize('kind',['article','keyword'])
@pytest.mark.parametrize('borrowed_client',[False,True])
@pytest.mark.parametrize('borrowed_provider',[False,True])
@pytest.mark.parametrize('close_fails',[False,True])
@pytest.mark.parametrize('error',[TimeoutError,asyncio.CancelledError])
def test_public_search_preserves_interruption_and_closes_only_owned(tmp_path,monkeypatch,error,borrowed_client,borrowed_provider,close_fails,kind):
    primary=error('original interruption');provider=Mock(side_effect=primary)
    client=Mock();col=client.get_collection.return_value
    col.metadata={'embedding_model':ne.MODEL,'dimensions':ne.DIMENSIONS}
    col.get_model.return_value=NS(dimension=ne.DIMENSIONS)
    col.get.return_value={'ids':['AI'],'embeddings':[[1.0]+[0.0]*(ne.DIMENSIONS-1)]}
    if close_fails:
        client.close.side_effect=OSError('secondary client close')
        provider.close.side_effect=OSError('secondary provider close')
    snapshot=NS(client=client,sources=set(ALL_SOURCES),collection=client.get_collection)
    monkeypatch.setattr(st,'GenerationStore',lambda *a,**kw:NS(snapshot=lambda:snapshot))
    monkeypatch.setattr(ne,'openai_embed',lambda:provider)
    (tmp_path/'chroma.sqlite3').touch()
    monkeypatch.setattr(st,'resolve_keyword_root',lambda *a:tmp_path)
    monkeypatch.setattr(st,'persistent_client',lambda *a:client)
    call=ne.search if kind=='article' else ne.search_keywords
    with pytest.raises(error) as caught:
        call('합성 질의',client=client if borrowed_client else None,embed=provider if borrowed_provider else None)
    assert caught.value is primary
    assert client.close.call_count==int(not borrowed_client)
    assert provider.close.call_count==int(not borrowed_provider)
    if close_fails:
        assert len(getattr(primary,'__notes__',[]))==int(not borrowed_client)+int(not borrowed_provider)


@pytest.mark.parametrize('error',[TimeoutError,asyncio.CancelledError,RagDeadlineExceeded])
@pytest.mark.parametrize('close_fails',[False,True])
def test_service_snapshot_closes_but_shared_api_stays_open(tmp_path,monkeypatch,error,close_fails):
    client=Mock();api=Mock()
    if close_fails:client.close.side_effect=OSError('synthetic close')
    snapshot=NS(client=client,sources=set(ALL_SOURCES),generation='fake')
    svc=service.RagService(NS(snapshot=lambda:snapshot),Vocabulary(categories=Categories(rules=False)),ModelGateway(api),trace_dir=tmp_path/'trace')
    primary=error('synthetic interruption')
    monkeypatch.setattr(service,'search',Mock(side_effect=primary))
    if error is TimeoutError:assert svc.run(request_payload(),ExecutionContext(time.monotonic()+30)).status=='failed'
    else:
        with pytest.raises(error) as caught:svc.run(request_payload(),ExecutionContext(time.monotonic()+30))
        assert caught.value is primary
    client.close.assert_called_once();api.close.assert_not_called()
    assert len(list((tmp_path/'trace').glob('*.json')))==1


@pytest.mark.parametrize('close_fails',[False,True])
def test_evaluation_snapshot_cancel_preserves_primary(tmp_path,monkeypatch,close_fails):
    client=Mock()
    if close_fails:client.close.side_effect=OSError('secondary close')
    snapshot=NS(client=client,generation='fake')
    root=tmp_path/'index';st.atomic_json(root/'generations/fake/vocabulary.json',{})
    store=NS(root=root,snapshot=lambda:snapshot)
    config=RunConfiguration(**configuration_payload(corpus_revision='fake',embedding_model=evaluate_service.MODEL,collections=list(evaluate_service.COLLECTIONS)))
    primary=asyncio.CancelledError('evaluation cancelled')
    monkeypatch.setattr(evaluate_service,'search',Mock(side_effect=primary))
    with pytest.raises(asyncio.CancelledError) as caught:
        evaluate_service.record_retrieval(dataset_one(),store,Mock(),config,seconds_per_request=5)
    assert caught.value is primary
    client.close.assert_called_once()


@pytest.mark.parametrize('stage',['embed','summary'])
@pytest.mark.parametrize('error',[TimeoutError,asyncio.CancelledError])
def test_gateway_uses_remaining_timeout_without_closing_shared_api(stage,error):
    api=Mock();api.with_options.return_value=api
    api.embeddings.create.side_effect=error('synthetic API interruption')
    api.chat.completions.create.side_effect=error('synthetic API interruption')
    gateway=ModelGateway(api);ctx=ExecutionContext(5,clock=lambda:1)
    with pytest.raises(error):
        if stage=='embed':gateway.embed(['합성'],ctx)
        else:gateway.structured('합성',{}, {},ctx)
    api.with_options.assert_called_once_with(timeout=4,max_retries=0)
    api.close.assert_not_called()


@pytest.mark.parametrize('ambient_error',[False,True])
def test_successful_search_cleanup_failure_is_not_hidden(monkeypatch,ambient_error):
    client=Mock();provider=Mock(return_value=[[1.]])
    client.get_collection.return_value.query.return_value={'ids':[[]],'distances':[[]],'documents':[[]],'metadatas':[[]]}
    failure=OSError('close after success');client.close.side_effect=failure
    snapshot=NS(client=client,sources=set(ALL_SOURCES),collection=client.get_collection)
    monkeypatch.setattr(st,'GenerationStore',lambda *a,**kw:NS(snapshot=lambda:snapshot))
    monkeypatch.setattr(ne,'openai_embed',lambda:provider)
    def check():
        with pytest.raises(OSError) as caught:ne.search('합성')
        assert caught.value is failure
    if ambient_error:
        try:raise ValueError('outer handled error')
        except ValueError:check()
    else:check()
    client.close.assert_called_once();provider.close.assert_called_once()


@pytest.mark.parametrize('primary',[None,TimeoutError('original'),asyncio.CancelledError('original')])
def test_cleanup_attempts_all_and_reports_each_error(primary):
    from backend.app.services.rag.resources import close_owned
    first=OSError('first');second=asyncio.CancelledError('second')
    close1=Mock(side_effect=first);close2=Mock(side_effect=second)
    resources=[('one',close1),('two',close2)]
    if primary is None:
        with pytest.raises(OSError) as caught:close_owned(resources)
        assert caught.value is first
        target=first
    else:
        close_owned(resources,primary=primary);target=primary
    assert target.__notes__==['cleanup_failed:one:OSError','cleanup_failed:two:CancelledError']
    close1.assert_called_once();close2.assert_called_once()


@pytest.mark.parametrize('error',[None,TimeoutError,asyncio.CancelledError])
@pytest.mark.parametrize('close_fails',[False,True])
def test_evaluation_cli_owned_api_preserves_error(tmp_path,monkeypatch,error,close_fails):
    import sys
    import openai
    from backend.app.db import session
    dataset=tmp_path/'dataset.json';dataset.write_text(dataset_one().model_dump_json())
    configuration=tmp_path/'configuration.json'
    configuration.write_text(json.dumps(configuration_payload(corpus_revision='fake',
        embedding_model=evaluate_service.MODEL,collections=list(evaluate_service.COLLECTIONS))))
    env=tmp_path/'.env';env.write_text('OPENAI_API_KEY=synthetic-not-a-real-key\n')
    monkeypatch.setattr(session,'ENV_PATH',env)
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    monkeypatch.setattr(sys,'argv',['evaluate_service','--dataset',str(dataset),'--configuration',str(configuration),
        '--index-root',str(tmp_path/'index'),'--seconds-per-request','5','--output',str(tmp_path/'run.json')])
    api=Mock();factory=Mock(return_value=api)
    primary=error('evaluation interruption') if error is not None else None
    cleanup=OSError('api close error')
    if close_fails:api.close.side_effect=cleanup
    monkeypatch.setattr(openai,'OpenAI',factory)
    record=Mock(return_value=Mock(),side_effect=primary)
    monkeypatch.setattr(evaluate_service,'record_retrieval',record)
    write=Mock();monkeypatch.setattr(evaluate_service,'write_new_json',write)
    if error is not None or close_fails:
        with pytest.raises(error or OSError) as caught:evaluate_service.main()
        assert caught.value is (primary if primary is not None else cleanup)
        write.assert_not_called()
        if close_fails:
            assert caught.value.__notes__==['cleanup_failed:openai_client:OSError']
    else:
        evaluate_service.main();write.assert_called_once()
    record.assert_called_once();api.close.assert_called_once()
    factory.assert_called_once_with(api_key='synthetic-not-a-real-key',max_retries=0)
