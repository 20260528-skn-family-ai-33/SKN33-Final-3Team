"""팀이 채운 대/중분류 검수 엑셀(대중분류_검수용.xlsx "검수" 시트)을 category_decisions.csv로 옮깁니다.

- O: 지금 분류가 맞음(확인) · X: "맞는 분류" 칸의 분류로 바꿈 · "미분류로 둠"은 미분류로 확정
- 맞는 분류가 분류 틀(대 8 · 중 23)에 없는 값이면 알려 주고 멈춤
- 예전 버전 엑셀로 검수했어도 됨: 주제어 기준으로 옮기고, 지금 분류표에 없는 주제어(동의어로 합쳐진 말)는 따로 알려 줌
- build_categories.py가 이 파일의 X를 규칙·LLM 판단보다 먼저 적용 (배정방법 "팀 검수")

실행: python backend/app/services/news_keywords/build/import_category_review.py --file 대중분류_검수용.xlsx [--file 다른사람.xlsx]
"""
import argparse
import importlib.util
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
NK = HERE.parent
OUT = NK / "category_decisions.csv"
spec = importlib.util.spec_from_file_location("build_categories", HERE / "build_categories.py")
bc = importlib.util.module_from_spec(spec); spec.loader.exec_module(bc)
COL, PICK = "팀 확인(O/X)", "맞는 분류 (X일 때 선택)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", action="append", required=True)
    ap.add_argument("--output", type=Path, default=OUT)
    args = ap.parse_args()
    df = pd.concat([pd.read_excel(f, sheet_name="검수", dtype=str, keep_default_na=False).assign(파일=Path(f).name) for f in args.file])
    df = df[df[COL].isin(["O", "X"])]
    frame = {f"{b} > {m}": (b, m) for b, m, _ in bc.TAX} | {"미분류로 둠": ("미분류", "미분류")}
    bad = False
    rows = []
    for r in df.itertuples(index=False):
        d = r[df.columns.get_loc(COL)]
        pick = r[df.columns.get_loc(PICK)].strip()
        big, mid = r[df.columns.get_loc("지금 대분류")], r[df.columns.get_loc("지금 중분류")]
        if d == "X":
            if pick not in frame:
                print("맞는 분류가 분류 틀에 없음:", r.주제어, repr(pick)); bad = True; continue
            big, mid = frame[pick]
        rows.append({"주제어": r.주제어, "판정": d, "대분류": big, "중분류": mid,
                     "원래 분류": f'{r[df.columns.get_loc("지금 대분류")]} > {r[df.columns.get_loc("지금 중분류")]}',
                     "근거": r[df.columns.get_loc("의견")].replace("\n", " ").strip()})
    out = pd.DataFrame(rows)
    dup = out.groupby("주제어").agg(n=("대분류", "nunique"), m=("중분류", "nunique"))
    for w in dup[(dup.n > 1) | (dup.m > 1)].index:
        print("같은 주제어를 다르게 표시:", w, out[out.주제어 == w][["판정", "대분류", "중분류"]].values.tolist()); bad = True
    if bad:
        sys.exit("위 항목을 엑셀에서 정리한 뒤 다시 실행하세요.")
    out = out.drop_duplicates("주제어")
    out.to_csv(args.output, index=False, encoding="utf-8-sig")
    now = set(pd.read_csv(NK / "topic_categories.csv", dtype=str, keep_default_na=False).주제어)
    gone = out[~out.주제어.isin(now)]
    print(f"{len(out)}줄 → {args.output}", out.판정.value_counts().to_dict())
    if len(gone):
        print(f"지금 분류표에 없는 주제어 {len(gone)}개 (동의어로 합쳐져 대표어 분류를 따름) — X인 것:",
              gone[gone.판정 == "X"][["주제어", "대분류", "중분류"]].values.tolist())


if __name__ == "__main__":
    main()
