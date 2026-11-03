"""공개 가능한 합성 예시. 실제 기사·정답셋·검색 품질 결과가 아닙니다."""
from backend.app.schemas.rag import ALL_SOURCES


def request_payload(use_case="local_issue", **overrides):
    payload = {
        "request_id": "synthetic-request", "query": "미국의 배터리 관련 근거 기사를 찾아줘",
        "use_case": use_case,
        "conditions": {"geography": {"mode": "selected", "codes": ["US"]}, "products": [],
                       "period": {"start": "2026-09-01", "end": "2026-09-30"}, "blocked_topics": []},
    }
    payload.update(overrides)
    return payload


def article_payload(number=1, source=None):
    source = source or ALL_SOURCES[(number - 1) % len(ALL_SOURCES)].value
    return {
        "article_id": number, "source_code": source, "external_id": f"synthetic-{number}",
        "title": f"합성 기사 {number}", "countries": ["US"], "published_date": "2026-09-15",
        "source_url": f"https://example.org/articles/{number}", "body_status": "available",
        "content_version": "synthetic-v1",
        "evidence": [{"evidence_id": f"evidence-{number}", "chunk_id": f"{number}:0",
                      "quote": "이 문장은 계약 검증용 합성 문장입니다."}],
        "summary": [{"text": "계약 검증용 합성 문장이다.", "evidence_ids": [f"evidence-{number}"]}],
    }


def response_payload(request=None, status="ok", count=5, **overrides):
    request = request or request_payload()
    payload = {
        "request_id": request["request_id"], "use_case": request["use_case"], "status": status,
        "applied_conditions": request["conditions"], "target_count": request.get("limit", 5),
        "returned_count": count, "articles": [article_payload(i + 1) for i in range(count)],
        "completed_sources": [s.value for s in ALL_SOURCES],
    }
    if status != "ok":
        payload["reason"] = "합성 상태 예시"
    payload.update(overrides)
    return payload


def evaluation_payload():
    cases = []
    for number, source in enumerate(ALL_SOURCES, 1):
        cases.append({"case_id": f"synthetic-case-{number}",
                      "request": request_payload("chat", request_id=f"synthetic-request-{number}"),
                      "relevant_articles": [{"source_code": source.value, "external_id": f"synthetic-{number}"}]})
    return {"dataset_id": "synthetic-contract-smoke-v1", "split": "dev", "synthetic": True, "cases": cases}


def configuration_payload(**overrides):
    value = {"configuration_id": "synthetic-config-v1", "retriever_revision": "fake-search-v1",
             "corpus_revision": "synthetic-articles-v1", "embedding_model": "fake-embedding",
             "chunk_chars": 1000, "overlap_chars": 150, "collections": ["total_article"]}
    value.update(overrides)
    return value
