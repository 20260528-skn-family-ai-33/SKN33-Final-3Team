# SQL·Visualization Agent 오프라인 평가

평가일: 2026-11-09 (KST) · 관련 이슈 #74 / PR #75

## 목적과 평가 범위

SQL Agent의 조건 검증·집계·실패 처리와 Visualization Agent의 결과 변환이
설계 계약을 만족하는지 실제 실행으로 평가한다. 기존 구현과 개선 구현에 같은
입력·기대 결과·판정 기준을 적용한다.

**이 문서의 점수는 선정한 회귀 사례의 통과율이다. 실제 LLM의 자연어→SQL 정확도나
서비스 전체 정확도가 아니다.** 수정한 기능과 경계 사례를 중심으로 선정했으므로
일반 사용자 질문의 분포를 대표하지 않으며, 미공개 검증 데이터셋도 아니다.

## 결과

| 평가 영역 | PR 작업 전 | 리뷰 수정 전 | 현재 | 판정 단위 |
|---|---:|---:|---:|---|
| SQL | 14/17 (82.35%) | 17/17 (100%) | 17/17 (100%) | 조건·안전성·집계·실패 처리 |
| Visualization | 3/14 (21.43%) | 13/14 (92.86%) | 14/14 (100%) | 월 축·날짜·정렬·이름·표시 상한 |
| SQL→Visualization | 4/5 (80%) | 4/5 (80%) | 5/5 (100%) | 조회→저장→차트 및 접근 제어 |
| 합계 | 21/36 (58.33%) | 34/36 (94.44%) | 36/36 (100%) | 등록된 사례 36개 |

- PR 작업 전: `1f2c7668365baea75d4df0ac8895166133952bef`
- 리뷰 수정 전: `5a9966c581bede2cab73e4e65a77bfea06c15073`
- 현재 평가 대상: `5302c47` 이후 작업 트리의 `sql.py`, `charts.py` (두 운영 모듈은 해당 커밋과 동일)
- 과거 비교는 **두 운영 모듈만 해당 커밋으로 메모리에서 교체**한다. 나머지 코드,
  평가 테스트, fixture, 의존성은 동일하다. 과거 브랜치 전체의 테스트 결과를 뜻하지 않는다.
- Git checkout·reset·파일 덮어쓰기 없이 각각 별도 프로세스에서 실행했다.

## 입력과 판정 기준

평가 실행 코드의 `CASES`에는 ID, 담당 Agent, 평가 항목, 통과 기준, 실행할 테스트가
등록되어 있다. 입력과 기대 값은 참조된 테스트에 고정되어 있다.

| 사례 | 고정 입력 예시 | 기대 결과 |
|---|---|---|
| S01 | 확정 국가 US, HS 3304, HS2022, 조회·비교 기간 | 범위를 벗어난 SQL 거부, 확정 범위 SQL 허용 |
| S08 | DELETE, 비공개 표, 본문, 파일 출력, 잠금 등의 SQL | 실행 전 거부 |
| S11–S13 | Infinity·NaN·sNaN·큰 정수, 0.0005초 제한 | 검증 오류·재시도·연결 정리, 최소 1ms |
| S15 | 누락 월이 있는 길이가 다른 비교 기간 | 총액 null, 검증 월로 계산한 월평균 |
| V01 | 국가별로 서로 다른 월이 누락된 행 | 공통 월 축과 각 국가의 null 유지 |
| V04 | 순위 12행 / 65행 | 12개 표시 / 60개 표시 및 생략 안내 |
| V05–V06 | 역순 날짜, 2월 30일, 월·일 형식 혼합 | 날짜·값 동시 정렬 / 부적절한 시간축 생략 |
| V08 | 60·61·180·181·720·732개월, 중간 월 누락 | 월→분기→연도 전환, 60년 초과 생략, null 유지 |
| V09·V12 | 같은 국가·품목 이름, 코드 접미사와 같은 실제 이름 | 값 순서를 유지하면서 라벨 충돌 해소 |
| V11 | 총액 null, 검증 소계 동률, 누락 월과 실제 0 | 소계 내림차순·동률 저장 순서·null/0 구분 |
| I01–I02 | SQLite의 미국·중국 수입/수출 자료 | 저장된 값과 차트 일치, 국가별 구분, SQL 재실행 없음 |
| I03 | VN·US·JP·CN·TH·DE·AU 모두 0, 수입/수출·비교 유무 | 표·요약·차트가 VN·US·JP·CN·TH 순서 유지 |
| I04 | 타인 소유 결과 / 만료 결과 | 각각 404 / 410, 원본 SQL 재실행 없음 |
| I05 | 길이가 다른 불완전 기간 | 저장된 월평균으로 비교하고 그 사실 안내 |

전체 36개 기준과 테스트 경로는 [평가 실행 코드](../backend/scripts/evaluate_sql_visualization.py)와
아래 JSON의 `cases`에 있다. 대부분은 기존 테스트를 재사용한다. 기간 상한 평가는
과거 구현에도 같은 입력 계약을 적용하도록 내부 helper 대신 `build_chart` 진입점으로 실행한다.

### 채점

- unittest 테스트 메서드 하나를 평가 사례 하나로 센다. 여러 subtest가 있어도 분모는 1이다.
- 모든 assertion·subtest가 통과해야 PASS다. 한 subtest만 실패해도 그 사례 전체가 FAIL이다.
- 예외는 ERROR, 건너뜀은 SKIP, 예상 실패는 XFAIL로 기록하며 모두 통과율 분모에 포함한다.
- 통과율 = PASS 사례 수 / 등록 사례 수 × 100. 가중 평균이나 임의 LLM 채점은 사용하지 않는다.
- 현재 결과는 PASS 36개, 나머지 상태 0개다.
- 모의 실행의 소요 시간을 실제 모델 응답 시간이나 운영 DB 성능으로 보고하지 않는다.

## 개선과 회귀 확인

작업 전 대비 미통과였던 항목은 S11–S13, V01–V09, V11–V12, I05다.
날짜·이름·누락 월·상한·안내 문구까지 판정하므로, 모든 미통과가 잘못된 금액 반환을
뜻하는 것은 아니다. JSON의 `details`에 실제 assertion 또는 예외가 기록되어 있다.

리뷰 수정 전에는 V11과 I03이 미통과했다. 금액 동률을 국가코드로 재정렬하면서
표·요약과 차트의 대상 순서가 달라졌기 때문이다. 이 부분은 PR 작업 전 구현에서는
통과했으므로, PR 중 도입되었다가 리뷰 후 복구된 회귀로 구분한다. 현재는 둘 다 통과한다.

## 재현 방법

저장소 루트에서 백엔드 의존성이 설치된 Python 3.11 환경을 사용한다.
평가기는 네트워크 연결과 DNS 조회를 차단하고, SQLAlchemy SQLite fixture와 mock만 사용한다.
LLM·RDS·Redis·Chroma 및 API 키는 필요하지 않다.

```powershell
python -m backend.scripts.evaluate_sql_visualization --output docs/evaluations/sql_visualization_current.json
python -m backend.scripts.evaluate_sql_visualization --source-ref 1f2c766 --output docs/evaluations/sql_visualization_before.json
python -m backend.scripts.evaluate_sql_visualization --source-ref 5a9966c --output docs/evaluations/sql_visualization_before_review.json
```

현재 평가는 종료 코드 0이다. 과거 비교는 미통과 사례가 있으므로 종료 코드 1이 정상적인
평가 결과다. 해당 Git 커밋이 로컬에 있어야 과거 비교가 가능하다.

```powershell
python -m unittest backend.tests.test_agent_sql_visualization backend.tests.test_agent_sql_visualization_evaluation
```

평가기 자체도 실패한 subtest, SKIP, ERROR를 PASS로 집계하지 않는지와 분모·사례 등록을 검증한다.

## 실행 증거

- [현재 결과 JSON](evaluations/sql_visualization_current.json)
- [PR 작업 전 결과 JSON](evaluations/sql_visualization_before.json)
- [리뷰 수정 전 결과 JSON](evaluations/sql_visualization_before_review.json)

각 파일에는 평가 시각, Git 기준, Python·패키지 버전, 평가 기준·운영 모듈·fixture·테스트의
SHA256, 항목별 상태 및 실패 내역이 포함된다. 비교할 때 동일한 `manifest_sha256`과
`evidence_sha256`인지 확인한다. 소스 해시는 비교 대상 버전에 따라 달라진다.

## 미평가 및 후속 항목

- 실제 모델의 자연어 해석·SQL 생성 정확도: 모델 응답을 mock하므로 측정하지 않았다.
- 운영 MySQL 문법·쿼리 계획·시간 초과·성능: SQLite 및 연결 mock 검증과 별개다.
- 브라우저 렌더링·색상·음수 축·전체 누락값 축: 이 평가에 포함하지 않았다.
  프론트 `niceTicks([-10, -5])`의 양수 축과 `niceTicks([])`의 Infinity 문제는 별도 후속 작업이다.
- 전체 백엔드 테스트 통과 여부를 대신하지 않는다. 이전 전체 검수는 Windows 프로세스 오류
  `0xC0000005`로 종료되었으며, 담당 에이전트 테스트 결과와 분리해 해석해야 한다.
- 실제 LLM 평가는 별도의 고정 자연어 질문·정답 결과표로 수행해야 한다. SQL 문자열 일치가
  아니라 결과 행·수치·범위 일치, 실행 성공률, 안전성, 지연·비용을 모델·프롬프트 버전과 함께 기록한다.
