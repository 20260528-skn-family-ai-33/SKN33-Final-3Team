import hashlib
import hmac
import re
import secrets
from ipaddress import ip_address, ip_network
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
    token_hash = hash_token(token)
    session = store.db.redis.session(token_hash)
    u = store.t('users')
    row = store.one(select(u).where(u.c.user_id == session['user_id'])) if session else None
    if not row or row['account_status'] != 'ACTIVE' or row['auth_version'] != session['auth_version']:
        raise ApiError(401, 'UNAUTHENTICATED', '세션이 만료되었거나 이용할 수 없는 계정입니다.')
    row.update(csrf_hash=session['csrf_hash'], token_hash=token_hash)
    if request.method not in ('GET','HEAD','OPTIONS'):
        provided = request.headers.get('X-CSRF-Token', '')
        require(hmac.compare_digest(row['csrf_hash'], hash_token(provided)), 'CSRF 검증에 실패했습니다.', 'CSRF_FAILED', 403)
    return row


def issue_session(store, user_id, response, settings):
    token = secrets.token_urlsafe(32)
    csrf = hash_token('csrf:'+token)
    ttl = int(settings.get('API_SESSION_HOURS', 12))*3600
    u = store.t('users')
    version = store.c.scalar(select(u.c.auth_version).where(u.c.user_id == user_id))
    store.db.redis.save_session(hash_token(token), {'user_id':str(user_id),
        'csrf_hash':hash_token(csrf), 'auth_version':version}, ttl)
    response.set_cookie('bs_session', token, max_age=ttl, httponly=True,
                        secure=settings.secure_cookie, samesite='lax', path='/')
    return csrf


def revoke_sessions(store, user_id):
    # Transactional generation counter invalidates ALL device sessions without a Redis scan.
    from sqlalchemy import update
    t=store.t('users')
    store.c.execute(update(t).where(t.c.user_id == user_id).values(auth_version=t.c.auth_version+1))


def administrator(user):
    require(user and user['role']=='ADMIN', '관리자 권한이 필요합니다.', 'FORBIDDEN', 403)


def client_ip(request, settings):
    peer=request.client.host if request.client else 'unknown'
    networks=[ip_network(value.strip()) for value in settings.get('API_TRUSTED_PROXY_CIDRS','').split(',') if value.strip()]
    def trusted(value):
        address=ip_address(value)
        return any(address in network for network in networks)
    try:
        if not trusted(peer): return peer
        forwarded=request.headers.get('x-forwarded-for','')
        if not forwarded or len(forwarded)>2048: return peer
        chain=[str(ip_address(part.strip())) for part in forwarded.split(',')]
        for address in reversed(chain):
            if not trusted(address): return address
    except ValueError:
        return peer
    return peer


def limit_auth_request(database, request, settings, identity, body):
    email=str(body.get('email') or '').strip().casefold()
    if email:
        limit_public(database,hash_token(f'{identity}:email:{email}'),
                     maximum=int(settings.get('API_AUTH_EMAIL_LIMIT',10)))
    limit_public(database,hash_token(f'{identity}:ip:{client_ip(request,settings)}'),
                 maximum=int(settings.get('API_AUTH_IP_LIMIT',100)))


def limit_public(database, key, maximum=10, seconds=60):
    database.redis.limit(key, maximum, seconds)
