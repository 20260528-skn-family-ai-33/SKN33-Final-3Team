"""One-shot dataset adapter around the committed evaluator; agent/scoring stay unchanged."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
from unittest.mock import patch

from dotenv import dotenv_values
from backend.scripts import evaluate_sql_live as live
from backend.scripts.evaluate_sql_visualization import ROOT, digest_bytes
from backend.scripts.prepare_sql_holdout import BASE, QUESTIONS, MANIFEST

RESULT = live.DATA / 'sql_holdout_result.json'
LOCK = live.DATA / 'sql_holdout_run_lock.json'
ADAPTER_FILES = ['backend/scripts/evaluate_sql_holdout.py','backend/scripts/prepare_sql_holdout.py']


def check_frozen():
    head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    if head!=BASE:
        raise ValueError('Holdout must run from the reviewed baseline commit')
    sources=['backend/app','backend/scripts/evaluate_sql_live.py','backend/scripts/evaluate_sql_visualization.py',
             'docs/evaluations/sql_snapshot.sql','docs/evaluations/sql_questions.jsonl','docs/evaluations/sql_questions_manifest.json']
    subprocess.run(['git','diff','--exit-code',BASE,'--',*sources],cwd=ROOT,check=True,capture_output=True)
    if subprocess.check_output(['git','ls-files','--others','--exclude-standard','backend/app'],cwd=ROOT,text=True).strip():
        raise ValueError('Unexpected untracked production module')
    manifest=json.loads(MANIFEST.read_text(encoding='utf-8'))
    if digest_bytes(QUESTIONS.read_bytes())!=manifest['questions_sha256']:
        raise ValueError('Frozen holdout questions changed')
    for path,expected in manifest['frozen_code_sha256'].items():
        if digest_bytes((ROOT/path).read_bytes())!=expected:
            raise ValueError('Frozen source changed: '+path)
    cases=[json.loads(line) for line in QUESTIONS.read_text(encoding='utf-8').splitlines()]
    if len(cases)!=30 or any(sum(c['category']==cat for c in cases)<4 for cat in manifest['category_counts']):
        raise ValueError('Holdout category coverage differs from freeze')
    return manifest,cases


def enrich_report(report, manifest, cases, adapter_hashes):
    """Change provenance labels and add a subset metric only; never change core scores."""
    report['dataset_role']='question_holdout'
    report['kind']='single_run_question_holdout_real_llm_local_mysql'
    report['agent_revision']=BASE
    report['holdout_frozen_at_utc']=manifest['frozen_at_utc']
    report['execution_policy']='Exactly one run; committed agent/prompt and original scoring unchanged'
    report['adapter_sha256']=adapter_hashes
    report['evidence_sha256'].update({str(QUESTIONS.relative_to(ROOT)):manifest['questions_sha256'],
        str(MANIFEST.relative_to(ROOT)):digest_bytes(MANIFEST.read_bytes()),**adapter_hashes})
    report['limitations']=[
        'New questions held out from agent tuning; same synthetic snapshot and categories, not unseen real-world data.',
        'Questions and independent SQL/Python gold were agent-authored; human review pending.',
        'Preconfirmed conditions; Planner and interactive clarification not evaluated.',
        'SQL generation/execution and scoring are the reviewed commit; adapter only selects dataset and records provenance.',
        'ChartSpec through in-memory snapshot adapter; UI rendering not evaluated.',
        'One run only. Costs from received usage are estimates, not billing statements.']
    ids={c['id'] for c in cases if c['global_scope_control']}
    selected=[r for r in report['cases'] if r['id'] in ids]
    report['global_scope_controls']={
        'planned_ids':sorted(ids),'evaluated':len(selected),'correct':sum(r.get('correct',False) for r in selected),
        'execution_success':sum(r.get('execution_success',False) for r in selected),
        'explicit_refusals':[r['id'] for r in selected if r.get('status')=='NO_DATA'
            and any((call.get('draft') or {}).get('unsupported') for call in r.get('calls',[]))]}
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-live',action='store_true',required=True)
    parser.add_argument('--env-file',type=Path,help='Read only OPENAI_API_KEY; no DB settings are adopted')
    parser.add_argument('--max-cost-usd',type=float,default=1.0)
    args=parser.parse_args(argv)
    manifest,cases=check_frozen()
    if RESULT.exists() or LOCK.exists():
        raise ValueError('Holdout already attempted; refusing a second run or evidence overwrite')
    if not 0<args.max_cost_usd<=1:
        raise ValueError('Holdout cost cap must be positive and at most USD 1')
    key=os.environ.get('OPENAI_API_KEY')
    if not key and args.env_file:
        key=next((v for k,v in dotenv_values(args.env_file).items() if k.lower()=='openai_api_key' and v),None)
    if not key: raise ValueError('OPENAI_API_KEY is missing; no model calls made')
    # Validate the snapshot before consuming the one-run marker. No model calls here.
    engine=live.local_engine(3308)
    try:
        if live.database_identity(engine)['data_sha256']!=manifest['snapshot']['data_sha256']:
            raise ValueError('Snapshot differs from frozen gold')
    finally: engine.dispose()
    adapter_hashes={p:digest_bytes((ROOT/p).read_bytes()) for p in ADAPTER_FILES}
    with LOCK.open('x',encoding='utf-8') as handle:
        json.dump(dict(started_at_utc=datetime.now(timezone.utc).isoformat(),agent_revision=BASE,
            questions_sha256=manifest['questions_sha256'],adapter_sha256=adapter_hashes,
            policy='No second run, even if partial or low-scoring'),handle,ensure_ascii=False,indent=2)
        handle.write('\n')
    writer=live.write_json
    def write_report(path,value):
        if Path(path)==RESULT:
            value=enrich_report(value,manifest,cases,adapter_hashes)
        writer(path,value)
    try:
        # Only data locations, API-key environment, and evidence writer are adapted.
        # generate/validate/execute/retry/build_chart/score_case remain the committed functions.
        with patch.object(live,'QUESTIONS',QUESTIONS), patch.object(live,'MANIFEST',MANIFEST), \
                patch.object(live,'write_json',write_report), patch.dict(os.environ,{'OPENAI_API_KEY':key}):
            return live.main(['--run-live','--allow-unreviewed-gold','--max-cost-usd',str(args.max_cost_usd),
                              '--output',str(RESULT)])
    finally:
        check_frozen()
        if any(digest_bytes((ROOT/p).read_bytes())!=h for p,h in adapter_hashes.items()):
            raise ValueError('Dataset adapter changed during execution')


if __name__=='__main__': raise SystemExit(main())
