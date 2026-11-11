"""저장한 실제 호출 결과만 다시 채점한다. 모델 호출·DB 연결·고정 질문 수정은 하지 않는다(팀 score_sql_live_report.py와 같은 원칙).

정답 수정은 report_agent_live_gold_corrections.json에 이유와 함께 따로 둔다. 원본 결과 파일은 덮어쓰지 않는다.
  python -m backend.scripts.score_report_agent_live --input docs/evaluations/report_agent_live_result.json \
      --output docs/evaluations/report_agent_live_result_rescored.json
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from backend.scripts import evaluate_report_agent_live as live
from backend.scripts.evaluate_sql_visualization import ROOT, digest_bytes, revision_metadata

CORRECTIONS = live.DATA / 'report_agent_live_gold_corrections.json'


def rescore(report, cases, corrections):
    if report['questions_sha256'] != digest_bytes(live.SETS['live']['questions'].read_bytes()):
        raise ValueError('실행한 질문 세트와 지금 질문 세트가 다릅니다')
    expected = {case['id']: case for case in cases}
    ids = [record['id'] for record in report['cases']]
    if len(ids) != len(set(ids)) or any(identity not in expected for identity in ids):
        raise ValueError('중복되거나 모르는 사례 ID가 있습니다')
    if any(identity not in expected for identity in corrections):
        raise ValueError('정답 수정에 모르는 사례 ID가 있습니다')
    corrected = {i: {**expected[i], 'expected': fix['expected']} for i, fix in corrections.items()}
    cases = [corrected.get(case['id'], case) for case in cases]
    for record in report['cases']:
        if 'actual' not in record:
            continue   # 오류·중단 사례는 출력이 없어 그대로 둔다
        case = next(c for c in cases if c['id'] == record['id'])
        record['expects_report'] = case['expected']['report'] is not None
        record.update(live.score_case(case, record['actual']))
        if record['id'] in corrections:
            record['gold_corrected'] = True
    report['scoring_version'] = 2
    report['dataset_role'] = 'development'
    report['summary'] = live.summarize(report['cases'], cases)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    if args.input.resolve() == args.output.resolve():
        parser.error('원본 실행 결과는 보존합니다. 다른 출력 경로를 고르세요')
    fixes = json.loads(CORRECTIONS.read_text(encoding='utf-8'))
    report = rescore(json.loads(args.input.read_text(encoding='utf-8')), live.load_questions(live.SETS['live']['questions']),
                     fixes['corrections'])
    sources = ['backend/scripts/score_report_agent_live.py', 'backend/scripts/evaluate_report_agent_live.py',
               'docs/evaluations/report_agent_live_gold_corrections.json']
    report['rescoring'] = {'at_utc': datetime.now(timezone.utc).isoformat(), 'raw_report': args.input.name,
                           'raw_report_sha256': digest_bytes(args.input.read_bytes()), 'harness': revision_metadata(sources),
                           'source_sha256': {p: digest_bytes((ROOT / p).read_bytes()) for p in sources},
                           'corrected_cases': sorted(fixes['corrections']),
                           'reason': fixes['principle']}
    live.write_json(args.output, report)
    print(json.dumps(report['summary']['overall_accuracy'], ensure_ascii=False))
    print(json.dumps(report['summary']['by_category'], ensure_ascii=False))


if __name__ == '__main__':
    main()
