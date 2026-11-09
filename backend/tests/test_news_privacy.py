import unittest
from backend.app.services.news_privacy import mask_article_contacts as mask, remove_legacy_markers, _delete_spans

class NewsPrivacyTest(unittest.TestCase):
    def test_external_contacts_are_preserved(self):
        s='담당자: Example Person\n이메일: contact @example.com\nTel: +94 11 1234567\n핫라인 156\n협회 Example Contact Two (전화번호:09 123456789)'
        self.assertEqual(mask(s),s)

    def test_preserve_prose_and_heading(self):
        s='홍길동 변호사\n서론\n최근 문의가 증가하고 있다.\n핫라인 156'
        result=mask(s,title='[기고] 법률')
        self.assertEqual(result,'서론\n최근 문의가 증가하고 있다.\n핫라인 156')

    def test_malformed_contributor_email(self):
        s='홍길동 법인장(user@@example.com)\n기업 대표번호 +94 11 1234567'
        self.assertEqual(mask(s,title='[기고] 인증'),'기업 대표번호 +94 11 1234567')

    def test_numeric_facts_and_external_interview(self):
        s='KOTRA 무역관에 문의: 법령 2.1.0198-20, HS 30049010, https://example.com/1127508725, (2022.7.15), 2106.90-9099'
        self.assertEqual(mask(s),s)
        s='KOTRA 무역관과 인터뷰를 진행한 외부 행사 담당자에게 문의(+62-21-1234-5678 / event@example.com)'
        self.assertEqual(mask(s),s)

    def test_external_contact_near_disclaimer(self):
        s='- 이메일: Invest@example.hr\n- 홈페이지: example.hr\n※ 외부 전문가 기고로 KOTRA의 공식 의견이 아님'
        self.assertEqual(mask(s),s)

    def test_remove_legacy_markers_and_empty_rows(self):
        s='본문 첫 문장\n작성자: [이름], 무역관[이름]\n문의: KOTRA 무역관 [이름] 과장([이메일])\n[작성자 정보]\n([전화번호])\n본문 마지막 문장'
        self.assertEqual(remove_legacy_markers(s), '본문 첫 문장\n본문 마지막 문장')

    def test_bilingual_author_row_is_removed(self):
        s='본문\n작성자: 홍길동(Gildong Hong, 코트라 예시 무역관)\n자료: 공공 보고서'
        self.assertEqual(mask(s), '본문\n자료: 공공 보고서')

    def test_preserve_external_information_with_no_markers(self):
        s='회사 담당자: Example Person\n전화: +94 11 1234567\n이메일: event@example.com\n핫라인 156'
        self.assertEqual(mask(s),s)

    def test_overlapping_original_spans(self):
        self.assertEqual(_delete_spans('abcdefghij', [(2, 6), (4, 8), (0, 1)]), 'bij')

    def test_contributor_headings_and_summary_are_preserved(self):
        heading = '미국 반도체 시장 동향'
        summary = '현지 기업 대표 10명 중 8명이 수출 증가 예상'
        self.assertEqual(mask(heading + '\n본문입니다.', title='[기고] 시장'), heading + '\n본문입니다.')
        self.assertEqual(mask('홍길동 무역회사 대표\n' + summary, title='[기고] 시장'), summary)
        self.assertEqual(mask('홍길동\n법무법인 예시 베트남사무소 대표변호사\n연락처: +84 (024) 3724 5201~3\n이메일: writer@example.com\n' + summary, title='[기고] 시장'), summary)

    def test_contributor_signature_with_phone(self):
        self.assertEqual(mask('홍길동 변호사(+82 10 1234 5678)\n본문입니다.', title='[기고] 법률'), '본문입니다.')

    def test_contributor_heading_ending_in_role_is_not_signature(self):
        for heading in ('현지 기업을 지원하는 전문가', '수출 확대를 위한 전문가', '시장을 분석하는 연구원'):
            source = heading + '\n본문입니다.'
            self.assertEqual(mask(source, title='[기고] 시장'), source)

    def test_generic_contributor_heading_is_not_a_name(self):
        source = '현지 기업 대표\n본문입니다.'
        self.assertEqual(mask(source, title='[기고] 시장'), source)
        self.assertEqual(mask('이진 변호사\n본문입니다.', title='[기고] 법률', writer='이진'), '본문입니다.')

    def test_signature_after_editor_note(self):
        note = '[편집자 주] 외부 기고문입니다.'
        source = note + '\n홍길동 변호사\n이메일: writer@example.com\n본문입니다.'
        self.assertEqual(mask(source, title='[기고] 법률'), note + '\n본문입니다.')

    def test_signature_is_full_identity_grammar_not_role_keyword(self):
        for heading in ('중국 시장 진출 전문가', '베트남 수출 시장 전문가', '해외 기업 대표'):
            source = heading + '\n본문입니다.'
            self.assertEqual(mask(source, title='[기고] 시장'), source)
        for signature in ('홍길동 변호사 writer@example.com', '홍길동 법인장(writer@@example.com)',
                          '홍길동 ABC(예시연구원) 중국법인장(writer@example.com)'):
            self.assertEqual(mask(signature + '\n본문입니다.', title='[기고] 법률'), '본문입니다.')

    def test_explicit_author_preserves_following_sentence(self):
        for delimiter in ('. ', '。 ', '; '):
            source = '작성자: 홍길동' + delimiter + '신청 기간은 11월 1일부터입니다.'
            self.assertEqual(mask(source), '신청 기간은 11월 1일부터입니다.')
        self.assertEqual(mask('작성자: 홍길동(writer@example.com). 신청서를 제출하세요.'), '신청서를 제출하세요.')

    def test_block_ownership_variations_and_prose_boundaries(self):
        # 3 소속 × 3 담당자 표기 × 3 연락처 필드 × 2 빈 줄 = 54가지 배치
        for office in ('KOTRA 하노이 무역관', '코트라 예시 무역관', 'KOTRA'):
            for identity in ('홍길동 과장', '담당자: 홍길동', '김철수 대리'):
                for contact in ('전화번호: +84 24 1234 5678', '이메일: staff@example.com', 'Tel: 02-123-4567'):
                    for gap in ('\n', '\n\n'):
                        source = gap.join((office, identity, contact))
                        with self.subTest(office=office, identity=identity, contact=contact, gap=gap):
                            result = mask(source)
                            self.assertEqual(result, source)
                            self.assertEqual(mask(result), result)
                            external = '독일 전시 주최사\n' + contact
                            boundary = office + '\n홍길동 과장\n' + external
                            self.assertEqual(mask(boundary), boundary)
        source = 'KOTRA 무역관\n지원 사업은 종료되었습니다.\n전화번호: +84 24 1234 5678'
        self.assertEqual(mask(source), source)

    def test_mixed_owner_permutations_preserve_external_bytes(self):
        from itertools import permutations
        internal = '문의: KOTRA 무역관(staff@kotra.or.kr)'
        external1 = '독일 주최사(event@example.com, +49 30 1234 5678)'
        external2 = '외부 행사 홈페이지 https://example.com/event'
        for separator in (', ', '; ', ' 또는 ', ' / '):
            for parts in permutations((internal, external1, external2)):
                with self.subTest(separator=separator, parts=parts):
                    expected = separator.join(parts)
                    result = mask(separator.join(parts))
                    self.assertEqual(result, expected)
                    self.assertEqual(mask(result), result)

    def test_explicit_author_contact_block_and_ownership_are_independent(self):
        for title in ('', '[기고] 법률'):
            for heading in ('', 'KOTRA 무역관\n'):
                for gap in ('\n', '\n\n'):
                    with self.subTest(title=title, heading=heading, gap=gap):
                        source = heading + gap.join(('작성자: 홍길동', '이메일: writer@example.com', '전화번호: +84 24 1234 5678', '본문입니다.'))
                        result = mask(source, title=title)
                        self.assertNotIn('writer@', result)
                        self.assertNotIn('1234', result)
                        self.assertIn('본문입니다.', result)
                        self.assertEqual(mask(result, title=title), result)
        source = '작성자: 홍길동. 안내문입니다.\n이메일: event@example.com'
        self.assertEqual(mask(source), '안내문입니다.\n이메일: event@example.com')

    def test_author_contact_ends_at_general_prose(self):
        source = '작성자: 홍길동\n이메일: writer@example.com\n행사 안내\n이메일: event@example.com'
        self.assertEqual(mask(source), '행사 안내\n이메일: event@example.com')

    def test_inline_author_body_closes_contributor_signature_scan(self):
        body = '아래는 행사 연사 안내입니다.\n김철수 변호사\n이메일: event@example.com'
        for delimiter in ('. ', '; ', '。 '):
            source = '작성자: 홍길동' + delimiter + body
            result = mask(source, title='[기고] 행사')
            self.assertEqual(result, body)
            self.assertEqual(mask(result, title='[기고] 행사'), result)
        # 실제 서명과 작성자 전용 블록은 계속 삭제해야 합니다.
        self.assertEqual(mask('홍길동 변호사\n이메일: writer@example.com\n본문입니다.', title='[기고] 법률'), '본문입니다.')
        self.assertEqual(mask('작성자: 홍길동\n이메일: writer@example.com\n본문입니다.', title='[기고] 법률'), '본문입니다.')

    def test_review_office_refers_to_an_external_contact(self):
        for org in ('독일 상공회의소', '현지 전시 사무국'):
            source = f'KOTRA는 {org}(event@example.com, +49 30 1234 5678)에 문의할 것을 안내합니다.'
            self.assertEqual(mask(source), source)

    def test_author_email_inside_instructions(self):
        for instruction, expected in (
            ('이메일: writer@example.com으로 문의하세요.', ''),
            ('작성자에게 writer@example.com으로 문의하세요.', ''),
            ('작성자께 writer@kotra.or.kr로 연락 바랍니다. 행사 문의: event@example.com',
             '작성자께 연락 바랍니다. 행사 문의: event@example.com'),
        ):
            with self.subTest(instruction=instruction):
                body = '작성자: 홍길동\n' + instruction
                self.assertEqual(mask(body), expected)
                self.assertEqual(mask(expected), expected)
        self.assertEqual(mask('앞 문단\n작성자에게 writer@example.com으로 문의하세요.\n뒤 문단'), '앞 문단\n뒤 문단')
        external = '이메일: event@example.com으로 문의하세요.'
        self.assertEqual(mask(external), external)

    def test_author_email_after_affiliation_only(self):
        for affiliation in ('법무법인 예시', '소속: 법무법인 예시'):
            for field, tail in (('이메일: writer@example.com', ''),
                                ('작성자에게 writer@example.com으로 문의하세요.', '')):
                body = '작성자: 홍길동\n' + affiliation + '\n' + field + '\n본문입니다.'
                expected = affiliation + '\n' + tail + '본문입니다.'
                self.assertEqual(mask(body), expected)
                self.assertEqual(mask(expected), expected)
        for boundary in ('법무법인 예시 행사 안내', '독일 제조사 문의처'):
            body = boundary + '\n이메일: event@example.com으로 문의하세요.'
            self.assertEqual(mask('작성자: 홍길동\n' + body), body)

    def test_inquiry_contact_survives_author_removal(self):
        for contact in (
            '문의: KOTRA 무역관 김철수 과장(staff@example.com, 신청 마감 11월 30일)',
            '담당자: 김철수 대리 / staff@example.com\n전화번호: +82 2 1234 5678',
            '문의처: 협회 행사 담당자  김철수 대리',
        ):
            for separator in ('\n', '. ', ' / '):
                with self.subTest(contact=contact, separator=separator):
                    source = '작성자: 홍길동' + separator + contact
                    self.assertEqual(mask(source), contact)
                    self.assertEqual(mask(contact), contact)

    def test_company_affiliation_preserves_inquiry_boundary(self):
        inquiry = '문의 담당자: 김철수 대리\n이메일: staff@example.com\n전화번호: 02-123-4567'
        for company in ('소속: 예시테크', '주식회사 예시테크', '소속：Example Inc.'):
            body = '작성자: 홍길동\n' + company + '\n이메일: writer@example.com\n' + inquiry
            self.assertEqual(mask(body), company + '\n' + inquiry)
            self.assertEqual(mask('작성자: 홍길동\n' + company + '\n' + inquiry), company + '\n' + inquiry)

    def test_deadline_in_author_signature(self):
        date = '신청 마감 11월 30일'
        inquiry = '문의: 김철수 대리(staff@example.com, ' + date + ')'
        for fields in (date + ', writer@example.com', 'writer@example.com, ' + date):
            signature = '홍길동 과장(' + fields + ')'
            for label, title in (('작성자: ', ''), ('', '[기고] 신청 안내')):
                body = label + signature + '\n' + inquiry
                self.assertEqual(mask(body, title=title), date + '\n' + inquiry)
            self.assertEqual(mask('작성자: ' + signature + '. ' + inquiry), date + ' ' + inquiry)

    def test_parenthesized_extension_in_author_contact_only(self):
        inquiry = '문의 담당자: 김철수\n연락처 : +1-202-555-0142 (313)\nE-mail : staff@example.com'
        for phone in ('연락처 : +1-202-555-0142 (335)', 'Tel: 02-1234-5678(313)'):
            with self.subTest(phone=phone):
                source = '작성자: 홍길동\n' + phone + '\nE-mail : writer@example.com\n' + inquiry
                self.assertEqual(mask(source), inquiry)
                self.assertEqual(mask(inquiry), inquiry)
