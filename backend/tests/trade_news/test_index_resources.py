import asyncio
from types import SimpleNamespace as NS
from unittest.mock import Mock
import pytest
from backend.app.schemas.rag import ALL_SOURCES
from backend.app.scheduler.jobs import news_embed as ne
from backend.app.services.rag import store as st
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
