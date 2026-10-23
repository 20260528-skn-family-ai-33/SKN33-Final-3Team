"""검증된 프로젝트 사전과 원문 근거로 기사 태그를 만듭니다."""
import re

VERSION = 'tags-v1'
KEYWORDS = ('수출', '수입', '관세', '수출통제', '수출 규제', '반덤핑', '자유무역협정',
            'FTA', 'CBAM', 'PPWR', '원산지', '무역수지', '통관', '화장품', '반도체',
            '철강', '자동차', '의약품', '배터리', '농산물')


def match_term(term, text):
    # 영문 약어가 다른 영단어 안에 포함되면 태그로 만들지 않습니다.
    pattern = re.escape(term)
    if term.isascii():
        pattern = r'(?<![A-Za-z0-9])' + pattern + r'(?![A-Za-z0-9])'
    return re.search(pattern, text, re.I)


def extract_tags(title, body, countries=(), products=()):
    """사전 행: 국가 code/name/aliases, 품목 code/version/name/aliases.

    국가 코드는 직접 생성하지 않습니다. 국가 언급은 교역 상대국 확정과 다릅니다.
    품목 별칭이 여러 코드에 대응하면 HS 태그를 만들지 않고 이유를 남깁니다.
    """
    segments = [title, *[s.strip() for s in re.split(r'(?<=[.!?。])\s+|\n', body) if s.strip()]]
    tags, unresolved = [], []
    for row in countries:
        code = row['code']
        if not code or not row['name'] or row.get('role', 'COUNTRY') != 'COUNTRY':
            continue
        aliases = set([row['name'], *row.get('aliases', [])])
        # 한 글자 한자 약칭과 조사 같은 짧은 이름은 오탐 위험이 있어 자동 사용하지 않습니다.
        matches = [(s, alias) for s in segments for alias in sorted(aliases) if len(alias) >= 2 and match_term(alias, s)]
        if matches:
            sentence, alias = matches[0]
            tags.append({'kind': 'country', 'country_code': code, 'role': 'mentioned',
                         'term': alias, 'evidence': sentence, 'method': 'dictionary_alias'})
    product_matches = {}
    for row in products:
        code, version = row['code'], row['version']
        if not re.fullmatch(r'(?:\d{4}|\d{6}|\d{10})', code) or not re.fullmatch(r'[A-Z0-9_]+', version):
            raise ValueError('HS 사전 코드·판본 형식 오류')
        # 품목명 자동 부분 일치는 하지 않습니다. 팀이 명시한 별칭만 사용합니다.
        for alias in row.get('aliases', []):
            if len(alias) < 2:
                continue
            sentence = next((s for s in segments if match_term(alias, s)), None)
            if sentence:
                product_matches.setdefault(alias, []).append((row, sentence))
        explicit = next((s for s in segments if re.search(r'\bHS\s*(?:코드\s*)?[:：]?\s*' + re.escape(code) + r'(?!\d)', s, re.I)), None)
        if explicit:
            product_matches.setdefault('HS ' + code, []).append((row, explicit))
    emitted = set()
    for term, matches in sorted(product_matches.items()):
        identities = {(r['code'], r['version']) for r, _ in matches}
        if len(identities) != 1:
            unresolved.append({'term': term, 'reason': 'ambiguous_hs_mapping', 'matches': sorted(identities)})
            continue
        row, sentence = matches[0]
        key = row['code'], row['version']
        if key not in emitted:
            tags.append({'kind': 'hs', 'hs_code': key[0], 'hs_ver': key[1],
                         'term': term, 'evidence': sentence, 'method': 'verified_dictionary'})
            emitted.add(key)
    for term in KEYWORDS:
        sentence = next((s for s in segments if match_term(term, s.replace('관세청', '') if term == '관세' else s)), None)
        if sentence:
            tags.append({'kind': 'keyword', 'keyword_text': term, 'term': term,
                         'evidence': sentence, 'method': 'literal'})
    return {'version': VERSION, 'tags': tags, 'unresolved': unresolved,
            'dictionary_status': {'countries_loaded': len(countries), 'products_loaded': len(products)},
            'limitation': '국가 태그는 언급 근거이며 수출 대상·정책 시행 주체를 확정하지 않음. 사전 미제공 시 해당 코드 태그 없음.'}
