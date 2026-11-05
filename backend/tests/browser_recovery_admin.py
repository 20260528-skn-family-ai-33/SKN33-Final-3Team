"""수동 브라우저 회귀용 localhost 앱. SQLite·가짜 Redis·모의 메일만 사용한다.

python -m backend.tests.browser_recovery_admin
frontend에서 node tests/recovery-admin-browser.cjs 실행 (먼저 npm run build).
"""
import asyncio
from pathlib import Path
from fastapi.staticfiles import StaticFiles
from backend.tests.test_recovery_keyword_admin import RecoveryKeywordAdminTest
from backend.tests.api_support import create_test_app
from backend.app.api import recovery


def main():
    import uvicorn
    case = RecoveryKeywordAdminTest()
    case.setUp()
    try:
        case.seed_admin()
        case.app.state.settings.values['API_CORS_ORIGINS'] = 'http://127.0.0.1:8876'
        app = create_test_app(case.app.state.settings, case.engine)
        serial = asyncio.Lock()

        @app.middleware('http')
        async def sqlite_single_connection(request, call_next):
            # SQLite in-memory StaticPool 연결 하나를 병렬 요청이 공유하지 않게 한다.
            async with serial:
                return await call_next(request)

        from unittest.mock import patch
        root = Path(__file__).resolve().parents[2]
        app.mount('/', StaticFiles(directory=root/'frontend/dist', html=True), name='frontend')
        with patch.object(recovery, 'send_temporary_password', return_value=None):
            uvicorn.run(app, host='127.0.0.1', port=8876, log_level='warning')
    finally:
        case.doCleanups()


if __name__ == '__main__':
    main()
