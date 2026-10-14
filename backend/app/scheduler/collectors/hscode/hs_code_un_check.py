"""UN Comtrade HS 목록을 받아 hs_code_mst.csv의 2·4·6단위 코드와 대조합니다 (새 판 검증용).

실행 (저장소 루트에서): python backend/app/scheduler/collectors/hscode/hs_code_un_check.py [--csv 경로]

- UN 목록: https://comtradeapi.un.org/files/v1/app/reference/<분류>.json (H5=HS2017, H6=HS2022, H7=HS2028 예상)
  받은 파일은 hscode_outputs/un/<분류>.json에 저장하고, 있으면 다시 받지 않습니다 (--refresh로 다시 받기).
- 6단위까지는 국제 공통이라 코드가 같아야 합니다. 10단위는 한국 고유라 대조하지 않습니다.
- 이유가 확인된 차이(KNOWN)는 '알려진 예외'로, 그 밖의 차이는 '확인 필요'로 나눠 로그와 보고서 CSV에 남깁니다.
- 종료 코드: 0 = 확인 필요 차이 없음, 1 = 확인 필요 차이 있음, 2 = UN에 해당 판 목록이 아직 없음·다운로드 실패
"""
import argparse
import csv
import json
import logging
from datetime import datetime
from pathlib import Path

import requests

BASE = Path(__file__).resolve().parent
OUTPUT_DIR = BASE / "hscode_outputs"
UN_URL = "https://comtradeapi.un.org/files/v1/app/reference/{}.json"
# HS 판 → UN Comtrade 분류 코드. ponytail: HS2028=H7은 UN 명명 규칙상 예상값, 발표되면 확인
UN_CLASS = {"HS2017": "H5", "HS2022": "H6", "HS2028": "H7"}
# 이유가 확인된 차이 (2026-10 HS2022 대조 결과)
KNOWN = {
    "CSV": {"77": "관세청 표의 유보 류(제77류)", "2424": "관세청 특수코드 이사화물", "242400": "관세청 특수코드 이사화물"},
    "UN": {"99": "UN 통계용 품목 미지정", "9999": "UN 통계용 품목 미지정", "999999": "UN 통계용 품목 미지정"},
}
TIMEOUT = 120
log = logging.getLogger("hs_code_un_check")


class UnListMissing(RuntimeError):
    pass


def setup_log(log_dir):
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"hs_code_un_check_{datetime.now():%Y%m%d_%H%M%S}.log"
    log.setLevel(logging.INFO)
    for h in log.handlers:
        h.close()
    log.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    for h in (logging.StreamHandler(), logging.FileHandler(path, encoding="utf-8")):
        h.setFormatter(fmt)
        log.addHandler(h)
    return path


def parse_un(content, un_class):
    """UN 목록 JSON을 검증해 {코드: {"level": 단위, "text": 영문명}} (2·4·6단위)로 바꿉니다.
    JSON이 아니거나(HTML 오류 화면 등) 구조·분류가 다르면 ValueError."""
    try:
        data = json.loads(content)
        if data.get("classCode") != un_class:
            raise ValueError(f"classCode가 {data.get('classCode')!r}입니다 (기대 {un_class})")
        codes = {r["id"]: {"level": r["aggrlevel"], "text": r["text"].split(" - ", 1)[-1]}
                 for r in data["results"] if r["aggrlevel"] in (2, 4, 6)}
    except (ValueError, TypeError, KeyError, AttributeError) as e:  # JSONDecodeError는 ValueError
        raise ValueError(f"UN 목록 형식이 아닙니다: {type(e).__name__}: {e}") from e
    if not any(v["level"] == 6 for v in codes.values()):
        raise ValueError("UN 목록에 6단위 코드가 없습니다")
    return codes, data.get("className")


def fetch_un(un_class, save_dir, refresh=False):
    """UN 목록을 돌려줍니다. 저장된 파일이 깨졌으면 다시 받고, 받은 내용은 검증을 통과해야만 저장(캐시 교체)합니다."""
    path = Path(save_dir) / f"{un_class}.json"
    if path.exists() and not refresh:
        try:
            codes, name = parse_un(path.read_bytes(), un_class)
            log.info(f"UN 목록: {un_class} {name} | 저장된 파일 {path}")
            return codes
        except ValueError as e:
            log.warning(f"저장된 UN 목록이 잘못돼 다시 받습니다: {e}")

    res = requests.get(UN_URL.format(un_class), timeout=TIMEOUT)
    if res.status_code == 404:
        raise UnListMissing(f"UN Comtrade에 {un_class} 목록이 아직 없습니다 ({UN_URL.format(un_class)}).")
    res.raise_for_status()
    try:
        codes, name = parse_un(res.content, un_class)
    except ValueError as e:
        raise UnListMissing(f"받은 UN 목록이 잘못돼 저장하지 않았습니다: {e}") from e
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_bytes(res.content)
    tmp.replace(path)  # 검증된 내용만 캐시로 교체
    log.info(f"UN 목록 다운로드: {un_class} {name} ({len(res.content):,} bytes) | {path}")
    return codes


def read_csv_codes(path):
    """CSV의 2·4·6단위 코드와 hs_ver."""
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = [r for r in csv.DictReader(f) if len(r["hs_code"]) in (2, 4, 6)]
    versions = {r["hs_ver"] for r in rows}
    if len(versions) != 1:
        raise ValueError(f"CSV의 hs_ver가 하나가 아닙니다: {sorted(versions)}")
    return {r["hs_code"] for r in rows}, versions.pop()


def compare(csv_codes, un):
    """차이 목록: [{"hs_code", "level", "side"(CSV에만/UN에만), "known", "reason", "un_text"}]"""
    diffs = []
    for side, codes, known in (("CSV에만", csv_codes - set(un), KNOWN["CSV"]), ("UN에만", set(un) - csv_codes, KNOWN["UN"])):
        for code in sorted(codes, key=lambda c: (len(c), c)):
            diffs.append({"hs_code": code, "level": len(code), "side": side, "known": code in known,
                          "reason": known.get(code, "확인 필요"), "un_text": un.get(code, {}).get("text", "")})
    return diffs


def run(csv_path=OUTPUT_DIR / "hs_code_mst.csv", un_class=None, save_dir=OUTPUT_DIR / "un", refresh=False,
        log_dir=OUTPUT_DIR / "logs"):
    """대조하고 종료 코드(0·1·2)를 돌려줍니다."""
    log_path = setup_log(log_dir)
    try:
        csv_codes, hs_ver = read_csv_codes(csv_path)
        un_class = un_class or UN_CLASS.get(hs_ver)
        if not un_class:
            raise UnListMissing(f"{hs_ver}에 맞는 UN 분류 코드를 모릅니다. --un-class로 지정하세요.")
        log.info(f"UN 대조 시작 | CSV {csv_path} ({hs_ver}, 2·4·6단위 {len(csv_codes):,}개) | UN {un_class}")
        un = fetch_un(un_class, save_dir, refresh)
    except (UnListMissing, requests.RequestException, ValueError, FileNotFoundError) as e:
        log.error(f"대조 불가: {e} | 로그: {log_path}")
        return 2

    for level in (2, 4, 6):
        mine = {c for c in csv_codes if len(c) == level}
        theirs = {c for c, v in un.items() if v["level"] == level}
        log.info(f"{level}단위 | CSV {len(mine):,} | UN {len(theirs):,} | 공통 {len(mine & theirs):,}")
    diffs = compare(csv_codes, un)
    report = Path(save_dir) / f"un_check_{hs_ver}_{un_class}.csv"
    report.parent.mkdir(parents=True, exist_ok=True)
    with open(report, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["hs_code", "level", "side", "known", "reason", "un_text"])
        writer.writeheader()
        writer.writerows(diffs)
    for d in diffs:
        (log.info if d["known"] else log.warning)(
            f"{d['side']} {d['hs_code']} ({d['level']}단위): {d['reason']}{' | ' + d['un_text'] if d['un_text'] else ''}")
    unknown = [d for d in diffs if not d["known"]]
    log.info(f"결과: 차이 {len(diffs)}개 (알려진 예외 {len(diffs) - len(unknown)}개, 확인 필요 {len(unknown)}개) | "
             f"보고서 {report} | 로그: {log_path}")
    return 1 if unknown else 0


def main():
    parser = argparse.ArgumentParser(description="UN Comtrade HS 목록과 hs_code_mst.csv 2·4·6단위 코드 대조")
    parser.add_argument("--csv", type=Path, default=OUTPUT_DIR / "hs_code_mst.csv", help="대조할 CSV")
    parser.add_argument("--un-class", help="UN 분류 코드 (기본: CSV hs_ver로 결정, HS2022 → H6)")
    parser.add_argument("--refresh", action="store_true", help="저장된 UN 목록이 있어도 다시 받기")
    args = parser.parse_args()
    raise SystemExit(run(args.csv, args.un_class, refresh=args.refresh))


if __name__ == "__main__":
    main()
