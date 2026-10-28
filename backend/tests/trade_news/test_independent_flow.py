"""Independent PR29 snapshot: fake HTTP + SQLite + fake embeddings, no native supervisor."""
import json
import pytest
from sqlalchemy import text
from .test_daily_scheduler import local, fixtures, offline_runner, job, NOW, DAY
from .test_project_tags import strict_tags


def test_direct_child_collection_sql_tags_index_and_idempotent_replay(local):
    fixtures(local)
    strict_tags(local.db)
    path=local.tmp/'responses.json'
    spec=json.loads(path.read_text())
    for url,response in spec.items():
        if '/article/' in url or '/news/2026' in url:
            response['body']=response['body'].replace('100억원 계약을 체결했다.','100억원 계약을 체결했다. HS 854200 품목이다.')
    path.write_text(json.dumps(spec))
    calls=[]
    for iteration in (1,2):
        result=job.run_with_rag(local.root,now=NOW,env_path=local.env,runner=offline_runner(local,calls,bounded=False))
        assert result['status']=='complete',result
        assert result['target_date']==DAY
        assert result['dictionary']['status']=='complete'
        assert result['mysql']['include_tags'] and result['mysql']['generate_tags']
        assert result['mysql']['inserted' if iteration==1 else 'existing']==2
        assert result['vector']['status']=='complete'
        assert result['vector']['body']['collection']=='total_article'
        if iteration==2:assert result['vector']['body']['embedded']==0
        with local.db.connect() as conn:
            assert conn.execute(text('SELECT COUNT(*) FROM article')).scalar()==2
            assert conn.execute(text('SELECT COUNT(*) FROM article_tag WHERE hs_code IS NOT NULL')).scalar()==2
            assert conn.execute(text("SELECT COUNT(*) FROM article_tag WHERE keyword_text='수출'")).scalar()==2
            assert dict(conn.execute(text('SELECT country_code,COUNT(DISTINCT article_id) FROM article_tag WHERE country_code IS NOT NULL GROUP BY country_code')).all())=={'KR':2,'US':2}
            count=conn.execute(text('SELECT COUNT(*) FROM article_tag')).scalar()
            if iteration==1:before=count
            else:assert count==before
        assert json.loads((local.root/'current_job.json').read_text())==result
    assert len(calls)==8
    assert '--tag-dictionary' in calls[1] and '--include-tags' in calls[2]
    assert not (local.tmp/'network_attempts.log').exists()


def test_direct_child_vector_exit124_is_failure_not_timeout_and_replay(local,monkeypatch):
    fixtures(local)
    monkeypatch.setenv('OFFLINE_VECTOR_FAULT','exit124')
    failed=job.run_with_rag(local.root,now=NOW,env_path=local.env,runner=offline_runner(local,[],bounded=False))
    assert failed['status']=='partial' and failed['mysql']['status']=='complete',failed
    assert failed['vector']['status']=='failed' and 'reason' not in failed['vector']
    monkeypatch.delenv('OFFLINE_VECTOR_FAULT')
    recovered=job.run_with_rag(local.root,now=NOW,env_path=local.env,runner=offline_runner(local,[],bounded=False))
    assert recovered['status']=='complete' and recovered['mysql']['existing']==2,recovered
    with local.db.connect() as conn:assert conn.execute(text('SELECT COUNT(*) FROM article')).scalar()==2
    assert not (local.tmp/'network_attempts.log').exists()
