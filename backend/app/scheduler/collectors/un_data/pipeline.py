"""UN 최종 CSV 검증. 원천 변환 코드는 별도 과거 참고 자료에 보관합니다."""
import csv, re
from pathlib import Path
from decimal import Decimal, ROUND_HALF_UP
from datetime import date

HEAD = 'freq_code period_start country_code hs6_code hs_ver total_import_usd korea_import_usd korea_share_pct valuation_basis'.split()
VER = {'H2':'HS2002','H3':'HS2007','H4':'HS2012','H5':'HS2017','H6':'HS2022'}

def require(condition, message):
    if not condition: raise ValueError(message)

def read_rows(path):
    path = Path(path)
    require(path.suffix.lower() == '.csv', '최종 CSV 파일만 지원합니다')
    with path.open(encoding='utf-8-sig', newline='') as f:
        yield from csv.DictReader(f)


def amount(value):
    if value in ('', None): return None
    n=Decimal(str(value))
    require(n.is_finite() and n>=0, '금액은 유한한 음수 아닌 수여야 함')
    require(n == n.quantize(Decimal('.001')), '소수 3자리 초과')
    require(n < Decimal(10)**21, 'DECIMAL(24,3) 초과')
    return n

def share(world, korea):
    return (korea*100/world).quantize(Decimal('.000001'), rounding=ROUND_HALF_UP) if world is not None and world>0 and korea is not None else None




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


def validate_csv(path):
    return validate_records(read_rows(path))
