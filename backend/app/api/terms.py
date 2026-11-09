"""약관 본문 조회와 회원가입 동의. 본문·버전은 service_option에 있고 동의 이력은 user_consent에 남긴다."""
from datetime import datetime, timezone
from sqlalchemy import select
from backend.app.api.common import now, require, unavailable


OPTION_TYPES = {'NOTIFICATION': 'NOTIFICATION_TERMS', 'SERVICE': 'SERVICE_TERMS',
                'PRIVACY': 'PRIVACY_TERMS', 'PRIVACY_POLICY': 'PRIVACY_POLICY'}
TERMS_TYPES = {option_type: terms_type for terms_type, option_type in OPTION_TYPES.items()}


def active_terms(store, option_type='NOTIFICATION_TERMS'):
    t = store.t('service_option')
    rows = store.rows(select(t).where(t.c.option_type == option_type, t.c.enabled == True)
                      .order_by(t.c.sort_order.desc(), t.c.option_code.desc()))
    effective = []
    for row in rows:
        value = (row['value_json'] or {}).get('effective_at')
        if value:
            try:
                parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
                timestamp = parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc).replace(tzinfo=None)
            except (ValueError, TypeError):
                unavailable('약관 적용 시각 설정을 확인하세요.', 'FEATURE_NOT_READY')
            if timestamp > now(): continue
        effective.append(row)
    return effective


def current(store, terms_type='NOTIFICATION'):
    kind = terms_type.upper().replace('-', '_')
    require(kind in OPTION_TYPES, '지원하지 않는 약관입니다.', 'UNSUPPORTED_TERMS_TYPE', 422)
    rows = active_terms(store, OPTION_TYPES[kind])
    if not rows: unavailable('승인된 활성 약관이 아직 등록되지 않았습니다.', 'FEATURE_NOT_READY')
    row = rows[0]; data = row['value_json']
    require(isinstance(data.get('body'), str) and bool(data['body'].strip()), '약관 본문이 준비되지 않았습니다.', 'FEATURE_NOT_READY', 503)
    return {'terms_type': kind, 'version': row['option_code'], 'title': row['label_ko'], 'body': data['body'],
            'required': kind != 'PRIVACY_POLICY'}


def notification_versions(store):
    """user_consent에는 가입 약관도 함께 있으므로 알림 동의 판단은 알림 약관 버전으로 좁힌다."""
    t = store.t('service_option')
    return select(t.c.option_code).where(t.c.option_type == 'NOTIFICATION_TERMS')


def signup_versions(store, data):
    """가입 필수 동의를 검증하고 동의한 약관 버전을 돌려준다. 사용자를 만들기 전에 호출한다."""
    require(data.age_confirmed, '만 14세 이상만 가입할 수 있습니다.', 'AGE_CONFIRMATION_REQUIRED', 422)
    versions = []
    for kind, version in (('SERVICE', data.service_terms_version), ('PRIVACY', data.privacy_terms_version)):
        expected = current(store, kind)['version']
        require(expected == version, '약관이 변경되었습니다. 다시 확인하고 동의해 주세요.', 'TERMS_VERSION_CHANGED', 409)
        versions.append(expected)
    return versions


def record_signup(store, uid, versions):
    at = now()
    for version in versions:
        store.insert('user_consent', user_id=uid, terms_version=version, action='AGREE', acted_at=at)


def signup_consents(store, uid):
    """내 계정 화면에 보여 줄 가입 약관 동의 기록(최신순). 종류마다 첫 항목이 가장 최근 동의다. 약관 도입 전 가입자는 비어 있다."""
    c, o = store.t('user_consent'), store.t('service_option')
    rows = store.rows(select(c.c.terms_version, c.c.acted_at, o.c.option_type).join(o, o.c.option_code == c.c.terms_version)
                      .where(c.c.user_id == uid, c.c.action == 'AGREE', o.c.option_type.in_(['SERVICE_TERMS', 'PRIVACY_TERMS']))
                      .order_by(c.c.acted_at.desc(), c.c.consent_id.desc()))
    return [{'terms_type': TERMS_TYPES[r['option_type']], 'terms_version': r['terms_version'], 'acted_at': r['acted_at']} for r in rows]
