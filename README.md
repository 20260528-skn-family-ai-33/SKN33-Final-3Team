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
│   │   │   ├── collectors/       ⑥ 외부 API 수집 — 호출과 파싱만 담당
│   │   │   │                        customs.py, kotra_news.py, kotra_flash.py, ecos.py (예정)
│   │   │   └── jobs/             수집 → 검증 → 적재 → 알림 순서 실행
│   │   │                            hourly.py, daily.py, monthly.py (예정)
│   │   ├── db/                   ④ MySQL·Chroma 연결 설정 (분석용 읽기 전용 계정 분리)
│   │   ├── models/               SQLAlchemy 테이블 정의 (통계, 기사, 사용자, 알림, 리포트 등)
│   │   ├── schemas/              API 요청·응답 형식 (Pydantic)
│   │   ├── services/             라우터와 배치가 같이 쓰는 로직 (관심 범위 계산, 알림 생성 등)
│   │   └── templates/
│   │       └── reports/          리포트 Jinja2 템플릿 (HTML → PDF)
│   └── tests/                    백엔드 테스트 (SQL 검사, 수치 대조 등)
│
├── db/
│   └── mysql/
│       └── init/                 MySQL 초기 설정 SQL: 테이블 생성, 인덱스, 읽기 전용 계정
│
├── notebooks/                    데이터 탐색, ML 실험, 골든셋으로 LLM 비교
└── docs/                         프로젝트 문서 (아키텍처, 흐름도, API 명세 등)