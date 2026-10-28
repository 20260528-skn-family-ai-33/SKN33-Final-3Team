"""ML/RAG 담당자 연결 지점. 이 모듈은 모델 학습·임베딩·수집을 하지 않는다."""
from typing import Protocol


class RagProvider(Protocol):
    def search_and_summarize(self, *, question: str, conditions: dict, allowed_articles: list[dict]) -> dict:
        """{articles, text, evidence_ids} 반환. 서버가 필터링한 article_id만 근거로 사용."""
        ...


class IntentProvider(Protocol):
    def extract(self, *, question: str, previous_conditions: dict) -> dict:
        """intent.IntentSlots 형식 반환. 국가명·원문 품목 표현만 추출하며 HS 코드는 추론하지 않는다.
        서버가 국가/품목/기간을 검증한 후 고정 쿼리에 사용한다.
        """
        ...


class MachineLearningProvider(Protocol):
    def analyze(self, *, conditions: dict, verified_rows: list[dict]) -> dict:
        """API-23/27은 팀의 보류 해제 및 계약 확정 후 연결."""
        ...
