"""단신 적재의 사용량·캐시·재시도 검증. 합성 입력만 사용합니다."""
import json
from types import SimpleNamespace as NS
from unittest.mock import Mock
import pytest
from sqlalchemy import create_engine
from backend.app.services import shortnews_analysis as analysis


def test_worker_stop_handler_waits_and_releases_own_lock_without_processes(tmp_path, monkeypatch):
    import signal
    import sys
    from backend.app.scheduler import shortnews_worker as worker
    from filelock import FileLock
    calls = []
    handlers = {}
    class FakeScheduler:
        running = True
        def get_jobs(self): return []
        def start(self):
            calls.append('start')
            handlers[signal.SIGTERM](signal.SIGTERM, None)
        def shutdown(self, *, wait):
            calls.append(('shutdown', wait))
            self.running = False
    scheduler = FakeScheduler()
    monkeypatch.setattr(worker, 'ROOT', tmp_path)
    monkeypatch.setattr(worker, 'build_scheduler', lambda: scheduler)
    monkeypatch.setattr(signal, 'signal', lambda sig, fn: handlers.setdefault(sig, fn))
    monkeypatch.setattr(sys, 'argv', ['shortnews_worker'])
    worker.main()
    assert calls == ['start', ('shutdown', True)]
    assert not scheduler.running
    with FileLock(str(tmp_path/'data/shortnews/worker.lock'), timeout=0):
        pass

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
        case.embed.side_effect=encode;case.shared_embed.side_effect=encode;case.ask.side_effect=ask
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

def test_memory_engine_identity_is_stable_but_not_shared():
    from backend.app.db.news_lock import engine_identity
    first=create_engine('sqlite://');second=create_engine('sqlite://')
    try:
        assert engine_identity(first)==engine_identity(first)
        assert engine_identity(first)!=engine_identity(second)
    finally:first.dispose();second.dispose()
