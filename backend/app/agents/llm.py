"""One bounded Responses call; retries and durable budget belong to the caller."""
import json


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
        with factory(api_key=key, timeout=timeout, max_retries=0) as client:
            response = client.responses.parse(model=model, instructions=prompt,
                input=json.dumps(payload, ensure_ascii=False, allow_nan=False), text_format=schema,
                max_output_tokens=max_output_tokens, temperature=0, store=False)
        if response.status != 'completed' or response.output_parsed is None:
            raise ValueError('LLM response refused or incomplete')
        # Revalidate even when a test/integration provider returns a model instance.
        parsed = response.output_parsed
        return schema.model_validate(parsed.model_dump() if hasattr(parsed, 'model_dump') else parsed)
