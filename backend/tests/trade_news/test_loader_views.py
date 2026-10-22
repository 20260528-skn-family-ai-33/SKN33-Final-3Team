import json,re,sqlite3
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from sqlalchemy import text
import load_project
from news.save import Store
from news.staging import PreparationStore
from .helpers import article,prepared,engine
ROOT=Path(__file__).resolve().parents[3]


def setup(tmp):
    root=tmp/'data';store=Store(root);prep=PreparationStore(root/'prepared.sqlite3')
    for n in (1,2):
        a=article(n);p=prepared(n);prep.save(a,p);store.save_article({**a,'body_masked':p['body_masked']})
    store.export();store.close();prep.close()
    mapping=tmp/'map.json';mapping.write_text('{"hankyung":"HANKYUNG"}')
    args=SimpleNamespace(data_dir=root,source_map=mapping,target_date=date(2026,10,21),sites=['hankyung'],apply=False,include_tags=False,result_file=tmp/'result.json')
    return args

def test_plan_insert_repeat_and_failure_report(tmp_path):
    args=setup(tmp_path);never=Mock(side_effect=AssertionError('no DB'))
    r=load_project.execute(args,never);assert r['status']=='plan_only';never.assert_not_called()
    db=engine(tmp_path/'project.sqlite3');args.apply=True
    assert load_project.execute(args,lambda:db)['inserted']==2
    assert load_project.execute(args,lambda:db)['existing']==2
    with db.begin() as c:c.execute(text("UPDATE article SET body_masked='keep' WHERE external_id=:id"),{'id':article(2)['source_article_id']})
    r=load_project.execute(args,lambda:db)
    assert (r['status'],r['existing'],r['failed'],r['unprocessed'])==('failed',1,1,0)
    assert json.loads(args.result_file.read_text())==r
    with db.connect() as c:assert c.execute(text("SELECT body_masked FROM article WHERE external_id=:id"),{'id':article(2)['source_article_id']}).scalar()=='keep'
    db.dispose()

def test_connection_failure_is_reported_without_secret(tmp_path):
    args=setup(tmp_path);args.apply=True
    def bad():raise RuntimeError('SECRET')
    r=load_project.execute(args,bad)
    assert r['status']=='failed' and r['unprocessed']==2 and 'SECRET' not in args.result_file.read_text()

def test_old_bad_data_does_not_block_current_day(tmp_path):
    args=setup(tmp_path)
    with sqlite3.connect(args.data_dir/'articles.sqlite3') as c:
        row=json.loads(c.execute('SELECT payload FROM articles LIMIT 1').fetchone()[0]);row['published_at']='2026-10-19';row['body']=''
        c.execute('UPDATE articles SET payload=? WHERE article_id=?',(json.dumps(row),row['source_article_id']))
    r=load_project.execute(args)
    assert r['status']=='plan_only' and r['articles_to_store']==1

def test_views_include_real_sources_and_distinct_country(tmp_path):
    db=engine(tmp_path/'project.sqlite3')
    sql=(ROOT/'db/mysql/init/01_schema.sql').read_text()
    views=re.findall(r'CREATE VIEW v_article_.*?;',sql)
    assert len(views)==2
    with db.begin() as c:
        for v in views:c.execute(text(v))
        for i,src in enumerate(['KOTRA_MARKET','HANKYUNG','FNNEWS','OTHER'],1):
            c.execute(text("INSERT INTO article VALUES (:id,:src,:ext,'test','2026-10-21','https://example.test','clean',NULL)"),dict(id=i,src=src,ext=str(i)))
        for i in (1,2,2,4):c.execute(text("INSERT INTO article_tag (article_id,country_code) VALUES (:id,'KR')"),dict(id=i))
        assert c.execute(text('SELECT article_count FROM v_article_day')).scalar()==3
        assert c.execute(text('SELECT article_count FROM v_article_country_day')).scalar()==2
        assert set(c.execute(text('SELECT source_code FROM article')).scalars())=={'KOTRA_MARKET','HANKYUNG','FNNEWS','OTHER'}
    db.dispose()

def test_seed_mapping_migration_and_no_scheduler_registration():
    b=ROOT/'backend/app/scheduler/collectors/trade_news'
    mapping=json.loads((b/'source_map.sample.json').read_text())
    seed=(ROOT/'db/mysql/init/06_service_option_seed.sql').read_text()
    migration=(ROOT/'db/mysql/migrations/20261022_trade_news_sources.sql').read_text()
    for code in mapping.values():
        assert f"('{code}', 'SOURCE'" in seed and f"('{code}', 'SOURCE'" in migration
    schema=(ROOT/'db/mysql/init/01_schema.sql').read_text()
    for v in re.findall(r'CREATE VIEW v_article_.*?;',schema):assert v.replace('CREATE VIEW','CREATE OR REPLACE VIEW') in migration
    assert 'trade_news_daily' not in (ROOT/'backend/app/scheduler/main.py').read_text()
    assert '/backend/app/scheduler/collectors/trade_news/data/' in (ROOT/'.gitignore').read_text()


def test_source_seed_idempotent_without_replacing_existing():
    seed=(ROOT/'db/mysql/init/06_service_option_seed.sql').read_text()
    statement=seed[seed.index('-- 기사 제공 언론사.'):].split('INSERT IGNORE',1)[1]
    db=sqlite3.connect(':memory:')
    db.execute('CREATE TABLE service_option(option_code TEXT PRIMARY KEY,option_type TEXT,label_ko TEXT,value_json TEXT,sort_order INTEGER,enabled INTEGER)')
    statement='INSERT OR IGNORE'+statement
    db.execute(statement);db.execute(statement)
    rows=db.execute('SELECT option_code,option_type,enabled,value_json FROM service_option ORDER BY option_code').fetchall()
    assert len(rows)==2 and all(row[1:3]==('SOURCE',1) for row in rows)
    assert {json.loads(row[3])['crawler_source'] for row in rows}=={'hankyung','fnnews'}
    db.execute("UPDATE service_option SET enabled=0 WHERE option_code='HANKYUNG'")
    db.execute(statement)
    assert db.execute("SELECT enabled FROM service_option WHERE option_code='HANKYUNG'").fetchone()[0]==0
    db.close()
