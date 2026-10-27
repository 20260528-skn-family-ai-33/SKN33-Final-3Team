"""검증된 프로젝트 사전과 원문 근거로 기사 태그를 만듭니다."""
import re

VERSION = 'tags-v3'


def match_term(term, text):
    # 영문 약어가 다른 영단어 안에 포함되면 태그로 만들지 않습니다.
    pattern = re.escape(term)
    if term.isascii():
        pattern = r'(?<![A-Za-z0-9])' + pattern + r'(?![A-Za-z0-9])'
    return re.search(pattern, text, re.I)


# 조사 단위를 조합해 전체 어절의 끝까지 검사합니다. '로+부터+도', '만+으로+는' 등을
# 개별 문구로 열거하지 않습니다. 남은 글자가 있으면 피지컬AI·피지산업처럼 차단합니다.
_WORD_CHAR = r'[가-힣A-Za-z0-9]'
_JOSA = r'(?:에게서|한테서|으로|에서|에게|한테|부터|까지|처럼|보다|조차|마저|밖에|이라도|라도|은|는|이|가|을|를|의|에|로|와|과|도|만|서)'
_COUNTRY_PARTICLE = re.compile(r'(?:산|발|행)?' + _JOSA + r'*(?!' + _WORD_CHAR + r')')

# 국명과 같은 표기를 쓰는 일반 명사는 해당 등장 위치의 앞뒤 근거로만 구별합니다.
# 문장 전체를 배제하지 않아 '수단 정부의 협상 수단', '인도에서 물품 인도'를 함께 처리합니다.
_METHOD_BEFORE = re.compile(r'(?:결제|지급|운송|교통|협상|압박|보복|제재|해결|생계|통신|운반|통제|정책)(?:의)?\s*$')
_METHOD_AFTER = re.compile(r'^(?:으로|을|도)?\s*(?:활용|사용|삼|이용|동원|다양화)')
_DELIVERY_BEFORE = re.compile(r'(?:물품|상품|제품|화물|선박|항공기|차량|장비|물량|범죄인|신병)(?:의)?\s*$')
_DELIVERY_VERB = re.compile(
    r'^(?:했(?:다|으며|고)|하(?:다|고|는|며|여)|한(?:다|다고)|해(?:서|야)?|받(?:다|았다|는|고)|되(?:다|었다|는)|됐(?:다|고))'
    r'(?!' + _WORD_CHAR + r')')
_DELIVERY_AFTER = re.compile(r'^(?:를)?\s*(?:완료|마쳤|마치|지연|예정)')
_GEO_AFTER = re.compile(r'^(?:에서|으로|로)|^\s*(?:정부|당국|시장|기업|수도|대사관)')
_MONEY_AFTER = re.compile(r'^\s*(?:원|달러|유로|엔)' + _JOSA + r'*(?:이다|이었다|다)?(?!' + _WORD_CHAR + r')')


def common_word_usage(name, before, after):
    """국명 자체를 금지하지 않고 일반 명사·동사·금액인 용례만 제외합니다."""
    if name == '수단':
        return bool(_METHOD_BEFORE.search(before) or _METHOD_AFTER.match(after))
    if name == '인도':
        if _GEO_AFTER.match(after):
            return False
        return bool(_DELIVERY_BEFORE.search(before) or _DELIVERY_VERB.match(after)
                    or _DELIVERY_AFTER.match(after))
    if name == '오만':
        return bool(_MONEY_AFTER.match(after))
    return False


def country_evidence(segments, countries, country_rules):
    """국가명은 긴 지명을 우선하며, 공통 사전의 단독 표현 제한을 유지합니다.

    공통 사전에만 있는 긴 지명도 중첩 판단에 사용하지만 DB에 없는 코드를 만들지는 않습니다.
    '수단'의 일반 명사 용례는 해당 등장 위치만 배제해 같은 문장의 실제 국가 언급은 보존합니다.
    """
    names = {name for row in countries for name in (row['name'], *row.get('aliases', [])) if len(name) >= 2}
    standalone = set()
    for name, kind, code, canonical, split in country_rules:
        if kind == '국가' and len(name) >= 2:
            names.add(name)
            if split == 'N':
                standalone.add(name)
    patterns = [(name, re.compile(
        (r'(?<![A-Za-z0-9])' + re.escape(name) + r'(?![A-Za-z0-9])')
        if name.isascii() else re.escape(name), re.I)) for name in sorted(names)]
    evidence = {}
    for sentence in segments:
        spans = [(m.start(), m.end(), name) for name, pattern in patterns for m in pattern.finditer(sentence)]
        for start, end, name in spans:
            # 같은 위치의 '인도네시아'를 '인도'로도 중복 해석하지 않습니다.
            if any(a <= start and end <= b and b-a > end-start for a, b, _ in spans):
                continue
            if name in standalone and not name.isascii():
                if start and re.match(r'[가-힣A-Za-z0-9]', sentence[start-1]):
                    # '대수단'의 대(對)는 단어 시작에서만 인정합니다. '거대수단' 등은 허용하지 않습니다.
                    trade_prefix = sentence[start-1] == '대' and (
                        start == 1 or not re.match(r'[가-힣A-Za-z0-9]', sentence[start-2]))
                    if not trade_prefix:
                        continue
                if not _COUNTRY_PARTICLE.match(sentence[end:]):
                    continue
            if common_word_usage(name, sentence[:start], sentence[end:]):
                continue
            evidence.setdefault(name, sentence)
    return evidence


def extract_tags(title, body, countries=(), products=(), *, country_rules=(), keywords=(), published_date=None):
    """사전 행: 국가 code/name/aliases, 품목 code/version/name/aliases.

    국가 코드는 직접 생성하지 않습니다. 국가 언급은 교역 상대국 확정과 다릅니다.
    품목 별칭이 여러 코드에 대응하면 HS 태그를 만들지 않고 이유를 남깁니다.
    """
    segments = [title, *[s.strip() for s in re.split(r'(?<=[.!?。])\s+|\n', body) if s.strip()]]
    tags, unresolved = [], []
    evidence = country_evidence(segments, countries, country_rules)
    for row in countries:
        code = row['code']
        if not code or not row['name'] or row.get('role', 'COUNTRY') != 'COUNTRY':
            continue
        aliases = set([row['name'], *row.get('aliases', [])])
        # 한 글자 한자 약칭과 조사 같은 짧은 이름은 오탐 위험이 있어 자동 사용하지 않습니다.
        matches = [(evidence[alias], alias) for alias in sorted(aliases) if alias in evidence]
        if matches:
            sentence, alias = matches[0]
            tags.append({'kind': 'country', 'country_code': code, 'role': 'mentioned',
                         'term': alias, 'evidence': sentence, 'method': 'dictionary_alias'})
    if published_date is not None:
        from backend.app.scheduler.collectors.hscode.hs_code_build import hs_ver_for
        version = hs_ver_for(published_date)
        products = [r for r in products if len(r['code']) == 6 and r['version'] == version]
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
    keyword_seen = set()
    for item in keywords:
        term, canonical = item['term'], item['canonical']
        sentence = next((s for s in segments if match_term(term, s.replace('관세청', '') if term == '관세' else s)), None)
        if sentence and canonical not in keyword_seen:
            keyword_seen.add(canonical)
            tags.append({'kind': 'keyword', 'keyword_text': canonical, 'term': term,
                         'evidence': sentence, 'method': 'project_canonical_dictionary'})
    return {'version': VERSION, 'tags': tags, 'unresolved': unresolved,
            'dictionary_status': {'countries_loaded': len(countries), 'products_loaded': len(products)},
            'limitation': '국가 태그는 언급 근거이며 수출 대상·정책 시행 주체를 확정하지 않음. 사전 미제공 시 해당 코드 태그 없음.'}
