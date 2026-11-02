"""Shared Redis state; unavailable Redis never silently disables authentication limits."""
import json
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
