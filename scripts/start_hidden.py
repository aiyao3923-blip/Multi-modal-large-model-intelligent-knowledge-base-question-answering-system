"""Internal Windows launcher: hidden children with no inherited caller pipe handles.

PowerShell Start-Process can retain a caller's redirected pipes in background
children. close_fds=True keeps CLI/IDE capture from waiting for the service to
stop. Only task-owned new log files under storage/local-runtime are opened.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def launch(role: str, log_dir: Path, command: list[str]) -> int:
    if sys.platform != 'win32':
        raise ValueError('This local launcher requires Windows.')
    if role not in ('backend', 'frontend'):
        raise ValueError('Unknown service role.')
    allowed = (ROOT / 'storage/local-runtime').resolve()
    target = log_dir.resolve(strict=True)
    if not allowed.is_relative_to(ROOT) or not target.is_relative_to(allowed) or target == allowed:
        raise ValueError('Log directory must be a child of this project runtime directory.')
    if not command or not Path(command[0]).is_absolute() or not Path(command[0]).is_file():
        raise ValueError('An explicit existing executable is required.')
    with (target / f'{role}.stdout.log').open('xb') as stdout, (target / f'{role}.stderr.log').open('xb') as stderr:
        process = subprocess.Popen(
            command, cwd=ROOT / role, stdin=subprocess.DEVNULL,
            stdout=stdout, stderr=stderr, close_fds=True,
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    return process.pid


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--role', choices=('backend', 'frontend'), required=True)
    parser.add_argument('--log-dir', type=Path, required=True)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    try:
        process_id = launch(args.role, args.log_dir, command)
    except (OSError, ValueError):
        # Do not echo commands/environment; custom arguments may contain private data.
        print('Hidden service launch failed; check the executable and new runtime log directory.', file=sys.stderr)
        return 1
    print(json.dumps({'pid': process_id}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())