"""직접 CLI에서도 팀 공통 사전 모듈을 찾도록 저장소 루트를 연결합니다."""
from pathlib import Path
import sys

_PROJECT_ROOT = Path(__file__).resolve().parents[6]
if (_PROJECT_ROOT / 'backend/app').is_dir() and str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
