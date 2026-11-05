"""이름·이메일 확인 및 SMTP 임시 비밀번호 발급. 평문은 메일에만 사용한다."""
import secrets
import smtplib
import ssl
import string
from email.message import EmailMessage

import bcrypt
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import update

from backend.app.api import accounts
from backend.app.api.common import ApiError, require
from backend.app.api.security import revoke_sessions


class RecoveryIdentity(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr


def identity(data):
    value = RecoveryIdentity.model_validate(data)
    require(bool(value.name.strip()), '이름을 입력해 주세요.')
    return value.name.strip(), str(value.email).strip().lower()


def matches(user, name):
    return bool(user and user['account_status'] != 'WITHDRAWN' and (user['name'] or '').strip() == name)


def confirm_account(store, data):
    name, email = identity(data)
    found = matches(accounts.get_user(store, email=email), name)
    return {'matches': found, 'message': '가입된 계정입니다.' if found else '해당정보로 가입된 계정이 없습니다'}


def temporary_password():
    # 20자리, 대문자·소문자·숫자를 각각 포함하는 암호학적 난수.
    alphabet = string.ascii_letters + string.digits
    while True:
        value = ''.join(secrets.choice(alphabet) for _ in range(20))
        if all(any(c in group for c in value) for group in (string.ascii_uppercase, string.ascii_lowercase, string.digits)):
            return value


def send_temporary_password(settings, recipient, password):
    host, sender = settings.get('API_SMTP_HOST'), settings.get('API_SMTP_FROM')
    require(host and sender, '임시 비밀번호 메일 발송 설정이 준비되지 않았습니다.', 'MAIL_NOT_CONFIGURED', 503)
    mode = settings.get('API_SMTP_SECURITY', 'starttls').lower()
    require(mode in ('starttls', 'ssl'), '메일 서버 TLS 설정을 확인하세요.', 'MAIL_NOT_CONFIGURED', 503)
    message = EmailMessage()
    message['Subject'] = '[Borderless] 임시 비밀번호 안내'
    message['From'], message['To'] = sender, recipient
    message.set_content(f'임시 비밀번호: {password}\n\n이 비밀번호로 로그인한 뒤 계정 설정에서 비밀번호를 변경해 주세요.\n재발급하면 이전 비밀번호는 사용할 수 없습니다.')
    try:
        port = int(settings.get('API_SMTP_PORT', 465 if mode == 'ssl' else 587))
        timeout = int(settings.get('API_SMTP_TIMEOUT_SECONDS', 10))
        client = smtplib.SMTP_SSL(host, port, timeout=timeout, context=ssl.create_default_context()) if mode == 'ssl' else smtplib.SMTP(host, port, timeout=timeout)
        try:
            if mode == 'starttls':
                client.starttls(context=ssl.create_default_context())
            if settings.get('API_SMTP_USERNAME'):
                client.login(settings.get('API_SMTP_USERNAME'), settings.get('API_SMTP_PASSWORD', ''))
            refused = client.send_message(message)
            if refused:
                raise smtplib.SMTPRecipientsRefused(refused)
        finally:
            # DATA 접수 뒤 QUIT 연결 종료 오류가 발송 성공을 실패로 뒤집지 않게 한다.
            try:
                client.quit()
            except (OSError, smtplib.SMTPException):
                client.close()
    except (OSError, smtplib.SMTPException, ValueError):
        raise ApiError(503, 'MAIL_DELIVERY_FAILED', '메일을 발송하지 못했습니다. 잠시 후 다시 시도해 주세요.', retryable=True) from None


def issue_password(store, data, settings):
    require(data.get('action') in ('ISSUE', 'REISSUE'), '발급 요청을 확인하세요.')
    name, email = identity(data)
    user = accounts.get_user(store, email=email, lock=True)
    require(matches(user, name), '해당정보로 가입된 계정이 없습니다', 'ACCOUNT_NOT_FOUND', 404)
    require(user['account_status'] == 'ACTIVE', '이용할 수 없는 계정입니다.', 'ACCOUNT_UNAVAILABLE', 403)
    # 소셜 전용 계정에 로컬 비밀번호를 임의로 만들지 않는다.
    require(bool(user['password_hash']) and user['password_hash'].startswith(('$2a$', '$2b$', '$2y$')),
            '소셜 가입 계정은 해당 소셜 로그인으로 이용해 주세요.', 'SOCIAL_ACCOUNT', 400)
    value = temporary_password()
    hashed = bcrypt.hashpw(value.encode(), bcrypt.gensalt()).decode()
    users = store.t('users')
    store.c.execute(update(users).where(users.c.user_id == user['user_id']).values(password_hash=hashed))
    revoke_sessions(store, user['user_id'])
    # 요청 트랜잭션 안에서 발송: 명시적인 SMTP 실패는 위 변경도 롤백한다.
    send_temporary_password(settings, email, value)
    return {'message': '입력한 이메일로 임시 비밀번호를 발송했습니다. 로그인 후 비밀번호를 변경해 주세요.'}
