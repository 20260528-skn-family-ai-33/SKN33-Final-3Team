"""이름·이메일 확인 및 SMTP 임시 비밀번호 발급. 평문은 메일에만 사용한다."""
import secrets
import json
import math
import subprocess
import sys
import time
import string
from pathlib import Path

import bcrypt
from pydantic import BaseModel, Field
from sqlalchemy import update

from backend.app.api import accounts
from backend.app.api.common import ApiError, require
from backend.app.api.security import revoke_sessions, hash_token
from backend.app.api.database import Store
from backend.app.api.schemas import AccountEmail


class RecoveryIdentity(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: AccountEmail


def identity(data):
    value = RecoveryIdentity.model_validate(data)
    require(bool(value.name.strip()), '이름을 입력해 주세요.')
    return value.name.strip(), str(value.email).strip()


def matches(user, name, email):
    # 계정 식별은 대소문자까지 일치해야 한다. DB 조회 결과도 다시 확인한다.
    return bool(user and user['account_status'] != 'WITHDRAWN'
                and (user['name'] or '').strip() == name and user['email'] == email)


def confirm_account(store, data):
    name, email = identity(data)
    found = matches(accounts.get_user(store, email=email), name, email)
    return {'matches': found, 'message': '가입된 계정입니다.' if found else '해당정보로 가입된 계정이 없습니다'}


def limit_request(database, data, settings):
    require(data.get('action') in ('ISSUE', 'REISSUE'), '발급 요청을 확인하세요.')
    _, email = identity(data)
    cooldown = int(settings.get('API_RECOVERY_COOLDOWN_SECONDS', 600))
    maximum = int(settings.get('API_RECOVERY_DAILY_MAX', 5))
    require(cooldown > 0 and maximum > 0, '계정 복구 요청 제한 설정을 확인하세요.', 'RECOVERY_NOT_CONFIGURED', 503)
    # 대소문자가 다른 계정도 같은 메일함으로 전달될 수 있어 남용 제한은 공유한다.
    database.redis.limit_recovery(hash_token(email.casefold()), cooldown, maximum)


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
    try:
        port = int(settings.get('API_SMTP_PORT', 465 if mode == 'ssl' else 587))
        timeout = float(settings.get('API_SMTP_TIMEOUT_SECONDS', 10))
        total = float(settings.get('API_SMTP_TOTAL_TIMEOUT_SECONDS', 15))
        require(math.isfinite(total) and 0 < total <= 15 and math.isfinite(timeout) and timeout > 0,
                '메일 발송 시간 제한 설정을 확인하세요.', 'MAIL_NOT_CONFIGURED', 503)
        payload = {'host': host, 'port': port, 'mode': mode, 'sender': sender, 'recipient': recipient,
                   'username': settings.get('API_SMTP_USERNAME'), 'password': settings.get('API_SMTP_PASSWORD', ''),
                   'temporary_password': password, 'timeout': min(timeout, total)}
        # DNS·TLS·다단계 인증까지 포함한 총 상한. 스레드 타임아웃은 종료 후에도 메일을 보낼 수 있어
        # 별도 프로세스를 사용하고, 시간 초과 시 종료·회수한 뒤 조건부 복구를 진행한다.
        # 자격정보/임시 비밀번호는 명령 인자·로그·임시 파일에 넣지 않고 stdin 파이프로만 전달한다.
        deadline = time.monotonic() + total
        # Windows venv launcher는 별도 Python을 띄울 수 있다. 표준 라이브러리만 쓰는 작업은
        # 실제 인터프리터를 직접 실행해야 kill()이 전송 프로세스 자체를 종료한다.
        executable = getattr(sys, '_base_executable', None) or sys.executable
        process = subprocess.Popen([executable, '-I', '-S', str(Path(__file__).with_name('smtp_delivery.py'))],
                                   stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            process.communicate(json.dumps(payload).encode('utf-8'), timeout=max(0, deadline-time.monotonic()))
            if process.returncode != 0:
                raise OSError('SMTP delivery failed')
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
    except (OSError, subprocess.TimeoutExpired, ValueError):
        raise ApiError(503, 'MAIL_DELIVERY_FAILED', '메일을 발송하지 못했습니다. 잠시 후 다시 시도해 주세요.', retryable=True) from None


def prepare_password(store, data):
    require(data.get('action') in ('ISSUE', 'REISSUE'), '발급 요청을 확인하세요.')
    name, email = identity(data)
    user = accounts.get_user(store, email=email, lock=True)
    # 없음/이름 불일치/이용 제한/탈퇴/소셜 전용 여부를 서로 다른 응답으로 노출하지 않는다.
    eligible = (matches(user, name, email) and user['account_status'] == 'ACTIVE'
                and bool(user['password_hash']) and user['password_hash'].startswith(('$2a$', '$2b$', '$2y$')))
    require(eligible, '입력한 정보로 임시 비밀번호를 발급할 수 없습니다. 가입 정보를 확인하거나 기존 로그인 방법을 이용해 주세요.',
            'RECOVERY_NOT_AVAILABLE', 403)
    value = temporary_password()
    hashed = bcrypt.hashpw(value.encode(), bcrypt.gensalt()).decode()
    users = store.t('users')
    store.c.execute(update(users).where(users.c.user_id == user['user_id']).values(password_hash=hashed))
    revoke_sessions(store, user['user_id'])
    return user, email, value, hashed


def issue_password(database, data, settings):
    # UPDATE뿐 아니라 COMMIT 성공까지 확인한 후에만 SMTP를 호출한다.
    with database.engine.begin() as connection:
        user, email, value, hashed = prepare_password(Store(database, connection), data)
    try:
        send_temporary_password(settings, email, value)
    except ApiError:
        # 메일 실패 시 후속 변경을 덮어쓰지 않는 조건부 복구. SMTP 동안 행 잠금은 유지하지 않는다.
        with database.engine.begin() as connection:
            users = Store(database, connection).t('users')
            connection.execute(update(users).where(users.c.user_id == user['user_id'],
                               users.c.password_hash == hashed, users.c.auth_version == user['auth_version']+1)
                               .values(password_hash=user['password_hash'], auth_version=user['auth_version']))
        raise
    return {'message': '입력한 이메일로 임시 비밀번호를 발송했습니다. 로그인 후 비밀번호를 변경해 주세요.'}
