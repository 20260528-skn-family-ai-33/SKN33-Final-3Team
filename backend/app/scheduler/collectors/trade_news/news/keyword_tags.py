"""DB 대표어를 유지하며 문법 요소·부분 일치를 제외하고 자동 키워드 최대 5개를 선택합니다.

기사 수집·무역 검수나 공통 키워드 사전의 분류 상태는 변경하지 않습니다.
본문 등장 횟수 → 제목 근거 → 구체적인 표현 길이 → 첫 등장 순으로 선택합니다.
이 순위는 규칙 기반 근거이며 의미 이해 모델의 주제 판정은 아닙니다.
"""
from functools import lru_cache
import re

MAX_KEYWORDS = 5

# 단어 중간에 있는 한글·영문·숫자·밑줄을 별도 키워드로 해석하지 않습니다.
_WORD = r'[가-힣ㄱ-ㅎㅏ-ㅣA-Za-z0-9_一-龥]'
_PARTICLES = (
    '으로부터', '에게서', '한테서', '으로서', '으로써', '로부터', '로서', '로써',
    '께서', '이야말로', '야말로', '이라도', '이든', '이나', '라고', '으로',
    '에서', '에게', '에겐', '한테', '부터', '까지', '처럼', '보다', '만큼',
    '조차', '마저', '밖에', '라도', '하고', '이랑', '랑', '든',
    '은', '는', '이', '가', '을', '를', '의', '에', '로', '와', '과', '도', '만', '께', '엔',
)
GRAMMATICAL_TERMS = frozenset(_PARTICLES) | {
    '및', '또는', '혹은', '그리고', '그러나', '하지만', '또한', '또',
    '따라서', '그러므로', '그래서', '즉', '등', '등등', '한',
}
def _alternatives(words):
    return '(?:' + '|'.join(map(re.escape, sorted(words, key=len, reverse=True))) + ')'


# 격조사·부사격 표현 뒤에 보조사가 붙는 순서를 검사합니다.
# '철+도+를/와'는 잘못된 연결이므로 '철도를/철도와'에서 철을 떼지 않습니다.
_CASE = _alternatives(('에게서', '한테서', '으로서', '으로써', '로서', '로써', '께서',
                       '으로', '에서', '에게', '에겐', '한테', '하고', '이랑', '랑',
                       '이', '가', '을', '를', '의', '에', '로', '와', '과', '께', '엔', '이나', '라고', '이든', '든'))
_EXTENSION = _alternatives(('부터', '까지', '처럼', '보다', '만큼', '조차', '마저',
                            '밖에', '이라도', '라도', '이야말로', '야말로'))
_AUX = r'(?:도|만|은|는)'
_PARTICLE = (r'(?:만?' + _CASE + _EXTENSION + r'*' + _AUX + r'*|'
             + _EXTENSION + r'+' + _AUX + r'*|' + _AUX + r'+)')

# 하다/되다 어간, 시제·의지의 '겠', 연결·인용 어미를 조합합니다.
# 하+겠+다고/다는, 하+고자, 했+으면 등을 완성된 단어별로 열거하지 않습니다.
_ENDING = r'(?:다(?:가|고|는|던|면|지만)?|고(?:자)?|으?며|으?면(?:서)?|는(?:데|지)?|을|지만|거든|도록|으?므로|으?니(?:까)?|려(?:고|면)?|습니다|어요)'
_FINITE_ENDING = r'(?:겠)?' + _ENDING
# 붙여 쓴 보조동사 '오다'도 인식합니다. 해+왔+다/고, 되어+오+겠+다는 등입니다.
_COMING = r'(?:오' + _FINITE_ENDING + r'|왔' + _FINITE_ENDING + r'|온(?:다(?:고|는|면|지만)?)?|올)'
_EO_SUFFIX = r'(?:서|야|' + _COMING + r')?'
_PREDICATE = (r'(?:(?:하|되)기(?:' + _PARTICLE + r')?|하(?:' + _FINITE_ENDING
              + r'|여' + _EO_SUFFIX + r'|였' + _FINITE_ENDING + r')|(?:해|돼)' + _EO_SUFFIX
              + r'|(?:했|됐)' + _FINITE_ENDING + r'|되(?:' + _FINITE_ENDING + r'|어' + _EO_SUFFIX
              + r'|었' + _FINITE_ENDING + r')|(?:한|된)(?:다(?:고|는|면|지만)?)?|할|될)' + _AUX + r'*')

# 서술격 조사 '이다'는 한 글자 품목에도 허용합니다. 금+이다와 금+했다를 구분합니다.
# '금일/철인' 같은 일반 단어를 금/철로 나누지 않도록 축약형 '일/인'은 추정하지 않습니다.
_COPULA = (r'(?:이(?:다(?:고|는|면|지만)?|고|며|면(?:서)?|지만|니(?:까)?|라(?:고|는|면|서|도|며)'
           + r'|었' + _FINITE_ENDING + r'|겠' + _ENDING + r'|기(?:' + _PARTICLE + r')?|에요)|입니다)'
           + _AUX + r'*')


@lru_cache(maxsize=32768)
def _pattern(term):
    # '금하다'의 금처럼 한 글자 뒤의 동사를 품목의 활용으로 추정하지 않습니다.
    suffixes = [_PARTICLE, _COPULA]
    if len(term) > 1:
        suffixes.append(_PREDICATE)
    suffix = '(?:' + '|'.join(suffixes) + ')'
    return re.compile(r'(?<!' + _WORD + r')(?P<term>' + re.escape(term)
                      + r')(?:' + suffix + r')?(?!' + _WORD + r')', re.I)


def _mentions(hits):
    """동의어가 같은 위치를 겹쳐 가리켜도 한 번의 언급으로 셉니다."""
    count, previous_segment, previous_end = 0, None, -1
    for segment, start, end in sorted(set(hits)):
        if segment != previous_segment or start >= previous_end:
            count += 1
        previous_end = max(previous_end, end) if segment == previous_segment else end
        previous_segment = segment
    return count


def select_keyword_tags(segments, keywords):
    """segments[0]은 제목, 나머지는 본문 전체의 문장입니다. 국가·HS 태그는 다루지 않습니다."""
    matches = {}
    for item in keywords:
        term, canonical = item['term'], item['canonical']
        # DB 행을 삭제하거나 상태로 제한하지 않고, 자동 태깅의 문법 오탐만 막습니다.
        if term in GRAMMATICAL_TERMS or canonical in GRAMMATICAL_TERMS:
            continue
        hits = [(index, match.start('term'), match.end('term'), term)
                for index, sentence in enumerate(segments)
                for match in _pattern(term).finditer(sentence)]
        if hits:
            matches.setdefault(canonical, []).extend(hits)

    # 같은 어절의 더 긴 DB 단어가 인식되면 그 일부인 한 글자를 중복 생성하지 않습니다.
    # '철도는'의 철은 제외하지만 별도 문장 '철을 수출했다'의 철은 유지합니다.
    lexical_ends = {}
    for hits in matches.values():
        for index, start, end, term in hits:
            if len(term) > 1 and re.fullmatch(_WORD + '+', term):
                key = index, start
                lexical_ends[key] = max(lexical_ends.get(key, end), end)

    ranked = []
    for canonical, hits in matches.items():
        hits = [h for h in hits if len(h[3]) != 1 or lexical_ends.get(h[:2], h[2]) <= h[2]]
        if not hits:
            continue
        body_hits = [(index, start, end) for index, start, end, _ in hits if index > 0]
        title_hit = any(index == 0 for index, *_ in hits)
        # 근거는 본문을 우선하며 같은 위치의 동의어는 긴 표현부터 고릅니다.
        evidence = min(hits, key=lambda h: (h[0] == 0, h[0], h[1], -len(h[3]), h[3]))
        index, start, _, term = evidence
        score = (-_mentions(body_hits), -int(title_hit), -max(len(h[3]) for h in hits),
                 index, start, canonical)
        ranked.append((score, {'kind': 'keyword', 'keyword_text': canonical, 'term': term,
                               'evidence': segments[index], 'method': 'project_canonical_dictionary'}))
    return [tag for _, tag in sorted(ranked, key=lambda row: row[0])[:MAX_KEYWORDS]]
