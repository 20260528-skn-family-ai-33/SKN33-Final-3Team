"""색인 시 보존한 사전으로 서비스 생성. 실제 기사 검색은 Chroma만 사용합니다."""
from pathlib import Path

from backend.app.services.rag.gateway import ModelGateway
from backend.app.services.rag.service import RagService
from backend.app.services.rag.store import GenerationStore, resolve_root


def create_service(api, *, root=None):
    """각 run이 색인과 사전을 같은 버전으로 고정합니다. 생성 시 외부 호출 없음."""
    root = resolve_root(root)
    return RagService(GenerationStore(root), None, ModelGateway(api),
                      cache_dir=Path(root)/'summary_cache', trace_dir=Path(root)/'traces')
