"""문법·부분 일치 오탐, 정상 표현·대표어, 5개 선택과 독립적인 국가/HS 저장을 검증합니다."""
import copy

import pytest
from sqlalchemy import text

from news.keyword_tags import select_keyword_tags
from news.tagging import extract_tags
from news.project_tags import read_project_dictionary, tag_for_load
from news.mysql_store import MySQLArticleStore
from .helpers import prepared
from .test_daily_scheduler import local


def words(*terms):
    return [{'term': term, 'canonical': term} for term in terms]


def chosen(body, keywords, title=''):
    return [t['keyword_text'] for t in extract_tags(title, body, keywords=keywords)['tags']
            if t['kind'] == 'keyword']


def test_grammar_and_word_fragments_do_not_become_keywords():
    body = '한국 기업의 수출이 늘었다. 소비 및 생산비가 증가했다. 한 기업은 금요일에 소식을 전했다.'
    candidates = words('의', '한', '비', '소', '및', '그리고', '는', '를', '금', '수출')
    assert chosen(body, candidates) == ['수출']


@pytest.mark.parametrize('body,term', [
    ('수출은 증가했다.', '수출'), ('수출만으로는 수익이 부족하다.', '수출'),
    ('관세를 협의한다.', '관세'), ('반도체에서도 변화가 있다.', '반도체'),
    ('농산물로부터도 기회를 찾는다.', '농산물'), ('미국에 자동차를 수출했다.', '수출'),
    ('반도체를 수입하는 기업이다.', '수입'), ('협정이 체결된 뒤 관세가 조정됐다.', '관세'),
    ('쌀을 수입했다.', '쌀'), ('금의 수입량이 증가했다.', '금'),
    ('AI가 수출 관리에 활용된다.', 'AI'), ('FTA에서 정한 조건이다.', 'FTA'),
    ('탄소중립·공급망을 논의했다.', '공급망'),
    ('기업이 반도체를 수출하면 관세가 낮아진다.', '수출'),
    ('기업이 반도체를 수출하면서 공급망을 확대했다.', '수출'),
    ('쌀을 수입해도 관세가 붙는다.', '수입'),
    ('제품을 수출했으면 실적에 반영한다.', '수출'),
    ('쌀을 수입하기에는 비용이 높다.', '수입'),
    ('반도체를 수출한다면 신고해야 한다.', '수출'),
    ('상품을 수출하였다.', '수출'),
    ('상품을 수출했으며 대금을 받았다.', '수출'),
    ('금으로부터도 수익을 얻는다.', '금'),
    ('쌀만으로는 수요를 채울 수 없다.', '쌀'),
    ('철을 수출했다.', '철'),
])
def test_normal_particles_predicates_and_meaningful_single_letters_survive(body, term):
    assert chosen(body, words(term)) == [term]


@pytest.mark.parametrize('body,term', [
    ('기업은 반도체를 수출하겠다고 밝혔다.', '수출'),
    ('기업은 배터리를 수출하겠다는 계획을 발표했다.', '수출'),
    ('기업은 반도체를 수출하고자 한다.', '수출'),
    ('기업은 반도체를 수출해왔다.', '수출'),
    ('주요 수입 품목은 금이다.', '금'),
    ('주요 수입 품목은 쌀이다.', '쌀'),
    ('핵심 분야는 농업이다.', '농업'),
])
def test_seven_reviewed_intention_connection_auxiliary_and_copula_cases(body, term):
    assert chosen(body, words(term)) == [term]


@pytest.mark.parametrize('form', [
    '하겠다', '하겠지만', '하겠다는', '하겠다고', '하겠다면', '하겠습니다',
    '하겠어요', '했겠다고', '하고자', '해왔고', '해왔으며', '해왔으면',
    '해오고', '해오는', '해오면서', '해오겠다는', '해온다', '해온', '해올',
    '하여왔다', '돼왔다', '되어왔다', '돼오겠다는', '되겠다고', '됐겠다고',
])
def test_conjugation_families_share_stem_tense_and_ending_rules(form):
    assert chosen('상품을 수출' + form + '.', words('수출')) == ['수출']


@pytest.mark.parametrize('form', [
    '이다', '이고', '이며', '이면', '이면서', '이지만', '이니까', '이라고',
    '이라는', '이라면', '이라서', '이었다', '이었으며', '이겠다는',
    '입니다', '이에요', '이기도',
])
def test_copula_does_not_enable_verbs_for_single_letter_nouns(form):
    assert chosen('수입 품목은 금' + form + '.', words('금')) == ['금']


@pytest.mark.parametrize('body,term', [
    ('한국의 수출', '한'), ('소비가 늘었다.', '소'), ('생산비가 증가했다.', '비'),
    ('금요일에 발표한다.', '금'), ('관세청의 업무다.', '관세'),
    ('국제무역관계의 변화다.', '무역'), ('OpenAI의 제품이다.', 'AI'),
    ('반도체산업을 다뤘다.', '반도체'), ('AFTA 협정이다.', 'FTA'),
    ('철도를 통해 자동차를 수출했다.', '철'),
    ('철도와 항만을 확충했다.', '철'),
    ('수입을 금했다.', '금'),
    ('금일 발표한 내용이다.', '금'), ('철인이 경기한다.', '철'),
    ('금하는 행위다.', '금'), ('금하겠다고 밝혔다.', '금'),
    ('금하고자 한다.', '금'), ('금해왔다.', '금'),
    ('금이라는단어를 붙여 썼다.', '금'), ('수출하겠다는계획이다.', '수출'),
])
def test_unregistered_compounds_are_not_inferred_from_substrings(body, term):
    assert chosen(body, words(term)) == []


def test_explicit_database_aliases_preserve_compounds_and_canonical_identity():
    candidates = [{'term': '자유무역협정', 'canonical': '무역협정'},
                  {'term': '농산물', 'canonical': '농업'},
                  {'term': '전기차', 'canonical': '전기차'},
                  {'term': 'EV', 'canonical': '전기차'},
                  {'term': '수출액', 'canonical': '수출'}]
    result = chosen('자유무역협정은 농산물을 다룬다. 전기차와 EV의 수출액이 늘었다.', candidates)
    assert set(result) == {'무역협정', '농업', '전기차', '수출'}
    assert result.count('전기차') == 1


def test_longer_database_word_blocks_single_letter_only_at_same_mention():
    candidates = words('철', '철도', '쌀', '금')
    assert chosen('철도는 운송 기반이다.', candidates) == ['철도']
    assert set(chosen('철도는 운송 기반이다. 철을 수출했고 쌀도 수출했다. 금으로 결제했다.', candidates)) == {'철도', '철', '쌀', '금'}
    assert chosen('철도는 운송 기반이다.', list(reversed(candidates))) == ['철도']


def test_grammar_alias_cannot_bypass_canonical_guard():
    assert chosen('의 그리고 AND', [{'term': '의', 'canonical': '의료'},
                                     {'term': 'AND', 'canonical': '그리고'}]) == []


def test_body_frequency_ranks_before_dictionary_order_and_limit_is_five():
    candidates = words(*(f'품목{i}' for i in range(7)))
    body = ' '.join(' '.join([f'품목{i}'] * (i + 1)) for i in range(7))
    result = chosen(body, candidates)
    assert result == ['품목6', '품목5', '품목4', '품목3', '품목2']
    assert chosen(body, list(reversed(candidates))) == result


def test_title_evidence_breaks_equal_body_frequency_ties():
    assert chosen('배터리와 반도체를 수출한다.', words('배터리', '반도체'), title='배터리') == ['배터리', '반도체']


def test_many_candidates_never_expand_automatic_keywords_beyond_five():
    candidates = words(*(f'합성주제{i:03d}' for i in range(150)))
    result = chosen(' '.join(row['term'] for row in candidates), candidates)
    assert len(result) == 5 and len(set(result)) == 5


def test_conjugation_changes_do_not_cap_country_or_hs_tags():
    countries = [{'code': 'C' + str(i), 'name': '시험국' + str(i)} for i in range(7)]
    products = [{'code': '85420' + str(i), 'version': 'HS2022', 'name': '품목' + str(i),
                 'aliases': ['시험품목' + str(i)]} for i in range(7)]
    body = (' '.join(row['name'] for row in countries) + '\n'
            + ' '.join(row['aliases'][0] for row in products) + '\n'
            + ' '.join('합성주제' + str(i) for i in range(150))
            + '\n금이다. 쌀이다. 농업이다. 수출하겠다고 밝혔다. 수출해왔다.')
    result = extract_tags('', body, countries=countries, products=products,
                          keywords=words(*(f'합성주제{i}' for i in range(150)), '금', '쌀', '농업', '수출'))
    assert len([tag for tag in result['tags'] if tag['kind'] == 'keyword']) == 5
    assert {tag['country_code'] for tag in result['tags'] if tag['kind'] == 'country'} == {row['code'] for row in countries}
    assert {tag['hs_code'] for tag in result['tags'] if tag['kind'] == 'hs'} == {row['code'] for row in products}


def test_overlapping_aliases_do_not_inflate_frequency():
    candidates = [{'term': '무역 협정', 'canonical': '협정'},
                  {'term': '협정', 'canonical': '협정'},
                  {'term': '반도체', 'canonical': '반도체'}]
    assert chosen('무역 협정이 체결됐다. 반도체 수요와 반도체 공급이 증가했다.', candidates)[0] == '반도체'


def test_evidence_is_from_body_and_input_is_not_mutated():
    candidates = words('수출');original = copy.deepcopy(candidates)
    tags = select_keyword_tags(['수출 계획', '한국 기업은 반도체를 수출했다.'], candidates)
    assert tags[0]['evidence'] == '한국 기업은 반도체를 수출했다.'
    assert candidates == original


def test_no_keywords_does_not_reject_otherwise_eligible_article():
    p = prepared();before = copy.deepcopy(p)
    from news.dictionary import validate_dictionary
    dictionary = validate_dictionary({'countries': [], 'products': [], 'keywords': words('및', '의')})
    result = tag_for_load(p, dictionary)
    assert p == before and result['eligible'] == before['eligible'] is True
    assert result['focus'] == before['focus']
    assert not [t for t in result['tagging']['tags'] if t['kind'] == 'keyword']


def test_database_tags_store_at_most_five_keywords_keep_country_hs_and_repeat(local):
    candidates = [(f'합성주제{i:03d}', 'UNCLASSIFIED') for i in range(20)]
    candidates += [('의', 'UNCLASSIFIED'), ('및', 'CLASSIFIED'), ('한', 'PENDING')]
    with local.db.begin() as conn:
        conn.execute(text('INSERT INTO keyword_topic VALUES (:term,:status,NULL)'),
                     [{'term': term, 'status': status} for term, status in candidates])
    with local.db.connect() as conn:
        dictionary = read_project_dictionary(conn)
        p = prepared()
        p['body_masked'] += '\nHS 854200 품목이다. ' + ' '.join(term for term, _ in candidates[:20])
        before = copy.deepcopy(p);tagged = tag_for_load(p, dictionary)
        assert p == before and tagged['focus'] == p['focus'] and tagged['eligible'] == p['eligible']
        keywords = [t for t in tagged['tagging']['tags'] if t['kind'] == 'keyword']
        assert len(keywords) == 5 and not {'의', '및', '한'} & {t['keyword_text'] for t in keywords}
        assert {t['country_code'] for t in tagged['tagging']['tags'] if t['kind'] == 'country'} == {'KR', 'US'}
        assert [(t['hs_code'], t['hs_ver']) for t in tagged['tagging']['tags'] if t['kind'] == 'hs'] == [('854200', 'HS2022')]
        store = MySQLArticleStore(conn, {'hankyung': 'HANKYUNG'}, include_tags=True)
        assert store.save(tagged, return_result=True)['status'] == 'inserted'
        assert store.save(tagged, return_result=True)['status'] == 'existing'
        assert conn.execute(text('SELECT COUNT(*) FROM article')).scalar_one() == 1
        assert conn.execute(text('SELECT COUNT(*) FROM article_tag WHERE keyword_text IS NOT NULL')).scalar_one() == 5
        assert conn.execute(text('SELECT COUNT(*) FROM article_tag WHERE country_code IS NOT NULL')).scalar_one() == 2
        assert conn.execute(text('SELECT COUNT(*) FROM article_tag WHERE hs_code IS NOT NULL')).scalar_one() == 1
