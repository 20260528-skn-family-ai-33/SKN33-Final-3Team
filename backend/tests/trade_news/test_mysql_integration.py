"""기존 ERD의 실제 MySQL 회귀 시험. 기본 실행에서는 연결하지 않습니다."""
import re
import pytest
from sqlalchemy import text, event
from news.mysql_store import MySQLArticleStore, ArticleConflict
from .helpers import prepared
from .mysql_test_support import ROOT, empty_schema

pytestmark = pytest.mark.mysql_integration


@pytest.fixture
def mysql(mysql_test_url):
    schema = (ROOT/'db/mysql/init/01_schema.sql').read_text(encoding='utf-8')
    statements = []
    # 기존 회귀 시험과 동일한 실제 ERD 및 FK 의존 테이블을 사용합니다.
    for name in ('service_option','market_region','currency','country','hs_code','article','article_tag'):
        ddl = re.search(r'CREATE TABLE '+name+r' \(.*?;(?=\s|$)', schema, re.S)
        assert ddl is not None, name
        statements.append(ddl.group())
    with empty_schema(mysql_test_url, statements) as db:
        with db.begin() as conn:
            conn.execute(text("INSERT INTO service_option (option_code,option_type,label_ko,value_json,sort_order,enabled) VALUES ('HANKYUNG','SOURCE','test','{}',1,1)"))
        yield db


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
