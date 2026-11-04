"""PR #52 리뷰 지적 회귀 테스트. 실제 LLM 응답·답변 문장 모양을 그대로 옮겼다."""
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from backend.app.agents.digest import display_amounts, korean_usd
from backend.app.agents.planner import build_plan
from backend.app.agents.state import PlannerOutput, ResultBinding, Verification
from backend.app.agents.verify import check_sentence, verify


class PlannerShapeTest(unittest.TestCase):
    """실제 LLM이 낸 모양: 단순 순위에도 자기 자신을 가리키는 target_selections, 복합 질문의 빈 국가명."""

    def test_self_selection_on_simple_ranking_is_ignored(self):
        raw = {'tasks': [{'intent': 'RANKING', 'question': '2025년 한국 수출액 상위 국가 2곳을 보여줘',
            'slots': {'period': {'start': '2025-01-01', 'end': '2025-12-01'}, 'ranking_limit': 2,
                      'ranking_group': 'COUNTRY', 'trade_flow': 'EXPORT', 'countries': [], 'products': None},
            'custom_query': None, 'source_tasks': [],
            'target_selections': [{'source_task': 1, 'dimension': 'COUNTRY', 'limit': 2, 'order_by': 'amount_usd'}]}]}
        output = PlannerOutput.model_validate_json(json.dumps(raw))
        self.assertEqual(output.tasks[0].target_selections, [])
        plan, _ = build_plan(output)
        self.assertEqual(plan.tasks[0].steps[1].args, {'template': 'RANKING', 'top': 2, 'amount_only': True})
        share = {'tasks': [{'intent': 'MARKET_SHARE', 'question': '2025년 한국 수출의 국가별 비중을 알려줘',
            'slots': {'share_basis': 'KOREA_TRADE_PARTNER'}, 'source_tasks': [],
            'target_selections': [{'source_task': 1, 'dimension': 'COUNTRY', 'limit': None, 'order_by': None}]}]}
        self.assertEqual(PlannerOutput.model_validate(share).tasks[0].target_selections, [])

    def test_compound_selection_links_source_and_drops_unnamed_country(self):
        raw = {'tasks': [
            {'intent': 'RANKING', 'question': '2025년 수출 상위 국가 2곳',
             'slots': {'ranking_limit': 2, 'ranking_group': 'COUNTRY', 'countries': []}},
            {'intent': 'STATISTICS', 'question': '그 국가들의 월별 추이',
             'slots': {'countries': [{'mention': '그 국가들', 'name': ''}]}, 'source_tasks': [],
             'target_selections': [{'source_task': 1, 'dimension': 'COUNTRY', 'limit': 2, 'order_by': 'amount_usd'}]}]}
        task = PlannerOutput.model_validate(raw).tasks[1]
        self.assertEqual(task.source_tasks, [1])
        self.assertIsNone(task.slots.countries)  # [](전체 국가)가 아니라 생략: 대상은 선행 결과에서 온다
        plan, _ = build_plan(PlannerOutput.model_validate(raw))
        self.assertEqual(plan.tasks[1].input_bindings[0].selection.limit, 2)

    def test_unnamed_country_without_source_falls_back_to_mention(self):
        raw = {'tasks': [{'intent': 'STATISTICS', 'question': '베트남 수출',
                          'slots': {'countries': [{'mention': '베트남', 'name': ' '}]}}]}
        self.assertEqual(PlannerOutput.model_validate(raw).tasks[0].slots.countries[0].name, '베트남')


class AmountDisplayTest(unittest.TestCase):
    def test_korean_usd_rounds_to_man(self):
        for value, text in [(26_100_000, '2,610만 달러'), (14_100_000, '1,410만 달러'), (40_200_000, '4,020만 달러'),
                            (1_175_000, '118만 달러'), (123_456_789, '1억 2,346만 달러'), (100_000_000, '1억 달러'),
                            (1_234, '1,234달러'), (9_999.6, '1만 달러'), (0, '0달러'), (-26_100_000, '-2,610만 달러')]:
            with self.subTest(value=value):
                self.assertEqual(korean_usd(value), text)

    def test_compose_sees_display_text_and_verify_accepts_it(self):
        digest = {'period': {'start': '2025-01', 'end': '2025-12'}, 'trade_flow': 'EXPORT',
                  'targets': [{'country_name': '미국', 'total_usd': 26_100_000, 'monthly_avg_usd': 2_175_000,
                               'change_pct': 5.2}]}
        shown = display_amounts(digest)['targets'][0]
        self.assertEqual((shown['total_usd'], shown['change_pct']), ('2,610만 달러', 5.2))
        names = ('미국', '중국')
        self.assertTrue(check_sentence(f'2025년 미국 수출 총액은 {shown["total_usd"]}입니다.', [digest], known_names=names)[0])
        for wrong in ('2억6100만 달러', '261억 달러', '2억 6,100만 달러'):
            with self.subTest(wrong=wrong):
                self.assertFalse(check_sentence(f'2025년 미국 수출 총액은 {wrong}입니다.', [digest], known_names=names)[0])


class SentenceCheckTest(unittest.TestCase):
    def digest(self, change):
        return {'period': {'start': '2025-01', 'end': '2025-12'}, 'compare_period': {'start': '2024-01', 'end': '2024-12'},
                'targets': [{'country_name': '미국', 'total_usd': 14_100_000, 'monthly_avg_usd': 1_175_000,
                             'change_pct': change}]}

    def test_direction_word_must_match_sign(self):
        cases = [(5.2, '미국 수출은 전년 대비 5.2% 감소했습니다.', False), (-5.2, '미국 수출은 전년 대비 5.2% 감소했습니다.', True),
                 (5.2, '미국 수출은 전년 대비 5.2% 증가했습니다.', True), (-5.2, '미국 수출은 전년 대비 5.2% 늘었습니다.', False),
                 (-5.2, '미국 수출은 전년 대비 -5.2% 감소했습니다.', True), (5.2, '미국 수출은 전년 대비 +5.2% 감소했습니다.', False),
                 (-5.2, '미국 수출 증감률은 -5.2%입니다.', True)]
        for change, sentence, expected in cases:
            with self.subTest(change=change, sentence=sentence):
                self.assertEqual(check_sentence(sentence, [self.digest(change)], known_names=('미국',))[0], expected)

    def test_real_llm_sentences_pass(self):
        digest = self.digest(5.2)
        for sentence in ['2025년 미국 수출은 총 1,410만 달러이며, 월평균 수출액은 117.5만 달러입니다.',
                         '2025년 미국 수출은 총 1,410만 달러이며, 월평균 수출액은 118만 달러입니다.',
                         '수출액은 모두 미국 달러(USD) 단위입니다.',
                         '상위 2개국 가운데 미국의 2025년 수출 총액은 1,410만 달러입니다.']:
            with self.subTest(sentence=sentence):
                self.assertTrue(check_sentence(sentence, [digest], known_names=('미국', '중국'))[0])
        self.assertFalse(check_sentence('2025년 미국 수출은 총 117.5만 달러이며, 월평균 수출액은 1,410만 달러입니다.',
                                        [digest], known_names=('미국',))[0])
        big = {'period': {'start': '2025-01', 'end': '2025-12'}, 'targets': [{'total_usd': 120_000_000}]}
        self.assertTrue(check_sentence('2025년 수출 총액은 1억 2,000만 달러입니다.', [big])[0])
        self.assertFalse(check_sentence('2025년 수출 총액은 1억 2,100만 달러입니다.', [big])[0])

    def test_snapshot_keeps_digest_totals(self):
        digest = {'kind': 'RANKING', 'period': {'start': '2025-01', 'end': '2025-12'}, 'total_usd': 500_000_000,
                  'top': [{'rank': 1, 'name': '미국', 'amount_usd': 100_000_000}]}
        snapshot = {'period': {'start': '2025-01', 'end': '2025-12'}, '_verification_source': 'SNAPSHOT',
                    'columns': [], 'rows': [{'rank': 1, 'country_name': '미국', 'export_amount_usd': 100_000_000}]}
        task = SimpleNamespace(task_id='t1', intent='RANKING', answer_status='ANSWERED', steps=[SimpleNamespace(step_id='t1.sql')])
        draft = SimpleNamespace(conclusion=[SimpleNamespace(sentences=['2025년 전체 수출 총액은 5억 달러입니다.'])],
                                explanation=[], turn_summary='')
        state = SimpleNamespace(plan=SimpleNamespace(tasks=[task]), draft=draft, verification=Verification(),
                                results={'t1.sql': SimpleNamespace(digest=digest)})
        verify(state, snapshots={'t1.sql': snapshot})
        self.assertEqual(state.verification.status, 'PASS')


class BindingTest(unittest.TestCase):
    def test_two_selections_accumulate(self):
        from backend.app.agents.bindings import resolve
        from backend.app.agents.state import Conditions
        stored = {'period': {'start': '2025-01-01', 'end': '2025-12-01'}, 'trade_flow': 'EXPORT',
                  'country_codes': [], 'hs_items': []}
        countries = {'conditions': stored, 'meta': {'kind': 'RANKING', 'group': 'COUNTRY'}, 'rows': [
            {'country_code': 'US', 'export_amount_usd': 300}, {'country_code': 'CN', 'export_amount_usd': 200},
            {'country_code': 'VN', 'export_amount_usd': 100}]}
        products = {'conditions': stored, 'meta': {'kind': 'RANKING', 'group': 'HS6'}, 'rows': [
            {'hs_item': {'hs_code': '330499', 'hs_ver': 'HS2022'}, 'export_amount_usd': 50},
            {'hs_item': {'hs_code': '330410', 'hs_ver': 'HS2022'}, 'export_amount_usd': 10}]}
        bindings = [ResultBinding(source_task_id='t1', source_step_id='t1.sql',
                                  selection={'dimension': 'COUNTRY', 'limit': 2, 'order_by': 'amount_usd'}),
                    ResultBinding(source_task_id='t2', source_step_id='t2.sql',
                                  selection={'dimension': 'HS6', 'limit': 1, 'order_by': 'amount_usd'})]
        task = SimpleNamespace(task_id='t3', input_bindings=bindings, resolved={}, applied_defaults=[],
                               question='상위 국가 2곳과 품목 1개의 추이', steps=[SimpleNamespace(step_id='t3.check')])
        state = SimpleNamespace(input=SimpleNamespace(question='국가 상위 2개와 품목 상위 1개 추이'), results={}, pending=None)
        with patch('backend.app.agents.bindings.source_versions', return_value=[(countries, {}), (products, {})]):
            out = resolve(None, state, task, Conditions())
        self.assertEqual(out.country_codes, ['US', 'CN'])
        self.assertEqual([h.hs_code for h in out.hs_items], ['330499'])


class PromisingMarketHs4Test(unittest.TestCase):
    def test_hs4_answer_asks_for_hs6(self):
        from backend.tests.api_support import reference_data, test_engine
        from backend.tests.test_agent_state import state
        from backend.app.agents.conditions import check_conditions
        from backend.app.agents.state import TaskSlots
        from backend.app.api.config import Settings
        from backend.app.api.database import Database, Store
        engine, meta = test_engine(); self.addCleanup(engine.dispose)
        reference_data(engine, meta)
        extra = {'hs_code': '330410', 'hs_ver': 'HS2022', 'name_ko': '입술화장품', 'source_edition': 'HS2022',
                 'original_name_ko': None}
        if meta.tables['hs_code'].c.depth.computed is None: extra['depth'] = 6
        with engine.begin() as c: c.execute(meta.tables['hs_code'].insert(), [extra])
        # 후보 HS6는 예측 모델의 수출액 순서로 고른다(conditions.market_hs4). 모델 파일 없이 순서만 대신한다.
        self.enterContext(patch('backend.app.agents.market.loaded', return_value=(object(), None, {})))
        self.enterContext(patch('backend.app.agents.market.hs6_by_exports', return_value=['330499', '330410']))
        db = Database(engine); db.settings = Settings({'API_HS_VECTOR_ENABLED': 'false'})
        connection = engine.connect(); self.addCleanup(connection.close)
        s = state(); task = s.plan.tasks[0]; step = task.steps[0]
        task.intent = 'PROMISING_MARKET'
        s.input.question = '화장품 유망시장 알려줘'
        task.slots = TaskSlots(products=['화장품'], countries=None)
        task.resolved['t1:hs:화장품'] = {'hs_items': [{'hs_code': '3304', 'hs_ver': 'HS2022'}]}
        check_conditions(Store(db, connection), s, step)
        self.assertEqual(s.pending.kind, 'HS_ITEM')
        self.assertTrue(s.pending.choices)
        self.assertTrue(all(len(c.choice_id) == 6 and c.choice_id.startswith('3304') for c in s.pending.choices))


class WorkerStartupTest(unittest.TestCase):
    def test_missing_redis_modules_are_named(self):
        from backend.app.agents.worker import require_modules
        client = MagicMock()
        client.module_list.return_value = []
        with self.assertRaisesRegex(RuntimeError, 'RediSearch·RedisJSON'):
            require_modules(client)
        client.module_list.return_value = [{b'name': b'search'}, {b'name': b'ReJSON'}]
        require_modules(client)

    def test_chromium_install_failure_keeps_worker_running(self):
        import subprocess
        from backend.app.api import worker
        with patch('sys.argv', ['worker', '--once']), \
                patch('backend.app.services.report_browser.ensure_chromium',
                      side_effect=subprocess.CalledProcessError(1, 'playwright')), \
                patch.object(worker, 'Settings', return_value=MagicMock(engine=MagicMock(), get=lambda k, d=None: d)), \
                patch.object(worker, 'Database'), patch.object(worker, 'run_loop') as loop:
            with self.assertLogs('backend.app.api.worker', 'WARNING'):
                worker.main()
        loop.assert_called_once()


class ResultTableTest(unittest.TestCase):
    def test_empty_compare_month_is_hidden(self):
        from backend.app.api.results import display_columns
        rows = [{'month': '2025-01', 'amount_usd': 1, 'verified_months': 1, 'compare_month': None, 'compare_amount_usd': None}]
        columns = [{'key': k, 'label': k} for k in rows[0]]
        self.assertNotIn('compare_month', [c['key'] for c in display_columns(columns, rows)])
        rows[0].update(compare_month='2024-01', compare_amount_usd=2)
        self.assertIn('비교 기준월', [c['label'] for c in display_columns(columns, rows)])

    def test_truncated_ranking_excel_says_so(self):
        from io import BytesIO
        from openpyxl import load_workbook
        from backend.app.api.files import xlsx_bytes
        table = {'columns': [{'key': 'rank', 'label': '순위'}], 'rows': [{'rank': 1}], 'total_rows': 1000}
        book = load_workbook(BytesIO(xlsx_bytes([table], {'truncated': True, 'rows_total': 4321}, [])))
        notes = [row for row in book['정보'].iter_rows(values_only=True) if row[0] == '안내']
        self.assertIn('4,321행', notes[0][1])


@unittest.skipUnless(os.environ.get('AGENT_SQL_MYSQL_URL'), '실제 MySQL 검증 전용: AGENT_SQL_MYSQL_URL에 접속 URL')
class MysqlPercentTest(unittest.TestCase):
    """pymysql은 빈 파라미터를 받으면 %를 포맷 문자로 읽는다. SQLite로는 재현되지 않아 실제 MySQL로 확인한다."""

    def test_percent_in_generated_sql_runs(self):
        from sqlalchemy import create_engine
        from backend.app.agents.sql import execute_readonly
        from backend.app.agents.state import SqlDraft
        engine = create_engine(os.environ['AGENT_SQL_MYSQL_URL']); self.addCleanup(engine.dispose)
        draft = SqlDraft(sql="SELECT CASE WHEN '수출 화장품 통계' LIKE '%화장품%' THEN DATE_FORMAT('2025-03-01','%Y-%m') END AS value",
                         columns=[{'key': 'value', 'label': '결과', 'type': 'string', 'unit': None}], summary='조회', unsupported=None)
        rows, _ = execute_readonly(engine, draft)
        self.assertEqual(rows, [{'value': '2025-03'}])
