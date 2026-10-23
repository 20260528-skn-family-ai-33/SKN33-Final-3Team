"""MySQL 8.4 기사 ERD 정의 검사. 다른 정의를 자동 덮어쓰지 않고 적용 전에 중단합니다."""
import hashlib
import json
import re
from pathlib import Path


class SchemaMismatch(ValueError):
    pass


def normalize(sql):
    # 문자열 내부의 대소문자·공백·괄호는 보존합니다. CHECK 의미를 바꾸는 정규화 금지.
    sql = re.sub(r"\bCOMMENT\s+'(?:''|\\.|[^'\\])*'", '', sql, flags=re.I)
    sql = re.sub(r'\bAUTO_INCREMENT=\d+', '', sql, flags=re.I)
    tokens = re.findall(r"'(?:''|\\.|[^'\\])*'|`(?:``|[^`])*`|\w+|[^\s]", sql)
    return tuple(t if t.startswith("'") else t.strip('`').lower() for t in tokens)


def parts(ddl):
    columns, objects = {}, {}
    for line in ddl.splitlines()[1:]:
        line = line.strip().removesuffix(',')
        column = re.match(r'`(\w+)` ', line)
        named = re.match(r'(?:UNIQUE KEY|KEY|CONSTRAINT) `(\w+)` ', line)
        if column:
            columns[column[1]] = line
        elif named:
            objects[named[1]] = line
        elif line.startswith('PRIMARY KEY '):
            objects['PRIMARY'] = line
    return columns, objects


class Contract:
    def __init__(self, sql_path):
        self.document = json.loads(Path(sql_path).with_name('20261023_admin_news_contract.json').read_text())
        if hashlib.sha256(Path(sql_path).read_bytes()).hexdigest() != self.document['migration_sha256']:
            raise SchemaMismatch('마이그레이션 SQL 변경: ERD 기준 정의를 함께 검토해야 합니다')
        self.ddl = self.document['ddl']

    def validate(self, conn, kind, table, name):
        actual = conn.exec_driver_sql(f'SHOW CREATE TABLE `{table}`').first()[1]
        expected = self.ddl[table]
        ac, ao = parts(actual)
        ec, eo = parts(expected)
        if kind == 'table':
            valid = normalize(actual) == normalize(expected)
        elif kind in ('column', 'required', 'enum'):
            column = 'target_table' if kind == 'enum' else name
            acceptable = [ec[column]]
            # nullable 확장 단계와 NOT NULL 확정 단계 사이에서 재개할 수 있습니다.
            if column in ('admin_meta_json', 'detail_json'):
                acceptable.append(ec[column].replace('NOT NULL', 'DEFAULT NULL'))
            if kind == 'enum':
                acceptable.append(ec[column].replace(",'hs_code_correlation'", ''))
            valid = column in ac and normalize(ac[column]) in [normalize(v) for v in acceptable]
        else:
            valid = name in ao and normalize(ao[name]) == normalize(eo[name])
        if not valid:
            raise SchemaMismatch(f'ERD 정의 불일치: {table}.{name} ({kind})')

    def validate_existing(self, conn, steps, tables):
        # 기본 기사/태그 중복 방지 키도 검사합니다. 신규 테이블만 검사하면 충분하지 않습니다.
        for table, names in {
            'article': ('PRIMARY', 'uq_article_source_id'),
            'article_tag': ('PRIMARY', 'uq_tag_country', 'uq_tag_hs', 'uq_tag_keyword'),
        }.items():
            for name in names:
                self.validate(conn, 'index', table, name)
        for kind, table, name, _ in steps:
            if table not in tables or kind in ('data', 'view', 'trigger'):
                continue
            columns, objects = parts(conn.exec_driver_sql(f'SHOW CREATE TABLE `{table}`').first()[1])
            present = (kind == 'table' or kind == 'enum'
                       or kind in ('column', 'required') and name in columns
                       or kind in ('index', 'constraint') and name in objects)
            if present:
                self.validate(conn, kind, table, name)
