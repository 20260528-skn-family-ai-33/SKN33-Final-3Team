"""실제 기사에서 검수용 초안 생성. 정답은 검수 전 null로 유지합니다."""
import argparse
from collections import Counter, defaultdict
import hashlib
import html
import json
from pathlib import Path

from backend.app.schemas.rag import ALL_SOURCES, ArticleKey, ArticlePeriod
from backend.app.services.rag.evaluation import EvaluationDataset, write_new_json


def prepare_drafts(articles, output, *, dev_count=10, final_count=50):
    output = Path(output)
    if output.exists():
        raise FileExistsError('기존 검수 자료를 보존합니다')
    by_source, seen_content, seen_keys = defaultdict(list), set(), set()
    for row in sorted(articles, key=lambda a: (a['source_code'], a['external_id'])):
        key = ArticleKey(source_code=row['source_code'], external_id=row['external_id'])
        digest = hashlib.sha256(row['body'].encode()).hexdigest()
        if not row['body'].strip() or key.key in seen_keys or digest in seen_content:
            continue
        ArticlePeriod(start=row['published_date'], end=row['published_date'])
        seen_keys.add(key.key); seen_content.add(digest)
        by_source[key.source_code.value].append(row)
    for rows in by_source.values():
        rows.sort(key=lambda row: hashlib.sha256((row['source_code']+row['external_id']).encode()).hexdigest())
    dev_candidates = {source: rows[::6] for source, rows in by_source.items()}
    dev_selection = []
    while any(dev_candidates.values()) and len(dev_selection) < dev_count:
        for source in ALL_SOURCES:
            if dev_candidates.get(source.value) and len(dev_selection) < dev_count:
                dev_selection.append(dev_candidates[source.value].pop(0))
    ordered = []
    while any(by_source.values()):
        for source in ALL_SOURCES:
            if by_source[source.value]:
                ordered.append(by_source[source.value].pop())
    if len(ordered) < dev_count + final_count:
        raise ValueError('초안에 필요한 서로 다른 실제 기사가 부족합니다')
    dev_ids = {(a['source_code'], a['external_id']) for a in dev_selection}
    if len(dev_selection) < dev_count:
        for a in ordered:
            identity = (a['source_code'], a['external_id'])
            if identity not in dev_ids and len(dev_selection) < dev_count:
                dev_selection.append(a)
                dev_ids.add(identity)
    final_selection = [a for a in ordered if (a['source_code'], a['external_id']) not in dev_ids][:final_count]
    review, datasets = [], {}
    for split, selected in [('dev', dev_selection), ('holdout', final_selection)]:
        cases = []
        for n, article in enumerate(selected, 1):
            case_id = f'{split}-{n:03d}'
            country = article.get('countries', [])
            query = f"{article['published_date']} 무렵 {article['title']}와 관련해 기사에서 확인되는 변화와 배경을 설명해줘."
            cases.append({'case_id':case_id, 'request':{'request_id':case_id, 'query':query, 'use_case':'chat',
                'conditions':{'geography':{'mode':'selected','codes':country} if country else {'mode':'all','codes':[]},
                'products':[], 'period':{'start':article['published_date'][:7]+'-01', 'end':article['published_date']},
                'blocked_topics':[]}}, 'relevant_articles':None})
            review.append({'case_id':case_id, 'split':split, 'review_status':'unreviewed',
                           'question_draft':query, 'candidate_article':article,
                           'review_notes':'질문의 자연스러움·원문 근거·추가 정답·확정 조건을 검수하세요. 제목 기반 초안이며 정답이 아닙니다.'})
        dataset = EvaluationDataset(dataset_id=f'actual-article-{split}-draft-v1', split=split, synthetic=False,
                                    reviewed=False, cases=cases)
        datasets[split] = dataset
    output.mkdir(parents=True)
    for split, dataset in datasets.items():
        write_new_json(output/(split+'.json'), dataset.model_dump(mode='json'))
    write_new_json(output/'review.json', review)
    counts = Counter(item['candidate_article']['source_code'] for item in review)
    manifest = {'status':'unreviewed_draft', 'source_counts':dict(counts),
                'split_source_counts':{s:dict(Counter(x['candidate_article']['source_code'] for x in review if x['split']==s))
                                       for s in ('dev','holdout')},
                'missing_sources':[s.value for s in ALL_SOURCES if not counts[s.value]],
                'dev_cases':dev_count, 'final_cases':final_count, 'baseline_measured':False,
                'warning':'정답과 질문은 미검수입니다. 출처별 검증 범위를 함께 확인하고 누락 출처 자료를 보완해야 합니다.'}
    write_new_json(output/'manifest.json',manifest)
    sections=[]
    for item in review:
        a=item['candidate_article']; esc=html.escape
        sections.append(f'<section><h2>{esc(item["case_id"])} · {esc(a["source_code"])}</h2>'
            f'<p>{esc(item["question_draft"])}</p><p>미검수 · 정답 미확정</p>'
            f'<p>{esc(a["title"])} · {esc(a["published_date"])}<br>{esc(a.get("source_url", ""))}</p>'
            f'<details><summary>기사 전체 본문 확인</summary><pre>{esc(a["body"])}</pre></details></section>')
    (output/'review.html').write_text('<!doctype html><html lang="ko"><meta charset="utf-8"><title>RAG 평가 초안 검수</title>'
        '<style>body{font-family:system-ui;max-width:1000px;margin:40px auto;padding:20px}section{border-top:1px solid #ccc;padding:20px 0}pre{white-space:pre-wrap;line-height:1.7}</style>'
        '<h1>실제 기사 기반 평가 초안 — 미검수</h1><p>질문과 정답 후보를 검수한 뒤 정답 목록을 확정합니다. 현재 정답은 모두 null입니다.</p>'
        +''.join(sections)+'</html>',encoding='utf-8')
    return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--articles',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(prepare_drafts([json.loads(line) for line in args.articles.read_text(encoding='utf-8').splitlines()], args.output),ensure_ascii=False))


if __name__=='__main__':main()
