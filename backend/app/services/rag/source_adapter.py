"""API 화면 유형 별칭을 명시적으로 정규화합니다. DB SOURCE는 별도 계약입니다."""
def normalize_article_type(value):
    if value == 'BREAKING':
        return 'SHORT_NEWS'
    if value in ('ALL', 'MARKET', 'SHORT_NEWS'):
        return value
    raise ValueError('unsupported_article_type')


def sources_for_article_type(value):
    """API 탭 유형만 변환합니다. DB SOURCE 및 RAG 모델에는 별칭을 넣지 않습니다."""
    value = normalize_article_type(value)
    return {'ALL':'ALL', 'MARKET':('KOTRA_MARKET',), 'SHORT_NEWS':('KOTRA_SHORT_NEWS',)}[value]
