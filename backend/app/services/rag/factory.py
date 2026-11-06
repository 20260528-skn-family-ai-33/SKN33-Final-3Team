"""벡터 검색과 동일 버전 원문을 사용하는 v26 서비스를 생성합니다."""
from pathlib import Path

from backend.app.services.rag.gateway import ModelGateway
from backend.app.services.rag.service import RagService
from backend.app.services.rag.store import GenerationStore, resolve_root


def create_service(api, *, root=None, catalog_path=None, article_allowed=None):
    """각 run이 색인과 사전을 같은 버전으로 고정합니다. 생성 시 외부 호출 없음."""
    root = resolve_root(root)
    from backend.app.services.rag.article_catalog import LocalArticleCatalog
    path = Path(catalog_path) if catalog_path else root / 'article_catalog.sqlite3'
    catalog = LocalArticleCatalog(path) if path.is_file() else None
    return RagService(GenerationStore(root), None, ModelGateway(api),
                      cache_dir=Path(root)/'summary_cache', trace_dir=Path(root)/'traces', catalog=catalog, article_allowed=article_allowed)
