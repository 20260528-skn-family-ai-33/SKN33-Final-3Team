"""Synthetic SQLite regressions for ranking/trend multiple product filters."""
import io
import unittest
from openpyxl import load_workbook
from sqlalchemy import select
from backend.tests import test_backend_api as fixtures


class RankMultiProductsTest(unittest.TestCase):
    setUp = fixtures.BackendApiTest.setUp
    tearDown = fixtures.BackendApiTest.tearDown
    signup = fixtures.BackendApiTest.signup
    post = fixtures.BackendApiTest.post

    def prepare(self):
        self.signup()
        with self.engine.begin() as c:
            hs = self.meta.tables['hs_code']
            original = dict(c.execute(select(hs).where(hs.c.hs_code == '330499')).mappings().one())
            row = {**original, 'hs_code': '330410', 'name_ko': '립 메이크업'}
            if hs.c.depth.computed is not None:
                row.pop('depth', None)
            c.execute(hs.insert(), row)
            for name in ('export_summary', 'import_summary'):
                table = self.meta.tables[name]
                rows = list(c.execute(select(table).where(table.c.hs6_code == '330499')).mappings())
                c.execute(table.insert(), [{**r, 'hs6_code': '330410', 'amount_usd': r['amount_usd'] * 2} for r in rows])

    def get(self, endpoint='rankings', **filters):
        return self.client.get('/api/v1/trade/' + endpoint, params={
            'period_start': '2025-10', 'period_end': '2025-12', 'hs_ver': 'HS2022', **filters})

    def test_union_amounts_overlap_and_saved_export(self):
        self.prepare()
        for endpoint in ('rankings', 'trends'):
            with self.subTest(endpoint=endpoint):
                single = self.get(endpoint, hs_code='330499').json()
                multiple = self.get(endpoint, hs_codes=['330499', '330410'])
                self.assertEqual(multiple.status_code, 200, multiple.text)
                data = multiple.json()
                self.assertEqual([h['hs_code'] for h in data['conditions']['hs_items']], ['330499', '330410'])
                self.assertEqual(sum(r['export_amount_usd'] for r in data['items']),
                                 3 * sum(r['export_amount_usd'] for r in single['items']))
                self.assertEqual(sum(r['import_amount_usd'] for r in data['items']),
                                 3 * sum(r['import_amount_usd'] for r in single['items']))
                overlap = self.get(endpoint, hs_codes=['3304', '330499', '330410', '330499'])
                self.assertEqual(overlap.status_code, 200, overlap.text)
                self.assertEqual(overlap.json()['items'], data['items'])
                exported = self.client.get('/api/v1/results/' + data['result_id'] + '/file')
                self.assertEqual(exported.status_code, 200, exported.text if exported.status_code != 200 else '')
                self.assertTrue(exported.content.startswith(b'PK'))
                workbook = load_workbook(io.BytesIO(exported.content))
                sheet = workbook['결과표1']
                headers = [cell.value for cell in sheet[1]]
                for field, label in [('export_amount_usd', '수출액 (USD)'), ('import_amount_usd', '수입액 (USD)')]:
                    column = headers.index(label)
                    self.assertEqual(sum(row[column] or 0 for row in list(sheet.values)[1:]),
                                     sum(row[field] for row in data['items']))
                info = str(list(workbook['정보'].values))
                self.assertIn('330499', info)
                self.assertIn('330410', info)
        ranked = self.get(hs_codes=['330499', '330410'], country_codes=['US'], group_by='HS6', sort_flow='IMPORT').json()
        self.assertEqual([r['hs_item']['hs_code'] for r in ranked['items']], ['330410', '330499'])

    def test_invalid_multi_filter_is_rejected(self):
        self.prepare()
        for params in ({'hs_code': '330499', 'hs_codes': ['330410']},
                       {'hs_codes': ['330499'], 'hs_ver': ''},
                       {'hs_codes': ['999999']}, {'hs_codes': ['330']},
                       {'hs_codes': ['330499'] * 201}):
            with self.subTest(params=params):
                response = self.get(**params)
                self.assertIn(response.status_code, (400, 422), response.text)

    def test_interested_scope_restricts_every_requested_product(self):
        self.prepare()
        current = self.client.get('/api/v1/users/me/interests').json()['interests']
        saved = self.client.put('/api/v1/users/me/interests', headers={'X-CSRF-Token': self.csrf}, json={
            'hs_items': [{'hs_code': '330499', 'hs_ver': 'HS2022'}], 'country_codes': ['US'],
            'expected_version': current['version']})
        self.assertEqual(saved.status_code, 200, saved.text)
        allowed = self.get(scope='INTERESTED', hs_codes=['3304', '330499'])
        self.assertEqual(allowed.status_code, 200, allowed.text)
        self.assertEqual(allowed.json()['conditions']['hs_items'], [{'hs_code': '330499', 'hs_ver': 'HS2022'}])
        denied = self.get(scope='INTERESTED', hs_codes=['330499', '330410'])
        self.assertEqual(denied.status_code, 400, denied.text)
