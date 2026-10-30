"""에이전트 tool. 설계: docs/AGENT_DESIGN.md §5."""
from backend.app.schemas.rag import RagRequest, RagResponse

NEWS_PENDING_MESSAGE = '기사 검색은 지원 예정입니다.'


def search_news(request: RagRequest) -> RagResponse | None:
    """RAG 담당자가 구현할 기사 검색 자리. None이면 tool은 NO_DATA + NEWS_PENDING_MESSAGE로 처리한다(D-43).
    TODO(C-15): RAG 모듈이 연결되면 이 함수가 RagResponse를 돌려준다."""
    return None
