"""명시적으로 실행할 때만 관리자 생성. 사용자/기준 데이터 자동 시드 없음."""
from getpass import getpass
from sqlalchemy import select
from backend.app.api.config import Settings
from backend.app.api.database import Database, Store
from backend.app.api.security import password_hash
from backend.app.api.common import now


def main():
    password=getpass('Admin password: ')
    confirmation=getpass('Confirm password: ')
    hashed=password_hash(password,confirmation)
    database=Database(Settings().engine())
    with database.engine.begin() as connection:
        store=Store(database,connection); users=store.t('users')
        for name in ('users','notification_setting'): store.t(name)
        if connection.scalar(select(users.c.user_id).where(users.c.email=='admin@example.com')):
            raise SystemExit('Existing admin@example.com was not changed.')
        uid=store.insert('users',email='admin@example.com',password_hash=hashed,landing_page='GLOBAL',
            name='관리자',role='ADMIN',account_status='ACTIVE',created_at=now())
        store.insert('notification_setting',user_id=uid,notifications_enabled=False,report_popup=True,major_issue_popup=True,spike_popup=True)
    print('Admin created; credentials were not logged.')


if __name__=='__main__': main()
