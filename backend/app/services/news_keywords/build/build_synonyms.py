"""④ 동의어 사전(synonyms.csv)을 다시 만듭니다.

기준
- 아래 GROUPS의 묶음 = 같은 뜻으로 확정한 표기들. 근거:
  · 괄호 표기 — 원본 키워드에 "전기차(EV)"처럼 괄호로 함께 적힌 쌍 (기자가 같은 뜻이라고 적은 것)
  · 표기 유사 — 기호·띄어쓰기·대소문자·한글/영문 표기만 다른 것 (K-뷰티 · K뷰티 · K-BEAUTY)
  · 같은 뜻 — 글자는 다르지만 뜻이 같다고 판단한 것 (LLM 판단, 팀 검수 대상 — CLASSIFICATION_RULES.md)
- 대표어 = 묶음 안에서 기사 수가 가장 많은 표기 (동의어 적용 전 주제어 기준)
- 판정 칸 (규칙으로 확인되는지 코드가 직접 검사):
  · 규칙: 괄호 — 원본 키워드에 "A(B)"로 실제로 함께 적힌 쌍
  · 규칙: 표기 — 기호·띄어쓰기·대소문자를 지우면 같은 글자 (K뷰티 = K-뷰티, CHAT GPT = 챗GPT)
  · LLM 판단 — 위 규칙으로 확인되지 않아 뜻으로 묶은 것 (번역어·약어·같은 뜻 다른 말: K-BEAUTY = K-뷰티, 인공지능 = AI)
- 데이터에 실제로 있는 표기만 남기고, 두 개 미만이면 묶음을 건너뜀
- 검수 파일은 읽지 않습니다. 팀 검수 결과에서 규칙으로 옮길 수 없는 같은 말만 코드 안 예외 목록(SYNONYM_EXTRA)에 둡니다 — 판정 "예외: 같은 말"
- 접미사 규칙: OO산업 · OO시장 · OO업 → OO (기본 단어가 주제어로 있고, 예외 목록에 없을 때) — 판정 "규칙: 접미사"
- 같은 기본어 규칙: 기본 단어가 없거나 적어도 OO산업 · OO시장 · OO업끼리는 같은 말 (관광업 = 관광산업) — 판정 "규칙: 같은 기본어"
- 범용 접미사(률·율·화·성·품)는 규칙으로 쓰지 않음: 운동화 ≠ 운동, 현대화 ≠ 현대, 화장 ≠ 화장품처럼 틀리는 경우가 많아
  팀이 같은 말로 정한 것(경제성장 = 경제성장률, 디지털 = 디지털화 등)만 예외 목록에 둠
- 반대말(rule_data.SYNONYM_OPPOSITES: 수출 ↔ 수입 …)과 묶지 않을 쌍(SYNONYM_BLOCK)은 어떤 규칙으로도 묶지 않음
- 사슬(A→B→C)은 마지막 대표어로 정리 (keyword_clean.py는 한 번만 바꾸므로)

실행: python backend/app/services/news_keywords/build/build_synonyms.py [--output 경로]
"""
import argparse
import csv
import importlib.util
import re
from collections import Counter
from pathlib import Path

import pandas as pd

NK = Path(__file__).resolve().parents[1]
RAW = Path(__file__).resolve().parents[5] / "data" / "news" / "news_raw_20220101_20261013.csv"
spec = importlib.util.spec_from_file_location("keyword_clean", NK / "keyword_clean.py")
kc = importlib.util.module_from_spec(spec); spec.loader.exec_module(kc)
rd = kc.rd   # 규칙 데이터 (반대말 SYNONYM_OPPOSITES)
OUT = kc.SYNONYMS_PATH   # data/news_keywords/synonyms.csv (git 제외, 이 코드로 다시 만듦)

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
    """동의어를 적용하기 전 주제어별 기사 수 (정제·지역 분리·예외까지 적용)와, 원본에 "A(B)"로 함께 적힌 쌍."""
    df = kc.read_input(raw_csv)
    geo, keep = kc.load_geo(), kc.load_exceptions()
    docs, paren = Counter(), set()
    for k, n in zip(df["kwrd"], df["natn"]):
        docs.update(set(kc.process_article(k, n, geo, keep)["kw_topic"]))
        for a, b in re.findall(r"([^,()]+)\(([^()]+)\)", k or ""):
            a, b = kc.normalize(a.strip().lstrip("#")), kc.normalize(b.strip())
            if a and b:
                paren.add(frozenset((a, b)))
    docs.paren = paren
    return docs


def skeleton(word):
    """기호·띄어쓰기·대소문자를 지운 글자 골격 (케이 → K, 챗 → CHAT, 지피티 → GPT 표기 차이 포함)."""
    w = kc.normalize(word).upper().replace("케이", "K").replace("챗", "CHAT").replace("지피티", "GPT")
    return re.sub(r"[\s\-·_./&]", "", w)


def check(form, rep, paren):
    if frozenset((form, rep)) in paren:
        return "규칙: 괄호"
    if skeleton(form) == skeleton(rep):
        return "규칙: 표기"
    return "LLM 판단"


# ── 접미사 규칙: OO산업 · OO시장 · OO업 → OO ─────────────────────────────
# 같은 대상을 범위만 다르게 부른 말이라 기본 단어 하나로 모읍니다 (검색 누락 · 급증 건수 쪼개짐 방지, HS 연결도 기본 품목어로).
SUFFIXES = ("산업", "시장", "업")
SUFFIX_MIN_BASE = 5          # 기본 단어(OO)가 주제어로 5건 이상 있을 때만
SUFFIX_EXCLUDE_BASE = {      # 기본 단어가 너무 넓거나(형용사·범위어) 다른 뜻이라 묶으면 안 되는 것
    "서비스", "미래", "유망", "혁신", "전략", "고급", "고부가가치", "해외", "지원", "선물", "투자", "신흥",
    "규슈",                  # 지명
    "노동",                  # 노동(노동법·노조 포함) ≠ 노동시장 — 노동시장은 고용시장과 묶음(팀 검수)
}

# 예외: 규칙으로 옮길 수 없지만 팀이 같은 말로 정한 쌍 (팀 동의어 검수 2026-10-19 · O). (표기, 대표어 후보)
SYNONYM_EXTRA = [
    ("제조", "제조업"),   # 대표어는 제조업 (접미사 규칙만 쓰면 제조로 뒤집힘)
    ("신에너지자동차", "신에너지차"), ("리튬배터리", "리튬이온배터리"), ("지속가능", "지속가능성"), ("건설자재", "건축자재"), ("물가상승률", "물가상승"),
    ("실버경제", "실버산업"), ("의약", "의약품"), ("태양광발전", "태양광"), ("경제성장", "경제성장률"), ("보건의료", "보건"), ("디지털헬스", "디지털헬스케어"),
    ("관세율", "관세"), ("스마트농업", "스마트팜"), ("푸드", "식품"), ("탈탄소화", "탈탄소"), ("음식", "식품"), ("건강식품", "건강기능식품"),
    ("인력부족", "노동력부족"), ("친환경에너지", "그린에너지"), ("원격진료", "원격의료"), ("현지진출", "해외진출"), ("참관", "참관기"), ("노동자", "근로자"),
    ("수출금지", "수출통제"), ("상표권", "상표"), ("고용시장", "노동시장"), ("의료미용", "미용의료"), ("기능성식품", "건강기능식품"), ("지적재산권", "지식재산권"),
    ("여행", "관광"), ("푸드", "음식"), ("일자리", "채용"), ("특별경제구역", "경제특구"), ("풍력발전", "풍력"), ("채용", "고용"), ("디지털화", "디지털"),
    ("바이오테크", "바이오"), ("일자리", "고용"), ("중부지역", "중부"), ("스마트화", "스마트"), ("웰빙", "웰니스"), ("애완동물", "반려동물"), ("창업", "스타트업"),
    ("피부", "피부관리"), ("펫테크", "펫케어"), ("헤어", "헤어케어"), ("성장률", "성장"), ("건강보조식품", "건강기능식품"), ("스타트업생태계", "스타트업"),
    ("한국화장품", "화장품"), ("화장품수출", "화장품"), ("디자이너패션", "패션"), ("노동", "고용"), ("두피케어", "헤어케어"), ("대체에너지", "재생에너지"),
    ("식품가공", "식품"), ("벤처", "스타트업"), ("식품수출", "식품"), ("완성차", "자동차"),
]

# 예외: 규칙이 묶지만 팀이 같은 말이 아니라고 정한 쌍 (팀 동의어 검수 · X). 방향 없음
SYNONYM_BLOCK = [("온라인시장", "온라인"), ("의료미용시장", "의료미용")]


def opposite(a, b):
    """반대말 쌍이면 True (수출금지 ↔ 수입금지, 구직 ↔ 구인)."""
    return any((x in a and y in b) or (y in a and x in b) for x, y in rd.SYNONYM_OPPOSITES)


def suffix_pairs(docs):
    """OO산업 · OO시장 · OO업 → OO (OO가 2글자 이상, 기사 SUFFIX_MIN_BASE건 이상, 예외 아님)."""
    out = []
    for w in docs:
        for suf in SUFFIXES:
            base = w[: -len(suf)]
            if w.endswith(suf) and len(base) >= 2 and docs.get(base, 0) >= SUFFIX_MIN_BASE and base not in SUFFIX_EXCLUDE_BASE:
                out.append((w, base, suf))
                break
    return out


def same_base_pairs(docs):
    """같은 기본어의 OO산업 · OO시장 · OO업끼리 → 기사 많은 쪽 (기본 단어 OO가 접미사 규칙 조건을 못 채울 때: 관광업 = 관광산업)."""
    groups = {}
    for w in docs:
        for suf in SUFFIXES:
            if w.endswith(suf) and len(w) - len(suf) >= 2:
                groups.setdefault(w[: -len(suf)], []).append(w)
                break
    out = []
    for base, forms in groups.items():
        if len(forms) < 2 or base in SUFFIX_EXCLUDE_BASE or docs.get(base, 0) >= SUFFIX_MIN_BASE:
            continue
        rep = max(forms, key=lambda f: (docs[f], f))
        out += [(f, rep, base) for f in forms if f != rep]
    return out


class SynonymConflict(ValueError):
    """규칙·예외로 만든 사전이 막아야 할 쌍(반대말·SYNONYM_BLOCK)을 합치거나 예외 같은 말(SYNONYM_EXTRA)을 가름 — 목록을 정리해야 함."""


def conflicts(rows, blocked, joined=()):
    """최종 사전 {표기: (대표어, 판정)}을 막을 쌍·묶을 쌍과 대조합니다. 어긋난 줄의 설명 목록.
    - 막을 쌍(blocked: SYNONYM_BLOCK)은 최종 대표어가 달라야 함 (다른 연결을 거쳐 합쳐지면 안 됨). 반대말은 따로 검사
    - 묶을 쌍(joined: SYNONYM_EXTRA)은 최종 대표어가 같아야 함"""
    rep = lambda w: rows[w][0] if w in rows else w
    how = lambda w: f"{w} → {rows[w][0]} ({rows[w][1]})" if w in rows else f"{w} (대표어)"
    pairs = lambda s: (sorted(p) for p in sorted(s, key=sorted))
    out = [f"{a} ≠ {b} (묶지 않음)인데 둘 다 '{rep(a)}'로 합쳐짐: {how(a)} / {how(b)}" for a, b in pairs(blocked) if rep(a) == rep(b)]
    out += [f"{a} = {b} (같은 말)인데 대표어가 갈라짐: {how(a)} / {how(b)}" for a, b in pairs(joined) if rep(a) != rep(b)]
    return out


def build(docs, extra=None, block=None):
    """순서: ① GROUPS(확정 묶음) → ② 예외 같은 말(SYNONYM_EXTRA) → ③ 접미사 규칙 → ④ 같은 기본어 규칙
    → 사슬(A→B→C)은 마지막 대표어로 정리. 반대말과 묶지 않을 쌍(SYNONYM_BLOCK)은 어떤 규칙으로도 묶지 않습니다.
    최종 사전에서 반대말·막을 쌍이 같은 대표어가 되거나 예외 같은 말이 갈라지면 SynonymConflict로 멈춥니다."""
    extra = SYNONYM_EXTRA if extra is None else extra
    blocked = {frozenset((kc.normalize(a), kc.normalize(b))) for a, b in (SYNONYM_BLOCK if block is None else block)}
    paren = getattr(docs, "paren", set())
    rows, skipped = {}, []

    def add(form, rep, basis, judge):
        # 반대말·막을 쌍은 묶지 않음. 이미 반대 방향(rep → form)이 있으면 넣지 않음 — 먼저 정한 쪽이 대표어 (순환 방지)
        if opposite(form, rep) or frozenset((form, rep)) in blocked:
            return
        if form != rep and form not in rows and rows.get(rep, {}).get("대표어") != form:
            rows[form] = {"표기": form, "대표어": rep, "근거": basis, "판정": judge}

    for basis, forms in GROUPS:
        forms = list(dict.fromkeys(kc.normalize(f) for f in forms))
        present = [f for f in forms if docs[f] > 0]
        if len(present) < 2:
            skipped.append(forms[0]); continue
        rep = max(present, key=lambda f: docs[f])
        for f in present:
            add(f, rep, basis, check(f, rep, paren))
    joined = set()
    for a, b in extra:
        form, rep = kc.normalize(a), kc.normalize(b)
        if docs[form] > 0 and docs[rep] > 0:
            add(form, rep, "팀 검수 (규칙 아님)", "예외: 같은 말")
            joined.add(frozenset((form, rep)))
    for form, base, suf in suffix_pairs(docs):
        add(form, base, f"접미사({suf})", "규칙: 접미사")
    for form, rep, base in same_base_pairs(docs):
        add(form, rep, f"같은 기본어({base})", "규칙: 같은 기본어")

    def final(w, seen=()):
        r = rows.get(w)
        return w if r is None or w in seen else final(r["대표어"], seen + (w,))
    out = []
    for form, r in rows.items():
        rep = final(r["대표어"], (form,))
        if rep != form and not opposite(form, rep):          # 사슬이 돌아오면(A→B→A) · 사슬 끝이 반대말이면 묶지 않음
            out.append({**r, "대표어": rep, "표기_기사수": docs[form], "대표어_기사수": docs[rep]})
    df = pd.DataFrame(out, columns=["표기", "대표어", "근거", "판정", "표기_기사수", "대표어_기사수"])
    bad = conflicts({r.표기: (r.대표어, r.판정) for r in df.itertuples(index=False)}, blocked, joined)
    if bad:
        raise SynonymConflict(f"막을 쌍·같은 말 예외와 맞지 않는 쌍 {len(bad)}개. build_synonyms.py의 SYNONYM_EXTRA·SYNONYM_BLOCK을 "
                              "정리한 뒤 다시 실행하세요.\n" + "\n".join(f"  {x}" for x in bad))
    return df, skipped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, default=RAW)
    ap.add_argument("--output", type=Path, default=OUT)
    args = ap.parse_args()
    df, skipped = build(topic_counts(args.input))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.output, index=False, encoding="utf-8-sig")
    print(f"규칙 {len(df)}개 (대표어 {df.대표어.nunique()}개) → {args.output} / 데이터에 없어 건너뛴 묶음 {skipped}")
    print("판정:", df.판정.value_counts().to_dict())


if __name__ == "__main__":
    main()
