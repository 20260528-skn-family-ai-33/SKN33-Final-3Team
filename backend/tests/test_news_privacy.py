import unittest
from backend.app.services.news_privacy import mask_article_contacts as mask, remove_legacy_markers, _delete_spans

class NewsPrivacyTest(unittest.TestCase):
    def test_external_contacts_are_preserved(self):
        s='담당자: Example Person\n이메일: contact @example.com\nTel: +94 11 1234567\n핫라인 156\n협회 Example Contact Two (전화번호:09 123456789)'
        self.assertEqual(mask(s),s)

    def test_staff_and_authors(self):
        s='작성자: KOTRA 무역관 홍길동, John Smith\n지원사업 문의: KOTRA 무역관 김철수 과장(test@kotra.or.kr), +82 2 1234 5678'
        result=mask(s)
        for token in ('홍길동','John Smith','김철수','test@','1234 5678'):self.assertNotIn(token,result)
        self.assertEqual(mask(result),result)

    def test_preserve_prose_and_heading(self):
        s='홍길동 변호사\n서론\n최근 문의가 증가하고 있다.\n핫라인 156'
        result=mask(s,title='[기고] 법률')
        self.assertEqual(result,'서론\n최근 문의가 증가하고 있다.\n핫라인 156')

    def test_malformed_contributor_email(self):
        s='홍길동 법인장(user@@example.com)\n기업 대표번호 +94 11 1234567'
        self.assertEqual(mask(s,title='[기고] 인증'),'기업 대표번호 +94 11 1234567')

    def test_staff_dates_preserved(self):
        s='KOTRA 무역관 문의: 2024-2027 사업이며 a@kotra.or.kr 또는 +82 2 1234 5678'
        result=mask(s)
        self.assertIn('2024-2027',result)
        self.assertNotIn('a@',result)

    def test_numeric_facts_and_external_interview(self):
        s='KOTRA 무역관에 문의: 법령 2.1.0198-20, HS 30049010, https://example.com/1127508725, (2022.7.15), 2106.90-9099'
        self.assertEqual(mask(s),s)
        s='KOTRA 무역관과 인터뷰를 진행한 외부 행사 담당자에게 문의(+62-21-1234-5678 / event@example.com)'
        self.assertEqual(mask(s),s)

    def test_external_contact_near_disclaimer(self):
        s='- 이메일: Invest@example.hr\n- 홈페이지: example.hr\n※ 외부 전문가 기고로 KOTRA의 공식 의견이 아님'
        self.assertEqual(mask(s),s)

    def test_foreign_staff(self):
        result=mask('문의처: KOTRA 무역관 Example(example@kotra.or.kr)')
        self.assertNotIn('Example',result)

    def test_remove_legacy_markers_and_empty_rows(self):
        s='본문 첫 문장\n작성자: [이름], 무역관[이름]\n문의: KOTRA 무역관 [이름] 과장([이메일])\n[작성자 정보]\n([전화번호])\n본문 마지막 문장'
        self.assertEqual(remove_legacy_markers(s), '본문 첫 문장\n본문 마지막 문장')

    def test_bilingual_author_row_is_removed(self):
        s='본문\n작성자: 홍길동(Gildong Hong, 코트라 예시 무역관)\n자료: 공공 보고서'
        self.assertEqual(mask(s), '본문\n자료: 공공 보고서')

    def test_inline_contact_preserves_surrounding_text(self):
        s='KOTRA 무역관은 상담을 지원합니다. 김철수 과장(test@kotra.or.kr)에게 문의하세요.'
        result=mask(s)
        self.assertNotIn('[이름]',result)
        self.assertNotIn('[이메일]',result)
        self.assertNotIn('()',result)
        self.assertNotIn('에게',result)
        self.assertIn('KOTRA 무역관은 상담을 지원합니다.',result)
        self.assertIn('문의하세요.',result)

    def test_preserve_external_information_with_no_markers(self):
        s='회사 담당자: Example Person\n전화: +94 11 1234567\n이메일: event@example.com\n핫라인 156'
        self.assertEqual(mask(s),s)

    def test_direct_path_does_not_use_legacy_cleanup(self):
        from unittest.mock import patch
        with patch('backend.app.services.news_privacy.remove_legacy_markers', side_effect=AssertionError('legacy path used')):
            self.assertEqual(mask('본문\n작성자: 홍길동\n자료: 보고서'), '본문\n자료: 보고서')
            self.assertEqual(mask('KOTRA 무역관은 안내합니다. 김철수 과장(test@kotra.or.kr)에게 문의하세요.'),
                             'KOTRA 무역관은 안내합니다. 문의하세요.')

    def test_overlapping_original_spans(self):
        self.assertEqual(_delete_spans('abcdefghij', [(2, 6), (4, 8), (0, 1)]), 'bij')

    def test_email_digits_do_not_shift_name_offsets(self):
        s='KOTRA 무역관 안내. 김철수 과장(staff12345678@kotra.or.kr), 홍길동 대리(other@kotra.or.kr)에게 문의하세요.'
        result=mask(s)
        for token in ('김철수', '홍길동', 'staff12345678', 'other@', '[이름]', '[이메일]'):
            self.assertNotIn(token,result)
        self.assertIn('KOTRA 무역관 안내.',result)
        self.assertEqual(mask(result),result)
