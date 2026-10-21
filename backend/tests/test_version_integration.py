"""Real W2 HTTP integration tests, safe alongside all existing unittest modules.

Run with the project's bound interpreter:
  python -B -m unittest discover -s backend/tests -p test_version_integration.py -v
Each case executes in an independent Python process. Discovery does not import
app/settings, modify APP_CONFIG or reuse/subclass the legacy Regression fixture.
Failed cases retain their synthetic-only database and HTTP trace for diagnosis.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

# Child cases must use the interpreter that launched this suite, not a lab-machine path.
PYTHON = Path(sys.executable).resolve()
FIXTURE = Path(__file__).with_name('version_integration_fixture.py').resolve()
TEMP_ROOT = (Path(tempfile.gettempdir()) / 'w2-version-http-integration').resolve()


class VersionHttpIntegrationTests(unittest.TestCase):
    def run_isolated(self, case):
        self.assertTrue(PYTHON.is_file(), f'Missing project interpreter: {PYTHON}')
        TEMP_ROOT.mkdir(parents=True, exist_ok=True)
        case_root = Path(tempfile.mkdtemp(prefix=case.removeprefix('test_') + '-', dir=TEMP_ROOT)).resolve()
        self.assertNotEqual(case_root, TEMP_ROOT)
        self.assertTrue(case_root.is_relative_to(TEMP_ROOT))
        # Do not pass ambient APP_CONFIG/provider/model/proxy/credential variables
        # from a developer session (or another unittest module) into the child.
        env = {key: os.environ[key] for key in ('SYSTEMROOT', 'WINDIR', 'COMSPEC') if key in os.environ}
        env.update(TEMP=str(case_root), TMP=str(case_root), HF_HUB_OFFLINE='1',
                   TRANSFORMERS_OFFLINE='1', HF_DATASETS_OFFLINE='1')
        command = [str(PYTHON), '-I', '-B', str(FIXTURE), '--case', case, '--case-root', str(case_root)]
        try:
            completed = subprocess.run(command, cwd=case_root, env=env,
                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=75)
        except subprocess.TimeoutExpired as error:
            self.fail(f'Isolated case timed out (child terminated); fixture retained: {case_root}\n{error}')
        output = completed.stdout + completed.stderr
        records = [line.removeprefix('W2_RESULT=') for line in completed.stdout.splitlines()
                   if line.startswith('W2_RESULT=')]
        if completed.returncode or len(records) != 1:
            (case_root / 'result.txt').write_text(output, encoding='utf-8')
            self.fail(f'W2 child exit={completed.returncode}; evidence={case_root}\n{output}')
        summary = json.loads(records[0])
        self.assertEqual(summary, {'case': case, 'tests': 1, 'failures': 0,
                                  'errors': 0, 'skipped': 0, 'success': True}, output)
        # Recursive removal is permitted ONLY for this exact task-owned, non-root
        # child directory, never for a computed project/data path or TEMP_ROOT.
        target = case_root.resolve(strict=True)
        allowed = TEMP_ROOT.resolve(strict=True)
        self.assertEqual(target, case_root)
        self.assertEqual(target.parent, allowed)
        self.assertNotEqual(target, allowed)
        self.assertNotEqual(allowed.parent, allowed)
        self.assertTrue(target.is_relative_to(allowed))
        shutil.rmtree(target)

    def test_upload_draft_publish_http_chain(self):
        self.run_isolated(self._testMethodName)

    def test_capabilities_reject_unknown_migration(self):
        self.run_isolated(self._testMethodName)

    def test_ordinary_owner_version_lifecycle_and_shared_roles_read_only(self):
        self.run_isolated(self._testMethodName)

    def test_model_failover_stops_after_version_withdrawal(self):
        self.run_isolated(self._testMethodName)


if __name__ == '__main__':
    unittest.main(verbosity=2)
