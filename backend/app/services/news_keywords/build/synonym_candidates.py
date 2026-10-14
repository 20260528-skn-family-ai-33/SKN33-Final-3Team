"""④ 동의어 검수 파일(동의어_검수용.csv)을 만듭니다: 이미 반영한 규칙 + 아직 반영하지 않은 후보.

후보를 뽑는 방법
- 임베딩: 기사 10건 이상 주제어를 multilingual-e5-base로 벡터화 → 단어마다 가장 가까운 3개 → 코사인 유사도 0.93 이상
  (모델은 PC에 받아 둔 것만 쓰고 외부로 보내지 않음: HF_HUB_OFFLINE=1)
- 같은 기사 동시 등장: 기사 5건 이상 단어끼리, 함께 나온 기사 5건 이상 · 동시등장률(함께 ÷ 적은 쪽 기사 수) 50% 이상
  → 그중 임베딩 유사도 0.9 이상만 (커피·원두 같은 관련어를 거르기 위해)
  · 넓은 말(COOC_EXCLUDE, 예전 불용어 44개)은 아무 말과나 같이 나와 후보에서 뺌
- 1차 의견(규칙): 반대말 쌍(OPP) → "주의: 반대 뜻" / 짧은 말 + 접미사(SUFFIX) → "주의: 범위가 다를 수 있음"
  / 한쪽이 다른 쪽을 포함 → "주의: 한쪽이 다른 쪽을 포함" / 나머지 → "같은 뜻 후보"
- 표기·대표어 후보: 두 말 중 기사 수가 많은 쪽을 대표어 후보로
- 자동 O (팀 결정 2026-10-14): 근거가 임베딩(동시 등장 제외)이고 유사도 AUTO_O_SIM(0.96) 이상인 미반영 후보는
  "팀 확인(O/X)"를 O로 미리 채움 (의견 칸에 표시). 나머지 미반영 후보만 사람이 O/X

후보는 판단이 아니라 "볼 목록"입니다. 팀이 O를 준 것만 build_synonyms.py의 GROUPS에 넣습니다.
필요: sentence-transformers, intfloat/multilingual-e5-base (llm_env)

실행: python backend/app/services/news_keywords/build/synonym_candidates.py [--output 경로]
"""
import argparse
import os
from collections import Counter
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

NK = Path(__file__).resolve().parents[1]
DATA = Path(__file__).resolve().parents[5] / "data" / "news_keywords"
MODEL = "intfloat/multilingual-e5-base"
EMB_MIN_ARTICLES, EMB_TOP, EMB_THRESHOLD = 10, 3, 0.93
CO_MIN_ARTICLES, CO_MIN_TOGETHER, CO_MIN_RATE, CO_EMB_THRESHOLD = 5, 5, 0.5, 0.9
COOC_EXCLUDE = set("""수출 수입 무역 교역 수출입 투자 진출 투자진출 시장진출 진출전략 경제 산업 시장 기술 정책 소비 소비자 기업 한국 글로벌
서비스 비즈니스 혁신 발전 협력 성장 개발 변화 생산 프로젝트 동향 전망 현황 트렌드 경제전망 경제동향 시장동향 산업동향 인터뷰 참관기
전시회 박람회 전략 리스크""".split())
OPP = [("수출", "수입"), ("해외", "외국인"), ("금지", "허용"), ("상승", "하락"), ("증가", "감소"), ("B2B", "B2C"), ("지출", "수입"),
       ("온라인", "오프라인"), ("구직", "구인"), ("천연", "인조"), ("탄산", "수산화"), ("화력", "수력"), ("재사용", "재활용")]
SUFFIX = ("시장", "산업", "법", "율", "률", "정책", "기업", "업", "화", "성", "처리", "용품", "서비스", "기술", "관리", "인상")
PENDING = "미반영 (검수 후 반영)"
AUTO_O_SIM = 0.96  # 임베딩 후보 자동 O 기준 (동시 등장 후보는 적용 안 함)
AUTO_O_NOTE = f"자동 O: 임베딩 유사도 {AUTO_O_SIM} 이상"


def auto_mark(df):
    """임베딩 후보 중 유사도 AUTO_O_SIM 이상인 미반영 줄에 O를 미리 채웁니다."""
    sim = pd.to_numeric(df["임베딩 유사도"], errors="coerce")
    hit = (df["현재 상태"] == PENDING) & (df["근거"] == "임베딩") & (sim >= AUTO_O_SIM)
    df.loc[hit, "팀 확인(O/X)"] = "O"
    df.loc[hit, "의견"] = AUTO_O_NOTE
    return df


def opinion(a, b):
    for x, y in OPP:
        if (x in a and y in b) or (y in a and x in b):
            return "주의: 반대 뜻"
    short, long_ = sorted([a, b], key=len)
    if long_.startswith(short) and long_[len(short):] in SUFFIX:
        return "주의: 범위가 다를 수 있음 (" + long_[len(short):] + ")"
    if short in long_:
        return "주의: 한쪽이 다른 쪽을 포함"
    return "같은 뜻 후보"


def embedding_pairs(words, encode):
    emb = encode(words)
    sim = emb @ emb.T
    np.fill_diagonal(sim, -1)
    pairs = []
    for i, w in enumerate(words):
        for j in np.argsort(-sim[i])[:EMB_TOP]:
            if i < j or words[j] not in [p[0] for p in pairs if p[1] == w]:
                pairs.append((w, words[j], float(sim[i, j])))
    seen, out = set(), []
    for a, b, s in sorted(pairs, key=lambda x: -x[2]):
        k = tuple(sorted([a, b]))
        if k not in seen:
            seen.add(k); out.append((a, b, round(s, 3)))
    return out


def cooc_pairs(topic_lists, known):
    arts = [sorted({w for w in t if w and w not in COOC_EXCLUDE}) for t in topic_lists]
    df = Counter(w for a in arts for w in a)
    pair = Counter(p for a in arts for p in combinations([w for w in a if df[w] >= CO_MIN_ARTICLES], 2))
    rows = []
    for (a, b), c in pair.items():
        if c < CO_MIN_TOGETHER or frozenset((a, b)) in known:
            continue
        rate = c / min(df[a], df[b])
        if rate >= CO_MIN_RATE:
            rows.append((a, b, c, round(rate, 2)))
    return rows


def build(encode, clean_csv=DATA / "news_keywords_clean.csv", synonyms_csv=NK / "synonyms.csv"):
    res = pd.read_csv(clean_csv, dtype=str, keep_default_na=False, usecols=["kw_topic"])
    topic_lists = [s.split("|") for s in res.kw_topic]
    docs = Counter(w for t in topic_lists for w in set(t) if w)
    syn = pd.read_csv(synonyms_csv, dtype=str, keep_default_na=False)
    rows = [{"표기": r.표기, "대표어 후보": r.대표어, "근거": r.근거, "임베딩 유사도": "", "표기 기사수": r.표기_기사수,
             "대표어 기사수": r.대표어_기사수, "현재 상태": "1차 반영", "1차 의견": r.판정, "팀 확인(O/X)": "", "의견": ""}
            for r in syn.itertuples()]

    def cand(a, b, basis, sim, op):
        rep, form = (a, b) if docs[a] >= docs[b] else (b, a)
        return {"표기": form, "대표어 후보": rep, "근거": basis, "임베딩 유사도": sim, "표기 기사수": docs[form],
                "대표어 기사수": docs[rep], "현재 상태": PENDING, "1차 의견": op, "팀 확인(O/X)": "", "의견": ""}

    words = [w for w, c in docs.most_common() if c >= EMB_MIN_ARTICLES]
    for a, b, s in embedding_pairs(words, encode):
        if s >= EMB_THRESHOLD:
            rows.append(cand(a, b, "임베딩", s, opinion(a, b)))
    known = {frozenset((r["표기"], r["대표어 후보"])) for r in rows}
    co = cooc_pairs(topic_lists, known)
    vec = dict(zip(sorted({w for a, b, *_ in co for w in (a, b)}), encode(sorted({w for a, b, *_ in co for w in (a, b)}))))
    co = [(a, b, c, rate, round(float(vec[a] @ vec[b]), 3)) for a, b, c, rate in co]
    for a, b, c, rate, s in sorted([x for x in co if x[4] >= CO_EMB_THRESHOLD], key=lambda x: -x[4]):
        op = opinion(a, b)
        rows.append(cand(a, b, f"동시 등장 ({c}건, {rate:.0%})", s, "확인: 같은 뜻인지, 함께 쓰는 관련어인지" if op == "같은 뜻 후보" else op))
    return auto_mark(pd.DataFrame(rows))


def e5_encoder():
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(MODEL, device="cpu")
    return lambda ws: model.encode([f"query: {w}" for w in ws], batch_size=64, normalize_embeddings=True, show_progress_bar=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", type=Path, default=DATA / "동의어_검수용.csv")
    args = ap.parse_args()
    out = build(e5_encoder())
    out.to_csv(args.output, index=False, encoding="utf-8-sig")
    print(f"{len(out)}줄 → {args.output}")
    print(out["현재 상태"].value_counts().to_dict(), out["근거"].str.split(" ").str[0].value_counts().to_dict())


if __name__ == "__main__":
    main()
