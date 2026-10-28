"""SQLite-only contract fixtures. No RDS writes or production master-data loader."""
import json
import re
from datetime import date
from pathlib import Path
from sqlalchemy import (MetaData, Table, Column, Integer, String, Date, DateTime, Numeric,
                        JSON, text, create_engine)
from sqlalchemy.pool import StaticPool
from backend.app.api.runtime_schema import RUNTIME_TABLES
from backend.app.api.common import now, month_end, shift_month


def test_engine():
    engine = create_engine('sqlite://',poolclass=StaticPool,connect_args={'check_same_thread':False})
    schema = json.loads(Path(__file__).with_name('fixtures').joinpath('api_schema.json').read_text(encoding='utf-8'))
    meta = MetaData()
    for name, columns in schema.items():
        if name == 'async_job': continue  # 사용 보류 테이블이 없어도 API 전체가 동작해야 한다.
        defs = []
        for c in columns:
            kind = c['type']; typ = String()
            if kind.startswith(('bigint','int','smallint','tinyint')): typ = Integer()
            elif kind.startswith(('decimal','numeric')): typ = Numeric(24,6)
            elif kind == 'date': typ = Date()
            elif kind.startswith(('datetime','timestamp')): typ = DateTime()
            elif kind == 'json': typ = JSON()
            default = c['default']
            defs.append(Column(c['name'],typ,primary_key=c['pk'],nullable=c['nullable'],
                autoincrement=c['auto'],server_default=text(default) if default else None))
        Table(name,meta,*defs)
    for table in RUNTIME_TABLES: table.to_metadata(meta)
    meta.create_all(engine)
    return engine, meta


def reference_data(engine, meta):
    with engine.begin() as c:
        c.execute(meta.tables['country'].insert(),[
            {'country_code':'US','name_ko':'미국','country_role':'COUNTRY'},
            {'country_code':'CN','name_ko':'중국','country_role':'COUNTRY'},
            {'country_code':'TOTAL','name_ko':'총계','country_role':'TOTAL'}])
        c.execute(meta.tables['hs_code'].insert(),[
            {'hs_code':'3304','hs_ver':'HS2022','depth':4,'name_ko':'화장품','source_edition':'HS2022'},
            {'hs_code':'330499','hs_ver':'HS2022','depth':6,'name_ko':'기초화장품','source_edition':'HS2022'}])
        c.execute(meta.tables['service_option'].insert(),[
            {'option_code':'RPT_ON_DEMAND','option_type':'REPORT_TYPE','label_ko':'요청형','enabled':True,'sort_order':1,'value_json':{}},
            {'option_code':'MONTHLY','option_type':'REPORT_PERIOD','label_ko':'월간','enabled':True,'sort_order':2,'value_json':{}},
            {'option_code':'TERMS_V1','option_type':'NOTIFICATION_TERMS','label_ko':'알림 약관','enabled':True,'sort_order':1,'value_json':{'body':'테스트용 약관'}}])
        for target in ('export_summary','import_summary'):
            c.execute(meta.tables['batch_run'].insert(),{'source_code':'CUSTOMS','job_name':'HS6_AGGREGATION',
                'target_table':target,'period_start':date(2024,1,1),'period_end':date(2025,12,31),'status':'SUCCESS','detail_json':{'schema_version':1}})
            values=[]
            for i in range(24):
                m=shift_month(date(2024,1,1),i)
                for country,amount in [('US',1000000+i*10000),('CN',2000000+i*10000),('TOTAL',99000000)]:
                    values.append({'trade_month':m,'hs6_code':'330499','hs_ver':'HS2022','country_code':country,'amount_usd':amount})
            c.execute(meta.tables[target].insert(),values)
