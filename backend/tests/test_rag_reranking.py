import time

import pytest

from backend.app.schemas.rag import ALL_SOURCES, RagRequest
from backend.app.services.rag.retrieval import search, Candidate
from backend.app.services.rag.reranking import rerank_candidates
from backend.app.services.rag.runtime import ExecutionContext, RagDeadlineExceeded
from backend.app.services.rag.store import GenerationStore
from backend.app.services.rag.vocabulary import Vocabulary
from backend.tests.rag_fixtures import request_payload
from backend.tests.test_rag_pipeline import document, vectors, FakeGateway


class Scorer(FakeGateway):
    invalid = False
    expire = False
    calls = 0

    def structured(self, instruction, payload, schema, context):
        self.calls += 1
        assert set(schema['properties']['scores']['required']) == {str(a['candidate_id']) for a in payload['articles']}
        if self.expire:
            raise RagDeadlineExceeded('global_deadline_exceeded')
        assert all('차단 본문' not in str(a['chunks']) for a in payload['articles'])
        return {'scores': {str(a['candidate_id']): {'score': 2,
                            'chunk_id': next(iter(a['chunks'])),
                            'quote': '없는 발췌' if self.invalid else next(iter(a['chunks'].values()))}
                           for a in payload['articles']}}


@pytest.mark.parametrize('mode', ['pool', 'rerank', 'failure', 'deadline', 'local'])
def test_pool_final_limit_blocking_and_rerank_failures(tmp_path, mode):
    store = GenerationStore(tmp_path / 'index', dimensions=8)
    docs = [document(i) for i in range(1, 14)]
    docs.append(document(14, body='차단 본문', kw_topic=['차단']))
    store.publish(docs, ALL_SOURCES, vectors)
    payload = request_payload('chat')
    payload['conditions']['blocked_topics'] = ['차단']
    req = RagRequest.model_validate(payload)
    scorer = Scorer()
    scorer.invalid, scorer.expire = mode == 'failure', mode == 'deadline'
    snapshot = store.snapshot()
    def execute(**options):
        return search(req, snapshot, Vocabulary(), scorer,
                      ExecutionContext(time.monotonic() + 30), **options)
    try:
        baseline = execute()
        if mode == 'deadline':
            with pytest.raises(RagDeadlineExceeded):
                execute(candidate_limit=10, rerank=True)
            return
        result = execute(candidate_limit=10, rerank='local_feedback' if mode == 'local' else mode != 'pool')
        assert len(result.candidates) == 5
        if mode != 'local':
            assert [c.article.key for c in result.candidates] == [c.article.key for c in baseline.candidates]
        assert all(c.article.external_id != '14' for c in result.candidates)
        assert bool(result.failures) == (mode == 'failure')
        assert scorer.calls == {'pool': 0, 'rerank': 2, 'failure': 2, 'local': 0}[mode]
    finally:
        snapshot.client.close()


def test_partial_direct_evidence_moves_above_background():
    candidates = [Candidate(document(i), i / 10, 'v1', {str(i): f'기사 {i}의 독립 근거'})
                  for i in range(1, 4)]

    class Gateway:
        def structured(self, instruction, payload, schema, context):
            return {'scores': {str(i): {'score': 2 if i == 2 else 1,
                                'chunk_id': str(i + 1), 'quote': f'기사 {i + 1}의 독립 근거'}
                               for i in range(3)}}

    ranked = rerank_candidates(RagRequest.model_validate(request_payload('chat')),
                              candidates, Gateway(), ExecutionContext(time.monotonic() + 30))
    assert [c.article.external_id for c in ranked] == ['3', '1', '2']


@pytest.mark.parametrize('bad', ['missing_id', 'extra_id', 'foreign_quote', 'boolean_score'])
def test_invalid_model_judgment_is_retried_once_then_rejected(bad):
    candidates = [Candidate(document(i), i / 10, 'v1', {str(i): f'기사 {i}의 독립 근거'})
                  for i in range(1, 3)]

    class Gateway:
        calls = 0

        def structured(self, instruction, payload, schema, context):
            self.calls += 1
            rows = {str(i): {'score': 2, 'chunk_id': str(i + 1),
                     'quote': f'기사 {i + 1}의 독립 근거'} for i in range(2)}
            if bad == 'missing_id':
                rows.pop('1')
            elif bad == 'extra_id':
                rows['2'] = rows['1']
            elif bad == 'foreign_quote':
                rows['0']['quote'] = rows['1']['quote']
            else:
                rows['0']['score'] = True
            return {'scores': rows}

    gateway = Gateway()
    with pytest.raises(ValueError):
        rerank_candidates(RagRequest.model_validate(request_payload('chat')),
                          candidates, gateway, ExecutionContext(time.monotonic() + 30))
    assert gateway.calls == 2
