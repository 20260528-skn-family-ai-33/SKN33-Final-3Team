import unittest
from unittest.mock import Mock
from backend.tests.api_support import reference_data, test_engine
from backend.tests.test_agent_state import state
from backend.app.agents.clarification import answer_patch, apply_answer
from backend.app.agents.conditions import check_conditions, country_matches
from backend.app.agents.state import TaskSlots
from backend.app.api.database import Database, Store
from backend.app.api.config import Settings
from backend.app.api.common import ApiError


class ConditionTest(unittest.TestCase):
    def setUp(self):
        engine, meta = test_engine()
        self.addCleanup(engine.dispose)
        reference_data(engine, meta)
        db = Database(engine)
        db.settings = Settings({'API_HS_VECTOR_ENABLED':'false'})
        self.connection = engine.connect()
        self.addCleanup(self.connection.close)
        self.store = Store(db, self.connection)
        self.state = state()
        self.task = self.state.plan.tasks[0]
        self.step = self.task.steps[0]

    def test_direct_country_reply_outside_suggested_choices_resumes_once(self):
        self.state.input.question='미구 수출액'
        self.task.slots=TaskSlots(countries=[{'mention':'미구','name':'미구'}],recent_months=3)
        self.store.db.settings.values['API_CHAT_COUNTRY_SIMILARITY']='0.4'
        check_conditions(self.store,self.state,self.step)
        prompt=self.state.pending
        self.assertEqual(prompt.kind,'COUNTRY')
        self.assertNotIn('CN',[c.choice_id for c in prompt.choices])
        apply_answer(self.state,answer_patch(self.store,self.state,{'clarification_id':prompt.clarification_id,'text':'중국'}))
        check_conditions(self.store,self.state,self.step)
        self.assertIsNone(self.state.pending)
        self.assertEqual(self.task.conditions.country_codes,['CN'])
        self.assertEqual(self.step.status,'DONE')

    def test_similar_country_requires_confirmation_even_with_single_match(self):
        rows = [{'country_code':c, 'name_ko':n, 'country_role':'COUNTRY'}
                for c,n in [('SD','수단'),('SS','남수단'),('VN','베트남')]]
        self.assertEqual(country_matches('수단',rows,0.6)[1][0]['country_code'], 'SD')
        self.assertIsNone(country_matches('베트낭',rows,0.6)[0])
        self.assertEqual(country_matches('베트남',rows,0.6)[0], 'VN')

    def test_share_basis_is_asked_then_reused_and_foreign_basis_is_no_data(self):
        self.task.intent='MARKET_SHARE'
        self.task.slots=TaskSlots(recent_months=3)
        check_conditions(self.store,self.state,self.step)
        prompt=self.state.pending
        self.assertEqual(prompt.kind,'SHARE_BASIS')
        interpreter=Mock(side_effect=AssertionError('exact label must not call LLM'))
        patch=answer_patch(self.store,self.state,{'clarification_id':prompt.clarification_id,
                            'text':'해외 수입시장의 한국 비중'}, interpreter)
        apply_answer(self.state,patch)
        check_conditions(self.store,self.state,self.step)
        self.assertEqual(self.step.status,'FAILED')
        self.assertEqual(self.state.results[self.step.step_id].status,'NO_DATA')
        self.assertEqual(self.state.ask_counts[prompt.condition_key],1)

    def test_unrelated_text_repeats_only_twice(self):
        self.task.intent='RANKING'
        for _ in range(2):
            check_conditions(self.store,self.state,self.step)
            self.assertEqual(self.state.pending.kind,'RANKING_GROUP')
            apply_answer(self.state,{})
        check_conditions(self.store,self.state,self.step)
        self.assertEqual(self.step.status,'FAILED')
        self.assertIsNone(self.state.pending)

    def test_exact_product_and_country_period_are_resolved_against_sqlite(self):
        self.state.input.question='미국 기초화장품 수출액'
        self.task.slots=TaskSlots(products=['기초화장품'],countries=[{'mention':'미국','name':'미국'}],recent_months=3)
        check_conditions(self.store,self.state,self.step)
        self.assertEqual(self.step.status,'DONE')
        self.assertEqual(self.task.conditions.country_codes,['US'])
        self.assertEqual(self.task.conditions.hs_items[0].hs_code,'330499')
        self.assertEqual(self.task.conditions.period.start.isoformat(),'2025-10-01')

    def test_invalid_choice_does_not_consume_question(self):
        self.task.intent='RANKING'
        check_conditions(self.store,self.state,self.step)
        with self.assertRaises(ApiError):
            answer_patch(self.store,self.state,{'clarification_id':self.state.pending.clarification_id,
                                                'choice_ids':['injected']})
        self.assertEqual(self.state.status,'NEEDS_INPUT')
