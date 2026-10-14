"""④ 동의어 사전(synonyms.csv)을 다시 만듭니다.

기준
- 아래 GROUPS의 묶음 = 같은 뜻으로 확정한 표기들. 근거:
  · 괄호 표기 — 원본 키워드에 "전기차(EV)"처럼 괄호로 함께 적힌 쌍 (기자가 같은 뜻이라고 적은 것)
  · 표기 유사 — 기호·띄어쓰기·대소문자·한글/영문 표기만 다른 것 (K-뷰티 · K뷰티 · K-BEAUTY)
  · 같은 뜻 — 글자는 다르지만 뜻이 같다고 판단한 것 (판단 주체: Claude, 팀 검수 대상)
- 대표어 = 묶음 안에서 기사 수가 가장 많은 표기 (동의어 적용 전 주제어 기준)
- 데이터에 실제로 있는 표기만 남기고, 두 개 미만이면 묶음을 건너뜀
- 임베딩·동시 등장 후보는 여기 넣지 않음 → synonym_candidates.py로 뽑아 팀 O/X 후 GROUPS에 추가

실행: python backend/app/services/news_keywords/build/build_synonyms.py [--output 경로]
"""
import argparse
import csv
import importlib.util
from collections import Counter
from pathlib import Path

import pandas as pd

NK = Path(__file__).resolve().parents[1]
RAW = Path(__file__).resolve().parents[5] / "data" / "news" / "news_raw_20220101_20261013.csv"
spec = importlib.util.spec_from_file_location("keyword_clean", NK / "keyword_clean.py")
kc = importlib.util.module_from_spec(spec); spec.loader.exec_module(kc)

# (근거, [같은 뜻 표기들]) — 첫 번째 원소는 이름표일 뿐, 대표어는 기사 수로 정함
GROUPS = [
 ("괄호 표기", ["AI", "인공지능"]),
 ("괄호 표기", ["전기차", "EV", "전기자동차", "ELECTRIC VEHICLE", "ELECTRICVEHICLE"]),
 ("괄호 표기", ["FDI", "외국인직접투자"]),
 ("괄호 표기", ["GDP", "국내총생산"]),
 ("괄호 표기", ["ICT", "정보통신기술"]),
 ("괄호 표기", ["ESS", "에너지저장장치"]),
 ("괄호 표기", ["SMR", "소형모듈원자로"]),
 ("괄호 표기", ["GVC", "글로벌가치사슬"]),
 ("괄호 표기", ["PPP", "민관협력"]),
 ("괄호 표기", ["PB", "자체브랜드"]),
 ("괄호 표기", ["EEC", "동부경제회랑", "태국동부경제회랑"]),
 ("괄호 표기", ["EPR", "생산자책임재활용"]),
 ("괄호 표기", ["BRICS", "브릭스"]),
 ("괄호 표기", ["LLM", "대규모언어모델"]),
 ("괄호 표기", ["비건", "VEGAN"]),
 ("괄호 표기", ["에듀테크", "EDTECH", "EDU TECH"]),
 ("괄호 표기", ["메타버스", "METAVERSE"]),
 ("괄호 표기", ["리쇼어링", "RESHORING"]),
 ("괄호 표기", ["탈탄소", "DECARBONIZATION"]),
 ("표기 유사", ["CHATGPT", "CHAT GPT", "챗GPT", "챗지피티"]),
 ("표기 유사", ["K-뷰티", "K뷰티", "케이뷰티", "K-BEAUTY", "KBEAUTY", "K BEAUTY"]),
 ("표기 유사", ["K-푸드", "K푸드", "케이푸드", "K-FOOD", "KFOOD", "K FOOD"]),
 ("표기 유사", ["K-POP", "KPOP", "케이팝", "K-팝", "K팝"]),
 ("표기 유사", ["K-MOVE", "KMOVE"]),
 ("표기 유사", ["K-소비재", "K소비재"]),
 ("표기 유사", ["K-콘텐츠", "K콘텐츠", "K-CONTENTS", "K-컨텐츠"]),
 ("표기 유사", ["K-패션", "K패션", "K-FASHION"]),
 ("표기 유사", ["E스포츠", "E-스포츠", "ESPORTS", "E-SPORTS", "이스포츠"]),
 ("표기 유사", ["E-모빌리티", "E모빌리티", "이모빌리티", "EMOBILITY", "E MOBILITY", "E-MOBILITY"]),
 ("표기 유사", ["넷제로", "NETZERO", "NET-ZERO", "NET ZERO"]),
 ("표기 유사", ["이차전지", "2차전지"]),
 ("표기 유사", ["스마트팩토리", "스마트공장", "SMART FACTORY"]),
 ("표기 유사", ["코로나19", "코로나", "COVID-19", "COVID19", "코로나바이러스"]),
 ("같은 뜻", ["우크라이나사태", "러-우사태", "러우사태", "러시아-우크라이나사태", "러시아우크라이나사태", "러시아-우크라이나전쟁", "러시아우크라이나전쟁", "우크라이나전쟁", "러-우전쟁"]),
 ("같은 뜻", ["워싱턴무역관", "코트라워싱턴", "워싱턴DC무역관", "워싱턴D.C.무역관"]),
 ("같은 뜻", ["KOTRA", "코트라", "대한무역투자진흥공사"]),
 ("같은 뜻", ["원자력", "원전", "원자력발전"]),
 ("같은 뜻", ["재생에너지", "신재생에너지", "신재생", "재생가능에너지"]),
 ("같은 뜻", ["전자상거래", "이커머스", "E-COMMERCE", "ECOMMERCE", "E커머스", "E-커머스"]),
 ("같은 뜻", ["완구", "장난감"]),
 ("같은 뜻", ["IRA", "인플레이션감축법"]),
 ("같은 뜻", ["CBAM", "탄소국경조정제도", "탄소국경세"]),
 ("같은 뜻", ["USMCA", "미국멕시코캐나다협정"]),
 ("같은 뜻", ["RCEP", "역내포괄적경제동반자협정"]),
 ("같은 뜻", ["CPTPP", "포괄적점진적환태평양경제동반자협정"]),
 ("같은 뜻", ["사물인터넷", "IOT"]),
 ("같은 뜻", ["빅데이터", "BIG DATA"]),
 ("같은 뜻", ["클라우드", "CLOUD"]),
 ("같은 뜻", ["반도체", "SEMICONDUCTOR"]),
 ("같은 뜻", ["수소", "HYDROGEN"]),
 ("같은 뜻", ["할랄", "HALAL"]),
 ("같은 뜻", ["스타트업", "START-UP", "STARTUP"]),
 ("같은 뜻", ["디지털전환", "DX", "디지털트랜스포메이션"]),
]


def topic_counts(raw_csv=RAW):
    """동의어를 적용하기 전 주제어별 기사 수 (정제·지역 분리·예외까지 적용)."""
    df = kc.read_input(raw_csv)
    geo, keep = kc.load_geo(), kc.load_exceptions()
    docs = Counter()
    for k, n in zip(df["kwrd"], df["natn"]):
        docs.update(set(kc.process_article(k, n, geo, keep)["kw_topic"]))
    return docs


def build(docs):
    rows, skipped = [], []
    for basis, forms in GROUPS:
        forms = list(dict.fromkeys(kc.normalize(f) for f in forms))
        present = [f for f in forms if docs[f] > 0]
        if len(present) < 2:
            skipped.append(forms[0]); continue
        rep = max(present, key=lambda f: docs[f])
        rows += [{"표기": f, "대표어": rep, "근거": basis, "표기_기사수": docs[f], "대표어_기사수": docs[rep]}
                 for f in present if f != rep]
    return pd.DataFrame(rows), skipped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, default=RAW)
    ap.add_argument("--output", type=Path, default=NK / "synonyms.csv")
    args = ap.parse_args()
    df, skipped = build(topic_counts(args.input))
    df.to_csv(args.output, index=False, encoding="utf-8-sig")
    print(f"규칙 {len(df)}개 (대표어 {df.대표어.nunique()}개) → {args.output} / 데이터에 없어 건너뛴 묶음 {skipped}")


if __name__ == "__main__":
    main()
