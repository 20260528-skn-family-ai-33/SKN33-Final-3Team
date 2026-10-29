import asyncio,logging
from types import SimpleNamespace
import pytest
from scrapy import Request
from scrapy.http import TextResponse
from scrapy.settings import Settings
from scrapy.exceptions import IgnoreRequest
from news.save import Store
import news.save as save
import news.traffic as traffic
from news import settings
LIST='https://www.hankyung.com/sitemap/latest-article.xml'
BODY='https://www.hankyung.com/article/2026102100001'

def context(path,delay=60):
    store=Store(path)
    sp=SimpleNamespace(store=store,sites=['hankyung'],article_inflight={},logger=logging.getLogger('test'))
    cr=SimpleNamespace(settings=Settings({'DAILY_REQUEST_LIMIT':100,'DOWNLOAD_DELAY':60}),engine=SimpleNamespace(downloader=SimpleNamespace(slots={'hankyung':SimpleNamespace(delay=delay)})))
    return store,sp,traffic.TrafficMiddleware(cr)

def response(m,sp,req,status=403,body=''):
    return m.process_response(req,TextResponse(req.url,status=status,body=body.encode(),encoding='utf-8',request=req),sp)

@pytest.fixture
def clock(monkeypatch):
    clock=SimpleNamespace(now=1000000.,waits=[])
    async def sleep(n):clock.waits.append(n);clock.now+=n
    for module in (save,traffic):monkeypatch.setattr(module,'time',SimpleNamespace(time=lambda:clock.now))
    monkeypatch.setattr(traffic,'asyncio',SimpleNamespace(sleep=sleep))
    return clock

@pytest.mark.parametrize('delay',[60,180,300])
def test_three_403_retries_restart_and_body_independence(tmp_path,clock,delay):
    for attempt in range(1,4):
        s,sp,m=context(tmp_path,delay)
        req=Request(LIST);asyncio.run(m.process_request(req,sp))
        if attempt>1:assert clock.waits[-1]>=delay
        clock.now+=10
        if attempt<3:assert isinstance(response(m,sp,req),Request)
        else:
            with pytest.raises(IgnoreRequest):response(m,sp,req)
        assert s.used('hankyung')==attempt;s.close()
    s,sp,m=context(tmp_path,delay)
    with pytest.raises(IgnoreRequest):asyncio.run(m.process_request(Request(LIST),sp))
    assert s.used('hankyung')==3
    asyncio.run(m.process_request(Request(BODY),sp));assert s.used('hankyung')==4;s.close()

def test_daily_budget_including_retry_persists(tmp_path,clock):
    s,sp,m=context(tmp_path);m.crawler.settings.set('DAILY_REQUEST_LIMIT',1)
    req=Request(LIST);asyncio.run(m.process_request(req,sp));retry=response(m,sp,req)
    with pytest.raises(IgnoreRequest):asyncio.run(m.process_request(retry,sp))
    assert s.used('hankyung')==1;s.close()
    s,sp,m=context(tmp_path);m.crawler.settings.set('DAILY_REQUEST_LIMIT',1)
    with pytest.raises(IgnoreRequest):asyncio.run(m.process_request(Request(BODY),sp))
    assert s.used('hankyung')==1;s.close()

@pytest.mark.parametrize('url,status,body',[(BODY,403,''),(LIST,200,'<title>Access denied</title>'),(BODY,429,'')])
def test_site_block_still_stops_requests(tmp_path,clock,url,status,body):
    s,sp,m=context(tmp_path);req=Request(url);asyncio.run(m.process_request(req,sp))
    with pytest.raises(IgnoreRequest):response(m,sp,req,status,body)
    with pytest.raises(IgnoreRequest):asyncio.run(m.process_request(Request(BODY),sp))
    assert s.used('hankyung')==1;s.close()

def test_operating_limits_unchanged():
    assert (settings.DOWNLOAD_DELAY,settings.AUTOTHROTTLE_START_DELAY,settings.AUTOTHROTTLE_MAX_DELAY)==(60,60,300)
    assert settings.DAILY_REQUEST_LIMIT==100
    assert settings.CONCURRENT_REQUESTS==settings.CONCURRENT_REQUESTS_PER_DOMAIN==1
