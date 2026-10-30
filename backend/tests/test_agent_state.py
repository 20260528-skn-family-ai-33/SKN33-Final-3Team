"""에이전트 state 계약: 체크포인트 왕복, 하위 질문 계획, 되묻기 일관성, 차트 모양, 크기 상한."""
import unittest
from datetime import datetime
from decimal import Decimal

from pydantic import ValidationError

from backend.app.schemas.rag import HsProduct
from backend.app.agents.state import (AgentState, ChartSpec, Conditions, Interests, MAX_CONCLUSION_SENTENCES, MAX_DIGEST_BYTES,
                                      MAX_EXPLANATION_SENTENCES, MAX_INTEREST_COUNTRIES, MAX_INTEREST_HS, MAX_LABEL_CHARS,
                                      MAX_LIST_ITEMS, MAX_RECENT_MONTHS, MAX_TEXT_CHARS, PlannerSlots, SessionContext,
                                      ToolResult, TurnSummary, TaskSlots, shorten_name, shorten_names)

CAUSE = {'task_id': 't1', 'intent': 'CAUSE', 'question': '베트남 화장품 수출 왜 늘었어?', 'steps': [
    {'step_id': 't1.check', 'tool': 'check_conditions'},
    {'step_id': 't1.sql', 'tool': 'query_trade_data', 'depends_on': ['t1.check']},
    {'step_id': 't1.news', 'tool': 'search_articles', 'depends_on': ['t1.check']},
    {'step_id': 't1.chart', 'tool': 'build_chart', 'depends_on': ['t1.sql']}]}
RANKING = {'task_id': 't2', 'intent': 'RANKING', 'question': '화장품 수출 상위 10개국', 'steps': [
    {'step_id': 't2.check', 'tool': 'check_conditions'},
    {'step_id': 't2.sql', 'tool': 'query_trade_data', 'depends_on': ['t2.check']}]}
PENDING = {'clarification_id': 'c1', 'task_id': 't1', 'kind': 'HS_ITEM', 'condition_key': 't1:hs:화장품',
           'message': '품목을 고르세요', 'asked_at': datetime(2026, 10, 29, 9, 1),
           'choices': [{'choice_id': '330499', 'label': '기타 화장품',
                        'patch': {'hs_items': [{'hs_code': '330499', 'hs_ver': 'HS2022'}]}}]}


def state(tasks=(CAUSE,), **extra):
    base = {'input': {'job_id': '11', 'session_id': '3', 'user_id': 7, 'question': '베트남 화장품 수출 왜 늘었어?',
                      'context': {'session_id': '3'}, 'received_at': datetime(2026, 10, 29, 9)},
            'plan': {'tasks': list(tasks)}}
    return AgentState.model_validate({**base, **extra})


def chart(**extra):
    return {'chart_id': 'c1', 'task_id': 't1', 'type': 'line', 'title': '베트남 화장품 수출 추이', 'unit': 'USD', 'source': '관세청',
            'result_id': 'r1', 'labels': ['2025-08', '2025-09'], 'series': [{'name': '수출액', 'values': [1.0, 2.0]}], **extra}


class AgentStateTest(unittest.TestCase):
    def test_chart_references_must_use_the_same_result(self):
        selector = {'result_id': 'r1', 'group_by': 'COUNTRY', 'country_codes': ['VN']}
        self.assertEqual(ToolResult(status='OK', result_id='r1', chart=chart(), chart_selector=selector).result_id, 'r1')
        for fields in ({'chart': chart(), 'chart_selector': {**selector, 'result_id': 'r2'}},
                       {'result_id': 'r2', 'chart': chart()},
                       {'result_id': 'r2', 'chart_selector': selector}):
            with self.assertRaises(ValidationError):
                ToolResult(status='OK', **fields)
        self.assertIsNotNone(ToolResult(status='OK', chart_selector=selector).chart_selector)

    def test_recovery_preserves_charged_execution_budget(self):
        from backend.app.agents.state import RunBudget
        s = state(run_budget={'charged_seconds': 65, 'recovery_count': 1},
                  round_started_at=datetime(2026, 10, 30, 9))
        restored = AgentState.model_validate_json(s.model_dump_json())
        self.assertEqual(restored.run_budget.remaining_seconds(), 115)
        self.assertEqual(restored.run_budget.recovery_count, 1)
        # 벽시계로 10분이 지났다는 이유로 차감하거나 재개할 때 180초로 초기화하지 않는다.
        self.assertEqual(restored.round_started_at, s.round_started_at)
        self.assertEqual(RunBudget(charged_seconds=181).remaining_seconds(), 0)
        for fields in ({'charged_seconds': -1}, {'recovery_count': -1}, {'charged_seconds': float('inf')}):
            with self.assertRaises(ValidationError):
                RunBudget(**fields)

    def test_task_limit_has_explicit_guidance_before_plan_creation(self):
        from backend.app.agents.state import task_count_error
        self.assertIsNone(task_count_error(3))
        self.assertEqual(task_count_error(4), '한 번에 3개 이하로 나눠 질문해 주세요.')

    def test_share_basis_is_distinct_from_ranking_group(self):
        slots = TaskSlots(share_basis='FOREIGN_IMPORT_MARKET', ranking_group='COUNTRY')
        self.assertEqual(TaskSlots.model_validate_json(slots.model_dump_json()), slots)
        self.assertIsNone(PlannerSlots().share_basis)
        s = state(status='NEEDS_INPUT', pending={**PENDING, 'kind': 'SHARE_BASIS',
                  'condition_key': 't1:share_basis', 'choices': []})
        self.assertEqual(s.pending.kind, 'SHARE_BASIS')
        with self.assertRaises(ValidationError):
            TaskSlots(share_basis='UNKNOWN')

    def test_conclusions_cover_each_task_in_plan_order(self):
        draft = {'conclusion': [{'task_id': 't1', 'sentences': ['첫 답변입니다.', '추가 설명입니다.']},
                                {'task_id': 't2', 'sentences': ['두 번째 답변입니다.']}],
                 'explanation': ['공통 설명입니다.'], 'turn_summary': ''}
        s = state([CAUSE, RANKING], draft=draft)
        self.assertEqual(s.draft.sentences(), ['첫 답변입니다.', '추가 설명입니다.', '두 번째 답변입니다.', '공통 설명입니다.'])
        self.assertEqual(AgentState.model_validate_json(s.model_dump_json()), s)
        for entries in (draft['conclusion'][:1], list(reversed(draft['conclusion'])),
                        [draft['conclusion'][0]] * 2,
                        [draft['conclusion'][0], {'task_id': 't9', 'sentences': ['없는 질문입니다.']}]):
            with self.assertRaises(ValidationError):
                state([CAUSE, RANKING], draft={**draft, 'conclusion': entries})

    def test_report_preview_preserves_formats_and_conditions(self):
        from backend.app.agents.state import ReportRequest, Task
        request = {'preview_id': 'p1', 'expires_at': datetime(2026, 10, 30, 9, 10),
                   'conditions': {'country_codes': ['VN']}, 'formats': ['PDF', 'DOCX']}
        r = ReportRequest.model_validate(request)
        self.assertFalse(r.confirmed)
        self.assertEqual(ReportRequest.model_validate_json(r.model_dump_json()), r)
        for formats in ([], ['PDF', 'PDF'], ['XLSX']):
            with self.assertRaises(ValidationError):
                ReportRequest.model_validate({**request, 'formats': formats})
        with self.assertRaises(ValidationError):
            ReportRequest.model_validate({**request, 'conditions': {'trade_flow': 'IMPORT'}})
        task = {'task_id': 't1', 'intent': 'REPORT', 'question': '리포트',
                'conditions': request['conditions'], 'report_request': request}
        self.assertEqual(Task.model_validate(task).report_request.formats, ['PDF', 'DOCX'])
        with self.assertRaises(ValidationError):
            Task.model_validate({**task, 'conditions': {'country_codes': ['US']}})

    def test_chart_selector_keeps_targets_without_all_chart_payloads(self):
        from backend.app.agents.state import ChartSelector
        selector = {'result_id': 'r1', 'group_by': 'COUNTRY', 'country_codes': ['VN', 'US']}
        r = ToolResult(status='OK', chart=chart(), chart_selector=selector)
        self.assertEqual(ToolResult.model_validate_json(r.model_dump_json()), r)
        for extra in ({'country_codes': []}, {'country_codes': ['VN', 'VN']}, {'group_by': 'PAIR'}):
            with self.assertRaises(ValidationError):
                ChartSelector.model_validate({**selector, **extra})
        # ML 결과표에는 HS6 행만 있으므로 HS4는 거부한다(D-74)
        hs6 = {'result_id': 'r1', 'group_by': 'HS6', 'hs_items': [{'hs_code': '330499', 'hs_ver': 'HS2022'}]}
        self.assertEqual(ChartSelector.model_validate(hs6).hs_items[0].hs_code, '330499')
        with self.assertRaises(ValidationError):
            ChartSelector.model_validate({**hs6, 'hs_items': [{'hs_code': '3304', 'hs_ver': 'HS2022'}]})
        self.assertEqual(ChartSpec.model_validate(chart(granularity='QUARTER')).granularity, 'QUARTER')

    def test_slots_preserve_omitted_and_explicit_clear(self):
        omitted = TaskSlots()
        cleared = TaskSlots(products=[], countries=[])
        self.assertIsNone(omitted.products)
        self.assertEqual(cleared.products, [])
        self.assertEqual(TaskSlots.model_validate_json(cleared.model_dump_json()), cleared)
        self.assertIsNone(TaskSlots.model_validate_json(omitted.model_dump_json()).countries)
        with self.assertRaises(ValidationError):
            TaskSlots(period={'start': '2025-01-01', 'end': '2025-12-01'}, recent_months=12)
        with self.assertRaises(ValidationError):
            TaskSlots(compare_period={'start': '2024-01-01', 'end': '2024-12-01'})

    def test_report_wait_requires_running_report_step(self):
        task = {'task_id': 't1', 'intent': 'REPORT', 'question': '리포트 생성', 'steps': [
            {'step_id': 't1.check', 'tool': 'check_conditions', 'status': 'DONE'},
            {'step_id': 't1.report', 'tool': 'generate_report', 'depends_on': ['t1.check'], 'status': 'RUNNING'}]}
        wait = {'step_id': 't1.report', 'report_id': 'r1', 'started_at': datetime(2026, 10, 30, 9)}
        s = state([task, RANKING], status='WAITING_REPORT', report_wait=wait)
        self.assertEqual(AgentState.model_validate_json(s.model_dump_json()), s)
        self.assertIsNone(s.pending)
        self.assertEqual(s.ready_steps(), [])  # 대기 중 다음 하위 질문을 앞질러 실행하지 않는다
        s.status, s.report_wait = 'RUNNING', None
        s.plan.tasks[0].steps[1].status = 'DONE'
        s = AgentState.model_validate(s.model_dump())
        self.assertEqual([step.step_id for step in s.ready_steps()], ['t2.check'])
        for status, reference in [('RUNNING', wait), ('WAITING_REPORT', None)]:
            with self.assertRaises(ValidationError):
                state([task], status=status, report_wait=reference)
        with self.assertRaises(ValidationError):
            state(status='WAITING_REPORT', report_wait={**wait, 'step_id': 't1.sql'})

    def test_task_outcomes_survive_partial_failure(self):
        s = state([CAUSE, RANKING])
        s.plan.tasks[0].steps[0].status = 'FAILED'   # t1 조건 확인 실패 → t1만 NO_DATA로 끝낸다(D-72)
        s.skip_blocked()
        s.plan.tasks[0].answer_status = 'NO_DATA'
        restored = AgentState.model_validate_json(s.model_dump_json())
        self.assertEqual([t.answer_status for t in restored.plan.tasks], ['NO_DATA', None])
        self.assertEqual([s.step_id for s in restored.ready_steps()], ['t2.check'])
        self.assertFalse(restored.steps_finished())
        for step in restored.plan.tasks[1].steps:
            step.status = 'DONE'
        restored.plan.tasks[1].answer_status = 'ANSWERED'
        done = AgentState.model_validate(restored.model_dump())
        self.assertTrue(done.steps_finished())
        self.assertEqual([t.answer_status for t in done.plan.tasks], ['NO_DATA', 'ANSWERED'])

    def test_answer_status_only_after_steps_finish(self):
        with self.assertRaises(ValidationError):  # 단계가 남았는데 결과를 정함
            state([{**CAUSE, 'answer_status': 'ANSWERED'}])
        with self.assertRaises(ValidationError):  # 끝난 작업인데 결과 없는 하위 질문
            state(status='SUCCEEDED')

    def test_dispatch_does_not_compose_while_waiting(self):
        # ready_steps()가 비어도 리포트·되묻기 대기 중이거나 단계가 남았으면 compose로 가지 않는다(D-76)
        task = {'task_id': 't1', 'intent': 'REPORT', 'question': '리포트 생성', 'steps': [
            {'step_id': 't1.check', 'tool': 'check_conditions', 'status': 'DONE'},
            {'step_id': 't1.report', 'tool': 'generate_report', 'depends_on': ['t1.check'], 'status': 'RUNNING'}]}
        waiting = state([task], status='WAITING_REPORT',
                        report_wait={'step_id': 't1.report', 'report_id': 'r1', 'started_at': datetime(2026, 10, 30, 9)})
        self.assertEqual((waiting.ready_steps(), waiting.steps_finished()), ([], False))
        self.assertFalse(state(status='NEEDS_INPUT', pending=PENDING).steps_finished())
        self.assertFalse(state(status='RUNNING').steps_finished())
        self.assertTrue(state([{'task_id': 't1', 'intent': 'OUT_OF_SCOPE', 'question': '날씨 알려줘'}]).steps_finished())
        # 실패를 둔 노드가 skip_blocked()를 빠뜨려도 dispatch가 멈추지 않는다
        s = state(status='RUNNING')
        for step, status in zip(s.plan.tasks[0].steps, ['DONE', 'FAILED', 'DONE', 'PENDING']):
            step.status = status
        self.assertEqual(s.ready_steps(), [])
        self.assertTrue(s.steps_finished())
        self.assertEqual(s.plan.tasks[0].steps[3].status, 'SKIPPED')   # sql 실패에 막힌 chart

    def test_planner_slots_turn_limits_into_guidance(self):
        # 플래너 출력은 상한을 넘어도 받고 안내 문구로 바꾼다. 저장용 TaskSlots는 거부한다(D-73)
        many = PlannerSlots(products=[f'품목{n}' for n in range(6)], countries=['가' * 121], recent_months=12000)
        self.assertEqual(len(many.limit_errors()), 3)   # 품목 개수, 이름 길이, 최근 개월 수
        self.assertEqual(PlannerSlots(products=['화장품'], recent_months=12).limit_errors(), [])
        with self.assertRaises(ValidationError): TaskSlots(products=[f'품목{n}' for n in range(6)])
        with self.assertRaises(ValidationError): TaskSlots(recent_months=MAX_RECENT_MONTHS + 1)
        # 상한을 넘은 하위 질문은 단계 없이 NO_DATA로 끝내고 나머지는 계속한다
        over = {'task_id': 't1', 'intent': 'STATISTICS', 'question': '품목 6개 수출', 'answer_status': 'NO_DATA'}
        self.assertEqual([x.step_id for x in state([over, RANKING]).ready_steps()], ['t2.check'])
        with self.assertRaises(ValidationError): state([{**over, 'answer_status': None}])  # 결과 없이 단계도 없음

    def test_planner_cannot_confirm_latest_prediction(self):
        # 최신 기준 예측 확인(MODEL_PERIOD)은 ask_user만 기록한다. 플래너 출력에는 이 칸이 없다(D-74)
        with self.assertRaises(ValidationError): PlannerSlots.model_validate({'latest_prediction_confirmed': True})
        self.assertTrue(TaskSlots(latest_prediction_confirmed=True).latest_prediction_confirmed)

    def test_checkpoint_round_trip(self):
        task = {**CAUSE, 'conditions': {'hs_items': [{'hs_code': '3304', 'hs_ver': 'HS2022'}], 'country_codes': ['VN'],
                                        'period': {'start': '2024-10-01', 'end': '2025-09-01'}}}
        s = state([task], results={'t1.sql': {'status': 'OK', 'result_id': 'r1', 'digest': {'total_usd': 1.5}},
                                   't1.chart': {'status': 'OK', 'chart': chart()}})
        self.assertEqual(AgentState.model_validate_json(s.model_dump_json()), s)

    def test_ready_steps_across_tasks(self):
        s = state([CAUSE, RANKING])
        self.assertEqual([x.step_id for x in s.ready_steps()], ['t1.check', 't2.check'])
        s.plan.tasks[0].steps[0].status = 'DONE'
        self.assertEqual([x.step_id for x in s.ready_steps()], ['t1.sql', 't1.news', 't2.check'])

    def test_failure_skips_dependents_transitively(self):
        s = state([CAUSE, RANKING])
        s.plan.tasks[0].steps[0].status = 'FAILED'
        self.assertEqual(s.skip_blocked(), ['t1.sql', 't1.news', 't1.chart'])  # chart는 sql을 거쳐 check에 의존
        self.assertEqual([x.step_id for x in s.ready_steps()], ['t2.check'])
        s.plan.tasks[0].steps[3].status = 'PENDING'  # skip_blocked를 빠뜨려도 SKIPPED 의존은 실행되지 않는다
        self.assertEqual([x.step_id for x in s.ready_steps()], ['t2.check'])

    def test_plan_rules(self):
        with self.assertRaises(ValidationError):  # 하위 질문 사이 의존
            state([CAUSE, {**RANKING, 'steps': [RANKING['steps'][0], {**RANKING['steps'][1], 'depends_on': ['t1.sql']}]}])
        with self.assertRaises(ValidationError):  # check_conditions로 시작하지 않음
            state([{**RANKING, 'steps': RANKING['steps'][1:]}])
        with self.assertRaises(ValidationError):  # 하위 질문은 3개까지
            state([CAUSE, RANKING, {**RANKING, 'task_id': 't3'}, {**RANKING, 'task_id': 't4'}])
        # 시장 유형은 지원 예정 안내만, 단계 없음
        self.assertEqual(state([{'task_id': 't1', 'intent': 'MARKET_TYPE', 'question': '베트남 화장품 시장 유형'}])
                         .plan.tasks[0].steps, [])

    def test_pending_only_while_waiting(self):
        self.assertEqual(state(status='NEEDS_INPUT', pending=PENDING).pending.kind, 'HS_ITEM')
        with self.assertRaises(ValidationError): state(status='NEEDS_INPUT')
        with self.assertRaises(ValidationError): state(status='RUNNING', pending=PENDING)
        with self.assertRaises(ValidationError): state(status='NEEDS_INPUT', pending={**PENDING, 'task_id': 't9'})

    def test_ask_limit_per_condition(self):
        self.assertEqual(state(ask_counts={'t1:hs:화장품': 2}).ask_counts['t1:hs:화장품'], 2)
        with self.assertRaises(ValidationError): state(ask_counts={'t1:hs:화장품': 3})

    def test_chart_shape(self):
        self.assertEqual(ChartSpec.model_validate(chart()).type, 'line')
        with self.assertRaises(ValidationError): ChartSpec.model_validate(chart(series=[{'name': 'a', 'values': [1.0]}]))
        with self.assertRaises(ValidationError): ChartSpec.model_validate(chart(type='grouped_bar'))
        with self.assertRaises(ValidationError): ChartSpec.model_validate(chart(labels=[str(n) for n in range(61)]))
        # 40자에서 자른 뒤 겹치는 라벨·시리즈 이름은 거부한다(품목명은 shorten_names로 만든다)
        long = '가' * 45
        with self.assertRaises(ValidationError): ChartSpec.model_validate(chart(labels=[long + 'A', long + 'B']))
        with self.assertRaises(ValidationError):
            ChartSpec.model_validate(chart(series=[{'name': long + '1', 'values': [1.0, 2.0]}, {'name': long + '2', 'values': [1.0, 2.0]}]))

    def test_rows_do_not_go_into_state(self):
        rows = [{'month': f'2025-{m:02}', 'usd': 1.0, 'country': 'VN' * 50} for m in range(1, 13)] * 10
        with self.assertRaises(ValidationError):
            state(results={'t1.sql': {'status': 'OK', 'result_id': 'r1', 'digest': {'rows': rows}}})
        self.assertGreater(len(str(rows)), MAX_DIGEST_BYTES)

    def test_digest_only_takes_json_values(self):
        # Decimal은 JSON 왕복에서 문자열, NaN은 null이 되므로 tool이 바꿔 넣어야 한다
        with self.assertRaises(ValidationError): ToolResult(status='OK', digest={'total_usd': Decimal('1.5')})
        with self.assertRaises(ValidationError): ToolResult(status='OK', digest={'growth': float('nan')})
        with self.assertRaises(ValidationError): ChartSpec.model_validate(chart(series=[{'name': 'a', 'values': [1.0, float('nan')]}]))
        r = ToolResult(status='OK', digest={'total_usd': 1.5, 'top': [{'country': 'VN', 'usd': 2}]})
        self.assertEqual(ToolResult.model_validate_json(r.model_dump_json()), r)

    def test_largest_allowed_state_fits(self):
        # 모든 칸을 허용 최대로 채운다. state에 칸을 더하면 여기에도 최대값으로 넣는다.
        q, text, label, args = '가' * 4000, '가' * MAX_TEXT_CHARS, '가' * MAX_LABEL_CHARS, {'a': 'x' * 500}   # 질문은 한글 12KB
        texts, labels = [text] * MAX_LIST_ITEMS, [label] * MAX_LIST_ITEMS
        period = {'start': '2024-10-01', 'end': '2025-09-01'}
        full = {'hs_items': [{'hs_code': f'3304{n:02}', 'hs_ver': 'HS2022'} for n in range(5)],
                'country_codes': [f'C{n:02}' for n in range(20)], 'period': period, 'compare_period': period,
                'article_period': {'start': '2024-09-01', 'end': '2025-10-31'}}
        turns = [{'question': q, 'intent': 'STATISTICS', 'conditions': full, 'summary': '가' * 200,
                  'answered_at': datetime(2026, 10, 29, 9, n)} for n in range(5)]
        tasks, results = [], {}
        for t in ('t1', 't2', 't3'):
            tasks.append({**CAUSE, 'task_id': t, 'question': q, 'conditions': full, 'applied_defaults': texts,
                          'slots': {'products': ['가' * 120] * 5, 'countries': ['나' * 120] * 20,
                                    'comparison_requested': True, 'period': period, 'compare_period': period,
                                    'article_period': full['article_period']}, 'steps': [
                {**s, 'step_id': s['step_id'].replace('t1', t), 'args': args,
                 'depends_on': [d.replace('t1', t) for d in s.get('depends_on', [])]} for s in CAUSE['steps']]})
            common = {'status': 'OK', 'source': label, 'checks': labels, 'error': text, 'digest': {'s': '가' * 2700}}  # digest 상한 근처
            results[f'{t}.sql'] = {**common, 'result_id': 'r1', 'period': period}
            results[f'{t}.news'] = {**common, 'article_ids': list(range(20)), 'article_period': full['article_period']}
            results[f'{t}.chart'] = {**common, 'chart_selector': {'result_id': 'r1', 'group_by': 'PAIR',
                'country_codes': full['country_codes'], 'hs_items': full['hs_items']}, 'chart': chart(
                task_id=t, title='가' * 40, unit='가' * 20, source=label,
                labels=[label[:-2] + f'{n:02}' for n in range(60)],   # 한 차트 안에서 라벨·시리즈 이름은 겹치지 않는다
                series=[{'name': label[:-1] + str(n), 'values': [123456789012.5] * 60} for n in range(5)])}
        n_sentences = MAX_CONCLUSION_SENTENCES + MAX_EXPLANATION_SENTENCES
        # 숫자는 문장(200자) 안에서 찾으므로 한 문장에 한 글자 숫자 100개가 최대다
        checks = [{'index': n, 'numbers': ['1'] * (MAX_TEXT_CHARS // 2), 'status': 'PASS'} for n in range(n_sentences)]
        s = AgentState.model_validate({
            'input': {'job_id': '11', 'session_id': '3', 'user_id': 7, 'question': q,
                      'context': {'session_id': '3', 'conditions': full, 'recent': turns},
                      'interests': {'hs_items': [{'hs_code': f'{n:06}', 'hs_ver': 'HS2022'} for n in range(MAX_INTEREST_HS)],
                                    'country_codes': [f'C{n:03}' for n in range(MAX_INTEREST_COUNTRIES)]},
                      'received_at': datetime(2026, 10, 29, 9)},
            'status': 'NEEDS_INPUT', 'plan': {'tasks': tasks}, 'results': results, 'warnings': texts,
            'pending': {**PENDING, 'condition_key': text, 'message': text, 'remaining': [label] * 5,
                        'selected': full['hs_items'], 'choices': [{'choice_id': str(n), 'label': text, 'patch': args} for n in range(5)]},
            'draft': {'conclusion': [{'task_id': t, 'sentences': [text, text]} for t in ('t1', 't2', 't3')], 'explanation': [text] * MAX_EXPLANATION_SENTENCES,
                      'turn_summary': '가' * 200, 'title': '가' * 20},
            'verification': {'checks': checks}})
        self.assertEqual(len(s.input.context.recent[0].question), 200)
        self.assertEqual(len(s.plan.tasks[0].question), 500)

    def test_display_text_is_cut_but_llm_sentences_are_rejected(self):
        hs4, text = '가' * 347, '가' * MAX_TEXT_CHARS  # 시드에서 가장 긴 HS4 품목명 길이
        c = ChartSpec.model_validate(chart(title=hs4, labels=[hs4, '화장품'], series=[{'name': hs4, 'values': [1.0, 2.0]}]))
        self.assertEqual((len(c.title), len(c.labels[0]), len(c.series[0].name)), (MAX_LABEL_CHARS,) * 3)
        p = state(status='NEEDS_INPUT', warnings=[hs4] * 20, pending={**PENDING, 'message': hs4, 'remaining': [hs4],
                                                                       'choices': [{**PENDING['choices'][0], 'label': hs4}]})
        self.assertEqual((len(p.warnings), len(p.warnings[0]), len(p.pending.choices[0].label), len(p.pending.remaining[0])),
                         (MAX_LIST_ITEMS, MAX_TEXT_CHARS, MAX_TEXT_CHARS, MAX_TEXT_CHARS))
        many = {'hs_items': [{'hs_code': f'{n:06}', 'hs_ver': 'HS2022'} for n in range(150)]}
        self.assertEqual(len(state(input={**state().input.model_dump(), 'interests': many}).input.interests.hs_items),
                         MAX_INTEREST_HS)  # 저장 개수 제한이 없는 관심사는 앞에서부터 남긴다
        # compose가 쓰는 문장은 자르지 않고 거부해 재시도한다
        with self.assertRaises(ValidationError): state(draft={'conclusion': [{'task_id': 't1', 'sentences': [text + '가']}], 'turn_summary': ''})
        with self.assertRaises(ValidationError): state(draft={'conclusion': [{'task_id': 't1', 'sentences': [text] * 3}], 'turn_summary': ''})

    def test_interests_over_condition_limit_are_selected(self):
        hs = [{'hs_code': f'{n:06}', 'hs_ver': 'HS2022'} for n in range(6)]
        self.assertTrue(Interests(hs_items=hs[:5], country_codes=[f'C{n:02}' for n in range(20)]).fits_conditions())
        self.assertFalse(Interests(hs_items=hs).fits_conditions())  # 품목 6개: 조건(D-24)에 그대로 못 넣는다
        self.assertFalse(Interests(country_codes=[f'C{n:02}' for n in range(21)]).fits_conditions())
        with self.assertRaises(ValidationError): Conditions(hs_items=hs)
        # 골라 받는 되묻기는 후보가 state에 있으므로 선택지는 '전체 범위'만 둔다
        select = {**PENDING, 'kind': 'INTEREST_SELECT', 'condition_key': 't1:interest',
                  'choices': [{'choice_id': 'GLOBAL', 'label': '전체 범위', 'patch': {'hs_items': [], 'country_codes': []}}]}
        self.assertEqual(state(status='NEEDS_INPUT', pending=select).pending.kind, 'INTEREST_SELECT')

    def test_sentence_checks_point_at_draft_sentences(self):
        draft = {'conclusion': [{'task_id': 't1', 'sentences': ['베트남 수출은 12% 늘었습니다.']}], 'explanation': ['화장품이 늘었습니다.'], 'turn_summary': ''}
        ok = state(draft=draft, verification={'checks': [{'index': 0, 'numbers': ['12%'], 'status': 'PASS'},
                                                         {'index': 1, 'status': 'PASS'}]})
        self.assertEqual(ok.draft.sentences()[ok.verification.checks[0].index], '베트남 수출은 12% 늘었습니다.')
        with self.assertRaises(ValidationError): state(draft=draft, verification={'checks': [{'index': 2, 'status': 'PASS'}]})
        with self.assertRaises(ValidationError): state(verification={'checks': [{'index': 0, 'status': 'PASS'}]})

    def test_finalize_revalidates_models_changed_in_place(self):
        # 인스턴스를 그대로 넘기면 안쪽 칸을 다시 보지 않으므로 finalize는 dump한 값으로 검증한다
        s = state(results={'t1.sql': {'status': 'OK'}})
        s.results['t1.sql'].status = 'FAILED'
        with self.assertRaises(ValidationError): AgentState.model_validate(s.model_dump())
        s = state()
        s.ask_counts['t1:hs:화장품'] = 3
        with self.assertRaises(ValidationError): AgentState.model_validate(s.model_dump())

    def test_conditions(self):
        with self.assertRaises(ValidationError):
            Conditions.model_validate({'hs_items': [{'hs_code': '330499', 'hs_ver': 'HS2022'}] * 2})
        with self.assertRaises(ValidationError): Conditions.model_validate({'country_codes': ['VN', 'VN']})
        # 기사 기간은 일 단위
        period = Conditions.model_validate({'article_period': {'start': '2026-07-29', 'end': '2026-10-28'}}).article_period
        self.assertEqual(period.start.day, 29)
        # 기사 결과 기간은 따로 둬서 1일~1일 기간도 JSON 왕복 뒤 일 단위로 남는다
        r = ToolResult(status='OK', article_period={'start': '2026-07-01', 'end': '2026-10-01'})
        self.assertEqual(ToolResult.model_validate_json(r.model_dump_json()), r)
        with self.assertRaises(ValidationError): ToolResult(status='OK', period={'start': '2026-07-29', 'end': '2026-10-28'})
        # 기존 채팅 코드가 MySQL context_json에 저장한 말일 기준 기간도 읽는다
        stored = Conditions.model_validate({'period': {'start': '2024-10-01', 'end': '2025-09-30'},
                                            'compare_period': {'start': '2023-10-01', 'end': '2024-02-29'}})
        self.assertEqual((stored.period.end.isoformat(), stored.compare_period.end.isoformat()), ('2025-09-01', '2024-02-01'))
        with self.assertRaises(ValidationError): Conditions.model_validate({'period': {'start': '2025-01-01', 'end': '2025-09-15'}})

    def test_context_keeps_last_five_turns_and_latest_conditions(self):
        context = SessionContext(session_id='3')
        vn = {'country_codes': ['VN'], 'period': {'start': '2025-01-01', 'end': '2025-09-01'}}
        for n in range(7):
            turn = TurnSummary.model_validate({'question': f'q{n}', 'intent': 'STATISTICS',
                                               'conditions': vn if n == 5 else None, 'answered_at': datetime(2026, 10, 29, 9, n)})
            context = context.remember(turn)
        self.assertEqual([t.question for t in context.recent], ['q2', 'q3', 'q4', 'q5', 'q6'])
        self.assertEqual(context.conditions.country_codes, ['VN'])  # 조건 없는 답변(q6)은 최근 조건을 지우지 않는다
        self.assertEqual(SessionContext.model_validate_json(context.model_dump_json()), context)

    def test_result_must_belong_to_plan_and_failure_needs_reason(self):
        with self.assertRaises(ValidationError): state(results={'t9.sql': {'status': 'OK'}})
        with self.assertRaises(ValidationError): state(results={'t1.sql': {'status': 'FAILED'}})
        with self.assertRaises(ValidationError):  # 차트의 task_id는 그 단계의 하위 질문
            state([CAUSE, RANKING], results={'t1.chart': {'status': 'OK', 'chart': chart(task_id='t2')}})


class ShortenNameTest(unittest.TestCase):
    def test_long_product_name_keeps_head_and_tail(self):
        name = '전동축[캠샤프트(cam shaft)와 크랭크샤프트(crank shaft)를 포함한다], ' * 3 + '클러치(clutch)와 유니버설조인트'
        short = shorten_name(name)
        self.assertEqual(len(short), MAX_LABEL_CHARS)
        self.assertEqual(short[:21], name[:20] + '…')
        self.assertTrue(name.endswith(short[21:]))
        self.assertEqual(shorten_name('화장품'), '화장품')
        self.assertEqual(len(shorten_name('x' * 50, head=45)), MAX_LABEL_CHARS)  # 앞부분이 길어도 상한을 넘지 않는다

    def test_names_that_collide_after_shortening_get_hs_code(self):
        a = '전동축[캠샤프트(cam shaft)와 크랭크샤프트(crank shaft)를 포함한다]·베어링하우징과 플레인 샤프트 베어링, 클러치'
        b = a.replace('베어링하우징', '베어링 하우징')   # 가운데만 다름
        self.assertEqual(shorten_name(a), shorten_name(b))
        hs = lambda code, ver='HS2022': HsProduct(hs_code=code, hs_ver=ver)
        labels = shorten_names([a, b, '화장품'], [hs('8483'), hs('8484'), hs('3304')])
        self.assertEqual(len(set(labels)), 3)
        self.assertTrue(labels[0].endswith('(8483)') and labels[1].endswith('(8484)'))
        self.assertTrue(all(len(x) <= MAX_LABEL_CHARS for x in labels))
        self.assertEqual(labels[2], '화장품')   # 겹치지 않으면 코드를 붙이지 않는다
        # 같은 코드가 판본만 달리 들어오면 판본까지 붙인다
        labels = shorten_names([a, a], [hs('8483', 'HS2017'), hs('8483')])
        self.assertTrue(labels[0].endswith('(8483 HS2017)') and labels[1].endswith('(8483 HS2022)'))
        self.assertTrue(all(len(x) <= MAX_LABEL_CHARS for x in labels))


if __name__ == '__main__':
    unittest.main()
