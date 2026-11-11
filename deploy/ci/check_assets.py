"""Read-only production capability checks. Execute with the worker interpreter and identity."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def model_check():
    from backend.app.agents.market import growth, loaded
    from backend.app.api.config import Settings
    settings = Settings()
    bundle, predict, _ = loaded(settings)
    predictions = bundle["predictions"]
    if predictions.empty or predict(bundle, country_code=predictions.iloc[0].country_code, top=1).empty:
        raise ValueError("model A returned no sample prediction")
    optional, _, _ = growth(settings, bundle["anchor"][:7])
    if not optional:
        print("Optional model B unavailable", flush=True)
        return False
    return True


def article_check():
    from backend.app.api.config import Settings
    from backend.app.services.news_collections import ARTICLE_COLLECTION
    from backend.app.services.rag.store import GenerationStore, shared_client
    snapshot = GenerationStore(Settings().get("NEWS_VECTOR_ROOT"), client_factory=shared_client).snapshot()
    try:
        rows = snapshot.collection(ARTICLE_COLLECTION).query(
            query_embeddings=[[0.0] * (snapshot.dimensions - 1) + [1.0]], n_results=1)
        if not rows.get("ids") or not rows["ids"][0]:
            raise ValueError("active article collection returned no results")
    finally:
        snapshot.client.close()


def database_check():
    from backend.app.api.config import Settings
    from backend.scripts.migrate_integration import plan
    from backend.scripts.migrate_email_case import plan as email_plan
    engine = Settings().engine()
    try:
        with engine.connect() as conn:
            if plan(conn) or email_plan(conn):
                raise ValueError("pending migration")
    finally:
        engine.dispose()


if __name__ == "__main__":
    optional_missing = False
    checks = [("schema", database_check), ("model A", model_check), ("RAG article index", article_check)]
    for code, (name, check) in enumerate(checks, start=11):
        try:
            if check() is False:
                optional_missing = True
        except Exception as exc:
            print(f"Required check failed: {name} ({type(exc).__name__})", flush=True)
            sys.exit(code)
        print(f"Verified {name}", flush=True)
    sys.exit(4 if optional_missing else 0)
