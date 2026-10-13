"""SK네트웍스 수출입 품목(HS 6단위)의 상대국 무역 데이터를 UN Comtrade에서 받아 원본 CSV와 수집 통계를 저장합니다.

- 수출 품목: 우리가 수출하는 나라(보고국)의 수입 → 상대국 전 세계(0)·한국(410) → 시장 규모와 한국산 비중
- 수입 품목: 우리가 수입하는 나라(보고국)의 수출 → 상대국 전 세계(0)·한국(410) → 공급 규모와 한국행 비중
- 상대 나라(--scope): customs = 관세청 데이터에서 한국과 거래하는 나라 전부를 모든 품목에 적용(기본)
                     item = 품목별로 그 품목을 거래한 나라만 (--top N이면 상위 N개국)
- 주기(--freq): M 월별(기본), A 연간. UN에 그 기간 데이터를 올린 나라에만 요청하고, 나머지는 수집 통계에 기록합니다.

- 인증키: 저장소 루트 .env의 UN_COMTRADE가 있으면 키 API(1회 최대 12개 기간), 없으면 키 없는 공개 API(1회 1개월·500행)를 씁니다.

실행: python backend/app/scheduler/collectors/uncomtrade/uncomtrade_collect.py --customs-csv <customs_raw_*.csv ...>
"""
import argparse
import csv
import hashlib
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests
from dotenv import dotenv_values

BASE = Path(__file__).resolve().parent
# 인증키는 저장소 루트의 .env에서 읽습니다 (값은 출력하지 않음).
ENV_PATH = Path(__file__).resolve().parents[5] / ".env"
ENV_KEY = "un_comtrade"
KEY_API_URL = "https://comtradeapi.un.org/data/v1/get/C/{freq}/HS"
KEY_MAX_ROWS = 50000  # 무료 키 1회 한도(10만 행)보다 여유 있게 잡습니다.
KEY_MAX_PERIODS = 12
DEFAULT_ITEMS = BASE / "skn_items_hscode.md"
API_URL = "https://comtradeapi.un.org/public/v1/preview/C/{freq}/HS"  # 키 없는 공개 API: 1회 1개월, 최대 500행
REPORTERS_URL = "https://comtradeapi.un.org/files/v1/app/reference/Reporters.json"
AVAILABILITY_URL = "https://comtradeapi.un.org/public/v1/getDA/C/{freq}/HS"  # 기간별 데이터를 올린 보고국 목록
FLOW = {"수출": "M", "수입": "X"}  # 수출 품목은 상대국의 수입, 수입 품목은 상대국의 수출을 봅니다.
PARTNERS = "0,410"  # 0: 전 세계, 410: 한국
MAX_ROWS = 500
RETRIES = 4
TIMEOUT = 120
QUOTA_WAITS = 12  # 공개 API 사용량 한도(403)에 걸렸을 때 기다렸다 다시 시도하는 최대 횟수


def quota_wait(resp):
    """공개 API 한도 초과(403) 응답이면 "replenished in HH:MM:SS"의 남은 초를 돌려줍니다."""
    if resp.status_code != 403:
        return None
    match = re.search(r"replenished in (\d+):(\d+):(\d+)", resp.text)
    if not match:
        return None
    h, m, sec = map(int, match.groups())
    return h * 3600 + m * 60 + sec + 30


def load_key(env_path=ENV_PATH):
    """.env의 UN_COMTRADE 키를 읽습니다 (대소문자 무관). 없으면 빈 문자열 → 공개 API 사용."""
    if not Path(env_path).exists():
        return ""
    values = {k.lower(): (v or "").strip().strip("\"'") for k, v in dotenv_values(env_path).items()}
    return values.get(ENV_KEY, "")


def parse_items(md_path):
    """품목 HS Code md 파일의 표에서 사업부·구분·품목·HS 4/6/10단위를 읽습니다."""
    rows, section, kind = [], "", ""
    for line in Path(md_path).read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            section = line[3:].strip()
        elif line.startswith("#### "):
            title = line[5:].split("(")[0]
            kind = "수출" if "수출" in title else "수입"
        elif line.startswith("|") and re.search(r"\d{4}\.\d{2}", line):
            cells = [c.strip().strip("*").strip() for c in line.strip().strip("|").split("|")]
            hs4 = next((c for c in cells if re.fullmatch(r"\d{4}", c)), "")
            hs6 = next((c for c in cells if re.fullmatch(r"\d{4}\.\d{2}", c)), "").replace(".", "")
            hs10 = next((c for c in cells if re.fullmatch(r"\d{4}\.\d{2}-\d{4}", c)), "")
            rows.append({"사업부": section, "구분": kind, "품목": cells[0], "hs4": hs4, "hs6": hs6,
                         "hs10": hs10.replace(".", "").replace("-", "")})
    if not rows:
        raise ValueError(f"{md_path}에서 품목 표를 찾지 못했습니다")
    return pd.DataFrame(rows)


def load_reporters(path):
    """UN 보고국 코드표를 받아 저장하고, 현재 유효한 국가의 ISO2 → UN 숫자코드 표를 만듭니다."""
    if not path.exists():
        resp = requests.get(REPORTERS_URL, timeout=TIMEOUT)
        resp.raise_for_status()
        path.write_bytes(resp.content)
    results = json.loads(path.read_text(encoding="utf-8-sig"))["results"]
    # 같은 ISO2에 과거 코드(예: 인도 356, ~1974)가 있으므로 종료일이 없는 현재 코드만 씁니다.
    return {r["reporterCodeIsoAlpha2"]: (str(r["reporterCode"]), r["text"]) for r in results
            if r.get("reporterCodeIsoAlpha2") and not r.get("isGroup") and not r.get("entryExpiredDate")}


def select_countries(customs_csvs, items, top_n, scope="customs"):
    """관세청 원본(총계 행 제외)으로 품목별 상대 나라를 고르고, 품목·나라별 한국 수출·수입액을 붙입니다.

    scope="customs": 관세청 데이터의 거래국 전부를 모든 품목에 적용 (그 품목 거래가 없으면 kr_usd=0)
    scope="item": 그 품목을 거래한 나라만 (top_n이 있으면 상위 N개국)
    """
    if isinstance(customs_csvs, (str, Path)):
        customs_csvs = [customs_csvs]
    use = ["year", "statCd", "statCdCntnKor1", "hsCd", "expDlr", "impDlr"]
    df = pd.concat([pd.read_csv(f, dtype=str, keep_default_na=False, usecols=use) for f in customs_csvs])
    df = df[df["year"] != "총계"].copy()
    countries = df.drop_duplicates("statCd").set_index("statCd")["statCdCntnKor1"]
    df["hs6"] = df["hsCd"].str[:6]
    df = df[df["hs6"].isin(set(items["hs6"]))]
    for col in ["expDlr", "impDlr"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    sums = df.groupby(["hs6", "statCd", "statCdCntnKor1"], as_index=False)[["expDlr", "impDlr"]].sum()
    out = []
    for kind, col in [("수출", "expDlr"), ("수입", "impDlr")]:
        for hs6 in items.loc[items["구분"] == kind, "hs6"].drop_duplicates():
            part = sums[(sums["hs6"] == hs6) & (sums[col] > 0)].sort_values(col, ascending=False)
            total = part[col].sum()
            amount = dict(zip(part["statCd"], part[col]))
            if scope == "item":
                chosen = list(part["statCd"].head(top_n) if top_n else part["statCd"])
            else:  # 거래액 순서 뒤에 거래가 없는 나라를 이어 붙입니다.
                chosen = list(part["statCd"]) + sorted(set(countries.index) - set(part["statCd"]))
            for rank, iso2 in enumerate(chosen, 1):
                usd = amount.get(iso2, 0)
                out.append({"구분": kind, "hs6": hs6, "rank": rank, "iso2": iso2, "국가": countries[iso2],
                            "kr_usd": int(usd), "kr_share": round(usd / total, 4) if total else 0})
    return pd.DataFrame(out), sorted(df["year"].unique())


def plan_calls(targets, months, available=None):
    """한 달·한 방향 안에서 (보고국 수 × 품목 수 × 상대국 2) ≤ 500이 되도록 품목·보고국을 묶어 호출 목록을 만듭니다.

    available: {월: 그 달 데이터를 올린 UN 보고국 코드 집합}. 주어지면 데이터가 없는 보고국은 요청하지 않습니다.
    """
    per_call = MAX_ROWS // 2  # 상대국 2개(전 세계·한국)
    calls = []
    for kind in ["수출", "수입"]:
        part = targets[(targets["구분"] == kind) & targets["un_code"].ne("")]
        for month in months:
            ok = available.get(month, set()) if available is not None else None
            groups, codes, reporters = [], [], set()
            for hs6, g in part.groupby("hs6", sort=True):
                regs = set(g["un_code"]) if ok is None else set(g["un_code"]) & ok
                regs = sorted(regs, key=int)
                # 한 품목의 보고국이 한 호출 한도를 넘으면 보고국을 나눠 단독 호출합니다.
                if len(regs) > per_call:
                    groups += [([hs6], set(regs[i:i + per_call])) for i in range(0, len(regs), per_call)]
                    continue
                if not regs:
                    continue
                merged = reporters | set(regs)
                if codes and len(merged) * (len(codes) + 1) > per_call:
                    groups.append((codes, reporters))
                    codes, merged = [], set(regs)
                codes, reporters = codes + [hs6], merged
            if codes:
                groups.append((codes, reporters))
            for codes, reporters in groups:
                calls.append({"flow": FLOW[kind], "period": month, "cmdCode": ",".join(codes),
                              "reporterCode": ",".join(sorted(reporters, key=int))})
    return calls


def plan_key_calls(targets, periods, available):
    """키 API용: 한 방향·최대 12개 기간을 한 번에 묶고, 예상 행 수가 한도를 넘으면 품목을 나눕니다."""
    calls = []
    chunks = [periods[i:i + KEY_MAX_PERIODS] for i in range(0, len(periods), KEY_MAX_PERIODS)]
    for kind in ["수출", "수입"]:
        part = targets[(targets["구분"] == kind) & targets["un_code"].ne("")]
        for chunk in chunks:
            ok = set().union(*(available.get(p, set()) for p in chunk))
            regs = sorted(set(part["un_code"]) & ok, key=int)
            if not regs:
                continue
            per_call = max(1, KEY_MAX_ROWS // (len(regs) * len(chunk) * 2))
            codes = sorted(part["hs6"].unique())
            for i in range(0, len(codes), per_call):
                calls.append({"flow": FLOW[kind], "period": ",".join(chunk), "cmdCode": ",".join(codes[i:i + per_call]),
                              "reporterCode": ",".join(regs)})
    return calls


def load_availability(months, cache_dir, delay, freq="M"):
    """기간마다 UN에 데이터를 올린 보고국 코드 목록을 받습니다."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    out = {}
    with requests.Session() as session:
        for month in months:
            path = cache_dir / f"availability_{freq}_{month}.json"
            if not path.exists():
                for attempt in range(1, RETRIES + QUOTA_WAITS + 1):
                    resp = session.get(AVAILABILITY_URL.format(freq=freq), params={"period": month}, timeout=TIMEOUT)
                    if resp.status_code == 200:
                        break
                    wait = quota_wait(resp)
                    if wait:
                        print(f"  사용량 한도 초과 → {wait // 60}분 대기 ({datetime.now():%H:%M})")
                    time.sleep(wait or delay * 2 ** attempt)
                resp.raise_for_status()
                path.write_bytes(resp.content)
                time.sleep(delay)
            data = json.loads(path.read_text(encoding="utf-8")).get("data") or []
            out[month] = {str(d["reporterCode"]) for d in data}
    return out


def month_range(start, end):
    y, m = int(start[:4]), int(start[4:])
    out = []
    while f"{y}{m:02d}" <= end:
        out.append(f"{y}{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def fetch(session, call, delay, freq="M", key=""):
    params = {"reporterCode": call["reporterCode"], "period": call["period"], "partnerCode": PARTNERS,
              "partner2Code": "0", "cmdCode": call["cmdCode"], "flowCode": call["flow"],
              "customsCode": "C00", "motCode": "0", "includeDesc": "true"}
    attempt = quota_waits = 0
    while True:
        attempt += 1
        try:
            if key:
                resp = session.get(KEY_API_URL.format(freq=freq), params=params, timeout=TIMEOUT,
                                   headers={"Ocp-Apim-Subscription-Key": key})
            else:
                resp = session.get(API_URL.format(freq=freq), params=params, timeout=TIMEOUT)
            wait = quota_wait(resp)
            if wait and quota_waits < QUOTA_WAITS:
                quota_waits += 1
                attempt -= 1  # 한도 대기는 재시도 횟수에 넣지 않습니다.
                print(f"  사용량 한도 초과 → {wait // 60}분 {wait % 60}초 대기 후 이어서 받습니다 ({datetime.now():%H:%M})")
                time.sleep(wait)
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                raise requests.HTTPError(f"HTTP {resp.status_code}")
            body = resp.json()
            if resp.status_code != 200 or body.get("error"):
                raise ValueError(f"HTTP {resp.status_code} {body.get('error')}")
            return body.get("data") or []
        except (requests.RequestException, ValueError) as exc:
            if attempt >= RETRIES:
                raise
            wait = delay * 2 ** attempt
            print(f"  재시도 {attempt}/{RETRIES - 1} ({exc}) {wait:.0f}초 대기")
            time.sleep(wait)


def collect(calls, cache_dir, delay, freq="M", key="", max_rows=MAX_ROWS):
    cache_dir.mkdir(parents=True, exist_ok=True)
    rows, failed, capped = [], [], []
    with requests.Session() as session:
        for i, call in enumerate(calls, 1):
            periods = call["period"].split(",")
            mode = "key" if key else "pub"
            # 품목·보고국 목록이 길면 Windows 경로 길이 제한에 걸리므로 짧은 해시로 이름을 만듭니다.
            digest = hashlib.md5(f"{call['cmdCode']}|{call['reporterCode']}".encode()).hexdigest()[:10]
            name = f"{mode}_{freq}_{call['flow']}_{periods[0]}-{periods[-1]}_{digest}.json"
            path = cache_dir / name
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
            else:
                try:
                    data = fetch(session, call, delay, freq, key)
                except Exception as exc:  # 한 호출이 실패해도 나머지는 계속 받습니다.
                    failed.append({**call, "error": str(exc)})
                    print(f"[{i}/{len(calls)}] 실패 {call['flow']} {call['period']} {call['cmdCode']}: {exc}")
                    continue
                path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                time.sleep(delay)
            if len(data) >= max_rows:
                capped.append(call)
            rows.extend(data)
            print(f"[{i}/{len(calls)}] {call['flow']} {periods[0]}~{periods[-1]} 품목 {call['cmdCode'].count(',') + 1}개 → {len(data)}행")
    return rows, failed, capped


def summarize(rows, targets, calls, failed, capped, months, customs_months, available=None):
    got = {(str(r["reporterCode"]), str(r["cmdCode"]), str(r["flowCode"]), str(r["period"]))
           for r in rows if str(r.get("partnerCode")) == "0"}
    flow = targets["구분"].map(FLOW)
    reported = targets[targets["un_code"] != ""]
    missing = []
    for t, f in zip(reported.itertuples(), flow[reported.index]):
        months_missing = [m for m in months if (t.un_code, t.hs6, f, m) not in got]
        if months_missing:
            missing.append({"구분": t.구분, "hs6": t.hs6, "iso2": t.iso2, "국가": t.국가,
                            "missing_months": len(months_missing), "months": months_missing})
    return {
        "period": f"{months[0]}~{months[-1]}", "country_basis": f"관세청 {customs_months[0]}~{customs_months[-1]}",
        "items": int(targets["hs6"].nunique()), "targets": len(targets),
        "not_in_un": targets.loc[targets["un_code"] == "", ["구분", "hs6", "iso2", "국가"]].to_dict("records"),
        "calls": len(calls), "failed_calls": failed, "capped_calls_500": capped, "rows": len(rows),
        "target_month_total": len(reported) * len(months),
        "target_month_missing": sum(m["missing_months"] for m in missing),
        "target_month_not_reported_to_un": sum(1 for t in reported.itertuples() for m in months
                                               if available is not None and t.un_code not in available.get(m, set())),
        "missing_by_target": sorted(missing, key=lambda m: -m["missing_months"]),
    }


def write_csv(path, rows):
    fields = list(dict.fromkeys(k for r in rows for k in r))
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--customs-csv", type=Path, nargs="+", required=True, help="상대 나라 선정 기준 관세청 원본 (여러 개 가능)")
    parser.add_argument("--scope", choices=["customs", "item"], default="customs", help="customs: 관세청 거래국 전부, item: 품목별 거래국")
    parser.add_argument("--freq", choices=["M", "A"], default="M", help="M 월별, A 연간")
    parser.add_argument("--items-md", type=Path, default=DEFAULT_ITEMS)
    parser.add_argument("--start", default="202401", help="시작 연월 YYYYMM (연간은 연도만 사용)")
    parser.add_argument("--end", default="202512", help="종료 연월 YYYYMM (연간은 연도만 사용)")
    parser.add_argument("--top", type=int, default=0, help="--scope item일 때 품목별 상위 N개국 (0이면 전부)")
    parser.add_argument("--delay", type=float, default=2.0, help="호출 사이 대기(초)")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r"\d{6}", args.start) or not re.fullmatch(r"\d{6}", args.end) or args.start > args.end:
        parser.error("--start, --end는 YYYYMM 형식이고 시작이 종료보다 앞서야 합니다")

    scope = f"top{args.top}" if args.scope == "item" and args.top else args.scope
    label = (f"{args.start[:4]}_{args.end[:4]}_annual" if args.freq == "A" else f"{args.start}_{args.end}") + f"_{scope}"
    out = args.output_dir or BASE / "un_comtrade_outputs" / label
    out.mkdir(parents=True, exist_ok=True)
    items = parse_items(args.items_md)
    items.to_csv(out / "skn_items.csv", index=False, encoding="utf-8-sig")
    reporters = load_reporters(out / "un_reporters.json")
    print(f"품목 {len(items)}개 (수출 HS6 {items[items['구분'] == '수출'].hs6.nunique()}, 수입 HS6 {items[items['구분'] == '수입'].hs6.nunique()})")

    targets, customs_months = select_countries(args.customs_csv, items, args.top, args.scope)
    targets["un_code"] = targets["iso2"].map(lambda c: reporters.get(c, ("", ""))[0])
    targets["un_name"] = targets["iso2"].map(lambda c: reporters.get(c, ("", ""))[1])
    targets.to_csv(out / "un_targets.csv", index=False, encoding="utf-8-sig")
    print(f"상대 나라 조합 {len(targets)}개 (UN 미보고 {int((targets['un_code'] == '').sum())}개)")

    if args.freq == "A":
        months = [str(y) for y in range(int(args.start[:4]), int(args.end[:4]) + 1)]
    else:
        months = month_range(args.start, args.end)
    available = load_availability(months, out / "_cache", args.delay, args.freq)
    key = load_key()
    if key:
        calls = plan_key_calls(targets, months, available)
        print(f"인증키 사용: 호출 {len(calls)}회 예정")
    else:
        calls = plan_calls(targets, months, available)
        print(f"공개 API(키 없음): 호출 {len(calls)}회 예정 (호출 간 {args.delay}초)")
    rows, failed, capped = collect(calls, out / "_cache", args.delay, args.freq, key,
                                   KEY_MAX_ROWS * 2 if key else MAX_ROWS)
    write_csv(out / f"un_comtrade_raw_{label}.csv", rows)
    report = summarize(rows, targets, calls, failed, capped, months, customs_months, available)
    report["created_at"] = datetime.now().isoformat(timespec="seconds")
    (out / f"un_comtrade_collection_{label}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"완료: {len(rows)}행, 실패 {len(failed)}회, 대상×월 누락 {report['target_month_missing']}/{report['target_month_total']}")
    print(f"결과: {out}")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
