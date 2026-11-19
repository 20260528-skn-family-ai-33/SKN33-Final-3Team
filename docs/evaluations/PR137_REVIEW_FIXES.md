# PR #137 리뷰 수정 검증 (2026-11-19)

## 변경 범위

- 키워드 지정 확인창 복구 및 인라인 오류 표시 유지.
- 미사용 관리자 대분류만 확인 후 이름 수정·삭제. 기존 감사 이력을 보존하며 버전 검사·MySQL 이름 잠금으로 동시 저장 직렬화. API-104·105 제한 유지.
- 회원·구독의 날짜 미상 행만 과거 집계에서 제외하고 제외 건수 표시.
- 기존 근거 배지·색상 유지. 긴 참고 표기 위치에만 작은 참고 태그 표시(기사 문장 및 완료된 답변). 스트리밍은 문자열 정리만 수행.
- LIST.md 제거·Git 제외, CHANGELOG의 pending 표기를 #137로 정리.

## 결과

- Python 3.11, main 기준 cf623f143290863b60e64aa5c1cc0a3c143a304b.
- `.env` 로딩과 외부 TCP/DNS 연결을 차단한 동일 조건에서 unittest discover 실행.
- main: 2,704개, 실패 66개·오류 34개·skip 58개.
- 수정본: 2,729개, 실패 66개·오류 34개·skip 62개. 실패·오류 테스트 ID 집합이 main과 동일하다. 전체 통과로 표시하지 않는다.
- 마지막 이름 중복 검사 보존 후 관련 단위 테스트 12개 및 MySQL 테스트 4개 재확인.
- 실제 compose MySQL 8.4, 독립 임시 DB에서 연결 두 개로 동시성 4개 통과: 동일 버전 수정/삭제, 삭제/중분류 추가, 예외 후 롤백·잠금 해제, READ COMMITTED. 임시 DB는 테스트 종료 시 제거.
- 프런트 npm test 63개 통과, vite build 통과. 기존 500 kB 청크 경고 유지. lint 오류 없음, 기존 경고 3개 유지.
- 별도 SQLite/fakeredis 서버와 Chrome에서 지정 확인/취소, 독립 대분류 추가·수정·삭제 확인창 및 새로고침 후 유지 검증 통과 (`frontend/tests/admin-major-browser.cjs`).
- 실제 JSX 렌더링에서 혼합 기사 근거 배지·참고 문장 태그 및 완료된 본문 표시 통과 (`frontend/tests/article-presentation-render.mjs`).
- main 대비 db/·backend/migrations/·runtime_schema.py·models 변경 없음. 운영 DB DDL·실데이터 변경 없음.

## 2차 리뷰 반영 (heoyuna0819-creator 코멘트, 3차 Claude 리뷰)

- [P2-1] 키워드 화면 '+ 대분류/중분류 추가'의 저장 버튼이 확인창을 열고, 추가 사유를 직접 입력해야 저장한다. 사유 자동 입력(`대분류 추가: OO`)을 제거했다. 취소 시 요청 없음.
- [P2-2] API-79 계산 방식은 바꾸지 않았다. DEC-37 확정 전 임시 방식임을 `BACKEND_API.md`·`BACKEND_API_STATUS.md`·`ADMIN_METRICS.md`에 맞춰 적고, 탈퇴·이용 제한 계정 포함과 탈퇴·삭제·해지 시 과거 수치 변동을 알려진 한계(후속 과제)로 명시했다. 방식 자체는 팀 합의 필요.
- [P3-3] 대분류 복원에 쓰는 `api_audit_event` CATEGORY/major 기록의 삭제·아카이브 금지를 `ADMIN_METRICS.md`·`ADMIN_KEYWORD_API.md`에 운영 주의로 남겼다.
- [3차 리뷰 P3] 대분류 중복 오류 문구를 구분했다. 같은 철자·기본 분류는 "같은 이름의 대분류 또는 기본 분류가 이미 있습니다.", 철자만 다른 대분류 생성·이름 수정은 등록 철자와 같은 대분류로 보는 이유, 철자만 다른 중분류 추가·이동만 "같은 철자로 입력하세요."
- 검증: 대분류 관리 단위 테스트(문구 케이스 추가), MySQL 8.0.46 동시성·collation 테스트, Chrome 브라우저 테스트(인라인 추가 확인·취소 단계 추가), 프런트 npm test·build.

## main과 동일하게 남은 실패·오류

외부 연결 차단, 로컬 실행 환경 및 기존 테스트 기대값과 관련된 결과다. 이번 수정으로 해결한 것으로 보지 않는다.

### 실패

- `test_agent_report.ReportAgentTest.test_condition_report_keeps_the_ondemand_template`
- `test_agent_report.ReportAgentTest.test_result_based_report_uses_the_ondemand_template_without_requery`
- `test_agent_report.ReportAgentTest.test_result_report_cells_are_readable_in_pdf_and_docx`
- `test_backend_api.BackendApiTest.test_queued_report_without_file_or_execution_json_is_claimed`
- `test_backend_api.BackendApiTest.test_report_expired_claim_recovers_after_restart`
- `test_backend_api.BackendApiTest.test_report_idempotency_worker_files_and_frozen_content`
- `test_backend_api.BackendApiTest.test_report_retry_round_and_restart_without_job_table`
- `test_chat_review_regressions.ChatReviewRegressionTest.test_confidence_uses_valid_distinct_codes`
- `test_db_tls.DatabaseTlsTest.test_environment_override_and_missing_ca_fail_before_connecting`
- `test_fx_daily_load.FxDailyLoadTest.test_failed_currency_still_records_actual_requests`
- `test_fx_daily_load.FxDailyLoadTest.test_failure_after_first_currency_keeps_counts_of_both`
- `test_fx_daily_load.FxDailyLoadTest.test_loads_only_currencies_with_ecos_code_and_records_batch`
- `test_fx_daily_load.FxDailyLoadTest.test_no_data_period_is_success`
- `test_fx_daily_load.FxDailyLoadTest.test_recent_run_fetches_each_currency_from_its_own_start`
- `test_fx_daily_load.FxDailyLoadTest.test_rerun_updates_without_duplicates`
- `test_fx_daily_load.FxDailyLoadTest.test_second_run_while_first_is_running_is_skipped`
- `test_fx_daily_load.FxDailyLoadTest.test_warns_when_currency_has_fewer_than_two_dates`
- `test_news_embed_rebuild.KeywordRecoveryTest.test_pointer_error_after_replace_restores_legacy_and_generation_paths (existing_pointer=False)`
- `test_news_embed_rebuild.KeywordRecoveryTest.test_pointer_error_after_replace_restores_legacy_and_generation_paths (existing_pointer=True)`
- `test_periodic_report_pdf.PeriodicContextTest.test_browser_failure_keeps_worker_retry_policy_without_legacy_fallback`
- `test_pr_search_changes.SearchChangesTest.test_clear_hs6_embedding_is_offered_and_public_search_stays_hs4`
- `test_report_input_paths.ReportInputPathsTest.test_report_tables_store_only_analysis_conditions_and_can_be_reused`
- `test_report_news_regressions.ReportNewsRegressionTest.test_changes_mom_report_finishes_with_mom_kpi`
- `test_report_news_regressions.ReportNewsRegressionTest.test_zero_trade_report_finishes_and_exports_files`
- `test_s3_performance_review.PerformanceReviewTests.test_other_process_reader_is_pinned_without_global_lock`
- `test_shortnews_daily.ShortnewsDailyTest.test_all_pages_of_yesterday_are_loaded_and_rerun_is_idempotent`
- `test_shortnews_daily.ShortnewsDailyTest.test_chroma_failure_keeps_day_retryable_after_mysql_commit`
- `test_shortnews_daily.ShortnewsDailyTest.test_concurrent_run_is_skipped_and_lock_is_released`
- `test_shortnews_daily.ShortnewsDailyTest.test_cycling_pages_stop_with_partial_state_and_saved_articles`
- `test_shortnews_daily.ShortnewsDailyTest.test_database_failure_is_pending_then_recovered`
- `test_shortnews_daily.ShortnewsDailyTest.test_failed_and_downtime_days_are_all_recovered_without_today`
- `test_shortnews_daily.ShortnewsDailyTest.test_incomplete_collection_loads_saved_articles_but_reports_partial`
- `test_shortnews_daily.ShortnewsDailyTest.test_interrupted_state_and_commit_before_checkpoint_are_replayed`
- `test_shortnews_daily.ShortnewsDailyTest.test_legacy_run_history_recovers_missing_dates`
- `test_shortnews_daily.ShortnewsDailyTest.test_load_failure_is_not_success`
- `test_shortnews_daily.ShortnewsDailyTest.test_mysql_success_vector_partial_remains_retryable`
- `test_shortnews_daily.ShortnewsDailyTest.test_new_page_combination_without_new_ids_does_not_truncate_later_page`
- `test_shortnews_daily.ShortnewsDailyTest.test_old_failure_does_not_block_current_target`
- `test_shortnews_daily.ShortnewsDailyTest.test_overlapping_pages_with_new_ids_continue`
- `test_shortnews_daily.ShortnewsDailyTest.test_partial_day_is_retried_and_does_not_block_next_day`
- `test_shortnews_daily.ShortnewsDailyTest.test_successful_old_dates_are_skipped_but_yesterday_is_refreshed`
- `test_shortnews_daily.ShortnewsDailyTest.test_target_day_uses_kst_across_year_and_leap_boundaries (instant='2026-12-31T18:00:00+00:00')`
- `test_shortnews_daily.ShortnewsDailyTest.test_target_day_uses_kst_across_year_and_leap_boundaries (instant='2024-02-29T18:00:00+00:00')`
- `test_shortnews_daily.ShortnewsDailyTest.test_team_fields_survive_collection_and_same_source_upsert`
- `test_shortnews_daily.ShortnewsDailyTest.test_zero_articles_records_requested_day_in_batch`
- `test_shortnews_recovery.RecoveryBoundaryTest.test_atomic_replace_failure_keeps_previous_valid_state`
- `test_shortnews_recovery.RecoveryBoundaryTest.test_checkpoint_failure_after_commit_stays_pending_when_later_day_saves`
- `test_shortnews_recovery.RecoveryBoundaryTest.test_checkpoint_failure_before_one_day_skips_only_that_day`
- `test_shortnews_recovery.RecoveryBoundaryTest.test_database_switch_replays_history_and_switch_back_keeps_separate_progress`
- `test_shortnews_recovery.RecoveryBoundaryTest.test_env_database_is_frozen_for_whole_recovery_run`
- `test_shortnews_recovery.RecoveryBoundaryTest.test_final_report_failure_after_db_commit_retries_without_duplicates`
- `test_shortnews_recovery.RecoveryBoundaryTest.test_interrupt_leaves_retryable_state_and_releases_locks`
- `test_shortnews_recovery.RecoveryBoundaryTest.test_latest_summary_failure_preserves_verified_day_checkpoint`
- `test_shortnews_recovery.RecoveryBoundaryTest.test_legacy_unreadable_report_does_not_block_date_recovery`
- `test_shortnews_recovery.RecoveryBoundaryTest.test_new_database_replays_manual_dates_before_operating_start`
- `test_shortnews_recovery.RecoveryBoundaryTest.test_old_date_directory_creation_failure_does_not_block_current_date`
- `test_shortnews_recovery.RecoveryBoundaryTest.test_old_date_initial_report_failure_does_not_block_current_date`
- `test_shortnews_recovery.RecoveryBoundaryTest.test_same_database_different_output_is_locked`
- `test_shortnews_recovery.RecoveryBoundaryTest.test_simultaneous_jobs_only_one_collects_and_commits`
- `test_shortnews_recovery.RecoveryBoundaryTest.test_unexpected_day_exception_is_isolated`
- `test_shortnews_recovery.RecoveryBoundaryTest.test_v1_checkpoint_is_revalidated_without_duplicate_rows`
- `test_shortnews_review.SchedulerSwitchTest.test_env_file_and_environment_precedence_control_both_jobs`
- `test_shortnews_transition.SourceTransitionTest.test_missing_body_in_one_row_fails_whole_day_and_recovers`
- `test_shortnews_transition.SourceTransitionTest.test_v2_successes_are_recollected_after_contract_change_without_duplicates`
- `test_un_scheduler_integration.SchedulerIntegrationTest.test_execute_once_reads_env_and_closes_resources`
- `test_un_scheduler_integration.SchedulerIntegrationTest.test_monthly_collection_moves_to_eighteenth_and_other_schedules_stay`

### 오류

- `test_agent_report_live_evaluation.LiveHarnessTest.test_changed_questions_and_holdout_rerun_are_refused`
- `test_agent_report_live_evaluation.LiveHarnessTest.test_holdout_refuses_changed_prompt_or_schema_before_key_and_lock`
- `test_backend_api.BackendApiTest.test_report_recovers_when_db_commit_fails_after_file_log`
- `test_chat_review_regressions.ChatReviewRegressionTest.test_invalid_vector_winner_does_not_boost_other_candidate`
- `test_db_tls.DatabaseTlsTest.test_existing_non_tls_configuration_is_unchanged`
- `test_db_tls.DatabaseTlsTest.test_relative_ca_is_resolved_and_driver_validates_identity`
- `test_fx_daily_load.FxDailyLoadTest.test_failure_keeps_existing_rates_and_records_failed`
- `test_ml_reporting.TestPdfReport.test_pdf_contains_schema_methods_actual_metrics_and_failure_status`
- `test_ml_reporting.TestPdfReport.test_pdf_describes_actual_feature_usage_and_market_type_results (metadata={'features_used': ['fx_change_12m', 'log_gdp', 'mkt_growth', 'kor_share'], 'model_b': 'classifier'}, predictions={'rows': 10, 'types_known': 10})`
- `test_ml_reporting.TestPdfReport.test_pdf_describes_actual_feature_usage_and_market_type_results (metadata={'features_used': [], 'model_b': 'rule (기준선 미달)'}, predictions={'rows': 10, 'types_known': 3})`
- `test_ml_reporting.TestPdfReport.test_pdf_describes_actual_feature_usage_and_market_type_results (metadata={'features_used': []}, predictions={'rows': 10, 'types_known': 0})`
- `test_ml_reporting.TestPdfReport.test_pdf_describes_actual_feature_usage_and_market_type_results (metadata={}, predictions={})`
- `test_ml_reporting.TestPdfReport.test_pdf_does_not_infer_missing_features_from_invalid_or_unrelated_dates (available='availability.csv의 연도별 최초 공개일 (근거는 evidence 열)', used=[])`
- `test_ml_reporting.TestPdfReport.test_pdf_does_not_infer_missing_features_from_invalid_or_unrelated_dates (available='2026-99-01', used=[])`
- `test_ml_reporting.TestPdfReport.test_pdf_does_not_infer_missing_features_from_invalid_or_unrelated_dates (available=None, used=[])`
- `test_ml_reporting.TestPdfReport.test_pdf_does_not_infer_missing_features_from_invalid_or_unrelated_dates (available='2026-08-31', used=[])`
- `test_ml_reporting.TestPdfReport.test_pdf_does_not_infer_missing_features_from_invalid_or_unrelated_dates (available='2026-09-30', used=[])`
- `test_ml_reporting.TestPdfReport.test_pdf_does_not_infer_missing_features_from_invalid_or_unrelated_dates (available='2026-10-22', used=['log_gdp', 'mkt_growth'])`
- `test_ml_reporting.TestPdfReport.test_pdf_explains_unevaluated_b_and_late_external_data`
- `test_periodic_report_pdf.PeriodicContextTest.test_legacy_snapshot_pdf_and_docx_do_not_use_html_renderer`
- `test_periodic_report_pdf.PeriodicContextTest.test_report_sources_count_legacy_and_current_short_news_once`
- `test_periodic_report_pdf.PeriodicContextTest.test_report_sources_include_articles_used_for_spikes_beyond_the_20_cards`
- `test_pr_search_changes.SearchChangesTest.test_embedding_failure_is_not_cached_but_recovery_is_cached`
- `test_s3_review.S3ReviewTests.test_market_requests_reuse_manifest_and_model_selection_until_refresh`
- `test_shortnews_daily.ShortnewsDailyTest.test_manual_retry_uses_explicit_date`
- `test_shortnews_daily.ShortnewsDailyTest.test_wrong_date_blocks_entire_day_preserves_raw_and_retries`
- `test_weekly_report_sources.WeeklyReportSourcesTest.test_baseline_only_short_news_is_a_source_of_the_spike_ratio`
- `test_weekly_report_sources.WeeklyReportSourcesTest.test_baseline_sources_follow_the_count_filters`
- `test_weekly_report_sources.WeeklyReportSourcesTest.test_issue_cut_by_the_display_limit_leaves_no_source`
- `test_weekly_report_sources.WeeklyReportSourcesTest.test_issue_inside_the_display_limit_keeps_its_source`
- `test_weekly_report_sources.WeeklyReportSourcesTest.test_keyword_spike_baseline_alone_adds_its_source`
- `test_weekly_report_sources.WeeklyReportSourcesTest.test_keyword_spike_cut_by_the_display_limit_leaves_no_source`
- `test_weekly_report_sources.WeeklyReportSourcesTest.test_major_issue_spike_baseline_is_a_source`
- `test_weekly_report_sources.WeeklyReportSourcesTest.test_same_media_used_by_a_shown_item_stays_once`

