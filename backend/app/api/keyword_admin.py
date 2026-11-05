"""키워드 사전 편집. 검수 상태·수동 출처는 같은 트랜잭션의 감사 이력에 보존한다."""
from sqlalchemy import select, update, func, case
from backend.app.api.common import require, digest, now, page_info
from backend.app.api.admin import audit
from backend.app.services.news_keywords.category_rules import TAX
from backend.app.services.news_schema import freeze_keyword_categories


def exact_target(store, column):
    # 운영 인덱스와 같은 collation. 이전 스키마에서도 사전 키의 대소문자를 보존한다.
    return column.collate('utf8mb4_bin' if store.c.dialect.name == 'mysql' else 'BINARY')


def latest_reviews(store):
    a = store.t('api_audit_event')
    target = exact_target(store, a.c.target_id)
    ranked = (select(target.label('target_id'), a.c.after_json['review_status'].as_string().label('review_status'),
                     func.row_number().over(partition_by=target, order_by=(a.c.created_at.desc(), a.c.audit_id.desc())).label('position'))
              .where(a.c.target_type == 'KEYWORD', a.c.action.in_(['KEYWORD_ASSIGN', 'KEYWORD_HOLD', 'KEYWORD_RELEASE']))
              .subquery('ranked_reviews'))
    return select(ranked.c.target_id, ranked.c.review_status).where(ranked.c.position == 1).subquery('latest_reviews')


def topic_view(row):
    value = {k: row[k] for k in ('keyword_text', 'canonical_text', 'category_id', 'status', 'review_status')}
    return {**row, 'version': digest(value)}


def listing(store, query, page=1, size=20):
    k, c, t = store.t('keyword_topic'), store.t('keyword_category'), store.t('article_tag')
    latest = latest_reviews(store)
    review = func.coalesce(latest.c.review_status, case((k.c.status == 'CLASSIFIED', 'REVIEWED'), else_='UNREVIEWED'))
    base = k.outerjoin(latest, exact_target(store, latest.c.target_id) == k.c.keyword_text)
    counts = (select(t.c.keyword_text, func.count(func.distinct(t.c.article_id)).label('article_count'))
              .where(t.c.keyword_text.is_not(None)).group_by(t.c.keyword_text).subquery('article_counts'))
    count = func.coalesce(counts.c.article_count, 0)
    filters = []
    if query.get('status'):
        require(query['status'] in ('PENDING', 'UNCLASSIFIED', 'CLASSIFIED', 'SYNONYM'), '키워드 상태를 확인하세요.')
        filters.append(k.c.status == query['status'])
    else:
        filters.append(k.c.status != 'SYNONYM')
    if query.get('review_status'):
        require(query['review_status'] in ('UNREVIEWED', 'HELD', 'REVIEWED'), '검수 상태를 확인하세요.')
        filters.append(review == query['review_status'])
    if query.get('q'): filters.append(k.c.keyword_text.contains(query['q'].strip(), autoescape=True))
    require(not query.get('candidate_category_id'), '후보 분야는 저장되지 않습니다. 확정된 분야를 상세에서 확인하세요.')
    sort = query.get('sort', 'ARTICLE_COUNT_DESC')
    require(sort in ('ARTICLE_COUNT_DESC', 'KEYWORD_ASC', 'UPDATED_DESC'), '정렬 기준을 확인하세요.')
    order = {'ARTICLE_COUNT_DESC': count.desc(), 'KEYWORD_ASC': k.c.keyword_text.asc(), 'UPDATED_DESC': k.c.updated_at.desc()}[sort]
    stmt = (select(k, c.c.major_name, c.c.minor_name, review.label('review_status'), count.label('article_count'))
            .select_from(base.outerjoin(c, k.c.category_id == c.c.category_id).outerjoin(counts, counts.c.keyword_text == k.c.keyword_text))
            .where(*filters))
    rows = store.rows(stmt.order_by(order, k.c.keyword_text).offset((page-1)*size).limit(size))
    # 총 건수는 기사 집계·분야 조인 없이 센다. 감사 이력은 행별 상관 조회 대신 쿼리당 한 번 순위를 계산한다.
    total = store.count(select(k.c.keyword_text).select_from(base).where(*filters))
    return {'items': [topic_view(r) for r in rows], 'page': page_info(page, size, total)}


def topic_row(store, word, lock=False):
    k = store.t('keyword_topic')
    stmt = select(k).where(k.c.keyword_text == word)
    row = store.one(stmt.with_for_update() if lock else stmt)
    require(row is not None, '키워드를 찾을 수 없습니다.', 'NOT_FOUND', 404)
    # 잠금 조회는 MySQL REPEATABLE READ에서도 앞선 변경의 최신 이력을 읽는다.
    a = store.t('api_audit_event')
    # 감사 테이블 기본 collation과 관계없이 사전의 대소문자 구분 키를 그대로 비교한다.
    history = (select(a.c.after_json)
               .where(a.c.target_type == 'KEYWORD', exact_target(store, a.c.target_id) == word,
                      a.c.action.in_(['KEYWORD_ASSIGN', 'KEYWORD_HOLD', 'KEYWORD_RELEASE']))
               .order_by(a.c.created_at.desc(), a.c.audit_id.desc()).limit(1))
    latest = store.c.scalar(history.with_for_update() if lock else history) or {}
    row['review_status'] = latest.get('review_status', 'REVIEWED' if row['status'] == 'CLASSIFIED' else 'UNREVIEWED')
    return topic_view(row)


def detail(store, word, article_limit=3):
    require(article_limit in (3, 10), '기사 근거는 3건 또는 10건입니다.')
    row = topic_row(store, word)
    a, t = store.t('article'), store.t('article_tag')
    ids = select(t.c.article_id).where(t.c.keyword_text == word)
    stmt = select(a.c.article_id, a.c.title, a.c.published_date, a.c.source_code).where(a.c.article_id.in_(ids))
    return {'keyword': row, 'articles': store.rows(stmt.order_by(a.c.published_date.desc(), a.c.article_id.desc()).limit(article_limit)),
            'article_count': store.count(stmt), 'apply_scopes': ['NEW_ONLY']}


def confirm(data, version=None):
    require(data.get('confirmed') is True and 0 < len(data.get('reason', '').strip()) <= 1000, '확인과 1~1000자의 변경 사유가 필요합니다.')
    if version is not None:
        require(data.get('expected_version') == version, '다른 관리자가 변경했습니다.', 'VERSION_CONFLICT', 409)


def review(store, actor, data):
    before = topic_row(store, data['keyword_text'], lock=True)
    confirm(data, before['version'])
    require(before['status'] != 'SYNONYM', '동의어는 대표 키워드에서 분류해 주세요.')
    action = data['action']
    require(action in ('ASSIGN', 'HOLD', 'RELEASE'), '처리 종류를 확인하세요.')
    require(data['apply_scope'] == 'NEW_ONLY', '새 기사부터 적용만 지원합니다.', 'UNSUPPORTED_SCOPE', 422)
    patch = {}
    if action == 'ASSIGN':
        category = category_row(store, data['category_id'], lock=True)
        patch = {'category_id': category['category_id'], 'status': 'CLASSIFIED'}
        state = 'REVIEWED'
    else:
        require(data['category_id'] is None, '보류·해제 시 분야는 null이어야 합니다.')
        if action == 'HOLD':
            require(before['status'] != 'CLASSIFIED', '분류된 키워드는 지정 해제 후 보류하세요.')
            state = 'HELD'
        else:
            patch = {'category_id': None, 'status': 'PENDING'}
            state = 'UNREVIEWED'
    k = store.t('keyword_topic')
    # 최초 변경 전에 현재 표시값을 보존한다. 신규 적재기는 최초 수신 정책으로 같은 JSON을 고정한다.
    tags = store.t('article_tag')
    ids = store.c.execute(select(tags.c.article_id).where(tags.c.keyword_text == before['keyword_text']).distinct()).scalars().all()
    freeze_keyword_categories(store.c, store.t('article'), ids)
    store.c.execute(update(k).where(k.c.keyword_text == before['keyword_text']).values(**patch, updated_at=now(), synced_at=None))
    keys = ('keyword_text', 'canonical_text', 'category_id', 'status', 'review_status')
    old = {key: before[key] for key in keys}
    after = {**old, **patch, 'review_status': state, 'apply_scope': 'NEW_ONLY'}
    if action == 'ASSIGN': after.update(major_name=category['major_name'], minor_name=category['minor_name'])
    return audit(store, actor['user_id'], 'KEYWORD_'+action, 'KEYWORD', before['keyword_text'], old, after, data['reason'])


def category_row(store, identity, lock=False):
    t = store.t('keyword_category')
    stmt = select(t).where(t.c.category_id == identity)
    row = store.one(stmt.with_for_update() if lock else stmt)
    require(row is not None, '분야를 찾을 수 없습니다.', 'NOT_FOUND', 404)
    a = store.t('api_audit_event')
    created = store.c.scalar(select(a.c.audit_id).where(a.c.target_type == 'CATEGORY', a.c.target_id == str(identity), a.c.action == 'CATEGORY_CREATE').limit(1))
    value = {key: row[key] for key in ('category_id', 'major_name', 'minor_name')}
    base = next((terms.split() for major, minor, terms in TAX if (major, minor) == (row['major_name'], row['minor_name'])), None)
    return {**value, 'version': digest(value), 'editable': bool(created) and base is None, 'representative_terms': base or []}


def categories(store):
    t = store.t('keyword_category')
    rows = store.rows(select(t.c.category_id).order_by(t.c.category_id))
    return {'items': [category_row(store, r['category_id']) for r in rows], 'base_majors': list(dict.fromkeys(major for major, _, _ in TAX))}


def category_change(store, actor, data, identity=None, removing=False):
    require(not removing, '분야 삭제·이관은 제공하지 않습니다.', 'FEATURE_NOT_AVAILABLE', 422)
    before = category_row(store, identity, lock=True) if identity is not None else None
    confirm(data, before['version'] if before else None)
    if before: require(before['editable'], '기본 분야 또는 출처 미확인 분야는 변경할 수 없습니다.', 'FORBIDDEN', 403)
    t = store.t('keyword_category')
    major, minor = data['major_name'].strip(), data['minor_name'].strip()
    require(0 < len(major) <= 40 and 0 < len(minor) <= 60, '대분류는 40자, 중분류는 60자 이내입니다.')
    require((major, minor) not in {(a, b) for a, b, _ in TAX}, '기본 분류 이름은 관리자 분야로 사용할 수 없습니다.')
    duplicate = select(t.c.category_id).where(t.c.major_name == major, t.c.minor_name == minor)
    if before: duplicate = duplicate.where(t.c.category_id != identity)
    require(store.c.scalar(duplicate) is None, '같은 분야가 이미 있습니다.', 'CATEGORY_EXISTS', 409)
    values = {'major_name': major, 'minor_name': minor, 'updated_at': now(), 'synced_at': None}
    if before: store.c.execute(update(t).where(t.c.category_id == identity).values(**values))
    else: identity = store.insert('keyword_category', **values)
    after, action = {'major_name': major, 'minor_name': minor}, 'CATEGORY_RENAME' if before else 'CATEGORY_CREATE'
    previous = {key: before[key] for key in ('major_name', 'minor_name')} if before else {}
    return {**audit(store, actor['user_id'], action, 'CATEGORY', identity, previous, after, data['reason']), 'category_id': identity}
