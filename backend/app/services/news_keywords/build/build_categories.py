"""⑤ 대/중분류 표(data/news_keywords/topic_categories.csv)를 다시 만듭니다.

배정 순서와 분류 틀(TAX)은 공통 모듈 category_rules.py에 있습니다 (기사 처리 keyword_clean.py도 같은 classify()를 씀).
  1) 규칙(일치) → 2) 규칙(끝말) → 3) 규칙(포함) → 4) 규칙 수정 → 5) LLM 판단 → 6) 검수 필요
  추정(규칙(포함)·LLM 판단)도 확정합니다 (팀 결정 2026-10-22). 검수 파일은 읽지 않습니다.
대상: 정리된 주제어(kw_topic) 중 기사 MIN_ARTICLES(10)건 이상.
표에 없는 드문 주제어는 기사 처리 때 같은 규칙으로 분류하고, 그래도 못 정한 말은 미분류 목록(unclassified_topics.csv)에 남습니다.
기준·과정 설명은 CLASSIFICATION_RULES.md

실행: python backend/app/services/news_keywords/build/build_categories.py [--output 경로] [--min-articles 10]
"""
import argparse
import importlib.util
from collections import Counter
from pathlib import Path

import pandas as pd

NK = Path(__file__).resolve().parents[1]
DATA = Path(__file__).resolve().parents[5] / "data" / "news_keywords"
OUT = DATA / "topic_categories.csv"
MIN_ARTICLES = 10
COLUMNS = ["주제어", "대분류", "중분류", "배정방법", "규칙 결과", "기사수"]
_spec = importlib.util.spec_from_file_location("category_rules", NK / "category_rules.py")
cr = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(cr)
TAX, rule, assign, load_lists = cr.TAX, cr.rule, cr.assign, cr.load_lists


def build(clean_csv=DATA / "news_keywords_clean.csv", min_articles=MIN_ARTICLES):
    res = pd.read_csv(clean_csv, dtype=str, keep_default_na=False, usecols=["kw_topic"])
    docs = Counter(w for s in res.kw_topic for w in set(s.split("|")) if w)
    fixes, llm = load_lists()
    rows = []
    for w, n in docs.items():
        if n >= min_articles:
            big, mid, how, rule_txt = cr.classify(w, fixes, llm)
            rows.append({"주제어": w, "대분류": big, "중분류": mid, "배정방법": how, "규칙 결과": rule_txt, "기사수": n})
    df = pd.DataFrame(rows, columns=COLUMNS)  # 대상이 없어도 같은 칸의 빈 표
    return df.sort_values(["기사수", "주제어"], ascending=[False, True]).reset_index(drop=True).astype({"기사수": int})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", type=Path, default=OUT)
    ap.add_argument("--min-articles", type=int, default=MIN_ARTICLES)
    args = ap.parse_args()
    df = build(min_articles=args.min_articles)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.output, index=False, encoding="utf-8-sig")
    print(f"{len(df)}개 → {args.output}")
    print(df.배정방법.value_counts().to_dict())


if __name__ == "__main__":
    main()
