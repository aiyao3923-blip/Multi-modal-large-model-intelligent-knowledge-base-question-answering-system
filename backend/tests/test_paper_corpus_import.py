"""Only synthetic databases; never imports the live app configuration."""
import importlib.util
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

path = Path(__file__).resolve().parents[1] / 'scripts/import_paper_corpus.py'
spec = importlib.util.spec_from_file_location('paper_import_test_target', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class PaperImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / 'fixture.db'
        with closing(sqlite3.connect(self.db)) as con, con:
            for table in module.PROTECTED:
                con.execute(f'CREATE TABLE "{table}" (id INTEGER PRIMARY KEY, content TEXT)')
                con.execute(f'INSERT INTO "{table}" VALUES (1, ?)', (table + '原始资料',))
            con.execute('CREATE TABLE users(id INTEGER PRIMARY KEY,password_digest TEXT)')
            con.execute("INSERT INTO users VALUES(1,'synthetic-auth-must-not-export')")
            con.execute('CREATE TABLE llm_channels(id INTEGER PRIMARY KEY,api_key TEXT)')
            con.execute("INSERT INTO llm_channels VALUES(1,'synthetic-provider-must-not-export')")

    def test_data_snapshot_excludes_auth_and_provider_material(self):
        data, hashes = module.business_snapshot(self.db)
        encoded = json.dumps(data, ensure_ascii=False)
        self.assertNotIn('synthetic-auth', encoded)
        self.assertNotIn('synthetic-provider', encoded)
        self.assertEqual(set(data['tables']), set(module.PROTECTED))
        self.assertTrue(data['not_a_full_database_backup'])
        self.assertEqual(hashes, module.row_snapshot(self.db))
        with closing(sqlite3.connect(self.db)) as con, con:
            self.assertEqual(con.execute('select api_key from llm_channels').fetchone()[0], 'synthetic-provider-must-not-export')

    def test_preservation_detects_original_change_but_allows_new_rows(self):
        _, baseline = module.business_snapshot(self.db)
        with closing(sqlite3.connect(self.db)) as con, con:
            con.execute("INSERT INTO documents VALUES(2,'new authorized paper')")
        self.assertEqual(module.row_snapshot(self.db, baseline), baseline)
        with closing(sqlite3.connect(self.db)) as con, con:
            con.execute("UPDATE chunks SET content='unexpected edit' WHERE id=1")
        self.assertNotEqual(module.row_snapshot(self.db, baseline), baseline)

    def test_receipt_is_complete_json_and_no_leftover_temp(self):
        receipt = self.root / 'receipt.json'
        module.atomic_json(receipt, {'state':'started','document':'P010'})
        module.atomic_json(receipt, {'state':'verified','documents':69})
        self.assertEqual(json.loads(receipt.read_text()), {'state':'verified','documents':69})
        self.assertEqual(list(self.root.glob('*.tmp')), [])

    def test_missing_original_row_is_detected(self):
        _, baseline = module.business_snapshot(self.db)
        with closing(sqlite3.connect(self.db)) as con, con:
            con.execute('DELETE FROM documents WHERE id=1')
        self.assertNotEqual(module.row_snapshot(self.db, baseline), baseline)

if __name__ == '__main__':
    unittest.main()