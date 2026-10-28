"""기사 판정을 바꾸지 않고, 적재 대상 DB의 사전으로 검증된 기사 태그만 생성합니다."""
from sqlalchemy import text
from news.dictionary import validate_dictionary, dictionary_fingerprint
from news.tagging import extract_tags


def read_project_dictionary(connection):
    """조회 트랜잭션을 끝낸 뒤 반환해 기사 INSERT의 begin과 충돌하지 않습니다."""
    from backend.app.services.news_keywords.rule_data import GEO_DICT
    if connection.in_transaction():
        raise ValueError('태그 사전 조회에는 새 트랜잭션이 필요합니다')
    aliases = {}
    for name, kind, code, canonical, split in GEO_DICT:
        if kind == '국가' and len(name) >= 2:
            aliases.setdefault(code, set()).add(name)
    with connection.begin():
        countries = [dict(code=row.country_code, name=row.name_ko, role=row.country_role,
                          aliases=sorted(aliases.get(row.country_code, set()) - {row.name_ko}))
                     for row in connection.execute(text('SELECT country_code,name_ko,country_role FROM country ORDER BY country_code'))]
        # 기존 태깅기는 HS2를 지원하지 않습니다. 품목명으로 코드를 추정하거나 판본을 임의 선택하지 않습니다.
        products = [dict(code=row.hs_code, version=row.hs_ver, name=row.name_ko)
                    for row in connection.execute(text('SELECT hs_code,hs_ver,name_ko FROM hs_code ORDER BY hs_code,hs_ver'))
                    if len(row.hs_code) in (4, 6, 10)]
        rows = {r.keyword_text: (r.status, r.canonical_text) for r in connection.execute(
            text('SELECT keyword_text,status,canonical_text FROM keyword_topic ORDER BY keyword_text'))}
        keywords = []
        for term in rows:
            target, visited = term, set()
            while target in rows and rows[target][0] == 'SYNONYM':
                if target in visited:
                    raise ValueError('keyword_canonical_cycle')
                visited.add(target)
                target = rows[target][1]
            if target not in rows:
                raise ValueError('keyword_canonical_missing')
            keywords.append({'term': term, 'canonical': target})
    return validate_dictionary({'countries': countries, 'products': products, 'keywords': keywords})


def dictionary_summary(dictionary):
    return {'source': 'project_database', 'dictionary_sha256': dictionary_fingerprint(dictionary),
            'countries_loaded': len(dictionary['countries']), 'products_loaded': len(dictionary['products']), 'keywords_loaded': len(dictionary.get('keywords', [])),
            'warnings': [kind + '_dictionary_empty' for kind in ('countries', 'products', 'keywords') if not dictionary.get(kind)]}


def tag_for_load(article, dictionary):
    """검수 결과·본문은 보존하고 적재용 복사본의 태그만 갱신합니다."""
    from backend.app.services.news_keywords.rule_data import GEO_DICT
    tags = extract_tags(article['title'], article['body_masked'], dictionary['countries'], dictionary['products'],
                        country_rules=GEO_DICT, keywords=dictionary.get('keywords', []),
                        published_date=article['published_date'])
    tags.update(dictionary_sha256=dictionary_fingerprint(dictionary), generation='project-tags-v7')
    return {**article, 'tagging': tags}
