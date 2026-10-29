"""관세청 HS부호 단위별품목명 xlsx로 HS 코드 마스터 CSV(2·4·6·10단위)를 만듭니다.

- 2·4·10단위: xlsx 시트의 공식 한글 품목명을 그대로 씁니다.
- 6단위: xlsx 6단위 시트에 일부만 있어 10단위 코드 앞 6자리로 목록을 만듭니다.
  이름은 6단위 시트 → 10단위 XXXXXX0000 행 → (XXXX00이면) 4단위 이름 순으로 찾고,
  name_ko = 'LLM 요약(공백 포함 20자 이하)|6단위명 원본' 형태로 저장합니다.
  요약은 2·4·5·6단위 이름을 모두 LLM에 넣어 만들므로 '기타' 같은 6단위명도 뜻이 통하게 됩니다.
  길이 초과·중복·누락은 재요청하고, 해결되지 않으면 CSV를 저장하지 않습니다.
- 관세청 특수코드 2424·242400(이사화물)은 4단위 시트에 없어 직접 추가합니다.
- UN 통계용 코드 99·9999·999999(품목 미지정)는 관세율표에 없어 고정값으로 추가합니다.
  한국어 공식 명칭이 없어 이름은 팀 번역, 6단위 원문은 UN 영문 원문이고 source_edition에 출처를 따로 적습니다.
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
# UN Comtrade 통계용 코드 (WCO HS·관세율표에 없음). 6단위 값은 원문 자리에 들어갈 UN 영문 원문입니다.
UN_STATS_NAME = "품목 미지정(UN 통계용)"
UN_STATS = {"99": UN_STATS_NAME, "9999": UN_STATS_NAME, "999999": "Commodities not specified according to kind"}
UN_STATS_SOURCE = "UN Comtrade 통계용 코드 (한국어 명칭은 팀 번역)"
NAME_MAX = 500  # DB name_ko VARCHAR(500). 6단위는 요약 부분만 해당
OUTPUT_DIR = BASE / "hscode_outputs"
DEFAULT_MODEL = "gpt-4.1-mini"
BATCH = 40
SUMMARY_MAX = 20  # 공백과 문장부호 포함
RETRIES = 3
PROMPT = """너는 관세 품목 분류 전문가다. 각 HS 6단위 품목의 상위 분류명과 품목명을 보고,
무슨 물품인지 바로 알 수 있는 한국어 품목명을 공백과 문장부호 포함 20자 이하로 요약해라.
가능하면 10~16자를 목표로 간결하게 쓰고, 긴 화학명은 원문을 식별할 수 있는 통용 약어를 활용해라.
재질·용도·가공 상태 등 원문의 구별 정보를 보존하고, 원문에 없는 정보를 만들지 마라.
모든 요약은 서로 달라야 하고 이미 사용한 요약과도 일치하면 안 된다. 코드나 일련번호를 붙여 중복을 피하지 마라.
출력 값에는 HS코드(숫자 6자리 및 점으로 구분한 코드)를 절대 쓰지 마라. 코드는 JSON 키로만 사용한다.
요약 끝에 A/B/C 등 구분용 영문자나 번호를 붙이지 마라. 자연스러운 한국어 품목명으로 끝내라.
올바른 출력 예: {"330410": "입술화장용 제품", "330420": "눈화장용 제품", "330499": "기타 피부관리용 화장품"}.
상위 분류부터 마지막 세부 품목까지 읽고 마지막 품목의 범위를 요약해라. 상위 분류의 다른 품목을 섞지 마라.
재료가 세부 품목에 없으면 상위 분류에서 확인해라. 예: '납과 그 제품 > 시트ㆍ박 > 두께 0.2mm 이하' → '납 시트·박(0.2mm 이하)'.
'기타'만 쓰지 말고 상위 분류의 물품명을 넣어라. 예: '살아 있는 말ㆍ당나귀 > 말 > 기타' → '번식용 외 살아 있는 말'.
입력은 {"코드": "분류명 > 분류명 > 품목명"} JSON이고, 출력은 같은 코드를 키로 한 {"코드": "요약"} JSON만 내라."""
log = logging.getLogger("hs_code_build")


def edition_date(path):
    """관세청 xlsx 파일명의 판 날짜(YYYYMMDD). 예: hs_names_20260101.xlsx → '20260101'"""
    found = re.search(r"(\d{8})", Path(path).stem)
    return found.group(1) if found else None


def source_for(date):
    """source_edition 값. 매년 판 날짜가 들어가므로 DB에서 어느 판으로 적재됐는지 구분하는 데도 씁니다."""
    return f"관세청 HS부호 단위별품목명 {date}"


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


def load_names(xlsx):
    """xlsx의 모든 시트(2~10단위)를 {코드: 한글명}으로 합칩니다."""
    names = {}
    for df in pd.read_excel(xlsx, sheet_name=None, dtype=str).values():
        for code, name in df.iloc[:, :2].dropna().itertuples(index=False):
            names[code.strip()] = name.strip()
    return names | KR_SPECIAL | UN_STATS


def six_name(code, names):
    """6단위명: 6단위 시트 → 10단위 XXXXXX0000 → (XXXX00이면) 4단위명 순으로 찾습니다."""
    return names.get(code) or names.get(code + "0000") or (names.get(code[:4], "") if code.endswith("00") else "")


def six_parts(code, names):
    """LLM 입력용 [4단위명, 5단위명, 6단위명]. 빈 이름과 중복(XXXX00은 4단위명=6단위명)은 뺍니다."""
    return list(dict.fromkeys(p for p in (names.get(code[:4], ""), names.get(code[:5], ""), six_name(code, names)) if p))


def summary_input(code, names):
    """재료·산업을 알 수 있도록 2단위 분류도 LLM 요약 문맥에 포함합니다."""
    return " > ".join(dict.fromkeys(p for p in [names.get(code[:2], ""), *six_parts(code, names)] if p))


def codes_by_level(names):
    ten = sorted(c for c in names if len(c) == 10)
    return {2: sorted(c for c in names if len(c) == 2),
            4: sorted(c for c in names if len(c) == 4),
            6: sorted({c[:6] for c in ten} | {c for c in (*KR_SPECIAL, *UN_STATS) if len(c) == 6}),
            10: ten}


def valid_summary(value, source=""):
    """요약 조건: 비어 있지 않은 문자열, 구분기호·HS코드·구분용 영문자 없음, SUMMARY_MAX자 이하.
    LLM이 null·숫자·목록을 주면 str()로 'None' 같은 값이 저장되므로 문자열만 받고,
    이전 버전이 캐시에 남긴 'None' 문자열도 거릅니다.
    끝의 영문자는 원문(source)에 단독으로 있는 글자일 때만 허용합니다 (예: '비타민 C와 …' → '비타민C')."""
    if not isinstance(value, str):
        return False
    value = value.strip()
    tail = re.search(r"[가-힣)]([A-Z])$", value)
    return (value not in ("", "None") and SEP not in value and len(value) <= SUMMARY_MAX
            and not re.search(r"[0-9](?:[.\s-]*[0-9]){5}", value)
            and not (tail and not re.search(rf"(?<![A-Za-z]){tail[1]}(?![A-Za-z])", source)))


def summary_source(code, names, original):
    """끝 영문자 검사(valid_summary)에 쓰는 원문. 생성·시드·적재가 같은 기준으로 검사하도록
    CSV에도 남는 2·4단위명과 6단위 원문만 씁니다."""
    return " ".join((names.get(code[:2], ""), names.get(code[:4], ""), original or ""))


def csv_names(rows):
    """CSV 행 → {판: {코드: 이름(6단위는 요약 부분)}}. 시드·적재에서 summary_source에 넘깁니다."""
    names = {}
    for r in rows:
        names.setdefault(r["hs_ver"], {})[r["hs_code"]] = r["name_ko"].split(SEP, 1)[0]
    return names


def compact_summary(value):
    """긴 LLM 응답의 수치 조건·공백만 압축합니다. 내용은 자르거나 삭제하지 않습니다."""
    if not isinstance(value, str):
        return value
    value = value.strip()
    if len(value) <= SUMMARY_MAX:
        return value
    operators = {"이상": "≥", "이하": "≤", "초과": ">", "미만": "<"}
    value = re.sub(r"([-−]?\d[\d,]*(?:\.\d+)?(?:%|kg|g|mm|cm|m|kW|W))\s*(이상|이하|초과|미만)",
                   lambda m: operators[m[2]] + m[1], value)
    value = value.replace("표백 안 한", "미표백").replace("표백하지 않은", "미표백")
    if len(value) > SUMMARY_MAX:
        value = re.sub(r"\s+", "", value)
    return value


def summarize(items, prompt, model, api_key, cache_path, label, stats=None, reserved=(), sources=None):
    """{코드: 입력문}을 BATCH개씩 LLM에 보내 {코드: 요약}을 받습니다. 결과는 캐시해 재실행 시 이어서 합니다.
    캐시에 유효하지 않은 요약이 있으면(이전 버전에서 저장된 'None' 등) 버리고 다시 요청합니다.
    sources: 끝 영문자 검사용 {코드: 원문} (기본: 입력문)."""
    sources = sources or items
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    reserved = set(reserved)
    used = set(reserved)
    bad = []
    for c, value in cache.items():
        if c not in items:  # 수동 요약으로 바뀐 코드 등: 이번 대상은 아니지만 이미 받은 요약은 캐시에 남김
            continue
        value = compact_summary(value)
        if not valid_summary(value, sources[c]) or value.strip() in used:
            bad.append(c)
        else:
            cache[c] = value.strip()
            used.add(cache[c])
    for c in bad:
        del cache[c]
    if bad:
        log.info(f"[{label}] 캐시의 유효하지 않은 요약 {len(bad)}개 제외 → 재요청: {', '.join(bad[:10])}")
    todo = [c for c in items if c not in cache]
    log.info(f"[{label}] 대상 {len(items):,}개 | 캐시 완료 {len(items) - len(todo):,}개 | 요청 {len(todo):,}개")
    cache_path.parent.mkdir(parents=True, exist_ok=True)  # API 비용을 쓴 뒤 저장 실패하지 않도록 먼저 만듦
    client = OpenAI(api_key=api_key)
    started = time.time()
    for i in range(0, len(todo), BATCH):
        chunk = {c: items[c] for c in todo[i:i + BATCH]}
        chunk_size = len(chunk)
        for attempt in range(1, RETRIES + 1):
            if stats is not None:
                stats["requests"] = stats.get("requests", 0) + 1  # 재시도 호출도 셈 (batch_run.request_count)
            try:
                headings = {c[:4] for c in chunk}  # 같은 4단위만 보냄. 다른 4단위와의 중복은 응답 검사에서 거름
                nearby = reserved | {v for c, v in cache.items() if c in items and c[:4] in headings}
                res = client.chat.completions.create(
                    model=model, temperature=0, response_format={"type": "json_object"},
                    messages=[{"role": "system", "content": prompt + "\n관련 분류 및 수동 확정 요약(재사용 금지): " + json.dumps(sorted(nearby), ensure_ascii=False)},
                              {"role": "user", "content": json.dumps(chunk, ensure_ascii=False)}])
                got = json.loads(res.choices[0].message.content)
                if not isinstance(got, dict):
                    raise ValueError("요약 응답은 JSON 객체여야 합니다.")
                for c in list(chunk):
                    value = compact_summary(got.get(c))
                    if valid_summary(value, sources[c]) and value.strip() not in used:
                        cache[c] = value.strip()
                        used.add(cache[c])
                        del chunk[c]
                    else:
                        length = len(value.strip()) if isinstance(value, str) else "문자열 아님"
                        reason = ("응답 누락" if c not in got
                                  else "다른 코드와 요약 중복" if isinstance(value, str) and value.strip() in used
                                  else "길이 또는 코드/구분문자 조건 위반")
                        log.debug("[%s] 거부 %s: %r (%s)", label, c, value, reason)
                        chunk[c] = (f"{items[c]}\n이전 응답: {json.dumps(value, ensure_ascii=False)} "
                                    f"(글자 수: {length}; 거부 이유: {reason}). 공백 포함 20자 이하·중복 없음·코드/구분문자 없음 조건으로 다시 요약해라.")
                if not chunk:
                    break
                log.info(f"[{label}] 길이·중복·누락 검증 실패 {len(chunk)}개 ({attempt}/{RETRIES})")
            except Exception as e:  # 네트워크·형식 오류는 재시도, 끝내 실패한 코드는 다음 실행에서 다시 요청
                log.info(f"[{label}] 요청 실패 {i + 1}~{i + chunk_size} ({attempt}/{RETRIES}): {e}")
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
            rows.append({"hs_code": code, "depth": level, "name_ko": name, "hs_ver": hs_ver,
                         "source_edition": UN_STATS_SOURCE if code in UN_STATS else source})
    return rows


def run(customs_xlsx=None, hs_ver=None, model=DEFAULT_MODEL, env=ENV_PATH, output_dir=OUTPUT_DIR, cache_dir=OUTPUT_DIR):
    """CSV를 만들고 결과를 돌려줍니다: {"csv": 경로, "hs_ver": 판, "rows": 행 수, "missing": 요약 누락 수, ...}
    요약 누락이 있으면 CSV를 저장하지 않고 csv=None, missing=누락 수를 돌려줍니다.
    요약 캐시는 output_dir과 상관없이 cache_dir(기본 hscode_outputs/)에 판별 파일명으로 둡니다.
    결과 폴더를 바꿔도(자동 갱신의 hscode_outputs/<판>/) 이미 요약한 코드를 다시 요청하지 않게 하기 위해서입니다."""
    customs_xlsx = Path(customs_xlsx) if customs_xlsx else latest_reference()
    output_dir = Path(output_dir)
    key = {k.lower(): v for k, v in dotenv_values(env).items()}.get(ENV_KEY, "").strip()
    if not key:
        raise ValueError(f"{env}에 OPENAI_API_KEY가 없습니다.")
    date = edition_date(customs_xlsx)
    hs_ver = hs_ver or (hs_ver_for(date) if date else "HS2022")
    source = source_for(date or customs_xlsx.stem)

    log_path = setup_log(output_dir / "logs")
    started = datetime.now()
    log.info(f"HS 코드 마스터 생성 시작 | {source} | {hs_ver} | 모델 {model}")
    names = load_names(customs_xlsx)
    items = {c: summary_input(c, names) for c in codes_by_level(names)[6] if c not in UN_STATS}  # 고정값은 요약 안 함
    log.info(f"관세청 xlsx 로드 완료 | 6단위 {len(items):,}개")

    stats = {"requests": 0}
    overrides = {c: v for c, v in load_overrides(overrides_path(hs_ver)).items() if c in items}
    fixed = overrides | {c: UN_STATS_NAME for c in UN_STATS if len(c) == 6}
    sources = {c: summary_source(c, names, six_name(c, names)) for c in items}
    if any(not valid_summary(v, sources.get(c, "")) for c, v in fixed.items()) or len(set(fixed.values())) != len(fixed):
        raise ValueError("고정·수동 요약의 20자 제한 또는 중복 검증 실패")
    summaries = summarize({c: v for c, v in items.items() if c not in fixed}, PROMPT, model, key,
                          Path(cache_dir) / f"hs6_summary_cache_v2_{hs_ver}_{model}.json", "품목 요약", stats,
                          reserved=fixed.values(), sources=sources)
    summaries |= fixed
    log.info(f"수동 요약 {len(overrides)}개 적용 ({overrides_path(hs_ver).name})")
    received = len(set(names) - set(KR_SPECIAL) - set(UN_STATS))
    missing = sorted(set(items) - set(summaries))
    if missing:  # CSV는 저장하지 않고 호출 수와 누락 수만 돌려줌 (hs_code_refresh가 batch_run에 기록)
        log.error(f"20자 이하·중복 없는 요약 생성 실패 {len(missing)}개: {', '.join(missing[:10])}. "
                  f"CSV를 저장하지 않았습니다. 재실행하면 누락분만 요청합니다. | 로그: {log_path}")
        return {"csv": None, "hs_ver": hs_ver, "rows": 0, "missing": len(missing),
                "received": received, "requests": stats["requests"]}
    rows = build(names, summaries, source, hs_ver)

    out_path = output_dir / "hs_code_mst.csv"
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:  # 엑셀에서 한글이 깨지지 않도록 BOM 포함
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    levels = pd.Series([len(r["hs_code"]) for r in rows]).value_counts().sort_index().to_dict()
    too_long = [r["hs_code"] for r in rows if len(db_name(r["name_ko"])) > NAME_MAX]
    dup = pd.Series(summaries).loc[lambda x: x.duplicated(keep=False)]
    bad_sep = [r["hs_code"] for r in rows if r["name_ko"].count(SEP) != (r["depth"] == 6)]
    log.info(f"단위별 행 수 {levels} | 전체 {len(rows):,}행")
    log.info(f"구분기호 '{SEP}' 개수 이상 {len(bad_sep)}개 (6단위 1개, 그 외 0개여야 함): {', '.join(bad_sep[:10]) or '-'}")
    log.info(f"요약 중복 {len(dup)}개: {', '.join(f'{c}={v}' for c, v in dup.sort_values().head(10).items()) or '-'}")
    log.info(f"DB name_ko(6단위는 요약) {NAME_MAX}자 초과 {len(too_long)}개: {', '.join(too_long[:10]) or '-'}")
    log.info(f"결과: {out_path} | 소요 {str(datetime.now() - started).split('.')[0]} | 로그: {log_path}")
    # received: xlsx에서 읽은 코드 수 (직접 추가한 관세청 특수코드 제외), requests: OpenAI 호출 수
    return {"csv": out_path, "hs_ver": hs_ver, "rows": len(rows), "missing": 0,
            "received": received, "requests": stats["requests"]}


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
        result = run(args.customs_xlsx, args.hs_ver, args.model, args.env, args.output_dir, args.cache_dir)
    except (FileNotFoundError, ValueError) as e:
        raise SystemExit(str(e))
    if result["missing"]:
        raise SystemExit(f"요약 생성 실패 {result['missing']}개로 CSV를 저장하지 않았습니다. 재실행하면 누락분만 요청합니다.")


if __name__ == "__main__":
    main()
