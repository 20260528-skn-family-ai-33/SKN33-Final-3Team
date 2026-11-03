"""Serve published team generations without renaming collections or changing index writers."""
import json
import re

from backend.app.schemas.rag import ArticleSource
from backend.app.services.rag.store import (
    ARTICLE_COLLECTION, MODEL, GenerationStore, IndexSnapshot, validate_document_contract,
)


class _TeamCollectionClient:
    def __init__(self, client):
        self.client = client

    def get_collection(self, name, *, embedding_function):
        if name != ARTICLE_COLLECTION:
            raise ValueError('unknown_article_collection')
        return self.client.get_collection('total_article', embedding_function=embedding_function)

    def close(self):
        self.client.close()


class SearchStore(GenerationStore):
    def snapshot(self):
        try:
            return super().snapshot()
        except ValueError as exc:
            if str(exc) != 'single_collection_migration_required':
                raise
        generation = json.loads((self.root / 'active.json').read_text())['generation']
        if not re.fullmatch(r'[0-9a-f]{32}', generation):
            raise ValueError('invalid_generation')
        folder = self.root / 'generations' / generation
        report = json.loads((folder / 'status.json').read_text())
        validate_document_contract(report)
        if (report.get('generation') != generation or report.get('status') != 'ready'
                or set(report.get('collections', {})) != {'total_article'}
                or report.get('embedding_model') != MODEL or report.get('dimensions') != self.dimensions
                or not (folder / 'chroma/chroma.sqlite3').is_file()):
            raise ValueError('incompatible_team_search_index')
        sources = frozenset(ArticleSource(s) for s in report['sources'])
        client = _TeamCollectionClient(self.client_factory(folder / 'chroma'))
        return IndexSnapshot(generation, client, sources, self.dimensions)
