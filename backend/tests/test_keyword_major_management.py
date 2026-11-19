"""미사용 대분류의 감사 이력 복원·확인·버전·사용 여부 검증."""
import unittest
from datetime import datetime
from sqlalchemy import select
from backend.tests import test_recovery_keyword_admin as fixtures
from backend.app.api.database import Store
from backend.app.api.keyword_admin import major_state


class MajorManagementTest(unittest.TestCase):
    setUp = fixtures.RecoveryKeywordAdminTest.setUp
    call = fixtures.RecoveryKeywordAdminTest.call
    seed_admin = fixtures.RecoveryKeywordAdminTest.seed_admin

    def create(self, name, minor=None):
        body={'major_name':name,'confirmed':True,'reason':'분류 생성'}
        if minor is not None: body['minor_name']=minor
        return self.call('POST','admin/keyword-categories',body)

    def listing(self):
        return self.client.get('/api/v1/admin/keyword-categories').json()

    def change(self, name, new=None, version=None, **extra):
        body={'major_name':name,'expected_version':version or self.listing()['major_version'],
              'confirmed':True,'reason':'오타 또는 미사용 정리',**extra}
        if new is not None: body['new_major_name']=new
        return self.call('PATCH' if new is not None else 'DELETE','admin/keyword-majors',body)

    def test_rename_delete_persist_and_stale_requests_cannot_resurrect(self):
        self.seed_admin()
        self.assertEqual(self.create('오타').status_code,200)
        listing=self.listing()
        self.assertIn('오타',listing['editable_major_names'])
        response=self.change('오타','수정',listing['major_version'])
        self.assertEqual(response.status_code,200,response.text)
        self.assertNotIn('오타',self.listing()['major_names'])
        self.assertIn('수정',self.listing()['major_names'])
        self.assertEqual(self.change('오타','또 수정',listing['major_version']).status_code,409)
        self.assertEqual(self.create('오타','늦은 중분류').status_code,409)
        self.assertEqual(self.change('수정').status_code,200)
        self.assertNotIn('수정',self.listing()['major_names'])
        self.assertEqual(self.create('수정','늦은 중분류').status_code,409)
        existing=self.create('다른 분야','중분류').json()['category']
        response=self.call('PATCH',f"admin/keyword-categories/{existing['category_id']}",{
            'major_name':'수정','minor_name':'옮기기','expected_version':existing['version'], 'confirmed':True,'reason':'이동'})
        self.assertEqual(response.status_code,409,response.text)
        with self.engine.connect() as c:
            a=self.meta.tables['api_audit_event']
            actions=list(c.execute(select(a.c.action).where(a.c.target_id.in_(['major:오타','major:수정']))).scalars())
        self.assertCountEqual(actions,['CATEGORY_CREATE','CATEGORY_RENAME','CATEGORY_DELETE'])

    def test_used_base_names_and_invalid_new_names_are_protected(self):
        self.seed_admin(); self.create('빈 대분류'); self.create('사용 중','중분류')
        base=self.listing()['base_majors'][0]
        self.assertEqual(self.create(base).status_code,409)
        for name,status in [(' ',400),('가'*41,400),('사용 중',409),(base,409),('빈 대분류',409)]:
            with self.subTest(name=name): self.assertEqual(self.change('빈 대분류',name).status_code,status)
        self.create('독립'); stale=self.listing()['major_version']; self.create('독립','새 중분류')
        self.assertEqual(self.change('독립',version=stale).status_code,409)
        self.assertEqual(self.change('독립').status_code,409)
        self.assertEqual(self.change('독립','바꿈').status_code,409)
        self.assertEqual(self.change('빈 대분류','바꿈',confirmed=False).status_code,400)
        self.assertEqual(self.change('빈 대분류',reason='').status_code,400)
        self.assertEqual(self.change('없는 대분류').status_code,409)

    def test_management_requires_admin(self):
        body={'major_name':'분류','new_major_name':'변경','expected_version':'stale','reason':'변경','confirmed':True}
        self.assertEqual(self.call('PATCH','admin/keyword-majors',body).status_code,403)
        body.pop('new_major_name')
        self.assertEqual(self.call('DELETE','admin/keyword-majors',body).status_code,403)

    def test_replay_is_ordered_and_ignores_conflicting_events(self):
        self.seed_admin()
        with self.engine.begin() as c:
            store=Store(self.app.state.database,c)
            events=[('CATEGORY_CREATE',None,'A'),('CATEGORY_CREATE',None,'B'),
                    ('CATEGORY_RENAME','없는 이름','C'),('CATEGORY_RENAME','A','B'),
                    ('CATEGORY_RENAME','A','C'),('CATEGORY_DELETE','A',None),
                    ('CATEGORY_DELETE','C',None)]
            for index,(action,old,new) in reversed(list(enumerate(events))):
                a=self.meta.tables['api_audit_event']
                c.execute(a.insert().values(audit_id=f'audit_{index:03}',actor_id=self.uid,action=action,
                    target_type='CATEGORY',target_id='major:'+(old or new),reason='복원 검증',
                    before_json={'major_name':old} if old else {},after_json={'major_name':new} if new else {},
                    created_at=datetime(2026,11,19)))
            state=major_state(store)
            self.assertEqual(state['saved'],{'B'})
            self.assertEqual(state['retired'],{'A','C'})
