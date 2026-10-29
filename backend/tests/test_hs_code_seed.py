"""Seed/UPDATE generation validates data and preserves the summary-only update scope."""
import csv
import importlib.util
import tempfile
import unittest
from pathlib import Path

src = Path(__file__).resolve().parents[1] / 'app/scheduler/collectors/hscode/hs_code_seed.py'
spec = importlib.util.spec_from_file_location('hs_code_seed', src)
seed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(seed)


class SeedTest(unittest.TestCase):
    def write_csv(self, folder, names):
        path = Path(folder) / 'master.csv'
        with path.open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=seed.hb.FIELDS)
            writer.writeheader()
            for i, name in enumerate(names):
                writer.writerow(dict(hs_code=f'33049{i}', depth='6', name_ko=name + '|원문',
                                     source_edition='관세청 20260101', hs_ver='HS2022'))
        return path

    def test_invalid_summary_refuses_both_artifacts(self):
        for names in (['가' * 21], ['화장품330499'], ['요약A'], ['화장품', '화장품']):
            with self.subTest(names=names), tempfile.TemporaryDirectory() as folder:
                path = self.write_csv(folder, names)
                out = Path(folder) / 'seed.sql'
                with self.assertRaises(ValueError):
                    seed.generate(path, out, Path(folder) / 'update.sql')
                self.assertFalse(out.exists())

    def test_update_changes_only_summary_and_guards_missing_targets(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.write_csv(folder, ['피부관리용 화장품', '눈화장용 제품'])
            initial, migration = Path(folder) / 'seed.sql', Path(folder) / 'update.sql'
            result = seed.generate(path, initial, migration)
            self.assertEqual(result['summaries'], 2)
            sql = migration.read_text(encoding='utf-8')
            self.assertIn('SET h.name_ko = n.name_ko', sql)
            self.assertIn('h.hs_code = n.hs_code AND h.hs_ver = n.hs_ver', sql)
            self.assertIn('@hs46_missing = 0', sql)
            self.assertIn('@hs46_external_duplicates = 0', sql)
            self.assertNotIn('SET h.original_name_ko', sql)
            self.assertIn('START TRANSACTION;', sql)
            self.assertIn('COMMIT;', sql)
            self.assertIn('original_name_ko', initial.read_text(encoding='utf-8'))
            self.assertIn('AS new ON DUPLICATE KEY UPDATE', initial.read_text(encoding='utf-8'))
            self.assertNotIn('VALUES(name_ko)', initial.read_text(encoding='utf-8'))

    def test_trailing_letter_checked_against_csv_parent_names(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'master.csv'
            with path.open('w', encoding='utf-8-sig', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=seed.hb.FIELDS)
                writer.writeheader()
                for code, name in (('29', '제29류 유기화학품'), ('2936', '프로비타민과 비타민 C'), ('293627', '비타민C|기타')):
                    writer.writerow(dict(hs_code=code, depth=str(len(code)), name_ko=name,
                                         source_edition='src', hs_ver='HS2022'))
            self.assertEqual(len(seed.read_master([path])), 3)  # C는 4단위명에만 있어도 허용 (생성 단계와 같은 기준)

    def test_multiple_editions_share_one_seed_and_header(self):
        with tempfile.TemporaryDirectory() as folder:
            first = self.write_csv(folder, ['피부관리용 화장품'])
            second = Path(folder) / 'hs2017.csv'
            second.write_text(first.read_text(encoding='utf-8-sig').replace('HS2022', 'HS2017'), encoding='utf-8-sig')
            out, update = Path(folder) / 'seed.sql', Path(folder) / 'update.sql'
            result = seed.generate([first, second], out, update)
            self.assertEqual((result['rows'], result['versions']), (2, 'HS2017, HS2022'))
            self.assertTrue(update.read_text(encoding='utf-8').startswith('-- Issue #46: HS2017, HS2022'))

    def test_literals_escape_quotes_and_are_sql_mode_independent(self):
        self.assertEqual(seed.sql_text("O'Brien"), "'O''Brien'")
        self.assertEqual(seed.sql_text(None), 'NULL')
        for value in ('x\\y', 'x\ny', 'x\x00y'):
            encoded = seed.sql_text(value)
            self.assertTrue(encoded.startswith("CONVERT(X'"))
            self.assertIn(value.encode('utf-8').hex(), encoded)


if __name__ == '__main__':
    unittest.main()
