import json
import unittest
from contextlib import nullcontext
from unittest.mock import MagicMock, patch

from backend.app.agents.compose import ARTICLE_PROMPT, compose_input, numbered_articles
from backend.app.agents.runtime import Services
from backend.app.agents.state import AgentState, Draft, MAX_DIGEST_BYTES, ToolResult
from backend.app.api.config import Settings
from backend.scripts.evaluate_writer_dev import DEV, article, dev_state, statistic


def compose(state, *outputs, text=False):
    client, execution = MagicMock(), MagicMock()
    client.parse.side_effect = outputs
    execution.call.side_effect = lambda node, timeout, call, **kw: call(timeout)
    services = Services(execution, Settings({}), client)
    with patch.object(services, 'read_store', return_value=nullcontext()), \
         patch('backend.app.agents.templates._names', return_value=({}, {})):
        (services.compose_text if text else services.compose)(state)
    return client


class WriterEvidenceTest(unittest.TestCase):
    def test_article_guidance_follows_each_tasks_search_plan(self):
        for names, expected in [(['01-large-export'], [False]), (['06-partial'], [True]),
                                (['01-large-export', '06-partial'], [False, True])]:
            with self.subTest(cases=names):
                state = dev_state([row for name in names for row in DEV[name]])
                draft = Draft(conclusion=[{'task_id': t.task_id, 'sentences': ['통계를 확인했습니다.']}
                                          for t in state.plan.tasks], turn_summary='')
                request = compose(state, draft).parse.call_args.kwargs
                tasks = request['payload']['tasks']
                self.assertEqual([t['article_search_planned'] for t in tasks], expected)
                self.assertEqual(ARTICLE_PROMPT in request['prompt'], any(expected))
                for t, planned in zip(tasks, expected, strict=True):
                    self.assertEqual('article_period' in t['conditions'], planned)
                    if not planned:
                        self.assertFalse(any('기사 검색' in w for w in t['warnings']))
                if any(expected):
                    self.assertTrue(any('시간' in w for w in tasks[-1]['warnings']))

    def test_text_compose_keeps_article_rules_token_budget_and_conversation_context(self):
        state = dev_state([('CONVERSATION', '이전 질문은 무엇인가요?', None, []), *DEV['04-context']])
        draft = Draft(conclusion=[{'task_id': 't1', 'sentences': ['이전 대화가 없습니다.']},
                                  {'task_id': 't2', 'sentences': ['통관 절차 공지는 배경 자료입니다.[1]']}],
                      turn_summary='')
        request = compose(state, draft, text=True).parse.call_args.kwargs
        self.assertEqual(request['max_output_tokens'], 6000)
        self.assertIn(ARTICLE_PROMPT, request['prompt'])
        self.assertIn('recent', request['payload'])
        self.assertEqual([t['article_search_planned'] for t in request['payload']['tasks']], [False, True])
        self.assertEqual(state.draft, draft)

    def test_text_fallback_for_conversation_and_hs_lookup(self):
        state = dev_state([('CONVERSATION', '이전 질문은 무엇인가요?', None, []),
                           ('HS_LOOKUP', '면도기 HS 코드는?', None, [])])
        task = state.plan.tasks[1]
        state.results[task.steps[0].step_id] = ToolResult(status='OK', digest={'kind': 'HS_CODES',
            'items': [{'query': '면도기', 'code': '821210', 'name': '면도기', 'level': 'HS6'}]})
        task.answer_status = state.task_outcome(task.task_id)
        with self.assertLogs('backend.app.agents.runtime', 'WARNING'):
            compose(state, ValueError('draft'), ValueError('draft'), text=True)
        self.assertEqual([t.answer_status for t in state.plan.tasks], ['PARTIAL', 'PARTIAL'])
        self.assertIn('821210 면도기', state.draft.conclusion[1].sentences[0])
        self.assertNotIn('차트', ' '.join(state.draft.sentences()))

    def test_rag_scope_survives_checkpoint_and_is_attached_to_the_right_sentence(self):
        row = article(1, '업계 동향', '직접 확인된 공지입니다.', 'mixed', ['수출 증가 원인'])
        row['summary'].append({'text': '참고(직접 답변 아님): 운영 시간이 달라졌습니다.',
            'evidence_ids': ['evidence-1'], 'unverified_question_parts': ['수출 증가 원인', '수입 동향']})
        row['evidence'][0]['quote'] = 'PRIVATE QUOTE'
        state = dev_state([('CAUSE', '수출 증가 원인과 수입 동향은?', statistic([100]), [row])])
        state = AgentState.model_validate_json(state.model_dump_json())
        payload, _ = compose_input(state, lambda _: {}, numbered_articles(state))
        news = payload['tasks'][0]['digests']['news']
        self.assertEqual(news['unverified_question_parts'], ['수출 증가 원인'])
        self.assertNotIn('items', news)
        result, = payload['articles']
        self.assertEqual(result['evidence_role'], 'mixed')
        self.assertEqual(result['reference_limitation'], row['reference_limitation'])
        self.assertEqual([s['unverified_question_parts'] for s in result['summary']],
                         [['수출 증가 원인'], ['수출 증가 원인', '수입 동향']])
        self.assertNotIn('PRIVATE QUOTE', str(payload))

    def test_oversize_sentence_limits_cannot_turn_into_unqualified_evidence(self):
        row = article(3, '합성 공지', '공지를 확인했습니다.', 'mixed')
        row['summary'].append({'text': '인용하면 안 되는 문장', 'evidence_ids': ['evidence-3'],
                               'unverified_question_parts': ['가' * 3000]})
        state = dev_state([('LOCAL_ISSUES', '공지는?', None, [row])])
        result = state.results['t1.news']
        self.assertTrue(result.digest['truncated'])
        self.assertEqual(result.completeness, 'PARTIAL')
        self.assertLessEqual(len(json.dumps(result.digest, ensure_ascii=False, separators=(',', ':')).encode()), MAX_DIGEST_BYTES)
        payload, _ = compose_input(state, lambda _: {}, numbered_articles(state))
        self.assertEqual(len(payload['articles'][0]['summary']), 1)
        self.assertNotIn('인용하면 안 되는 문장', str(payload))

    def test_sparse_display_numbers_and_duplicate_article_across_tasks(self):
        state = dev_state(DEV['05-two-tasks'])
        first = state.results['t1.news']
        first.digest['items'].insert(0, {**first.digest['items'][0], 'article_id': 7,
            'evidence_sufficient': False, 'summary': ['근거가 없는 비공개 문장']})
        first.article_ids.insert(0, 7)
        second = state.results['t2.news']
        second.digest['items'][0]['article_id'] = second.article_ids[0] = 1
        # An older checkpoint contains plain strings and no evidence role metadata.
        second.digest['items'][0].pop('evidence_role')
        second.digest['items'][0]['summary'] = ['예전 요약']
        numbered = numbered_articles(state)
        self.assertEqual([(a['n'], a['task_id'], a['article_id']) for a in numbered],
                         [(1, 't1', 7), (2, 't1', 1), (3, 't2', 1)])
        payload, _ = compose_input(state, lambda _: {}, numbered)
        self.assertEqual([a['n'] for a in payload['articles']], [2, 3])
        self.assertEqual(payload['articles'][1]['evidence_role'], 'unclassified')
        self.assertNotIn('근거가 없는 비공개 문장', str(payload))

        def draft(t1, t2, explanation):
            return Draft(conclusion=[{'task_id': 't1', 'sentences': [t1]}, {'task_id': 't2', 'sentences': [t2]}],
                         explanation=[explanation], turn_summary='기사 요약')
        valid = draft('포장 [2]', '창고 [3]', '두 공지 [2, 3]')
        for citation in ('[2][3]', '[2, 3]', '[2,3]'):
            with self.subTest(citation=citation):
                current = state.model_copy(deep=True)
                expected = draft('포장 [2]', '창고 [3]', '두 공지 ' + citation)
                self.assertEqual(compose(current, expected).parse.call_count, 1)
                self.assertEqual(current.draft, expected)
        for bad in [draft('포장 [3]', '창고 [3]', '전체 [2]'), draft('포장 [1]', '창고 [3]', '전체 [2]'),
                    draft('포장 [2]', '창고 [3]', '전체 [2, 99]'), draft('포장 [2]', '창고 [3]', '전체 [2,]'),
                    draft('포장 [2]', '창고 [3]', '전체 [2-3]'), draft('포장 [2]', '창고 [3]', '전체 [n]'), draft('포장 [2]', '창고 [2,3]', '전체 [2]')]:
            with self.subTest(bad=bad), self.assertLogs('backend.app.agents.runtime', 'WARNING'):
                current = state.model_copy(deep=True)
                client = compose(current, bad, valid)
                self.assertEqual(client.parse.call_count, 2)
                self.assertEqual(current.draft, valid)

    def test_two_articles_in_one_task_can_share_a_bracket(self):
        first, second = DEV['05-two-tasks']
        state = dev_state([('LOCAL_ISSUES', '포장·창고 공지를 알려 주세요.', None, first[3] + second[3])])
        draft = Draft(conclusion=[{'task_id': 't1', 'sentences': ['두 공지가 있습니다.[1, 2]']}], turn_summary='')
        self.assertEqual(compose(state, draft).parse.call_count, 1)
        self.assertEqual(state.draft, draft)

    def test_stats_only_after_both_draft_attempts_fail(self):
        state = dev_state(DEV['06-partial'])
        with self.assertLogs('backend.app.agents.runtime', 'WARNING'):
            client = compose(state, ValueError('draft'), TimeoutError())
        self.assertEqual(client.parse.call_count, 2)
        text = ' '.join(state.draft.sentences())
        self.assertIn('2,610만 달러', text)
        self.assertIn('시간', text)
        self.assertNotIn('차트', text)
        self.assertNotIn('기사 목록', text)
        self.assertEqual(state.plan.tasks[0].answer_status, 'PARTIAL')

    def test_article_only_and_no_data_use_the_remaining_results(self):
        for news_available in [True, False]:
            with self.subTest(news_available=news_available):
                state = dev_state(DEV['04-context'])
                task = state.plan.tasks[0]
                for step in task.steps:
                    if step.tool == 'query_trade_data':
                        step.status = 'FAILED'
                        state.results[step.step_id] = ToolResult(status='NO_DATA', error='통계가 미집계입니다.')
                    if step.tool == 'build_chart':
                        step.status = 'SKIPPED'
                    if step.tool == 'search_articles' and not news_available:
                        step.status = 'FAILED'
                        state.results[step.step_id] = ToolResult(status='NO_DATA', error='조건에 맞는 기사가 없습니다.')
                task.answer_status = state.task_outcome(task.task_id)
                client = compose(state)
                client.parse.assert_not_called()
                self.assertEqual(task.answer_status, 'PARTIAL' if news_available else 'NO_DATA')
                text = ' '.join(state.draft.sentences())
                self.assertIn('미집계', text)
                self.assertEqual('기사 목록' in text, news_available)
                self.assertNotIn('결과표', text)
                self.assertNotIn('차트', text)

    def test_explicit_zero_survives_custom_statistics_fallback(self):
        state = dev_state(DEV['06-partial'])
        state.results['t1.sql'].digest = {'kind': 'LLM_SQL', 'columns': [{'key': '수출액', 'unit': 'USD'}],
                                           'rows': [{'수출액': 0}]}
        with self.assertLogs('backend.app.agents.runtime', 'WARNING'):
            compose(state, ValueError('draft'), ValueError('draft'))
        self.assertIn('0달러', state.draft.sentences()[0])
        self.assertIn('시간', state.draft.sentences()[1])

    def test_old_uncorrected_draft_with_invalid_citation_cannot_be_restored(self):
        state = dev_state(DEV['06-partial'])
        state.draft = Draft(conclusion=[{'task_id': 't1', 'sentences': ['근거가 없는 인용[8]']}], turn_summary='')
        state.verification.regeneration_count = 1
        with self.assertLogs('backend.app.agents.runtime', 'WARNING'):
            client = compose(state, TimeoutError())
        client.parse.assert_called_once()
        self.assertNotIn('[8]', ' '.join(state.draft.sentences()))
        self.assertIn('2,610만 달러', state.draft.sentences()[0])
