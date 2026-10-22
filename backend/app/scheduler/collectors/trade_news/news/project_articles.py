"""프로젝트 적재 대상 선택. 일일 적재는 날짜·출처를 먼저 한정합니다."""
import json
from news.dates import publication_date
from news.report import validate_report_row
from news.article_schema import article_values


def select_prepared_articles(store, prepared_db, target_date=None, sites=None):
    target = publication_date(str(target_date)) if target_date is not None else None
    selected, rejected, unscoped = [], [], []
    for row in store.db.execute('SELECT * FROM articles ORDER BY url'):
        if sites is not None and row['site'] not in sites:
            continue
        identity = {'url': row['url'], 'source_code': row['site'], 'external_id': row['article_id']}
        # 다른 날짜의 오래된 본문·검수 오류가 이번 적재를 막지 않도록 범위를 먼저 선택합니다.
        if target is not None:
            try:
                day = publication_date(json.loads(row['payload'])['published_at'])
            except (ValueError, TypeError, KeyError):
                unscoped.append({**identity, 'reason': '게시 날짜를 해석할 수 없어 적재 범위 확인 불가'})
                continue
            if day != target:
                continue
        try:
            a = validate_report_row(row)
            ready = prepared_db.execute(
                'SELECT eligible,payload FROM prepared_article WHERE source_code=? AND external_id=?',
                (a['source_code'], a['source_article_id'])).fetchone()
            if ready is None or not ready[0]:
                raise ValueError('서비스 품질·주제 검증이 없는 기사')
            payload = json.loads(ready[1])
            expected = (a['source_code'], a['source_article_id'], a['title'],
                        publication_date(a['published_at']).isoformat(), a['source_url'], a.get('body_masked'))
            actual = tuple(payload[k] for k in ('source_code','external_id','title','published_date','source_url','body_masked'))
            if not payload['eligible'] or actual != expected:
                raise ValueError('준비 결과와 저장된 서비스 기사 불일치')
            # INSERT와 같은 ERD 검증을 먼저 수행해 잘못된 기사만 분리합니다.
            # 기존 본문·제목을 자르거나 수정하지 않습니다.
            article_values(payload)
            selected.append(payload)
        except (ValueError, TypeError, KeyError) as exc:
            rejected.append({**identity, 'reason': str(exc)})
    return {'articles': selected, 'rejected': rejected, 'unscoped_errors': unscoped}


def prepared_articles(store, prepared_db, target_date=None, sites=None):
    """기존 호출자는 잘못된 적재 대상이 있으면 전체 계획을 중단합니다."""
    result = select_prepared_articles(store, prepared_db, target_date, sites)
    if result['rejected'] or result['unscoped_errors']:
        raise ValueError('적재 대상 검증 실패')
    return result['articles']
