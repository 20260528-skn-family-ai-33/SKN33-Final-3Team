import unittest
from copy import deepcopy
from unittest.mock import Mock

from backend.app.agents.compose import expand_article_selections, validate_citations
from backend.app.agents.state import ArticleReview, Draft, ToolResult
from backend.app.agents.verify import snapshot_evidence, verify
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
        verify(self.state, articles=self.cards if cards is None else cards, snapshots=getattr(self, 'snapshots', None))
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

    def test_mixed_and_uncited_source_claims_rejected_before_publication(self):
        lines = [
            '2025년 한국의 대중국 수출액 통계와 기사 내용은 각각 확인된 사실이며, 기사 내용만으로 수출 변화의 원인을 단정할 수 없습니다[1].',
            '월별 수출액은 11월 1억 122만 달러로 가장 적습니다. 기사에서는 시장 경쟁을 배경으로 들었습니다. [1]',
            '기사에서 언급한 감소 배경은 2021년 이후 흐름에 관한 것이며 2025년 수출 변화의 원인으로 확인된 것은 아닙니다.',
        ]
        for line in lines:
            with self.subTest(line=line):
                self.check(line, explanation=True)
                with self.assertRaises(ValueError):
                    validate_citations(self.state.draft, [{'n': 1, 'task_id': 't1'}])

    def test_selected_body_fact_binds_citation_without_changing_other_prose(self):
        cards = [{'n': 1, 'task_id': 't1', 'summary': [{'text': self.fact}]}]
        self.check('[기사 1 문장 1]')
        self.state.draft.explanation = ['이번 조회에서는 수출 변화의 원인을 확인할 수 없습니다.']
        draft = expand_article_selections(self.state.draft, cards)
        self.assertEqual(draft.conclusion[0].sentences, [self.fact + ' [1]'])
        self.assertEqual(self.check(draft.conclusion[0].sentences[0]).status, 'PASS')
        self.assertEqual(draft.explanation, ['이번 조회에서는 수출 변화의 원인을 확인할 수 없습니다.'])
        validate_citations(draft, cards)
        with self.assertRaises(ValueError):
            validate_citations(draft, [{**cards[0], 'task_id': 't2'}])
        for marker in ['[기사 1 문장 2]', '조회 금액은 48달러입니다. [기사 1 문장 1]']:
            self.check(marker)
            with self.assertRaises(ValueError):
                expand_article_selections(self.state.draft, cards)

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

    def test_confirmed_trade_decline_does_not_prove_its_cause(self):
        self.state.results[self.step.step_id].digest = {
            'kind': 'TREND', 'period': {'start': '2025-01-01', 'end': '2025-12-31'},
            'trade_flow': 'EXPORT', 'rows': [
                {'month': '2025-01', 'amount_usd': 120}, {'month': '2025-12', 'amount_usd': 80}]}
        trade = self.state.results[self.step.step_id].digest
        self.snapshots = {self.step.step_id: snapshot_evidence({'rows': trade['rows'],
            'conditions': {'period': trade['period'], 'trade_flow': 'EXPORT', 'country_codes': ['CN']},
            'meta': {'kind': 'TREND'}})}
        self.assertEqual(self.check('2025년 12월 수출액은 80달러입니다.').status, 'PASS')
        fact = self.fact + ' [1]'
        claim = '이는 2025년 한국의 대중국 화장품 수출 감소의 원인입니다.'
        limitation = '이 기사만으로는 2025년 한국 수출 변화의 원인을 확인할 수 없습니다.'
        for tail in (claim, '그 결과 한국의 수출이 줄었습니다.', '이것이 수출 감소를 초래했습니다.',
                     '이것이 수출 감소의 원인이라고 볼 수 있습니다.'):
            with self.subTest(tail=tail):
                self.assertEqual(self.check(fact + ' ' + tail).status, 'MISMATCH')
                self.assertEqual([f.passage for f in self.state.verification.feedback], [tail])
        self.assertEqual(self.check(fact + ' ' + limitation).status, 'PASS')
        self.check(fact + ' ' + claim)
        verify(self.state, snapshots=self.snapshots)
        self.assertEqual(self.state.verification.checks[0].status, 'MISMATCH')
        self.assertEqual(self.check(fact + ' ' + limitation + ' ' + claim).status, 'MISMATCH')
        # Conclusion, shared explanation and private turn summary are distinct draft fields.
        self.state.draft = Draft(conclusion=[{'task_id':'t1', 'sentences':[fact]},
                {'task_id':'t2', 'sentences':['자료를 조회했습니다.']}],
            explanation=[claim], turn_summary=claim)
        verify(self.state, articles=self.cards)
        checks = self.state.verification.checks
        self.assertEqual((checks[0].status, checks[-1].status), ('PASS', 'MISMATCH'))
        self.assertEqual(self.state.verification.summary_status, 'MISMATCH')
        self.assertEqual(self.state.draft.conclusion[0].sentences, [fact])
        self.assertEqual(self.state.draft.turn_summary, '')

    def test_causal_review_requires_the_relation_not_just_a_matching_date(self):
        cause = '중국의 화장품 소비 위축은 2025년 한국의 대중국 화장품 수출 감소의 원인입니다.'
        direct = self.card('t1', cause)
        direct.update(evidence_role='direct_evidence')
        direct['sentences'][0]['unverified_question_parts'] = []
        self.assertEqual(self.check(cause + ' [1]', cards=[direct]).status, 'PASS')
        self.assertEqual(self.check(cause + ' [1]').status, 'MISMATCH')
        # 2025 only occurs in the card's list of questions it cannot answer.
        self.assertNotIn('ARTICLE_PARAPHRASE', [f.reason for f in self.state.verification.feedback])

    def test_referential_claim_review_is_bound_to_its_antecedent(self):
        cards = [self.card('t1', '중국 시장의 수요 감소가 한국 수출 감소의 원인입니다.')]
        claim = '이는 한국 수출 감소의 원인입니다.'
        previous = '중국 시장의 수요가 감소했습니다. [1]'
        cards[0]['sentences'].append({'text': previous.split(' [')[0], 'quotes': [previous.split(' [')[0]]})
        line = previous + ' ' + claim
        self.assertEqual(self.check(line, cards=cards).status, 'MISMATCH')
        def adjudicate(state, node, prompt, payload, schema, tokens, validate, **kwargs):
            req, = payload['claims']
            self.assertEqual((req['antecedent'], req['claim']), (previous, claim))
            self.assertEqual(set(req['sources']), {'1'})
            return validate(schema(claims=[{'index': 0, 'supported': True}]))
        services = Mock()
        services.structured.side_effect = adjudicate
        review_article_claims(services, self.state, cards, 'compose')
        self.assertEqual(self.check(line, cards=cards).status, 'PASS')
        self.assertEqual(self.check(line.replace('수요가', '수입이'), cards=cards).status, 'MISMATCH')

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
