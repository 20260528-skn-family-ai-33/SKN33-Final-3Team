"""Validate user input against the pending server question before resuming."""
from typing import Annotated
from pydantic import Field
from backend.app.agents.state import Model, HsProduct, MAX_HS_ITEMS, MAX_COUNTRIES
from backend.app.api.chat_slots import country_key, numeric_product, SlotRepair
from backend.app.api.common import mask_text, require


class ClarificationReading(Model):
    choice_id: str | None
    new_expression: str | None
    interest_change: bool | None
    unrelated: bool | None


class InterestSelection(Model):
    hs_items: Annotated[list[HsProduct], Field(max_length=MAX_HS_ITEMS)]
    country_codes: Annotated[list[str], Field(max_length=MAX_COUNTRIES)]


def answer_patch(store, state, answer, interpret=None):
    pending = state.pending
    require(pending and answer.get('clarification_id') == pending.clarification_id,
            '현재 되묻기와 일치하지 않습니다.', 'CLARIFICATION_CONFLICT', 409)
    fields = set(answer) - {'clarification_id'}
    require(len(fields)==1 and fields <= {'choice_ids', 'hs_item', 'interest_selection', 'report_confirmation', 'text'},
            '되묻기 답은 한 가지 방식으로 제출하세요.')
    choices = {c.choice_id:c.patch for c in pending.choices}
    if 'report_confirmation' in fields:
        item = answer['report_confirmation']
        task = next(t for t in state.plan.tasks if t.task_id==pending.task_id)
        request = task.report_request
        require(pending.kind=='REPORT_CONFIRM' and request and isinstance(item, dict)
                and set(item)=={'preview_id','confirm','formats'} and item['preview_id']==request.preview_id
                and type(item['confirm']) is bool, '리포트 미리보기와 확인 요청이 일치하지 않습니다.')
        formats = item['formats']
        require(isinstance(formats,list) and 1<=len(formats)<=2 and all(x in ('PDF','DOCX') for x in formats)
                and len(set(formats))==len(formats), 'PDF 또는 DOCX 형식을 선택하세요.')
        return {'report_confirmation':item}
    if pending.kind=='REPORT_CONFIRM':
        require(fields=={'text'}, '리포트 확인 또는 취소 버튼을 이용하세요.')
        return {}  # Text alone never authorizes report creation.
    if 'interest_selection' in fields:
        require(pending.kind=='INTEREST_SELECT','관심 범위 선택 요청이 아닙니다.')
        try: selection = InterestSelection.model_validate(answer['interest_selection'])
        except ValueError: require(False,'관심 품목은 5개, 국가는 20개 이하로 선택하세요.')
        keys = [(p.hs_code,p.hs_ver) for p in selection.hs_items]
        require(bool(keys or selection.country_codes) and len(set(keys))==len(keys)
                and len(set(selection.country_codes))==len(selection.country_codes)
                and set(keys)<={(p.hs_code,p.hs_ver) for p in state.input.interests.hs_items}
                and set(selection.country_codes)<=set(state.input.interests.country_codes), '등록한 관심 범위에서 선택하세요.')
        return {'interest':'APPLIED', **selection.model_dump()}
    if 'choice_ids' in fields:
        ids=answer['choice_ids']
        require(isinstance(ids,list) and len(ids)==1 and isinstance(ids[0],str) and ids[0] in choices,
                '제시된 선택지 하나를 고르세요.')
        return choices[ids[0]]
    if 'hs_item' in fields:
        require(pending.kind=='HS_ITEM','품목 선택 요청이 아닙니다.')
        try:
            item = HsProduct.model_validate(answer['hs_item'])
            numeric_product(store,item.hs_code,item.hs_code,{item.hs_code})
            from backend.app.api.accounts import validate_scope
            validate_scope(store,[item.model_dump()],[])
        except (ValueError,SlotRepair): require(False,'등록된 HS 품목과 판본을 선택하세요.')
        return {'hs_items':[item.model_dump()]}
    raw = answer.get('text')
    require(isinstance(raw,str) and 1<=len(raw.strip())<=4000,'직접 입력은 1~4,000자로 작성하세요.')
    text = mask_text(raw.strip())
    key = country_key(text)
    for choice in pending.choices:
        if key in (country_key(choice.choice_id),country_key(choice.label)): return choice.patch
    if pending.kind=='COUNTRY':
        from sqlalchemy import select
        from backend.app.agents.conditions import country_matches
        country=store.t('country')
        rows=store.rows(select(country.c.country_code,country.c.name_ko,country.c.country_role)
                        .where(country.c.country_role=='COUNTRY'))
        code,_=country_matches(text,rows,1)
        if code: return {'country_codes':[code]}
    if pending.kind=='HS_ITEM' and key.isdigit() and len(key) in (4,6):
        try: numeric_product(store,key,key,{key})
        except SlotRepair: return {}
        return {'hs_items':[{'hs_code':key,'hs_ver':'HS2022'}]}
    if pending.kind in ('MODEL_PERIOD','INTEREST_CONTEXT','INTEREST_CHANGE'):
        if key in ('예','네','응','좋아','동의','확인','적용','생성'): return pending.choices[0].patch
        if key in ('아니','아니오','아니요','싫어','거절','취소','전체'): return pending.choices[-1].patch
    if interpret is None: return {}
    reading = interpret({'kind':pending.kind,'message':pending.message,'text':text,
                         'choices':[{'choice_id':c.choice_id,'label':c.label} for c in pending.choices]})
    if reading.unrelated: return {}
    if reading.choice_id in choices: return choices[reading.choice_id]
    if (reading.new_expression and pending.kind in ('COUNTRY','HS_ITEM') and len(reading.new_expression)<=120
            and country_key(reading.new_expression) in key):
        return {'replace_expression':reading.new_expression}
    if reading.interest_change: return {'request_interest_change':True}
    return {}


def apply_answer(state, patch):
    pending = state.pending
    task = next(t for t in state.plan.tasks if t.task_id == pending.task_id)
    if patch: task.resolved[pending.condition_key] = patch
    if pending.kind=='MODEL_PERIOD' and patch:
        task.slots.latest_prediction_confirmed = bool(patch.get('latest_prediction_confirmed'))
    if pending.kind=='REPORT_CONFIRM' and 'report_confirmation' in patch:
        confirmation = patch['report_confirmation']
        task.report_request.confirmed = confirmation['confirm']
        task.report_request.formats = confirmation['formats']
    state.pending, state.status = None, 'RUNNING'
    task.steps[0].attempts = 0
    task.steps[0].status = 'PENDING'
