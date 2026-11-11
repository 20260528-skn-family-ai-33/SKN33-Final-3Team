"""Report Agent 평가 사례(이슈 #82): 채팅 API → 워커 → 그래프 → 리포트 접수·대기·완료를 실제 코드로 끝까지 돌린다.

LLM 플래너만 Mock으로 고정하고, 조건 확인·미리보기·승인·접수·대기 깨우기·답변 저장은 SQLite에서 실제로 실행한다.
설계 근거: docs/AGENT_DESIGN.md D-76·D-81·D-90·D-92·D-93·D-97, §14.6.
"""
import unittest
from datetime import timedelta
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
        with patch('backend.app.api.reports.report_pdf', side_effect=lambda doc, settings: documents.append(doc) or b'%PDF-test'), \
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
