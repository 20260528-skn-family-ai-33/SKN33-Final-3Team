"""공통 본문 색인용 합성 자료. 상위 RAG 서비스에 의존하지 않습니다."""
import hashlib
from backend.app.schemas.rag import ALL_SOURCES
from backend.app.services.rag.documents import ArticleDocument


def vectors(texts):
    return [[b/255 for b in hashlib.sha256(t.encode()).digest()[:8]] for t in texts]


def document(n=1, source=None, **changes):
    value = dict(article_id=n, source_code=source or ALL_SOURCES[(n-1)%4], external_id=str(n),
                 title=f'합성 기사 {n}', body='미국 배터리 수출에 관한 검증용 문장입니다.',
                 source_url=f'https://example.org/{n}', countries=['US'], published_date='2026-09-15',
                 kw_topic=['배터리'], kw_sub=['배터리'], keywords_ready=True)
    value.update(changes)
    return ArticleDocument(**value)
