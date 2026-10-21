"""Regression checks for hidden Windows launch without caller-handle inheritance."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('local_hidden_launcher', ROOT / 'scripts/start_hidden.py')
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)


class HiddenLauncherTests(unittest.TestCase):
    def setUp(self):
        self.allowed = (ROOT / 'storage/local-adaptation/launcher-tests').resolve()
        self.allowed.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix='hidden-', dir=self.allowed)).resolve()
        self.logs = self.root / 'storage/local-runtime/test/run'
        self.logs.mkdir(parents=True)
        (self.root / 'backend').mkdir()
        (self.root / 'frontend').mkdir()
        self.command = [sys.executable, '-c', 'print("synthetic only")']
        self.root_patch = patch.object(launcher, 'ROOT', self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)

    def tearDown(self):
        # Every file here was created by this test; never remove a supplied path.
        target = self.root.resolve(strict=True)
        self.assertEqual(target.parent, self.allowed)
        self.assertNotEqual(target, self.allowed)
        self.assertTrue(target.is_relative_to(self.allowed))
        shutil.rmtree(target)

    def test_hidden_detached_child_closes_all_caller_handles(self):
        with patch.object(launcher.subprocess, 'Popen', return_value=SimpleNamespace(pid=12345)) as spawn:
            self.assertEqual(launcher.launch('backend', self.logs, self.command), 12345)
        args, kwargs = spawn.call_args
        self.assertEqual(args[0], self.command)
        self.assertEqual(kwargs['cwd'], self.root / 'backend')
        self.assertEqual(kwargs['stdin'], subprocess.DEVNULL)
        self.assertIs(kwargs['close_fds'], True)
        self.assertTrue(kwargs['creationflags'] & subprocess.CREATE_NO_WINDOW)
        self.assertTrue(kwargs['creationflags'] & subprocess.CREATE_NEW_PROCESS_GROUP)
        for stream in ('stdout', 'stderr'):
            self.assertEqual(Path(kwargs[stream].name), self.logs / f'backend.{stream}.log')
            self.assertTrue(kwargs[stream].closed)

    def test_external_or_runtime_root_log_targets_fail_before_spawn(self):
        for target in (self.root, self.root / 'storage/local-runtime'):
            with self.subTest(target=target), patch.object(launcher.subprocess, 'Popen') as spawn:
                with self.assertRaises(ValueError):
                    launcher.launch('frontend', target, self.command)
                spawn.assert_not_called()

    def test_existing_log_is_never_overwritten(self):
        existing = self.logs / 'backend.stdout.log'
        existing.write_bytes(b'existing task evidence')
        with patch.object(launcher.subprocess, 'Popen') as spawn:
            with self.assertRaises(FileExistsError):
                launcher.launch('backend', self.logs, self.command)
            spawn.assert_not_called()
        self.assertEqual(existing.read_bytes(), b'existing task evidence')

    def test_unknown_role_and_implicit_executable_rejected(self):
        with patch.object(launcher.subprocess, 'Popen') as spawn:
            with self.assertRaises(ValueError):
                launcher.launch('unknown', self.logs, self.command)
            with self.assertRaises(ValueError):
                launcher.launch('backend', self.logs, ['python', '-c', 'print(1)'])
            spawn.assert_not_called()
        self.assertEqual(list(self.logs.iterdir()), [])


if __name__ == '__main__':
    unittest.main()