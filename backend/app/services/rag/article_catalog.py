"""Read original bodies matching the selected index versions."""
import sqlite3
from pathlib import Path

from backend.app.services.rag.documents import ArticleDocument


class LocalArticleCatalog:
    def __init__(self, path):
        self.path = Path(path).resolve()
        if not self.path.is_file():
            raise FileNotFoundError('article_catalog_missing')

    def originals(self, candidates):
        """Use the catalog only for the exact versions found in the vector snapshot."""
        found = {}
        conn = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True)
        try:
            for candidate in candidates:
                key = candidate.article.key
                row = conn.execute('SELECT payload FROM articles WHERE source_code=? AND external_id=?', key).fetchone()
                if row:
                    article = ArticleDocument.model_validate_json(row[0])
                    if article.key == key and article.version == candidate.version:
                        found[key] = article
        finally:
            conn.close()
        return found
