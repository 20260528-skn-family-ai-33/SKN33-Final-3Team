"""사람의 명시적인 문항 검수 결과를 확인해 평가 정답셋으로 변환합니다."""
import argparse
import hashlib
import json
from pathlib import Path

from backend.app.schemas.rag import ArticleKey, RagRequest
from backend.app.services.rag.evaluation import EvaluationDataset, fingerprint, write_new_json


def file_hash(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def prepare_review(dataset_path, corpus_path, output):
    dataset = EvaluationDataset.model_validate_json(Path(dataset_path).read_text(encoding='utf-8'))
    template = {'dataset_fingerprint': fingerprint(dataset), 'corpus_sha256': file_hash(corpus_path),
                'reviewer': None, 'review_reference': None,
                'instructions': '질문·조건·복수 정답을 원문으로 검수한 후 각 decision을 approved로 바꿉니다. '
                                '정답 없음은 Dev에서 approved_no_answer로 분리합니다. pending은 확정할 수 없습니다.',
                'cases': [{'case_id': c.case_id, 'request': c.request.model_dump(mode='json'),
                           'decision': 'pending', 'conditions_confirmed': False, 'evidence': [],
                           'no_answer_reason': None} for c in dataset.cases]}
    write_new_json(output, template)
    return template


def nonblank(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(name + '_required')
    return value


def finalize_review(dataset_path, review_path, corpus_path, output):
    dataset = EvaluationDataset.model_validate_json(Path(dataset_path).read_text(encoding='utf-8'))
    review = json.loads(Path(review_path).read_text(encoding='utf-8'))
    if review['dataset_fingerprint'] != fingerprint(dataset):
        raise ValueError('draft_changed_since_review_started')
    if review['corpus_sha256'] != file_hash(corpus_path):
        raise ValueError('corpus_changed_since_review_started')
    nonblank(review.get('reviewer'), 'reviewer')
    reference = nonblank(review.get('review_reference'), 'review_reference')
    rows = review['cases']
    by_id = {r['case_id']: r for r in rows}
    if len(by_id) != len(rows) or set(by_id) != {c.case_id for c in dataset.cases}:
        raise ValueError('review_case_ids_mismatch')
    wanted = {ArticleKey(source_code=e['source_code'], external_id=e['external_id']).key
              for row in rows for e in row['evidence']}
    articles = {}
    with Path(corpus_path).open(encoding='utf-8') as stream:
        for line in stream:
            article = json.loads(line)
            key = ArticleKey(source_code=article['source_code'], external_id=article['external_id']).key
            if key in wanted:
                if key in articles:
                    raise ValueError('duplicate_corpus_article')
                articles[key] = article
    cases = []
    for original in dataset.cases:
        row = by_id[original.case_id]
        request = RagRequest.model_validate(row['request'])
        if request.request_id != original.request.request_id:
            raise ValueError('request_identity_changed')
        if row.get('conditions_confirmed') is not True:
            raise ValueError('human_condition_review_required')
        keys = []
        if row['decision'] == 'approved':
            if not row['evidence']:
                raise ValueError('positive_answer_requires_evidence')
            for entry in row['evidence']:
                key = ArticleKey(source_code=entry['source_code'], external_id=entry['external_id'])
                if key.key not in articles:
                    raise ValueError('gold_article_not_in_frozen_corpus')
                article = articles[key.key]
                quote = nonblank(entry.get('quote'), 'quote')
                nonblank(entry.get('reason'), 'relevance_reason')
                if quote not in article['body']:
                    raise ValueError('gold_quote_not_in_original')
                if not request.conditions.period.start.isoformat() <= article['published_date'] <= request.conditions.period.end.isoformat():
                    raise ValueError('gold_article_outside_period')
                geo = request.conditions.geography
                if geo.mode == 'selected' and not set(geo.codes) & set(article.get('countries', [])):
                    raise ValueError('gold_article_outside_countries')
                keys.append(key.model_dump(mode='json'))
        elif row['decision'] == 'approved_no_answer' and dataset.split == 'dev':
            if row['evidence']:
                raise ValueError('no_answer_must_have_no_gold_articles')
            nonblank(row.get('no_answer_reason'), 'no_answer_reason')
        else:
            raise ValueError('explicit_human_review_required')
        cases.append({'case_id': original.case_id, 'request': request.model_dump(mode='json'), 'relevant_articles': keys})
    if dataset.split == 'holdout' and len(cases) != 50:
        raise ValueError('final_set_requires_50_positive_questions')
    confirmed = EvaluationDataset.model_validate({**dataset.model_dump(mode='json'), 'reviewed': True,
                                                  'review_reference': reference, 'cases': cases})
    output = Path(output)
    if output.exists():
        raise FileExistsError('기존 검수 결과는 덮어쓰지 않습니다')
    output.mkdir(parents=True)
    write_new_json(output/'dataset.json', confirmed.model_dump(mode='json'))
    write_new_json(output/'review_audit.json', {**review, 'review_file_sha256': file_hash(review_path),
        'confirmed_dataset_fingerprint': fingerprint(confirmed), 'semantic_review': 'human',
        'automatic_checks': ['identity', 'quote_in_original', 'date', 'country'],
        'human_checks': ['question_quality', 'all_relevant_answers', 'product_and_block_conditions', 'semantic_relevance']})
    return confirmed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'finalize'])
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--corpus', type=Path, required=True)
    parser.add_argument('--review', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.action == 'prepare':
        result = prepare_review(args.dataset, args.corpus, args.output)
        print(json.dumps({'status':'pending_human_review','cases':len(result['cases'])}))
    else:
        if args.review is None:parser.error('--review가 필요합니다')
        result = finalize_review(args.dataset, args.review, args.corpus, args.output)
        print(json.dumps({'status':'reviewed','cases':len(result.cases),'quality_measured':False}))


if __name__ == '__main__':
    main()
