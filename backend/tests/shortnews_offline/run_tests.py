import argparse
import hashlib
import importlib
import json
import os
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
notes = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument('--repo', type=Path, required=True)
parser.add_argument('--evidence-dir', type=Path)
parser.add_argument('tests', nargs='*')
args = parser.parse_args()
repo = args.repo.resolve()
allowed_repositories = {Path(__file__).resolve().parents[3]}
if repo not in allowed_repositories:
    parser.error('Only the approved C, S or representative repository is allowed')
for selected in args.tests:
    if not Path(selected.split('::')[0]).resolve().is_relative_to(repo/'backend/tests'):
        parser.error('Test selection must be under the explicitly selected repository')
if args.evidence_dir:
    args.evidence_dir.mkdir(parents=True, exist_ok=True)
run = Path(tempfile.mkdtemp(prefix='shortnews-offline-', dir=args.evidence_dir))
snapshot = run/'runner_snapshot'
snapshot.mkdir()
for source in [Path(__file__).resolve(), *sorted((notes/'guard').glob('*.py'))]:
    destination = snapshot/source.relative_to(notes)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(source.read_bytes())
root = run/'isolated'
root.mkdir()
keep = {key:value for key,value in os.environ.items() if key in {'PATH','HOME','LANG','TMPDIR','TMP','TEMP'}}
os.environ.clear()
os.environ.update(keep)
os.environ.update(PYTHONDONTWRITEBYTECODE='1', SHORTNEWS_OFFLINE_ROOT=str(root),
                  ANONYMIZED_TELEMETRY='False', SCHEDULER_GUARD_LOG=str(run/'scheduler_guard.jsonl'),
                  SCHEDULER_TEST_REPO=str(repo),
                  PYTHONPATH=os.pathsep.join([str(notes/'guard'),str(repo)]))
os.chdir(root)
sys.path[:0] = [str(notes/'guard'),str(repo)]
from offline_guard import install
install()
imports = {}
for name in ('backend.app.scheduler.main','backend.app.scheduler.jobs.shortnews_daily',
             'backend.app.scheduler.jobs.un_data_refresh','backend.app.services.shortnews_keywords'):
    path = Path(importlib.import_module(name).__file__).resolve()
    assert path.is_relative_to(repo)
    imports[name] = str(path)
(run/'imports.json').write_text(json.dumps(imports,indent=2))
selected = args.tests or [str(repo/path) for path in ['backend/tests/test_shortnews_analysis.py', 'backend/tests/test_shortnews_daily.py', 'backend/tests/test_shortnews_followup.py', 'backend/tests/test_shortnews_keywords.py', 'backend/tests/test_shortnews_load.py', 'backend/tests/test_shortnews_pipeline.py', 'backend/tests/test_shortnews_recovery.py', 'backend/tests/test_shortnews_review.py', 'backend/tests/test_shortnews_search.py', 'backend/tests/test_shortnews_transition.py', 'backend/tests/test_shortnews_vectors_integration.py', 'backend/tests/test_shortnews_worker.py', 'backend/tests/test_news_embed.py', 'backend/tests/test_news_erd_compat.py', 'backend/tests/test_news_keywords.py', 'backend/tests/test_news_load.py', 'backend/tests/test_news_load_lock.py', 'backend/tests/test_admin_news_migration.py', 'backend/tests/test_admin_news_mysql.py', 'backend/tests/test_admin_news_views.py', 'backend/tests/test_hs_code_refresh.py', 'backend/tests/test_customs_monthly.py::MonthlyScheduleTest::test_shared_scheduler_registers_all_jobs_without_executing_them', 'backend/tests/test_un_scheduler_integration.py', 'backend/tests/test_shortnews_isolated_contract.py', 'backend/tests/test_news_index_migrate.py']]
(run/'manifest.json').write_text(json.dumps({'repo':str(repo),'selected':selected,'run':str(run),
    'test_file_sha256':{name:hashlib.sha256(Path(name).read_bytes()).hexdigest()
                       for name in sorted({str(p) for item in selected for p in ([Path(item.split('::')[0])] if Path(item.split('::')[0]).is_file() else Path(item).rglob('test_*.py'))})}},indent=2))
print('RUN_EVIDENCE='+str(run),flush=True)
import pytest
code = pytest.main(selected+['-q','-p','no:cacheprovider','-p','offline_guards',
                            '-p','scheduler_fixture_guard','-p','safe_collection','--basetemp='+str(root/'pytest'),
                            '--junitxml='+str(run/'results.xml')])
(run/'exit.json').write_text(json.dumps({'exit_code':code}))

from import_guard import verify_modules
verify_modules(repo,run/'final_imports.json')

raise SystemExit(code)
