"""Model-selected source chunks; quotes are reconstructed locally, never rewritten."""


def summary_ranges(candidate):
    return [{'chunk_id': key, 'start': 0, 'end': len(text), 'quote': text}
            for key, text in candidate.chunks.items() if text.strip()]


def selection_schema(size):
    return {'type': 'object', 'additionalProperties': False, 'properties': {
        'sentences': {'type': 'array', 'maxItems': 2, 'items': {
            'type': 'object', 'additionalProperties': False, 'properties': {
                'text': {'type': 'string'},
                'evidence_index': {'type': 'integer', 'minimum': 0, 'maximum': size - 1}},
            'required': ['text', 'evidence_index']}}}, 'required': ['sentences']}


def materialize_summary(value, spans, candidate):
    if not isinstance(value, dict) or set(value) != {'sentences'} or not isinstance(value['sentences'], list):
        raise ValueError('invalid_summary_selection')
    if len(value['sentences']) > 2:
        raise ValueError('summary_sentence_limit')
    result = []
    for row in value['sentences']:
        if not isinstance(row, dict) or set(row) != {'text', 'evidence_index'}:
            raise ValueError('invalid_summary_selection_fields')
        index = row['evidence_index']
        if type(index) is not int or not 0 <= index < len(spans):
            raise ValueError('invalid_summary_range')
        span = spans[index]
        chunk_id = span['chunk_id']
        original = candidate.chunks[chunk_id]
        if original[span['start']:span['end']] != span['quote']:
            raise ValueError('summary_source_changed')
        quote = original[span['start']:span['end']]
        if not quote.strip() or not isinstance(row['text'], str) or not row['text'].strip():
            raise ValueError('empty_summary_evidence')
        result.append({'text': row['text'], 'chunk_id': chunk_id, 'quote': quote})
    return {'sentences': result}
