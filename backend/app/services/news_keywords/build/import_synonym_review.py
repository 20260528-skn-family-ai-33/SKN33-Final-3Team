"""팀이 O/X/△를 채운 동의어 검수 엑셀을 synonym_decisions.csv로 옮깁니다 (build_synonyms.py가 읽음).

- 여러 사람이 나눠 채운 파일을 함께 넣을 수 있음: --file 파일 --rows 2-150 --file 파일 --rows 151-304
  (rows는 엑셀 행 번호, 생략하면 전체)
- 같은 쌍을 두 사람이 다르게 표시했거나, 한 표기에 O가 두 개면 알려 주고 멈춤 (먼저 정리해야 함)

실행: python backend/app/services/news_keywords/build/import_synonym_review.py --file A.xlsx --rows 2-150 --file B.xlsx --rows 151-304
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

NK = Path(__file__).resolve().parents[1]
OUT = NK / "synonym_decisions.csv"
COL = "팀 확인(O/X)"


def read(path, rows=None):
    df = pd.read_excel(path, dtype=str, keep_default_na=False)
    df["행"] = df.index + 2
    if rows:
        lo, hi = map(int, rows.split("-"))
        df = df[(df.행 >= lo) & (df.행 <= hi)]
    return df[df[COL].isin(["O", "X", "△"])][["표기", "대표어 후보", COL, "의견", "행"]].assign(파일=Path(path).name)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", action="append", required=True)
    ap.add_argument("--rows", action="append")
    ap.add_argument("--output", type=Path, default=OUT)
    args = ap.parse_args()
    rows = args.rows or [None] * len(args.file)
    df = pd.concat([read(f, r) for f, r in zip(args.file, rows)], ignore_index=True)
    bad = False
    conflict = df.groupby(["표기", "대표어 후보"])[COL].nunique()
    for k in conflict[conflict > 1].index:
        print("같은 쌍을 다르게 표시:", k, df[(df.표기 == k[0]) & (df["대표어 후보"] == k[1])][[COL, "파일", "행"]].values.tolist()); bad = True
    o = df[df[COL] == "O"]
    for w, g in o.groupby("표기"):
        if g["대표어 후보"].nunique() > 1:
            print("한 표기에 O가 두 개:", w, g[["대표어 후보", "파일", "행"]].values.tolist()); bad = True
    if bad:
        sys.exit("위 항목을 엑셀에서 정리한 뒤 다시 실행하세요.")
    out = df.drop_duplicates(["표기", "대표어 후보"]).rename(columns={COL: "판정"})[["표기", "대표어 후보", "판정", "의견"]]
    out.to_csv(args.output, index=False, encoding="utf-8-sig")
    print(f"{len(out)}줄 → {args.output}", out.판정.value_counts().to_dict())
    notes = out[out.의견.str.strip() != ""]
    notes = notes[~notes.의견.str.startswith("자동 O")]
    if len(notes):
        print("의견 칸 확인 필요 (대표어 변경·추가 묶음 등):"); print(notes.to_string(index=False))


if __name__ == "__main__":
    main()
