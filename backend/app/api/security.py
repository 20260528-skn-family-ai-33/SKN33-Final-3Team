import hashlib
import hmac
import re
import secrets
from datetime import timedelta
import bcrypt
from sqlalchemy import select, delete
from backend.app.api.common import ApiError, now, require, unavailable


def hash_token(value):
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(value, confirmation):
    require(value == confirmation, '비밀번호 확인값이 다릅니다.', 'PASSWORD_POLICY_VIOLATION')
    require(8 <= len(value) and len(value.encode()) <= 72 and not re.search(r'\s', value)
            and re.search(r'[A-Za-z]', value) and re.search(r'\d', value)
            and re.search(r'[^A-Za-z0-9\s]', value),
            '비밀번호는 영문·숫자·특수문자를 포함한 8자 이상, UTF-8 72바이트 이하입니다.', 'PASSWORD_POLICY_VIOLATION')
    return bcrypt.hashpw(value.encode(), bcrypt.gensalt()).decode()


def password_matches(value, hashed):
    try:
        return bool(hashed) and bcrypt.checkpw(value.encode(), hashed.encode())
    except (ValueError, TypeError):
        return False


def authenticate(store, request, required=True):
    token = request.cookies.get('bs_session')
    if not token:
        if required:
            raise ApiError(401, 'UNAUTHENTICATED', '로그인이 필요합니다.')
        return None
    s, u, p = store.t('api_auth_session'), store.t('users'), store.t('api_user_profile')
    row = store.one(select(u, p.c.name, p.c.role, p.c.account_status, p.c.password_change_advised,
                           s.c.csrf_hash, s.c.token_hash).select_from(s.join(u, s.c.user_id == u.c.user_id).join(p, p.c.user_id == u.c.user_id))
                    .where(s.c.token_hash == hash_token(token), s.c.expires_at > now()))
    if not row or row['account_status'] != 'ACTIVE':
        raise ApiError(401, 'UNAUTHENTICATED', '세션이 만료되었거나 이용할 수 없는 계정입니다.')
    if request.method not in ('GET','HEAD','OPTIONS'):
        provided = request.headers.get('X-CSRF-Token', '')
        require(hmac.compare_digest(row['csrf_hash'], hash_token(provided)), 'CSRF 검증에 실패했습니다.', 'CSRF_FAILED', 403)
    return row


def issue_session(store, user_id, response, settings):
    token = secrets.token_urlsafe(32)
    csrf = hash_token('csrf:'+token)
    ttl = int(settings.get('API_SESSION_HOURS', 12))*3600
    store.insert('api_auth_session', token_hash=hash_token(token), user_id=user_id,
                 csrf_hash=hash_token(csrf), expires_at=now()+timedelta(seconds=ttl))
    response.set_cookie('bs_session', token, max_age=ttl, httponly=True,
                        secure=settings.secure_cookie, samesite='lax', path='/')
    return csrf


def revoke_sessions(store, user_id):
    t=store.t('api_auth_session')
    store.c.execute(delete(t).where(t.c.user_id == user_id))


def administrator(user, super_only=False):
    allowed = ('SUPER_ADMIN',) if super_only else ('ADMIN','SUPER_ADMIN')
    require(user and user['role'] in allowed, '관리자 권한이 필요합니다.', 'FORBIDDEN', 403)


def limit_public(database, key, maximum=10, seconds=60):
    """별도 커밋으로 인증 실패에도 카운터 유지. 모든 서버 프로세스가 DB 카운터 공유."""
    from sqlalchemy.exc import IntegrityError
    t=database.table('api_rate_limit')
    for attempt in range(2):
        try:
            with database.engine.begin() as c:
                row=c.execute(select(t).where(t.c.bucket_key==key).with_for_update()).mappings().first()
                if row is None:
                    c.execute(t.insert().values(bucket_key=key,window_start=now(),request_count=1))
                    return
                if row['window_start']+timedelta(seconds=seconds) <= now():
                    c.execute(t.update().where(t.c.bucket_key==key).values(window_start=now(),request_count=1))
                    return
                if row['request_count'] >= maximum:
                    raise ApiError(429,'RATE_LIMITED','잠시 후 다시 시도하세요.',retryable=True)
                c.execute(t.update().where(t.c.bucket_key==key).values(request_count=row['request_count']+1))
                return
        except IntegrityError:
            if attempt:
                raise
