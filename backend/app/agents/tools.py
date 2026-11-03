"""에이전트 tool. 설계: docs/AGENT_DESIGN.md §5, §13.4, §14.4."""
import time

from openai import OpenAI

from backend.app.api.config import Settings
from backend.app.schemas.rag import RagRequest, RagResponse
from backend.app.services.rag.factory import create_service
from backend.app.services.rag.runtime import ExecutionContext

NEWS_PENDING_MESSAGE = '기사 검색은 지원 예정입니다.'


def search_news(request: RagRequest) -> RagResponse:
    """챗봇의 기사 검색 요청을 공통 RAG 서비스에 전달한다."""
    context = ExecutionContext(time.monotonic() + 15)
    settings = Settings()
    root = settings.get('NEWS_VECTOR_ROOT')
    if request.order_by == 'published_date_desc':
        return create_service(None, root=root).run(request, context)
    with OpenAI(api_key=settings.get('OPENAI_API_KEY'), timeout=context.remaining(), max_retries=0) as api:
        return create_service(api, root=root).run(request, context)
