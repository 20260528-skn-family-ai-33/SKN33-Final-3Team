import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from backend.app.agents.market import loaded, model_paths, predict
from backend.app.agents.state import Conditions
from backend.app.api.config import Settings


class MarketTest(unittest.TestCase):
    def test_country_prediction_uses_model_rank_for_rows_and_digest(self):
        conditions = Conditions(country_codes=['VN'])
        row = {'anchor': '2026-09-01', 'country_code': 'VN', 'country_size': 100,
               'product_rank': 5, 'product_size': 35, 'growth_pct': 2.1, 'exp_12m': 123456}
        provider = Mock()
        provider.return_value.to_dict.return_value = [
            {**row, 'hs6': '330499', 'country_rank': 3},
            {**row, 'hs6': '330410', 'country_rank': 1},
            {**row, 'hs6': '330420', 'country_rank': 1},
        ]
        result = predict(SimpleNamespace(conditions=conditions), {}, provider, {'model': 'model-v1'})
        provider.assert_called_once_with({}, country_code='VN', hs6=None, top=5)
        self.assertEqual([r['country_rank'] for r in result.rows], [1, 1, 3])
        self.assertEqual([r['hs6'] for r in result.result.digest['targets'][0]['items']],
                         ['330410', '330420', '330499'])

    def test_registry_path_relative_to_monthly_directory_and_ready_only(self):
        base=Path('tmp')
        base.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base.resolve()) as folder:
            root=Path(folder)
            for num,status in [(1,'READY'),(2,'FAILED')]:
                record={'status':status,'model_key':'model_a','artifact_path':f'{num}.joblib',
                    'created_at':f'2026-10-0{num}','data_info':{'trade_month':'2026-09-01'}}
                (root/f'model_a_two_stage_{num}.registry.json').write_text(json.dumps(record),encoding='utf-8')
            settings=Settings({'API_ML_MODEL_DIRECTORY':folder})
            paths=model_paths(settings)
            self.assertEqual(paths,(root/'1.joblib',))
            paths[0].write_bytes(b'monthly model artifact')
            with patch('backend.app.ml.two_stage.load_market_bundle',return_value={'anchor':'2026-09-01'}) as load:
                bundle,_,versions=loaded(settings)
                load.assert_called_once_with([str(paths[0])])
            self.assertEqual(bundle['anchor'],'2026-09-01')
            self.assertEqual(set(versions),{'model'})

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
