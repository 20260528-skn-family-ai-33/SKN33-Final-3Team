```
SKN33-Final-3Team/
├── .github/                      GitHub 설정
│   ├── ISSUE_TEMPLATE/           이슈 템플릿 (bug_report, feature---task)
│   └── PULL_REQUEST_TEMPLATE.md  PR 템플릿
├── .gitignore                    저장소에 올리지 않을 파일 목록
│
├── frontend/                     ① 사용자 화면 (React)
│   └── src/
│       ├── pages/                화면 단위: S00 로그인 ~ S11 마이페이지
│       ├── components/           여러 화면에서 같이 쓰는 부품 (알림 팝업, 차트, 기사 카드 등)
│       └── api/                  백엔드 API 호출 함수
│
├── backend/                      ②③④⑤ 서버 전체 (FastAPI + LangGraph + 스케줄러)
│   ├── app/
│   │   ├── routers/              ② API 엔드포인트
│   │   │                            auth, chat, market, my, alerts, reports, me, hs
│   │   ├── agents/               ③ AI 에이전트 (LangGraph)
│   │   │                            supervisor(라우터), sql, rag, verifier(검증 관문), report
│   │   ├── ml/                   ML 모델: 급변 탐지(Isolation Forest), 수출액 예측(Prophet/LightGBM),
│   │   │                            국가 군집(K-Means). 월 배치 때 학습하고, 결과는 DB에 저장
│   │   ├── scheduler/            ⑤ 배치 스케줄러 (APScheduler)
│   │   │   ├── main.py           스케줄러 실행: python -m backend.app.scheduler.main
│   │   │   ├── collectors/       ⑥ 외부 API 수집 — 호출과 파싱만 담당
│   │   │   │   ├── customs/      관세청 수출입실적 API (CUSTOMS_COLLECT_USAGE.md)
│   │   │   │   ├── news/         KOTRA 해외시장뉴스 (NEWS_COLLECT_USAGE.md)
│   │   │   │   ├── shortnews/    KOTRA 단신속보 (SHORTNEWS_COLLECT_USAGE.md)
│   │   │   │   └── hscode/       HS 코드 마스터 CSV 생성·UN 코드 대조 (HSCODE_USAGE.md)
│   │   │   └── jobs/             수집 → 검증 → 적재 → 알림 순서 실행
│   │   │                            hs_code_load.py(적재), hs_code_refresh.py(연간 자동 갱신)
│   │   ├── db/                   ④ MySQL 연결(session.py), 공통 CRUD(crud.py), 실행 기록(batch_log.py → batch_run)
│   │   ├── models/               SQLAlchemy 테이블 정의 (hs_code, service_option, batch_run …)
│   │   ├── schemas/              API 요청·응답 형식 (Pydantic)
│   │   ├── services/             라우터와 배치가 같이 쓰는 로직 (관심 범위 계산, 알림 생성 등)
│   │   └── templates/
│   │       └── reports/          리포트 Jinja2 템플릿 (HTML → PDF)
│   ├── requirements.txt          백엔드 패키지 (pip install -r backend/requirements.txt)
│   └── tests/                    백엔드 테스트 (python -m unittest discover backend/tests)
│
├── db/
│   └── mysql/
│       └── init/                 MySQL 초기 설정 SQL: 테이블 생성, 인덱스, 읽기 전용 계정
│
├── notebooks/                    데이터 탐색, ML 실험, 골든셋으로 LLM 비교
└── docs/                         프로젝트 문서 (아키텍처, 흐름도, API 명세 등)

```
