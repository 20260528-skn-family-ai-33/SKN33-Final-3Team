"""단신 본문은 공통 세대 색인 한 곳에 저장합니다. 분류 후보 색인은 별개입니다."""
from collections.abc import Mapping
from copy import deepcopy

ARTICLE_COLLECTION = 'kotra_article'
INTERNAL_SOURCE = 'KOTRA_SHORT_NEWS'
API_SOURCE = 'KOTRA_BREAKING'


def internal_source(source):
    """API 표기만 변환하며 DB의 출처 값은 변경하지 않습니다."""
    if source in (API_SOURCE, INTERNAL_SOURCE):
        return INTERNAL_SOURCE
    raise ValueError('지원하지 않는 단신속보 출처')


def sync_body(*, db_url, root=None, embed=None, dry_run=False, progress=None, run_sources=None):
    """공통 함수의 최종 결과를 검증하고 partial/failed를 그대로 전달합니다."""
    if db_url is None:
        raise ValueError('명시적 DB 대상이 필요합니다')
    if run_sources is None:
        from backend.app.scheduler.jobs.news_embed import run_sources
    def checkpoint(value):
        if progress is not None:
            progress(deepcopy(value))
    result = run_sources([INTERNAL_SOURCE], db_url=db_url, root=root, embed=embed,
                         dry_run=dry_run, progress=checkpoint if progress else None)
    if not isinstance(result, Mapping) or result.get('status') not in {'complete', 'planned', 'partial', 'failed'}:
        raise ValueError('공통 본문 색인 결과의 status 계약 불일치')
    if result.get('sources') != [INTERNAL_SOURCE]:
        raise ValueError('공통 본문 색인의 요청·결과 출처 불일치')
    body, words = result.get('body', {}), result.get('keywords', {})
    if (body.get('sources') != [INTERNAL_SOURCE] or body.get('collection') != ARTICLE_COLLECTION
            or words.get('scope') != 'all_database_sources' or words.get('collection') != 'news_keyword'):
        raise ValueError('공통 본문·키워드 범위 계약 불일치')
    expected = 'planned' if dry_run else 'complete'
    if result['status'] in {'complete', 'planned'} and (
            result['status'] != expected or body.get('status') != expected or words.get('status') != expected):
        raise ValueError('공통 본문 색인 단계 완료 계약 불일치')
    return deepcopy(dict(result))
