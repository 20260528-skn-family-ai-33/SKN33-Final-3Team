"""외부 API 없이 사용량의 미제공·오류 응답을 검증합니다."""
from types import SimpleNamespace as NS
import pytest
from backend.app.services.news_usage import record_usage


@pytest.mark.parametrize('tokens,expected', [(7,7),(0,0),(None,None),(-1,None),(True,None),('7',None)])
def test_reported_usage_is_not_assumed_zero(tokens, expected):
    rows=[]
    record_usage(rows.append,'synthetic-model',NS(usage=NS(total_tokens=tokens,prompt_tokens=3,completion_tokens=4)))
    assert rows==[{'model':'synthetic-model','tokens':expected,'input_tokens':3,
                   'output_tokens':4,'reported':expected is not None}]


def test_failed_response_usage_is_unknown_and_optional_sink_is_safe():
    rows=[]
    record_usage(rows.append,'synthetic-model',None)
    assert rows==[{'model':'synthetic-model','tokens':None,'input_tokens':None,
                   'output_tokens':None,'reported':False}]
    record_usage(None,'synthetic-model',None)
