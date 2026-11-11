import json
import unittest
from contextlib import nullcontext
from unittest.mock import MagicMock, patch

from backend.app.agents.compose import ARTICLE_PROMPT, compose_input, numbered_articles
from backend.app.agents.runtime import Services
from backend.app.agents.state import AgentState, Draft, MAX_DIGEST_BYTES, SentenceCheck, ToolResult
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
                self.assertEqual('통관 신고서 양식 개정' in text, news_available)
                self.assertEqual('[1]' in text, news_available)
                self.assertEqual('인과관계는 판단할 수 없습니다' in text, news_available)
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

    def test_rewrite_preserves_verified_sentence_and_rejects_unknown_amount(self):
        for text in (False, True):
            for new, accepted in [('수출액은 0달러입니다.', True), ('수출액은 777달러입니다.', False)]:
                with self.subTest(text=text, new=new):
                    state = dev_state(DEV['02-zero-null'])
                    old = '수출액은 10달러입니다.'
                    kept = '표를 확인하세요.'
                    def draft(sentence):
                        return Draft(conclusion=[{'task_id': 't1', 'sentences': [sentence, kept]}],
                                     turn_summary='')
                    compose(state, draft(old), text=text)
                    self.assertEqual(state.fixed_conclusion_ids, [])
                    state.verification.checks = [SentenceCheck(index=0, status='MISMATCH', numbers=['10달러'])]
                    state.verification.regeneration_count = 1
                    client = compose(state, draft(new), text=text)
                    payload = client.parse.call_args.kwargs['payload']
                    self.assertEqual(payload['regeneration']['draft']['conclusion'][0]['sentences'], [old, kept])
                    self.assertEqual(state.draft.sentences(), [new if accepted else old, kept])

    def test_action_rewrite_uses_only_addressed_span(self):
        from backend.app.agents.state import ClaimFeedback, WriterEdits

        old = '미국 수출은 전년 동월 대비 1.5% 감소했습니다.'
        phrase = '전년 동월 대비 1.5% 감소'
        start = old.index(phrase)
        for text in (False, True):
            for section in ('conclusion', 'summary'):
                for replacement, expected in [
                    ('전년 동월 대비 3% 증가', 'ACCEPTED_CORRECTION'),
                    ('전년 동월 대비 777% 증가', 'UNSUPPORTED_CORRECTION'),
                    ('전월 대비 1.5% 감소', 'REQUESTED_COMPARISON_CHANGED'),
                    (phrase, 'UNCHANGED_EDIT'),
                ]:
                    with self.subTest(text=text, section=section, replacement=replacement):
                        state = dev_state(DEV['02-zero-null'])
                        state.plan.tasks[0].question = '미국 수출은 전년 동월 대비 어떻게 변했나요?'
                        state.results['t1.sql'].digest = {'kind':'TREND', 'trade_flow':'EXPORT',
                            'rows':[{'country_name':'미국', 'yoy_pct':3, 'mom_pct':-1.5}]}
                        body = old if section == 'conclusion' else '검증된 본문입니다.'
                        state.draft = Draft(conclusion=[{'task_id':'t1','sentences':[body]}],
                                            turn_summary='유지할 요약' if section == 'conclusion' else '')
                        verification = state.verification
                        verification.regeneration_count = 1
                        verification.summary_status = 'PASS' if section == 'conclusion' else 'MISMATCH'
                        verification.failed_summary = old if section == 'summary' else ''
                        index = 0 if section == 'conclusion' else None
                        feedback = ClaimFeedback(section=section, task_id='t1', index=index, reason='COMPARISON',
                            passage=phrase, span=(start, start + len(phrase)), action='CORRECT', step_id='t1.sql')
                        verification.feedback = [feedback]
                        verification.confirmed_claims = [feedback.model_copy(update={
                            'span':(0,2), 'passage':'미국', 'action':'PRESERVE', 'reason':'MATCH'})]
                        result = WriterEdits(edits=[{'feedback_index':0, 'replacement':replacement}])
                        client = compose(state, result, text=text)
                        sent = client.parse.call_args.kwargs['payload']['regeneration']
                        self.assertEqual(sent['confirmed_claims'][0]['span'], [0,2])
                        self.assertEqual(state.writer_rewrite_result, expected)
                        amended = old.replace(phrase, replacement)
                        accepted = expected == 'ACCEPTED_CORRECTION'
                        self.assertEqual(state.draft.sentences(), [amended if accepted and section=='conclusion' else body])
                        self.assertEqual(state.draft.turn_summary, (amended if accepted else old) if section=='summary' else '유지할 요약')

    def test_protected_and_invalid_feedback_preserve_draft(self):
        from backend.app.agents.state import ClaimFeedback, WriterEdits

        for text in (False, True):
            for condition, expected in [
                ('review', 'PRESERVED_NO_CORRECTION'), ('overlap', 'PROTECTED_CLAIM'),
                ('outside', 'INVALID_LOCATION'), ('stale', 'STALE_SPAN'),
                ('snapshot_only', 'NO_WRITER_EVIDENCE'), ('date', 'PROTECTED_CITATION_OR_DATE'),
                ('timeout', 'MODEL_OR_SCHEMA_FAILURE'),
            ]:
                with self.subTest(text=text, condition=condition):
                    state = dev_state(DEV['04-context'])
                    line = '9월 3일 기사에서 확인했습니다.[1]'
                    state.draft = Draft(conclusion=[{'task_id':'t1','sentences':[line]}], turn_summary='')
                    first = state.draft.model_copy(deep=True)
                    start = line.index('3')
                    feedback = ClaimFeedback(section='conclusion', task_id='t1', index=0,
                        passage='3', span=(start, start+1), action='CORRECT', reason='VALUE', step_id='t1.sql')
                    if condition == 'review':
                        feedback.action = 'REVIEW'
                    elif condition == 'outside':
                        feedback.index = 3
                    elif condition == 'stale':
                        feedback.passage = '7'
                    elif condition == 'snapshot_only':
                        feedback.step_id = 't1.unknown'
                    elif condition == 'overlap':
                        state.verification.confirmed_claims = [feedback.model_copy(update={'action':'PRESERVE'})]
                    state.verification.feedback = [feedback]
                    state.verification.regeneration_count = 1
                    value = TimeoutError() if condition == 'timeout' else WriterEdits(edits=[
                        {'feedback_index':0,'replacement':'15'}])
                    client = compose(state, value, text=text)
                    self.assertEqual(client.parse.call_count, int(condition != 'review'))
                    self.assertEqual(state.writer_rewrite_result, expected)
                    self.assertEqual(state.draft, first)

    def test_multi_source_rewrite_binds_each_changed_claim_to_its_requested_step(self):
        from backend.app.agents.state import ClaimFeedback, WriterEdits

        original = '미국 수출액은 100달러이고 일본 수출액은 199달러입니다.'
        for text in (False, True):
            for replacement, step_id, accepted in [('200', 't1.jp', True), ('200', 't1.sql', False),
                                                    ('100', 't1.jp', False), ('201', 't1.jp', False)]:
                with self.subTest(text=text, replacement=replacement, step_id=step_id):
                    state = dev_state(DEV['01-large-export'])
                    task = state.plan.tasks[0]
                    sql = next(step for step in task.steps if step.tool == 'query_trade_data')
                    task.steps.append(sql.model_copy(update={'step_id':'t1.jp'}))
                    for key, country, amount in [('t1.sql','미국',100), ('t1.jp','일본',200)]:
                        state.results[key] = ToolResult(status='OK', result_id=key, digest={
                            'kind':'TREND', 'trade_flow':'EXPORT', 'rows':[{'country_name':country,'amount_usd':amount}]})
                    state.draft = Draft(conclusion=[{'task_id':'t1','sentences':[original]}], turn_summary='')
                    start = original.index('199')
                    state.verification.regeneration_count = 1
                    state.verification.feedback = [ClaimFeedback(section='conclusion', task_id='t1', index=0,
                        action='CORRECT', reason='VALUE', span=(start,start+3), passage='199', step_id=step_id,
                        targets=('일본',))]
                    compose(state, WriterEdits(edits=[{'feedback_index':0,'replacement':replacement}]), text=text)
                    self.assertEqual(state.draft.sentences(), [original.replace('199', replacement) if accepted else original])
                    self.assertEqual(state.writer_rewrite_result, 'ACCEPTED_CORRECTION' if accepted else 'UNSUPPORTED_CORRECTION')

    def test_partial_news_uses_task_owned_body_evidence_and_display_number(self):
        rows = [
            ('LOCAL_ISSUES', '다른 항구 소식은?', None, [article(11,'항구','선박이 출항했습니다.','direct_evidence')]),
            ('CAUSE', '물동량 변화가 창고 재개 때문인가요?', statistic([500]), [
                article(12,'창고 운영','창고가 운영을 재개했습니다.','direct_evidence')])]
        for sufficient in (True, False):
            state = dev_state(rows)
            task = state.plan.tasks[1]
            for step in task.steps:
                if step.tool == 'query_trade_data':
                    step.status = 'FAILED'
                    state.results[step.step_id] = ToolResult(status='NO_DATA', error='통계 조회 실패')
                elif step.tool == 'build_chart':
                    step.status = 'SKIPPED'
                elif step.tool == 'search_articles':
                    state.results[step.step_id].digest['items'][0]['evidence_sufficient'] = sufficient
            task.answer_status = state.task_outcome(task.task_id)
            _, fixed = compose_input(state, lambda _: {}, numbered_articles(state))
            answer = ' '.join(fixed[task.task_id].sentences)
            self.assertEqual('창고가 운영을 재개했습니다.[2]' in answer, sufficient)
            self.assertIn('통계 조회 실패', answer)
            self.assertNotIn('[1]', answer)
            self.assertNotIn('출항', answer)
            self.assertNotIn('500', answer)

    def test_multiple_article_partial_retries_uncited_answer_then_falls_back_to_evidence(self):
        rows = [article(11,'창고','창고가 운영을 재개했습니다.','direct_evidence'),
                article(12,'항구','선박이 출항했습니다.','context_reference')]
        state = dev_state([('CAUSE','창고 재개로 수출이 늘었나요?',statistic([500]),rows)])
        task = state.plan.tasks[0]
        for step in task.steps:
            if step.tool == 'query_trade_data':
                step.status = 'FAILED'
                state.results[step.step_id] = ToolResult(status='NO_DATA', error='통계 조회 실패')
            elif step.tool == 'build_chart':
                step.status = 'SKIPPED'
        task.answer_status = state.task_outcome(task.task_id)
        invalid = Draft(conclusion=[{'task_id':'t1','sentences':['기사 목록을 확인하세요.']}], turn_summary='')
        with self.assertLogs('backend.app.agents.runtime', 'WARNING'):
            client = compose(state, invalid, invalid)
        self.assertEqual(client.parse.call_count, 2)
        self.assertIn('창고가 운영을 재개했습니다.[1]', state.draft.sentences()[0])
        self.assertIn('인과관계는 판단할 수 없습니다', state.draft.sentences()[1])
