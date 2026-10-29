"""프로젝트 태그 사전의 구조를 검사합니다. 코드의 실제 의미·공식성을 보증하지 않습니다."""
import hashlib
import json
import re
from pathlib import Path


def validate_dictionary(data):
    if not isinstance(data, dict) or set(data) - {'countries', 'products', 'keywords'}:
        raise ValueError('태그 사전은 countries/products/keywords 배열을 갖는 객체여야 합니다')
    for kind in ('countries', 'products'):
        rows = data.get(kind, [])
        if not isinstance(rows, list):
            raise ValueError(f'{kind}: 배열이 필요합니다')
        seen = set()
        for index, row in enumerate(rows):
            label = f'{kind}[{index}]'
            if not isinstance(row, dict):
                raise ValueError(f'{label}: 객체가 필요합니다')
            required = {'code', 'name'} | ({'version'} if kind == 'products' else set())
            allowed = required | {'aliases'} | ({'role'} if kind == 'countries' else set())
            if required - row.keys() or row.keys() - allowed:
                raise ValueError(f'{label}: 필수 필드 누락 또는 알 수 없는 필드')
            for field in required:
                value = row[field]
                if not isinstance(value, str) or not value.strip() or value != value.strip():
                    raise ValueError(f'{label}.{field}: 앞뒤 공백 없는 문자열이 필요합니다')
            aliases = row.get('aliases', [])
            if not isinstance(aliases, list) or any(not isinstance(a, str) or not a.strip() or a != a.strip() for a in aliases):
                raise ValueError(f'{label}.aliases: 비어 있지 않은 문자열 배열이 필요합니다')
            if len(aliases) != len(set(aliases)):
                raise ValueError(f'{label}.aliases: 중복 별칭')
            if kind == 'countries':
                if not re.fullmatch(r'[A-Za-z0-9_-]{1,12}', row['code']) or len(row['name']) > 120:
                    raise ValueError(f'{label}: 국가 코드·이름 형식 오류')
                if row.get('role', 'COUNTRY') not in ('COUNTRY','SPECIAL','TOTAL'):
                    raise ValueError(f'{label}: 국가 역할 형식 오류')
                identity = row['code']
            else:
                if not re.fullmatch(r'(?:[0-9]{4}|[0-9]{6}|[0-9]{10})', row['code']):
                    raise ValueError(f'{label}: 태깅 지원 HS 코드 길이는 4·6·10자리입니다')
                if not re.fullmatch(r'[A-Z0-9_]{1,10}', row['version']) or len(row['name']) > 500:
                    raise ValueError(f'{label}: HS 판본·이름 형식 오류')
                identity = row['code'], row['version']
            if identity in seen:
                raise ValueError(f'{label}: 중복 코드 행')
            seen.add(identity)
    keywords = data.get('keywords', [])
    if not isinstance(keywords, list):
        raise ValueError('keywords: 배열이 필요합니다')
    seen = set()
    for row in keywords:
        if not isinstance(row, dict) or set(row) != {'term', 'canonical'}:
            raise ValueError('keywords: term/canonical 필요')
        if any(not isinstance(v, str) or not v.strip() or v != v.strip() or len(v) > 120 for v in row.values()):
            raise ValueError('keywords: 유효한 대표어·별칭 필요')
        if row['term'] in seen:
            raise ValueError('keywords: 중복 표현')
        seen.add(row['term'])
    return data


def load_dictionary(path=None):
    if path is None:
        return {'countries':[], 'products':[]}
    return validate_dictionary(json.loads(Path(path).read_text(encoding='utf-8')))


def dictionary_fingerprint(data):
    # 원문 근거와 함께 어떤 사전으로 태깅했는지 재현할 수 있게 지문을 기록합니다.
    canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical.encode()).hexdigest()
