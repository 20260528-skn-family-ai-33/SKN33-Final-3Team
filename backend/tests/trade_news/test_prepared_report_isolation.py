"""준비 데이터 오류를 보고하되 정상 날짜 집계와 원본 보존을 검증합니다."""
import copy,hashlib,json
from pathlib import Path
import pytest
from news.daily import initialize,daily_summary,read_prepared_summary
from news.preparation import prepare_article
from news.staging import PreparationStore
from news.save import Store
from .helpers import article

TARGET='2026-10-21'
OP={'sites':{'hankyung':{'daily_limit':100,'status':'available_candidates_processed'}}}

def setup(root):
    s=Store(root);initialize(s);p=PreparationStore(root/'prepared.sqlite3')
    a=article();ready=prepare_article(a);p.save(a,ready)
    s.save_article({**a,'body_masked':ready['body_masked']});s.export(full=False)
    old=article(99);old['published_at']='2026-10-19';p.save(old,prepare_article(old))
    return s,p,old

@pytest.mark.parametrize('payload,reason',[
    ('{','invalid_json'),('null','invalid_date'),('[]','invalid_date'),('123','invalid_date'),
    ('{}','invalid_date'),('{"published_date":"bad"}','invalid_date'),
    ('{"published_date":null}','invalid_date'),('{"published_date":20261019}','invalid_date'),
])
def test_bad_old_prepared_is_reported_without_stopping(tmp_path,payload,reason):
    s,p,old=setup(tmp_path)
    p.db.execute('UPDATE prepared_article SET payload=? WHERE external_id=?',(payload,old['source_article_id']));p.db.commit();p.close()
    path=tmp_path/'prepared.sqlite3';digest=hashlib.sha256(path.read_bytes()).hexdigest()
    report=daily_summary(s,TARGET,['hankyung'],set(),OP)
    assert report['counts']['eligible_stored']==report['counts']['body_prepared']==1
    assert report['counts']['prepared_data_errors']==1
    error=report['data_issues']['prepared_errors'][0]
    assert (error['source_code'],error['external_id'],error['published_date'],error['reason_code'])==('hankyung',old['source_article_id'],None,reason)
    assert not report['data_issues']['prepared_summary_complete'] and report['notes']
    assert hashlib.sha256(path.read_bytes()).hexdigest()==digest
    s.close()

@pytest.mark.parametrize('field,bad',[
    ('quality',None),('quality',{}),('quality',{'usable_body':'false'}),
    ('focus',None),('focus',{'accepted':1}),('source_url',None),
    ('source_url','https://www.fnnews.com/news/2026102100099'),('external_id','wrong'),
])
def test_bad_current_fields_are_not_counted_as_valid(tmp_path,field,bad):
    s,p,old=setup(tmp_path)
    row=json.loads(p.db.execute('SELECT payload FROM prepared_article WHERE external_id=?',(old['source_article_id'],)).fetchone()[0])
    row['published_date']=TARGET;row[field]=bad
    p.db.execute('UPDATE prepared_article SET payload=? WHERE external_id=?',(json.dumps(row),old['source_article_id']));p.db.commit();p.close()
    report=daily_summary(s,TARGET,['hankyung'],set(),OP)
    assert report['counts']['body_prepared']==1 and report['counts']['prepared_data_errors']==1
    error=report['data_issues']['prepared_errors'][0]
    assert error['published_date']==TARGET and error['reason_code']=='invalid_prepared_fields'
    s.close()

def test_other_day_fields_not_used_and_repair_clears_error(tmp_path):
    s,p,old=setup(tmp_path)
    original=p.db.execute('SELECT payload FROM prepared_article WHERE external_id=?',(old['source_article_id'],)).fetchone()[0]
    p.db.execute('UPDATE prepared_article SET payload=? WHERE external_id=?',('{',old['source_article_id']));p.db.commit()
    assert daily_summary(s,TARGET,['hankyung'],set(),OP)['counts']['prepared_data_errors']==1
    # 시험 DB에서만 수정 후 재집계. 운영 함수는 원본을 수정하지 않습니다.
    p.db.execute('UPDATE prepared_article SET payload=? WHERE external_id=?',(original,old['source_article_id']));p.db.commit()
    report=daily_summary(s,TARGET,['hankyung'],set(),OP)
    assert report['data_issues']['prepared_summary_complete'] and report['counts']['prepared_data_errors']==0
    assert report['counts']['body_prepared']==1
    p.close();s.close()
