"""One bounded Responses call; retries and durable budget belong to the caller."""
import json
import re
from typing import get_args
from pydantic import BaseModel

from backend.app.agents.state import Draft, WriterEdits


def validation_details(error, schema):
    """Bounded diagnostics: schema paths and constraints, never model input/text."""
    from backend.app.agents import state
    fields=set(); visited=set()
    def collect(annotation):
        if isinstance(annotation,type) and issubclass(annotation,BaseModel):
            if annotation in visited: return
            visited.add(annotation)
            for name,field in annotation.model_fields.items():
                fields.add(name); collect(field.annotation)
        else:
            for child in get_args(annotation): collect(child)
    # validate() may fail on another state model (e.g. build_plan), so its developer-defined field names are safe too.
    for model in [schema,*vars(state).values()]: collect(model)
    rows=error.errors(include_input=False,include_url=False)
    details=[]
    reasons={'too_long':'항목 개수 상한 초과','too_short':'항목 개수 하한 미달',
        'missing':'필수 필드 누락','extra_forbidden':'허용하지 않는 필드',
        'literal_error':'허용하지 않는 선택 값','json_invalid':'올바르지 않은 JSON',
        'string_type':'문자열이 필요합니다','list_type':'목록이 필요합니다',
        'greater_than_equal':'최솟값 미달','less_than_equal':'최댓값 초과'}
    for row in rows[:10]:
        cause=(row.get('ctx') or {}).get('error')
        # Only SafeError carries fixed text; other validators may put response text in their exception.
        reason=str(cause) if isinstance(cause,state.SafeError) else reasons.get(row['type'],'스키마 규칙 위반')
        details.append({'field':'.'.join(str(part) if isinstance(part,int) or part in fields else '<unknown>'
            for part in row.get('loc',())[:12]) or '<root>',
            'type':row['type'] if re.fullmatch(r'[a-z_]{1,64}',row['type']) else 'validation_error', 'reason':reason})
    return {'model':error.title if error.title in {m.__name__ for m in visited} else '<unknown>',
            'errors':details,'total':len(rows),'truncated':len(rows)>10}


class StructuredClient:
    def __init__(self, settings, client_factory=None):
        self.settings = settings
        self.client_factory = client_factory

    def parse(self, *, model, timeout, prompt, payload, schema, max_output_tokens):
        from openai import OpenAI
        factory = self.client_factory or OpenAI
        key = next((v for k, v in self.settings.values.items() if k.lower() == 'openai_api_key' and v), None)
        if not key:
            raise ValueError('LLM API key missing')
        # GPT-5·6 계열(gpt-5.6-luna·gpt-6-luna 등 추론 모델)은 temperature를 받지 않아 빼고 추론 강도만 넘긴다.
        # Writer(Draft·WriterEdits)는 출력 예산에 추론 토큰도 포함되므로 low로 고정한다(main #87).
        # TODO(C-35): 이슈 86 평가로 플래너·compose 모델과 추론 강도(API_LLM_REASONING_EFFORT)를 확정한다.
        reasoning = model.startswith(('gpt-5', 'gpt-6'))
        options = {} if reasoning else {'temperature': 0}
        if reasoning:
            effort = 'low' if schema in (Draft, WriterEdits) else self.settings.get('API_LLM_REASONING_EFFORT')
            if effort:
                options['reasoning'] = {'effort': effort}
        with factory(api_key=key, timeout=timeout, max_retries=0) as client:
            response = client.responses.parse(model=model, instructions=prompt,
                input=json.dumps(payload, ensure_ascii=False, allow_nan=False), text_format=schema,
                max_output_tokens=max_output_tokens, store=False, **options)
        if response.status != 'completed' or response.output_parsed is None:
            raise ValueError('LLM response refused or incomplete')
        # Revalidate even when a test/integration provider returns a model instance.
        parsed = response.output_parsed
        return schema.model_validate(parsed.model_dump() if hasattr(parsed, 'model_dump') else parsed)
