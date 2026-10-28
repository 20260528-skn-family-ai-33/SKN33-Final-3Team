"""Read-only RDS/local readiness check: python -m backend.scripts.check_api_schema."""
import json
from sqlalchemy import inspect, select, text
from backend.app.api.config import Settings
from backend.app.api.database import Database, Store
from backend.app.api.runtime_schema import RUNTIME_TABLES
from backend.app.api import reference, trade, news
from backend.app.api.common import ApiError


def main():
    engine = Settings().engine(); database = Database(engine)
    existing = set(inspect(engine).get_table_names())
    required = {t.name for t in RUNTIME_TABLES}
    print(json.dumps({'connected':True,'missing_app_tables':sorted(required-existing)},ensure_ascii=False))
    with engine.connect() as connection:
        connection.execute(text('SET TRANSACTION READ ONLY'))
        store=Store(database,connection)
        for name in ('export_summary','import_summary','article','fx_daily','un_data','alert_event','model_run','report'):
            print(json.dumps({'table':name,'has_rows':store.has_data(name)},ensure_ascii=False))
        checks={
            'hs_search':lambda:len(reference.hs_search(store,'화장품')['items']),
            'country_reference':lambda:len(reference.countries(store)['items']),
            'trade_publication':lambda:trade.availability(store)['trade_end_month'],
            'articles':lambda:len(news.articles(store,size=3)['items']),
            'exchange_rates':lambda:[x['availability'] for x in news.exchange_rates(store)['items']],
        }
        for name, check in checks.items():
            try: result={'check':name,'status':'OK','result':check()}
            except ApiError as error: result={'check':name,'status':error.code,'message':error.message}
            print(json.dumps(result,ensure_ascii=False,default=str))
        connection.rollback()
    engine.dispose()


if __name__ == '__main__': main()
