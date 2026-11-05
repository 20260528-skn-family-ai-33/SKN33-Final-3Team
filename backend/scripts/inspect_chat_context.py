"""Read-only Redis chat context inspection; never print connection credentials."""
import argparse
import json
from backend.app.api.config import Settings
from backend.app.api.redis_store import RedisState
from backend.app.agents.state import CONTEXT_KEY


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('session_id', help='Chat session ID from the messages request in browser Network')
    args = parser.parse_args()
    redis = RedisState(Settings())
    key = redis.prefix + CONTEXT_KEY.format(session_id=args.session_id)
    raw = redis.chat_context(args.session_id)
    print(json.dumps({'key': key, 'ttl_seconds': redis.call('ttl', key),
        'active_job_id': redis.chat_active(args.session_id),
        'context': json.loads(raw) if raw else None}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
