"""Shared Redis state; unavailable Redis never silently disables authentication limits."""
import json
import secrets
from redis import Redis
from redis.exceptions import RedisError
from backend.app.api.common import ApiError

RATE_SCRIPT = """
local n = redis.call('INCR', KEYS[1])
if n == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return n
"""

# D-77: an expired owner must never remove/renew its successor's lock.
CHAT_RELEASE_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""
CHAT_RENEW_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('EXPIRE', KEYS[1], ARGV[2])
end
return 0
"""
CHAT_CONTEXT_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    redis.call('SET', KEYS[2], ARGV[2], 'EX', ARGV[3])
    return 1
end
return 0
"""

# 최근 24시간의 허용 요청과 재요청 간격을 하나의 원자적 연산으로 예약한다.
# 계정 존재·메일 성공 여부와 무관하게 차감하며 거절 요청은 TTL을 연장하지 않는다.
RECOVERY_RATE_SCRIPT = """
local clock = redis.call('TIME')
local now = tonumber(clock[1]) * 1000 + math.floor(tonumber(clock[2]) / 1000)
local window = 86400000
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', now - window)
local wait = math.max(0, redis.call('PTTL', KEYS[1]))
if redis.call('ZCARD', KEYS[2]) >= tonumber(ARGV[2]) then
  local oldest = redis.call('ZRANGE', KEYS[2], 0, 0, 'WITHSCORES')
  wait = math.max(wait, tonumber(oldest[2]) + window - now)
end
if wait > 0 then return math.ceil(wait / 1000) end
redis.call('SET', KEYS[1], '1', 'EX', ARGV[1])
redis.call('ZADD', KEYS[2], now, ARGV[3])
redis.call('PEXPIRE', KEYS[2], window)
return 0
"""


class RedisState:
    def __init__(self, settings, client=None):
        self.client = client or Redis.from_url(settings.get('API_REDIS_URL', 'redis://127.0.0.1:6379/0'),
            decode_responses=True, socket_connect_timeout=2, socket_timeout=2)
        self.prefix = settings.get('API_REDIS_PREFIX', 'borderless:dev:')
        if not self.prefix.endswith(':'): self.prefix += ':'

    def call(self, method, *args, **kwargs):
        try: return getattr(self.client, method)(*args, **kwargs)
        except RedisError:
            raise ApiError(503, 'REDIS_UNAVAILABLE', '인증 저장소 연결을 확인하세요. 잠시 후 다시 시도하세요.', retryable=True) from None

    def ping(self): return self.call('ping')

    def queue_id(self):
        """이 Redis 한 대를 가리키는 고정 ID(D-106). 처음 부른 프로세스가 만들고, 다른 프로세스는 같은 값을 읽는다.
        TTL이 없어 Redis를 비우거나 새로 띄우면 바뀌는데, 그때는 체크포인트도 함께 사라지므로 옛 작업을 못 가져가는 편이 맞다."""
        key = self.prefix + 'job_queue_id'
        token = secrets.token_hex(16)
        # SET NX GET(Redis 7+, 에이전트는 Redis 8 필요 D-07): 있으면 기존 값, 없으면 None을 돌려줘 한 번에 끝난다
        return self.call('set', key, token, nx=True, get=True) or token

    def chat_context(self, session_id, *, refresh=False):
        from backend.app.agents.state import CONTEXT_KEY, CONTEXT_TTL_SECONDS
        key = self.prefix + CONTEXT_KEY.format(session_id=session_id)
        return self.call('getex', key, ex=CONTEXT_TTL_SECONDS) if refresh else self.call('get', key)

    def save_chat_context(self, session_id, job_id, payload):
        """After MySQL commit and graph return, publish only for this lock owner."""
        from backend.app.agents.state import ACTIVE_KEY, CONTEXT_KEY, CONTEXT_TTL_SECONDS
        return bool(self.call('eval', CHAT_CONTEXT_SCRIPT, 2,
            self.prefix + ACTIVE_KEY.format(session_id=session_id),
            self.prefix + CONTEXT_KEY.format(session_id=session_id),
            str(job_id), payload, CONTEXT_TTL_SECONDS))

    def chat_active(self, session_id):
        from backend.app.agents.state import ACTIVE_KEY
        return self.call('get', self.prefix + ACTIVE_KEY.format(session_id=session_id))

    def clear_chat_context(self, session_id):
        from backend.app.agents.state import CONTEXT_KEY
        return self.call('delete',self.prefix+CONTEXT_KEY.format(session_id=session_id))

    def acquire_chat(self, session_id, job_id):
        from backend.app.agents.state import ACTIVE_KEY, ACTIVE_TTL_SECONDS
        return bool(self.call('set', self.prefix + ACTIVE_KEY.format(session_id=session_id),
                              str(job_id), nx=True, ex=ACTIVE_TTL_SECONDS))

    def release_chat(self, session_id, job_id):
        from backend.app.agents.state import ACTIVE_KEY
        return bool(self.call('eval', CHAT_RELEASE_SCRIPT, 1,
                             self.prefix + ACTIVE_KEY.format(session_id=session_id), str(job_id)))

    def renew_chat(self, session_id, job_id):
        from backend.app.agents.state import ACTIVE_KEY, ACTIVE_TTL_SECONDS
        return bool(self.call('eval', CHAT_RENEW_SCRIPT, 1,
            self.prefix + ACTIVE_KEY.format(session_id=session_id), str(job_id), ACTIVE_TTL_SECONDS))

    def save_session(self, token_hash, payload, ttl):
        self.call('set', self.prefix+'session:'+token_hash, json.dumps(payload), ex=ttl)

    def session(self, token_hash):
        value = self.call('get', self.prefix+'session:'+token_hash)
        return json.loads(value) if value else None

    def revoke(self, token_hash):
        self.call('delete', self.prefix+'session:'+token_hash)

    def limit(self, key, maximum, seconds):
        count = self.call('eval', RATE_SCRIPT, 1, self.prefix+'limit:'+key, seconds)
        if count > maximum:
            raise ApiError(429, 'RATE_LIMITED', '요청이 많습니다. 잠시 후 다시 시도하세요.', retryable=True)

    def limit_recovery(self, email_hash, cooldown=600, daily_maximum=5):
        # 중괄호 hash tag는 Redis Cluster에서도 두 키를 같은 슬롯에 둔다. 이메일 원문은 저장하지 않는다.
        key = self.prefix + 'recovery:{' + email_hash + '}'
        wait = int(self.call('eval', RECOVERY_RATE_SCRIPT, 2, key+':cooldown', key+':daily',
                             cooldown, daily_maximum, secrets.token_hex(16)))
        if wait:
            raise ApiError(429, 'RECOVERY_RATE_LIMITED', f'임시 비밀번호 요청이 많습니다. {wait}초 후 다시 시도해 주세요.',
                           retryable=True, retry_after=wait)
