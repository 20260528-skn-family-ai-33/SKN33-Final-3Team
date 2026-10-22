"""본문의 무역 활동 연결을 검사합니다. 제목·도입부·문장 비율은 진단용입니다."""
import re
from news.relevance import assess_relevance, TRADE

VERSION = 'focus-v7.1'

# 이전 평가 도구용 진단 함수입니다. 최종 판정에서는 호출하지 않습니다.
def broad_economic_overview(article, body):
    # 경제 전반을 다루는 제목에 한해, 무역을 설명하지 않는 거시경제 문단이
    # 과반이고 서로 다른 거시 주제가 3개 이상일 때 종합 경제 기사로 판단합니다.
    # 수출 문장이 있다는 이유만으로 포함하거나, 경제라는 단어만으로 제외하지 않습니다.
    if not (re.search(r'경제', article['title'])
            and re.search(r'성장|전망|불확실|회복|진단|동향', article['title'])):
        return False
    paragraphs = [p.strip() for p in body.splitlines() if len(p.strip()) >= 40]
    topics = (r'물가|인플레이션', r'금리|통화정책', r'내수|소비|소매판매',
              r'설비투자|건설투자|투자 부진', r'인구|생산가능인구', r'PMI|구매관리자지수')
    domestic = [p for p in paragraphs if not TRADE.search(p)
                and any(re.search(pattern, p) for pattern in topics)]
    topic_count = sum(any(re.search(pattern, p) for p in domestic) for pattern in topics)
    return len(paragraphs) >= 4 and len(domestic) > len(paragraphs)/2 and topic_count >= 3



def concrete_trade_evidence(body):
    """키워드 비율 대신 본문의 구체적 사실·품목·영향을 함께 확인합니다."""
    paragraphs = [p.strip() for p in body.splitlines() if p.strip()]
    countries = r'미국|중국|한국|일본|유럽|EU|캐나다|멕시코|베트남|인도|브라질|해외'
    goods = r'반도체|전기차|자동차|철강|배터리|전해액|소재|화장품|소비재|농산물|원유|선박|기계|첨단기술'
    rows = []
    for index, paragraph in enumerate(paragraphs):
        kinds = []
        # 수출과 무관한 매출·주가 숫자가 같은 문단에 있다는 이유로 근거로 세지 않습니다.
        if re.search(r'(?:수출|수입|무역흑자|무역적자|무역수지)[^.。\n]{0,45}?(?:\d[\d,.]*|수십|수백|수천)(?:\s*(?:억|조|만|천))*\s*(?:%|달러|원)', paragraph):
            kinds.append('무역 실적 수치')
        if (re.search(r'수출|수입|해외.{0,20}공급', paragraph)
                and re.search(goods, paragraph) and re.search(countries, paragraph)):
            kinds.append('국가·품목·교역')
        if (re.search(r'관세|통상 조치|수입규제|수출통제', paragraph)
                and re.search(countries, paragraph)
                and re.search(r'부과|인상|인하|제한|시행|검토|협상|면제|무관세|우회|회피', paragraph)):
            kinds.append('통상 조치')
        if re.search(r'수출.{0,45}(?:성장|지속|위험|기여|견인)|(?:보호무역|통상 갈등)', paragraph):
            kinds.append('무역 영향')
        # 해외 공장만 언급하면 인정하지 않습니다. 관세 대응과 생산·조달의 연결이 필요합니다.
        if (re.search(r'관세|무관세', paragraph) and re.search(countries, paragraph)
                and re.search(goods, paragraph)
                and re.search(r'현지.{0,12}생산|생산.{0,12}(?:기지|거점)|공장|공급망|역내.{0,12}조달', paragraph)
                and re.search(r'면제|무관세|우회|회피|피하|피하기|장벽.{0,12}(?:넘|대응)|관세.{0,12}부담 없이', paragraph)):
            kinds.append('관세 대응 생산·공급망')
        if kinds:
            rows.append({'paragraph':index+1,'kinds':kinds,'text':paragraph})
    types = {kind for row in rows for kind in row['kinds']}
    # 한 문단이어도 독립된 근거 유형이 함께 있으면 인정합니다. 문단 개수는 조건이 아닙니다.
    sufficient = ('관세 대응 생산·공급망' in types or (len(types) >= 2
                  and bool(types & {'무역 실적 수치','통상 조치'})
                  and '국가·품목·교역' in types))
    return sufficient, rows


def activity_evidence(body):
    """국가·품목 사전 일치를 요구하지 않고 본문의 거래·규제·해외 활동을 검사합니다."""
    patterns = {
        '교역 실적·거래·지원': (
            r'(?:수출|수입|무역)[^.。\n]{0,55}(?:계약|협약|상담|허가|규제|통제|선적|납품|지원|협력|물량|물품|품목|생산|수백|수천|수십|수출량|수입량|\d[\d,.]*\s*(?:%|억|조|만|천|달러|대|t))'
            r'|(?:계약|협약|구매|공급|납품|실증)[^.。\n]{0,55}수출'
            # '수출 실적을 언급'만으로 통과하지 않습니다. 실적은 변화·결과와 연결합니다.
            r'|(?:수출|수입|무역)(?:액|량|금액|대수|\s*실적)[^.。\n]{0,45}(?:증가|감소|늘|줄|기록|개선|악화|회복|달성|호조|부진|성장|하락|상승|유지)'
        ),
        '통상 규제·절차': (
            r'(?:관세|반덤핑|수출\s*통제|수입\s*규제|원산지)[^.。\n]{0,55}(?:부과|인상|인하|면제|무관세|우회|허가|세율|시행|적용|절차|생산|통관)'
            r'|(?:부과|면제|우회|생산)[^.。\n]{0,55}(?:관세|무관세)'
            r'|(?:무역협정|자유무역협정|FTA)[^.。\n]{0,45}(?:체결|협상|발효|원산지|관세)'
            # 실제 수출입 제한 조치와 협정의 적용·절차 변경도 본문 근거입니다.
            r'|(?:수출|수입)(?:을|를|이|가|은|는|에 대한)?\s*(?:제한|금지|허용)\s*(?:하|한|했|되|됐|조치|정책|방침|결정)'
            r'|(?:무역협정|자유무역협정|FTA)[^.。\n]{0,45}(?:적용[^.。\n]{0,20}(?:확대|변경|시행|안내|조정)|절차[^.。\n]{0,20}(?:조정|변경))'
            r'|절충\s*교역[^.。\n]{0,80}(?:수출|수입|조건|요청|요구)'
        ),
        # 국명·품목의 열거 대신 해외 고객·시장·현지 거점과 공급 행동의 연결을 봅니다.
        '해외 시장·공급·지원': (
            r'(?:해외|북미|미국|유럽|글로벌|현지|국제)[^.。\n]{0,100}(?:고객|시장|공급망|공장|법인|기업)[^.。\n]{0,100}(?:공급|납품|진출|공략|기술\s*지원|설치|엔지니어|판로)'
            r'|(?:해외|북미|미국|유럽|현지)[^.。\n]{0,70}(?:구매|납품|수출|기술\s*지원)[^.。\n]{0,45}(?:협의|계약|확대|강화|제공|추진)'
        ),
    }
    rows=[]
    for index,paragraph in enumerate(body.splitlines(),1):
        if not paragraph.strip():
            continue
        kinds=[kind for kind,pattern in patterns.items() if re.search(pattern,paragraph,re.I)]
        if kinds:
            rows.append({'paragraph':index,'kinds':kinds,'text':paragraph})
    return rows


def assess_focus(article, body_masked):
    clean_article = {**article, 'body': body_masked}
    lead_passed, lead_note = assess_relevance(clean_article)
    sentences = [s.strip() for s in re.split(r'(?<=[.!?。])\s+|\n', body_masked) if len(s.strip()) >= 20]
    if not sentences:
        sentences = [s.strip() for s in re.split(r'(?<=[.!?。])\s+|\n', body_masked) if s.strip()]
    evidence = [s for s in sentences if TRADE.search(s)]
    ratio = len(evidence) / max(1, len(sentences))
    _, concrete = concrete_trade_evidence(body_masked)
    activities = activity_evidence(body_masked)
    # 국가·품목 목록은 기존 상세 근거 표시용으로만 남깁니다. 일치 여부로 탈락시키지 않습니다.
    # 관세 대응 생산은 별도 사실 근거입니다. 문단 수·문장 비율·제목으로 통과를 제한하지 않습니다.
    localization = any('관세 대응 생산·공급망' in row['kinds'] for row in concrete)
    accepted = bool(activities or localization)
    reason = ('본문의 거래·규제·무역지원 또는 해외 공급 활동 연결 확인' if accepted
              else '본문에서 구체적인 거래·규제·무역지원 또는 해외 공급 활동 연결을 확인하지 못함')
    return {'accepted':accepted,'reason':reason,'version':VERSION,
            'concrete_evidence':concrete,'activity_evidence':activities,
            'lead_passed':lead_passed,'lead_note':lead_note,
            'trade_sentence_count':len(evidence),'sentence_count':len(sentences),
            'trade_sentence_ratio':ratio,
            'limitation':'표현 연결을 검사하는 규칙 기반 판정이며 의미 이해 모델이 아닙니다. 제목·도입부·문장 비율은 진단용입니다.'}
