# 기사 수집 리뷰 회귀 테스트

저장소 루트에서 실행합니다. Python 3.12 기준입니다.

```sh
python -m pip install -r backend/app/scheduler/collectors/trade_news/requirements.txt -r backend/app/scheduler/collectors/trade_news/requirements-mysql.txt pytest
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
