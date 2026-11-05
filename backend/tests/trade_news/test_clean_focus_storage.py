import copy, json
import pytest
from news.clean import clean_body
from news.focus import assess_focus, activity_evidence
from news.staging import PreparationStore
from news.save import Store
from .helpers import article, prepared

@pytest.mark.parametrize('body',[
    '직장인의 월 수입액은 300만원이다.',
    '가구의 연간 수입액은 5000만원을 기록했다.',
    '직장인의 월 수입은 300달러다.',
    '직장인의 월평균 수입액은 300달러다.',
    '근로자의 연간 수입액은 5000만원이다.',
    '직장인의 월 수입은 300만원이다.', '임대 수입 100억원을 기록했다.',
    '광고 수입이 50% 증가했다.', '월세 수입 300만원으로 생활한다.',
    '수입은 급여 300만원과 임대료로 구성된다.', '가계 수입이 10% 증가했다.',
    '수출 통계를 언급했으나 개인의 월 수입은 300만원이다.',
])
def test_income_not_trade(body):
    assert not assess_focus({'title':'수입 변화'},body)['accepted']

@pytest.mark.parametrize('body',[
    '한국 기업은 미국 반도체 수입 300만달러 계약을 체결했다.',
    '중국산 가구 수입액은 300만달러로 증가했다.',
    '개인 수입품 300만달러를 통관했다.',
    '가구의 수입량은 300만개로 증가했다.',
    '한국 기업의 월 반도체 수입액은 300만달러로 증가했다.',
    '직장인의 월 수입액은 300만원이다. 한국 기업은 미국 반도체 수입 계약을 체결했다.',
    '직장인의 월 수입은 300달러다. EU는 중국산 전기차에 관세를 부과했다.',
    '월 수입액은 300억원으로 증가했다.', '연간 수입량은 100만t으로 증가했다.',
    '중국산 제품 수입을 금지하는 조치를 시행했다.',
    'EU 관세 장벽을 피하기 위해 중국 전기차업체가 현지 생산기지를 구축했다.',
    '임대 수입 100만원이 발생했다. 한국 기업은 반도체 수출 계약을 체결했다.',
    '월 수입 300만원 규모의 농산물을 통관했다.',
])
def test_trade_preserved(body):
    result=assess_focus({'title':'기업 동향'},body)
    assert result['accepted']
    assert all(row['text'] in body for row in result['activity_evidence']+result['concrete_evidence'])

def test_clean_mask_preserves_original_and_change_evidence():
    raw=article();before=copy.deepcopy(raw);result=clean_body(raw['body'])
    assert raw==before
    assert '100억원' in result['body_masked'] and 'sample@example.test' not in result['body_masked']
    assert '공유하기' not in result['body_masked']
    assert any(row['before']=='공유하기' for row in result['removed'])

def test_both_bodies_and_versions_still_stored(tmp_path):
    raw=article();p=prepared();assert p['eligible']
    s=PreparationStore(tmp_path/'prepared.sqlite3');s.save(raw,p);s.save(raw,p)
    original=json.loads(s.db.execute('SELECT payload FROM raw_version').fetchone()[0])
    assert original['body']==raw['body']
    assert s.db.execute('SELECT COUNT(*) FROM raw_version').fetchone()[0]==1
    assert json.loads(s.db.execute('SELECT payload FROM prepared_article').fetchone()[0])['body_masked']==p['body_masked']
    s.close();store=Store(tmp_path)
    item={**raw,'body_masked':p['body_masked']};store.save_article(item);store.export()
    row=json.loads(store.db.execute('SELECT payload FROM articles').fetchone()[0])
    exported=json.loads(next((tmp_path/'articles').glob('*.json')).read_text())
    assert row['body']==exported['body']==raw['body']
    assert row['body_masked']==exported['body_masked']==p['body_masked']
    store.close()


def test_income_inside_full_body_does_not_supply_trade_evidence():
    body='직장인들의 생활비 부담이 커지고 있다.\n직장인의 월 수입액은 300만원이다.\n주거비와 식비를 제외하면 저축 여력이 부족하다.'
    original=body
    assert not assess_focus({'title':'생활비 조사'},body)['accepted']
    assert body==original
    # 유효한 한 문단만 추가해도 기존 기준대로 통과하고, 근거 문장은 변형하지 않습니다.
    trade='한국 기업은 미국 반도체 수입 300만달러 계약을 체결했다.'
    result=assess_focus({'title':'생활비 조사'},body+'\n'+trade)
    assert result['accepted']
    assert [e['text'] for e in result['activity_evidence']]==[trade]


@pytest.mark.parametrize(('before', 'after'), [
    ('(서울=뉴스1) 홍길동 기자 = 대구의 수출이 늘었다.', '대구의 수출이 늘었다.'),
    ('[세종=뉴시스]김하나 기자=본문', '본문'),
    ('(워싱턴·뉴욕=연합뉴스) 김하나 박두리 특파원 ＝ 본문', '본문'),
    ('(대구-) 김하나 기자 본문', '본문'),
    ('(서울=뉴스1) 김하나 기자\n본문', '본문'),
    ('본문\n[email\u00a0protected] 김하나 박두리 기자', '본문'),
    ('(서울=뉴스1) [기자정보] = 본문 속 A=B', '본문 속 A=B'),
    ('[email protected] [기자정보]', ''),
    ('김하나 법무법인 태평양 전문위원', '김하나 법무법인 태평양 전문위원'),
    ('(서울=뉴스1) 장관은 기자들과 만났다.', '(서울=뉴스1) 장관은 기자들과 만났다.'),
])
def test_reporter_credit_preserves_context(before, after):
    assert clean_body(before)['body_masked'] == after
    assert clean_body(after)['body_masked'] == after
