"""D-90/95: select downstream inputs from owned, unexpired result rows."""
import re
from backend.app.agents.conditions import ask
from backend.app.agents.state import Conditions, ToolResult
from backend.app.api.chat_slots import SlotRepair
from backend.app.api.results import read_snapshot


def source_versions(store,state,task):
    sources=[]
    for binding in task.input_bindings:
        result=state.results.get(binding.source_step_id)
        if not result or result.status!='OK' or not result.result_id: raise SlotRepair('선행 분석 결과가 없습니다.')
        snapshot=read_snapshot(store,state.input.user_id,result.result_id,'RESULT')
        meta=snapshot['meta']
        if (meta.get('completeness')!=result.completeness or meta.get('limitations')!=result.limitations):
            raise SlotRepair('선행 분석 결과가 변경되었습니다. 새 질문으로 다시 조회하세요.')
        sources.append((snapshot,{'result_id':result.result_id,'version':meta.get('input_fingerprint'),
                       'completeness':result.completeness,'limitations':result.limitations}))
    return sources


def resolve(store,state,task,out):
    for binding,(snapshot,source) in zip(task.input_bindings,source_versions(store,state,task),strict=True):
        stored=snapshot['conditions']
        out=Conditions.model_validate({k:v for k,v in stored.items() if k in Conditions.model_fields})
        selection=binding.selection
        if not selection: continue
        key=f'{task.task_id}:input:{binding.source_step_id}'
        rule=task.resolved.get(key) or selection.model_dump(exclude_none=True)
        kind=snapshot['meta']['kind']; group=snapshot['meta'].get('group_by',snapshot['meta'].get('group'))
        dimension=rule.get('dimension'); order=rule.get('order_by'); n=rule.get('limit')
        if kind=='PREDICTION':
            supported=[('COUNTRY','product_rank'),('HS6','country_rank')]
            if group=='COUNTRY': supported=supported[1:]
            elif group=='HS6': supported=supported[:1]
        elif kind=='RANKING': supported=[(group,'amount_usd')]
        else: raise SlotRepair('선행 결과에서 대상 순위를 선택할 수 없습니다.')
        if dimension and order and (dimension,order) not in supported:
            raise SlotRepair('선행 결과의 순위 기준과 요청 대상이 다릅니다.')
        rows=snapshot['rows']
        group_key='country_code' if order=='country_rank' else 'hs6' if order=='product_rank' else None
        groups=sorted({r[group_key] for r in rows}) if group_key else []
        named_group=rule.get('group_value')
        evidence=state.input.question+' '+task.question
        explicit_count=n and re.search(rf'(?<!\d){n}(?!\d)',evidence)
        if (not (dimension and order and n) or (not explicit_count and key not in task.resolved)
                or (len(groups)>1 and not named_group)):
            options=[]
            for dim,metric in supported:
                for count in ([n] if n else [1,3,5]):
                    if dimension and dimension!=dim: continue
                    group_field='country_code' if metric=='country_rank' else 'hs6' if metric=='product_rank' else None
                    group_values=sorted({r[group_field] for r in rows}) if group_field else [None]
                    for value in group_values:
                        patch={'dimension':dim,'order_by':metric,'limit':count}
                        if value: patch['group_value']=value
                        options.append((f'{dim}_{count}_{value or "all"}',f'{value or ""} {"국가" if dim=="COUNTRY" else "품목"} 상위 {count}개'.strip(),patch))
            ask(state,task,key,'INPUT_SELECTION','선행 결과에서 사용할 대상을 선택하세요.', options[:5]); return out
        if n>(20 if dimension=='COUNTRY' else 5): raise SlotRepair('선택한 국가 또는 품목이 조건 상한을 넘습니다.')
        if named_group and group_key: rows=[r for r in rows if r[group_key]==named_group]
        field=('export_amount_usd' if out.trade_flow=='EXPORT' else 'import_amount_usd') if order=='amount_usd' else order
        def code(row): return row.get('country_code') if dimension=='COUNTRY' else row.get('hs6') or (row.get('hs_item') or {}).get('hs_code')
        sorted_rows=sorted((r for r in rows if code(r) and r.get(field) is not None),
            key=lambda r:(-r[field] if order=='amount_usd' else r[field],code(r)))
        codes=list(dict.fromkeys(code(r) for r in sorted_rows))[:n]
        if not codes: raise SlotRepair('요청한 대상이 선행 결과표에 없습니다.')
        if dimension=='COUNTRY': out.country_codes=codes
        else:
            from backend.app.agents.state import HsProduct
            out.hs_items=[HsProduct(hs_code=c,hs_ver='HS2022') for c in codes]
        if len(codes)<n:
            state.results[task.steps[0].step_id]=ToolResult(status='OK',completeness='PARTIAL',
                limitations=[f'요청한 {n}개 중 {len(codes)}개 대상만 있습니다.'])
        label=f'{order} 기준 상위 {len(codes)}개: '+', '.join(codes)
        if label not in task.applied_defaults: task.applied_defaults.append(label)
    return out
