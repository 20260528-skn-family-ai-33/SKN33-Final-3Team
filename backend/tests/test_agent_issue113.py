"""이슈 113: 상대국 주어 수출입 방향, 여러 비교 기간을 한 표·차트로."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.tests.api_support import reference_data, test_engine
from backend.app.agents.charts import build_chart
from backend.app.agents.planner import build_plan, postprocess
from backend.app.agents.state import Conditions, PlannerOutput, PlannerSlots
from backend.app.agents.templates import run_template
from backend.app.agents.verify_claims import NameCatalog
from backend.app.agents.verify_evidence import build_facts
from backend.app.api.database import Store, Database
from backend.app.api.results import display_columns

US = [{'mention': '미국', 'name': '미국'}]
Y = lambda y: {'start': f'{y}-01-01', 'end': f'{y}-12-01'}


def plan(question, tasks):
    return postprocess(PlannerOutput.model_validate({'tasks': tasks}), question).tasks


class PartnerSubjectFlowTest(unittest.TestCase):
    def test_partner_subject_is_flipped_to_korea_basis(self):
        tasks = plan('미국이 한국에게 대표적으로 수입 수출하는 항목이뭐야?', [
            {'intent': 'RANKING', 'question': '미국이 한국에게 대표적으로 수입하는 품목 순위',
             'slots': {'countries': US, 'trade_flow': 'IMPORT', 'ranking_group': 'HS6'}},
            {'intent': 'RANKING', 'question': '미국이 한국에게 대표적으로 수출하는 품목 순위',
             'slots': {'countries': US, 'trade_flow': 'EXPORT', 'ranking_group': 'HS6'}}])
        self.assertEqual([(t.question, t.slots.trade_flow) for t in tasks], [
            ('미국이 한국에게 대표적으로 수입하는 품목 순위 (한국의 대미국 수출)', 'EXPORT'),
            ('미국이 한국에게 대표적으로 수출하는 품목 순위 (한국의 대미국 수입)', 'IMPORT')])

    def test_previous_both_flows_does_not_undo_partner_subject_flow(self):
        question = '미국이 한국에서 수입하는 품목은?'
        output = PlannerOutput.model_validate({'tasks': [{'intent': 'RANKING', 'question': question,
            'slots': {'countries': US, 'trade_flow': 'IMPORT', 'ranking_group': 'HS6'}}]})
        [task] = postprocess(output, question, previous_both_trade_flows=True).tasks
        self.assertEqual(task.slots.trade_flow, 'EXPORT')

    def test_already_flipped_by_llm_stays_and_korea_subject_unchanged(self):
        [task] = plan('미국은 한국산 화장품을 얼마나 수입해?', [{'intent': 'STATISTICS', 'question': '미국은 한국산 화장품을 얼마나 수입해?',
            'slots': {'countries': US, 'products': ['화장품'], 'trade_flow': 'EXPORT'}}])
        self.assertEqual(task.slots.trade_flow, 'EXPORT')
        [task] = plan('한국이 미국에 수출하는 품목 순위', [{'intent': 'RANKING', 'question': '한국이 미국에 수출하는 품목 순위',
            'slots': {'countries': US, 'trade_flow': 'EXPORT', 'ranking_group': 'HS6'}}])
        self.assertEqual((task.question, task.slots.trade_flow), ('한국이 미국에 수출하는 품목 순위', 'EXPORT'))
        [task] = plan('미국은 한국의 최대 수출국이야?', [{'intent': 'STATISTICS', 'question': '미국은 한국의 최대 수출국이야?',
            'slots': {'countries': US, 'trade_flow': 'EXPORT'}}])
        self.assertEqual(task.question, '미국은 한국의 최대 수출국이야?')



if __name__ == '__main__':
    unittest.main()
