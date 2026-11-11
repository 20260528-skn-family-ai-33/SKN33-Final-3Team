import time
import unittest
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, patch

import fakeredis

from backend.app.agents.clarification import answer_patch, apply_answer
from backend.app.agents.conditions import check_conditions
from backend.app.agents.context import ChatContext, completed_context
from backend.app.agents.planner import article_retry_plan, build_plan, postprocess
from backend.app.agents.state import Draft, PlannerOutput
from backend.app.agents.tools import article_query, article_result
from backend.app.api.config import Settings
from backend.app.api.database import Database, Store
from backend.app.api.redis_store import RedisState
from backend.app.services.rag.gateway import ModelGateway
from backend.app.services.rag.runtime import ExecutionContext, RagDeadlineExceeded
from backend.evals.agent.run import agent_state
from backend.tests.api_support import reference_data, test_engine


class ArticleRetryTest(unittest.TestCase):
    def test_timeout_then_retry_restores_selected_product_period_and_original_query(self):
        engine, meta = test_engine()
        self.addCleanup(engine.dispose)
        reference_data(engine, meta)
        db = Database(engine)
        db.settings = Settings({'API_HS_VECTOR_ENABLED': 'false'})
        question = '글로벌 메모리 반도체 수요 증가에…中 YMTC 역대 최대 규모 증설 관련 시장 영향을 알려줘'
        state = agent_state(question)
        output = PlannerOutput(tasks=[{'intent': 'LOCAL_ISSUES', 'question': question,
            'slots': {'products': ['글로벌 메모리 반도체', 'YMTC']}}])
        state.plan = build_plan(postprocess(output, question))[0]
        task = state.plan.tasks[0]
        with engine.connect() as connection:
            store = Store(db, connection)
            connection.execute(store.t('hs_code').insert().values(hs_code='854232', hs_ver='HS2022',
                depth=6, name_ko='메모리 집적회로', source_edition='HS2022'))
            with patch('backend.app.api.reference._search_depth', return_value={
                    'match_type': 'SIMILAR', 'items': [{'hs_code': '854232',
                    'hs_ver': 'HS2022', 'name_ko': '메모리 집적회로'}]}):
                check_conditions(store, state, task.steps[0])
            prompt = state.pending
            apply_answer(state, answer_patch(store, state, {
                'clarification_id': prompt.clarification_id, 'choice_ids': ['854232']}))
            with patch('backend.app.api.reference._search_depth', side_effect=AssertionError('selected')):
                check_conditions(store, state, task.steps[0])
            self.assertIsNone(state.pending)
            window = task.conditions.article_period
            query = article_query(task, {}, {('854232', 'HS2022'): '메모리'})
            self.assertIn('YMTC', query)
            self.assertNotIn('한국이', query)
            with patch('backend.app.agents.tools.search_news', side_effect=RagDeadlineExceeded('query_embedding_timeout')):
                result = article_result(state, task, task.steps[1], query=query)
            state.results[task.steps[1].step_id] = result
            task.steps[1].status = 'DONE'
            task.answer_status = state.task_outcome(task.task_id)
            state.draft = Draft(conclusion=[{'task_id': task.task_id, 'sentences': [result.error]}], turn_summary='')
            self.assertEqual(task.answer_status, 'NO_DATA')
            saved = completed_context(state)
            self.assertEqual(saved.recent, [])
            self.assertIsNone(saved.conditions)
            redis = RedisState({'API_REDIS_PREFIX': 'test:retry'}, fakeredis.FakeRedis(decode_responses=True))
            redis.acquire_chat('eval', 'first')
            cache = ChatContext(redis)
            cache.publish(saved, 'first')
            durable = {'article_retry': saved.article_retry.model_dump(mode='json')}
            for restored in (cache.load('eval', durable, 'first'), cache.load('eval', durable, 'newer')):
                retry = agent_state('다시검색', restored.model_dump(mode='json'))
                retry.plan = article_retry_plan(retry)
                again = retry.plan.tasks[0]
                with patch('backend.app.api.reference._search_depth', side_effect=AssertionError('must reuse selection')):
                    check_conditions(store, retry, again.steps[0])
                self.assertEqual(again.conditions, task.conditions)
                self.assertIsNone(retry.pending)
                article = SimpleNamespace(article_id=123, title='YMTC 증설', published_date=window.end,
                    source_code=SimpleNamespace(value='HANKYUNG'),
                    summary=[SimpleNamespace(text='증설 계획입니다.', unverified_question_parts=())],
                    evidence=[object()], evidence_role='direct_evidence', reference_limitation=None)
                def respond(request, **kwargs):
                    self.assertEqual(request.conditions.products[0].hs_code, '854232')
                    self.assertEqual(request.conditions.period, window)
                    self.assertIn(question, request.query)
                    return SimpleNamespace(request_id=request.request_id, applied_conditions=request.conditions,
                        failures=[], status='ok', articles=[article])
                with patch('backend.app.agents.tools.search_news', side_effect=respond):
                    result = article_result(retry, again, again.steps[1],
                        query=article_query(again, {}, {('854232', 'HS2022'): '메모리'}))
                self.assertEqual(result.status, 'OK')
                self.assertEqual(result.article_ids, [123])

    def test_retry_without_request_has_actionable_fixed_guidance(self):
        plan = article_retry_plan(agent_state('다시 검색해줘'))
        self.assertEqual(plan.tasks[0].answer_status, 'NO_DATA')
        self.assertEqual(plan.tasks[0].steps, [])
        self.assertIn('기사 주제', plan.tasks[0].applied_defaults[0])

    def test_embedding_returns_at_cap_and_ignores_late_usage(self):
        release, finished = Event(), Event()
        def slow_response(**kwargs):
            try:
                release.wait(1)
                return SimpleNamespace(usage=SimpleNamespace(total_tokens=100),
                    data=[SimpleNamespace(index=0, embedding=[1.0])])
            finally:
                finished.set()
        api = Mock()
        api.with_options.return_value.embeddings.create.side_effect = slow_response
        context = ExecutionContext(time.monotonic()+2)
        try:
            from backend.app.services.rag.runtime import query_limits
            query_limits.cache_clear()
            self.addCleanup(query_limits.cache_clear)
            with patch('backend.app.api.config.Settings', return_value=Settings({'API_RAG_QUERY_EMBED_SECONDS': '0.1'})):
                started = time.monotonic()
                with self.assertRaisesRegex(RagDeadlineExceeded, 'query_embedding_timeout'):
                    ModelGateway(api).embed(['query'], context)
                self.assertLess(time.monotonic()-started, 0.8)
            self.assertEqual(context.tokens, 0)
        finally:
            release.set()
            self.assertTrue(finished.wait(1))

    def test_retry_recomputes_default_window_but_keeps_requested_window(self):
        from datetime import date, datetime, timezone
        from unittest.mock import Mock
        from backend.app.agents.conditions import recent_article_period
        from backend.app.agents.state import ArticleRetry, Conditions
        old = {'start': date(2026, 7, 2), 'end': date(2026, 10, 1)}
        for default_period in (True, False):
            with self.subTest(default_period=default_period):
                request = ArticleRetry(question='반도체 기사', default_period=default_period,
                                       conditions=Conditions(article_period=old))
                retry = agent_state('다시검색', {'article_retry': request.model_dump(mode='json')})
                retry.input.received_at = datetime(2026, 11, 11, 3, tzinfo=timezone.utc)
                retry.plan = article_retry_plan(retry)
                again = retry.plan.tasks[0]
                with patch('backend.app.api.accounts.validate_scope'):
                    check_conditions(Mock(), retry, again.steps[0])
                expected = recent_article_period(retry.input.received_at) if default_period else old
                self.assertEqual(again.conditions.article_period.model_dump(), dict(expected))

    def test_full_query_slots_wait_within_the_same_deadline(self):
        from threading import Event as Gate
        from backend.app.services.rag import runtime as rag_runtime
        gate = Gate()
        original = rag_runtime._QUERY_POOL
        executor = rag_runtime.ThreadPoolExecutor(max_workers=1)
        rag_runtime._QUERY_POOL = (executor, rag_runtime.BoundedSemaphore(1))
        try:
            from threading import Thread
            first = Thread(target=lambda: rag_runtime.query_call(lambda: gate.wait(1) or 'first', 2))
            first.start()
            time.sleep(0.05)
            started = time.monotonic()
            calls = []
            with self.assertRaisesRegex(RagDeadlineExceeded, 'query_embedding_timeout'):
                rag_runtime.query_call(lambda: calls.append('second'), 0.1)   # 자리를 기다리다 상한을 넘김
            self.assertEqual(calls, [])   # 시간을 다 쓴 요청은 외부 호출을 보내지 않는다
            self.assertGreaterEqual(time.monotonic() - started, 0.09)
            gate.set()
            self.assertEqual(rag_runtime.query_call(lambda: 'third', 1), 'third')   # 자리가 나면 실행
            first.join(1)
        finally:
            gate.set()
            rag_runtime._QUERY_POOL = original
            executor.shutdown(wait=False)


if __name__ == '__main__':
    unittest.main()
