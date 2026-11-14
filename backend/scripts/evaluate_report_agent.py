"""Report Agent 오프라인 평가(이슈 #82). LLM·운영 DB 정확도를 재지 않고, 설계 계약을 고정 사례로 검증한다.

저장소 루트에서 실행: python -m backend.scripts.evaluate_report_agent --output docs/evaluations/report_agent_current.json
--source-ref를 주면 SOURCES의 운영 모듈만 그 커밋 내용으로 메모리에서 바꿔 끼운다(작업 트리·Git은 건드리지 않는다).
사례·테스트·fixture는 현재 것을 그대로 쓰므로 같은 기준으로 작업 전후를 비교할 수 있다. 실행마다 새 프로세스를 쓴다.
"""
import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import importlib
from importlib.metadata import version
import json
from pathlib import Path
import platform
import socket
import subprocess
import sys
import types
import unittest
from unittest.mock import patch

# 팀 평가기(SQL·Visualization)와 같은 증거 규칙: CRLF/LF와 무관한 해시, 커밋하지 않은 변경 표시
from backend.scripts.evaluate_sql_visualization import ROOT, digest_bytes, revision_metadata
# 이 작업에서 바꾸는 운영 모듈. 과거 비교 때 이 파일만 그 커밋 내용으로 교체한다.
SOURCES = ('backend/app/api/files.py', 'backend/app/services/report_ondemand_context.py',
           'backend/app/services/report_templates.py', 'backend/app/services/report_context.py',
           'backend/app/api/reports.py', 'backend/app/agents/report.py')  # 앞 모듈을 뒤 모듈이 가져다 쓰므로 이 순서로 바꾼다
FLOW = 'backend.tests.test_agent_report.ReportAgentTest'
GROUPS = [
    ('승인', FLOW, [
        ('R01', '명시 승인', 'report_waits_for_explicit_button_approval', '미리보기를 만들어 묻고, 입력창의 글은 승인으로 쓰지 않으며 리포트를 만들지 않는다(D-81·D-92).'),
        ('R02', '입력 검증', 'confirmation_must_match_the_preview_and_formats', '다른 미리보기·잘못된 형식·중복·빈 형식·문자열 확인값은 거부한다.'),
        ('R03', '형식 보존', 'selected_formats_are_kept_on_the_report', '사용자가 고른 PDF·DOCX를 리포트에 그대로 남긴다.'),
        ('R04', '지원 범위', 'import_report_is_refused_before_preview', '수입 리포트는 미리보기·리포트 없이 안내하고 수출로 바꾸지 않는다(D-81).'),
        ('R05', '취소', 'cancel_creates_no_report', '취소하면 리포트를 접수하지 않는다.'),
        ('R06', '만료', 'expired_preview_is_confirmed_again_not_created', '만료된 미리보기 승인으로 만들지 않고 새 미리보기로 다시 묻는다.'),
    ]),
    ('대기·완료', FLOW, [
        ('R07', '완료 대기', 'chat_waits_until_the_report_is_ready', 'QUEUED·RUNNING·DELAYED 동안 대기하고 READY에서 리포트 번호·완료 문구로 끝난다(D-76·D-93).'),
        ('R08', '실패 처리', 'failed_report_ends_the_wait_without_success_text', 'FAILED면 완료 문구 없이 그 질문만 NO_DATA, 다른 분석은 답한다(D-72).'),
        ('R09', '중복 방지', 'same_step_reuses_the_report_and_conflicting_input_is_refused', '같은 단계 재실행은 리포트를 재사용하고 승인 입력이 다르면 접수하지 않는다(D-97).'),
        ('R10', '대기 확인', 'waiting_checker_visits_every_waiting_chat', '한 번에 일부만 확인해도 커서로 모든 대기 채팅을 깨운다(§14.6).'),
        ('R11', '완료 제목', 'first_answer_title_and_summary_come_from_code', '첫 답변의 제목·요약을 LLM 재호출 없이 코드로 20자 이내 작성한다(D-93).'),
    ]),
    ('선행 결과', FLOW, [
        ('R12', '조건 충돌', 'result_based_report_refuses_sources_with_different_conditions', '조건이 다른 선행 결과를 합치지 않고 리포트만 NO_DATA로 끝낸다(D-90).'),
        ('R13', '조건 고정', 'result_based_report_cannot_change_conditions_by_text', '선행 결과 기반 리포트는 글로 조건을 바꾸지 않고 새 분석을 안내한다(D-92).'),
    ]),
    ('대기·완료', 'backend.tests.test_agent_graph.AgentGraphTest', [
        ('R14', '그래프 중단', 'report_wait_releases_graph_and_resumes_with_none', '리포트 대기 중 그래프가 실행권을 놓고 완료 후 이어서 재개한다(D-76).'),
    ]),
    ('대기·완료', 'backend.tests.test_agent_pr61_fixes.FinishBudgetTest', [
        ('R15', '실행 예산', 'report_ready_reserves_even_when_budget_is_spent', '실행 예산을 다 써도 완성된 리포트의 결과 확인을 거부하지 않는다(§13.4).'),
    ]),
    ('선행 결과', 'backend.tests.test_agent_state.DependentTaskContractTest', [
        ('R16', '부분 결과', 'partial_result_propagates_to_report_and_rag', '선행 결과의 누락(PARTIAL)을 리포트 결과까지 전파한다(D-96).'),
        ('R17', '완료 결론', 'report_success_has_conclusion_without_digest', '요약 정보가 없어도 리포트 성공 결론을 고정 문구로 만든다(D-93).'),
    ]),
    ('선행 결과', 'backend.tests.test_backend_api.BackendApiTest', [
        ('R18', '결과 고정', 'agent_report_uses_approved_result_after_source_expires', '원본 결과가 만료돼도 승인한 사본으로 재조회 없이 문서를 만든다(D-90).'),
    ]),
    ('리포트 파일', FLOW, [
        ('R19', '템플릿', 'result_based_report_uses_the_ondemand_template_without_requery', '결과 기반 리포트도 요청형 템플릿으로 결과표·그래프를 싣고, 없는 섹션은 알리며, 다시 조회하지 않는다(D-90).'),
        ('R20', '기존 유지', 'condition_report_keeps_the_ondemand_template', '조건으로 요청한 채팅 리포트는 기존 요청형 집계 섹션을 그대로 쓴다.'),
        ('R21', '표시', 'result_report_cells_are_readable_in_pdf_and_docx', '일회성 판정·순위 상태를 PDF·DOCX에서 읽을 수 있는 말로 싣는다.'),
        ('R22', '분량·누락', 'result_tables_are_bounded_and_keep_missing_notes', 'PDF 결과표는 200행까지 싣고 전체 행 수·누락 사유·미포함 항목을 알린다.'),
        ('R23', '표시', 'result_trend_chart_uses_monthly_amounts_and_no_summing_notice',
         '월별 차트는 기간 합계가 아닌 월별 금액을 국가·품목별 선으로 그리고, 결과 기반 PDF에 품목 합산 안내를 싣지 않는다.'),
        ('R24', '분량·누락', 'wide_result_table_is_split_into_column_groups_in_the_pdf',
         '열이 많은 결과표는 식별 열을 반복한 열 묶음으로 나눠 실제 PDF에서 마지막 열까지 보인다.'),
        ('R25', '표시', 'same_named_products_get_separate_chart_series',
         '이름이 같은 품목은 HS 코드(필요하면 판본·국가 코드)로 차트 시리즈를 나누고 범례에 표시한다.'),
    ]),
    # 이슈 #101: 직전 답변의 결과표로 만드는 리포트(설계 '이전 턴 결과 참조'). SOURCES 교체 비교 대상이 아니다(README 참고).
    ('이전 턴 결과', FLOW, [
        ('R26', '결과표 그대로', 'previous_table_report_copies_that_table_without_requery',
         "'방금 그 표로 리포트'는 직전 답변의 결과표를 그대로 미리보기·리포트에 싣고 다시 조회하지 않는다."),
        ('R27', '표 선택', 'previous_answer_with_several_tables_asks_which_one',
         '직전 답변에 결과표가 여러 개면 REPORT_TABLE로 묻고 고른 표만 싣는다.'),
        ('R28', '표 없음·만료', 'previous_table_missing_or_expired_ends_with_guidance',
         '직전 답변에 결과표가 없거나 보관 기간이 지났으면 미리보기 없이 안내로 끝난다.'),
        ('R29', '애매한 요청', 'ambiguous_report_request_can_use_the_previous_table',
         "'이걸로 리포트'는 REPORT_SOURCE로 묻고 결과표를 고르면 R26과 같은 경로로 간다."),
        ('R30', '예측 결과표', 'previous_prediction_table_keeps_ranking_basis',
         '직전 결과표가 유망시장 예측이면 순위 기준(group_by·anchor)까지 사본에 싣는다.'),
        ('R32', '조건 고정', 'previous_table_report_keeps_the_table_conditions',
         '직전 결과표 리포트는 질문의 기간 등으로 표 조건을 바꾸지 않고 알리며, 확인 화면의 글 수정은 새 분석을 안내한다(D-92).'),
        ('R31', '같은 턴 예측', 'same_turn_prediction_result_becomes_the_report_table',
         "'유망 수출국 예측 → 그 결과로 리포트'(t1.ml 연결)는 예측 결과표를 순위 기준까지 그대로 리포트에 싣는다."),
        ('R33', '기존 대화 표', 'legacy_chat_trend_table_becomes_a_report_with_its_comparison',
         '기존 채팅 API의 월별 추이 표로 리포트를 만들고, 답변에서 확정한 전년 비교 기간을 되살린다.'),
        ('R34', '부분 결과', 'previous_partial_table_keeps_partial_in_final_answer',
         '직전 결과표가 PARTIAL이면 실제 리포트 워커를 거친 완료 답변도 PARTIAL이고 누락 사유를 경고로 남긴다.'),
        ('R35', '저장 뒤 만료', 'tables_expired_after_the_answer_are_not_offered',
         '답변 저장 뒤 만료된 표는 선택지에서 빼고, 하나 남으면 바로 확인, 없으면 만료 안내다.'),
        ('R36', '기간 표시', 'same_looking_tables_show_their_period',
         '국가·품목·종류가 같은 후보는 선택지에 조회 기간을 붙여 구별한다.'),
        ('R37', '표 종류 판별', 'legacy_chat_table_kind_comes_from_its_columns',
         "기존 채팅 API 표(intent는 항상 STATISTICS)는 열(rank → share_pct → month 순)로 종류를 정한다."),
        ('R38', '종류 모름', 'legacy_chat_table_of_unknown_kind_ends_with_guidance',
         '기존 채팅 API 표의 종류를 판별할 수 없으면 작업 실패 대신 사유를 안내한다.'),
        ('R39', '수입 표 제외', 'import_tables_are_not_offered_for_a_report',
         '수입 표는 후보에서 빼고, 수출 표가 하나면 바로 확인, 수입 표만 있으면 수출 전용 안내다.'),
        ('R40', '품목 표시', 'legacy_multi_item_tables_are_labelled_by_their_own_item',
         '기존 채팅 API의 다중 품목 표는 선택지에 표마다 그 품목을 표시한다.'),
        ('R41', '긴 누락 사유', 'previous_partial_table_with_long_reasons_still_completes',
         '누락 사유가 1KB를 넘어도 완성된 리포트를 실패로 끝내지 않고 요약한다.'),
        ('R42', '조건 상한', 'legacy_table_beyond_agent_limits_ends_with_guidance',
         '에이전트 조건 상한(국가 20개·품목 5개)을 넘는 기존 표는 작업 실패 대신 안내로 끝난다.'),
        ('R43', '표 잘림', 'legacy_truncated_table_is_a_partial_report',
         "기존 순위표의 잘림(meta.truncated)을 PARTIAL과 '전체 N개 중 상위 M개만' 사유로 바꿔 완료 답변까지 알린다."),
        ('R44', '상위 N개', 'previous_table_report_notes_an_ignored_ranking_limit',
         "'상위 1개만'(ranking_limit) 요청은 표를 바꾸지 않고 승인 화면에 미적용 안내를 보인다."),
        ('R45', '다음 목록', 'more_than_five_previous_tables_are_paged',
         "결과표가 5개를 넘으면 표 4개 + '다음 목록'으로 나눠 6번째 표도 고를 수 있다."),
        ('R46', '쪽 사라짐', 'next_page_that_disappeared_restarts_the_list',
         "'다음 목록' 뒤 후보가 줄어 다음 쪽이 사라지면 반복 없이 남은 후보를 첫 쪽부터 다시 보인다."),
        ('R47', '고른 표 만료', 'picked_table_that_expired_is_chosen_again',
         '고른 표가 재개 전에 만료되면 다른 표로 자동으로 바꾸지 않고 남은 표에서 다시 고르게 한다.'),
        ('R48', '미리보기 전 만료', 'table_expiring_before_the_preview_asks_again',
         '조건 확인과 미리보기 사이에 고른 표가 만료되면 JOB_FAILED 없이 미리보기를 만들지 않고 다시 고르게 한다.'),
        ('R49', '명시적 재선택', 'reselection_waits_for_an_explicit_choice',
         '다시 고르는 화면에서 선택지와 맞지 않는 글을 입력해도 남은 표를 자동으로 고르지 않고 다시 묻는다.'),
        ('R50', '후보 확인 중 만료', 'table_expiring_while_checking_candidates_asks_again',
         "후보 확인과 최종 읽기 사이에 만료돼도 '보관 기간이 지났습니다'로 끝내지 않고 다시 고르게 한다."),
    ]),
]
CASES = [dict(id=identity, group=group, category=category, criterion=criterion, test=f'{test_class}.test_{method}')
         for group, test_class, entries in GROUPS for identity, category, method, criterion in entries]


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT).decode('utf-8').strip()


LOOPBACK = {'127.0.0.1', '::1', 'localhost'}


def _host(address):
    return address[0] if isinstance(address, tuple) and address else address


def offline(original, position):
    """외부 주소 연결만 막는다. Windows asyncio는 이벤트 루프를 만들 때 127.0.0.1 소켓 쌍을 쓰므로 루프백은 허용한다."""
    def guarded(*args, **kwargs):
        target = args[position] if len(args) > position else None
        if _host(target) not in LOOPBACK:
            raise RuntimeError('오프라인 평가 중에는 외부 네트워크를 쓰지 않습니다')
        return original(*args, **kwargs)
    return guarded


def install_sources(revision):
    """새 프로세스에서만 SOURCES를 그 커밋 내용으로 바꾼다. 작업 트리·Git 색인은 바꾸지 않는다."""
    hashes = {}
    for path in SOURCES:
        source = subprocess.check_output(['git', 'show', f'{revision}:{path}'], cwd=ROOT)
        hashes[path] = digest_bytes(source)
        name = path[:-3].replace('/', '.')
        parent_name, attribute = name.rsplit('.', 1)
        parent = importlib.import_module(parent_name)
        module = types.ModuleType(name)
        # __file__은 실제 경로로 둔다(템플릿·layout.json을 __file__ 기준으로 찾는 모듈이 있다). 오류 위치에는 커밋을 남긴다.
        module.__package__, module.__file__ = parent_name, str(ROOT / path)
        sys.modules[name] = module
        setattr(parent, attribute, module)
        exec(compile(source, f'{revision}:{path}', 'exec'), module.__dict__)
    return hashes


def run_case(case):
    """테스트 메서드 하나가 사례 하나다. subTest 하나라도 실패하면 그 사례는 FAIL이다."""
    result = unittest.TestResult()
    unittest.defaultTestLoader.loadTestsFromName(case['test']).run(result)
    if result.errors or result.testsRun != 1:
        status = 'ERROR'
    elif result.failures or result.unexpectedSuccesses:
        status = 'FAIL'
    elif result.skipped:
        status = 'SKIP'
    elif result.expectedFailures:
        status = 'XFAIL'
    else:
        status = 'PASS'
    details = [message for _, message in result.errors + result.failures + result.expectedFailures]
    details += [reason for _, reason in result.skipped]
    if result.testsRun != 1:
        details.append(f'테스트 메서드 1개를 기대했지만 {result.testsRun}개가 실행됐습니다.')
    return {**case, 'status': status, 'details': details}


def summarize(outcomes):
    summary = {}
    for group in [*dict.fromkeys(case['group'] for case in outcomes), '합계']:
        cases = outcomes if group == '합계' else [case for case in outcomes if case['group'] == group]
        counts = {s.lower(): sum(case['status'] == s for case in cases) for s in ('PASS', 'FAIL', 'ERROR', 'SKIP', 'XFAIL')}
        summary[group] = {'total': len(cases), **counts, 'pass_rate_pct': round(100 * counts['pass'] / len(cases), 2)}
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-ref', help='SOURCES 운영 모듈을 이 로컬 Git 커밋 내용으로 평가합니다.')
    parser.add_argument('--output', type=Path, required=True, help='평가 결과 JSON 경로')
    args = parser.parse_args(argv)
    if args.source_ref and args.source_ref.startswith('-'):
        parser.error('--source-ref는 Git 커밋이어야 합니다')
    revision = git('rev-parse', '--verify', '--end-of-options', (args.source_ref or 'HEAD') + '^{commit}')
    tests = sorted({case['test'].rsplit('.', 2)[0].replace('.', '/') + '.py' for case in CASES})
    fixtures = ['backend/scripts/evaluate_report_agent.py', 'backend/tests/api_support.py', 'backend/tests/intent_fixtures.py']
    harness = revision_metadata(tests + fixtures)
    source_state = revision_metadata(list(SOURCES))
    with ExitStack() as stack:
        # (대상, 이름, 주소 인자 위치): 함수는 첫 인자, 소켓 메서드는 self 다음, sendto는 (self, data, address).
        for owner, name, position in ((socket, 'create_connection', 0), (socket, 'getaddrinfo', 0), (socket.socket, 'connect', 1),
                                      (socket.socket, 'connect_ex', 1), (socket.socket, 'sendto', 2)):
            stack.enter_context(patch.object(owner, name, offline(getattr(owner, name), position)))
        hashes = install_sources(revision) if args.source_ref else {p: digest_bytes((ROOT / p).read_bytes()) for p in SOURCES}
        outcomes = [run_case(case) for case in CASES]
    report = {
        'schema_version': 2,
        'hash_normalization': 'UTF-8 file bytes with CRLF normalized to LF before SHA256',
        'evaluation': 'Report Agent 오프라인 설계 계약 평가',
        'evaluated_at_utc': datetime.now(timezone.utc).isoformat(),
        'source_mode': 'historical_modules' if args.source_ref else 'working_tree',
        'source_revision': revision,
        'source_dirty': False if args.source_ref else source_state['dirty'],
        'source_changes': [] if args.source_ref else source_state['changes'],
        'harness_revision': harness['revision'], 'harness_dirty': harness['dirty'], 'harness_changes': harness['changes'],
        'source_sha256': hashes,
        'evidence_sha256': {p: digest_bytes((ROOT / p).read_bytes()) for p in tests + fixtures},
        'manifest_sha256': digest_bytes(json.dumps(CASES, ensure_ascii=False, sort_keys=True).encode()),
        'environment': {'python': platform.python_version(), 'platform': platform.platform(),
                        'packages': {n: version(n) for n in ('langgraph', 'SQLAlchemy', 'pydantic')}},
        'network': '차단. SQLite·fakeredis·Mock만 사용',
        'scoring': '테스트 메서드 1개 = 사례 1개, 모든 subTest 통과 시 PASS. SKIP·ERROR도 분모에 포함.',
        'limitations': ['선정한 설계 계약 사례의 통과율이며 실제 사용자 질문 분포나 서비스 전체 정확도가 아니다.',
                        'LLM 플래너·답변 초안은 Mock으로 고정했다. 실제 모델·RDS·지연·비용은 평가하지 않았다.',
                        '리포트 파일 생성은 완료 신호(report.status)로 대신했다. 파일 내용은 별도 사례로 본다.',
                        '과거 비교는 SOURCES 모듈만 교체하며 현재 테스트·fixture를 그대로 쓴다.'],
        'summary': summarize(outcomes), 'cases': outcomes,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    for group, values in report['summary'].items():
        print(f"{group}: {values['pass']}/{values['total']} ({values['pass_rate_pct']}%)")
    print(f'결과: {args.output}')
    return 0 if all(case['status'] == 'PASS' for case in outcomes) else 1


if __name__ == '__main__':
    raise SystemExit(main())
