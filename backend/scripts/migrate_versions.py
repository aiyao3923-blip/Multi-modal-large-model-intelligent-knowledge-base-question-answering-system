"""Explicit version migration CLI: dry-run by default, never an implicit startup action."""
from __future__ import annotations
import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from app.db.migrations import MigrationError, SensitiveDatabaseError, migrate_database

def main() -> int:
    parser = argparse.ArgumentParser(description='Preview or explicitly execute the version-foundation migration on an authorized target. No database backup or credential copy is performed.')
    parser.add_argument('--database', required=True, type=Path)
    parser.add_argument('--file-base', required=True, type=Path, help='Base used by existing relative document storage_path values')
    parser.add_argument('--upload-root', required=True, type=Path)
    parser.add_argument('--allowed-root', required=True, type=Path, help='Exact authorized workspace/target directory, not a drive root')
    parser.add_argument('--execute', action='store_true', help='Write only when a fresh exact plan digest matches')
    parser.add_argument('--expected-plan-digest')
    parser.add_argument('--sensitive-approval-reference', help='Reference to a real, separately approved no-secret-copy recovery decision. Never generate this as a substitute for user approval.')
    args = parser.parse_args()
    for field in ('database', 'file_base', 'upload_root', 'allowed_root'):
        if not getattr(args, field).is_absolute():
            parser.error('--' + field.replace('_', '-') + ' must be an absolute path')
    try:
        result = migrate_database(args.database, file_base=args.file_base, upload_root=args.upload_root, allowed_root=args.allowed_root, execute=args.execute, expected_plan_digest=args.expected_plan_digest, sensitive_approval_reference=args.sensitive_approval_reference)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (MigrationError, OSError, ValueError, sqlite3.Error) as error:
        print(json.dumps({'status': 'blocked' if isinstance(error, SensitiveDatabaseError) else 'failed', 'error': str(error), 'migration_completed': False}, ensure_ascii=False))
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
