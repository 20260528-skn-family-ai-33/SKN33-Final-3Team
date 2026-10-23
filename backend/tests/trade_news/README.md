# 기사 수집 리뷰 회귀 테스트

저장소 루트에서 실행합니다. Python 3.12 기준입니다.

```sh
python -m pip install -r backend/requirements.txt pytest
python -m pytest backend/tests/trade_news -q
```

기본 시험은 DNS/TCP 차단, 합성 기사, 실제 SQLAlchemy+SQLite, 가상 HTTP 기반 Scrapy 엔진을 사용합니다. 원문과 정제 본문 보존·소득 오탐·정상 교역 유지·요청 예산/403 재시작·적재 계획·트랜잭션·집계 뷰·재실행을 확인합니다. 실제 사이트와 MySQL은 연결하지 않습니다.

`test_run_lifecycle.py`는 이전 성공 보고서 보존, 시작 상태 기록, 다른 날짜의 중단 이력 구분, 실패 결과 분리를 확인합니다. 별도 시험 프로세스를 DB 커밋 직후 또는 JSON 교체 직전에 강제 종료하고, 재실행 시 본문 재요청 없이 DB·JSON·집계가 복구되는지 검사합니다. 실제 기사·운영 데이터는 사용하지 않습니다.

## 로컬 MySQL 8.4 회귀 시험

기존 ERD 1개와 관리자 ERD 14개, 총 15개 시험입니다. Python 3.12와 로컬 MySQL 8.4를 사용합니다. 기본 pytest 실행에서는 15개 모두 **DB 연결·환경변수 요구 없이 skip**됩니다. 실제 실행에는 `--trade-news-mysql`과 `TRADE_NEWS_TEST_MYSQL_URL`이 모두 필요합니다.

1. 로컬 MySQL에서 테이블·뷰가 없는 전용 `test_` 접두사 DB를 준비합니다. 같은 DB의 시험은 직렬로 실행합니다.
2. 해당 DB에서 테이블·뷰·트리거 생성/삭제와 자료 조회/입력/수정/삭제가 가능한 시험 계정을 사용합니다. 시험은 DB 자체를 생성하거나 삭제하지 않습니다.
3. 바이너리 로그 설정을 확인합니다. `log_bin=1`, `log_bin_trust_function_creators=0`이면 DB 전용 권한만으로 트리거를 생성할 때 오류 `1419`가 발생합니다. 아래 설정은 **전용 로컬 시험 서버의 관리자 계정**에서만 확인·변경합니다.

   ```sql
   SELECT VERSION(), @@GLOBAL.log_bin, @@GLOBAL.log_bin_trust_function_creators;
   -- 결과가 각각 log_bin=1, log_bin_trust_function_creators=0일 때만 적용
   SET GLOBAL log_bin_trust_function_creators = 1;
   ```

   설정 후에는 DB 전용 권한의 시험 계정으로 아래 pytest를 실행합니다. 시험 계정에 `SUPER` 권한을 추가할 필요는 없습니다. 시험을 위해 값을 0에서 1로 바꿨다면, 모든 시험이 끝난 뒤 관리자 계정으로 `SET GLOBAL log_bin_trust_function_creators = 0;`을 실행해 원래 값으로 되돌립니다. 이미 1이었거나 바이너리 로그가 꺼져 있다면 이 변경은 필요하지 않습니다. 시험 코드는 이 전역 설정을 자동 변경하지 않으며, 이 절차를 RDS·공유 운영 서버에 적용하지 않습니다.
4. 터미널에 시험 URL을 설정합니다. 프로젝트 `.env`와 `MYSQL_*`는 읽지 않습니다. 아래 값은 예시이며 실제 접속 정보를 Git에 저장하지 않습니다.

```sh
# 로컬 TCP 예시. 비밀번호의 URL 예약 문자는 percent-encoding해야 합니다.
export TRADE_NEWS_TEST_MYSQL_URL='mysql+pymysql://TEST_USER:TEST_PASSWORD@127.0.0.1:3306/test_trade_news?charset=utf8mb4'
# Unix 소켓도 지원: localhost 주소와 unix_socket=/절대/소켓/경로 쿼리 사용

python -m pytest -q -p no:cacheprovider \
  backend/tests/trade_news/test_mysql_integration.py \
  backend/tests/trade_news/test_mysql_admin_integration.py \
  --trade-news-mysql
```

위 명령은 설치된 로컬 서버를 사용합니다. 시험 코드가 서버 설치·시작·서비스 등록을 수행하지 않습니다. 허용 주소는 `localhost`, `127.0.0.1`, `::1`이며, 지정한 MySQL 포트 또는 Unix 소켓 이외 연결과 스케줄러 시작을 부모·강제 종료 시험 자식 모두에서 차단합니다. RDS·실제 기사 사이트에는 접속하지 않습니다. 시험 자료는 합성 기사·국가·HS 코드이며 정책 스냅샷과 결과는 pytest 임시 폴더에만 기록합니다.

| 범위 | 시험 수 |
|---|---:|
| 기존 ERD: 트랜잭션·재적재·충돌·태그 롤백 | 1 |
| 관리자 필수값·동일 기사 재실행 | 1 |
| 관리자 설정·수동 국가/HS 태그 보존 | 3 |
| 정책 변경 시 신규 기사에만 반영 | 1 |
| 스냅샷 저장 실패·손상 롤백 | 2 |
| 실제 JSON CHECK·INSERT 트리거의 잘못된 입력 거부 | 3 |
| 실행 이력 JSON 보존·복구 | 1 |
| SOURCE 누락 시 실행 이력 원자적 롤백 | 1 |
| 적재·집계·보고서·재실행 | 1 |
| 첫 article 커밋 직후 자식 강제 종료·재실행 복구 | 1 |
| 합계 | 15 |

`fixtures/mysql_admin_schema.sql`은 2026-10-22 관리자 ERD 중 기존 MySQL 시험에 쓰인 12개 의존 테이블·3개 트리거를 발췌한 **시험 전용 정의**입니다. JSON CHECK·외래 키·트리거를 실제 MySQL에서 실행하며 운영 초기화·마이그레이션에는 연결하지 않습니다. 기존 ERD 시험은 기존 `db/mysql/init/01_schema.sql`을 계속 사용합니다.

정상 완료나 pytest가 처리하는 실패에서는 이번 시험에서 생성에 성공한 뷰·테이블만 역순으로 삭제합니다. 기존 객체가 있으면 아무것도 삭제하지 않고 중단합니다. 부모 pytest 자체를 강제 종료하면 시험 객체가 남을 수 있으므로, 시험 DB임을 확인한 뒤 관리자가 정리하고 다시 빈 DB로 실행해야 합니다. 기사 커밋 직후 자식만 종료하는 회귀 시험에서는 부모가 남은 시험과 정리를 수행합니다. 소켓 없음·접속 거부는 로컬 서버/포트/계정 설정을, 권한 오류는 시험 DB의 DDL 권한을, 오류 `1419`는 위 바이너리 로그·트리거 설정을 확인하세요. 설정이 없거나 서버가 잘못된 경우 통과나 skip으로 숨기지 않고 실패로 표시합니다.

`test_run_publication.py`는 시작·완료에서 실제 호출하는 모든 JSON 기록 지점을 찾아, 각 지점 직전의 예외·강제 종료와 직후의 강제 종료를 주입합니다. 확정 전 실패 시 성공 결과 보존, 확정 후 출력 실패의 별도 대기 표시, 다음 날짜 실행에서 과거 최신 상태 복구를 검증합니다. 낡은 표시용 파일이 확정 상태를 덮어쓰지 않는지도 확인합니다.


`test_batch_history.py`는 공통 `BatchRun`·`ServiceOption` 모델을 임시 SQLite에 만들어 실행 이력을 확인합니다. 수집/적재별 상태·출처별 건수, 기본 명령의 DB 미연결, 기록 DB 장애 시 기사 처리 유지, SOURCE 자동 생성 방지, 같은 실행의 중복 기록 방지, 강제 종료 후 복구를 검사합니다. 가상 HTTP 응답으로 실제 일일 진입점과 Scrapy를 실행하며, 실제 사이트·MySQL·예약 실행은 사용하지 않습니다.


`test_daily_scheduler.py`는 실제 APScheduler의 등록 상태·KST 03:00 계산만 검사하며 `scheduler.start()`를 실행하지 않습니다. 작업 함수는 테스트용 자식 실행기를 통해 가상 HTTP와 임시 SQLite에 연결합니다. 전날 날짜·출처 전달, article 적재와 뷰 집계, 재실행, 403·0건·충돌·강제 종료 복구, 이전 보고서 재사용 방지를 확인합니다. 공용 main import에 필요한 OpenAI/Chroma SDK는 미사용 대역으로 두며 해당 서비스 동작 검증으로 세지 않습니다.

추가 복구 검증: 부모만 강제 종료한 뒤 수집/적재 자식이 살아 있으면 새 호출은 `already_running`으로 기존 상태를 보존합니다. 자식 종료 후 재실행의 재수집·재삽입 방지, 늦게 시작한 이전 자식의 요청·DB 쓰기 차단, 일일 CLI의 중간 프로세스 제거, 상세 결과 참조와 요약 일치도 합성 자료로 확인합니다.

`test_admin_erd.py`는 `backend/tests/news_erd_fixture.py`의 신규 기사 INSERT 제약과 필수 JSON을 임시 SQLite에 적용합니다. 기사 최초 수신 정보·정책 스냅샷·batch_run 상세 JSON, 기존 관리자 설정·수동 국가/HS 태그 보존, 정책 변경 시 새 기사에만 반영, 스냅샷 오류 롤백을 검사합니다. 일일 작업의 첫 article 커밋 직후 시험 자식을 종료하고 재실행해 본문 재요청·중복 INSERT 없이 적재와 실행 이력이 복구되는지도 검증합니다. 정책 스냅샷은 시험 폴더에만 기록합니다.

2026-10-23 별도 로컬 MySQL 8.4.11에서 기존 ERD 1개와 관리자 ERD 14개, 총 15개 시험이 통과했습니다. 관리자 필수값·JSON CHECK·트리거·수동 태그 보존·롤백·집계·강제 종료 복구를 확인했습니다. 기존 14개 실행기를 `test_mysql_admin_integration.py`로 옮겨 이제 위 명시 명령으로 저장소에서 재현할 수 있습니다. 옵션 없는 기본 실행에서는 MySQL 시험 15개가 모두 skip됩니다. RDS 환경과 실사이트 요청·실제 예약 실행 검증은 포함하지 않습니다.

저장소용 구성으로 정리한 뒤 2026-10-23에 같은 명시 명령으로 **15 passed, 0 skipped**를 다시 확인했습니다. 전용 DB 권한만 가진 임시 계정을 사용했고, 시험 후 테이블·뷰·트리거가 남지 않았습니다. 기본 회귀 시험과 공용 스케줄러 등록 시험 3개는 **162 passed, 15 skipped**였으며, MySQL 15개는 명시 옵션 없는 실행에서 연결 없이 건너뛴 결과입니다. 실제 사이트·RDS·예약은 실행하지 않았습니다.
