"""data/news_keywords/의 사전이 지금 코드로 다시 만든 결과와 같은지 확인합니다 (사전을 손으로 고치지 않았는지, 코드를 바꾼 뒤 다시 만들었는지).

확인하는 것
1. topic_categories.csv = build_categories.py 결과
2. synonyms.csv         = build_synonyms.py 결과
3. (--hypernyms) hypernyms.csv = build_hypernyms.py 결과   ※ 임베딩 저장(word_vectors.npz)이 없으면 OpenAI 호출

실행: python backend/app/services/news_keywords/build/verify.py [--hypernyms]
"""
import argparse
import importlib.util
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = Path(__file__).resolve().parents[5] / "data" / "news_keywords"


def load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def same(label, a, b, key):
    """a(파일)와 b(코드 결과)를 key 기준으로 비교, 다른 줄을 출력하고 같으면 True."""
    a, b = a.astype(str), b.astype(str)
    m = a.merge(b, on=key, how="outer", suffixes=("_파일", "_코드"), indicator=True)
    cols = [c for c in a.columns if c not in key and c in b.columns]
    diff = m._merge != "both"
    for c in cols:
        diff |= m[c + "_파일"] != m[c + "_코드"]
    bad = m[diff]
    print(f"[{'일치' if bad.empty else '다름'}] {label}: 파일 {len(a)}줄 / 코드 {len(b)}줄" + ("" if bad.empty else f" / 다른 줄 {len(bad)}"))
    if not bad.empty:
        print(bad.head(15).to_string(index=False))
    return bad.empty


def read(name):
    p = DATA / name
    if not p.exists():
        print(f"[없음] {p} — 먼저 만드세요")
        return None
    return pd.read_csv(p, dtype=str, keep_default_na=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hypernyms", action="store_true", help="상위어 사전까지 다시 계산")
    args = ap.parse_args()
    ok = True
    cat_file = read("topic_categories.csv")
    ok &= cat_file is not None and same("대/중분류 topic_categories.csv", cat_file, load("build_categories").build(), ["주제어"])
    bs = load("build_synonyms")
    syn_file = read("synonyms.csv")
    try:
        syn_code, _ = bs.build(bs.topic_counts())
        ok &= syn_file is not None and same("동의어 synonyms.csv", syn_file, syn_code, ["표기"])
    except bs.SynonymConflict as e:
        print(f"[예외 목록 모순] 동의어 synonyms.csv: {e}"); ok = False
    if args.hypernyms:
        bh = load("build_hypernyms")
        hyp_file = read("hypernyms.csv")
        pairs, _ = bh.hc.build()
        hyp_code, _ = bh.build(pairs, bh.topic_counts(), bh.kc.load_synonyms())
        ok &= hyp_file is not None and same("상위어 hypernyms.csv", hyp_file, hyp_code, ["하위어"])
    print("결과:", "모두 일치" if ok else "다른 곳 있음 (위 목록 확인)")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
