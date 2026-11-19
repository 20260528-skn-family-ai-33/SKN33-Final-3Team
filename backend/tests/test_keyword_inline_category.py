"""Create category pairs and assign an unreviewed keyword without leaving review."""
import unittest
from backend.tests import test_recovery_keyword_admin as fixtures


class InlineCategoryTest(unittest.TestCase):
    setUp = fixtures.RecoveryKeywordAdminTest.setUp
    call = fixtures.RecoveryKeywordAdminTest.call
    seed_admin = fixtures.RecoveryKeywordAdminTest.seed_admin
    review = fixtures.RecoveryKeywordAdminTest.review

    def test_create_major_then_minor_and_assign_with_audit_and_duplicate_guard(self):
        self.seed_admin()
        body = {'major_name':'새 대분류','minor_name':'첫 중분류','reason':'새 분류 기준','confirmed':True}
        first = self.call('POST','admin/keyword-categories',body)
        self.assertEqual(first.status_code,200,first.text)
        saved = first.json()['category']
        self.assertEqual(saved['category_id'],first.json()['category_id'])
        self.assertEqual((saved['major_name'],saved['minor_name']),('새 대분류','첫 중분류'))
        self.assertTrue(saved['editable'])
        self.assertTrue(saved['version'])
        second = self.call('POST','admin/keyword-categories',{**body,'minor_name':'추가 중분류'})
        self.assertEqual(second.status_code,200,second.text)
        category = second.json()['category']
        duplicate = self.call('POST','admin/keyword-categories',{**body,'minor_name':'추가 중분류'})
        self.assertEqual(duplicate.status_code,409)
        self.assertEqual(duplicate.json()['code'],'CATEGORY_EXISTS')
        self.assertEqual(self.review('ASSIGN',category['category_id']).status_code,200)
        detail = self.client.get('/api/v1/admin/keyword-topics/detail',params={'keyword_text':'검수키워드'}).json()
        self.assertEqual(detail['keyword']['category_id'],category['category_id'])
        self.assertEqual(detail['keyword']['review_status'],'REVIEWED')
        audit = self.client.get('/api/v1/admin/audit-events',params={'target_type':'CATEGORY'}).json()['items']
        self.assertEqual(len(audit),2)

    def test_creation_requires_admin_names_and_confirmation(self):
        body = {'major_name':'새 대분류','minor_name':'첫 중분류','reason':'추가','confirmed':True}
        self.assertEqual(self.call('POST','admin/keyword-categories',body).status_code,403)
        self.seed_admin()
        self.assertEqual(self.call('POST','admin/keyword-categories',{**body,'major_name':'   '}).status_code,400)
        self.assertEqual(self.call('POST','admin/keyword-categories',{**body,'confirmed':False}).status_code,400)

    def test_major_can_be_saved_without_minor_then_minor_independently(self):
        self.seed_admin()
        body = {'major_name':'독립 대분류','reason':'검수 화면 처리 사유','confirmed':True}
        before = self.client.get('/api/v1/admin/keyword-categories').json()['items']
        response = self.call('POST','admin/keyword-categories',body)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['major_name'],'독립 대분류')
        listing = self.client.get('/api/v1/admin/keyword-categories').json()
        self.assertIn('독립 대분류',listing['major_names'])
        self.assertEqual(listing['items'],before)
        self.assertEqual(self.call('POST','admin/keyword-categories',body).status_code,409)
        detail = self.client.get('/api/v1/admin/keyword-topics/detail',params={'keyword_text':'검수키워드'}).json()
        self.assertEqual(detail['keyword']['review_status'],'UNREVIEWED')
        minor = self.call('POST','admin/keyword-categories',{**body,'minor_name':'독립 중분류'})
        self.assertEqual(minor.status_code,200,minor.text)
        self.assertEqual(self.review('ASSIGN',minor.json()['category_id']).status_code,200)
        audit = self.client.get('/api/v1/admin/audit-events',params={'target_type':'CATEGORY'}).json()['items']
        self.assertEqual(len(audit),2)
        self.assertTrue(all(item['reason']==body['reason'] for item in audit))

    def test_major_only_validation_and_existing_major_duplicate(self):
        self.seed_admin()
        body={'major_name':'새 대분류','reason':'검수 사유','confirmed':True}
        for patch in ({'major_name':' '},{'major_name':'가'*41},{'reason':''},{'confirmed':False}):
            self.assertEqual(self.call('POST','admin/keyword-categories',{**body,**patch}).status_code,400)
        major=self.client.get('/api/v1/admin/keyword-categories').json()['items'][0]['major_name']
        self.assertEqual(self.call('POST','admin/keyword-categories',{**body,'major_name':major}).status_code,409)
