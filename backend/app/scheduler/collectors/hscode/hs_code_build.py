"""관세청 HS부호 단위별품목명 xlsx로 HS 코드 마스터 CSV(2·4·6·10단위)를 만듭니다.

- 2·4·10단위: xlsx 시트의 공식 한글 품목명을 그대로 씁니다.
- 6단위: xlsx 6단위 시트에 일부만 있어 10단위 코드 앞 6자리로 목록을 만듭니다.
  이름은 6단위 시트 → 10단위 XXXXXX0000 행 → (XXXX00이면) 4단위 이름 순으로 찾고,
  name_ko = 'LLM 요약(3~5단어)|6단위명 원본' 형태로 저장합니다.
  요약은 4·5·6단위 이름을 모두 LLM에 넣어 만들므로 '기타' 같은 6단위명도 뜻이 통하게 됩니다.
  5단어를 넘는 요약은 '최대한 짧게'로 한 번 더 요청하고, 그래도 넘으면 그 결과를 그대로 저장합니다.
- 관세청 특수코드 2424·242400(이사화물)은 4단위 시트에 없어 직접 추가합니다.
- reference/hs6_summary_overrides_<hs_ver>.csv의 요약은 LLM 요약보다 우선합니다(요약이 겹치는 코드 등 수동 수정분).
- hs_ver를 주지 않으면 xlsx 파일명의 판 날짜(YYYYMMDD)로 정합니다(hs_ver_for). 요약 캐시·수동 요약은 hs_ver별로 따로 둡니다.
- 6단위 원본은 DB에서 original_name_ko(TEXT)로 나뉘어 들어가므로 줄이지 않고 관세청 원문 그대로 둡니다.
  NAME_MAX(500자) 검사는 DB name_ko에 들어가는 값(6단위 요약, 2·4·10단위 이름)에만 적용합니다.
- 진행 상황은 화면과 결과 폴더의 logs/에 함께 기록합니다.
"""
import argparse
import csv
import json
import logging
import re
import time
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from dotenv import dotenv_values
from openai import OpenAI

BASE = Path(__file__).resolve().parent
# 관세청 원본 xlsx를 이 폴더에 hs_names_YYYYMMDD.xlsx로 두면 가장 최신 날짜 파일을 기본으로 씁니다.
REFERENCE_DIR = BASE / "reference"
ENV_PATH = Path(__file__).resolve().parents[5] / ".env"
ENV_KEY = "openai_api_key"
FIELDS = ["hs_code", "depth", "name_ko", "source_edition", "hs_ver"]
SEP = "|"  # 관세청 품목명·요약에 쓰이지 않는 기호 ('::'는 '[예: ...]'의 콜론과 헷갈려 바꿈)
KR_SPECIAL = {"2424": "이사화물", "242400": "이사화물"}
NAME_MAX = 500  # DB name_ko VARCHAR(500). 6단위는 요약 부분만 해당
OUTPUT_DIR = BASE / "hscode_outputs"
DEFAULT_MODEL = "gpt-4.1-mini"
BATCH = 40
RETRIES = 3
PROMPT = """너는 관세 품목 분류 전문가다. 각 HS 6단위 품목의 상위 분류명과 품목명을 보고,
무슨 물품인지 바로 알 수 있는 한국어 품목명을 3~5단어로 요약해라.
'기타'만 쓰지 말고 상위 분류의 물품명을 넣어라. 예: '살아 있는 말ㆍ당나귀 > 말 > 기타' → '번식용 외 살아 있는 말'.
입력은 {"코드": "분류명 > 분류명 > 품목명"} JSON이고, 출력은 같은 코드를 키로 한 {"코드": "요약"} JSON만 내라."""
RETRY_PROMPT = """너는 관세 품목 분류 전문가다. 아래 HS 6단위 품목의 요약이 너무 길다.
무슨 물품인지 알 수 있는 핵심만 남겨 최대한 짧게, 가능하면 5단어 이내의 한국어로 다시 요약해라.
입력은 {"코드": "분류명 > 분류명 > 품목명 (기존 요약: ...)"} JSON이고, 출력은 같은 코드를 키로 한 {"코드": "요약"} JSON만 내라."""
log = logging.getLogger("hs_code_build")


def edition_date(path):
    """관세청 xlsx 파일명의 판 날짜(YYYYMMDD). 예: hs_names_20260101.xlsx → '20260101'"""
    found = re.search(r"(\d{8})", Path(path).stem)
    return found.group(1) if found else None


def hs_ver_for(date):
    """날짜(YYYYMMDD 문자열이나 date)에 적용되는 HS 판.
    HS2017(2017~2021) → HS2022(2022~2027) → HS2028(2028~) 이후 5년 주기(HS2033, HS2038 …)로 봅니다.
    ponytail: HS2033 이후 주기는 가정값. WCO가 다른 연도로 발표하면 여기만 고치면 됩니다."""
    year = int(str(date)[:4])
    if year >= 2028:
        return f"HS{2028 + (year - 2028) // 5 * 5}"
    return "HS2022" if year >= 2022 else "HS2017"


def hs_period(hs_ver):
    """HS 판이 적용되는 기간(양끝 포함). HS2017: 2017~2021, HS2022: 2022~2027, HS2028부터 5년씩 (hs_ver_for와 같은 가정)."""
    year = int(hs_ver[2:])
    end = {2017: 2021, 2022: 2027}.get(year, year + 4)
    return date(year, 1, 1), date(end, 12, 31)


def latest_reference():
    found = sorted(REFERENCE_DIR.glob("hs_names_*.xlsx"))
    if not found:
        raise FileNotFoundError(f"{REFERENCE_DIR}에 hs_names_YYYYMMDD.xlsx가 없습니다.")
    return found[-1]


def overrides_path(hs_ver):
    return REFERENCE_DIR / f"hs6_summary_overrides_{hs_ver}.csv"


def setup_log(log_dir):
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"hs_code_build_{datetime.now():%Y%m%d_%H%M%S}.log"
    log.setLevel(logging.INFO)
    for h in log.handlers:  # 스케줄러처럼 한 프로세스에서 여러 번 실행해도 로그가 겹치지 않게
        h.close()
    log.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S")
    for h in (logging.StreamHandler(), logging.FileHandler(path, encoding="utf-8")):
        h.setFormatter(fmt)
        log.addHandler(h)
    return path


def word_count(text):
    return len(text.split())


def load_names(xlsx):
    """xlsx의 모든 시트(2~10단위)를 {코드: 한글명}으로 합칩니다."""
    names = {}
    for df in pd.read_excel(xlsx, sheet_name=None, dtype=str).values():
        for code, name in df.iloc[:, :2].dropna().itertuples(index=False):
            names[code.strip()] = name.strip()
    return names | KR_SPECIAL


def six_name(code, names):
    """6단위명: 6단위 시트 → 10단위 XXXXXX0000 → (XXXX00이면) 4단위명 순으로 찾습니다."""
    return names.get(code) or names.get(code + "0000") or (names.get(code[:4], "") if code.endswith("00") else "")


def six_parts(code, names):
    """LLM 입력용 [4단위명, 5단위명, 6단위명]. 빈 이름과 중복(XXXX00은 4단위명=6단위명)은 뺍니다."""
    return list(dict.fromkeys(p for p in (names.get(code[:4], ""), names.get(code[:5], ""), six_name(code, names)) if p))


def codes_by_level(names):
    ten = sorted(c for c in names if len(c) == 10)
    return {2: sorted(c for c in names if len(c) == 2),
            4: sorted(c for c in names if len(c) == 4),
            6: sorted({c[:6] for c in ten} | {c for c in KR_SPECIAL if len(c) == 6}),
            10: ten}


def summarize(items, prompt, model, api_key, cache_path, label, stats=None):
    """{코드: 입력문}을 BATCH개씩 LLM에 보내 {코드: 요약}을 받습니다. 결과는 캐시해 재실행 시 이어서 합니다."""
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    todo = [c for c in items if c not in cache]
    log.info(f"[{label}] 대상 {len(items):,}개 | 캐시 완료 {len(items) - len(todo):,}개 | 요청 {len(todo):,}개")
    client = OpenAI(api_key=api_key)
    started = time.time()
    for i in range(0, len(todo), BATCH):
        chunk = {c: items[c] for c in todo[i:i + BATCH]}
        for attempt in range(1, RETRIES + 1):
            if stats is not None:
                stats["requests"] = stats.get("requests", 0) + 1  # 재시도 호출도 셈 (batch_run.request_count)
            try:
                res = client.chat.completions.create(
                    model=model, temperature=0, response_format={"type": "json_object"},
                    messages=[{"role": "system", "content": prompt},
                              {"role": "user", "content": json.dumps(chunk, ensure_ascii=False)}])
                got = json.loads(res.choices[0].message.content)
                cache |= {c: str(got[c]).strip() for c in chunk if str(got.get(c, "")).strip()}
                break
            except Exception as e:  # 네트워크·형식 오류는 재시도, 끝내 실패한 코드는 다음 실행에서 다시 요청
                log.info(f"[{label}] 요청 실패 {i + 1}~{i + len(chunk)} ({attempt}/{RETRIES}): {e}")
                time.sleep(5 * attempt)
        cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
        done = min(i + BATCH, len(todo))
        left = (time.time() - started) / done * (len(todo) - done)
        log.info(f"[{label}] {done:,}/{len(todo):,} ({done / len(todo):.0%}) | 남은 시간 약 {left / 60:.1f}분")
    return {c: cache[c] for c in items if c in cache}


def load_overrides(path):
    if not path.exists():
        return {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        return {r["hs_code"].strip(): r["summary"].strip() for r in csv.DictReader(f)}


def db_name(name_ko):
    """DB name_ko 컬럼에 들어가는 값. 6단위 '요약|원본'은 요약 부분입니다."""
    return name_ko.split(SEP, 1)[0]


def build(names, summaries, source, hs_ver):
    rows = []
    for level, codes in codes_by_level(names).items():
        for code in codes:
            if level == 6:
                name = summaries.get(code, "") + SEP + six_name(code, names)
            else:
                name = names[code]
            rows.append({"hs_code": code, "depth": level, "name_ko": name, "source_edition": source, "hs_ver": hs_ver})
    return rows


def run(customs_xlsx=None, hs_ver=None, model=DEFAULT_MODEL, env=ENV_PATH, output_dir=OUTPUT_DIR, cache_dir=OUTPUT_DIR):
    """CSV를 만들고 결과를 돌려줍니다: {"csv": 경로, "hs_ver": 판, "rows": 행 수, "missing": 요약 누락 수, ...}
    요약 캐시는 output_dir과 상관없이 cache_dir(기본 hscode_outputs/)에 판별 파일명으로 둡니다.
    결과 폴더를 바꿔도(자동 갱신의 hscode_outputs/<판>/) 이미 요약한 코드를 다시 요청하지 않게 하기 위해서입니다."""
    customs_xlsx = Path(customs_xlsx) if customs_xlsx else latest_reference()
    output_dir = Path(output_dir)
    key = {k.lower(): v for k, v in dotenv_values(env).items()}.get(ENV_KEY, "").strip()
    if not key:
        raise ValueError(f"{env}에 OPENAI_API_KEY가 없습니다.")
    date = edition_date(customs_xlsx)
    hs_ver = hs_ver or (hs_ver_for(date) if date else "HS2022")
    source = f"관세청 HS부호 단위별품목명 {date or customs_xlsx.stem}"

    log_path = setup_log(output_dir / "logs")
    started = datetime.now()
    log.info(f"HS 코드 마스터 생성 시작 | {source} | {hs_ver} | 모델 {model}")
    names = load_names(customs_xlsx)
    items = {c: " > ".join(six_parts(c, names)) for c in codes_by_level(names)[6]}
    log.info(f"관세청 xlsx 로드 완료 | 6단위 {len(items):,}개")

    stats = {"requests": 0}
    summaries = summarize(items, PROMPT, model, key,
                          Path(cache_dir) / f"hs6_summary_cache_{hs_ver}_{model}.json", "1차 요약", stats)
    long_items = {c: f"{items[c]} (기존 요약: {summaries[c]})" for c in summaries if word_count(summaries[c]) > 5}
    retried = summarize(long_items, RETRY_PROMPT, model, key,
                        Path(cache_dir) / f"hs6_summary_retry_cache_{hs_ver}_{model}.json", "5단어 초과 재요약", stats)
    summaries |= retried  # 재요약도 5단어를 넘으면 그 결과를 그대로 씁니다
    overrides = load_overrides(overrides_path(hs_ver))
    summaries |= {c: v for c, v in overrides.items() if c in items}
    log.info(f"수동 요약 {len(overrides)}개 적용 ({overrides_path(hs_ver).name})")
    rows = build(names, summaries, source, hs_ver)

    out_path = output_dir / "hs_code_mst.csv"
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:  # 엑셀에서 한글이 깨지지 않도록 BOM 포함
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    levels = pd.Series([len(r["hs_code"]) for r in rows]).value_counts().sort_index().to_dict()
    missing = [c for c in items if c not in summaries]
    over = [c for c in summaries if word_count(summaries[c]) > 5]
    under = [c for c in summaries if word_count(summaries[c]) < 3]
    too_long = [r["hs_code"] for r in rows if len(db_name(r["name_ko"])) > NAME_MAX]
    dup = pd.Series(summaries).loc[lambda x: x.duplicated(keep=False)]
    bad_sep = [r["hs_code"] for r in rows if r["name_ko"].count(SEP) != (r["depth"] == 6)]
    log.info(f"단위별 행 수 {levels} | 전체 {len(rows):,}행")
    log.info(f"요약 누락 {len(missing)}개{' → 다시 실행하면 누락분만 요청' if missing else ''}")
    log.info(f"재요약 {len(retried):,}개 | 재요약 후에도 5단어 초과 {len(over)}개 (그대로 저장)"
             f"{': ' + ', '.join(f'{c}={summaries[c]}' for c in over[:10]) if over else ''}")
    log.info(f"3단어 미만 요약 {len(under)}개 (그대로 저장)")
    log.info(f"구분기호 '{SEP}' 개수 이상 {len(bad_sep)}개 (6단위 1개, 그 외 0개여야 함): {', '.join(bad_sep[:10]) or '-'}")
    log.info(f"요약 중복 {len(dup)}개: {', '.join(f'{c}={v}' for c, v in dup.sort_values().head(10).items()) or '-'}")
    log.info(f"DB name_ko(6단위는 요약) {NAME_MAX}자 초과 {len(too_long)}개: {', '.join(too_long[:10]) or '-'}")
    log.info(f"결과: {out_path} | 소요 {str(datetime.now() - started).split('.')[0]} | 로그: {log_path}")
    # received: xlsx에서 읽은 코드 수 (직접 추가한 관세청 특수코드 제외), requests: OpenAI 호출 수
    return {"csv": out_path, "hs_ver": hs_ver, "rows": len(rows), "missing": len(missing),
            "received": len(set(names) - set(KR_SPECIAL)), "requests": stats["requests"]}


def main():
    parser = argparse.ArgumentParser(description="관세청 xlsx + LLM 요약으로 HS 코드 마스터 CSV 생성")
    parser.add_argument("--customs-xlsx", type=Path, help="관세청 HS부호 단위별품목명 xlsx (기본: reference/의 최신 hs_names_YYYYMMDD.xlsx)")
    parser.add_argument("--hs-ver", help="HS 판 (기본: xlsx 파일명의 판 날짜로 결정, 예: 20260101 → HS2022)")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="요약에 쓸 OpenAI 모델")
    parser.add_argument("--env", type=Path, default=ENV_PATH, help="OPENAI_API_KEY가 있는 .env 경로 (기본: 저장소 루트)")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR, help="결과 폴더 (CSV·로그)")
    parser.add_argument("--cache-dir", type=Path, default=OUTPUT_DIR, help="요약 캐시 폴더 (기본: hscode_outputs/)")
    args = parser.parse_args()
    try:
        run(args.customs_xlsx, args.hs_ver, args.model, args.env, args.output_dir, args.cache_dir)
    except (FileNotFoundError, ValueError) as e:
        raise SystemExit(str(e))


if __name__ == "__main__":
    main()
