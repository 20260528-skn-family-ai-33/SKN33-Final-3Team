import unittest
from unittest.mock import Mock, patch
from backend.tests.api_support import reference_data, test_engine
from backend.tests.test_agent_state import state
from backend.app.agents.clarification import answer_patch, apply_answer
from backend.app.agents.conditions import check_conditions, country_matches
from backend.app.agents.state import TaskSlots
from backend.app.api.database import Database, Store
from backend.app.api.config import Settings
from backend.app.api.common import ApiError


class ConditionTest(unittest.TestCase):
    def test_market_hs4_uses_only_eligible_children_ranked_by_exports(self):
        import pandas as pd

        hs = self.store.t('hs_code')
        for code in range(330410,330416):
            self.store.c.execute(hs.insert().values(hs_code=str(code),hs_ver='HS2022',source_edition='HS2022',
                                                   depth=6,name_ko=f'품목 {code}'))
        self.state.input.question='미국 HS 3304 유망시장'
        self.task.intent='PROMISING_MARKET'
        self.task.slots=TaskSlots(countries=[{'mention':'미국','name':'미국'}],products=['3304'])
        predictions=pd.DataFrame([
            ('US','330415',120_000),('US','330499',340_000),('CN','330410',9_000_000),
        ],columns=['country_code','hs6','exp_12m'])
        with patch('backend.app.agents.market.loaded',return_value=({'predictions':predictions},None,{})):
            check_conditions(self.store,self.state,self.step)
        self.assertEqual([choice.choice_id for choice in self.state.pending.choices],['330499','330415'])

    def test_market_hs4_auto_selects_the_only_eligible_child(self):
        import pandas as pd

        self.state.input.question='HS 3304 유망시장'
        self.task.intent='PROMISING_MARKET'
        self.task.slots=TaskSlots(products=['3304'])
        predictions=pd.DataFrame([('US','330499',120_000)],columns=['country_code','hs6','exp_12m'])
        with patch('backend.app.agents.market.loaded',return_value=({'predictions':predictions},None,{})):
            check_conditions(self.store,self.state,self.step)
        self.assertEqual(self.step.status,'DONE')
        self.assertEqual([item.hs_code for item in self.task.conditions.hs_items],['330499'])
        self.assertIn('330499',self.task.applied_defaults[0])

    def test_explicit_country_code_is_kept(self):
        for mention in ('US', 'us'):
            with self.subTest(mention=mention):
                self.state.input.question=f'{mention} 수출액'
                self.task.slots=TaskSlots(countries=[{'mention':mention,'name':'미국'}],products=[],recent_months=3)
                check_conditions(self.store,self.state,self.step)
                self.assertEqual(self.step.status,'DONE')
                self.assertEqual(self.task.conditions.country_codes,['US'])

    def test_unknown_or_mismatched_country_code_asks_instead_of_dropping(self):
        for mention, choices in (('XX', ['US']), ('CN', ['US', 'CN'])):  # 없는 코드(UK→GB 같은 경우), 국가명과 어긋난 코드
            with self.subTest(mention=mention):
                self.state.pending=None; self.state.ask_counts={}
                self.state.input.question=f'{mention} 수출액'
                self.task.slots=TaskSlots(countries=[{'mention':mention,'name':'미국'}],products=[],recent_months=3)
                check_conditions(self.store,self.state,self.step)
                self.assertEqual(self.state.pending.kind,'COUNTRY')
                self.assertEqual([c.choice_id for c in self.state.pending.choices],choices)

    def test_non_country_abbreviation_is_not_a_partner(self):
        self.state.input.question='TV 수출액'
        self.task.slots=TaskSlots(countries=[{'mention':'TV','name':'텔레비전'}],products=[],recent_months=3)
        check_conditions(self.store,self.state,self.step)
        self.assertEqual(self.step.status,'DONE')
        self.assertEqual(self.task.conditions.country_codes,[])

    def test_followup_echoing_previous_conditions_inherits_them(self):
        from backend.app.agents.state import Conditions
        self.state.input.question='왜 증가한거야?'
        self.state.input.context.conditions=Conditions(country_codes=['CN'],hs_items=[{'hs_code':'330499','hs_ver':'HS2022'}])
        self.task.slots=TaskSlots(countries=[{'mention':'중국','name':'중국'}],products=['330499'],recent_months=3)
        check_conditions(self.store,self.state,self.step)
        self.assertEqual(self.step.status,'DONE')
        self.assertEqual(self.task.conditions.country_codes,['CN'])
        self.assertEqual([h.hs_code for h in self.task.conditions.hs_items],['330499'])
        # 최근 질문의 품목 표현을 다시 적어도 이어받는다.
        from datetime import datetime
        from backend.app.agents.state import TurnSummary
        self.state.input.context.recent=[TurnSummary(question='중국에 기초화장품 얼마나 팔았어?',intent='STATISTICS',answered_at=datetime(2026,11,4))]
        self.step.status='PENDING'
        self.task.slots=TaskSlots(countries=None,products=['기초화장품'],recent_months=3)
        check_conditions(self.store,self.state,self.step)
        self.assertEqual(self.step.status,'DONE')
        self.assertEqual([h.hs_code for h in self.task.conditions.hs_items],['330499'])
        # 질문에 새 국가가 있으면 플래너가 다시 적은 이전 국가는 이어받지 않는다(PR #61 재검수).
        self.state.input.question='미국은 왜 증가한거야?'
        self.step.status='PENDING'
        self.task.slots=TaskSlots(countries=[{'mention':'중국','name':'중국'},{'mention':'미국','name':'미국'}],products=None,recent_months=3)
        check_conditions(self.store,self.state,self.step)
        self.assertEqual(self.step.status,'DONE')
        self.assertEqual(self.task.conditions.country_codes,['US'])
        # 같은 국가가 이어받기와 코드 표현으로 두 번 들어와도 하나만 남는다.
        self.state.input.question='CN은 왜 증가한거야?'
        self.step.status='PENDING'
        self.task.slots=TaskSlots(countries=[{'mention':'중국','name':'중국'},{'mention':'CN','name':'중국'}],products=None,recent_months=3)
        check_conditions(self.store,self.state,self.step)
        self.assertEqual(self.step.status,'DONE')
        self.assertEqual(self.task.conditions.country_codes,['CN'])
        self.state.input.question='왜 증가한거야?'
        cases=[
            ([{'mention':'미국','name':'미국'}],None,['중국에 기초화장품 얼마나 팔았어?']),  # 이전 조건이 아닌 국가
            (None,['기초화장품'],['중국에 기초화장품 얼마나 팔았어?','중국 수출액 보여줘']),  # 직전 질문이 아닌 예전 턴의 표현
            (None,['장비'],['중국에 기초화장품 장비 얼마나 팔았어?'])]  # 직전 질문에 있지만 이전 품목과 무관한 일부 표현
        for countries,products,questions in cases:
            with self.subTest(countries=countries,products=products):
                self.state.input.context.recent=[TurnSummary(question=q,intent='STATISTICS',answered_at=datetime(2026,11,4)) for q in questions]
                self.step.status='PENDING'
                self.task.slots=TaskSlots(countries=countries,products=products,recent_months=3)
                check_conditions(self.store,self.state,self.step)  # 질문 원문에 있어야 하는 규칙은 그대로다
                self.assertEqual(self.step.status,'FAILED')

    def test_korea_is_reporting_country_and_clears_inherited_partner(self):
        from backend.app.agents.state import Conditions
        for name in ('한국','대한민국','우리나라'):
            self.state.input.question=f'{name} 수출액 보여줘'
            self.state.input.context.conditions=Conditions(country_codes=['US'])
            self.task.slots=TaskSlots(countries=[{'mention':name,'name':'대한민국'}],products=[],recent_months=3)
            check_conditions(self.store,self.state,self.step)
            self.assertIsNone(self.state.pending)
            self.assertEqual(self.task.conditions.country_codes,[])

    def test_korea_reporting_country_keeps_explicit_partner(self):
        self.state.input.question='한국의 중국 수입액 보여줘'
        self.task.slots=TaskSlots(countries=[{'mention':'한국','name':'대한민국'},
            {'mention':'중국','name':'중국'}],products=[],recent_months=3,trade_flow='IMPORT')
        check_conditions(self.store,self.state,self.step)
        self.assertIsNone(self.state.pending)
        self.assertEqual(self.task.conditions.country_codes,['CN'])
        self.assertEqual(self.task.conditions.trade_flow,'IMPORT')

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

    def test_empty_country_list_allows_manual_code_without_second_question(self):
        self.state.input.question='가상나라 수출액'
        self.task.slots=TaskSlots(countries=[{'mention':'가상나라','name':'가상나라'}],recent_months=3)
        check_conditions(self.store,self.state,self.step)
        prompt=self.state.pending
        self.assertEqual(prompt.choices,[])
        with self.assertRaises(ApiError):
            answer_patch(self.store,self.state,{'clarification_id':prompt.clarification_id,'text':'없는나라'})
        self.assertEqual(self.state.pending.clarification_id,prompt.clarification_id)
        apply_answer(self.state,answer_patch(self.store,self.state,{'clarification_id':prompt.clarification_id,'text':'cn'}))
        check_conditions(self.store,self.state,self.step)
        self.assertEqual(self.task.conditions.country_codes,['CN'])
        self.assertEqual(self.state.ask_counts[prompt.condition_key],1)

    def test_two_ambiguous_mentions_do_not_start_a_second_country_prompt(self):
        self.state.input.question='가상나라 미상나라 수출액'
        self.task.slots=TaskSlots(countries=[{'mention':n,'name':n} for n in ('가상나라','미상나라')])
        check_conditions(self.store,self.state,self.step)
        apply_answer(self.state,{'country_codes':['US']})
        check_conditions(self.store,self.state,self.step)
        self.assertEqual(self.step.status,'FAILED')
        self.assertIsNone(self.state.pending)
        self.assertEqual(sum(self.state.ask_counts.values()),1)

    def test_similar_country_requires_confirmation_even_with_single_match(self):
        rows = [{'country_code':c, 'name_ko':n, 'country_role':'COUNTRY'}
                for c,n in [('SD','수단'),('SS','남수단'),('VN','베트남')]]
        self.assertEqual(country_matches('수단',rows,0.6), ('SD',[]))
        self.assertEqual(country_matches('SD',rows,0.6), ('SD',[]))
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
