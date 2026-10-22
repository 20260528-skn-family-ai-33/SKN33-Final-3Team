"""실제 MySQL용 회귀 테스트. 기본 실행에서는 연결조차 하지 않습니다."""
import os,re
from pathlib import Path
import pytest
from sqlalchemy import create_engine,inspect,text,event
from sqlalchemy.engine import make_url
from news.mysql_store import MySQLArticleStore,ArticleConflict
from .helpers import prepared
ROOT=Path(__file__).resolve().parents[3]
pytestmark=pytest.mark.mysql_integration

@pytest.fixture
def mysql(request):
    if not request.config.getoption('--trade-news-mysql'):
        pytest.skip('실제 MySQL 검증 미승인: --trade-news-mysql 필요')
    url=make_url(os.environ['TRADE_NEWS_TEST_MYSQL_URL'])
    if url.drivername!='mysql+pymysql' or not (url.database or '').startswith('test_'):
        pytest.fail('mysql+pymysql 및 test_ 접두사의 전용 빈 DB 필요')
    db=create_engine(url)
    created=[]
    try:
        with db.connect() as c:
            if inspect(c).get_table_names() or inspect(c).get_view_names():pytest.fail('빈 테스트 DB만 사용 가능')
            c.rollback()
            schema=(ROOT/'db/mysql/init/01_schema.sql').read_text()
            # 실제 ERD DDL을 사용합니다. 국가·HS는 태그 FK 의존 테이블입니다.
            for name in ('service_option','market_region','currency','country','hs_code','article','article_tag'):
                ddl=re.search(r'CREATE TABLE '+name+r' \(.*?;(?=\s|$)',schema,re.S)
                assert ddl is not None,name
                c.execute(text(ddl.group()));created.append(name);c.commit()
            c.execute(text("INSERT INTO service_option (option_code,option_type,label_ko,value_json,sort_order,enabled) VALUES ('HANKYUNG','SOURCE','test','{}',1,1)"));c.commit()
        yield db
    finally:
        if created:
            with db.connect() as c:
                for name in reversed(created):c.execute(text('DROP TABLE '+name))
                c.commit()
        db.dispose()

def test_mysql_transaction_repeat_conflict_and_tags(mysql):
    starts=[]
    @event.listens_for(mysql,'before_cursor_execute')
    def before(c,cur,statement,params,ctx,many):
        if 'information_schema.TABLES' in statement:starts.append(c.in_transaction())
    with mysql.connect() as c:
        store=MySQLArticleStore(c,{'hankyung':'HANKYUNG'},include_tags=True)
        first=prepared();first['tagging']['tags']=[{'keyword_text':'수출'}]
        assert store.save(first,return_result=True)['status']=='inserted'
        second=prepared(2);second['tagging']['tags']=[];store.save(second)
        assert store.save(first,return_result=True)['status']=='existing'
        changed={**first,'body_masked':'changed'}
        with pytest.raises(ArticleConflict):store.save(changed)
        assert not c.in_transaction()
        bad=prepared(3);bad['tagging']['tags']=[{'keyword_text':'ok'},{'keyword_text':''}]
        with pytest.raises(ValueError):store.save(bad)
        assert not c.in_transaction()
        assert c.execute(text('SELECT COUNT(*) FROM article')).scalar()==2
        assert c.execute(text('SELECT COUNT(*) FROM article_tag')).scalar()==1
        assert c.execute(text('SELECT body_masked FROM article WHERE external_id=:id'),{'id':first['external_id']}).scalar()==first['body_masked']
    assert starts and all(starts)
