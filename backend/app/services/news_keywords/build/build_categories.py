"""⑥ 대/중분류 표(topic_categories.csv)를 다시 만듭니다.

순서 (규칙을 먼저 모두 대조하고, 규칙으로 안 되는 것만 LLM 판단을 씀)
  1) 규칙(일치): 주제어가 분류 틀(TAX)의 대표 키워드와 정확히 같음
  2) 규칙(포함): 주제어가 대표 키워드(2글자 이상)를 포함 — TAX 위에서부터 먼저 맞는 분류
  3) 규칙 수정: 규칙 결과가 글자만 겹쳐 틀린 경우 바로잡은 목록(category_rule_fixes.csv, 규칙 결과·이유 기록)
  4) LLM 판단: 규칙에 하나도 안 걸린 말의 분야를 LLM이 뜻으로 정한 목록(category_llm.csv)
  5) 검수 필요: 위 어디에도 없거나 LLM이 "너무 넓어 정하지 않음"(미분류)으로 둔 말
  ※ 팀 검수(category_decisions.csv)에서 X로 고친 말은 위 결과보다 우선 — 배정방법 "팀 검수"
     (O는 지금 결과를 확인한 것이라 그대로 둠)
대상: 정리된 주제어(kw_topic) 중 기사 MIN_ARTICLES(10)건 이상. 기준·과정 설명은 CLASSIFICATION_RULES.md

실행: python backend/app/services/news_keywords/build/build_categories.py [--output 경로] [--min-articles 10]
"""
import argparse
import csv
from collections import Counter
from pathlib import Path

import pandas as pd

NK = Path(__file__).resolve().parents[1]
DATA = Path(__file__).resolve().parents[5] / "data" / "news_keywords"
MIN_ARTICLES = 10

# (대분류, 중분류, 그 분류를 대표하는 키워드 — 띄어쓰기로 구분)
TAX = [
 ("무역·통상", "관세·무역규제", "관세 반덤핑 세이프가드 수입규제 무역규제 제재 쿼터 비관세 보호무역 덤핑 수출통제 수출규제 수입금지 수출금지 무역구제 상계관세 무역장벽 무역분쟁 무역전쟁"),
 ("무역·통상", "협정·통상정책", "FTA RCEP CPTPP IPEF 자유무역협정 무역협정 통상 협정 USMCA EPA CEPA WTO 경제동반자 BRICS 아세안 APEC G20"),
 ("무역·통상", "통관·인증·표준", "통관 인증 원산지 표준 규격 FDA 할랄인증 라벨링 HS코드 상표 특허 지식재산 지재권 위조 검역 등록 수입요건 시험"),
 ("무역·통상", "공급망·물류", "공급망 GVC 물류 해운 운송 리쇼어링 니어쇼어링 프렌드쇼어링 배송 항만 콜드체인 철도 항공 해상 화물 컨테이너 운임 창고 풀필먼트 택배 라스트마일 홍해"),
 ("무역·통상", "수출입 일반", "바이어 조달 입찰 정부조달 수출상담 무역사절단 수출입동향 교역동향 수입시장"),
 ("경제·시장", "거시경제", "GDP 경제성장 성장률 경기 인플레이션 물가 고물가 금리 기준금리 환율 재정 예산 실업 경상수지 국가신용 IMF 경기침체 디플레이션 통화 외환 부채 국채 금융 은행 증시 주식 부동산 세금 법인세 부가세 관세청"),
 ("경제·시장", "시장·소비 동향", "소비트렌드 MZ Z세대 소비심리 구매력 중산층 소매 유통 리테일 쇼핑 온라인쇼핑 편의점 대형마트 백화점 할인 명품 가성비 프리미엄 구독 라이브커머스 소셜커머스 SNS 인플루언서 틱톡 아마존 쿠팡 테무 쉬인 알리익스프레스 라자다 쇼피"),
 ("경제·시장", "인구·노동", "고령화 인구 청년 노동 임금 최저임금 노동법 일자리 고용 저출산 도시화 이민 비자 외국인근로자 근로자 노동자 인력 인재 노조 파업"),
 ("기업·투자·진출", "투자·M&A", "FDI 투자유치 외국인투자 인수 합병 M&A 벤처캐피탈 VC 투자환경 투자인센티브 경제특구 산업단지 공단 특구 자유무역지대 법인설립 합작"),
 ("기업·투자·진출", "진출·마케팅", "현지화 마케팅 브랜드 유통망 파트너 판로 홍보 프랜차이즈 진출전략 시장조사 바이어발굴 PB 수출바우처 해외지사 현지법인"),
 ("기업·투자·진출", "전시회·행사", "엑스포 EXPO 컨퍼런스 콘퍼런스 포럼 상담회 쇼 페어 FAIR 사절단 로드쇼 세미나 설명회 위크"),
 ("기업·투자·진출", "창업·취업", "스타트업 창업 취업 해외취업 채용 구인 구직 K-MOVE 인턴 유니콘"),
 ("정책·제도", "정부 정책·지원", "정책 지원 보조금 인센티브 세제 IRA 육성 비전 법 제도 규정 개정 법안 규제 가이드라인 로드맵 계획 정부 의회 대선 총선 선거 행정명령 국가전략 신정부"),
 ("기술·디지털", "AI·소프트웨어", "AI 소프트웨어 SW 클라우드 데이터 빅데이터 생성형 CHATGPT LLM 알고리즘 머신러닝 딥러닝 AX"),
 ("기술·디지털", "디지털·플랫폼", "디지털 DX ICT IT 핀테크 전자상거래 온라인 플랫폼 앱 메타버스 블록체인 가상자산 암호화폐 CBDC 전자결제 결제 간편결제 디지털헬스 원격의료 에듀테크 게임 E스포츠 OTT 스트리밍 웹툰"),
 ("기술·디지털", "로봇·자동화·스마트", "로봇 자동화 스마트 사물인터넷 드론 스마트팩토리 스마트시티 스마트팜 자율주행 3D프린팅 모빌리티 E-모빌리티 UAM 우주 위성 항공우주"),
 ("기술·디지털", "통신·보안", "5G 6G 통신 사이버 보안 정보보호 개인정보 해킹 네트워크 위성통신"),
 ("에너지·환경", "에너지 전환", "에너지 재생에너지 태양광 풍력 수소 원자력 SMR 전력 전력망 ESS 에너지전환 그리드 송전 배전 발전 지열 수력 바이오에너지 바이오연료 연료전지 전기요금 에너지효율"),
 ("에너지·환경", "화석연료·자원", "석유 가스 LNG 석탄 원유 유가 광물 핵심광물 희토류 리튬 니켈 코발트 흑연 구리 원자재 광업 광산 철광석 알루미늄 금 은 망간"),
 ("에너지·환경", "친환경·ESG·기후", "친환경 ESG 탄소 탄소중립 넷제로 탈탄소 기후 기후변화 재활용 순환경제 지속가능 녹색 그린 환경 폐기물 플라스틱 CBAM 탄소배출 온실가스 EPR 생물다양성 물 수자원 대기오염"),
 ("산업·품목", "품목 (→ HS 코드로 연결)", "자동차 전기차 배터리 이차전지 반도체 디스플레이 화장품 뷰티 스킨케어 메이크업 식품 음료 커피 차 농업 농산물 농식품 수산 축산 육류 의료 의약 제약 바이오 헬스케어 "
  "의료기기 건설 인프라 플랜트 철강 화학 석유화학 섬유 의류 패션 기계 부품 소재 장비 조선 선박 방산 방위산업 가전 전자 소비재 반려동물 펫 완구 가구 "
  "인테리어 주류 와인 맥주 할랄 비건 K-FOOD K-뷰티 K-POP 한식 건강기능식품 미용 웰니스 관광 콘텐츠 교육 스포츠 문화 한류 생활용품 유아용품 신발 "
  "가방 주얼리 시계 안경 문구 생수 과자 라면 간편식 냉동식품 유제품 우유 과일 채소 곡물 쌀 밀 대두 옥수수 설탕 소금 향신료 담배 의약품 백신 진단 치과 임플란트 "
  "건축 건자재 시멘트 타이어 플라스틱제품 포장 포장재 종이 목재 유리 세라믹 공구 농기계 공작기계 산업용로봇 엘리베이터 에어컨 냉장고 세탁기 TV 스마트폰 노트북 "
  "태블릿 PC 카메라 조명 LED 케이블 변압기 전선 모터 펌프 밸브 엔진 항공기 열차 버스 트럭 오토바이 자전거 전동킥보드 충전 충전기 충전소 수소차 신에너지차 하이브리드 내연기관 중고차"),
 ("기관·인물·사건", "인물·기관·기업", "트럼프 바이든 시진핑 푸틴 모디 테슬라 KOTRA 무역관 중앙은행 BYD 삼성 현대 기아 LG SK 화웨이 알리바바 텐센트 애플 구글 마이크로소프트 엔비디아 TSMC 폭스콘 월마트 코스트코 GM FORD STELLANTIS 토요타 폭스바겐 연준 FOMC EU집행위 상공회의소 투자청 관세청 무역관"),
 ("기관·인물·사건", "사건·이슈", "전쟁 사태 우크라이나사태 코로나19 팬데믹 재난 지진 홍수 가뭄 폭염 분쟁 위기 봉쇄 반군 시위 테러 재건 쿠데타 파업 산불"),
]


def _read(name):
    with open(NK / name, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def load_lists():
    fixes = {r["주제어"]: (r["대분류"], r["중분류"]) for r in _read("category_rule_fixes.csv")}
    llm = {r["주제어"]: (r["대분류"], r["중분류"]) for r in _read("category_llm.csv")}
    return fixes, llm


def rule(word):
    """규칙만으로 낸 결과 (대분류, 중분류, 방법) — 안 걸리면 None."""
    u = word.upper()
    for big, mid, keys in TAX:
        if any(u == k.upper() for k in keys.split()):
            return big, mid, "규칙(일치)"
    for big, mid, keys in TAX:
        if any(len(k) >= 2 and k.upper() in u for k in keys.split()):
            return big, mid, "규칙(포함)"
    return None


def load_decisions(path=NK / "category_decisions.csv"):
    """팀 검수에서 X로 고친 말: 주제어 → (대분류, 중분류). 없으면 빈 표."""
    if not Path(path).exists():
        return {}
    return {r["주제어"]: (r["대분류"], r["중분류"]) for r in _read(path.name) if r.get("판정") == "X"}


def assign(word, fixes=None, llm=None, team=None):
    """주제어 하나 → (대분류, 중분류, 배정방법, 규칙 결과)."""
    fixes, llm, team = fixes or {}, llm or {}, team or {}
    r = rule(word)
    rule_txt = f"{r[0]} > {r[1]}" if r else ""
    if word in team:
        return (*team[word], "팀 검수", rule_txt)
    if r and word in fixes:
        return (*fixes[word], "규칙 수정", rule_txt) if fixes[word][0] != "미분류" else ("미분류", "미분류", "검수 필요", rule_txt)
    if r:
        return (*r, rule_txt)
    if word in llm and llm[word][0] != "미분류":
        return (*llm[word], "LLM 판단", "")
    return "미분류", "미분류", "검수 필요", ""


def build(clean_csv=DATA / "news_keywords_clean.csv", min_articles=MIN_ARTICLES):
    res = pd.read_csv(clean_csv, dtype=str, keep_default_na=False, usecols=["kw_topic"])
    docs = Counter(w for s in res.kw_topic for w in set(s.split("|")) if w)
    fixes, llm = load_lists()
    team = load_decisions()
    rows = []
    for w, n in docs.items():
        if n >= min_articles:
            big, mid, how, rule_txt = assign(w, fixes, llm, team)
            rows.append({"주제어": w, "대분류": big, "중분류": mid, "배정방법": how, "규칙 결과": rule_txt, "기사수": n})
    df = pd.DataFrame(rows)
    return df.sort_values(["기사수", "주제어"], ascending=[False, True]).reset_index(drop=True).astype({"기사수": int})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", type=Path, default=NK / "topic_categories.csv")
    ap.add_argument("--min-articles", type=int, default=MIN_ARTICLES)
    args = ap.parse_args()
    df = build(min_articles=args.min_articles)
    df.to_csv(args.output, index=False, encoding="utf-8-sig")
    print(f"{len(df)}개 → {args.output}")
    print(df.배정방법.value_counts().to_dict())


if __name__ == "__main__":
    main()
