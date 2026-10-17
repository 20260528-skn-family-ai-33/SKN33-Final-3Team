"""UN CSV·변환 결과의 공통 검증 및 비중 계산."""
import csv, re
from pathlib import Path
from decimal import Decimal, ROUND_HALF_UP
from datetime import date

HEAD = 'freq_code period_start country_code hs6_code hs_ver total_import_usd korea_import_usd korea_share_pct valuation_basis'.split()
VER = {'H2':'HS2002','H3':'HS2007','H4':'HS2012','H5':'HS2017','H6':'HS2022'}

def require(condition, message):
    if not condition: raise ValueError(message)

# 빈 금액은 NULL로 유지하고, 음수·비정상 값·DB 소수 정밀도 초과를 차단합니다.
def amount(value):
    if value in ('', None): return None
    n=Decimal(str(value))
    require(n.is_finite() and n>=0, '금액은 유한한 음수 아닌 수여야 함')
    require(n == n.quantize(Decimal('.001')), '소수 3자리 초과')
    require(n < Decimal(10)**21, 'DECIMAL(24,3) 초과')
    return n

# 세계 수입액이 양수이고 한국 금액이 있을 때만 비중을 계산해 소수 6자리로 반올림합니다.
def share(world, korea):
    return (korea*100/world).quantize(Decimal('.000001'), rounding=ROUND_HALF_UP) if world is not None and world>0 and korea is not None else None




# 9컬럼 구조·복합 키·기간·코드·금액·비중을 공통 규칙으로 검증합니다.
def validate_records(records, allow_empty=False):
    rows=[]; seen=set()
    for r in records:
        require(list(r)==HEAD, '9개 컬럼 순서 불일치')
        key=tuple(r[h] for h in HEAD[:5]); require(key not in seen,'CSV PK 중복'); seen.add(key)
        f,p,c,h,v=key; d=date.fromisoformat(p)
        require(f in ('M','A') and d.day==1 and (f=='M' or d.month==1),'CSV 기간 오류')
        require(bool(re.fullmatch('[A-Z]{2}',c)) and bool(re.fullmatch(r'[0-9]{6}',h)) and v in VER.values(),'CSV 코드 오류')
        w=amount(r[HEAD[5]]); k=amount(r[HEAD[6]])
        require(w is not None or k is not None,'양쪽 NULL')
        require(w is None or k is None or k<=w,'부분집합 위반')
        pct=None if r[HEAD[7]]=='' else Decimal(r[HEAD[7]])
        require(pct==share(w,k),'비중 계산 불일치')
        require(r[HEAD[8]] in ('CIF','FOB'),'평가 기준 오류')
        rows.append(list(key)+[w,k,pct,r[HEAD[8]]])
    require(bool(rows) or allow_empty,'빈 입력은 적재 금지')
    return rows


# 최종 CSV를 읽어 공통 검증을 적용합니다. 기본적으로 빈 파일은 허용하지 않습니다.
def validate_csv(path):
    path=Path(path)
    require(path.suffix.lower()=='.csv', '최종 CSV 파일만 지원합니다')
    # 검증 예외의 traceback이 보관되어도 with를 벗어날 때 즉시 파일을 닫습니다.
    # 중간 RAW 목록을 만들지 않고 DictReader를 검증기에 직접 전달합니다.
    with path.open(encoding='utf-8-sig',newline='') as source:
        return validate_records(csv.DictReader(source))
