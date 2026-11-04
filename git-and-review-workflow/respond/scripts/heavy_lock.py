#!/usr/bin/env python3
"""Run one CPU-heavy command (build, test, install, runtime start) under a machine-wide slot.

Usage: heavy_lock.py [--slots N] [--label TEXT] -- COMMAND [ARGS...]

Parallel fixers and review children share $REVIEW_STATE_ROOT/heavy (default ~/.codex/review-state/heavy),
so at most N heavy commands run at once across every thread. Waiting time goes to stderr; the exit code
is the command's own.
"""

import argparse
import fcntl
import os
from pathlib import Path
import subprocess
import sys
import time


def slot_dir():
    root = os.environ.get("REVIEW_STATE_ROOT") or Path.home() / ".codex" / "review-state"
    return Path(root) / "heavy"


def acquire(slots, poll=1.0):
    directory = slot_dir()
    directory.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    while True:
        for index in range(slots):
            handle = open(directory / f"slot-{index}.lock", "a+")
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                handle.close()
                continue
            return handle, index, time.monotonic() - started
        time.sleep(poll)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--" not in argv:
        print("usage: heavy_lock.py [--slots N] [--label TEXT] -- COMMAND [ARGS...]", file=sys.stderr)
        return 2
    split = argv.index("--")
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--slots", type=int, default=int(os.environ.get("REVIEW_HEAVY_SLOTS", "1")))
    parser.add_argument("--label", default="")
    args = parser.parse_args(argv[:split])
    command = argv[split + 1:]
    if not command or args.slots < 1:
        print("heavy_lock.py needs a command and --slots >= 1", file=sys.stderr)
        return 2
    handle, index, waited = acquire(args.slots)
    try:
        handle.seek(0)
        handle.truncate()
        handle.write(f"pid={os.getpid()} label={args.label} cwd={os.getcwd()}\n")
        handle.flush()
        if waited >= 1:
            print(f"heavy_lock: waited {waited:.0f}s for slot {index}", file=sys.stderr)
        return subprocess.call(command)
    finally:
        handle.close()


if __name__ == "__main__":
    sys.exit(main())
