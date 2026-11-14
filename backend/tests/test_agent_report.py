"""Report Agent 평가 사례(이슈 #82): 채팅 API → 워커 → 그래프 → 리포트 접수·대기·완료를 실제 코드로 끝까지 돌린다.

LLM 플래너만 Mock으로 고정하고, 조건 확인·미리보기·승인·접수·대기 깨우기·답변 저장은 SQLite에서 실제로 실행한다.
설계 근거: docs/AGENT_DESIGN.md D-76·D-81·D-90·D-92·D-93·D-97, §14.6.
"""
import unittest
from datetime import date, timedelta
from unittest.mock import Mock

from sqlalchemy import select, update

from backend.app.agents.state import AgentState, Draft, PlannerOutput, ReportWait, ToolResult
from backend.app.api.common import now
from backend.app.api.database import Store
from backend.app.api.worker import run_once
from backend.tests import test_backend_api as fixtures

REPORT = [{'intent': 'REPORT', 'question': '수출 리포트 생성'}]
DONE_TEXT = '요청하신 리포트가 완성되었습니다.'


class ReportAgentTest(unittest.TestCase):
    setUp = fixtures.BackendApiTest.setUp
    tearDown = fixtures.BackendApiTest.tearDown
    signup = fixtures.BackendApiTest.signup
    post = fixtures.BackendApiTest.post

    # ── 진행 도우미 ──────────────────────────────────────────────
    def agent(self, tasks=REPORT):
        from langgraph.checkpoint.memory import InMemorySaver
        self.settings.values['API_CHAT_AGENT_ENABLED'] = 'true'
        self.db = self.app.state.database
        if not getattr(self.db, 'agent_checkpointer', None):
            self.db.agent_checkpointer = InMemorySaver()
        self.db.agent_client = Mock()

        def parse(**call):  # 계획은 고정값, 답변 초안은 LLM이 쓰는 하위 질문마다 숫자 없는 문장 하나
            if call['schema'] is Draft:
                return Draft(conclusion=[{'task_id': t['task_id'], 'sentences': ['조회 결과를 확인했습니다.']} for t in call['payload']['tasks']],
                             turn_summary='', title='수출 분석')
            if call['schema'].__name__ == 'ClarificationReading':   # 되묻기 직접 입력 해석: 선택지와 무관한 글로 본다
                return call['schema'](choice_id=None, new_expression=None, interest_change=None, unrelated=True)
            return PlannerOutput(tasks=tasks)
        self.db.agent_client.parse.side_effect = parse

    def ask(self, tasks=REPORT, question='수출 리포트 생성', key='report'):
        """질문을 보내고 워커를 한 번 돌린다. 리포트 확인 되묻기까지 진행된 job을 돌려준다."""
        self.agent(tasks)
        if self.csrf is None:
            self.uid = int(self.signup())
        sid = self.post('chat/sessions', {}).json()['session']['session_id']
        response = self.post(f'chat/sessions/{sid}/messages', {'question': question, 'client_message_id': key})
        self.assertEqual(response.status_code, 202, response.text)
        jid = response.json()['job']['job_id']
        self.work()
        return jid

    def work(self):
        self.assertTrue(run_once(self.db, self.settings))

    def job(self, jid):
        return self.client.get(f'/api/v1/chat/jobs/{jid}').json()['job']

    def confirm(self, jid, confirm=True, formats=('PDF',), **change):
        prompt = self.job(jid)['clarification']
        item = {'preview_id': prompt['report_preview']['preview_id'], 'confirm': confirm, 'formats': list(formats), **change}
        return self.post(f'chat/jobs/{jid}/clarifications', {'clarification_id': prompt['clarification_id'], 'report_confirmation': item})

    def reports(self):
        with self.engine.connect() as c:
            return [dict(r._mapping) for r in c.execute(select(self.meta.tables['report']))]

    def previews(self):
        t = self.meta.tables['api_snapshot']
        with self.engine.connect() as c:
            return list(c.scalars(select(t.c.snapshot_id).where(t.c.kind == 'PREVIEW')))

    def finish_report(self, status):
        """리포트 파일 생성은 리포트 워커 테스트가 맡는다. 여기서는 그 완료 신호(report.status)만 바꾼다."""
        with self.engine.begin() as c:
            t = self.meta.tables['report']
            c.execute(update(t).values(status=status))

    def state(self, jid):
        from backend.app.agents.graph import build_graph, graph_config
        graph = build_graph(Mock(), self.db.agent_checkpointer)  # 저장된 체크포인트만 읽는다(노드는 실행하지 않음)
        return AgentState.model_validate(graph.get_state(graph_config(jid)).values)

    def answer_text(self, job):
        return ' '.join(s for c in job['answer'].get('conclusion') or [] for s in c.get('sentences', [])) or job['answer'].get('text', '')

    # ── 승인 (D-81·D-92) ─────────────────────────────────────────
    def test_report_waits_for_explicit_button_approval(self):
        """R01 미리보기만 만들고 묻는다. 입력창의 '네'는 승인이 아니며 리포트를 만들지 않는다."""
        jid = self.ask()
        waiting = self.job(jid)
        self.assertEqual((waiting['status'], waiting['clarification']['kind']), ('NEEDS_INPUT', 'REPORT_CONFIRM'))
        self.assertEqual(waiting['clarification']['report_preview']['preview_id'], self.previews()[0])
        self.assertEqual(self.reports(), [])
        prompt = waiting['clarification']
        for text in ('네', '확인', '생성'):  # 짧은 승인·거절 말은 버튼 안내로 끝나고 실행하지 않는다
            with self.subTest(text=text):
                response = self.post(f'chat/jobs/{jid}/clarifications', {'clarification_id': prompt['clarification_id'], 'text': text})
                self.assertEqual(response.json()['job']['status'], 'NEEDS_INPUT', response.text)
                self.assertEqual(self.reports(), [])
                self.assertFalse(self.state(jid).plan.tasks[0].report_request.confirmed)
        # 그 밖의 글은 조건 수정으로 해석한다(§13.6). 승인이 되지 않고 새 미리보기로 다시 묻는다.
        response = self.post(f'chat/jobs/{jid}/clarifications', {'clarification_id': prompt['clarification_id'], 'text': '이대로 생성해 줘'})
        self.assertEqual(response.status_code, 202, response.text)
        self.work()
        again = self.job(jid)
        self.assertEqual((again['status'], again['clarification']['kind']), ('NEEDS_INPUT', 'REPORT_CONFIRM'), again)
        self.assertFalse(self.state(jid).plan.tasks[0].report_request.confirmed)
        self.assertEqual(self.reports(), [])

    def test_confirmation_must_match_the_preview_and_formats(self):
        """R02 다른 미리보기·지원하지 않는 형식·중복·빈 형식·문자열 확인값은 거부하고 아무것도 접수하지 않는다."""
        jid = self.ask()
        prompt = self.job(jid)['clarification']
        good = {'preview_id': prompt['report_preview']['preview_id'], 'confirm': True, 'formats': ['PDF']}
        for bad in ({**good, 'preview_id': 'other'}, {**good, 'formats': ['XLSX']}, {**good, 'formats': ['PDF', 'PDF']},
                    {**good, 'formats': []}, {**good, 'confirm': 'true'}, {k: v for k, v in good.items() if k != 'formats'}):
            with self.subTest(bad=bad):
                response = self.post(f'chat/jobs/{jid}/clarifications', {'clarification_id': prompt['clarification_id'], 'report_confirmation': bad})
                self.assertGreaterEqual(response.status_code, 400, response.text)
                self.assertLess(response.status_code, 500, response.text)
        self.assertEqual(self.job(jid)['status'], 'NEEDS_INPUT')
        self.assertEqual(self.reports(), [])

    def test_selected_formats_are_kept_on_the_report(self):
        """R03 사용자가 고른 형식(PDF·DOCX 중 한 개 이상)을 그대로 리포트에 남긴다."""
        for i, formats in enumerate((['DOCX'], ['PDF', 'DOCX'], ['PDF'])):
            with self.subTest(formats=formats):
                jid = self.ask(key=f'formats-{i}')
                self.assertEqual(self.confirm(jid, formats=formats).status_code, 202)
                self.work()
                self.assertEqual(self.job(jid)['status'], 'WAITING_REPORT')
                report = next(r for r in self.reports() if r['report_id'] == int(self.state(jid).report_wait.report_id))
                self.assertEqual(sorted(report['conditions_json']['formats']), sorted(formats))  # 중복 없는 선택(순서 무관)
                self.finish_report('READY'); self.work()

    def test_import_report_is_refused_before_preview(self):
        """R04 리포트는 수출 분석만 지원한다. 수입은 미리보기·리포트 없이 안내로 끝난다(수출로 바꾸지 않는다)."""
        jid = self.ask([{'intent': 'REPORT', 'question': '수입 리포트 생성', 'slots': {'trade_flow': 'IMPORT'}}], '수입 리포트 생성')
        done = self.job(jid)
        self.assertEqual(done['status'], 'SUCCEEDED', done)
        self.assertIn('수출', self.answer_text(done))
        self.assertNotIn(DONE_TEXT, self.answer_text(done))
        self.assertEqual((self.previews(), self.reports()), ([], []))

    def test_cancel_creates_no_report(self):
        """R05 취소하면 그 리포트 요청만 끝내고 리포트를 접수하지 않는다."""
        jid = self.ask()
        self.assertEqual(self.confirm(jid, confirm=False).status_code, 202)
        self.work()
        done = self.job(jid)
        self.assertEqual(done['status'], 'SUCCEEDED', done)
        self.assertNotIn(DONE_TEXT, self.answer_text(done))
        self.assertIsNone(done['answer'].get('report_id'))
        self.assertEqual(self.reports(), [])

    def test_expired_preview_is_confirmed_again_not_created(self):
        """R06 승인 뒤 실행 전에 미리보기가 만료되면 새 미리보기로 다시 묻는다. 만료된 승인으로 만들지 않는다."""
        jid = self.ask()
        old = self.job(jid)['clarification']['report_preview']['preview_id']
        self.assertEqual(self.confirm(jid).status_code, 202)
        with self.engine.begin() as c:
            t = self.meta.tables['api_snapshot']
            c.execute(update(t).where(t.c.snapshot_id == old).values(expires_at=now() - timedelta(minutes=1)))
        self.work()
        again = self.job(jid)
        self.assertEqual((again['status'], again['clarification']['kind']), ('NEEDS_INPUT', 'REPORT_CONFIRM'), again)
        self.assertNotEqual(again['clarification']['report_preview']['preview_id'], old)
        self.assertEqual(self.reports(), [])

    # ── 대기와 완료 (D-76·D-93) ─────────────────────────────────
    def test_chat_waits_until_the_report_is_ready(self):
        """R07 리포트가 QUEUED·RUNNING·DELAYED이면 채팅은 대기한다. READY가 되면 리포트 번호와 완료 문구로 끝난다."""
        jid = self.ask()
        self.confirm(jid); self.work()
        self.assertEqual(self.job(jid)['status'], 'WAITING_REPORT')
        self.settings.values['API_CHAT_AGENT_ENABLED'] = 'false'  # 리포트 파일 생성 작업을 이 테스트에서 돌리지 않는다
        for status in ('QUEUED', 'RUNNING', 'DELAYED'):
            with self.subTest(status=status):
                self.finish_report(status)
                run_once(self.db, self.settings)
                self.assertEqual(self.job(jid)['status'], 'WAITING_REPORT')
        self.finish_report('READY')
        self.work()
        done = self.job(jid)
        self.assertEqual(done['status'], 'SUCCEEDED', done)
        self.assertEqual(done['answer']['report_id'], str(self.reports()[0]['report_id']))
        self.assertIn(DONE_TEXT, self.answer_text(done))

    def test_failed_report_ends_the_wait_without_success_text(self):
        """R08 리포트가 FAILED면 대기를 끝내되 완료 문구·리포트 번호를 주지 않는다. 같은 질문의 다른 분석은 그대로 답한다(D-72)."""
        jid = self.ask([{'intent': 'STATISTICS', 'question': '수출 통계', 'slots': {'recent_months': 3}}, *REPORT])
        self.confirm(jid); self.work()
        self.assertEqual(self.job(jid)['status'], 'WAITING_REPORT')
        self.settings.values['API_CHAT_AGENT_ENABLED'] = 'false'
        self.finish_report('FAILED')
        self.work()
        done = self.job(jid)
        self.assertEqual(done['status'], 'SUCCEEDED', done)
        self.assertNotIn(DONE_TEXT, self.answer_text(done))
        self.assertIsNone(done['answer'].get('report_id'))
        self.assertEqual([t['answer_status'] for t in done['answer']['tasks']], ['ANSWERED', 'NO_DATA'])

    def test_first_answer_title_and_summary_come_from_code(self):
        """R11 리포트 성공이 첫 답변이면 제목은 조건을 담아 20자 이내로, 요약은 '리포트 생성 완료'로 코드가 쓴다(D-93)."""
        jid = self.ask()
        self.confirm(jid); self.work()
        self.settings.values['API_CHAT_AGENT_ENABLED'] = 'false'
        self.finish_report('READY'); self.work()
        done = self.job(jid)
        self.assertEqual(done['status'], 'SUCCEEDED', done)
        session = next(s for s in self.client.get('/api/v1/chat/sessions').json()['items'] if s['session_id'] == done['session_id'])
        self.assertTrue(session['title'] and len(session['title']) <= 20, session)
        self.assertEqual(self.db.agent_client.parse.call_count, 1)  # 결론·제목을 쓰려고 LLM을 다시 부르지 않는다

    # ── 선행 결과로 만드는 리포트 (D-90·D-92) ─────────────────────
    def test_result_based_report_refuses_sources_with_different_conditions(self):
        """R12 선행 결과들의 분석 조건이 다르면 임의로 합치지 않고 리포트만 NO_DATA로 끝낸다."""
        tasks = [{'intent': 'STATISTICS', 'question': '미국 수출', 'slots': {'recent_months': 3, 'countries': [{'mention': '미국', 'name': '미국'}]}},
                 {'intent': 'STATISTICS', 'question': '중국 수출', 'slots': {'recent_months': 3, 'countries': [{'mention': '중국', 'name': '중국'}]}},
                 {'intent': 'REPORT', 'question': '두 결과로 리포트', 'source_tasks': [1, 2]}]
        jid = self.ask(tasks, '미국 수출, 중국 수출 조회 후 리포트')
        done = self.job(jid)
        self.assertEqual(done['status'], 'SUCCEEDED', done)
        self.assertEqual(done['answer']['tasks'][-1]['answer_status'], 'NO_DATA')
        self.assertIn('같은 조건', self.answer_text(done) + ' '.join(w for w in done['answer'].get('warnings', [])))
        self.assertEqual(self.reports(), [])

    def test_result_based_report_cannot_change_conditions_by_text(self):
        """R13 선행 결과로 만드는 리포트는 글로 조건을 바꿀 수 없고 새 분석 질문을 안내한다(D-92)."""
        tasks = [{'intent': 'STATISTICS', 'question': '수출액 조회', 'slots': {'recent_months': 3}},
                 {'intent': 'REPORT', 'question': '조회 결과로 리포트', 'source_tasks': [1]}]
        jid = self.ask(tasks, '수출액 조회 후 리포트')
        prompt = self.job(jid)['clarification']
        self.assertEqual(prompt['kind'], 'REPORT_CONFIRM')
        response = self.post(f'chat/jobs/{jid}/clarifications', {'clarification_id': prompt['clarification_id'], 'text': '기간을 6개월로 바꿔 줘'})
        self.assertEqual(response.status_code, 202, response.text)
        self.work()
        done = self.job(jid)
        self.assertEqual(done['status'], 'SUCCEEDED', done)
        self.assertEqual([t['answer_status'] for t in done['answer']['tasks']], ['ANSWERED', 'NO_DATA'])
        self.assertIn('새 분석', self.answer_text(done) + ' '.join(done['answer'].get('warnings', [])))
        self.assertEqual(self.reports(), [])

    # ── 직전 답변의 결과표로 만드는 리포트 (이슈 #101, 설계 '이전 턴 결과 참조') ──
    def turn(self, sid, tasks, question, key):
        """같은 대화방에 질문을 하나 더 보낸다(이전 턴 참조 사례)."""
        self.agent(tasks)
        response = self.post(f'chat/sessions/{sid}/messages', {'question': question, 'client_message_id': key})
        self.assertEqual(response.status_code, 202, response.text)
        jid = response.json()['job']['job_id']
        self.work()
        return jid

    def answered(self, tasks, question='미국 수출 조회', key='first'):
        """첫 턴을 답변까지 끝내고 (대화방, 답변의 결과표 ID 목록)을 돌려준다."""
        jid = self.ask(tasks, question, key=key)
        done = self.job(jid)
        self.assertEqual(done['status'], 'SUCCEEDED', done)
        return done['session_id'], [t['result_id'] for t in done['answer']['tables']]

    def source_of(self, jid):
        prompt = self.job(jid)['clarification']
        self.assertEqual(prompt['kind'], 'REPORT_CONFIRM', prompt)
        return [s['result_id'] for s in prompt['report_preview']['source_results']]

    US = [{'intent': 'STATISTICS', 'question': '미국 수출', 'slots': {'recent_months': 3, 'countries': [{'mention': '미국', 'name': '미국'}]}}]
    CN = [{'intent': 'STATISTICS', 'question': '중국 수출', 'slots': {'recent_months': 3, 'countries': [{'mention': '중국', 'name': '중국'}]}}]

    def test_previous_table_report_copies_that_table_without_requery(self):
        """R26 '방금 그 표로 리포트'는 직전 답변의 결과표를 그대로 미리보기·리포트에 싣는다. 조건은 표의 조건이고 다시 조회하지 않는다."""
        from unittest.mock import patch
        sid, tables = self.answered(self.US)
        self.assertEqual(len(tables), 1)
        with patch('backend.app.api.trade.aggregate', side_effect=AssertionError('직전 결과표 리포트는 다시 조회하지 않습니다')):
            jid = self.turn(sid, REPORT, '방금 그 표로 리포트 만들어줘', 'again')
            self.assertEqual(self.source_of(jid), tables)
            saved = self.state(jid).plan.tasks[0]
            self.assertTrue(saved.previous_turn_result)
            self.assertEqual(saved.conditions.country_codes, ['US'])
            self.confirm(jid); self.work()
        self.assertEqual(self.job(jid)['status'], 'WAITING_REPORT')
        report, = self.reports()
        self.assertEqual([t['result_id'] for t in report['content_json']['source_tables']], tables)
        self.assertEqual(report['conditions_json']['chat_sources'], tables)
        # 문서도 같은 턴 결과 리포트(R19)와 같은 요청형 템플릿으로, 결과표 사본만으로 만든다(통계 재조회 없음)
        from backend.app.services.report_templates import render
        documents = []
        with patch('backend.app.api.reports.report_pdf', side_effect=lambda doc, settings, **kwargs: documents.append(doc) or b'%PDF-test'), \
             patch('backend.app.api.trade.aggregate', side_effect=AssertionError('직전 결과표 리포트는 다시 조회하지 않습니다')):
            self.work()
        self.assertEqual(self.reports()[0]['status'], 'READY')
        ctx = documents[0]['template_pdf']['context']
        self.assertEqual([r['result_id'] for r in ctx['results']], tables)
        html = render('RPT_ON_DEMAND', ctx)
        self.assertIn('승인한 분석 결과', html)
        self.assertIn(ctx['results'][0]['rows'][0][-1], html)   # 결과표 값 그대로

    def test_previous_answer_with_several_tables_asks_which_one(self):
        """R27 직전 답변에 결과표가 여러 개면 REPORT_TABLE로 묻고, 고른 표만 리포트에 싣는다. 재개해도 다시 묻지 않는다."""
        sid, tables = self.answered([*self.US, *self.CN], '미국, 중국 수출 조회')
        self.assertEqual(len(tables), 2)
        jid = self.turn(sid, REPORT, '방금 그 표로 리포트 만들어줘', 'again')
        prompt = self.job(jid)['clarification']
        self.assertEqual((prompt['kind'], prompt['message']), ('REPORT_TABLE', '어느 결과표로 리포트를 만들까요?'))
        # 질문 문장이 아니라 표를 만든 조건(국가·품목·종류)으로 구분한다
        self.assertEqual([c['label'] for c in prompt['choices']], ['1. 미국 · 월별 추이', '2. 중국 · 월별 추이'])
        response = self.post(f'chat/jobs/{jid}/clarifications', {'clarification_id': prompt['clarification_id'], 'choice_ids': ['T2']})
        self.assertEqual(response.status_code, 202, response.text)
        self.work()
        self.assertEqual(self.source_of(jid), tables[1:])
        self.assertEqual(self.state(jid).plan.tasks[0].conditions.country_codes, ['CN'])
        self.confirm(jid); self.work()
        self.assertEqual(self.job(jid)['status'], 'WAITING_REPORT')
        self.assertEqual(self.reports()[0]['conditions_json']['chat_sources'], tables[1:])

    def test_previous_table_missing_or_expired_ends_with_guidance(self):
        """R28 직전 답변에 결과표가 없거나(첫 질문·표 없는 답변) 보관 기간이 지났으면 미리보기 없이 안내로 끝난다."""
        jid = self.ask(REPORT, '방금 그 표로 리포트 만들어줘', key='alone')   # 대화방의 첫 질문
        self.assertIn('직전 답변에 리포트로 만들 결과표가 없습니다', self.answer_text(self.job(jid)) + ' '.join(self.job(jid)['answer'].get('warnings', [])))
        sid, tables = self.answered(self.US)
        with self.engine.begin() as c:
            t = self.meta.tables['api_snapshot']
            c.execute(update(t).where(t.c.snapshot_id == tables[0]).values(expires_at=now() - timedelta(minutes=1)))
        jid = self.turn(sid, REPORT, '방금 그 표로 리포트 만들어줘', 'expired')
        done = self.job(jid)
        self.assertEqual(done['status'], 'SUCCEEDED', done)
        self.assertIn('보관 기간이 지났습니다', self.answer_text(done) + ' '.join(done['answer'].get('warnings', [])))
        self.assertEqual((self.previews(), self.reports()), ([], []))

    def test_ambiguous_report_request_can_use_the_previous_table(self):
        """R29 '이걸로 리포트'는 REPORT_SOURCE로 묻고(준비 중 표시 없음), 결과표를 고르면 R26과 같은 경로로 간다."""
        sid, tables = self.answered(self.US)
        jid = self.turn(sid, REPORT, '이걸로 리포트 만들어줘', 'ambiguous')
        prompt = self.job(jid)['clarification']
        self.assertEqual(prompt['kind'], 'REPORT_SOURCE')
        self.assertEqual([c['label'] for c in prompt['choices']], ['직전 결과표로 리포트', '같은 조건으로 다시 조회'])
        response = self.post(f'chat/jobs/{jid}/clarifications', {'clarification_id': prompt['clarification_id'], 'choice_ids': ['PREVIOUS_RESULT']})
        self.assertEqual(response.status_code, 202, response.text)
        self.work()
        self.assertEqual(self.source_of(jid), tables)

    def test_previous_prediction_table_keeps_ranking_basis(self):
        """R30 직전 답변의 결과표가 유망시장 예측(PREDICTION)이면 순위 기준(group_by·anchor)까지 사본에 실어 채팅과 같은 순위를 보인다.
        예측 모델 파일 없이 돌도록 예측 결과표를 저장 결과로 직접 만든다(다른 PREDICTION 테스트와 같은 방식)."""
        from backend.app.api import results
        sid, tables = self.answered(self.US)
        meta = {'kind': 'PREDICTION', 'group_by': 'COUNTRY', 'anchor': '2025-09'}
        with self.engine.begin() as c:
            store = Store(self.db, c)
            conditions = results.read_snapshot(store, self.uid, tables[0], 'RESULT')['conditions']
            rid = results.result_table(store, self.uid, [{'country_code': 'US', 'hs6': '330499', 'hs_ver': 'HS2022',
                                                          'score': 0.9, 'product_rank': 1}], meta, conditions)['result_id']
            t = self.meta.tables['chat_message']
            row = c.execute(select(t).where(t.c.role == 'ASSISTANT', t.c.session_id == int(sid))).mappings().one()
            data = dict(row['result_json']); answer = dict(data['answer'])
            answer['tables'] = [{**answer['tables'][0], 'result_id': rid, 'meta': meta}]
            c.execute(update(t).where(t.c.message_id == row['message_id']).values(result_json={**data, 'answer': answer}))
        jid = self.turn(sid, REPORT, '방금 그 표로 리포트 만들어줘', 'prediction')
        self.assertEqual(self.source_of(jid), [rid])
        self.confirm(jid); self.work()
        copy, = self.reports()[0]['content_json']['source_tables']
        self.assertEqual((copy['kind'], copy['meta']), ('PREDICTION', meta))

    def test_previous_table_report_keeps_the_table_conditions(self):
        """R32 직전 결과표 리포트는 표를 만든 조건 그대로다. 질문에 기간이 함께 와도(플래너 슬롯) 표 조건을 바꾸지 않고 알리며,
        확인 화면에서 글로 조건을 바꾸려 하면 새 분석을 안내한다(같은 턴 연결 R13과 같은 규칙, D-92)."""
        sid, tables = self.answered(self.US)
        with self.engine.connect() as c:
            from backend.app.api.results import read_snapshot
            stored = read_snapshot(Store(self.db, c), self.uid, tables[0], 'RESULT')['conditions']
        question = '아까 본 표로 최근 6개월만 리포트 만들어줘'
        jid = self.turn(sid, [{'intent': 'REPORT', 'question': question, 'previous_turn_result': True,
                               'slots': {'recent_months': 6}}], question, 'six')
        self.assertEqual(self.source_of(jid), tables)
        task = self.state(jid).plan.tasks[0]
        from backend.app.agents.templates import api_conditions
        self.assertEqual(api_conditions(task.conditions)['period'], stored['period'])   # 표의 3개월 그대로(6개월로 바뀌지 않음)
        self.assertIn('직전 결과표의 조건 그대로 만들었습니다. 조건을 바꾸려면 새로 조회해 주세요.', task.applied_defaults)
        prompt = self.job(jid)['clarification']
        # 생성 전에 보이도록 승인 화면의 adjustments에도 같은 안내가 있다
        self.assertIn('직전 결과표의 조건 그대로 만들었습니다. 조건을 바꾸려면 새로 조회해 주세요.', prompt['report_preview']['adjustments'])
        response = self.post(f'chat/jobs/{jid}/clarifications', {'clarification_id': prompt['clarification_id'], 'text': '기간을 6개월로 바꿔 줘'})
        self.assertEqual(response.status_code, 202, response.text)
        self.work()
        done = self.job(jid)
        self.assertEqual(done['status'], 'SUCCEEDED', done)
        self.assertEqual(done['answer']['tasks'][-1]['answer_status'], 'NO_DATA')
        self.assertIn('새 분석', self.answer_text(done) + ' '.join(done['answer'].get('warnings', [])))
        self.assertEqual(self.reports(), [])

    def edit_answer(self, sid, change):
        """직전 ASSISTANT 답변(result_json.answer)을 change(answer)로 고친다."""
        with self.engine.begin() as c:
            t = self.meta.tables['chat_message']
            row = c.execute(select(t).where(t.c.role == 'ASSISTANT', t.c.session_id == int(sid))).mappings().one()
            data = dict(row['result_json']); answer = dict(data['answer'])
            change(answer)
            c.execute(update(t).where(t.c.message_id == row['message_id']).values(result_json={**data, 'answer': answer}))

    def edit_meta(self, rid, conditions=None, **values):
        """저장 결과표의 meta를 고친다(values 값이 None이면 그 키를 지운다). conditions를 주면 표의 조건을 바꾼다."""
        with self.engine.begin() as c:
            t = self.meta.tables['api_snapshot']
            payload = dict(c.execute(select(t.c.payload_json).where(t.c.snapshot_id == rid)).scalar_one())
            meta = {k: v for k, v in {**payload['meta'], **values}.items() if v is not None}
            c.execute(update(t).where(t.c.snapshot_id == rid).values(
                payload_json={**payload, 'meta': meta, 'conditions': payload['conditions'] if conditions is None else conditions}))

    def legacy_answer(self, sid, tables, conditions=None, columns=None):
        """직전 답변을 기존 채팅 API(api/chat.py) 형식으로 바꾼다: tasks 없음, intent는 항상 STATISTICS, 표 meta.kind 없음,
        표 스냅샷 조건은 기간·품목·국가·수출입만(비교 기간은 답변 conditions에만 확정)."""
        from backend.app.api.results import read_snapshot
        with self.engine.connect() as c:
            stored = read_snapshot(Store(self.db, c), self.uid, tables[0], 'RESULT')['conditions']
        for rid in tables:
            self.edit_meta(rid, kind=None, conditions={k: stored[k] for k in ('period', 'hs_items', 'country_codes', 'trade_flow') if k in stored})

        def change(answer):
            answer.pop('tasks', None)
            answer.update(intent='STATISTICS', conditions={**stored, **(conditions or {})},
                          tables=[{**t, 'meta': {k: v for k, v in (t.get('meta') or {}).items() if k != 'kind'},
                                   **({'columns': columns} if columns is not None else {})} for t in answer['tables']])
        self.edit_answer(sid, change)
        return stored

    def expire(self, *rids):
        with self.engine.begin() as c:
            t = self.meta.tables['api_snapshot']
            c.execute(update(t).where(t.c.snapshot_id.in_(rids)).values(expires_at=now() - timedelta(minutes=1)))

    def test_legacy_chat_table_kind_comes_from_its_columns(self):
        """R37 기존 채팅 API 답변은 표 종류와 관계없이 intent='STATISTICS'다. meta.kind가 없으면 표 열로 종류를 정한다.
        순위표에도 share_pct가 있으므로 rank를 먼저 본다. 판별할 수 없으면 None(작업 실패 대신 안내)."""
        from backend.app.agents.bindings import table_kind
        cols = lambda *keys: [{'key': k} for k in keys]
        self.assertEqual(table_kind({}, cols('month', 'export_amount_usd', 'import_amount_usd')), 'TREND')
        self.assertEqual(table_kind(None, cols('rank', 'country_name', 'export_amount_usd', 'share_pct')), 'RANKING')
        self.assertEqual(table_kind({}, cols('period', 'country_name', 'amount_usd', 'share_pct')), 'SHARE')
        self.assertEqual(table_kind({}, cols('label', 'amount_usd')), None)
        self.assertEqual(table_kind({'kind': 'PREDICTION'}, cols('rank')), 'PREDICTION')

    def test_legacy_chat_trend_table_becomes_a_report_with_its_comparison(self):
        """R33 기존 채팅 API의 월별 추이 표로 리포트를 만든다. 표의 기간·품목은 그대로 두고, 답변에서 확정한 전년 비교 기간을 되살린다."""
        sid, tables = self.answered(self.US)
        from backend.app.api.results import read_snapshot
        with self.engine.connect() as c:
            period = read_snapshot(Store(self.db, c), self.uid, tables[0], 'RESULT')['conditions']['period']
        last_year = {k: str(int(v[:4]) - 1) + v[4:] for k, v in period.items()}
        self.legacy_answer(sid, tables, conditions={'compare_period': last_year})
        jid = self.turn(sid, REPORT, '방금 그 표로 리포트 만들어줘', 'legacy')
        self.assertEqual(self.source_of(jid), tables)
        task = self.state(jid).plan.tasks[0]
        from backend.app.agents.templates import api_conditions
        self.assertEqual((api_conditions(task.conditions)['period'], task.conditions.country_codes), (period, ['US']))
        preview = self.job(jid)['clarification']['report_preview']['conditions']
        self.assertEqual(preview['compare_period'], last_year)   # '비교 없음'으로 바뀌지 않는다
        self.confirm(jid); self.work()
        self.assertEqual(self.job(jid)['status'], 'WAITING_REPORT')
        self.assertEqual(self.reports()[0]['content_json']['source_tables'][0]['kind'], 'TREND')

    def test_legacy_chat_table_of_unknown_kind_ends_with_guidance(self):
        """R38 기존 채팅 API 표의 종류를 열로도 판별할 수 없으면 작업 실패(JOB_FAILED) 대신 사유를 안내한다."""
        sid, tables = self.answered(self.US)
        self.legacy_answer(sid, tables, columns=[{'key': 'label', 'label': '구분'}])
        jid = self.turn(sid, REPORT, '방금 그 표로 리포트 만들어줘', 'unknown')
        done = self.job(jid)
        self.assertEqual(done['status'], 'SUCCEEDED', done)
        self.assertIn('종류를 확인할 수 없어', self.answer_text(done) + ' '.join(done['answer'].get('warnings', [])))

    def test_legacy_table_beyond_agent_limits_ends_with_guidance(self):
        """R42 기존 채팅 API는 국가 250개까지 허용했다. 에이전트 조건 상한(국가 20개)을 넘는 표는 작업 실패 대신 안내로 끝난다."""
        sid, tables = self.answered(self.US)
        stored = self.legacy_answer(sid, tables)
        self.edit_meta(tables[0], conditions={**stored, 'country_codes': [f'X{i:02d}' for i in range(21)]})
        jid = self.turn(sid, REPORT, '방금 그 표로 리포트 만들어줘', 'wide')
        done = self.job(jid)
        self.assertEqual(done['status'], 'SUCCEEDED', done)
        self.assertIn('리포트로 옮길 수 없습니다', self.answer_text(done) + ' '.join(done['answer'].get('warnings', [])))
        self.assertEqual((self.previews(), self.reports()), ([], []))

    def test_legacy_multi_item_tables_are_labelled_by_their_own_item(self):
        """R40 기존 채팅 API의 다중 품목 답변은 품목마다 표가 있고 답변 conditions에는 품목 전체가 있다. 선택지는 표마다 그 품목으로 구별한다."""
        sid, tables = self.answered([*self.US, *self.CN], '미국, 중국 수출 조회')
        stored = self.legacy_answer(sid, tables)
        items = [{'hs_code': '3304', 'hs_ver': 'HS2022'}, {'hs_code': '330499', 'hs_ver': 'HS2022'}]
        for rid, item in zip(tables, items):
            self.edit_meta(rid, conditions={**stored, 'country_codes': ['US'], 'hs_items': [item]})
        self.edit_answer(sid, lambda answer: answer['conditions'].update(country_codes=['US'], hs_items=items))
        jid = self.turn(sid, REPORT, '방금 그 표로 리포트 만들어줘', 'items')
        prompt = self.job(jid)['clarification']
        self.assertEqual([c['label'] for c in prompt['choices']], ['1. 미국 · HS 3304 · 월별 추이', '2. 미국 · HS 330499 · 월별 추이'])

    def test_import_tables_are_not_offered_for_a_report(self):
        """R39 수출입을 함께 조회한 답변의 수입 표는 리포트 후보에서 뺀다(리포트는 수출만, D-81).
        수출 표가 하나 남으면 바로 확인 화면, 수입 표만 있으면 수출 전용 안내다."""
        sid, tables = self.answered([*self.US, *self.CN], '미국, 중국 수출 조회')
        from backend.app.api.results import read_snapshot
        with self.engine.connect() as c:
            imported = read_snapshot(Store(self.db, c), self.uid, tables[1], 'RESULT')['conditions']
        self.edit_meta(tables[1], conditions={**imported, 'trade_flow': 'IMPORT'})
        jid = self.turn(sid, REPORT, '방금 그 표로 리포트 만들어줘', 'export-left')
        self.assertEqual(self.source_of(jid), tables[:1])

        sid, tables = self.answered(self.US, key='second')
        with self.engine.connect() as c:
            imported = read_snapshot(Store(self.db, c), self.uid, tables[0], 'RESULT')['conditions']
        self.edit_meta(tables[0], conditions={**imported, 'trade_flow': 'IMPORT'})
        jid = self.turn(sid, REPORT, '방금 그 표로 리포트 만들어줘', 'import-only')
        done = self.job(jid)
        self.assertEqual(done['status'], 'SUCCEEDED', done)
        self.assertIn('수출만 지원합니다', self.answer_text(done) + ' '.join(done['answer'].get('warnings', [])))
        self.assertEqual(self.reports(), [])

    def test_legacy_truncated_table_is_a_partial_report(self):
        """R43 기존 채팅 API의 순위표는 1,000행에서 잘리면 completeness 없이 meta.truncated·rows_total만 남긴다.
        리포트는 이를 PARTIAL과 '전체 N개 중 상위 M개만' 사유로 바꿔, 실제 리포트 워커를 거친 완료 답변까지 알린다."""
        sid, tables = self.answered(self.US)
        self.legacy_answer(sid, tables)
        self.edit_meta(tables[0], truncated=True, rows_total=1001)
        done = self.partial_report(sid, tables, None, 'cut')
        self.assertEqual(done['answer']['answer_status'], 'PARTIAL')
        self.assertTrue(any(w.startswith('전체 1,001개 중 상위') for w in done['answer']['warnings']), done['answer']['warnings'])

        # 에이전트 결과의 meta.truncated는 요약 행 상한이다(표는 전부 저장). 잘림으로 바꾸지 않는다
        sid, tables = self.answered(self.US, key='second')
        self.edit_meta(tables[0], truncated=True, rows_total=1001)
        jid = self.turn(sid, REPORT, '방금 그 표로 리포트 만들어줘', 'agent-cut')
        source, = self.job(jid)['clarification']['report_preview']['source_results']
        self.assertNotEqual(source['completeness'], 'PARTIAL')
        self.assertEqual(source['limitations'], [])

    def test_previous_table_report_notes_an_ignored_ranking_limit(self):
        """R44 '상위 1개만 리포트로'(ranking_limit)도 표 조건 변경 요청이다. 원본 표를 그대로 싣고, 제한이 적용되지 않는다는 안내를 승인 전에 보인다."""
        sid, tables = self.answered(self.US)
        question = '아까 표에서 상위 1개만 리포트로 만들어줘'
        jid = self.turn(sid, [{'intent': 'REPORT', 'question': question, 'previous_turn_result': True,
                               'slots': {'ranking_limit': 1}}], question, 'top1')
        self.assertEqual(self.source_of(jid), tables)
        prompt = self.job(jid)['clarification']
        self.assertIn('직전 결과표의 조건 그대로 만들었습니다. 조건을 바꾸려면 새로 조회해 주세요.', prompt['report_preview']['adjustments'])

    def six_tables(self, count=6):
        """직전 답변에 같은 모양(기간만 다른)의 결과표 count개(기본 6개)를 둔다. (대화방, 결과표 ID 목록)."""
        from backend.app.api import results
        sid, tables = self.answered(self.US)
        with self.engine.begin() as c:
            store = Store(self.db, c)
            source = results.read_snapshot(store, self.uid, tables[0], 'RESULT')
            rids = [tables[0]]
            first = date.fromisoformat(source['conditions']['period']['start'])
            for months in range(1, count):   # 시작 월만 당긴(데이터 범위 안) 같은 모양의 표를 더 만든다
                month = first.year * 12 + first.month - 1 - months
                start = date(month // 12, month % 12 + 1, 1).isoformat()
                conditions = {**source['conditions'], 'period': {**source['conditions']['period'], 'start': start}}
                rids.append(results.result_table(store, self.uid, source['rows'], source['meta'], conditions)['result_id'])
        self.edit_answer(sid, lambda answer: answer.update(tables=[{**answer['tables'][0], 'result_id': rid} for rid in rids]))
        return sid, rids

    def choose(self, jid, choice_id):
        prompt = self.job(jid)['clarification']
        response = self.post(f'chat/jobs/{jid}/clarifications', {'clarification_id': prompt['clarification_id'], 'choice_ids': [choice_id]})
        self.assertEqual(response.status_code, 202, response.text)

    def test_more_than_five_previous_tables_are_paged(self):
        """R45 직전 답변의 결과표가 5개를 넘으면 한 화면 버튼 5개(표 4개 + '다음 목록')를 유지하고 다음 목록에서 나머지를 고른다."""
        sid, rids = self.six_tables()
        jid = self.turn(sid, REPORT, '방금 그 표로 리포트 만들어줘', 'pages')
        prompt = self.job(jid)['clarification']
        self.assertEqual([c['choice_id'] for c in prompt['choices']], ['T1', 'T2', 'T3', 'T4', 'NEXT'])
        self.assertEqual(prompt['choices'][-1]['label'], '다음 목록 (5~6번)')
        response = self.post(f'chat/jobs/{jid}/clarifications', {'clarification_id': prompt['clarification_id'], 'choice_ids': ['NEXT']})
        self.assertEqual(response.status_code, 202, response.text)
        self.work()
        prompt = self.job(jid)['clarification']
        self.assertEqual(prompt['kind'], 'REPORT_TABLE')
        self.assertEqual([c['choice_id'] for c in prompt['choices']], ['T5', 'T6'])
        self.assertTrue(prompt['choices'][1]['label'].startswith('6. '))
        response = self.post(f'chat/jobs/{jid}/clarifications', {'clarification_id': prompt['clarification_id'], 'choice_ids': ['T6']})
        self.assertEqual(response.status_code, 202, response.text)
        self.work()
        self.assertIsNotNone(self.job(jid)['clarification'], self.job(jid))
        self.assertEqual(self.source_of(jid), rids[5:])
        self.confirm(jid); self.work()
        self.assertEqual(self.reports()[0]['conditions_json']['chat_sources'], rids[5:])

    def test_next_page_that_disappeared_restarts_the_list(self):
        """R46 '다음 목록'을 고른 뒤 재개 전에 표 하나가 만료돼 후보가 5개가 되면, 사라진 다음 쪽을 반복해 읽지 않고
        (무한 반복 없이) 지금 유효한 후보 5개를 첫 쪽으로 다시 보인다."""
        sid, rids = self.six_tables()
        jid = self.turn(sid, REPORT, '방금 그 표로 리포트 만들어줘', 'shrink')
        self.choose(jid, 'NEXT')
        self.expire(rids[2])
        self.work()
        prompt = self.job(jid)['clarification']
        self.assertEqual(prompt['kind'], 'REPORT_TABLE')
        self.assertEqual([c['choice_id'] for c in prompt['choices']], ['T1', 'T2', 'T3', 'T4', 'T5'])
        self.choose(jid, 'T5'); self.work()
        self.assertEqual(self.source_of(jid), rids[5:])   # 남은 후보 기준 5번째 = 원래 6번째 표

    def test_next_page_restarts_when_earlier_candidates_change(self):
        """R51 표 10개에서 '다음 목록'을 고른 뒤 재개 전에 앞쪽 표가 만료되면, 쪽 번호가 그대로여도 후보 구성이 바뀌었으므로
        첫 쪽부터 다시 보인다. 원래 5번째 표가 어느 화면에서도 빠지지 않고 고를 수 있어야 한다."""
        sid, rids = self.six_tables(10)
        jid = self.turn(sid, REPORT, '방금 그 표로 리포트 만들어줘', 'front')
        self.choose(jid, 'NEXT')
        self.expire(rids[0])
        self.work()
        prompt = self.job(jid)['clarification']
        self.assertEqual(prompt['kind'], 'REPORT_TABLE')
        self.assertEqual([c['choice_id'] for c in prompt['choices']], ['T1', 'T2', 'T3', 'T4', 'NEXT'])
        self.assertEqual(prompt['choices'][-1]['label'], '다음 목록 (5~9번)')
        self.choose(jid, 'T4'); self.work()   # 남은 후보의 4번째 = 원래 5번째 표
        self.assertEqual(self.source_of(jid), rids[4:5])

    def test_picked_table_that_expired_is_chosen_again(self):
        """R47 두 번째 쪽에서 고른 표가 재개 전에 만료되면 다른 표로 자동으로 바꾸지 않고, 남은 표 중에서 다시 고르게 한다."""
        sid, rids = self.six_tables()
        jid = self.turn(sid, REPORT, '방금 그 표로 리포트 만들어줘', 'gone')
        self.choose(jid, 'NEXT'); self.work()
        self.choose(jid, 'T6')
        self.expire(*rids[1:])   # 고른 T6을 포함해 T1만 남긴다
        self.work()
        prompt = self.job(jid)['clarification']
        self.assertEqual((prompt['kind'], prompt['message']),
                         ('REPORT_TABLE', '선택한 결과표의 보관 기간이 지났습니다. 남은 결과표 중에서 골라 주세요.'))
        self.assertEqual([c['choice_id'] for c in prompt['choices']], ['T1'])
        self.choose(jid, 'T1'); self.work()
        self.assertEqual(self.source_of(jid), rids[:1])

    def test_table_expiring_before_the_preview_asks_again(self):
        """R48 조건 확인과 미리보기 사이(같은 표를 두 번 읽는 사이)에 고른 표가 만료되면 JOB_FAILED 없이
        미리보기를 만들지 않고 남은 표에서 다시 고르게 한다. 다시 고른 표의 조건으로 처음부터 다시 계산한다."""
        from unittest.mock import patch
        from backend.app.agents import bindings
        sid, tables = self.answered([*self.US, *self.CN], '미국, 중국 수출 조회')
        jid = self.turn(sid, REPORT, '방금 그 표로 리포트 만들어줘', 'race')
        self.choose(jid, 'T1')   # 미국 표
        original = bindings.stored_conditions

        def expire_after_check(snapshot):
            self.expire(tables[0])   # 조건 확인이 표를 읽은 직후, 미리보기가 다시 읽기 전에 만료
            return original(snapshot)
        with patch('backend.app.agents.bindings.stored_conditions', side_effect=expire_after_check):
            self.work()
        job = self.job(jid)
        self.assertEqual(job['status'], 'NEEDS_INPUT', job)
        prompt = job['clarification']
        self.assertEqual((prompt['kind'], prompt['message']),
                         ('REPORT_TABLE', '선택한 결과표의 보관 기간이 지났습니다. 남은 결과표 중에서 골라 주세요.'))
        self.assertEqual([c['label'] for c in prompt['choices']], ['1. 중국 · 월별 추이'])
        self.assertEqual(self.previews(), [])   # 쓰지 않을 미리보기를 만들지 않는다
        self.choose(jid, 'T1'); self.work()
        self.assertEqual(self.source_of(jid), tables[1:])
        self.assertEqual(self.state(jid).plan.tasks[0].conditions.country_codes, ['CN'])

    def test_table_expiring_while_checking_candidates_asks_again(self):
        """R50 후보를 확인한 직후(같은 호출에서 고른 표를 최종으로 읽기 전)에 만료돼도 '보관 기간이 지났습니다'로 끝내지 않는다.
        확인한 스냅샷을 그대로 쓰고, 미리보기 단계의 재확인에서 만료를 알아채 남은 표에서 다시 고르게 한다."""
        from unittest.mock import patch
        from backend.app.agents import bindings
        sid, tables = self.answered([*self.US, *self.CN], '미국, 중국 수출 조회')
        jid = self.turn(sid, REPORT, '방금 그 표로 리포트 만들어줘', 'inner-race')
        self.choose(jid, 'T1')   # 미국 표
        original, calls = bindings._table_pages, []

        def expire_after_candidates(count):
            if not calls: self.expire(tables[0])   # 첫 후보 확인 직후 한 번만 만료
            calls.append(count)
            return original(count)
        with patch('backend.app.agents.bindings._table_pages', side_effect=expire_after_candidates):
            self.work()
        job = self.job(jid)
        self.assertEqual(job['status'], 'NEEDS_INPUT', job)
        self.assertEqual((job['clarification']['kind'], job['clarification']['message']),
                         ('REPORT_TABLE', '선택한 결과표의 보관 기간이 지났습니다. 남은 결과표 중에서 골라 주세요.'))
        self.assertEqual(self.previews(), [])
        self.choose(jid, 'T1'); self.work()
        self.assertEqual(self.source_of(jid), tables[1:])

    def test_reselection_waits_for_an_explicit_choice(self):
        """R49 만료로 다시 고르는 화면에서 선택지와 맞지 않는 글을 입력해도 남은 표를 자동으로 고르지 않고 다시 묻는다.
        만료 때문에 다시 묻는 것은 사용자 답 횟수(조건별 2회)에 세지 않는다."""
        sid, rids = self.six_tables()
        jid = self.turn(sid, REPORT, '방금 그 표로 리포트 만들어줘', 'unclear')
        self.choose(jid, 'NEXT'); self.work()
        self.choose(jid, 'T6')
        self.expire(*rids[1:])
        self.work()
        prompt = self.job(jid)['clarification']
        self.assertEqual([c['choice_id'] for c in prompt['choices']], ['T1'])
        response = self.post(f'chat/jobs/{jid}/clarifications', {'clarification_id': prompt['clarification_id'], 'text': '무슨 뜻이에요?'})
        self.assertEqual(response.status_code, 202, response.text)
        self.work()
        job = self.job(jid)
        self.assertEqual(job['status'], 'NEEDS_INPUT', job)
        self.assertEqual((job['clarification']['kind'], job['clarification']['message']),
                         ('REPORT_TABLE', '선택한 결과표의 보관 기간이 지났습니다. 남은 결과표 중에서 골라 주세요.'))
        self.assertEqual(self.previews(), [])
        self.choose(jid, 'T1'); self.work()
        self.assertEqual(self.source_of(jid), rids[:1])

    def partial_report(self, sid, tables, limitations, key):
        """직전 결과표를 PARTIAL로 만든 뒤 리포트를 승인하고, 실제 리포트 워커(문서 생성·READY 저장)를 거쳐 채팅을 끝낸다."""
        from unittest.mock import patch
        if limitations is not None:
            self.edit_meta(tables[0], completeness='PARTIAL', limitations=limitations)
        jid = self.turn(sid, REPORT, '방금 그 표로 리포트 만들어줘', key)
        self.assertEqual(self.source_of(jid), tables)
        self.confirm(jid); self.work()
        self.assertEqual(self.job(jid)['status'], 'WAITING_REPORT')
        with patch('backend.app.api.reports.report_pdf', return_value=b'%PDF-test'):
            self.work()   # 리포트 워커: content_json을 완성 문서로 바꿔 READY 저장
        report = next(r for r in self.reports() if r['report_id'] == int(self.state(jid).report_wait.report_id))
        self.assertEqual(report['status'], 'READY')
        self.assertNotIn('source_results', report['content_json'])   # 완료 후에는 승인 사본이 남지 않는다
        while self.job(jid)['status'] != 'SUCCEEDED':
            self.assertIn(self.job(jid)['status'], ('WAITING_REPORT', 'QUEUED', 'RUNNING'))
            self.work()
        return self.job(jid)

    def test_previous_partial_table_keeps_partial_in_final_answer(self):
        """R34 직전 결과표가 PARTIAL이면 실제 리포트 워커를 거친 완료 답변도 PARTIAL이고 누락 사유가 경고로 남는다(직전 표는 depends_on 밖)."""
        sid, tables = self.answered(self.US)
        reason = '기간 내 미집계 월이 있습니다.'
        done = self.partial_report(sid, tables, [reason], 'partial')
        self.assertEqual(done['answer']['answer_status'], 'PARTIAL')
        self.assertIn(reason, done['answer']['warnings'])

    def test_previous_partial_table_with_long_reasons_still_completes(self):
        """R41 누락 사유가 길어 ToolResult 상한(1KB)을 넘어도 완성된 리포트를 실패로 끝내지 않고 요약한다."""
        sid, tables = self.answered(self.US)
        done = self.partial_report(sid, tables, [f'{i}월 미집계 ' + '가' * 150 for i in range(1, 9)], 'long')
        self.assertEqual(done['answer']['answer_status'], 'PARTIAL')
        self.assertIn('추가 누락 사유는 선행 결과의 상세 정보를 확인하세요.', done['answer']['warnings'])

    def test_tables_expired_after_the_answer_are_not_offered(self):
        """R35 답변 저장 뒤 만료된 결과표는 선택지에서 빠진다. 남은 표가 하나면 바로 확인 화면, 없으면 만료 안내다."""
        sid, tables = self.answered([*self.US, *self.CN], '미국, 중국 수출 조회')
        self.expire(tables[0])
        jid = self.turn(sid, REPORT, '방금 그 표로 리포트 만들어줘', 'one-left')
        self.assertEqual(self.source_of(jid), tables[1:])
        sid, tables = self.answered([*self.US, *self.CN], '미국, 중국 수출 조회', key='second')
        self.expire(*tables)
        jid = self.turn(sid, REPORT, '방금 그 표로 리포트 만들어줘', 'none-left')
        done = self.job(jid)
        self.assertEqual(done['status'], 'SUCCEEDED', done)
        self.assertIn('보관 기간이 지났습니다', self.answer_text(done) + ' '.join(done['answer'].get('warnings', [])))

    def test_same_looking_tables_show_their_period(self):
        """R36 국가·품목·종류가 같고 기간만 다른 표는 선택지에 조회 기간을 붙여 구별한다."""
        two = [{**self.US[0], 'question': '미국 수출 2개월', 'slots': {**self.US[0]['slots'], 'recent_months': 2}}]
        sid, tables = self.answered([*self.US, *two], '미국 수출 3개월, 2개월 조회')
        self.assertEqual(len(tables), 2)
        jid = self.turn(sid, REPORT, '방금 그 표로 리포트 만들어줘', 'periods')
        prompt = self.job(jid)['clarification']
        self.assertEqual(prompt['kind'], 'REPORT_TABLE')
        with self.engine.connect() as c:
            from backend.app.api.results import read_snapshot
            periods = [read_snapshot(Store(self.db, c), self.uid, r, 'RESULT')['conditions']['period'] for r in tables]
        short = [f"{p['start'][2:7].replace('-', '.')}~{p['end'][2:7].replace('-', '.')}" for p in periods]
        self.assertEqual([c['label'] for c in prompt['choices']],
                         [f'{i}. 미국 · 월별 추이 · {s}' for i, s in enumerate(short, 1)])

    def test_same_turn_prediction_result_becomes_the_report_table(self):
        """R31 같은 질문에서 '유망 수출국 예측 → 그 결과로 리포트'(t1.ml 연결)는 예측 결과표를 순위 기준까지 그대로 리포트에 싣는다.
        예측 모델 파일 없이 돌도록 모델 호출(market.loaded·growth)만 가짜 예측으로 바꾼다(test_ondemand_report_pdf와 같은 방식)."""
        import pandas as pd
        from unittest.mock import patch

        def predict_markets(bundle, *, country_code=None, hs6=None, top=10):
            return pd.DataFrame([{'anchor': '2026-09-01', 'country_code': code, 'hs6': hs6 or '330499', 'growth_pct': 10.0 - rank,
                                  'exp_12m': 3400000.0, 'country_rank': rank, 'product_rank': 1, 'country_size': 3, 'product_size': 1}
                                 for rank, code in enumerate(['US', 'JP', 'CN'], 1)])
        tasks = [{'intent': 'PROMISING_MARKET', 'question': '330499 유망 수출국', 'slots': {'products': ['330499']}},
                 {'intent': 'REPORT', 'question': '예측 결과로 리포트', 'source_tasks': [1]}]
        with patch('backend.app.agents.market.loaded', return_value=({'anchor': '2026-09-01'}, predict_markets, {'model': 'a1'})), \
             patch('backend.app.agents.market.growth', return_value=({'anchor': '2026-09', 'directions': {}}, 'b1', None)):
            jid = self.ask(tasks, '330499 유망 수출국을 예측한 다음 그 예측 결과로 리포트 만들어 줘', key='ml')
            saved = self.state(jid)
            self.assertEqual(saved.plan.tasks[1].steps[0].depends_on, ['t1.ml'])
            ml = saved.results['t1.ml']
            self.assertEqual((ml.status, bool(ml.result_id)), ('OK', True), ml)
            self.assertEqual(self.source_of(jid), [ml.result_id])
            self.confirm(jid); self.work()
        self.assertEqual(self.job(jid)['status'], 'WAITING_REPORT')
        copy, = self.reports()[0]['content_json']['source_tables']
        self.assertEqual((copy['result_id'], copy['kind']), (ml.result_id, 'PREDICTION'))
        with self.engine.connect() as c:
            from backend.app.api.results import read_snapshot
            stored = read_snapshot(Store(self.db, c), self.uid, ml.result_id, 'RESULT')
        self.assertEqual(copy['rows'], stored['rows'])   # 채팅 결과표 값 그대로(재계산 없음)
        self.assertEqual([r['country_code'] for r in sorted(copy['rows'], key=lambda r: r['country_rank'])], ['US', 'JP', 'CN'])
        self.assertTrue(copy['meta']['group_by'])         # 리포트가 채팅과 같은 순위 기준으로 정렬하는 근거

    # ── 중복 접수와 입력 충돌 (D-97) ───────────────────────────────
    def test_same_step_reuses_the_report_and_conflicting_input_is_refused(self):
        """R09 같은 job·단계를 다시 실행하면 리포트를 새로 만들지 않고 재사용한다. 승인 입력이 다르면 접수하지 않는다."""
        from backend.app.agents import report
        jid = self.ask()
        self.confirm(jid, formats=['PDF']); self.work()
        saved = self.state(jid)
        task, step = saved.plan.tasks[0], saved.plan.tasks[0].steps[-1]
        rid = saved.report_wait.report_id
        with self.engine.begin() as c:
            again = report.submit(Store(self.db, c), saved, task, step)
        self.assertIsInstance(again, ReportWait)
        self.assertEqual(again.report_id, rid)
        changed = saved.model_copy(deep=True)
        changed.plan.tasks[0].report_request.formats = ['DOCX']
        with self.engine.begin() as c:
            refused = report.submit(Store(self.db, c), changed, changed.plan.tasks[0], changed.plan.tasks[0].steps[-1])
        self.assertIsInstance(refused, ToolResult)
        self.assertEqual(refused.status, 'NO_DATA')
        self.assertIn('새 질문', refused.error)
        self.assertEqual(len(self.reports()), 1)

    # ── 리포트 파일 (D-90) ───────────────────────────────────────
    def generated(self, tasks, question):
        """승인까지 진행한 뒤 리포트 작업을 실제 generate로 돌린다. PDF 변환(Playwright)만 가로채 문서를 받는다.
        생성 중 통계를 다시 조회하면 실패하도록 trade.aggregate를 막는다(결과 기반 리포트 전용)."""
        from contextlib import nullcontext
        from unittest.mock import patch
        jid = self.ask(tasks, question)
        self.confirm(jid, formats=['PDF', 'DOCX']); self.work()
        self.assertEqual(self.job(jid)['status'], 'WAITING_REPORT')
        documents = []
        requery = AssertionError('승인한 결과 기반 리포트는 통계를 다시 조회하지 않습니다') if tasks[0]['intent'] != 'REPORT' else None
        with patch('backend.app.api.reports.report_pdf', side_effect=lambda doc, settings, **kwargs: documents.append(doc) or b'%PDF-test'), \
             patch('backend.app.api.trade.aggregate', side_effect=requery) if requery else nullcontext():
            self.work()
        self.assertEqual(self.reports()[0]['status'], 'READY')
        return documents[0]

    def test_result_based_report_uses_the_ondemand_template_without_requery(self):
        """R19 선행 결과로 만든 리포트도 요청형 템플릿으로 만든다. 승인한 결과표를 그대로 싣고, 결과표에 없는 섹션은
        '해당 분석 결과에 포함되지 않음'으로 알리며, 통계를 다시 조회하지 않는다(D-90)."""
        from backend.app.services.report_templates import render
        tasks = [{'intent': 'STATISTICS', 'question': '수출액 조회', 'slots': {'recent_months': 3}},
                 {'intent': 'REPORT', 'question': '조회 결과로 리포트', 'source_tasks': [1]}]
        doc = self.generated(tasks, '수출액 조회 후 리포트')
        self.assertIn('template_pdf', doc)
        ctx = doc['template_pdf']['context']
        table = next(t for s in doc['sections'] for t in s['tables'])
        result = ctx['results'][0]
        self.assertEqual(result['result_id'], table['result_id'])
        self.assertEqual([c['label'] for c in result['columns']],  # 열 이름 그대로, 단위가 있으면 머리글에 붙인다
                         [c['label'] + (f" ({c['unit']})" if c.get('unit') else '') for c in table['columns']])
        self.assertEqual(len(result['rows']), len(table['rows']))
        self.assertEqual(result['rows'][0][-1], f"{table['rows'][0]['amount_usd']:,.0f}")  # 결과표 값 그대로(재계산 없음)
        self.assertTrue(result['chart'])  # 월별 추이 결과는 같은 꺾은선 그래프로
        html = render('RPT_ON_DEMAND', ctx)
        self.assertIn('승인한 분석 결과', html)
        self.assertIn('해당 분석 결과에 포함되지 않음', html)
        self.assertIn(result['rows'][0][-1], html)
        self.assertNotIn('수출액 상위', html)  # 결과에 없는 요청형 섹션(주요 수출국 등)을 지어내지 않는다
        # 저장된 섹션에 GROWTH_RISK가 있어도 ML을 돌리지 않았으므로 출처표에 '유망시장 예측' 행이 없고, 월평균 안내도 싣지 않는다
        self.assertIn('GROWTH_RISK', ctx['conditions']['sections'])
        self.assertEqual([s['label'] for s in ctx['sources']], ['분석 결과표'])
        self.assertNotIn('자체 ML 모델', html)
        self.assertNotIn('monthly_average_note', ctx['summary'])

    def test_result_report_cells_are_readable_in_pdf_and_docx(self):
        """R21 순위 결과의 일회성 판정·순위 상태는 원시 값({'suspected': ...}, NOT_APPLICABLE) 대신 읽을 수 있는 말로 싣는다."""
        import io
        from docx import Document
        from backend.app.api import files
        from backend.app.services.report_templates import render
        tasks = [{'intent': 'RANKING', 'question': '수출 순위', 'slots': {'recent_months': 3, 'ranking_group': 'COUNTRY'}},
                 {'intent': 'REPORT', 'question': '순위 결과로 리포트', 'source_tasks': [1]}]
        doc = self.generated(tasks, '수출 순위 조회 후 리포트')
        html = render('RPT_ON_DEMAND', doc['template_pdf']['context'])
        docx = '\n'.join(c.text for t in Document(io.BytesIO(files.report_docx(doc, self.settings))).tables
                         for row in t.rows for c in row.cells)
        for text in (html, docx):
            self.assertNotIn("'suspected'", text)
            self.assertNotIn('NOT_APPLICABLE', text)
            self.assertIn('판정 전', text)

    def test_result_trend_chart_uses_monthly_amounts_and_no_summing_notice(self):
        """R23 기간 합계(total_usd)가 앞 열에 있고 비교 기준월(compare_month)이 있어도 차트는 월별 금액(amount_usd)을
        국가·품목별 한 선으로 그린다. 결과 기반 PDF는 품목이 여러 개여도 '합산한 결과' 안내를 싣지 않는다."""
        from datetime import date
        from backend.app.services.report_ondemand_context import populate_results
        from backend.app.services.report_templates import render
        number = lambda key: {'key': key, 'label': key, 'type': 'number', 'unit': 'USD'}
        text = lambda key: {'key': key, 'label': key, 'type': 'string', 'unit': None}
        columns = [text('country_name'), text('name'), number('total_usd'), number('monthly_avg_usd'), text('month'),
                   number('amount_usd'), text('compare_month'), number('compare_amount_usd')]
        for label, compare_months in (('기간 길이가 다른 비교', [None, None]), ('기간 길이가 같은 비교', ['2024-01', '2024-02'])):
            with self.subTest(label):
                rows = [{'country_name': '미국', 'name': '화장품', 'total_usd': 300, 'monthly_avg_usd': 150, 'month': m,
                         'amount_usd': v, 'compare_month': cm, 'compare_amount_usd': None if cm is None else 50}
                        for m, v, cm in zip(('2025-01', '2025-02'), (100, 200), compare_months)]
                content = {'source_tables': [{'result_id': 'r1', 'kind': 'TREND', 'columns': columns, 'rows': rows}],
                           'source_results': [{'result_id': 'r1', 'completeness': 'COMPLETE', 'limitations': []}]}
                ctx = {'conditions': {'hs_items': [{'hs_code': h, 'name': n, 'label': n, 'display': f'{n} (HS {h})'} for h, n in (('330499', '화장품'), ('330410', '립 제품'))], 'countries': []},
                       'meta': {}, 'articles': [], 'report': {}}
                populate_results({'sections': ['KPI'], 'compare': 'NONE', 'metric': 'AMOUNT'}, ctx,
                                 date(2025, 1, 1), date(2025, 2, 28), content)
                self.assertEqual(ctx['results'][0]['chart'], {'labels': ['2025-01', '2025-02'],
                                                              'series': [{'name': '미국 · 화장품', 'values': [100, 200]}]})
        ctx['report'] = {'report_id': '1', 'report_type': 'RPT_ON_DEMAND', 'title': 't', 'period_label': 'p', 'kind_label': 'k',
                         'cover_eyebrow': 'e', 'cover_lead': 'l', 'footer_label': 'f', 'is_sample': False,
                         'published_at': '2026-11-09T12:00:00+09:00'}
        ctx.update(verification=None, article_limit=None, article_policy_note='', subscription={'label': ''})
        self.assertNotIn('합산한 결과', render('RPT_ON_DEMAND', ctx))

    def _result_ctx(self, columns, rows, kind='TREND'):
        from datetime import date
        from backend.app.services.report_ondemand_context import populate_results
        content = {'source_tables': [{'result_id': 'r1', 'kind': kind, 'columns': columns, 'rows': rows}],
                   'source_results': [{'result_id': 'r1', 'completeness': 'COMPLETE', 'limitations': []}]}
        ctx = {'conditions': {'hs_items': [], 'countries': []}, 'meta': {}, 'articles': [],
               'report': {'report_id': '1', 'report_type': 'RPT_ON_DEMAND', 'title': 't', 'period_label': 'p', 'kind_label': 'k',
                          'cover_eyebrow': 'e', 'cover_lead': 'l', 'footer_label': 'f', 'is_sample': False,
                          'published_at': '2026-11-09T12:00:00+09:00'},
               'verification': None, 'article_limit': None, 'article_policy_note': '', 'subscription': {'label': ''}}
        populate_results({'sections': ['KPI'], 'compare': 'NONE', 'metric': 'AMOUNT'}, ctx, date(2025, 1, 1), date(2025, 6, 30), content)
        return ctx

    def test_wide_result_table_is_split_into_column_groups_in_the_pdf(self):
        """R24 기간 길이가 다른 비교처럼 열이 많은 결과표(15열)는 A4에서 잘리지 않게 열 묶음으로 나눈다.
        월·국가·품목 식별 열은 묶음마다 반복하고, 실제 PDF에 마지막 열까지 보인다."""
        from backend.app.services.report_ondemand_context import RESULT_COLUMN_LIMIT
        keys = ['country_name', 'name', 'month', 'amount_usd', 'compare_amount_usd', 'total_usd', 'compare_total_usd',
                'change_pct', 'monthly_avg_usd', 'compare_monthly_avg_usd', 'verified_months', 'requested_months',
                'compare_verified_months', 'verified_subtotal_usd', 'compare_verified_subtotal_usd']
        columns = [{'key': k, 'label': k, 'type': 'string' if k in ('country_name', 'name', 'month') else 'number',
                    'unit': 'USD' if k.endswith('_usd') else None} for k in keys]
        rows = [{**{k: 1000 + i for i, k in enumerate(keys)}, 'country_name': '미국', 'name': '화장품', 'month': '2025-01',
                 'compare_verified_subtotal_usd': 13579}]
        ctx = self._result_ctx(columns, rows)
        parts = ctx['results']
        self.assertGreater(len(parts), 1)
        shown = []
        for part in parts:
            labels = [c['key'] for c in part['columns']]
            self.assertLessEqual(len(labels), RESULT_COLUMN_LIMIT)
            self.assertEqual(labels[:3], ['month', 'country_name', 'name'])  # 식별 열 반복
            self.assertEqual(len(part['rows'][0]), len(labels))
            shown += labels[3:]
        self.assertEqual(sorted(shown), sorted(set(keys) - {'country_name', 'name', 'month'}))  # 빠지거나 겹치는 열 없음
        self.assertTrue(parts[0]['chart'])
        self.assertIsNone(parts[1]['chart'])  # 그래프는 첫 묶음에만
        self.assertIn('열 2/', parts[1]['title'])
        self.assertIn('결과 1개', ctx['sources'][0]['scope'])  # 열 묶음은 결과 수를 늘리지 않는다
        # 실제 PDF: 마지막 열 값(13,579)이 페이지 밖으로 잘리지 않고 글자로 남는다
        from pathlib import Path
        from backend.app.api.config import Settings
        # 운영·CI와 같은 설정(API_REPORT_FONT)을 먼저 쓰고, 없으면 Windows 기본 글꼴(test_report_pagination과 같은 방식)
        font = Path(Settings().get('API_REPORT_FONT', 'C:/Windows/Fonts/malgun.ttf'))
        if not font.is_file():
            self.skipTest('PDF 한글 글꼴 없음')
        from io import BytesIO
        from pypdf import PdfReader
        from backend.app.api.common import ApiError
        from backend.app.services.report_pdf import template_pdf
        try:
            pdf = template_pdf('RPT_ON_DEMAND', ctx, {'API_REPORT_FONT': str(font)})
        except ApiError as exc:
            self.skipTest(f'PDF 렌더러 없음: {exc}')
        text = ''.join(page.extract_text() for page in PdfReader(BytesIO(pdf)).pages)
        self.assertIn('13,579', text)
        # 한글 글꼴을 실제로 썼는지: 같은 행의 국가·품목명이 글자로 남아야 한다(글꼴이 거부되면 숫자만 남는다)
        self.assertIn('미국', text)
        self.assertIn('화장품', text)

    def test_same_named_products_get_separate_chart_series(self):
        """R25 이름이 같은 품목(0409·040900 모두 '천연꿀')은 HS 코드로 시리즈를 나누고 범례에 HS 코드를 붙인다."""
        columns = [{'key': k, 'label': k, 'type': 'number' if k == 'amount_usd' else 'string', 'unit': 'USD' if k == 'amount_usd' else None}
                   for k in ('country_name', 'name', 'hs_code', 'month', 'amount_usd')]
        rows = [{'country_code': 'US', 'country_name': '미국', 'name': '천연꿀', 'hs_code': hs, 'hs_ver': 'HS2022', 'month': m, 'amount_usd': v}
                for hs, values in (('0409', (100, 200)), ('040900', (10, 20))) for m, v in zip(('2025-01', '2025-02'), values)]
        chart = self._result_ctx(columns, rows)['results'][0]['chart']
        self.assertEqual(chart['series'], [{'name': '미국 · 천연꿀 (HS 0409)', 'values': [100, 200]},
                                           {'name': '미국 · 천연꿀 (HS 040900)', 'values': [10, 20]}])
        # HS 코드도 같고 판본만 다르면 판본까지 붙인다
        rows[2:] = [{**r, 'hs_code': '0409', 'hs_ver': 'HS2017'} for r in rows[2:]]
        names = [s['name'] for s in self._result_ctx(columns, rows)['results'][0]['chart']['series']]
        self.assertEqual(names, ['미국 · 천연꿀 (HS 0409 · HS2022 · US)', '미국 · 천연꿀 (HS 0409 · HS2017 · US)'])

    def test_result_tables_are_bounded_and_keep_missing_notes(self):
        """R22 결과표는 최대 200행만 PDF에 싣고 전체 행 수를 알린다. 부분 결과(PARTIAL)의 누락 사유를 그대로 싣고,
        시리즈가 5개를 넘는 추이는 그래프 없이 표만 둔다."""
        from datetime import date
        from backend.app.services.report_ondemand_context import RESULT_ROW_LIMIT, populate_results
        from backend.app.services.report_templates import render
        columns = [{'key': 'country_name', 'label': '국가', 'type': 'string', 'unit': None},
                   {'key': 'month', 'label': '기준월', 'type': 'string', 'unit': None},
                   {'key': 'amount_usd', 'label': '수출액', 'type': 'number', 'unit': 'USD'}]
        rows = [{'country_name': f'국가{i % 6}', 'month': f'2025-{i // 6 % 12 + 1:02d}', 'amount_usd': i * 1000} for i in range(250)]
        content = {'source_tables': [{'result_id': 'r1', 'kind': 'TREND', 'columns': columns, 'rows': rows, 'total_rows': 250}],
                   'source_results': [{'result_id': 'r1', 'completeness': 'PARTIAL', 'limitations': ['2025-12 통계 미공개']}]}
        saved = {'sections': ['KPI', 'LOCAL_ISSUES'], 'compare': 'NONE', 'metric': 'AMOUNT'}
        ctx = {'conditions': {'hs_items': [], 'countries': []}, 'meta': {}, 'articles': [], 'report': {}}
        populate_results(saved, ctx, date(2025, 1, 1), date(2025, 12, 31), content)
        result = ctx['results'][0]
        self.assertEqual(len(result['rows']), RESULT_ROW_LIMIT)
        from backend.app.services.report_ondemand_context import _cell  # 비율은 리포트 공통 소수 2자리(REPORT_DIGITS)
        self.assertEqual(_cell(64.5, {'key': 'share_pct', 'type': 'number', 'unit': '%'}), '64.50%')
        self.assertEqual(_cell(1234.5, {'key': 'amount_usd', 'type': 'number', 'unit': 'USD'}), '1,234.50')
        self.assertIn('전체 250행 중 200행 표시', result['note'])
        self.assertIsNone(result['chart'])  # 국가 6개 > 시리즈 상한 5
        texts = [r['content'] for r in ctx['summary']['check_points']]
        self.assertIn('2025-12 통계 미공개', texts)
        self.assertIn('현지 이슈: 해당 분석 결과에 포함되지 않음', texts)
        self.assertNotIn('주요 지표', ' '.join(texts))  # 추이 결과가 있으므로 주요 지표·월별 추이는 포함된 항목
        self.assertIn('일부 누락', ctx['summary']['check_points'][0]['basis'])
        ctx['report'] = {'report_id': '1', 'report_type': 'RPT_ON_DEMAND', 'title': 't', 'period_label': 'p', 'kind_label': 'k',
                         'cover_eyebrow': 'e', 'cover_lead': 'l', 'footer_label': 'f', 'is_sample': False,
                         'published_at': '2026-11-09T12:00:00+09:00'}
        ctx.update(verification=None, article_limit=None, article_policy_note='', subscription={'label': ''})
        html = render('RPT_ON_DEMAND', ctx)  # 결과 기반 모드의 템플릿 변수만으로 렌더링된다(빠진 변수는 StrictUndefined 오류)
        self.assertEqual(html.count('<td class="r">249,000</td>'), 0)  # 201번째 행부터는 PDF에 없다
        self.assertIn('<td class="r">199,000</td>', html)

    def test_condition_report_keeps_the_ondemand_template(self):
        """R20 조건으로 요청한 채팅 리포트는 지금처럼 요청형 템플릿의 집계 섹션을 쓴다(결과 기반 모드가 아님)."""
        from datetime import date
        from backend.app.services.report_templates import render
        with self.engine.begin() as c:  # 현지 이슈 섹션은 기사 표가 비어 있으면 적재 대기로 멈춘다
            c.execute(self.meta.tables['article'].insert(), {'source_code': 'KOTRA_MARKET', 'external_id': 'r20', 'title': '기사',
                'published_date': date(2025, 12, 1), 'source_url': 'https://example.org/r20', 'body_masked': '본문',
                'visibility_status': 'VISIBLE', 'admin_meta_json': {}})
        doc = self.generated(REPORT, '수출 리포트 생성')
        ctx = doc['template_pdf']['context']
        self.assertNotIn('results', ctx)
        self.assertIn('amount_usd', ctx['summary']['kpi'])
        html = render('RPT_ON_DEMAND', ctx)
        self.assertNotIn('승인한 분석 결과', html)
        self.assertIn('월별 수출 추이', html)

    def test_waiting_checker_visits_every_waiting_chat(self):
        """R10 대기 확인기는 한 번에 size건만 보더라도 커서로 이어 보며 모든 대기 채팅을 깨운다."""
        from backend.app.agents import report
        jobs = []
        for i in range(3):
            jid = self.ask(key=f'wait-{i}')
            self.confirm(jid); self.work()
            self.finish_report('RUNNING')  # 파일 생성은 다른 워커가 맡는 중(이 워커가 리포트 작업을 집지 않게)
            self.assertEqual(self.job(jid)['status'], 'WAITING_REPORT')
            jobs.append(jid)
        self.finish_report('READY')
        for _ in range(3):
            with self.engine.begin() as c:
                report.wake_ready(Store(self.db, c), 1)
        self.assertEqual([self.job(j)['status'] for j in jobs], ['QUEUED'] * 3)
