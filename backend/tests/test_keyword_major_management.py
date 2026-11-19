"""미사용 대분류의 감사 이력 복원·확인·버전·사용 여부 검증."""
import unittest
from datetime import datetime
from sqlalchemy import select
from backend.tests import test_recovery_keyword_admin as fixtures
from backend.app.api.database import Store
from backend.app.api.keyword_admin import major_state, approximate_name_key as approximate


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
            self.assertEqual(set(state['saved'].values()),{'B'})
            self.assertEqual(state['retired'],{state['key']('A'),state['key']('C')})

    def test_names_follow_db_collation_for_case_accent_and_width(self):
        # keyword_category는 utf8mb4_0900_ai_ci다. 대소문자·악센트·전각만 다른 이름은 같은 대분류다.
        self.seed_admin()
        self.assertEqual(self.create('AI산업').status_code,200)
        for variant in ('ai산업','ＡＩ산업'):
            with self.subTest(variant=variant):
                response=self.create(variant)
                self.assertEqual(response.status_code,409,response.text)
                self.assertIn("'AI산업'",response.json()['message'])
                self.assertEqual(self.create(variant,'반도체').status_code,409)
        self.assertEqual(self.create('café').status_code,200)
        self.assertEqual(self.create('cafe').status_code,409)
        self.assertEqual(self.create('AI산업','반도체').status_code,200)
        listing=self.listing()
        self.assertEqual([n for n in listing['major_names'] if approximate('AI산업')==approximate(n)],['AI산업'])
        self.assertNotIn('AI산업',listing['editable_major_names'])

    def test_deleted_name_variants_cannot_resurrect_and_spelling_fix_is_allowed(self):
        self.seed_admin()
        self.create('B2B시장')
        self.assertEqual(self.change('B2B시장').status_code,200)
        for variant in ('b2b시장','B2B시장'):
            with self.subTest(variant=variant):
                response=self.create(variant,'유통')
                self.assertEqual(response.status_code,409,response.text)
                self.assertEqual(response.json()['code'],'CATEGORY_DELETED')
        self.create('ai산업')
        response=self.change('ai산업','AI산업')
        self.assertEqual(response.status_code,200,response.text)
        names=self.listing()['major_names']
        self.assertIn('AI산업',names); self.assertNotIn('ai산업',names)
        # 철자만 고친 것은 삭제가 아니므로 같은 키의 중분류 추가를 막지 않는다.
        self.assertEqual(self.create('AI산업','반도체').status_code,200)
        self.assertEqual(self.change('AI산업','AI산업').status_code,409)

    def test_replay_keeps_one_spelling_for_legacy_variant_events(self):
        self.seed_admin()
        with self.engine.begin() as c:
            a=self.meta.tables['api_audit_event']
            for index,name in enumerate(['AI산업','ai산업']):
                c.execute(a.insert().values(audit_id=f'audit_legacy_{index}',actor_id=self.uid,action='CATEGORY_CREATE',
                    target_type='CATEGORY',target_id='major:'+name,reason='이전 저장',before_json={},
                    after_json={'major_name':name},created_at=datetime(2026,11,19,index)))
        listing=self.listing()
        self.assertIn('AI산업',listing['major_names']); self.assertNotIn('ai산업',listing['major_names'])
        self.assertEqual(self.change('ai산업').status_code,200)
        self.assertNotIn('AI산업',self.listing()['major_names'])

    def test_duplicate_messages_match_what_the_admin_typed(self):
        # 같은 철자를 다시 입력한 경우 "같은 철자로 입력하세요"라고 안내하지 않는다.
        self.seed_admin(); self.create('AI산업'); self.create('다른'); self.create('사용','중')
        same='같은 이름의 대분류 또는 기본 분류가 이미 있습니다.'
        for response in (self.create('AI산업'),self.change('다른','AI산업'),self.create('사용'),
                         self.create(self.listing()['base_majors'][0])):
            with self.subTest(response=response.request.url):
                self.assertEqual(response.status_code,409,response.text)
                self.assertEqual(response.json()['message'],same)
        for response in (self.create('ai산업'),self.change('다른','ai산업')):
            message=response.json()['message']
            self.assertIn("'AI산업'",message); self.assertNotIn('같은 철자로 입력하세요',message)
        # 중분류 추가는 등록된 철자로 다시 입력하면 되므로 그렇게 안내한다.
        message=self.create('ai산업','반도체').json()['message']
        self.assertIn("'AI산업'",message); self.assertIn('같은 철자로 입력하세요',message)
