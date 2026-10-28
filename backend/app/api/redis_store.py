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
