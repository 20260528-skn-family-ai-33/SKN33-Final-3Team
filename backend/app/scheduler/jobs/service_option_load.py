"""공용 옵션 검증 / DB 조회 / 보존 적재 (기본은 DB에 접속하지 않는 validate).

저장소 루트에서 실행:
    python -m backend.app.scheduler.jobs.service_option_load --mode validate
    python -m backend.app.scheduler.jobs.service_option_load --mode check
    python -m backend.app.scheduler.jobs.service_option_load --mode load
    python -m backend.app.scheduler.jobs.service_option_load --mode load --apply-plan <plan.json>

check가 생성한 plan.json의 기존 차이 행에서 apply만 true로 바꾸면 명시 갱신한다.
원하는 값은 seed에서 수정한 뒤 validate/check로 계획을 다시 만든다.
실행 기록: data/service_option_load_outputs/<시각>_<실행ID>/

SOURCE value_json은 url, license, daily_limit, data_period,
source_update_frequency, jobs를 지원한다. 작업별 job_name/schedule/collects/
collection_scope/target_tables를 검증하며, AFTER_JOB의 중복·선행 작업·순환을 검사한다.
실행 방식: DAILY(time/timezone), INTERVAL(every_minutes), MANUAL,
AFTER_JOB(after_job_name). 범위: RECENT_MONTHS(anchor/months),
RECENT_DAYS(days/timezone), EXPLICIT_PERIOD(start/end).
기존 수출입 작업명 CUSTOMS_CSV_LOAD(수출입 원본), HS6_AGGREGATION(수출입 집계),
CUSTOMS_METRICS(수출입 원본 계산)를 지원한다. 각 고정 작업은 두 대상이 모두 필요하다.
새 job_name은 서비스 모듈의 JOB_TARGETS에 필수/선택 대상 계약을 추가한 뒤 사용한다.
등록 가능한 테이블과 실제 DB 테이블·batch_run ENUM의 배포 여부는 별도로 보고한다.

기본 seed는 확정된 12개 옵션만 등록한다. SOURCE의 null은 미확정이며 무제한이나
이용조건 확인 완료가 아니다. jobs=[]는 실행 설정 미구성으로 보고한다.
주간·월간·분기는 발행 설정이 비어 있으므로 기본 enabled=false다. 활성 {}는 거부하며,
활성화할 때 주기별 필수 키와 활성 REPORT_TYPE 참조를 검사한다. REPORT_TYPE의 {}는 허용한다.
확정된 운영값은 같은 manifest 형식의 별도 JSON을 --seed로 전달한다.
RULE은 CUSTOMS_ANOMALY/INTEREST_SCOPE/TRADE_CHANGE_RULE/NEWS_SPIKE_RULE,
주기는 WEEKLY/MONTHLY/QUARTERLY, 약관은 TERMS_ 버전의 body/effective_at을 지원한다.
가이드의 무역·기사 임계값, 분기 trigger, 발행 시각, 약관 예시는 기본 seed에 넣지 않는다.

service_option은 실행 설정, batch_run은 각 소비 작업의 실제 실행 이력이다.
이 CLI는 설정만 적재한다. SOURCE.jobs를 실행하거나 스케줄러·batch_run을 생성하지 않는다.
이미 있는 SOURCE 메타데이터도 기본 보존한다. 새 필드를 반영하려면 check의
계획에서 해당 행의 apply만 true로 검토한 뒤 --apply-plan으로 명시 적용한다.
기존 활성 빈 발행 설정 때문에 check가 실패한 경우에도 정정용 plan.json은 남긴다.
원인과 비활성화 변경을 검토한 뒤 해당 행만 apply=true로 지정한다. 자동 정정하지 않는다.
MySQL 통합 테스트는 SERVICE_OPTION_TEST_DATABASE=service_option_test_<이름> 및
SERVICE_OPTION_TEST_HOST/PORT/USER/PASSWORD를 명시한 전용 새 DB에서만 수행한다.
"""
import argparse
import json
from pathlib import Path

from backend.app.db.session import ENV_PATH
from backend.app.services.service_option_load import DEFAULT_OUTPUT, DEFAULT_SEED, run


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=("validate", "check", "load"), default="validate")
    parser.add_argument("--seed", type=Path, default=DEFAULT_SEED, help="UTF-8 JSON 기준정보 파일")
    parser.add_argument("--env", type=Path, default=ENV_PATH, help="MYSQL_* 파일 (환경변수 우선)")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="실행별 결과 폴더의 상위 경로")
    parser.add_argument("--apply-plan", type=Path, help="check 이후 검토한 계획 (load에서만 허용)")
    args = parser.parse_args(argv)
    if args.apply_plan is not None and args.mode != "load":
        parser.error("--apply-plan은 --mode load에서만 사용할 수 있습니다.")
    report = run(mode=args.mode, seed_path=args.seed, env_path=args.env,
                 output=args.output, apply_plan=args.apply_plan)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
