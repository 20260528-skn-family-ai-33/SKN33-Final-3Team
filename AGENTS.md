# AI 작업 안내

- 채팅 에이전트(`backend/app/agents/`), 채팅 API(`backend/app/api/chat.py`, `chat_slots.py`, `intent.py`), 작업 실행기(`backend/app/api/jobs.py`, `worker.py`), Redis 관련 코드를 바꾸기 전에 [docs/AGENT_DESIGN.md](docs/AGENT_DESIGN.md)와 [backend/app/agents/state.py](backend/app/agents/state.py)를 먼저 읽고 그 설계를 따른다.
- 설계 문서 §9의 미정 사항은 추측해서 구현하지 않는다. 임시값을 설정으로 빼고 `TODO(C-xx)`를 남긴다. 채팅 API를 바꿀 때는 [docs/AGENT_API_CHANGES.md](docs/AGENT_API_CHANGES.md)를 따른다.
- 백엔드 실행·API 현황은 [docs/BACKEND_API.md](docs/BACKEND_API.md)를 본다.
- 테스트: `python -m unittest discover backend/tests`. 외부 연결(LLM, RDS, Redis, Chroma) 없이 돌아야 한다.
