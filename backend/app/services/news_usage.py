"""Shared reported provider usage; no API, DB, or shortnews dependency."""
def record_usage(sink, model, response):
    """응답 검증 전에 보고된 사용량을 기록합니다. 실패·usage 미제공을 0원으로 확정하지 않습니다."""
    if sink is None:
        return
    usage = getattr(response, 'usage', None)
    def count(name):
        value = getattr(usage, name, None)
        return value if type(value) is int and value >= 0 else None
    tokens = count('total_tokens')
    sink({'model':model, 'tokens':tokens, 'input_tokens':count('prompt_tokens'),
          'output_tokens':count('completion_tokens'), 'reported':tokens is not None})
