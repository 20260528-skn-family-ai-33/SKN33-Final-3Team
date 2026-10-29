"""질문의 경영 문맥과 환경보호 문맥을 구분하는 회귀 검증."""
from types import SimpleNamespace

import pytest

from backend.app.services.rag.vocabulary import Vocabulary


def request(query):
    return SimpleNamespace(query=query, conditions=SimpleNamespace(
        products=(), geography=SimpleNamespace(codes=())))


def vocabulary():
    return Vocabulary(synonyms={'환경산업': '환경'},
                      hypernyms={'환경보호': '환경'},
                      categories={'환경': '친환경'})


@pytest.mark.parametrize('query', ['경영환경 개선 원인은?', '경영 환경을 분석해줘'])
def test_business_context_does_not_expand_to_ecology(query):
    v = vocabulary()
    assert v.query(request(query)) == query
    assert v.query_categories(request(query)) == set()


@pytest.mark.parametrize('query', ['환경보호 정책', '환경 산업 성장',
                                  '경영환경과 환경보호 정책을 비교해줘'])
def test_explicit_ecology_context_still_expands(query):
    v = vocabulary()
    assert {'환경산업', '환경', '환경보호'} <= set(v.query(request(query)).splitlines())
    assert v.query_categories(request(query)) == {'친환경'}


def test_other_dictionary_matches_and_body_subterms_unchanged():
    v = Vocabulary(synonyms={'EV': '전기차'}, hypernyms={'전기차': '자동차'})
    assert {'EV', '전기차', '자동차'} <= set(v.query(request('EV 수출')).splitlines())
    # 본문 색인의 구체어 추출 경로는 기존 부분 문자열 규칙을 유지합니다.
    body_v = Vocabulary(synonyms={'환경': '환경'})
    assert body_v.body_subterms('경영환경', ['환경']) == ['환경']
