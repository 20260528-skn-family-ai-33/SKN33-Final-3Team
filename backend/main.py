"""uvicorn backend.main:app --host 127.0.0.1 --port 8000"""
from backend.app.api.application import create_app

app = create_app()
