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

    def test_merged_hs6_is_queried_by_group_code_and_shown_as_requested(self):
        # 150930은 판 개정으로 150920 묶음에 들어 있어 모델에는 150920으로만 있다.
        conditions = Conditions(hs_items=[{'hs_code': '150930', 'hs_ver': 'HS2022'}])
        provider = Mock()
        provider.return_value.to_dict.return_value = [
            {'anchor': '2026-09-01', 'country_code': 'VN', 'hs6': '150920', 'country_rank': 2, 'country_size': 50,
             'product_rank': 1, 'product_size': 30, 'growth_pct': 3.0, 'exp_12m': 500000}]
        result = predict(SimpleNamespace(conditions=conditions), {}, provider, {'model': 'm'},
                         codes={('150930', 'HS2022'): '150920'})
        provider.assert_called_once_with({}, country_code=None, hs6='150920', top=5)
        self.assertEqual(result.result.status, 'OK')
        self.assertEqual((result.rows[0]['hs6'], result.rows[0]['hs_ver']), ('150930', 'HS2022'))
        # 묶음 대표 코드로 조회한 것은 누락이 아니다. 완전한 결과이고 안내는 ML 단계의 적용 조건에 남긴다.
        self.assertEqual(result.result.completeness, 'COMPLETE')

    def test_same_digits_in_two_editions_stay_separate(self):
        conditions = Conditions(hs_items=[{'hs_code': '150910', 'hs_ver': 'HS2017'}, {'hs_code': '150910', 'hs_ver': 'HS2022'}])
        base = {'anchor': '2026-09-01', 'country_code': 'VN', 'country_rank': 2, 'country_size': 50,
                'product_rank': 1, 'product_size': 30, 'growth_pct': 3.0, 'exp_12m': 500000}
        provider = Mock(side_effect=[SimpleNamespace(to_dict=lambda orient: [{**base, 'hs6': '150920'}]),
                                     SimpleNamespace(to_dict=lambda orient: [{**base, 'hs6': '150910'}])])
        result = predict(SimpleNamespace(conditions=conditions), {}, provider, {'model': 'm'},
                         codes={('150910', 'HS2017'): '150920'})
        self.assertEqual([c.kwargs['hs6'] for c in provider.call_args_list], ['150920', '150910'])
        self.assertEqual([(r['hs6'], r['hs_ver']) for r in result.rows], [('150910', 'HS2017'), ('150910', 'HS2022')])

    def test_hs2017_item_keeps_its_edition_for_names(self):
        conditions = Conditions(hs_items=[{'hs_code': '150910', 'hs_ver': 'HS2017'}])
        provider = Mock()
        provider.return_value.to_dict.return_value = [
            {'anchor': '2026-09-01', 'country_code': 'VN', 'hs6': '150920', 'country_rank': 2, 'country_size': 50,
             'product_rank': 1, 'product_size': 30, 'growth_pct': 3.0, 'exp_12m': 500000}]
        result = predict(SimpleNamespace(conditions=conditions), {}, provider, {'model': 'm'},
                         codes={('150910', 'HS2017'): '150920'})
        self.assertEqual((result.rows[0]['hs6'], result.rows[0]['hs_ver']), ('150910', 'HS2017'))

    def test_prediction_chart_keeps_item_edition(self):
        from backend.app.agents.charts import build_chart
        task = SimpleNamespace(task_id='t1', conditions=Conditions(hs_items=[{'hs_code': '150910', 'hs_ver': 'HS2017'}]))
        row = {'anchor': '2026-09-01', 'hs6': '150910', 'hs_ver': 'HS2017', 'product_rank': 1, 'country_rank': 3,
               'country_size': 50, 'product_size': 30, 'growth_pct': 3.0, 'exp_12m': 500000}
        rows = [{**row, 'country_code': 'VN', 'name': '베트남'}, {**row, 'country_code': 'US', 'name': '미국', 'product_rank': 2},
                {**row, 'hs6': '330499', 'hs_ver': 'HS2022', 'country_code': 'JP', 'name': '일본'}]
        snapshot = {'meta': {'kind': 'PREDICTION', 'group_by': 'HS6'}, 'rows': rows}
        with patch('backend.app.agents.charts.read_snapshot', return_value=snapshot):
            result, _, _ = build_chart(None, 1, task, 'r')
            self.assertEqual([(p.hs_code, p.hs_ver) for p in result.chart_selector.hs_items],
                             [('150910', 'HS2017'), ('330499', 'HS2022')])
            chosen, _, _ = build_chart(None, 1, task, 'r', {'hs_code': '150910', 'hs_ver': 'HS2017'})
        self.assertEqual(chosen.chart.labels, ['베트남', '미국'])

    def test_growth_direction_is_attached_and_a_growth_hidden_from_digest(self):
        conditions = Conditions(country_codes=['VN'])
        row = {'anchor': '2026-09-01', 'country_code': 'VN', 'country_size': 100, 'product_rank': 5,
               'product_size': 35, 'growth_pct': -3.0, 'exp_12m': 123456}
        provider = Mock()
        provider.return_value.to_dict.return_value = [{**row, 'hs6': '330499', 'country_rank': 1},
                                                      {**row, 'hs6': '330410', 'country_rank': 2}]
        result = predict(SimpleNamespace(conditions=conditions), {}, provider, {'model': 'm'},
                         directions={('VN', '330499'): 'UP'})
        self.assertEqual([r['growth_outlook'] for r in result.rows], ['증가 예상', None])
        items = result.result.digest['targets'][0]['items']
        self.assertEqual(items[0]['growth_outlook'], '증가 예상')
        self.assertFalse(any('growth_pct' in item for item in items))   # 모델 A 증감률은 요약에 넣지 않는다
        self.assertTrue(all('growth_pct' in r for r in result.rows))      # 순위 근거로 결과표에는 남긴다

    def test_growth_file_is_optional_and_checked(self):
        import tempfile, joblib, pandas as pd
        from pathlib import Path
        from backend.app.agents.market import growth, _load_growth
        _load_growth.cache_clear()
        with tempfile.TemporaryDirectory() as folder:
            settings = Settings({'API_ML_MODEL_DIRECTORY': folder})
            self.assertEqual(growth(settings)[:2], (None, 'none'))
            latest = pd.DataFrame({'anchor': pd.to_datetime(['2026-09-01'] * 2), 'country_code': ['VN', 'US'],
                                   'hs6': ['330499', '330499'], 'growth_pct': [3.0, -1.0], 'direction': ['UP', 'DOWN']})
            joblib.dump({'kind': 'growth_huber', 'latest': latest}, Path(folder) / 'model_b_growth_202609.joblib')
            data, version, note = growth(settings)
            self.assertEqual((data['anchor'], data['directions'][('US', '330499')], note), ('2026-09', 'DOWN', None))
            self.assertNotIn(version, ('none', 'invalid'))
            joblib.dump({'kind': 'growth_huber'}, Path(folder) / 'model_b_growth_202610.joblib')   # latest 없는 파일
            self.assertEqual(growth(settings)[:2], (None, 'invalid'))
            # 모델 A와 같은 기준월 파일이 있으면 더 최근 파일보다 그것을 쓴다
            self.assertEqual(growth(settings, '2026-09')[0]['anchor'], '2026-09')
            # 월별 학습 레지스트리가 있으면 그것으로 고른다(같은 기준월의 최신 READY 리비전, 실패 SKIPPED 기록은 건너뜀)
            import json
            joblib.dump({'kind': 'growth_huber', 'latest': latest}, Path(folder) / 'model_b_growth_202609_r2.joblib')
            for name, status in (('model_b_growth_202609', 'READY'), ('model_b_growth_202609_r2', 'READY'),
                                 ('model_b_growth_202609_r3', 'SKIPPED')):
                (Path(folder) / f'{name}.registry.json').write_text(json.dumps({
                    'model_key': 'model_b', 'status': status, 'artifact_path': f'{name}.joblib' if status == 'READY' else None,
                    'data_info': {'anchor': '2026-09-01'}}), encoding='utf-8')
            from backend.app.agents.market import growth_path
            self.assertEqual(growth_path(settings, '2026-09').name, 'model_b_growth_202609_r2.joblib')
            # 다른 달 레지스트리만 있으면 같은 기준월의 직접 만든 파일을 쓴다
            joblib.dump({'kind': 'growth_huber', 'latest': latest}, Path(folder) / 'model_b_growth_202611.joblib')
            self.assertEqual(growth_path(settings, '2026-11').name, 'model_b_growth_202611.joblib')
            # 리비전은 숫자 순서(_r10이 _r2보다 뒤)
            for revision in (2, 10):
                joblib.dump({'kind': 'growth_huber', 'latest': latest}, Path(folder) / f'model_b_growth_202611_r{revision}.joblib')
            self.assertEqual(growth_path(settings, '2026-11').name, 'model_b_growth_202611_r10.joblib')
            # 잘린 모델 B 파일(EOFError)도 증감 예상만 빠지고 예외를 올리지 않는다
            truncated = Path(folder) / 'model_b_growth_202611_r10.joblib'
            truncated.write_bytes(truncated.read_bytes()[:40])
            self.assertEqual(growth(settings, '2026-11')[:2], (None, 'invalid'))
            # 깨진 레지스트리는 예외를 올리지 않고 증감 예상 없이 답한다
            (Path(folder) / 'model_b_growth_202612.registry.json').write_text('{', encoding='utf-8')
            self.assertEqual(growth(settings, '2026-09')[:2], (None, 'invalid'))
        _load_growth.cache_clear()

    def test_model_codes_reads_correlation_groups(self):
        from backend.app.agents.market import model_codes
        from backend.tests.api_support import test_engine
        from backend.app.api.database import Database, Store
        from backend.app.models.hs_code_correlation import HsCodeCorrelation
        engine, meta = test_engine(); self.addCleanup(engine.dispose)
        table = HsCodeCorrelation.__table__   # API 테스트 스키마에는 없는 적재 테이블이라 모델 정의로 만든다
        table.create(engine)
        with engine.begin() as c:
            c.execute(table.insert(), [{'new_ver': 'HS2022', 'new_code': '150930', 'old_ver': 'HS2017', 'old_code': '150910',
                                        'relation': 'n:1', 'group_code': '150920', 'source_edition': 'test'}])
        with engine.connect() as c:
            hs = lambda code, ver='HS2022': SimpleNamespace(hs_code=code, hs_ver=ver)
            store = Store(Database(engine), c)
            self.assertEqual(model_codes(store, [hs('150930'), hs('330499')]), {('150930', 'HS2022'): '150920'})
            # HS2017 품목은 이전 판 코드(old_code)로 찾는다. 같은 숫자의 HS2022 코드로 보지 않는다(키에 판본을 둔다).
            self.assertEqual(model_codes(store, [hs('150910', 'HS2017')]), {('150910', 'HS2017'): '150920'})
            self.assertEqual(model_codes(store, [hs('150910')]), {})

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
