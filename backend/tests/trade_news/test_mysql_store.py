import copy
import pytest
from sqlalchemy import event, text
from news.mysql_store import MySQLArticleStore, ArticleConflict
from .helpers import engine, prepared

@pytest.fixture
def db(tmp_path):
    db=engine(tmp_path/'project.sqlite3');yield db;db.dispose()

def test_real_sqlalchemy_insert_repeat_conflict(db):
    seen=[]
    @event.listens_for(db,'before_cursor_execute')
    def check(c,cur,stmt,params,ctx,many):seen.append(c.in_transaction())
    p=prepared()
    with db.connect() as c:
        s=MySQLArticleStore(c,{'hankyung':'HANKYUNG'})
        assert s.save(p,return_result=True)['status']=='inserted'
        assert not c.in_transaction()
        assert s.save(p,return_result=True)['status']=='existing'
        changed=copy.deepcopy(p);changed['body_masked']+=' Changed'
        with pytest.raises(ArticleConflict):s.save(changed)
        assert not c.in_transaction()
        assert c.execute(text('SELECT body_masked FROM article')).scalar()==p['body_masked']
    assert seen and all(seen)

@pytest.mark.parametrize('mode',['missing','disabled','wrong_type'])
def test_source_failure_rolls_back(db,mode):
    with db.begin() as c:
        c.execute(text({'missing':"DELETE FROM service_option WHERE option_code='HANKYUNG'",'disabled':"UPDATE service_option SET enabled=0 WHERE option_code='HANKYUNG'",'wrong_type':"UPDATE service_option SET option_type='OTHER' WHERE option_code='HANKYUNG'"}[mode]))
    with db.connect() as c:
        with pytest.raises(ValueError):MySQLArticleStore(c,{'hankyung':'HANKYUNG'}).save(prepared())
        assert not c.in_transaction()
        assert c.execute(text('SELECT COUNT(*) FROM article')).scalar()==0

def test_tags_idempotence_and_rollback(db):
    p=prepared();p['tagging']['tags']=[dict(keyword_text='수출')]
    with db.connect() as c:
        s=MySQLArticleStore(c,{'hankyung':'HANKYUNG'},include_tags=True)
        s.save(p);s.save(p)
        assert c.execute(text('SELECT COUNT(*) FROM article_tag')).scalar()==1;c.rollback()
        bad=prepared(2);bad['tagging']['tags']=[{'keyword_text':'ok'},{'keyword_text':'','country_code':'KR'}]
        with pytest.raises(ValueError):s.save(bad)
        assert not c.in_transaction()
        assert c.execute(text('SELECT COUNT(*) FROM article')).scalar()==1
        assert c.execute(text('SELECT COUNT(*) FROM article_tag')).scalar()==1

def test_existing_transaction_not_committed_or_rolled_back(db):
    with db.connect() as c:
        tx=c.begin();c.execute(text("UPDATE service_option SET enabled=0 WHERE option_code='FNNEWS'"))
        with pytest.raises(ValueError):MySQLArticleStore(c,{'hankyung':'HANKYUNG'}).save(prepared())
        assert tx.is_active;tx.rollback()
        assert c.execute(text("SELECT enabled FROM service_option WHERE option_code='FNNEWS'")).scalar()==1

def test_metadata_error_leaves_connection_reusable(db):
    with db.begin() as c:c.execute(text('DROP TABLE article_tag'))
    with db.connect() as c:
        with pytest.raises(ValueError):MySQLArticleStore(c,{'hankyung':'HANKYUNG'},include_tags=True).save(prepared())
        assert not c.in_transaction()
        MySQLArticleStore(c,{'hankyung':'HANKYUNG'}).save(prepared())


@pytest.mark.parametrize('field,value',[('eligible',False),('title',''),('external_id','한글'),('body_masked',''),('published_date','invalid'),('category','x'*121)])
def test_invalid_article_never_starts_write(db,field,value):
    p=prepared();p[field]=value
    with db.connect() as c:
        with pytest.raises(ValueError):MySQLArticleStore(c,{'hankyung':'HANKYUNG'}).save(p)
        assert not c.in_transaction()
        assert c.execute(text('SELECT COUNT(*) FROM article')).scalar()==0
