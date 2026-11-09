"""Public view of the actual planner steps, in execution order."""
LABELS = {'check_conditions':'조건 확인', 'query_trade_data':'무역 데이터 조회',
          'search_articles':'기사 검색','build_chart':'차트 작성',
          'query_market_model':'유망시장 예측','generate_report':'리포트 생성','lookup_hs':'HS 코드 조회'}


def progress_for(state, node):
    ready=state.ready_steps() if node in LABELS or node=='dispatch' else []
    current=ready[0].step_id if ready and node in ('dispatch',ready[0].tool) else None
    if state.report_wait:
        current=state.report_wait.step_id
    if state.pending:
        task=next(t for t in state.plan.tasks if t.task_id==state.pending.task_id)
        current=task.steps[0].step_id
    plan=[]
    for task in state.plan.tasks:
        for step in task.steps:
            status=step.status
            if step.step_id==current:
                status='WAITING' if state.pending or state.report_wait else 'RUNNING'
            plan.append({'id':step.step_id,'question':task.question,'label':LABELS[step.tool],'status':status})
    final_status='DONE' if node=='complete' else 'RUNNING' if node in ('compose','verify','finalize') else 'PENDING'
    plan.append({'id':'answer','label':'답변 작성 및 검증','status':final_status})
    current_item=next((s for s in plan if s['status'] in ('RUNNING','WAITING')),None)
    label=('답변 완료' if node=='complete' else '사용자 답변 대기' if state.pending else
           '리포트 만드는 중' if state.report_wait else '숫자 확인 중' if node=='verify' else
           current_item['label']+' 중' if current_item else '실행 계획 준비')
    return {'plan':plan,'label':label,'total':len(plan),
            'done':sum(s['status'] in ('DONE','FAILED','SKIPPED') for s in plan)}
