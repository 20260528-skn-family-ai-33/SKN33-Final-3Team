"""Loopback-only chat UI smoke fixture; uses temporary articles and no paid API."""
import asyncio
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import uvicorn
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from backend.app.api.config import Settings
from backend.app.api.worker import run_once
from backend.tests.api_support import SIGNUP_CONSENTS, create_test_app, reference_data, test_engine
from backend.tests.chat_news_fixture import indexed_news
from backend.tests.intent_fixtures import BASE


def main():
    dist = Path(__file__).resolve().parents[2] / 'frontend/dist'
    if not (dist / 'index.html').is_file():
        raise SystemExit('먼저 frontend에서 npm ci 및 npm run build를 실행하세요.')
    engine, meta = test_engine()
    reference_data(engine, meta)
    with engine.begin() as conn:
        conn.execute(meta.tables['article'].insert(), {
            'article_id':1, 'source_code':'KOTRA_MARKET', 'external_id':'1',
            'title':'테스트용 배터리 수출 기사', 'published_date':date(2026,10,30),
            'source_url':'https://example.org/news/1', 'body_masked':'합성 기사에서 배터리 수출을 설명한다.',
            'category':'경제·무역', 'visibility_status':'VISIBLE', 'admin_meta_json':{}})
    settings = Settings(values={'API_COOKIE_SECURE':'false','API_HS_VECTOR_ENABLED':'false',
                                'API_CORS_ORIGINS':'http://127.0.0.1:8765'})
    app = create_test_app(settings, engine)

    # The fixture shares a single SQLite connection across all requests.
    requests = asyncio.Lock()

    @app.middleware('http')
    async def finish_demo_job(request, call_next):
        async with requests:
            response = await call_next(request)
            if request.method == 'POST' and request.url.path.endswith('/messages') and response.status_code == 202:
                run_once(app.state.database, settings)
            return response

    app.mount('/', StaticFiles(directory=dist, html=True), name='demo-ui')
    with TestClient(app) as client:
        login = client.post('/api/v1/auth/signup', json={'name':'테스트','email':'demo@example.org',
                            'password':'Sample!123','password_confirm':'Sample!123',**SIGNUP_CONSENTS})
        login.raise_for_status()
    def intent(*, question, previous_conditions):
        return {**BASE, 'intent':'LOCAL_ISSUES' if '최신' in question else 'RAG',
                'latest_articles':'최신' in question}
    try:
        with indexed_news(SimpleNamespace(engine=engine, meta=meta)), patch(
                'backend.app.api.intent.OpenAIIntentProvider.extract', side_effect=intent):
            uvicorn.run(app, host='127.0.0.1', port=8765)
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
