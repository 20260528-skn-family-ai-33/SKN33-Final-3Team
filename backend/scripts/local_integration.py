"""Local MySQL/Redis helpers; schema bootstrap uses a checked-in, data-free fixture."""
import argparse
import os
from pathlib import Path
from sqlalchemy import MetaData, text, inspect, select, update
from backend.app.api.config import Settings


def local_settings():
    return Settings(values={'API_DB_TARGET':'local','MYSQL_HOST':'127.0.0.1','MYSQL_PORT':'3307',
        'MYSQL_DATABASE':'trade_integration','MYSQL_USER':'integration','MYSQL_PASSWORD':'local-integration-only',
        'API_COOKIE_SECURE':'false','API_REDIS_URL':'redis://127.0.0.1:6379/0',
        'API_REDIS_PREFIX':'borderless:integration:','API_HS_VECTOR_ENABLED':'false'})


def demo_data(engine, metadata):
    """Additional synthetic data only for the fixed local integration database."""
    from datetime import date
    t=metadata.tables
    with engine.begin() as c:
        for code,unit in [('USD',1),('EUR',1),('CNY',1),('JPY',100),('GBP',1)]:
            if not c.scalar(select(t['currency'].c.currency_code).where(t['currency'].c.currency_code==code)):
                c.execute(t['currency'].insert().values(currency_code=code,name_ko=code,quote_unit=unit))
        for country,currency in [('US','USD'),('CN','CNY')]:
            c.execute(update(t['country']).where(t['country'].c.country_code==country,t['country'].c.currency_code.is_(None)).values(currency_code=currency))
        if not c.scalar(select(t['fx_daily'].c.currency_code).limit(1)):
            c.execute(t['fx_daily'].insert(),[{'base_date':day,'currency_code':'USD','rate_krw':rate} for day,rate in [(date(2025,12,30),1400),(date(2025,12,31),1407)]])
        for code,kind,label in [('KOTRA_MARKET','SOURCE','로컬 검증 기사'),('RPT_MONTHLY','REPORT_TYPE','월간 구독')]:
            if not c.scalar(select(t['service_option'].c.option_code).where(t['service_option'].c.option_code==code)):
                c.execute(t['service_option'].insert().values(option_code=code,option_type=kind,label_ko=label,enabled=True,sort_order=10,value_json={}))
        if not c.scalar(select(t['keyword_topic'].c.keyword_text).where(t['keyword_topic'].c.keyword_text=='로컬검증')):
            c.execute(t['keyword_topic'].insert().values(keyword_text='로컬검증',status='UNCLASSIFIED'))
        if not c.scalar(select(t['article'].c.article_id).where(t['article'].c.external_id=='local-integration-demo')):
            result=c.execute(t['article'].insert().values(source_code='KOTRA_MARKET',external_id='local-integration-demo',title='[로컬 검증용] 미국 화장품 시장',published_date=date(2025,12,20),source_url='https://example.org/local-integration-demo',body_masked='연동 검증을 위한 가상 기사입니다. 실제 뉴스가 아닙니다.',category='통상·규제'))
            identity=result.inserted_primary_key[0]
            for tag in [{'country_code':'US'},{'hs_code':'330499','hs_ver':'HS2022'},{'keyword_text':'로컬검증'}]:
                c.execute(t['article_tag'].insert().values(article_id=identity,**tag))


def main():
    p=argparse.ArgumentParser(); p.add_argument('action',choices=['schema','seed','export-schema','migrate','api','worker','check']); args=p.parse_args()
    fixture=Path(__file__).resolve().parents[1]/'tests'/'fixtures'/'integration_schema.sql'
    if args.action=='export-schema':
        source=Settings().engine()
        with source.connect() as reader:
            ddls=[reader.exec_driver_sql(f'SHOW CREATE TABLE `{name}`').first()[1]
                for name in inspect(reader).get_table_names() if not name.startswith('api_')]
        fixture.write_text('-- Schema-only local fixture, captured before integration migration. No production rows.\n\n'+';\n\n'.join(ddls)+';\n',encoding='utf-8')
        source.dispose(); print(f'Exported {len(ddls)} base table definitions, no data.'); return
    if args.action in ('api','worker','migrate','check'):
        os.environ.update(local_settings().values)
        if args.action=='api':
            import uvicorn
            uvicorn.run('backend.main:app',host='127.0.0.1',port=8000,proxy_headers=False)
        elif args.action=='worker':
            from backend.app.api.worker import run_loop
            from backend.app.api.database import Database
            settings=Settings(); run_loop(Database(settings.engine()),settings)
        elif args.action=='migrate':
            import sys
            from backend.scripts.migrate_integration import main as migrate
            sys.argv=['migrate','--apply','--record','tmp/local-integration-migration.json']; migrate()
            from backend.scripts.migrate_integration_cleanup import main as cleanup
            sys.argv=['cleanup','--apply']; cleanup()
        else:
            from backend.scripts.check_api_schema import main as check
            check()
        return
    target=local_settings().engine()
    if args.action=='schema':
        import re
        raw=fixture.read_text(encoding='utf-8')
        ddls={m.group(1):m.group(0).rstrip(';') for m in re.finditer(r'CREATE TABLE `(\w+)`[\s\S]+?;(?=\s*(?:CREATE TABLE|$))',raw)}
        with target.begin() as writer:
            writer.exec_driver_sql('SET FOREIGN_KEY_CHECKS=0')
            try:
                existing=set(inspect(writer).get_table_names())
                for name,ddl in ddls.items():
                    if name in existing:
                        if writer.scalar(text(f'SELECT COUNT(*) FROM `{name}`')):
                            raise RuntimeError('Schema bootstrap only supports empty local tables: '+name)
                        if writer.exec_driver_sql(f'SHOW CREATE TABLE `{name}`').first()[1] != ddl:
                            raise RuntimeError('Local schema differs: '+name)
                        continue
                    writer.exec_driver_sql(ddl.replace('%','%%'))
            finally: writer.exec_driver_sql('SET FOREIGN_KEY_CHECKS=1')
        print(f'Cloned {len(ddls)} table definitions to local MySQL; no source rows copied.')
    else:
        from backend.tests.api_support import reference_data
        metadata=MetaData(); metadata.reflect(target)
        with target.connect() as c:
            initialized=bool(c.scalar(text('SELECT COUNT(*) FROM country')))
        if not initialized: reference_data(target,metadata)
        demo_data(target,metadata)
        print('Synthetic local reference/trade fixtures inserted; no personal or production rows copied.')
    target.dispose()


if __name__=='__main__': main()
