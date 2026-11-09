"""Rescore saved evidence only: no model calls, DB connections or gold mutations."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from backend.scripts.evaluate_sql_live import (
    QUESTIONS, load_questions, score_refusal, summarize_live, write_json,
)
from backend.scripts.evaluate_sql_visualization import ROOT, digest_bytes, revision_metadata


def rescore(report, cases):
    if report['questions_sha256'] != digest_bytes(QUESTIONS.read_bytes()):
        raise ValueError('Question set differs from the executed evaluation')
    expected = {case['id']:case for case in cases}
    ids = [record['id'] for record in report['cases']]
    if len(ids) != len(set(ids)) or any(identity not in expected for identity in ids):
        raise ValueError('Duplicate or unknown evidence IDs')
    for record in report['cases']:
        if record['expected_outcome']=='reject':
            declared = any((call.get('draft') or {}).get('unsupported') for call in record.get('calls',[]))
            record.update(score_refusal(record.get('status'),record.get('observed_sql',[]),declared))
    report['scoring_version']=2
    report['summary']=summarize_live(report['cases'],cases)
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args(argv)
    if args.input.resolve()==args.output.resolve():
        parser.error('Preserve raw execution evidence; choose a different output path')
    report=rescore(json.loads(args.input.read_text(encoding='utf-8')),load_questions())
    sources=['backend/scripts/score_sql_live_report.py','backend/scripts/evaluate_sql_live.py']
    report['rescoring']={'at_utc':datetime.now(timezone.utc).isoformat(),
        'raw_report':args.input.name,'raw_report_sha256':digest_bytes(args.input.read_bytes()),
        'harness':revision_metadata(sources),
        'source_sha256':{path:digest_bytes((ROOT/path).read_bytes()) for path in sources},
        'reason':'Separate explicit supported-schema refusal from generic safe nonexecution/failure; no model/DB rerun.'}
    write_json(args.output,report)
    print(json.dumps(report['summary'],ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
