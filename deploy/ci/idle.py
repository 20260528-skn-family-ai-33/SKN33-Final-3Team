"""Refuse a service restart while a user job or collector is active."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from sqlalchemy import text
from backend.app.api.config import Settings

def main():
    engine = Settings().engine()
    queries = [
        "SELECT 1 FROM chat_message WHERE JSON_UNQUOTE(JSON_EXTRACT(result_json,'$._execution.status')) IN ('QUEUED','RUNNING') LIMIT 1",
        "SELECT 1 FROM report WHERE status IN ('QUEUED','RUNNING') LIMIT 1",
        "SELECT 1 FROM batch_run WHERE status='RUNNING' LIMIT 1",
    ]
    try:
        with engine.connect() as conn:
            return 75 if any(conn.execute(text(query)).first() for query in queries) else 0
    finally:
        engine.dispose()

if __name__ == "__main__":
    sys.exit(main())
