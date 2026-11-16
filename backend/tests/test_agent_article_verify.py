import unittest
from copy import deepcopy
from unittest.mock import Mock

from backend.app.agents.compose import validate_citations
from backend.app.agents.state import ArticleReview, Draft, ToolResult
from backend.app.agents.verify import verify
from backend.app.agents.verify_articles import review_article_claims
from backend.tests import test_agent_compose


class ArticleVerificationTest(unittest.TestCase):
    def setUp(self):
        self.state = test_agent_compose.ComposeTest().state(['CAUSE', 'CAUSE'])
        self.step = next(s for s in self.state.plan.tasks[0].steps if s.tool == 'query_trade_data')
        self.state.results[self.step.step_id] = ToolResult(status='OK', result_id='r',
            digest={'rows': [{'amount_usd': 48, 'country_name': '중국'}]})
        self.state.fixed_conclusion_ids = []
        self.fact = '2024년 중국 시장의 화장품 매출은 전년보다 3% 줄어들었다.'
        self.cards = [self.card('t1', self.fact), self.card('t2', '2024년 일본 화장품 수입액은 48달러였다.')]

    def card(self, task, fact):
        return {'task_id': task, 'article_id': task[-1], 'evidence_sufficient': True,
                'evidence_role': 'context_reference', 'sentences': [{
                    'text': '참고(직접 답변 아님): ' + fact, 'quotes': [fact],
                    'unverified_question_parts': ['2025년 수출 변화의 원인']}]}

    def check(self, line, *, cards=None, explanation=False):
        self.state.draft = Draft(conclusion=[
            {'task_id': 't1', 'sentences': ['분석 결과입니다.' if explanation else line]},
            {'task_id': 't2', 'sentences': ['분석 결과입니다.']}],
            explanation=[line] if explanation else [], turn_summary='')
        verify(self.state, articles=self.cards if cards is None else cards)
        return next(c for c in self.state.verification.checks if c.index == (2 if explanation else 0))

    def test_exact_reviewed_fact_with_attribution_and_register(self):
        line = '기사에 따르면 ' + self.fact.replace('줄어들었다.', '줄었습니다. [1]')
        check = self.check(line)
        self.assertEqual((check.status, check.basis, check.reason), ('PASS', 'ARTICLE', 'MATCH'))
        self.assertFalse(self.state.verification.feedback)

    def test_article_scope_wins_even_when_sql_has_the_requested_amount(self):
        for line in ['한국의 대중국 수출액은 48달러입니다.[1]', self.fact + ' [2]',
                     self.fact + ' [1, 2]', self.fact.replace('3%', '30%') + ' [1]']:
            with self.subTest(line=line):
                self.assertEqual(self.check(line).status, 'MISMATCH')
                self.assertNotIn('ARTICLE_PARAPHRASE', [f.reason for f in self.state.verification.feedback])

    def test_writer_must_cite_an_attributed_trend_even_when_it_adds_a_limitation(self):
        line = '기사의 배경 설명은 화장품 수출 하락에 관한 것이며, 이번 통계로는 원인을 확인할 수 없습니다.'
        self.check(line)
        with self.assertRaisesRegex(ValueError, '기사 번호'):
            validate_citations(self.state.draft, [{'task_id': 't1', 'n': 1}])
        self.check(line + '[1]')
        validate_citations(self.state.draft, [{'task_id': 't1', 'n': 1}])

    def test_missing_or_changed_body_cannot_certify_a_digest(self):
        for patch in [{'unavailable': True}, {'evidence_changed': True}, {'sentences': []},
                      {'sentences': [{'text': self.fact, 'quotes': [None]}]}]:
            with self.subTest(patch=patch):
                check = self.check(self.fact + '[1]', cards=[{**self.cards[0], **patch}])
                self.assertEqual((check.status, check.reason), ('MISMATCH', 'SOURCE_UNAVAILABLE'))

    def test_two_articles_and_trailing_analysis_checked_independently(self):
        line = self.fact + '[1] ' + self.cards[1]['sentences'][0]['text'] + '[2]'
        self.assertEqual(self.check(line, explanation=True).status, 'PASS')
        self.assertEqual(self.check(line + ' 한국의 수출액은 999달러입니다.', explanation=True).status, 'MISMATCH')
        self.assertEqual(self.check('[1] ' + self.fact).status, 'PASS')

    def test_paraphrase_requires_scoped_review_bound_to_unchanged_content(self):
        line = '기사는 2024년 중국의 화장품 매출이 전년 대비 3% 감소했다고 설명합니다.[1]'
        self.assertEqual(self.check(line).status, 'MISMATCH')
        original = self.state.draft.model_dump()
        services = Mock()
        def adjudicate(state, node, prompt, payload, schema, tokens, validate, **kwargs):
            self.assertEqual(schema, ArticleReview)
            self.assertNotIn('quotes', str(payload))
            return validate(schema(claims=[{'index': 0, 'supported': True}]))
        services.structured.side_effect = adjudicate
        review_article_claims(services, self.state, self.cards, 'compose')
        self.assertEqual(self.check(line).status, 'PASS')
        self.assertEqual(self.state.draft.model_dump(), original)
        for altered in ['미국', '일본']:
            self.assertEqual(self.check(line.replace('중국', altered)).status, 'MISMATCH')
        updated = deepcopy(self.cards)
        updated[0]['sentences'][0]['quotes'] = ['수정된 근거']
        self.assertEqual(self.check(line, cards=updated).status, 'MISMATCH')

    def test_unsupported_inference_or_failed_model_keeps_warning(self):
        line = self.fact.replace('줄어들었다', '늘었다') + ' [1]'
        self.check(line)
        services = Mock()
        services.structured.return_value = []
        review_article_claims(services, self.state, self.cards, 'compose')
        self.assertEqual(self.check(line).status, 'MISMATCH')
        services.structured.return_value = None
        review_article_claims(services, self.state, self.cards, 'compose')
        self.assertEqual(self.check(line).status, 'MISMATCH')
