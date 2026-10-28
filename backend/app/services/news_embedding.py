"""뉴스 본문·공통 키워드의 임베딩 설정. 변경 후 news_embed --rebuild가 필요합니다."""

MODEL = 'text-embedding-3-large'
DIMENSIONS = 3072


def embed(api, texts, *, usage_sink=None, model=None, dimensions=None):
    """공통 본문·키워드 제공자. 빌린 API 클라이언트는 호출자가 닫습니다."""
    import math
    from backend.app.services.news_usage import record_usage
    model = MODEL if model is None else model
    dimensions = DIMENSIONS if dimensions is None else dimensions
    response = None
    primary = None
    try:
        response = api.embeddings.create(model=model, dimensions=dimensions, input=texts)
    except BaseException as exc:
        primary = exc
        raise
    finally:
        record_usage(usage_sink, model, response, primary=primary)
    ordered = sorted(response.data, key=lambda row: row.index)
    if [r.index for r in ordered] != list(range(len(texts))):
        raise ValueError('embedding_response_indices')
    vectors = [r.embedding for r in ordered]
    if any(len(v) != dimensions or not all(math.isfinite(float(x)) for x in v)
           or not any(float(x) for x in v) for v in vectors):
        raise ValueError('invalid_embedding')
    return vectors
