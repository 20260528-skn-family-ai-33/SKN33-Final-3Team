"""사전·검수 파일이 코드로 다시 만든 결과와 같은지 확인합니다 (회의: "나눠준 거랑 코드 실행했을 때 일치하는지").

확인하는 것
1. topic_categories.csv  = build_categories.py 결과
2. synonyms.csv          = build_synonyms.py 결과
3. (--candidates) 동의어_검수용.csv = synonym_candidates.py 결과   ※ sentence-transformers 필요 (llm_env)
4. (--shared 폴더) 나눠 준 엑셀의 "코드가 채운 칸"이 지금 코드 결과와 같은지 (팀이 채운 O/X·의견 칸은 비교하지 않음)

실행: python backend/app/services/news_keywords/build/verify.py [--candidates] [--shared "C:/.../최종 프로젝트"]
"""
import argparse
import importlib.util
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
NK = HERE.parent
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", action="store_true", help="동의어 후보(임베딩)까지 다시 계산")
    ap.add_argument("--shared", type=Path, help="팀에 나눠 준 검수 파일이 있는 폴더")
    args = ap.parse_args()
    ok = True
    cat_code = load("build_categories").build()
    cat_file = pd.read_csv(NK / "topic_categories.csv", dtype=str, keep_default_na=False)
    ok &= same("대/중분류 topic_categories.csv", cat_file, cat_code, ["주제어"])
    bs = load("build_synonyms")
    syn_code, _ = bs.build(bs.topic_counts())
    ok &= same("동의어 synonyms.csv", pd.read_csv(NK / "synonyms.csv", dtype=str, keep_default_na=False), syn_code, ["표기"])
    rv_file = pd.read_csv(DATA / "동의어_검수용.csv", dtype=str, keep_default_na=False)
    if args.candidates:
        sc = load("synonym_candidates")
        ok &= same("동의어 후보 동의어_검수용.csv", rv_file, sc.build(sc.e5_encoder()), ["표기", "대표어 후보"])
    if args.shared:
        rf = load("review_files")
        filled = ["팀 확인(O/X)", "의견", "맞는 분류 (X일 때 선택)", "정한 분류 (대 > 중)"]
        code_cols = lambda df: df[[c for c in df.columns if c not in filled and not c.startswith("예시 기사")]]
        rv = rf.read_synonym_review()
        checks = [("동의어_검수용.xlsx", None, rv, ["표기", "대표어 후보"]),
                  ("동의어_랜덤100_검수용.xlsx", None, rf.random100(rv), ["표기", "대표어 후보"])]
        cat = rf.category_rows()
        checks.append(("대중분류_검수용.xlsx", "검수", pd.DataFrame({"주제어": cat.주제어, "배정방법": cat.배정방법,
                       "지금 대분류": cat.대분류, "지금 중분류": cat.중분류}), ["주제어"]))
        un = cat_file[cat_file.대분류 == "미분류"][["주제어"]].rename(columns={"주제어": "단어"})
        checks.append(("미분류_예시기사.xlsx", 0, un, ["단어"]))
        for name, sheet, code, key in checks:
            p = args.shared / name
            if not p.exists():
                print(f"[없음] {name}"); ok = False; continue
            try:
                f = pd.read_excel(p, sheet_name=sheet if sheet is not None else 0, dtype=str, keep_default_na=False)
            except PermissionError:
                print(f"[확인 못 함] {name}: 엑셀에서 열려 있음 — 닫고 다시 실행"); ok = False; continue
            cols = [c for c in code_cols(code).columns if c in f.columns]
            ok &= same(f"나눠 준 {name}", f[cols], code[cols], key)
    print("결과:", "모두 일치" if ok else "다른 곳 있음 (위 목록 확인)")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
