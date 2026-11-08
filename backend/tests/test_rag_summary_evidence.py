import unittest
from datetime import date
from types import SimpleNamespace

from backend.app.schemas.rag import RagRequest
from backend.tests.test_rag_pipeline import request_payload
from backend.app.services.rag.summary_evidence import summary_ranges, source_passage
from backend.app.services.rag.service import grounded_sentences
from backend.app.services.rag.gateway import ModelGateway


class SummaryEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.candidate = SimpleNamespace(chunks={'a': '원유 생산량\r\n단위: 천 배럴/일\n9월/8월\n-27\n',
                                               'b': '다른 기사 구간'},
                                         article=SimpleNamespace(identity='source:123', title='합성 기사',
                                             published_date=date(2026, 9, 1), category='', countries=['US']), version='v1')
        self.spans = summary_ranges(self.candidate)

    def request(self, query):
        return RagRequest.model_validate(request_payload(query=query))

    def scripted(self, replies):
        def structured(instruction, payload, schema, context):
            if 'relation' in schema['properties']:
                return {'relation':'related', 'reason':'본문이 합성 질문을 설명한다.'}
            return next(replies)
        return structured


    def test_exact_table_and_newlines_preserved_and_grounded(self):
        last = max(i for i, span in enumerate(self.spans) if span['chunk_id'] == 'a')
        key, quote = source_passage(self.spans, self.candidate, 0, last)
        payload = {'sentences': [{'text': '해당 표의 원유 생산량 변화다.', 'chunk_id': key, 'quote': quote}]}
        self.assertEqual(quote, self.candidate.chunks['a'])
        evidence, sentences = grounded_sentences(payload, self.candidate)
        self.assertEqual(sentences[0].evidence_ids, (evidence[0].evidence_id,))

    def test_invalid_selection_is_not_silently_repaired(self):
        for index in [True, -1, 999, 1.5]:
            with self.subTest(index=index), self.assertRaises(ValueError):
                source_passage(self.spans, self.candidate, index, 0)

    def test_changed_source_is_rejected(self):
        self.candidate.chunks['a'] = '변경된 원문'
        with self.assertRaises(ValueError):
            source_passage(self.spans, self.candidate, 0, 0)

    def test_long_line_is_fully_preserved(self):
        self.candidate.chunks = {'a': '문' * 1301}
        spans = self.spans = summary_ranges(self.candidate)
        self.assertEqual(''.join(s['quote'] for s in spans), self.candidate.chunks['a'])
        self.assertEqual(source_passage(spans, self.candidate, 0, len(spans)-1)[1], self.candidate.chunks['a'])


    def test_selected_passage_must_be_in_its_own_body_chunk(self):
        self.assertEqual(source_passage(self.spans, self.candidate, 1, 1)[1], '단위: 천 배럴/일\n')
        with self.assertRaisesRegex(ValueError, 'summary_range_crosses_chunks'):
            source_passage(self.spans, self.candidate, 1, len(self.spans)-1)

    def test_one_bad_range_keeps_a_valid_sibling_and_all_bad_ranges_can_be_recovered(self):
        self.candidate.article.title = '표'
        gateway = ModelGateway(None)
        request = self.request('어떤 단위인가?')
        flags = {'question_target':'표 단위','sentence_target':'표 단위','support_reason':'연결 인용',
                 'answers_question':True,'relevant_reference':False,'supported_by_quote':True,
                 'unverified_question_parts':[],'partial_role':'none','partial_passage':''}
        invalid_chunk = {'text':'해당 표의 원유 생산량 변화다.', 'evidence_index':999}
        replies = iter([{'sentences':[invalid_chunk]}, {'passages':[{'evidence_index':0,'end_index':1,'answer_index':1}]},
                        {'0':flags}])
        gateway.structured = self.scripted(replies)
        rows = gateway.summarize(request, self.candidate, SimpleNamespace(event=lambda *args, **kw: None))['sentences']
        self.assertEqual([r['text'] for r in rows], ['단위: 천 배럴/일'])

    def test_restore_original_spacing_without_losing_a_supported_sibling(self):
        from backend.tests.test_rag_pipeline import document
        body = '계약 물량은 20톤이다.\n\n  선적지는 A항이다.'
        article = document(1, body=body)
        indexed = '계약 물량은 20톤이다.\n선적지는 A항이다.'
        candidate = SimpleNamespace(article=article, version=article.version,
                                    chunks={'a': indexed, 'b': '선적지는 B항이다.'})
        rows = [{'text':'확인된 선적지는 A항이다.', 'quote':indexed,'chunk_id':'a'},
                {'text':'선적지는 B항이다.', 'quote':'선적지는 B항이다.','chunk_id':'b'}]
        evidence, summary = grounded_sentences({'sentences':rows}, candidate)
        self.assertEqual([e.quote for e in evidence],[body])
        self.assertEqual([s.text for s in summary], ['확인된 선적지는 A항이다.'])

    def test_partial_recovery_keeps_the_answer_chunk_and_checks_its_context(self):
        self.candidate.chunks = {'a':'다른 화물의 정보다. 해당 화물은 A항에 있다.',
                                 'b':'화물 N은 B항에서 환적했다. 최종 판매지는 기록되지 않았다.'}
        gateway = ModelGateway(None)
        request = self.request('화물 N의 환적 경로와 최종 판매지는?')
        flags = {'question_target':'화물 N', 'sentence_target':'환적', 'support_reason':'B항 기록',
                 'answers_question':True, 'relevant_reference':False, 'supported_by_quote':True,
                 'unverified_question_parts':['최종 판매지'], 'partial_role':'none', 'partial_passage':''}
        replies = iter([{'sentences':[]},
                        {'passages':[{'evidence_index':0,'end_index':3,'answer_index':2}]},
                        {'0':flags}])
        gateway.structured = self.scripted(replies)
        result = gateway.summarize(request, self.candidate, SimpleNamespace(event=lambda *args, **kw: None))
        self.assertEqual(result['sentences'][0]['text'], '화물 N은 B항에서 환적했다.')
        self.assertEqual(result['sentences'][0]['quote'], self.candidate.chunks['b'])
        self.assertEqual(result['sentences'][0]['chunk_id'], 'b')

    def test_partial_sentence_preserves_the_reviewed_witness_context(self):
        gateway = ModelGateway(None)
        quote = '화물 N은 B항에 도착했다. 이 화물은 C항으로 이동했다.'
        flags = {'question_target':'화물 N 경로', 'sentence_target':'확인된 구간', 'support_reason':'본문 구간',
                 'answers_question':False, 'relevant_reference':False, 'supported_by_quote':False,
                 'unverified_question_parts':[], 'partial_role':'direct', 'partial_passage':'이 화물은 C항으로 이동했다.'}
        gateway.structured = lambda *args: {'0':flags}
        result = gateway.check_summary(self.request('화물 N의 경로는?'),
            {'sentences':[{'text':'화물의 전체 이동 경로다.', 'quote':quote,'chunk_id':'b'}]},
            SimpleNamespace(event=lambda *args, **kw: None))
        self.assertEqual(result['sentences'][0]['text'], flags['partial_passage'])
        self.assertEqual(result['sentences'][0]['quote'], quote)

    def test_empty_draft_can_recover_an_independently_checked_partial_answer(self):
        gateway = ModelGateway(None)
        self.candidate.article.title = '기사'
        self.candidate.chunks = {'a': '화물은 A항을 거쳐 B항으로 이동했다. 최종 판매지는 기록되지 않았다.'}
        quote = '화물은 A항을 거쳐 B항으로 이동했다.'
        request = self.request('환적 경로와 최종 판매지는?')
        flags = {'question_target':'경로와 판매지','sentence_target':'경로','support_reason':quote,
                 'answers_question':True,'relevant_reference':False,'supported_by_quote':True,
                 'unverified_question_parts':['최종 판매지', '시장 점유율', '최종 판매지'],
                 'partial_role':'none','partial_passage':''}
        for supported in (True, False):
            flags['supported_by_quote'] = supported
            replies = iter([{'sentences':[]}, {'passages':[{'evidence_index':0,'end_index':1,'answer_index':0}]}, {'0':flags}])
            gateway.structured = self.scripted(replies)
            rows = gateway.summarize(request, self.candidate, SimpleNamespace(event=lambda *args, **kw: None))['sentences']
            self.assertEqual([r['text'] for r in rows], [quote] if supported else [])
            if supported:
                self.assertIn('최종 판매지는 기록되지 않았다.', rows[0]['quote'])
                self.assertEqual(rows[0]['unverified_question_parts'], ['최종 판매지'])

    def test_gateway_resolves_selected_chunk_without_model_quote(self):
        gateway = ModelGateway(None)
        self.candidate.article.title = '합성 생산량 표'
        request = self.request('생산량 변화는?')
        captured = []
        def structured(instruction, payload, schema, context):
            if 'relation' in schema['properties']:
                return {'relation':'related', 'reason':'본문이 생산량 변화를 설명한다.'}
            captured.append(payload)
            return {'sentences':[{'text':'표의 생산량 변화다.', 'evidence_index':0}]} if len(captured) == 1 else {'0': {'question_target': '질문 대상', 'sentence_target': '문장 대상', 'support_reason': '근거 확인', 'unverified_question_parts': [], 'partial_role': 'none', 'partial_passage': '', 'answers_question': True, 'relevant_reference': False, 'supported_by_quote': True}}
        gateway.structured = structured
        output = gateway.summarize(request, self.candidate, SimpleNamespace(event=lambda *args, **kw: None))
        self.assertEqual(output['sentences'][0]['quote'], self.candidate.chunks['a'])
        self.assertEqual(captured[0]['question'], request.query)
        self.assertEqual(''.join(chunk['text'] for chunk in captured[0]['evidence_spans']), ''.join(self.candidate.chunks.values()))

    def test_topic_review_reads_whole_body_and_keeps_uncertain_for_followup(self):
        body = '프랑스 지역 소식이다.\n' * 20 + '프랑스 수입사는 독일에서 생산한 향수를 수입했다.'
        self.candidate.article.body = body
        self.candidate.article.countries = ['FR']
        self.candidate.chunks = {'a':body}
        payload = request_payload(query='독일에서 생산해 수출한 화장품의 기사 제목과 날짜를 알려 줘.')
        payload['conditions']['geography'] = {'mode':'selected','codes':['DE']}
        payload['conditions']['products'] = [{'hs_ver':'HS2022','hs_code':'3303'}]
        request = RagRequest.model_validate(payload)
        for relation, expected_calls in [('unrelated',1), ('related',3), ('uncertain',3)]:
            with self.subTest(relation=relation):
                gateway = ModelGateway(None)
                calls = []
                events = []
                def structured(instruction, data, schema, context):
                    calls.append(data)
                    if 'relation' in schema['properties']:
                        return {'relation':relation, 'reason':'전체 본문 판정'}
                    if 'sentences' in schema['properties']:
                        return {'sentences':[]}
                    return {'passages':[]}
                gateway.structured = structured
                context = SimpleNamespace(event=lambda stage, **data: events.append((stage, data)))
                result = gateway.summarize(request, self.candidate, context)
                self.assertEqual(result, {'sentences':[]})
                self.assertEqual(len(calls), expected_calls)
                self.assertEqual(calls[0]['body'], body)
                self.assertEqual(calls[0]['publication_date'], '2026-09-01')
                self.assertEqual(calls[0]['target_geography']['codes'], ['DE'])
                self.assertNotIn('period', calls[0])
                # v26 records uncertainty and lets generation/evidence review decide.
                self.assertFalse(any(stage == 'article_topic_unconfirmed' for stage, _ in events))
                topic = next(data for stage, data in events if stage == 'article_topic_review')
                self.assertEqual(topic['relation'], relation)

    def test_empty_body_does_not_call_model(self):
        self.candidate.chunks = {'empty': '  '}
        gateway = ModelGateway(None)
        self.assertEqual(gateway.summarize(None, self.candidate, None), {'sentences': []})

    def test_semantic_check_filters_only_unsupported_or_off_topic_sentences(self):
        gateway = ModelGateway(None)
        request = SimpleNamespace(query='합성 질문', conditions=SimpleNamespace(model_dump=lambda **kw: {}))
        result = {'sentences': [{'text': '부분 답변', 'quote': '부분 근거', 'chunk_id': 'a'},
                                {'text': '다른 대상', 'quote': '다른 근거', 'chunk_id': 'b'}]}
        gateway.structured = lambda *args: {'0': {'question_target': '질문 대상', 'sentence_target': '문장 대상', 'support_reason': '근거 확인', 'unverified_question_parts': [], 'partial_role': 'none', 'partial_passage': '', 'answers_question': True, 'relevant_reference': False, 'supported_by_quote': True},
                                           '1': {'question_target': '질문 대상', 'sentence_target': '문장 대상', 'support_reason': '근거 확인', 'unverified_question_parts': [], 'partial_role': 'none', 'partial_passage': '', 'answers_question': False, 'relevant_reference': False, 'supported_by_quote': True}}
        context = SimpleNamespace(event=lambda *args, **kw: None)
        self.assertEqual(gateway.check_summary(request, result, context)['sentences'], result['sentences'][:1])
        gateway.structured = lambda *args: {'0': {'question_target': '질문 대상', 'sentence_target': '문장 대상', 'support_reason': '근거 확인', 'unverified_question_parts': [], 'partial_role': 'none', 'partial_passage': '', 'answers_question': True, 'relevant_reference': False, 'supported_by_quote': False},
                                           '1': {'question_target': '질문 대상', 'sentence_target': '문장 대상', 'support_reason': '근거 확인', 'unverified_question_parts': [], 'partial_role': 'none', 'partial_passage': '', 'answers_question': False, 'relevant_reference': False, 'supported_by_quote': False}}
        self.assertEqual(gateway.check_summary(request, result, context), {'sentences': []})

    def test_missing_or_non_boolean_verdict_is_failure_not_normal_hold(self):
        gateway = ModelGateway(None)
        request = SimpleNamespace(query='질문', conditions=SimpleNamespace(model_dump=lambda **kw: {}))
        result = {'sentences': [{'text': '문장', 'quote': '근거', 'chunk_id': 'a'}]}
        for verdict in [{}, {'0': {'question_target': '질문 대상', 'sentence_target': '문장 대상', 'support_reason': '근거 확인', 'unverified_question_parts': [], 'partial_role': 'none', 'partial_passage': '', 'answers_question': 'false', 'relevant_reference': False, 'supported_by_quote': True}}]:
            gateway.structured = lambda *args: verdict
            with self.assertRaises(ValueError):
                gateway.check_summary(request, result, None)



    def test_reference_is_labelled_and_never_bypasses_quote_check(self):
        gateway = ModelGateway(None)
        request = SimpleNamespace(query='정책 효과는?', conditions=SimpleNamespace(model_dump=lambda **kw: {}))
        row = {'text': '기사는 전년도 교역 증가를 설명한다.', 'quote': '전년도 교역 증가', 'chunk_id': 'a'}
        context = SimpleNamespace(event=lambda *args, **kw: None)
        flags = {'question_target': '정책 효과', 'sentence_target': '전년도 교역',
                 'support_reason': '시점이 다른 배경으로만 참조', 'unverified_question_parts': [], 'partial_role': 'none', 'partial_passage': '', 'answers_question': False,
                 'relevant_reference': True, 'supported_by_quote': True}
        gateway.structured = lambda *args: {'0': flags}
        result = gateway.check_summary(request, {'sentences': [row]}, context)
        self.assertTrue(result['sentences'][0]['text'].startswith('참고(직접 답변 아님): '))
        self.assertEqual(result['sentences'][0]['quote'], row['quote'])
        self.assertFalse(row['text'].startswith('참고'))
        flags['supported_by_quote'] = False
        self.assertEqual(gateway.check_summary(request, {'sentences': [row]}, context), {'sentences': []})
        flags.pop('relevant_reference')
        with self.assertRaises(ValueError):
            gateway.check_summary(request, {'sentences': [row]}, context)


if __name__ == '__main__':
    unittest.main()
