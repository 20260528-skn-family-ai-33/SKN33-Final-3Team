"""Publish synthetic API database rows to an isolated RAG index, without external inference."""
from contextlib import contextmanager
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from sqlalchemy import select

from backend.app.schemas.rag import ALL_SOURCES
from backend.app.services.rag.service import RagService
from backend.app.services.rag.store import GenerationStore
from backend.app.services.rag.vocabulary import Vocabulary
from backend.tests.test_rag_pipeline import FakeGateway, document, vectors


@contextmanager
def indexed_news(case):
    with TemporaryDirectory() as root, case.engine.connect() as conn:
        records = conn.execute(select(case.meta.tables['article'])).mappings()
        docs = [document(row['article_id'], source=row['source_code'], external_id=row['external_id'],
                         title=row['title'], category=row['category'], body=row['body_masked'] or '', published_date=row['published_date'],
                         source_url=row['source_url']) for row in records if row['source_url']]
        store = GenerationStore(Path(root), dimensions=8)
        store.publish(docs, ALL_SOURCES, vectors)
        from backend.app.services.rag.article_catalog import LocalArticleCatalog
        catalog = Path(root) / 'article_catalog.sqlite3'
        with sqlite3.connect(catalog) as db:
            db.execute('CREATE TABLE articles(source_code,external_id,published_date,payload)')
            db.executemany('INSERT INTO articles VALUES (?,?,?,?)',
                           [(a.source_code.value, a.external_id, a.published_date.isoformat(),
                             a.model_dump_json()) for a in docs])
        service = RagService(store, Vocabulary(), FakeGateway(), catalog=LocalArticleCatalog(catalog))
        with patch('backend.app.agents.tools.OpenAI'), patch('backend.app.agents.tools.create_service',return_value=service):
            yield
