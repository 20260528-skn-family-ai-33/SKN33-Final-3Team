import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from backend.app.agents.market import model_paths,predict
from backend.app.agents.state import Conditions
from backend.app.api.config import Settings


class MarketTest(unittest.TestCase):
    def test_registry_path_relative_to_monthly_directory_and_ready_only(self):
        base=Path('tmp')
        base.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base.resolve()) as folder:
            root=Path(folder)
            for num,status in [(1,'READY'),(2,'FAILED')]:
                record={'status':status,'model_key':'model_a','artifact_path':f'{num}.joblib',
                    'created_at':f'2026-10-0{num}','data_info':{'trade_month':'2026-09-01'}}
                (root/f'model_a_two_stage_{num}.registry.json').write_text(json.dumps(record),encoding='utf-8')
            paths,snapshot=model_paths(Settings({'API_ML_MODEL_DIRECTORY':folder}))
            self.assertEqual(paths,(root/'1.joblib',))
            self.assertEqual(snapshot.name,'features_snapshot_py311_latest_v2.pkl')

    def test_pair_rank_is_preserved_and_missing_pairs_are_partial(self):
        conditions=Conditions(hs_items=[{'hs_code':'330499','hs_ver':'HS2022'}],country_codes=['US','CN'])
        row={'anchor':'2026-09-01','country_code':'US','hs6':'330499','country_rank':17,'country_size':100,
             'product_rank':12,'product_size':35,'growth_pct':2.1,'exp_12m':123456}
        first,second=Mock(),Mock()
        first.to_dict.return_value=[row]; second.to_dict.return_value=[]
        result=predict(SimpleNamespace(conditions=conditions),{},Mock(side_effect=[first,second]),{'model':'model-v1'})
        self.assertEqual(result.result.completeness,'PARTIAL')
        self.assertEqual(result.rows[0]['country_rank'],17)
        self.assertEqual(result.meta['missing_targets'],['CN · 330499'])
        self.assertEqual(result.result.period.start.isoformat(),'2026-09-01')
