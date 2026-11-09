"""Offline guard/provenance checks; never call a model or a database."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch, MagicMock

from backend.scripts import evaluate_sql_holdout as holdout
from backend.scripts import evaluate_sql_live as live


def frozen_dataset():
    # Unit tests inspect the frozen artifact, not the current checkout's HEAD.
    # Later documentation commits/merges must not require rewinding the repository.
    manifest=json.loads(holdout.MANIFEST.read_text(encoding='utf-8'))
    cases=[json.loads(line) for line in holdout.QUESTIONS.read_text(encoding='utf-8').splitlines()]
    return manifest,cases


class HoldoutEvaluationTest(unittest.TestCase):
    def test_frozen_questions_are_new_balanced_and_include_global_controls(self):
        manifest,cases=frozen_dataset()
        self.assertEqual(holdout.digest_bytes(holdout.QUESTIONS.read_bytes()),manifest['questions_sha256'])
        original=live.load_questions()
        self.assertEqual(len(cases),30)
        self.assertEqual(set(manifest['category_counts'].values()),{5})
        self.assertFalse({c['question'] for c in cases}&{c['question'] for c in original})
        self.assertTrue(all(c['novelty'] for c in cases))
        controls=[c for c in cases if c['global_scope_control']]
        self.assertGreaterEqual(len(controls),4)
        self.assertTrue(all(c['expected_outcome']=='answer' for c in controls))
        self.assertEqual(manifest['human_review']['status'],'pending')

    def test_adapter_keeps_original_scores_and_rows(self):
        manifest,cases=frozen_dataset()
        report={'summary':{'result_accuracy':{'passed':0,'total':1}},'evidence_sha256':{},
                'cases':[dict(id='H01',status='NO_DATA',correct=False,execution_success=False,
                              actual_rows=[],calls=[dict(draft={'unsupported':'reason'})])],
                'limitations':[]}
        saved=deepcopy(report)
        enriched=holdout.enrich_report(report,manifest,cases,{'adapter':'hash'})
        self.assertEqual(enriched['summary'],saved['summary'])
        self.assertEqual(enriched['cases'],saved['cases'])
        self.assertEqual(enriched['dataset_role'],'question_holdout')
        self.assertEqual(enriched['global_scope_controls']['explicit_refusals'],['H01'])

    def test_prior_attempt_blocks_second_run_before_database_or_model(self):
        marker=MagicMock()
        marker.exists.return_value=True
        with patch.object(holdout,'check_frozen',return_value=({},[])), \
                patch.object(holdout,'LOCK',marker), patch.object(live,'local_engine') as db, \
                patch.object(live,'main') as run:
            with self.assertRaisesRegex(ValueError,'already attempted'):
                holdout.main(['--run-live'])
            db.assert_not_called()
            run.assert_not_called()


if __name__=='__main__': unittest.main()
