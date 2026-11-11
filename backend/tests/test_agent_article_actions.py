import unittest
from contextlib import nullcontext
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock, patch

from langgraph.checkpoint.memory import InMemorySaver

from backend.app.agents import article_actions
from backend.app.agents.compose import compose_input
from backend.app.agents.graph import build_graph, graph_config
from backend.app.agents.runtime import Services as Runtime
from backend.app.agents.state import AgentState, Draft, ToolResult
from backend.app.schemas.rag import ArticleEvidence
from backend.tests.test_agent_graph import Services, initial


class ArticleServices(Services):
    planner = Runtime.planner
    check = Runtime.check
    settings = {}

    def read_store(self):
        return nullcontext(None)

    def tool(self, state, step):
        task = next(task for task in state.plan.tasks if step in task.steps)
        return article_actions.run_action(self, state, task, step, lambda _: 'snapshot', 20)

    def compose(self, state):
        articles = [{**item, 'n': index, 'task_id': task.task_id}
                    for index, task in enumerate(state.plan.tasks, 1)
                    for step in task.steps
                    for item in (state.results.get(step.step_id, ToolResult(status='OK')).digest or {}).get('items', [])]
        self.payload, fixed = compose_input(state, lambda _: {}, articles)
        state.draft = Draft(conclusion=[fixed.get(task.task_id) or
            {'task_id': task.task_id, 'sentences': ['선택 기사의 핵심입니다. [1]']}
            for task in state.plan.tasks], turn_summary='')

    def compose_text(self, state):
        self.events.append('compose_text')
        self.compose(state)


class ArticleActionTest(unittest.TestCase):
    def test_compose_fallback_displays_selected_summary_when_related_search_times_out(self):
        data = initial()
        data['input']['article_action'] = {'article_id': '11', 'mode': 'both'}
        state = AgentState.model_validate(data)
        state.plan = article_actions.action_plan(state.input.article_action)
        summary, related = state.plan.tasks
        for task in state.plan.tasks:
            for step in task.steps:
                step.status = 'DONE'
        summary.answer_status = 'ANSWERED'
        related.steps[-1].status = 'FAILED'
        related.answer_status = 'NO_DATA'
        state.results['t1.news'] = ToolResult(status='OK', article_ids=[11], digest={'kind': 'ARTICLES', 'items': [{
            'article_id': 11, 'title': '선택 기사', 'published_date': '2026-07-28',
            'evidence_sufficient': True, 'summary': ['메모리 반도체 수요가 증가했습니다.', '공급 부족이 이어졌습니다.'],
        }]})
        state.results['t2.news'] = ToolResult(status='NO_DATA', error='기사 검색 시간이 초과되었습니다.')
        runtime = Mock()
        runtime.read_store.return_value = nullcontext(None)
        runtime.structured.return_value = None
        with patch('backend.app.agents.templates._names', return_value=({}, {})):
            Runtime.compose(runtime, state)
        self.assertEqual(state.draft.conclusion[0].sentences,
                         ['메모리 반도체 수요가 증가했습니다. [1]', '공급 부족이 이어졌습니다. [1]'])
        self.assertEqual(summary.answer_status, 'ANSWERED')
        self.assertEqual(state.draft.conclusion[1].sentences, ['기사 검색 시간이 초과되었습니다.'])

    def test_modes_route_selected_article_to_summary_and_exclude_it_from_related_search(self):
        row = {'article_id': 11, 'title': '선택 기사', 'body_masked': '기사의 실제 본문입니다.',
               'published_date': date(2024, 1, 5), 'source_code': 'HANKYUNG',
               'external_id': '11', 'source_url': 'https://example.com/11'}
        summary = ArticleEvidence(**{k: v for k, v in row.items() if k != 'body_masked'},
            countries=(), body_status='available', evidence=[{'evidence_id': 'e', 'chunk_id': 'c', 'quote': row['body_masked']}],
            summary=[{'text': '선택 기사의 요약입니다.', 'evidence_ids': ['e']}])

        def related(state, task, step, *, article_allowed, query, **kwargs):
            self.assertFalse(article_allowed(SimpleNamespace(article_id=11)))
            self.assertTrue(article_allowed(SimpleNamespace(article_id=12)))
            self.assertFalse(article_allowed(SimpleNamespace(article_id=13)))
            self.assertIn(row['body_masked'], query)
            if not search_matches:
                return ToolResult(status='NO_DATA', error='관련 기사를 찾지 못했습니다.')
            return ToolResult(status='OK', digest={'kind': 'ARTICLES', 'items': [{
                'article_id': 12, 'title': '다른 기사', 'published_date': '2026-11-01',
                'evidence_sufficient': True, 'summary': ['관련 기사 요약입니다.'],
            }]})

        cases = [('summary', 1, 0, True), ('related', 0, 1, True),
                 ('both', 1, 1, True), ('both', 1, 1, False)]
        for mode, summary_calls, search_calls, search_matches in cases:
            with (
                self.subTest(mode=mode, search_matches=search_matches),
                patch.object(article_actions, 'selected_row', return_value=row) as lookup,
                patch.object(article_actions, 'summarize', return_value=summary) as summarize,
                patch.object(article_actions, 'eligible_filter', return_value=(None, lambda a: a.article_id == 12)),
                patch.object(article_actions.tools, 'article_result', side_effect=related) as search,
            ):
                services = ArticleServices()
                data = initial()
                data['input']['article_action'] = {'article_id': '11', 'mode': mode}
                result = AgentState.model_validate(build_graph(services, InMemorySaver()).invoke(data, graph_config('j')))
                self.assertEqual(result.status, 'SUCCEEDED')
                self.assertEqual(summarize.call_count, summary_calls)
                self.assertEqual(search.call_count, search_calls)
                lookup.assert_called_with(None, 1, '11')
                self.assertEqual('compose_text' in services.events, bool(summary_calls))
                self.assertEqual([a['title'] for a in services.payload['articles']], ['선택 기사'] if summary_calls else [])
                self.assertNotIn(row['body_masked'], str(result.model_dump()))
                if summary_calls:
                    self.assertEqual(summarize.call_args.args[0]['body_masked'], row['body_masked'])
                    self.assertEqual(result.plan.tasks[0].conditions.article_period.start, row['published_date'])
                    self.assertEqual(result.plan.tasks[0].answer_status, 'ANSWERED')
                if not search_matches:
                    self.assertEqual(result.plan.tasks[-1].answer_status, 'NO_DATA')

    def test_unavailable_article_skips_both_operations(self):
        data = initial()
        data['input']['article_action'] = {'article_id': '11', 'mode': 'both'}
        services = ArticleServices()
        with (patch.object(article_actions, 'selected_row', return_value=None),
              patch.object(article_actions, 'summarize') as summarize,
              patch.object(article_actions.tools, 'article_result') as search):
            result = build_graph(services, InMemorySaver()).invoke(data, graph_config('j'))
        summarize.assert_not_called()
        search.assert_not_called()
        self.assertEqual([t['answer_status'] for t in result['plan']['tasks']], ['NO_DATA', 'NO_DATA'])


class ArticleActionTimeoutTest(unittest.TestCase):
    def test_related_timeout_points_back_to_article_screen_not_chat_retry(self):
        row = {'article_id': 11, 'title': '선택 기사', 'body_masked': '본문', 'published_date': date(2026, 7, 28)}
        state = AgentState.model_validate({**initial(), 'input': {**initial()['input'],
                                           'article_action': {'article_id': '11', 'mode': 'related'}}})
        state.plan = article_actions.action_plan(state.input.article_action)
        task = state.plan.tasks[0]
        from backend.app.agents.state import Conditions
        task.conditions = Conditions(
            article_period={'start': '2026-08-01', 'end': '2026-11-01'})
        from backend.app.services.rag.runtime import RagDeadlineExceeded
        services = SimpleNamespace(read_store=lambda: nullcontext(None))
        with (patch.object(article_actions, 'selected_row', return_value=row),
              patch.object(article_actions, 'eligible_filter', return_value=(None, lambda a: True)),
              patch.object(article_actions.tools, 'search_news', side_effect=RagDeadlineExceeded('query_embedding_timeout'))):
            result = article_actions.run_action(services, state, task, task.steps[-1], None, 20)
        self.assertNotIn('다시검색', result.error)
        self.assertIn('기사 화면에서 유사 기사 검색을 다시 요청', result.error)


class ArticleSummaryCitationTest(unittest.TestCase):
    def test_selected_summary_accepts_period_after_citation_but_not_other_articles(self):
        from backend.app.agents.compose import assemble
        data = initial()
        data['input']['article_action'] = {'article_id': '11', 'mode': 'summary'}
        state = AgentState.model_validate(data)
        state.plan = article_actions.action_plan(state.input.article_action)
        state.plan.tasks[0].steps[-1].status = 'DONE'
        state.results['t1.news'] = ToolResult(status='OK', article_ids=[11], digest={'kind': 'ARTICLES', 'items': [{
            'article_id': 11, 'title': '선택 기사', 'published_date': '2026-07-28',
            'evidence_sufficient': True, 'summary': ['핵심 내용입니다.']}]})
        for sentences, ok in [(['핵심 내용입니다 [1].'], True), (['핵심 내용입니다. [1]'], True),
                              (['다른 기사 내용입니다 [2].'], False), (['출처가 없습니다.'], False)]:
            with self.subTest(sentences=sentences):
                draft = Draft(conclusion=[{'task_id': 't1', 'sentences': sentences}], turn_summary='')
                if ok:
                    assemble(state, draft, {}, lambda _: {})
                else:
                    with self.assertRaises(ValueError):
                        assemble(state, draft, {}, lambda _: {})
