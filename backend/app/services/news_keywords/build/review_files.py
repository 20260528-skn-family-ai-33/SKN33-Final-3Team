"""팀에 나눠 주는 검수 파일을 만듭니다 (나눠 준 파일 = 이 코드의 결과).
팀 확인 칸: O(맞음) / X(틀림) / △(애매 — 반영하지 않고 회의에서 결정)

- 동의어_검수용.xlsx: 동의어_검수용.csv(synonym_candidates.py 결과)를 칸 너비·O/X 드롭다운이 있는 엑셀로
- 동의어_미반영_검수용.xlsx: 미반영 후보 303쌍 전체 (자동 O 줄도 포함, 하나씩 O/X/△)
- 동의어_랜덤100_검수용.xlsx: 미반영 후보에서 무작위 100개 (시드 고정 → 다시 뽑아도 같은 100개)
  · 회의 결정: 100개를 확인해 문제가 없으면 "임베딩 기준 이상 → 자동 동의어" 규칙으로 처리
- 대중분류_검수용.xlsx: topic_categories.csv 중 사람이 볼 줄(검수 필요 · 규칙(포함) · 규칙 수정 · LLM 판단) + 왜 붙었나 + 예시 기사 제목
- 미분류_예시기사.xlsx: 분야를 정하지 못한 주제어 전체(keyword_clean.py가 만든 unclassified_topics.csv, 기사 수 많은 순)와
  단어마다 최신 기사 최대 10건 (회의 결정: 예시 기사를 보고 분야를 정함). 빈도로 빼지 않고 "전체 기사 수"로 걸러 봄

실행: python backend/app/services/news_keywords/build/review_files.py [--out-dir 폴더]
"""
import argparse
import csv
import importlib.util
from collections import defaultdict
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

HERE = Path(__file__).resolve().parent
NK = HERE.parent
ROOT = Path(__file__).resolve().parents[5]
DATA = ROOT / "data" / "news_keywords"
RAW = ROOT / "data" / "news" / "news_raw_20220101_20261013.csv"
SEED = 20261014
EXAMPLES = 10
spec = importlib.util.spec_from_file_location("build_categories", HERE / "build_categories.py")
bc = importlib.util.module_from_spec(spec); spec.loader.exec_module(bc)

HEAD = PatternFill("solid", fgColor="DDEBF7")
TEAM = PatternFill("solid", fgColor="F0FDF4")


def write_sheet(ws, df, widths, ox_col=None, wrap_cols=()):
    ws.append(list(df.columns))
    for row in df.itertuples(index=False):
        ws.append(list(row))
    for c in ws[1]:
        c.font = Font(bold=True); c.fill = HEAD
    ws.freeze_panes = "A2"; ws.auto_filter.ref = ws.dimensions
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    if ox_col:
        col = get_column_letter(list(df.columns).index(ox_col) + 1)
        dv = DataValidation(type="list", formula1='"O,X,△"', allow_blank=True); ws.add_data_validation(dv)
        dv.add(f"{col}2:{col}{ws.max_row}")
        for r in range(2, ws.max_row + 1):
            ws[f"{col}{r}"].fill = TEAM
    for name in wrap_cols:
        col = list(df.columns).index(name) + 1
        for r in range(2, ws.max_row + 1):
            ws.cell(r, col).alignment = Alignment(wrap_text=True, vertical="top")


def read_synonym_review(path=DATA / "동의어_검수용.csv"):
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    for c in ("표기 기사수", "대표어 기사수"):
        df[c] = df[c].astype(int)
    return df


SYN_WIDTHS = [22, 20, 22, 12, 10, 11, 20, 34, 12, 30]


def synonym_files(out_dir):
    rv = read_synonym_review()
    wb = Workbook(); write_sheet(wb.active, rv, SYN_WIDTHS, "팀 확인(O/X)"); wb.active.title = "동의어 검수"
    wb.save(out_dir / "동의어_검수용.xlsx")
    rest = remaining(rv)
    wb = Workbook(); write_sheet(wb.active, rest, SYN_WIDTHS, "팀 확인(O/X)"); wb.active.title = "미반영 검수"
    wb.save(out_dir / "동의어_미반영_검수용.xlsx")
    s100 = random100(rv)
    wb = Workbook(); write_sheet(wb.active, s100, SYN_WIDTHS, "팀 확인(O/X)"); wb.active.title = "랜덤100"
    wb.save(out_dir / "동의어_랜덤100_검수용.xlsx")
    return rv, s100


def remaining(rv):
    """미반영 후보 전체."""
    return rv[rv["현재 상태"].str.startswith("미반영")]


def random100(rv):
    return rv[rv["현재 상태"].str.startswith("미반영")].sample(100, random_state=SEED).sort_index()


def recent_titles(res, n=2):
    titles = defaultdict(list)
    for t, d, title in sorted(zip(res.kw_topic, res.othbcDt, res.newsTitl), key=lambda x: x[1], reverse=True):
        for w in set(t.split("|")):
            if w and len(titles[w]) < n:
                titles[w].append(f"[{d[:10]}] {title}")
    return titles


def category_rows():
    cat = pd.read_csv(NK / "topic_categories.csv", dtype=str, keep_default_na=False)
    with open(NK / "category_rule_fixes.csv", encoding="utf-8-sig", newline="") as fh:
        fix_why = {r["주제어"]: r["이유"] for r in csv.DictReader(fh)}
    order = {"검수 필요": 0, "규칙(포함)": 1, "규칙 수정": 2, "LLM 판단": 3, "팀 검수": 4}
    rv = cat[cat.배정방법.isin(order)].copy()
    rv["_o"] = rv.배정방법.map(order); rv["_n"] = rv.기사수.astype(int)
    rv = rv.sort_values(["_o", "_n", "주제어"], ascending=[True, False, True])

    def why(w, how, rule_txt):
        if how == "규칙(포함)":
            for big, mid, keys in bc.TAX:
                hit = [k for k in keys.split() if len(k) >= 2 and k.upper() in w.upper()]
                if hit:
                    return f'"{hit[0]}"을(를) 포함해서 → {mid}'
        if w in fix_why:
            return f"규칙 결과({rule_txt}) 수정: {fix_why[w]}"
        if how == "LLM 판단":
            return "규칙에 안 걸려 LLM이 뜻으로 배정"
        if how == "팀 검수":
            head = "팀 검수에서 미분류로 확정" if w in bc.load_decisions() and bc.load_decisions()[w][0] == "미분류" else "팀 검수에서 바꾼 분류"
            return head + (f" (원래 규칙 결과: {rule_txt})" if rule_txt else "")
        return "규칙에도 안 걸리고 뜻이 넓어 분야를 정하지 못함"
    rv["왜"] = [why(w, h, t) for w, h, t in zip(rv.주제어, rv.배정방법, rv["규칙 결과"])]
    return rv


def category_file(out_dir, res):
    rv = category_rows()
    titles = recent_titles(res)
    frame = [f"{b} > {m}" for b, m, _ in bc.TAX] + ["미분류로 둠"]
    n_need, n_inc, n_fix, n_llm, n_team = [(rv.배정방법 == k).sum() for k in ("검수 필요", "규칙(포함)", "규칙 수정", "LLM 판단", "팀 검수")]
    dec_path = NK / "category_decisions.csv"
    dec = pd.read_csv(dec_path, dtype=str, keep_default_na=False).set_index("주제어") if dec_path.exists() else pd.DataFrame()
    def prior(w, col):
        if w not in dec.index:
            return ""
        d = dec.loc[w]
        if col == "판정": return d["판정"]
        if col == "분류": return "" if d["판정"] != "X" else ("미분류로 둠" if d["대분류"] == "미분류" else f'{d["대분류"]} > {d["중분류"]}')
        return d["근거"]
    guide = pd.DataFrame([
        ("이 파일은?", f"대/중분류(topic_categories.csv) 중 사람이 확인해야 하는 {len(rv)}줄. 규칙(일치)은 확실해서 제외."),
        ("무엇을 하나", "지금 분류가 맞으면 '팀 확인'에 O, 틀리면 X + '맞는 분류'를 드롭다운에서 선택, 애매하면 △ (반영하지 않고 회의에서 결정)"),
        ("판단 기준", "이 주제어가 들어간 기사가 주로 어떤 분야 이야기인가? ('예시 기사 제목' 참고)"),
        (f"검수 필요 ({n_need}줄)", "분야를 못 정한 말. 맞는 분류를 고르거나 '미분류로 둠' (미분류_예시기사.xlsx 참고)"),
        (f"규칙(포함) ({n_inc}줄)", "분류 키워드를 글자로 포함해 자동으로 붙은 것. 글자만 겹친 실수가 있을 수 있음"),
        (f"규칙 수정 ({n_fix}줄)", "규칙 결과가 글자만 겹쳐 틀려서 바로잡은 것 (category_rule_fixes.csv)"),
        (f"LLM 판단 ({n_llm}줄)", "규칙에 하나도 안 걸려 LLM이 뜻으로 배정한 것 (category_llm.csv)"),
        (f"팀 검수 ({n_team}줄)", "팀이 정한 분류가 반영된 것: X로 바꾼 분류 + 미분류로 확인한 말 (category_decisions.csv)"),
        ("검수 상태 칸", "이미 팀이 본 줄은 O/X·맞는 분류·의견이 채워져 있음 → 빈 줄(새로 생긴 주제어)만 검수하면 됨"),
        ("끝나면", "import_category_review.py --file 이 파일 → category_decisions.csv → build_categories.py 실행 (팀 검수가 규칙·LLM 판단보다 우선)"),
    ], columns=["항목", "내용"])
    df = pd.DataFrame({"주제어": rv.주제어, "기사수": rv._n, "배정방법": rv.배정방법, "지금 대분류": rv.대분류, "지금 중분류": rv.중분류,
                       "왜 이렇게 붙었나": rv.왜, "예시 기사 제목 (최근 2건)": ["\n".join(titles.get(w, [])) for w in rv.주제어],
                       "검수 상태": ["팀 검수 완료" if w in dec.index else "새로 검수 필요" for w in rv.주제어],
                       "팀 확인(O/X)": [prior(w, "판정") for w in rv.주제어], "맞는 분류 (X일 때 선택)": [prior(w, "분류") for w in rv.주제어],
                       "의견": [prior(w, "근거") for w in rv.주제어]})
    wb = Workbook()
    write_sheet(wb.active, guide, [18, 120]); wb.active.title = "설명"
    ws = wb.create_sheet("검수"); write_sheet(ws, df, [16, 8, 11, 15, 22, 40, 70, 14, 11, 34, 40], "팀 확인(O/X)", ["예시 기사 제목 (최근 2건)", "의견"])
    dv = DataValidation(type="list", formula1=f"=분류틀!$C$2:$C${len(frame) + 1}", allow_blank=True); ws.add_data_validation(dv)
    dv.add(f"J2:J{ws.max_row}")
    fills = {"검수 필요": "FDE2E2", "규칙(포함)": "FEF3C7", "규칙 수정": "FFEDD5", "LLM 판단": "E0F2FE", "팀 검수": "DCFCE7"}
    for r in range(2, ws.max_row + 1):
        ws.cell(r, 3).fill = PatternFill("solid", fgColor=fills[ws.cell(r, 3).value])
        if ws.cell(r, 8).value == "새로 검수 필요":
            ws.cell(r, 8).fill = PatternFill("solid", fgColor="FDE68A")
    fr = pd.DataFrame([(b, m, f"{b} > {m}", " ".join(k.split()[:8])) for b, m, k in bc.TAX] + [("", "", "미분류로 둠", "분야를 정하기 어려운 넓은 말")],
                      columns=["대분류", "중분류", "선택값 (드롭다운)", "대표 키워드 (일부)"])
    write_sheet(wb.create_sheet("분류틀"), fr, [16, 26, 42, 70])
    wb.save(out_dir / "대중분류_검수용.xlsx")
    return df


def unclassified_rows(path=DATA / "unclassified_topics.csv"):
    """keyword_clean.py가 만든 미분류 주제어 전체 목록 (기사 수 많은 순, 빈도로 빼지 않음)."""
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def unclassified_file(out_dir, res):
    un = unclassified_rows()
    raw = pd.read_csv(RAW, usecols=["bbstxSn", "indstCl"], dtype=str, keep_default_na=False).drop_duplicates("bbstxSn")
    res = res.merge(raw, on="bbstxSn", how="left").sort_values(["othbcDt", "bbstxSn"], ascending=False)
    by_word = defaultdict(list)
    for r in res.itertuples():
        for t in set(r.kw_topic.split("|")):
            if t and len(by_word[t]) < EXAMPLES:
                by_word[t].append(r)
    rows = []
    for w in un.주제어:
        for i, r in enumerate(by_word.get(w, []), 1):
            rows.append({"단어": w, "번호": i, "게시일": r.othbcDt[:10], "국가": r.natn, "제목": r.newsTitl, "원본 키워드": r.kwrd,
                         "같이 나온 주제어": r.kw_topic.replace("|", ", "), "다른 주제어의 분야": r.kw_category.replace("|", ", "),
                         "KOTRA 산업분류": r.indstCl, "기사ID": r.bbstxSn})
    summ = pd.DataFrame({"단어": un.주제어, "전체 기사 수": un.기사수.astype(int), "미분류 이유": un["미분류 이유"],
                         "정한 분류 (대 > 중)": "", "의견": ""})
    wb = Workbook()
    write_sheet(wb.active, summ, [14, 12, 40, 34, 40])
    wb.active.title = f"단어 {len(summ)}개"
    art = pd.DataFrame(rows, columns=["단어", "번호", "게시일", "국가", "제목", "원본 키워드", "같이 나온 주제어", "다른 주제어의 분야",
                                      "KOTRA 산업분류", "기사ID"])
    write_sheet(wb.create_sheet("예시 기사"), art, [10, 6, 11, 10, 50, 45, 45, 45, 18, 12])
    wb.save(out_dir / "미분류_예시기사.xlsx")
    return art


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", type=Path, default=DATA / "review")
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    res = pd.read_csv(DATA / "news_keywords_clean.csv", dtype=str, keep_default_na=False)
    rv, s100 = synonym_files(args.out_dir)
    cat = category_file(args.out_dir, res)
    art = unclassified_file(args.out_dir, res)
    print(f"동의어 {len(rv)}줄 / 랜덤 {len(s100)}개 / 분류 검수 {len(cat)}줄 / 미분류 예시 {art.단어.nunique()}개 단어 {len(art)}건 → {args.out_dir}")


if __name__ == "__main__":
    main()
