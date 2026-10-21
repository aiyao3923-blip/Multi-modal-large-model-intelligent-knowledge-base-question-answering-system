"""Run all backend unittest modules and clean only this process's legacy fixture."""
from __future__ import annotations
import argparse
import json
import shutil
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--pattern', default='test_*.py')
    parser.add_argument('--output', type=Path, help='New report path within this workspace; existing reports are never overwritten')
    args = parser.parse_args()
    output = args.output.resolve() if args.output else None
    if output and (not output.is_relative_to(ROOT) or output == ROOT or output.exists()):
        parser.error('Output must be a new file inside the current workspace')
    started = time.perf_counter()
    suite = unittest.defaultTestLoader.discover(str(ROOT / 'backend' / 'tests'), pattern=args.pattern)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    cleanup_errors = []
    legacy = sys.modules.get('test_regression')
    if legacy is not None and hasattr(legacy, 'TEMP'):
        target = Path(legacy.TEMP).resolve()
        allowed = (ROOT / 'storage' / 'refactor-validation' / 'test-runs').resolve()
        try:
            if not target.is_relative_to(allowed) or target == allowed:
                raise ValueError('Legacy test temporary path escaped its known workspace')
            if target.exists():
                shutil.rmtree(target)
        except (OSError, ValueError) as error:
            cleanup_errors.append(str(error))
    report = {'success': result.wasSuccessful() and not cleanup_errors, 'tests_passed': result.wasSuccessful(), 'tests': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors), 'skipped': len(result.skipped), 'duration_seconds': round(time.perf_counter()-started, 3), 'cleanup_errors': cleanup_errors, 'scope': 'isolated backend tests; not live database migration, model capability or complete product acceptance'}
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open('x', encoding='utf-8') as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report['success'] else 1

if __name__ == '__main__':
    raise SystemExit(main())
