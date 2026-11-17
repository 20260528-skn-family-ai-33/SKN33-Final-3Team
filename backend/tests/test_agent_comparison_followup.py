"""Confirmed trade scope survives an empty result or a failed query, independent of verified history."""
from contextlib import nullcontext
from datetime import date
from unittest import TestCase
from unittest.mock import patch

from backend.app.agents.context import year_over_year_base
from backend.app.agents.runtime import Services
from backend.app.agents.state import Conditions, ToolResult
from backend.tests import test_agent_report as report


class ComparisonFollowupTest(TestCase):
    setUp = report.ReportAgentTest.setUp
    tearDown = report.ReportAgentTest.tearDown
    signup = report.ReportAgentTest.signup
    post = report.ReportAgentTest.post
    agent = report.ReportAgentTest.agent
    job = report.ReportAgentTest.job
    work = report.ReportAgentTest.work

    def test_comparison_uses_confirmed_trade_scope_even_after_failure(self):
        question = '2025년 베트남 화장품 수출액 추이 보여줘'
        self.agent([{'intent': 'STATISTICS', 'question': question,
            'slots': {'products': ['화장품'], 'countries': [{'mention':'베트남', 'name':'베트남'}],
                      'period': {'start':'2025-01-01', 'end':'2025-12-31'}}}])
        self.signup()
        with self.engine.begin() as conn:
            conn.execute(self.meta.tables['country'].insert(), {'country_code':'VN', 'name_ko':'베트남', 'country_role':'COUNTRY'})
            conn.execute(self.meta.tables['export_summary'].insert(), [
                {'trade_month':date(year, month, 1), 'hs6_code':'330499', 'hs_ver':'HS2022',
                 'country_code':'VN', 'amount_usd':100 * (year - 2023) + month}
                for year in (2024, 2025) for month in range(1, 13)])
        for outcome in ('OK', 'NO_DATA', 'FAILED', 'WORKER_ERROR'):
            with self.subTest(outcome=outcome):
                sid = self.post('chat/sessions', {}).json()['session']['session_id']
                def send(text, key):
                    response = self.post(f'chat/sessions/{sid}/messages', {'question':text, 'client_message_id':key})
                    self.assertEqual(response.status_code, 202, response.text)
                    return response.json()['job']['job_id']

                jid = send(question, 'initial-' + outcome)
                self.work()
                job = self.job(jid)
                self.assertEqual(job['status'], 'NEEDS_INPUT', job)
                prompt = job['clarification']
                response = self.post(f'chat/jobs/{jid}/clarifications',
                                     {'clarification_id':prompt['clarification_id'], 'choice_ids':['330499']})
                self.assertEqual(response.status_code, 202, response.text)
                failure = nullcontext()
                if outcome in ('NO_DATA', 'FAILED'):
                    # 앞 조회의 상태만 바꾼다. 두 번째 조회와 저장·복원·SQL 경로는 실제 코드를 사용한다.
                    result = ToolResult(status=outcome, error='조건에 맞는 데이터가 없습니다.' if outcome == 'NO_DATA'
                                        else '무역 통계 조회에 실패했습니다.')
                    failure = patch.object(Services, 'tool', return_value=result)
                elif outcome == 'WORKER_ERROR':
                    failure = patch.object(Services, 'tool', side_effect=RuntimeError('worker failed after scope was checked'))
                with failure:
                    self.work()
                job = self.job(jid)
                if outcome == 'WORKER_ERROR':
                    self.assertEqual(job['status'], 'FAILED', job)
                else:
                    self.assertEqual(job['status'], 'SUCCEEDED', job)
                    self.assertEqual(job['answer']['tasks'][0].get('query_status'), 'FAILED' if outcome=='FAILED' else None)
                followup = send('전년과 비교해줘', 'compare-' + outcome)
                self.work()
                answer = self.job(followup)['answer']
                self.assertEqual(answer['answer_status'], 'ANSWERED', answer)
                cond = answer['conditions']
                self.assertEqual(cond['country_codes'], ['VN'])
                self.assertEqual(cond['hs_items'], [{'hs_code':'330499','hs_ver':'HS2022'}])
                self.assertEqual(cond['period'], {'start':'2025-01-01','end':'2025-12-31'})
                self.assertEqual(cond['compare_period'], {'start':'2024-01-01','end':'2024-12-31'})
                self.assertEqual(len(answer['tables'][0]['rows']), 12)

    def test_grounded_annual_amount_trend_uses_template_without_losing_custom_calculations(self):
        from backend.app.agents.planner import build_plan, postprocess
        from backend.app.agents.state import PlannerOutput
        for question, template in (
                ('2025년 베트남 화장품 수출액 추이 보여줘', 'TREND'),
                ('2025년 베트남 화장품 월별 수출액 추이 보여줘', 'TREND'),
                ('2025년 베트남 화장품 수입액의 월별 추이를 알려주세요', 'TREND'),
                ('2025년 베트남 화장품 상위 국가 수출액 추이 보여줘', 'LLM_SQL'),
                ('2025년 베트남 화장품 수출액 추이와 단가를 보여줘', 'LLM_SQL')):
            with self.subTest(question=question):
                parsed = PlannerOutput(tasks=[{'intent':'STATISTICS', 'question':question, 'custom_query':question,
                    'slots':{'products':['화장품'], 'countries':[{'mention':'베트남','name':'베트남'}]}}])
                plan, _ = build_plan(postprocess(parsed, question))
                query = next(step for step in plan.tasks[0].steps if step.tool=='query_trade_data')
                self.assertEqual(query.args['template'], template)

    def test_trade_trend_with_cause_retains_news_step(self):
        from backend.app.agents.planner import build_plan, postprocess
        from backend.app.agents.state import PlannerOutput
        question = '2025년 중국 화장품 수출 추이와 변화 원인 알려줘'
        slots = {'products':['화장품'], 'countries':[{'mention':'중국','name':'중국'}]}
        parsed = PlannerOutput(tasks=[
            {'intent':'STATISTICS','question':'2025년 중국 화장품 수출 추이','custom_query':'월별 수출액 조회','slots':slots},
            {'intent':'CAUSE','question':'2025년 중국 화장품 수출 변화 원인','slots':slots}])
        plan, _ = build_plan(postprocess(parsed, question))
        trend, cause = plan.tasks
        self.assertEqual(next(s.args['template'] for s in trend.steps if s.tool=='query_trade_data'), 'TREND')
        self.assertIn('search_articles', [s.tool for s in cause.steps])

    def test_new_explicit_scope_does_not_reuse_failed_lookup(self):
        base = Conditions(country_codes=['VN'], period={'start':'2025-03-01','end':'2025-05-31'})
        pending = {'confirmed_trade_conditions':base.model_dump(mode='json')}
        self.assertEqual(year_over_year_base('전년 동기와 비교해 주세요.', pending, None), base)
        self.assertIsNone(year_over_year_base('태국 2024년 수출과 비교해줘', pending, None))
        self.assertIsNone(year_over_year_base('전년과 비교해줘', {}, None))
