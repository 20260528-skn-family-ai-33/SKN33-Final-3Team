"""기존 article 컬럼의 검증을 적재 계획과 INSERT 직전에 동일하게 적용합니다."""
from datetime import date


def article_values(prepared):
    """본문을 바꾸거나 자르지 않고 기존 ERD 형식에 맞는지 확인합니다."""
    if not prepared['eligible']:
        raise ValueError('품질·주제 검증을 통과한 기사만 적재합니다')
    for field, maximum in [('external_id',100),('title',600),('source_url',2048)]:
        value = prepared[field]
        if not isinstance(value,str) or not value.strip() or len(value)>maximum:
            raise ValueError(f'{field}: 프로젝트 필드 길이·형식 오류')
    if not prepared['external_id'].isascii():
        raise ValueError('external_id는 ASCII 문자열이어야 합니다')
    if not isinstance(prepared['body_masked'],str) or not prepared['body_masked'].strip():
        raise ValueError('서비스 본문이 비어 있습니다')
    category = prepared.get('category')
    if category is not None and (not isinstance(category,str) or len(category)>120):
        raise ValueError('category 형식 오류')
    return (prepared['title'], date.fromisoformat(prepared['published_date']),
            prepared['source_url'], prepared['body_masked'], category)
