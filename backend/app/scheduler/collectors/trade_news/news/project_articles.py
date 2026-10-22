"""프로젝트 적재용 검수 통과 기사 목록. 각 원문을 모두 반환합니다."""
import json
from news.report import validate_report_row


def prepared_articles(store, prepared_db):
    result=[]
    for row in store.db.execute('SELECT * FROM articles ORDER BY url'):
        a=validate_report_row(row)
        ready=prepared_db.execute('SELECT eligible,payload FROM prepared_article WHERE source_code=? AND external_id=?',(a['source_code'],a['source_article_id'])).fetchone()
        if ready is None or not ready[0]:raise ValueError('서비스 품질·주제 검증이 없는 기사')
        payload=json.loads(ready[1])
        if payload['source_url']!=a['source_url'] or payload['body_masked']!=a.get('body_masked'):
            raise ValueError('준비 결과와 저장된 서비스 본문 불일치')
        result.append(payload)
    return result
