"""제목의 무역 관련 단서를 표시합니다. 수집·적재 여부는 결정하지 않습니다."""
import re
from copy import deepcopy

VERSION = 'title-candidates-v3'
PRIORITY_VERSION = 'title-review-priority-v2'
PATTERNS = {
    '직접 표현': r'무역|수출|수입규제|수입액|수입량|수입\s*재개|수입\s*물가|관세(?!청)|통상협상|자유무역|\bFTA\b|수출통제|반덤핑',
    '해외 시장': r'해외.{0,8}시장|유럽시장|미국시장|중국시장',
    '진출 행동': r'공략|진출|수주|납품|수출|판로',
    '생산·공급망': r'공급망|현지\s*생산|해외\s*공장|생산기지',
    '국제 사업': r'글로벌|세계\s*점유율|해외\s*박람회|SIAL',
    # 할랄 인증은 해외 거래 확정이 아닌 인증 관련 탐색 단서입니다.
    '인증': r'할랄\s*인증',
    '품목': r'반도체|전기차|가솔린차|자동차|수산물|K-?뷰티|철강|배터리|화장품|식품',
}

def review_priority(title):
    """제목의 표현 조합으로 검수 순서만 안내합니다. 무역 확정·자동 제외에 사용하지 않습니다."""
    # 기관·도로 이름 안의 단어가 실제 수출·품목 활동으로 오인되지 않게 합니다.
    context = re.sub(r'수출입은행|농협무역|무역협회|무역보험공사|반도체로', '', title)
    patterns = {
        '무역 활동': r'수출(?!입은행)|무역|수입규제|수입액|수입량|수입\s*재개|수입\s*물가|통상협상|자유무역|\bFTA\b',
        '무역 규제': r'관세(?!청)|수출\s*통제|반덤핑',
        # 단독 축약어만 인식해 러닝·러시아워 같은 일반 단어와 구분합니다.
        '해외 대상': r'미국|중국|일본|유럽|브라질|인니|카자흐|프랑스|러시아|북미|해외|실리콘밸리|파리|대러|EU|[美中日佛]|(?<![가-힣A-Za-z])(?:러|英|獨|韓)(?![가-힣A-Za-z])',
        '진출 행동': r'진출|공략|설립|개소|판로|계약|협약|수출',
        '해외 사업': r'공급망|현지\s*생산|해외\s*공장|생산기지|글로벌|SIAL|박람회|법인',
        # 인증 단독으로 높은 우선순위를 부여하지 않습니다.
        '인증': r'할랄\s*인증',
        '금융 중심': r'코스피|코스닥|환율|원화|달러당|주가|관련株|장비주|장비株|주식|증시',
        '거래·실적': r'감소|증가|호조|부진|계약|협약|상담|지원|경쟁력|본궤도|성과|확대',
    }
    hits = {k: list(dict.fromkeys(re.findall(p, context, re.I))) for k, p in patterns.items()}
    # 규제가 구체적인 대상·영향과 연결되면 주가 표현이 있어도 우선 확인합니다.
    if hits['무역 규제'] and (hits['해외 대상'] or re.search(PATTERNS['품목'] + r'|구리|타이어|굴착기', context, re.I)):
        level, reason = '높음', '무역 규제가 국가·품목과 함께 나타납니다.'
    elif hits['무역 활동'] and hits['금융 중심']:
        level, reason = '중간', '무역 표현은 있으나 금융·증시 분석이 중심일 수 있습니다.'
    elif hits['무역 활동'] and (hits['해외 대상'] or hits['거래·실적'] or hits['무역 규제']):
        level, reason = '높음', '무역 활동이 해외 대상 또는 거래·실적·지원과 연결됩니다.'
    elif hits['해외 대상'] and hits['진출 행동'] and re.search(r'공급망|시장', context):
        level, reason = '높음', '해외 대상과 시장·공급망 진출 행동이 함께 나타납니다.'
    elif hits['무역 활동'] or hits['무역 규제'] or hits['인증'] or hits['해외 사업'] or (hits['해외 대상'] and hits['진출 행동']):
        level, reason = '중간', '무역·해외 사업 단서는 있으나 구체적인 국제 거래 연결은 본문 확인이 필요합니다.'
    elif re.search(r'반도체로|수출입은행', title):
        level, reason = '낮음', '관련 단어가 도로명·기관명으로 쓰였고 별도 무역 활동 근거는 확인되지 않습니다.'
    elif hits['금융 중심']:
        level, reason = '낮음', '품목·주가 표현 외에 구체적인 무역 활동 연결은 확인되지 않습니다.'
    else:
        level, reason = '낮음', '품목명 등 탐색 단서는 있으나 제목에서 구체적인 무역 활동 연결은 확인되지 않습니다.'
    evidence = [m for values in hits.values() for m in values]
    evidence += re.findall(PATTERNS['품목'], title, re.I)
    return {'version': PRIORITY_VERSION, 'level': level, 'reason': reason,
            'matched_terms': list(dict.fromkeys(evidence)), 'basis': '제목의 표현 조합',
            'body_review_status': '미검수', 'exclusion_applied': False}


def annotate_candidate(title):
    """실제로 제목에 존재하는 표현만 근거로 기록합니다. 점수나 탈락값은 없습니다."""
    hits = {key: list(dict.fromkeys(re.findall(pattern, title, re.I)))
            for key, pattern in PATTERNS.items()}
    if hits['직접 표현'] or (hits['해외 시장'] and hits['진출 행동']):
        label = '무역 관련 후보'
        reason = '무역 직접 표현 또는 해외 시장 진출 표현이 있습니다. 본문 전체에서 주제를 확인해야 합니다.'
    elif hits['인증']:
        label = '간접 단서 있음'
        reason = '할랄 인증 단서가 있습니다. 해외 시장 진입·수출과 연결되는지는 본문 확인이 필요합니다.'
    elif hits['생산·공급망'] or hits['국제 사업']:
        label = '간접 단서 있음'
        reason = '공급망·현지 생산 또는 국제 사업 단서가 있습니다. 이 표현만으로 실제 교역이 주제인지는 알 수 없습니다.'
    elif hits['품목']:
        # 품목 단독 표현도 탐색 단서로 남기되 무역 여부는 확정하지 않습니다.
        label = '품목 단서 후보'
        reason = '제목에 ' + ', '.join(hits['품목']) + ' 표현이 있습니다. 해외 거래 여부는 드러나지 않아 본문 확인이 필요합니다.'
    else:
        label = '제목상 근거 약함'
        reason = '등록된 무역 관련 표현 조합이 제목에서 확인되지 않습니다. 무역 기사 제외 판정이 아니며 본문은 미검수입니다.'
    return {'version': VERSION, 'label': label, 'reason': reason,
            'evidence': [{'category': key, 'text': value, 'source': '제목'}
                         for key, values in hits.items() for value in values],
            'basis': '제목만 참고한 탐색용 표시', 'exclusion_applied': False,
            'review_priority': review_priority(title)}


def annotate_articles(articles):
    """원본·입력 순서를 보존하고 모든 기사에 표시만 추가합니다."""
    result = deepcopy(articles)
    for article in result:
        article['trade_candidate'] = annotate_candidate(article.get('title', ''))
    return result
