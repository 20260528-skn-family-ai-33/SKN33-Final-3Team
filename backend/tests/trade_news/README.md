# 기사 수집 리뷰 회귀 테스트

저장소 루트에서 실행합니다. Python 3.12 기준입니다.

```sh
python -m pip install -r backend/requirements.txt pytest
python -m pytest backend/tests/trade_news -q
```

기본 시험은 DNS/TCP 차단, 합성 기사, 실제 SQLAlchemy+SQLite, 가상 HTTP 기반 Scrapy 엔진을 사용합니다. 원문과 정제 본문 보존·소득 오탐·정상 교역 유지·요청 예산/403 재시작·적재 계획·트랜잭션·집계 뷰·재실행을 확인합니다. 실제 사이트와 MySQL은 연결하지 않습니다.

`test_run_lifecycle.py`는 이전 성공 보고서 보존, 시작 상태 기록, 다른 날짜의 중단 이력 구분, 실패 결과 분리를 확인합니다. 별도 시험 프로세스를 DB 커밋 직후 또는 JSON 교체 직전에 강제 종료하고, 재실행 시 본문 재요청 없이 DB·JSON·집계가 복구되는지 검사합니다. 실제 기사·운영 데이터는 사용하지 않습니다.

실제 MySQL 테스트는 기본 skip입니다. 별도 승인을 받은 뒤 MySQL 8.4의 **빈 전용 test_ 접두사 DB**와 DDL 가능한 테스트 계정을 준비합니다. 운영 DB를 지정하지 마세요. test_ 이름만으로 환경 안전성이 보장되지는 않으므로 대상을 확인해야 합니다.

```sh
# TRADE_NEWS_TEST_MYSQL_URL=mysql+pymysql://.../test_trade_news 를 환경에 설정 (Git에 저장 금지)
python -m pytest backend/tests/trade_news/test_mysql_integration.py --trade-news-mysql -q
```

실제 schema 파일의 필요한 테이블을 생성하고 종료 시 그 테이블만 삭제합니다. 프로세스 강제 종료 시 테스트 DB에 남은 테이블은 직접 확인해야 합니다. 실서버 SQL·외래 키·잠금 동작은 이 테스트를 실제 실행하기 전까지 미검증입니다.

`test_run_publication.py`는 시작·완료에서 실제 호출하는 모든 JSON 기록 지점을 찾아, 각 지점 직전의 예외·강제 종료와 직후의 강제 종료를 주입합니다. 확정 전 실패 시 성공 결과 보존, 확정 후 출력 실패의 별도 대기 표시, 다음 날짜 실행에서 과거 최신 상태 복구를 검증합니다. 낡은 표시용 파일이 확정 상태를 덮어쓰지 않는지도 확인합니다.


`test_batch_history.py`는 공통 `BatchRun`·`ServiceOption` 모델을 임시 SQLite에 만들어 실행 이력을 확인합니다. 수집/적재별 상태·출처별 건수, 기본 명령의 DB 미연결, 기록 DB 장애 시 기사 처리 유지, SOURCE 자동 생성 방지, 같은 실행의 중복 기록 방지, 강제 종료 후 복구를 검사합니다. 가상 HTTP 응답으로 실제 일일 진입점과 Scrapy를 실행하며, 실제 사이트·MySQL·예약 실행은 사용하지 않습니다.


`test_daily_scheduler.py`는 실제 APScheduler의 등록 상태·KST 03:00 계산만 검사하며 `scheduler.start()`를 실행하지 않습니다. 작업 함수는 테스트용 자식 실행기를 통해 가상 HTTP와 임시 SQLite에 연결합니다. 전날 날짜·출처 전달, article 적재와 뷰 집계, 재실행, 403·0건·충돌·강제 종료 복구, 이전 보고서 재사용 방지를 확인합니다. 공용 main import에 필요한 OpenAI/Chroma SDK는 미사용 대역으로 두며 해당 서비스 동작 검증으로 세지 않습니다.

추가 복구 검증: 부모만 강제 종료한 뒤 수집/적재 자식이 살아 있으면 새 호출은 `already_running`으로 기존 상태를 보존합니다. 자식 종료 후 재실행의 재수집·재삽입 방지, 늦게 시작한 이전 자식의 요청·DB 쓰기 차단, 일일 CLI의 중간 프로세스 제거, 상세 결과 참조와 요약 일치도 합성 자료로 확인합니다.

`test_admin_erd.py`는 `backend/tests/news_erd_fixture.py`의 신규 기사 INSERT 제약과 필수 JSON을 임시 SQLite에 적용합니다. 기사 최초 수신 정보·정책 스냅샷·batch_run 상세 JSON, 기존 관리자 설정·수동 국가/HS 태그 보존, 정책 변경 시 새 기사에만 반영, 스냅샷 오류 롤백을 검사합니다. 일일 작업의 첫 article 커밋 직후 시험 자식을 종료하고 재실행해 본문 재요청·중복 INSERT 없이 적재와 실행 이력이 복구되는지도 검증합니다. 정책 스냅샷은 시험 폴더에만 기록합니다.

2026-10-23 별도 로컬 MySQL 8.4.11에서 기존 ERD 1개와 관리자 ERD 14개, 총 15개 시험이 통과했습니다. 관리자 필수값·JSON CHECK·트리거·수동 태그 보존·롤백·집계·강제 종료 복구를 확인했습니다. 관리자 MySQL 14개 시험 실행기는 별도 로컬 검증 작업 폴더에 있으며 이 PR의 기본 pytest 명령에 포함되지 않습니다. RDS 환경과 실사이트 요청·실제 예약 실행 검증은 포함하지 않습니다.
