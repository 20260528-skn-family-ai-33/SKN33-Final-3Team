"""동의어 목록 전체를 상위어·하위어 관계로 다시 나눈 검토표(상하위어_분류.xlsx)를 만듭니다. (실험용, 사전은 바꾸지 않음)

대상 쌍: synonyms.csv(사전) + 동의어_검수용.csv(1차 묶음·미반영 후보) + review/동의어_모델비교.xlsx의 새 후보(있으면)
관계 판정 순서 (코드 규칙, 위가 우선)
  1. 반대·짝      OPP 반대말 쌍 (수출↔수입, 구직↔구인 …) — 상하위 아님
  2. 글자 포함    짧은 말이 긴 말 안에 그대로 있으면 짧은 말 = 상위어
     · 범위(산업·시장·업)    뒤에 산업·시장·업만 붙음 (화장품 → 화장품시장)            — 지금 접미사 규칙
     · 같은 대상(범용 접미사) 뒤에 성·품·률·율·회·케어만 붙음 (의약 → 의약품)         — 상하위 아님, 같은 수준
     · 종류(앞에 수식어)      앞에만 붙음 (화장품 → 기초화장품, 자전거 → 전기자전거)    — 접두사 규칙
     · 세부 주제(뒤에 붙음)   뒤에 다른 말이 붙음 (전기차 → 전기차보조금)
     · 세부 주제(앞뒤 붙음)   앞뒤 모두 붙음
  3. 같은 뜻 묶음  build_synonyms.GROUPS(괄호·표기·LLM 판단)로 묶인 쌍 — 같은 수준 (EV = 전기차처럼 약어는 동시 등장이 한쪽으로 쏠려도 동의어)
  4. 동시 등장     글자가 겹치지 않으면 기사 동시 등장의 쏠림으로 방향을 봄 (kw_clean, 동의어 적용 전 표기)
                  P(넓은 말 | 좁은 말) ≥ 0.3 이고 P(좁은 말 | 넓은 말) ≤ 0.1, 넓은 말 기사가 3배 이상 → 좁은 말 = 하위어 (반려견 → 반려동물)
  5. 그 밖        같은 수준(판단 보류)

실행: python backend/app/services/news_keywords/build/hierarchy_candidates.py [--output 경로]
"""
import argparse
import importlib.util
from collections import defaultdict
from pathlib import Path

import pandas as pd
from openpyxl import Workbook

HERE = Path(__file__).resolve().parent
NK = HERE.parent
DATA = Path(__file__).resolve().parents[5] / "data" / "news_keywords"
OUT = DATA / "review" / "상하위어_분류.xlsx"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bs = _load("build_synonyms")
sc = _load("synonym_candidates")
rf = _load("review_files")

RANGE_SUFFIX = ("산업", "시장", "업")
SAME_SUFFIX = ("성", "품", "률", "율", "회", "케어")
HIER = {"범위(산업·시장·업)", "종류(앞에 수식어)", "세부 주제(뒤에 붙음)", "세부 주제(앞뒤 붙음)", "포함(동시 등장)", "팀 지정"}

# 팀 지정 (2026-10-21): 하위어를 이 상위어 아래로. 규칙 결과보다 우선하고, 하위어의 다른 상위어 연결은 뺌
MANUAL_CHILDREN = {
    "미용": ["미용기기", "미용의료기기"],
    "온라인쇼핑": ["온라인쇼핑시장", "온라인시장"],
    "의료": ["의료시장", "의료관광", "의료장비", "의료기기산업", "의료미용시장", "의료장비시장"],
    "의약품": ["의약", "의약산업", "의약품시장", "의약품산업"],
    "전기차": ["전기차인프라", "전기차충전"],
    "카페": ["카페시장", "커피머신", "커피머신시장"],
    "반려동물": ["펫용품", "펫용품시장", "펫케어", "펫케어시장"],
}
# 팀 지정 (2026-10-21): 상하위가 아니라 각각 따로 상위어인 쌍
MANUAL_SEPARATE = [("물", "물가"), ("발전", "발전소"), ("서비스", "서비스업"), ("소재", "신소재"), ("전기", "전기차"),
                   ("트렌드", "소비트렌드"), ("온라인", "온라인쇼핑"), ("뷰티", "화장품"), ("현대", "현대화"),
                   ("리튬", "리튬이온배터리"), ("석유", "석유화학"), ("스마트", "스마트폰")]
TEAM_NOTE = "팀 지정 (2026-10-21)"
CO_UP, CO_DOWN, CO_RATIO, CO_MIN = 0.3, 0.1, 3, 3


def article_sets(path=DATA / "news_keywords_clean.csv"):
    """표기 → 그 표기가 붙은 기사 번호 집합 (kw_clean: 동의어 적용 전)."""
    df = pd.read_csv(path, dtype=str, keep_default_na=False, usecols=["kw_clean"])
    out = defaultdict(set)
    for i, v in enumerate(df["kw_clean"]):
        for w in set(x for x in v.split("|") if x):
            out[w].add(i)
    return out


def source_pairs():
    """[(a, b, 출처, 팀 판정)] — 사전·검수 목록·새 후보의 모든 쌍 (방향 무관, 중복 제거는 나중에)."""
    dec = {frozenset((r["표기"], r["대표어 후보"])): r["판정"]
           for r in bs.load_decisions().to_dict("records")}
    rows = []
    syn = pd.read_csv(NK / "synonyms.csv", dtype=str, keep_default_na=False)
    for a, b, why, j in zip(syn["표기"], syn["대표어"], syn["근거"], syn["판정"]):
        # 접미사 규칙 줄의 대표어는 사슬 끝(인공지능산업 → AI)이라, 바로 위 기본 단어(인공지능)와의 쌍으로 봄
        suf = why[len("접미사("):-1] if why.startswith("접미사(") else ""
        if suf and a.endswith(suf) and a[: -len(suf)] != b:
            rows.append((a, a[: -len(suf)], f"사전({j})"))
        rows.append((a, b, f"사전({j})"))
    rv = pd.read_csv(DATA / "동의어_검수용.csv", dtype=str, keep_default_na=False)
    rows += [(a, b, "검수 목록(" + ("1차 묶음" if s.startswith("1차") else "미반영 후보") + ")")
             for a, b, s in zip(rv["표기"], rv["대표어 후보"], rv["현재 상태"])]
    cmp_path = DATA / "review" / "동의어_모델비교.xlsx"
    if cmp_path.exists():
        for sheet in ("KURE 새 후보", "OpenAI 새 후보"):
            try:
                d = pd.read_excel(cmp_path, sheet_name=sheet, dtype=str)
            except ValueError:
                continue
            rows += [(a, b, f"새 후보({sheet.split()[0]})") for a, b in zip(d["표기"], d["대표어 후보"])]
    return [(a, b, src, dec.get(frozenset((a, b)), "")) for a, b, src in rows if a and b and a != b]


def group_pairs():
    """GROUPS로 같은 묶음에 든 쌍 (같은 뜻으로 확정한 표기들)."""
    out = set()
    for _, forms in bs.GROUPS:
        forms = [bs.kc.normalize(f) for f in forms]
        out |= {frozenset((x, y)) for x in forms for y in forms if x != y}
    return out


def opposite(a, b):
    return any((x in a and y in b) or (y in a and x in b) for x, y in sc.OPP)


def relation(a, b, arts, groups):
    """(상위어, 하위어, 관계, 붙은 말, 근거). 같은 수준이면 상위어 = 기사 많은 쪽, 하위어 = 다른 쪽."""
    na, nb = len(arts.get(a, ())), len(arts.get(b, ()))
    big, small = (a, b) if na >= nb else (b, a)
    if opposite(a, b):
        return big, small, "반대·짝", "", "OPP 반대말 쌍"
    short, long_ = sorted((a, b), key=len)
    if short in long_ and short != long_:
        i = long_.find(short)
        pre, suf = long_[:i], long_[i + len(short):]
        if pre and suf:
            return short, long_, "세부 주제(앞뒤 붙음)", f"{pre}+…+{suf}", "글자 포함"
        if pre:
            return short, long_, "종류(앞에 수식어)", pre, "글자 포함 (접두사)"
        if suf in RANGE_SUFFIX:
            return short, long_, "범위(산업·시장·업)", suf, "글자 포함 (접미사 규칙)"
        if suf in SAME_SUFFIX:
            return short, long_, "같은 대상(범용 접미사)", suf, "글자 포함 (범용 접미사)"
        return short, long_, "세부 주제(뒤에 붙음)", suf, "글자 포함 (접미사)"
    if frozenset((a, b)) in groups:
        return big, small, "동의어(같은 수준)", "", "GROUPS 같은 뜻 묶음"
    both = len(arts.get(a, set()) & arts.get(b, set()))
    nbig, nsmall = max(na, nb), min(na, nb)
    if nsmall >= CO_MIN and both / nsmall >= CO_UP and both / nbig <= CO_DOWN and nbig >= CO_RATIO * nsmall:
        return big, small, "포함(동시 등장)", "", f"P(상위|하위)={both / nsmall:.2f}, P(하위|상위)={both / nbig:.2f}"
    return big, small, "같은 수준(판단 보류)", "", f"동시 등장 {both}건"


def apply_manual(pairs, arts):
    """팀 지정 반영: ① 따로 둘 쌍은 '별개(팀 지정)'로 ② 지정한 하위어는 그 상위어 아래로만 (다른 상위어 연결은 '다른 상위어로 이동')."""
    sep = {frozenset(p) for p in MANUAL_SEPARATE}
    key = [frozenset((a, b)) for a, b in zip(pairs["상위어"], pairs["하위어"])]
    pairs.loc[[k in sep for k in key], ["관계", "근거"]] = ["별개(팀 지정)", TEAM_NOTE + ": 각각 상위어"]
    for up, children in MANUAL_CHILDREN.items():
        for down in children:
            moved = (pairs["하위어"] == down) & (pairs["상위어"] != up) & pairs["관계"].isin(HIER)
            pairs.loc[moved, "근거"] = pairs.loc[moved, "관계"] + f" → {TEAM_NOTE}: '{up}' 아래로 이동"
            pairs.loc[moved, "관계"] = "다른 상위어로 이동(팀 지정)"
            both = len(arts.get(up, set()) & arts.get(down, set()))
            row = {"상위어": up, "하위어": down, "관계": "팀 지정", "붙은 말": "", "근거": TEAM_NOTE,
                   "상위어 기사수": len(arts.get(up, ())), "하위어 기사수": len(arts.get(down, ())), "함께 나온 기사": both}
            same = (pairs["상위어"] == up) & (pairs["하위어"] == down) | (pairs["상위어"] == down) & (pairs["하위어"] == up)
            if same.any():
                i = pairs.index[same][0]
                for k, v in row.items():
                    pairs.at[i, k] = v
            else:
                pairs = pd.concat([pairs, pd.DataFrame([{**row, "출처": "팀 지정", "팀 판정": ""}])], ignore_index=True)
    return pairs


def build(arts=None):
    arts = arts if arts is not None else article_sets()
    groups = group_pairs()
    seen, rows = {}, []
    for a, b, src, judge in source_pairs():
        key = frozenset((a, b))
        if key in seen:
            r = rows[seen[key]]
            r["출처"] = ", ".join(dict.fromkeys(r["출처"].split(", ") + [src]))
            r["팀 판정"] = r["팀 판정"] or judge
            continue
        up, down, rel, part, why = relation(a, b, arts, groups)
        both = len(arts.get(up, set()) & arts.get(down, set()))
        seen[key] = len(rows)
        rows.append({"상위어": up, "하위어": down, "관계": rel, "붙은 말": part, "근거": why,
                     "상위어 기사수": len(arts.get(up, ())), "하위어 기사수": len(arts.get(down, ())), "함께 나온 기사": both,
                     "출처": src, "팀 판정": judge})
    pairs = apply_manual(pd.DataFrame(rows), arts)
    hier = pairs[pairs["관계"].isin(HIER)]
    # 상위어의 상위어(사슬)를 따라 맨 위 상위어를 찾음 (상위어가 여럿이면 기사 많은 쪽)
    parent = {}
    for r in hier.sort_values("상위어 기사수").itertuples(index=False):
        parent[r.하위어] = r.상위어
    def root(w, seen=()):
        return w if w not in parent or w in seen else root(parent[w], seen + (w,))
    summary = []
    for up, g in hier.groupby("상위어"):
        g = g.sort_values("하위어 기사수", ascending=False)
        kinds = g["관계"].value_counts()
        summary.append({"상위어": up, "맨 위 상위어": root(up), "상위어 기사수": int(g["상위어 기사수"].iloc[0]),
                        "하위어 개수": len(g),
                        **{k: int(kinds.get(k, 0)) for k in sorted(HIER)},
                        "하위어 예시 10개": ", ".join(f"{w}({n})" for w, n in zip(g["하위어"][:10], g["하위어 기사수"][:10]))})
    summary = pd.DataFrame(summary).sort_values(["하위어 개수", "상위어 기사수"], ascending=False)
    return pairs, summary


def write(pairs, summary, out=OUT):
    count = pairs["관계"].value_counts().rename_axis("관계").reset_index(name="쌍 수")
    info = pd.DataFrame({"항목": ["목적", "대상", "상위어별", "전체 쌍", "같은 수준", "관계 판정", "동시 등장 기준", "주의", "팀 지정"], "내용": [
        "동의어(같은 말로 합치기) 대신 상위어·하위어(넓은 말 ⊃ 좁은 말)로 다시 나눠 보기 — 실험용, 사전(synonyms.csv)은 바꾸지 않음",
        "synonyms.csv + 동의어_검수용.csv + 모델비교 새 후보, 중복 제거 %d쌍" % len(pairs),
        "상위어마다 하위어 개수·관계 종류별 개수·하위어 예시 10개(기사 수 많은 순, 괄호 안은 기사 수)",
        "쌍마다 상위어·하위어·관계·붙은 말·근거·기사 수·함께 나온 기사·출처·팀 판정(10/19)",
        "상하위가 아닌 쌍 (동의어·같은 대상·반대·판단 보류)",
        "① 반대말 쌍 → ② 글자 포함(짧은 말 = 상위어, 붙은 말 위치·종류로 세분) → ③ GROUPS 같은 뜻 → ④ 동시 등장 쏠림 → ⑤ 판단 보류 "
        "→ 마지막에 팀 지정(MANUAL_CHILDREN·MANUAL_SEPARATE)이 덮어씀",
        f"P(넓은 말|좁은 말) ≥ {CO_UP}, P(좁은 말|넓은 말) ≤ {CO_DOWN}, 넓은 말 기사 {CO_RATIO}배 이상, 좁은 말 {CO_MIN}건 이상",
        "글자 포함은 뜻을 보지 않음 (현대 ⊃ 현대차처럼 고유명사도 상하위로 나옴). 동시 등장은 번역어도 하위어로 볼 수 있음 "
        "(코스메틱 → 화장품). 판단 보류 쌍은 사람·LLM 확인 필요",
        "2026-10-21: " + " / ".join(f"{u} ⊃ {', '.join(c)}" for u, c in MANUAL_CHILDREN.items())
        + " | 각각 상위어: " + ", ".join(f"{a}·{b}" for a, b in MANUAL_SEPARATE)]})
    wb = Workbook()
    rf.write_sheet(wb.active, info, [14, 120]); wb.active.title = "설명"
    rf.write_sheet(wb.create_sheet("관계별 개수"), count, [26, 10])
    rf.write_sheet(wb.create_sheet("상위어별"), summary, [16, 16, 11, 10, 12, 12, 12, 12, 12, 90])
    hier = pairs[pairs["관계"].isin(HIER)].sort_values(["상위어", "하위어 기사수"], ascending=[True, False])
    rf.write_sheet(wb.create_sheet("상하위 쌍"), hier, [16, 18, 20, 10, 30, 10, 10, 10, 30, 8])
    same = (pairs[~pairs["관계"].isin(HIER)].sort_values(["관계", "상위어 기사수"], ascending=[True, False])
            .rename(columns={"상위어": "말 A (기사 많은 쪽)", "하위어": "말 B", "상위어 기사수": "말 A 기사수", "하위어 기사수": "말 B 기사수"}))
    rf.write_sheet(wb.create_sheet("같은 수준·반대·보류"), same, [16, 18, 20, 10, 30, 10, 10, 10, 30, 8])
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return count


def main():
    parser = argparse.ArgumentParser(description="동의어 목록을 상위어·하위어로 다시 나눈 검토표")
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    pairs, summary = build()
    count = write(pairs, summary, args.output)
    print(count.to_string(index=False))
    print(f"상위어 {len(summary):,}개 · 쌍 {len(pairs):,}개 → {args.output}")


if __name__ == "__main__":
    main()
