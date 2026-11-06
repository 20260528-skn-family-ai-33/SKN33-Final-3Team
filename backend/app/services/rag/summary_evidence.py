"""Model-selected body ranges; quotes are reconstructed locally, never rewritten."""
import re
from itertools import groupby


def body_units(text, offset=0):
    start = offset
    for boundary in re.finditer(r'(?:\r?\n)+|(?<=[.!?。！？])\s+|\Z', text[offset:]):
        end = offset + boundary.end()
        if text[start:end].strip():
            yield start, end
        start = end


def summary_ranges(candidate):
    """Only the stored body may witness a claim. Keep the original chunk offsets."""
    from backend.app.scheduler.jobs.news_embed import format_document
    article = candidate.article
    header = ''
    if hasattr(article, 'published_date'):
        header = format_document({'title': article.title, 'published_date': article.published_date,
                                  'category': article.category, 'countries': article.countries}, '')
    spans = []
    for key, text in candidate.chunks.items():
        offset = len(header) if header and text.startswith(header) else 0
        for start, end in body_units(text, offset):
            spans.append({'chunk_id': key, 'start': start, 'end': end, 'quote': text[start:end]})
    return spans




def numbered_chunks(spans):
    """The first offset and ordered texts identify every passage without sending hash IDs."""
    grouped = []
    for _, items in groupby(enumerate(spans), key=lambda pair:pair[1]['chunk_id']):
        parts = list(items)
        grouped.append({'first_index':parts[0][0], 'passages':[span['quote'] for _, span in parts]})
    return grouped


class InvalidSourceRange(ValueError):
    """A model-selected interval does not identify one verified chunk."""


def source_passage(spans, candidate, first, last):
    if type(first) is not int or type(last) is not int or not 0 <= first <= last < len(spans):
        raise InvalidSourceRange('invalid_summary_range')
    start, end = spans[first], spans[last]
    key = start['chunk_id']
    if end['chunk_id'] != key:
        raise InvalidSourceRange('summary_range_crosses_chunks')
    original = candidate.chunks[key]
    for span in spans[first:last+1]:
        if original[span['start']:span['end']] != span['quote']:
            raise ValueError('summary_source_changed')
    return key, original[start['start']:end['end']]




def coarse_selection_schema(size):
    return {'type': 'object', 'additionalProperties': False, 'properties': {
        'sentences': {'type': 'array', 'maxItems': 2, 'items': {
            'type': 'object', 'additionalProperties': False, 'properties': {
                'text': {'type': 'string'},
                'evidence_index': {'type': 'integer', 'minimum': 0, 'maximum': size - 1}},
            'required': ['text', 'evidence_index']}}}, 'required': ['sentences']}
