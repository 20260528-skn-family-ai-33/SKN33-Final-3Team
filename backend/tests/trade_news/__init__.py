"""크롤러 테스트에서 CLI의 최상위 모듈(news, load_project 등)을 불러올 경로를 설정합니다."""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
for _path in (_ROOT, _ROOT / "backend/app/scheduler/collectors/trade_news"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
