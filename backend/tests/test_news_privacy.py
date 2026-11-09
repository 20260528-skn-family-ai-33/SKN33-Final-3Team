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

    def test_split_office_contact_fields(self):
        for field in ('전화번호', '전화', '문의처', '연락처', 'Tel'):
            with self.subTest(field=field):
                self.assertEqual(mask('KOTRA 하노이 무역관\n' + field + ': +84-24-1234-5678'), 'KOTRA 하노이 무역관')

    def test_external_organizer_next_to_office_is_preserved(self):
        external = '문의: 독일 전시 주최사(event@example.com, +49 30 1234 5678)'
        for intro in ('KOTRA는 박람회 참가를 지원합니다.', 'KOTRA 하노이 무역관'):
            with self.subTest(intro=intro):
                self.assertEqual(mask(intro + '\n' + external), intro + '\n' + external)
        s = 'KOTRA 하노이 무역관\n전화번호: +84-24-1234-5678\n' + external + '\n전화번호: +49 30 1234 5678'
        self.assertEqual(mask(s), 'KOTRA 하노이 무역관\n' + external + '\n전화번호: +49 30 1234 5678')

    def test_hs_code_with_staff_contact_is_preserved(self):
        for code in ('HS 0301 99 0000', 'HS 코드: 0301 99 0000', 'HS CODE 0301 99 0000'):
            with self.subTest(code=code):
                result = mask('KOTRA 문의: staff@kotra.or.kr, ' + code)
                self.assertIn(code, result)
                self.assertNotIn('staff@', result)

    def test_contributor_headings_and_summary_are_preserved(self):
        heading = '미국 반도체 시장 동향'
        summary = '현지 기업 대표 10명 중 8명이 수출 증가 예상'
        self.assertEqual(mask(heading + '\n본문입니다.', title='[기고] 시장'), heading + '\n본문입니다.')
        self.assertEqual(mask('홍길동 무역회사 대표\n' + summary, title='[기고] 시장'), summary)
        self.assertEqual(mask('홍길동\n법무법인 예시 베트남사무소 대표변호사\n연락처: +84 (024) 3724 5201~3\n이메일: writer@example.com\n' + summary, title='[기고] 시장'), summary)

    def test_writer_particles_and_name_boundaries(self):
        for suffix in ('에게', '에게서', '께', '께서', '은', '는', '이', '가', '을', '를', '의', '와', '과', '도', '만', '으로', '으로부터', ''):
            with self.subTest(suffix=suffix):
                self.assertEqual(mask('KOTRA 홍길동' + suffix + ' 문의하세요.', writer='홍길동'), 'KOTRA 문의하세요.')
        for name in ('홍길동상', '홍길동대학교', '신홍길동'):
            s = 'KOTRA ' + name + ' 문의하세요.'
            self.assertEqual(mask(s, writer='홍길동'), s)

    def test_bare_staff_name_in_office_block(self):
        self.assertEqual(mask('KOTRA 하노이 무역관\n담당자: 홍길동\n전화번호: +84-24-1234-5678'), 'KOTRA 하노이 무역관')
        self.assertEqual(mask('담당자: 홍길동'), '담당자: 홍길동')

    def test_blank_lines_inside_contact_blocks(self):
        self.assertEqual(mask('KOTRA 하노이 무역관\n\n전화번호: +84-24-1234-5678'), 'KOTRA 하노이 무역관\n')
        self.assertEqual(mask('홍길동 변호사\n\n이메일: writer@example.com\n본문입니다.', title='[기고] 법률'), '\n본문입니다.')
        external = 'KOTRA 하노이 무역관\n\n독일 전시 주최사\n전화번호: +49 30 1234 5678'
        self.assertEqual(mask(external), external)

    def test_mixed_internal_and_external_contacts_on_one_line(self):
        internal = '문의: KOTRA 하노이 무역관(staff@kotra.or.kr)'
        external = '독일 전시 주최사(event@example.com, +49 30 1234 5678)'
        for source in (internal + ', ' + external, external + '; ' + internal):
            self.assertEqual(mask(source), external)

    def test_contributor_signature_with_phone(self):
        self.assertEqual(mask('홍길동 변호사(+82 10 1234 5678)\n본문입니다.', title='[기고] 법률'), '본문입니다.')

    def test_contact_row_preserves_arbitrary_instructions(self):
        for prose in ('신청서는 홈페이지에서 내려받으세요.', '서류를 준비해 주십시오.', '온라인 신청 권장'):
            result = mask('KOTRA 문의: a@kotra.or.kr, ' + prose)
            self.assertIn(prose, result)
            self.assertNotIn('a@kotra.or.kr', result)

    def test_hs_list_preserves_all_codes_and_deletes_actual_phone(self):
        codes = 'HS 0301 99 0000, 0302 99 0000'
        result = mask('KOTRA 문의: a@kotra.or.kr, ' + codes + ', 전화: 02-123-4567')
        self.assertIn(codes, result)
        self.assertNotIn('02-123-4567', result)
        self.assertNotIn('a@kotra.or.kr', result)

    def test_contributor_heading_ending_in_role_is_not_signature(self):
        for heading in ('현지 기업을 지원하는 전문가', '수출 확대를 위한 전문가', '시장을 분석하는 연구원'):
            source = heading + '\n본문입니다.'
            self.assertEqual(mask(source, title='[기고] 시장'), source)

    def test_mixed_contact_preserves_external_url_exactly(self):
        external = '외부 행사 홈페이지 https://example.com/event?q=1&x=2'
        self.assertEqual(mask('문의: KOTRA 무역관(staff@kotra.or.kr), ' + external), external)

    def test_mixed_contact_keeps_phone_with_its_owner(self):
        internal = '문의: KOTRA 무역관 staff@kotra.or.kr, +82 2 1234 5678'
        external = '독일 주최사 event@example.com, +49 30 1234 5678'
        for source in (internal + ', ' + external, external + '; ' + internal):
            self.assertEqual(mask(source), external)

    def test_mixed_contacts_with_word_separator(self):
        internal = '문의: KOTRA 무역관 김철수 과장(staff@kotra.or.kr)'
        external = '독일 주최사(event@example.com)'
        for separator in (' 또는 ', ' 혹은 ', ' 및 ', ' / '):
            self.assertEqual(mask(internal + separator + external), external)

    def test_generic_contributor_heading_is_not_a_name(self):
        source = '현지 기업 대표\n본문입니다.'
        self.assertEqual(mask(source, title='[기고] 시장'), source)
        self.assertEqual(mask('이진 변호사\n본문입니다.', title='[기고] 법률', writer='이진'), '본문입니다.')

    def test_signature_after_editor_note(self):
        note = '[편집자 주] 외부 기고문입니다.'
        source = note + '\n홍길동 변호사\n이메일: writer@example.com\n본문입니다.'
        self.assertEqual(mask(source, title='[기고] 법률'), note + '\n본문입니다.')

    def test_staff_role_with_particle(self):
        for suffix in ('에게', '께', '에게서', '은', '는'):
            self.assertEqual(mask('KOTRA 무역관 홍길동 과장' + suffix + ' 문의하세요.'), 'KOTRA 무역관 문의하세요.')
        source = 'KOTRA 무역관 홍길동 과장급 채용 문의'
        self.assertEqual(mask(source), source)

    def test_separate_name_row_retains_office_ownership(self):
        source = 'KOTRA 하노이 무역관\n홍길동 과장\n전화번호: +84 24 1234 5678'
        self.assertEqual(mask(source), 'KOTRA 하노이 무역관')

    def test_signature_is_full_identity_grammar_not_role_keyword(self):
        for heading in ('중국 시장 진출 전문가', '베트남 수출 시장 전문가', '해외 기업 대표'):
            source = heading + '\n본문입니다.'
            self.assertEqual(mask(source, title='[기고] 시장'), source)
        for signature in ('홍길동 변호사 writer@example.com', '홍길동 법인장(writer@@example.com)',
                          '홍길동 ABC(예시연구원) 중국법인장(writer@example.com)'):
            self.assertEqual(mask(signature + '\n본문입니다.', title='[기고] 법률'), '본문입니다.')

    def test_honorific_and_particle_are_one_identity_span(self):
        for suffix in ('님', '님께', '님에게', '님은', '씨', '씨에게', '씨는'):
            source = 'KOTRA 무역관 홍길동' + suffix + ' 문의하세요.'
            self.assertEqual(mask(source, writer='홍길동'), 'KOTRA 무역관 문의하세요.')
        self.assertEqual(mask('KOTRA 홍길동씨앗 문의하세요.', writer='홍길동'), 'KOTRA 홍길동씨앗 문의하세요.')

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
                            self.assertEqual(result.strip(), office)
                            self.assertEqual(mask(result), result)
                            external = '독일 전시 주최사\n' + contact
                            boundary = office + '\n홍길동 과장\n' + external
                            self.assertEqual(mask(boundary), office + '\n' + external)
        source = 'KOTRA 무역관\n지원 사업은 종료되었습니다.\n전화번호: +84 24 1234 5678'
        self.assertEqual(mask(source), source)

    def test_plan_records_reason_and_does_not_mutate_input(self):
        from backend.app.services.news_privacy import plan_article_contacts, Owner
        source = 'KOTRA 무역관\n홍길동 과장\n전화번호: +84 24 1234 5678\n외부 주최사 event@example.com'
        plans = plan_article_contacts(source)
        self.assertEqual(plans[1].owner, Owner.OFFICE)
        self.assertEqual(plans[1].reason, 'office-contact')
        self.assertTrue(plans[1].drop)
        self.assertTrue(plans[2].drop)
        self.assertEqual(plans[3].owner, Owner.EXTERNAL)
        self.assertFalse(plans[3].spans)
        self.assertIn('홍길동', source)

    def test_url_tokens_are_never_split_or_treated_as_phones(self):
        for url in ('https://example.com/02-123-4567',
                    'https://example.com/?x=1,KOTRA&next=/event',
                    'https://example.com/a;b?q=외부'):
            external = '독일 주최사 홈페이지 ' + url
            source = '문의: KOTRA 무역관(staff@kotra.or.kr), ' + external
            self.assertEqual(mask(source), external)
            source = 'KOTRA 문의: staff@kotra.or.kr, 신청 ' + url
            self.assertIn(url, mask(source))

    def test_mixed_owner_permutations_preserve_external_bytes(self):
        from itertools import permutations
        internal = '문의: KOTRA 무역관(staff@kotra.or.kr)'
        external1 = '독일 주최사(event@example.com, +49 30 1234 5678)'
        external2 = '외부 행사 홈페이지 https://example.com/event'
        for separator in (', ', '; ', ' 또는 ', ' / '):
            for parts in permutations((internal, external1, external2)):
                with self.subTest(separator=separator, parts=parts):
                    expected = separator.join(part for part in parts if part != internal)
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

    def test_author_removed_between_office_and_phone_is_idempotent(self):
        source = 'KOTRA 무역관\n작성자: 홍길동\n전화번호: +84 24 1234 5678'
        self.assertEqual(mask(source), 'KOTRA 무역관')
        self.assertEqual(mask(mask(source)), mask(source))

    def test_scam_example_protects_only_its_own_segment(self):
        example = '이메일 사칭 예시: fake@kotra.or.kr'
        staff = '문의: KOTRA 무역관 홍길동 과장(real@kotra.or.kr)'
        phone = '전화번호: +84 24 1234 5678'
        for source in (example + '\n' + staff + '\n' + phone,
                       staff + '\n' + phone + '\n' + example):
            self.assertEqual(mask(source), example)
        result = mask(example + '; ' + staff)
        self.assertIn('fake@kotra.or.kr', result)
        self.assertNotIn('real@', result)
        self.assertNotIn('홍길동', result)
        self.assertEqual(mask(result), result)

    def test_explicit_external_owner_overrides_inherited_office_or_author(self):
        for prefix in ('KOTRA 무역관', '작성자: 홍길동', 'KOTRA 무역관\n작성자: 홍길동'):
            for organization in ('독일 제조사', '현지 공급사', '독일 전시 주최사'):
                external = organization + ' 문의: vendor@example.com으로 신청서를 제출하세요.\n전화번호: +49 30 1234 5678'
                result = mask(prefix + '\n' + external)
                self.assertTrue(result.endswith(external))
                self.assertEqual(mask(result), result)
        source = '문의: KOTRA 무역관(staff@kotra.or.kr), 독일 제조사(vendor@example.com)'
        self.assertEqual(mask(source), '독일 제조사(vendor@example.com)')

    def test_author_contact_ends_at_general_prose(self):
        source = '작성자: 홍길동\n이메일: writer@example.com\n행사 안내\n이메일: event@example.com'
        self.assertEqual(mask(source), '행사 안내\n이메일: event@example.com')

    def test_scam_heading_protects_next_email_only(self):
        for heading in ('이메일 사칭 예시:', '사칭 이메일：'):
            for gap in ('\n', '\n\n'):
                example = heading + gap + 'fake@kotra.or.kr'
                source = example + '\n문의: KOTRA 무역관(real@kotra.or.kr)\n전화번호: +84 24 1234 5678'
                with self.subTest(heading=heading, gap=gap):
                    self.assertEqual(mask(source), example)
                    self.assertEqual(mask(mask(source)), example)
        source = '이메일 사칭 예시:\nfake@kotra.or.kr\nreal@kotra.or.kr'
        self.assertEqual(mask(source), '이메일 사칭 예시:\nfake@kotra.or.kr')

    def test_scam_heading_does_not_protect_prose_or_actual_contact(self):
        for following in ('문의: KOTRA 무역관(real@kotra.or.kr)',
                          '안내문입니다.\n문의: KOTRA 무역관(real@kotra.or.kr)'):
            result = mask('이메일 사칭 예시:\n' + following)
            self.assertNotIn('real@', result)
            if '안내문입니다.' in following:
                self.assertIn('안내문입니다.', result)
        source = '문의: KOTRA 무역관\nreal@kotra.or.kr'
        self.assertNotIn('real@', mask(source))

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
