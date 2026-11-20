#!/usr/bin/env python3
"""Create the canonical Toronto-dated update-from-work run directory under a single flock.

Usage:
  run_dir.py [--runs-root DIR] [--date YYYY-MM-DD] [-- COMMAND [ARGS...]]

Holds a nonblocking exclusive flock on <runs-root>/update-from-work.lock
(default ~/.codex/review-runs/update-from-work.lock). Never places a lock inside
a dated subfolder. Exit 10 if another run already holds the lock.
"""
import argparse
from contextlib import contextmanager
from datetime import date, datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from zoneinfo import ZoneInfo

TORONTO = ZoneInfo('America/Toronto')
DEFAULT_RUNS_ROOT = Path.home() / '.codex' / 'review-runs'
LOCK_BUSY = 10


def toronto_date(moment=None):
    moment = moment or datetime.now(timezone.utc)
    if moment.utcoffset() is None:
        raise ValueError('timestamp must include a timezone')
    return moment.astimezone(TORONTO).date()


@contextmanager
def acquire_run_lock(runs_root=DEFAULT_RUNS_ROOT):
    root = Path(runs_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / 'update-from-work.lock'
    handle = lock_path.open('a+')
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise
    try:
        handle.seek(0)
        handle.truncate()
        handle.write(f'pid={os.getpid()}\n')
        handle.flush()
        yield lock_path
    finally:
        handle.close()


def prepare_run(runs_root=DEFAULT_RUNS_ROOT, day=None):
    root = Path(runs_root).expanduser().resolve()
    day = day or toronto_date()
    run_dir = root / f'update-from-work-{day.isoformat()}'
    attempt_dir = run_dir / f'attempt-{secrets.token_hex(4)}'
    attempt_dir.mkdir(parents=True, exist_ok=False)
    return {
        'date': day.isoformat(),
        'runs_root': str(root),
        'lock_path': str(root / 'update-from-work.lock'),
        'run_dir': str(run_dir),
        'attempt_dir': str(attempt_dir),
        'report_path': str(run_dir / 'report.md'),
    }


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    command = []
    if '--' in argv:
        split = argv.index('--')
        argv, command = argv[:split], argv[split + 1:]
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--runs-root', type=Path, default=DEFAULT_RUNS_ROOT)
    parser.add_argument('--date', type=date.fromisoformat)
    args = parser.parse_args(argv)
    try:
        with acquire_run_lock(args.runs_root):
            info = prepare_run(args.runs_root, args.date)
            print(json.dumps(info))
            if command:
                env = {**os.environ,
                       'UPDATE_FROM_WORK_RUN_DIR': info['run_dir'],
                       'UPDATE_FROM_WORK_ATTEMPT_DIR': info['attempt_dir'],
                       'UPDATE_FROM_WORK_REPORT': info['report_path']}
                return subprocess.call(command, env=env)
            return 0
    except BlockingIOError:
        print(json.dumps({'status': 'skipped', 'reason': 'overlapping update-from-work lock held'}))
        return LOCK_BUSY


if __name__ == '__main__':
    sys.exit(main())
