import unittest
from types import SimpleNamespace

from backend.app.services.rag.summary_evidence import summary_ranges, materialize_summary
from backend.app.services.rag.service import grounded_sentences
from backend.app.services.rag.gateway import ModelGateway


class SummaryEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.candidate = SimpleNamespace(chunks={'a': '원유 생산량\r\n단위: 천 배럴/일\n9월/8월\n-27\n',
                                               'b': '다른 기사 구간'},
                                         article=SimpleNamespace(identity='source:123'), version='v1')
        self.spans = summary_ranges(self.candidate)

    def output(self, index=0):
        return {'sentences': [{'text': '해당 표의 원유 생산량 변화다.',
                               'evidence_index': index}]}

    def test_exact_table_and_newlines_preserved_and_grounded(self):
        payload = materialize_summary(self.output(), self.spans, self.candidate)
        self.assertEqual(payload['sentences'][0]['quote'], self.candidate.chunks['a'])
        evidence, sentences = grounded_sentences(payload, self.candidate)
        self.assertEqual(sentences[0].evidence_ids, (evidence[0].evidence_id,))

    def test_invalid_selection_is_not_silently_repaired(self):
        for index in [True, -1, 999, 1.5]:
            with self.subTest(index=index), self.assertRaises(ValueError):
                materialize_summary(self.output(index), self.spans, self.candidate)

    def test_changed_source_is_rejected(self):
        self.candidate.chunks['a'] = '변경된 원문'
        with self.assertRaises(ValueError):
            materialize_summary(self.output(), self.spans, self.candidate)

    def test_long_line_is_fully_preserved(self):
        self.candidate.chunks = {'a': '문' * 1301}
        spans = summary_ranges(self.candidate)
        self.assertEqual(''.join(s['quote'] for s in spans), self.candidate.chunks['a'])
        self.assertEqual(materialize_summary(self.output(0), spans, self.candidate)
                         ['sentences'][0]['quote'], self.candidate.chunks['a'])

    def test_held_has_no_fabricated_quote(self):
        self.assertEqual(materialize_summary({'sentences': []}, self.spans, self.candidate), {'sentences': []})

    def test_gateway_resolves_selected_chunk_without_model_quote(self):
        gateway = ModelGateway(None)
        self.candidate.article.title = '합성 생산량 표'
        request = SimpleNamespace(query='생산량 변화는?', conditions=SimpleNamespace(model_dump=lambda **kw: {}))
        captured = []
        def structured(instruction, payload, schema, context):
            captured.append(payload)
            return self.output(0) if len(captured) == 1 else {'0': {'question_target': '질문 대상', 'sentence_target': '문장 대상', 'support_reason': '근거 확인', 'answers_question': True, 'supported_by_quote': True}}
        gateway.structured = structured
        output = gateway.summarize(request, self.candidate, SimpleNamespace(event=lambda *args, **kw: None))
        self.assertEqual(output['sentences'][0]['quote'], self.candidate.chunks['a'])
        self.assertEqual(captured[0]['question'], request.query)
        self.assertEqual([s['text'] for s in captured[0]['evidence_spans']], list(self.candidate.chunks.values()))

    def test_empty_body_does_not_call_model(self):
        self.candidate.chunks = {'empty': '  '}
        gateway = ModelGateway(None)
        self.assertEqual(gateway.summarize(None, self.candidate, None), {'sentences': []})

    def test_semantic_check_filters_only_unsupported_or_off_topic_sentences(self):
        gateway = ModelGateway(None)
        request = SimpleNamespace(query='합성 질문', conditions=SimpleNamespace(model_dump=lambda **kw: {}))
        result = {'sentences': [{'text': '부분 답변', 'quote': '부분 근거', 'chunk_id': 'a'},
                                {'text': '다른 대상', 'quote': '다른 근거', 'chunk_id': 'b'}]}
        gateway.structured = lambda *args: {'0': {'question_target': '질문 대상', 'sentence_target': '문장 대상', 'support_reason': '근거 확인', 'answers_question': True, 'supported_by_quote': True},
                                           '1': {'question_target': '질문 대상', 'sentence_target': '문장 대상', 'support_reason': '근거 확인', 'answers_question': False, 'supported_by_quote': True}}
        context = SimpleNamespace(event=lambda *args, **kw: None)
        self.assertEqual(gateway.check_summary(request, result, context)['sentences'], result['sentences'][:1])
        gateway.structured = lambda *args: {'0': {'question_target': '질문 대상', 'sentence_target': '문장 대상', 'support_reason': '근거 확인', 'answers_question': True, 'supported_by_quote': False},
                                           '1': {'question_target': '질문 대상', 'sentence_target': '문장 대상', 'support_reason': '근거 확인', 'answers_question': False, 'supported_by_quote': False}}
        self.assertEqual(gateway.check_summary(request, result, context), {'sentences': []})

    def test_missing_or_non_boolean_verdict_is_failure_not_normal_hold(self):
        gateway = ModelGateway(None)
        request = SimpleNamespace(query='질문', conditions=SimpleNamespace(model_dump=lambda **kw: {}))
        result = {'sentences': [{'text': '문장', 'quote': '근거', 'chunk_id': 'a'}]}
        for verdict in [{}, {'0': {'question_target': '질문 대상', 'sentence_target': '문장 대상', 'support_reason': '근거 확인', 'answers_question': 'false', 'supported_by_quote': True}}]:
            gateway.structured = lambda *args: verdict
            with self.assertRaises(ValueError):
                gateway.check_summary(request, result, None)


if __name__ == '__main__':
    unittest.main()
