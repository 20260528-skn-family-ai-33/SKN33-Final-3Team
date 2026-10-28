import json,os,subprocess,sys,sqlite3
import pytest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
B=ROOT/'backend/app/scheduler/collectors/trade_news'
T=Path(__file__).parent

@pytest.mark.parametrize('old_broken',[False,True])
def test_full_daily_engine_and_repeat(tmp_path,old_broken):
    if old_broken:
        from news.staging import PreparationStore
        from news.preparation import prepare_article
        from .helpers import article
        root=tmp_path/'data';root.mkdir()
        old=article(99);old['published_at']='2026-10-19'
        p=PreparationStore(root/'prepared.sqlite3');p.save(old,prepare_article(old))
        p.db.execute("UPDATE prepared_article SET payload='{' ");p.db.commit();p.close()
    h='https://www.hankyung.com/article/2026102100001'
    f='https://www.fnnews.com/news/202610210000000001'
    specs={}
    for host,feed,url,title_class,body_class in [
        ('https://www.hankyung.com','/sitemap/latest-article.xml',h,'headline','id="articletxt"'),
        ('https://www.fnnews.com','/sitemap/news/latest.xml',f,'article-view__title','class="article-view__body"')]:
        specs[host+'/robots.txt']={'body':'User-agent: *\nAllow: /','headers':{'Content-Type':'text/plain'}}
        specs[host+feed]={'body':f'<urlset xmlns:news="http://www.google.com/schemas/sitemap-news/0.9"><url><loc>{url}</loc><news:news><news:title>반도체 수출 계약 체결</news:title><news:publication_date>2026-10-21</news:publication_date></news:news></url></urlset>'}
        specs[url]={'body':f'<html><head><meta property="article:published_time" content="2026-10-21"></head><body><h1 class="{title_class}">반도체 수출 계약 체결</h1><div {body_class}>한국 시험기업은 미국에 반도체 수출 100억원 계약을 체결했다.<br>공유하기<br>연락 sample@example.test</div></body></html>'}
    (tmp_path/'responses.json').write_text(json.dumps(specs));(tmp_path/'urls.txt').write_text('')
    env={**os.environ,'OFFLINE_DIR':str(tmp_path),'OFFLINE_ENTRYPOINT':'daily_collect','OFFLINE_RUN_AT':'2026-10-22T03:00:00+09:00','OFFLINE_VIRTUAL_CLOCK':'1','PYTHONDONTWRITEBYTECODE':'1','PYTHONPATH':os.pathsep.join([str(ROOT),str(T),str(B),os.environ.get('PYTHONPATH','')])}
    cmd=[sys.executable,str(T/'offline_runner.py'),'--data-dir',str(tmp_path/'data')]
    for attempt in (1,2):
        result=subprocess.run(cmd,cwd=B,env=env,capture_output=True,text=True,timeout=40)
        assert result.returncode==0,result.stdout+result.stderr
        assert not (tmp_path/'network_attempts.log').exists()
        with sqlite3.connect(tmp_path/'data/articles.sqlite3') as c:
            rows=[json.loads(x[0]) for x in c.execute('SELECT payload FROM articles')]
            assert len(rows)==2
            assert all('공유하기' in x['body'] and '공유하기' not in x['body_masked'] for x in rows)
        downloads=(tmp_path/'downloads.jsonl').read_text().splitlines()
        if attempt==1:count=len(downloads)
        else:
            # 일일 재실행은 목록을 갱신하지만 완료된 기사 본문은 재요청하지 않습니다.
            assert all(json.loads(x)['url'] not in (h,f) for x in downloads[count:])
            assert sum(json.loads(x)['url'] in (h,f) for x in downloads)==2
    result=json.loads((tmp_path/'data/daily_runs/2026-10-21/latest.json').read_text())
    assert result['target_date']=='2026-10-21'
    assert result['counts']['prepared_data_errors']==int(old_broken)
    assert result['counts']['body_prepared']==result['counts']['eligible_stored']==2
    if old_broken:
        with sqlite3.connect(tmp_path/'data/prepared.sqlite3') as c:
            assert c.execute('SELECT payload FROM prepared_article WHERE external_id=?',(old['source_article_id'],)).fetchone()[0]=='{'
            assert json.loads(c.execute('SELECT payload FROM raw_version WHERE external_id=?',(old['source_article_id'],)).fetchone()[0])['body']==old['body']


def test_parse_failure_keeps_diagnostic_html(tmp_path):
    url='https://www.hankyung.com/article/2026102100099'
    raw='<html><h1 class="headline">수출 계약</h1><div>선택자 없는 시험 본문</div></html>'
    spec={'https://www.hankyung.com/robots.txt':{'body':'User-agent: *\nAllow: /'},url:{'body':raw}}
    (tmp_path/'responses.json').write_text(json.dumps(spec));(tmp_path/'urls.txt').write_text(url)
    env={**os.environ,'OFFLINE_DIR':str(tmp_path),'PYTHONDONTWRITEBYTECODE':'1','PYTHONPATH':os.pathsep.join([str(ROOT),str(T),str(B),os.environ.get('PYTHONPATH','')])}
    cmd=[sys.executable,str(T/'offline_runner.py'),'--data-dir',str(tmp_path/'data'),'--urls-file',str(tmp_path/'urls.txt'),'--input-only','--sites','hankyung']
    subprocess.run(cmd,cwd=B,env=env,capture_output=True,text=True,timeout=40)
    assert not (tmp_path/'network_attempts.log').exists()
    assert next((tmp_path/'data/diagnostics').glob('*.html')).read_text()==raw
    with sqlite3.connect(tmp_path/'data/articles.sqlite3') as c:assert c.execute('SELECT COUNT(*) FROM articles').fetchone()[0]==0


@pytest.mark.parametrize('income',[
    '직장인의 월 수입액은 300만원이다.',
    '가구의 연간 수입액은 5000만원을 기록했다.',
    '직장인의 월 수입은 300달러다.',
])
def test_income_full_body_is_not_stored_as_passed_article(tmp_path,income):
    url='https://www.hankyung.com/article/2026102100088'
    # 제목의 무역 단서는 본문 판정을 대신할 수 없습니다.
    body='직장인들의 생활비 부담이 커지고 있다.<br>'+income+'<br>주거비와 식비를 제외하면 저축 여력이 부족하다.'
    html='<meta property="article:published_time" content="2026-10-21"><h1 class="headline">수출 기업 직원 생활비 조사</h1><div id="articletxt">'+body+'</div>'
    spec={'https://www.hankyung.com/robots.txt':{'body':'User-agent: *\nAllow: /'},url:{'body':html}}
    (tmp_path/'responses.json').write_text(json.dumps(spec));(tmp_path/'urls.txt').write_text(url)
    env={**os.environ,'OFFLINE_DIR':str(tmp_path),'PYTHONDONTWRITEBYTECODE':'1','PYTHONPATH':os.pathsep.join([str(ROOT),str(T),str(B),os.environ.get('PYTHONPATH','')])}
    result=subprocess.run([sys.executable,str(T/'offline_runner.py'),'--data-dir',str(tmp_path/'data'),'--urls-file',str(tmp_path/'urls.txt'),'--input-only','--sites','hankyung'],cwd=B,env=env,capture_output=True,text=True,timeout=40)
    assert result.returncode==0,result.stdout+result.stderr
    assert not (tmp_path/'network_attempts.log').exists()
    with sqlite3.connect(tmp_path/'data/articles.sqlite3') as c:
        assert c.execute('SELECT COUNT(*) FROM articles').fetchone()[0]==0
        assert c.execute('SELECT status FROM queue WHERE url=?',(url,)).fetchone()[0]=='filtered'
    with sqlite3.connect(tmp_path/'data/prepared.sqlite3') as c:
        assert c.execute('SELECT eligible FROM prepared_article').fetchone()[0]==0
        assert income in json.loads(c.execute('SELECT payload FROM raw_version').fetchone()[0])['body']
        p=json.loads(c.execute('SELECT payload FROM prepared_article').fetchone()[0])
        assert p['quality']['usable_body'] and not p['focus']['accepted']
        assert income in p['body_masked']
