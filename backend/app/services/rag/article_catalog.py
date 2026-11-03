"""Read-only catalog for metadata-only publication-order retrieval."""
import json
import sqlite3
from pathlib import Path

from backend.app.schemas.rag import ArticleSource
from backend.app.services.rag.documents import ArticleDocument
from backend.app.services.rag.retrieval import Candidate, hs_match


class LocalArticleCatalog:
    def __init__(self, path):
        self.path = Path(path).resolve()
        if not self.path.is_file():
            raise FileNotFoundError('article_catalog_missing')

    def iter_candidates(self, request, context):
        """Publication dates determine latest; no body chunks or semantic inference."""
        sources = ([s.value for s in ArticleSource] if request.source_codes == 'ALL'
                   else [s.value for s in request.source_codes])
        c = request.conditions
        query = ('SELECT payload FROM articles WHERE source_code IN (' + ','.join('?' for _ in sources)
                 + ') AND published_date>=? AND published_date<=? '
                   'ORDER BY published_date DESC, source_code, external_id')
        conn = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True)
        try:
            for (payload,) in conn.execute(query, (*sources, c.period.start.isoformat(), c.period.end.isoformat())):
                context.remaining()
                article = ArticleDocument.model_validate_json(payload)
                if request.reference_scope == 'target_only':
                    if c.geography.mode == 'selected' and not set(c.geography.codes) & set(article.countries):
                        continue
                    if c.products and article.source_code == ArticleSource.KOTRA_MARKET and not hs_match(c.products, article.products):
                        continue
                if set(c.blocked_topics) & set(article.kw_topic):
                    continue
                # Block-tag uncertainty is handled separately by the service, never by body review.
                yield Candidate(article, 0.0, article.version, {})
        finally:
            conn.close()
