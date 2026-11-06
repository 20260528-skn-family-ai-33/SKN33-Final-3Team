import unittest
from backend.app.services.shortnews_load import mask_shortnews

class ShortnewsMaskingTest(unittest.TestCase):
    def test_names_contacts_and_idempotence(self):
        source = '작성자: KOTRA 부쿠레슈티 무역관 홍길동\n문의 : KOTRA 워싱턴 D.C. 무역관 김철수 과장(a@example.com)\n전화번호: 09-12345678, 09-123456789 또는 09 123456789\n핫라인(1510), 직통 전화번호 156으로 문의'
        expected = '작성자: KOTRA 부쿠레슈티 무역관 [이름]\n문의 : KOTRA 워싱턴 D.C. 무역관 [이름] 과장([이메일])\n전화번호: 09-12345678, 09-123456789 또는 09 123456789\n핫라인(1510), 직통 전화번호 156으로 문의'
        self.assertEqual(mask_shortnews(source), expected)
        self.assertEqual(mask_shortnews(expected), expected)

    def test_preserves_news_facts(self):
        source = '김철수 과장은 회의에 참석했다.\nHS 3303 00 100 0, 8504 21 0000\n2024-2027년, 2026.11.06\n전화번호는 12월 16일까지 등록\n콜센터 문의 30~40%\n문의: 자세한 사항은 홈페이지 참조'
        self.assertEqual(mask_shortnews(source), source)

    def test_foreign_contact_names(self):
        source = '문의는 협회 Example Contact One (전화번호: 09-12345678) 또는 Example Contact Two (전화번호:09 123456789)'
        self.assertEqual(mask_shortnews(source), source)
