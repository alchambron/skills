#!/usr/bin/env python3
"""Cross-run feedback history for respond-all: skip feedback an earlier run already handled.

Commands:
  filter --discovery FILE --out FILE   drop untouched items earlier runs answered or explicitly skipped
  record RUN_DIR [RUN_DIR ...]         save each settled entry's handled feedback
  status                               print a history summary

Offline: reads collector output and run ledgers only. History lives in $RESPOND_STATE_DIR
(default ~/.codex/review-state/respond-all).
"""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import secrets
import sys

import ledger


def stamp():
    return datetime.now(timezone.utc).isoformat()


def state_dir():
    return Path(os.environ.get("RESPOND_STATE_DIR") or Path.home() / ".codex" / "review-state" / "respond-all")


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    temporary.write_text(json.dumps(value, indent=1) + "\n")
    temporary.replace(path)


def load_json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return default


@contextmanager
def locked():
    directory = state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    with open(directory / "state.lock", "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield directory


def read_state(directory):
    data = load_json(directory / "state.json", {"version": 1, "prs": {}})
    data.setdefault("prs", {})
    return data


def item_key(item):
    return f"{item['url']}@{item['date']}"


def filter_discovery(discovery, state):
    """Keep matches with at least one untouched item no earlier run handled; edits produce new keys."""
    kept, dropped = [], []
    for match in discovery.get("matches", []):
        handled = set(state["prs"].get(match["url"], {}).get("handled", []))
        remaining = [item for item in match.get("untouched", []) if item_key(item) not in handled]
        if remaining:
            kept.append({**match, "untouched": remaining})
        else:
            dropped.append({"url": match["url"], "reason": "already_handled"})
    return {**discovery, "matches": kept, "excluded": discovery.get("excluded", []) + dropped}, dropped


def settled(entry):
    """True when the run finished with the feedback: answered, or explicitly skipped."""
    status, phase = entry.get("status"), entry.get("phase")
    if status == "skipped":
        return True
    if status == "blocked" or phase == "blocked":
        return False
    actions, drafts = ledger.outstanding(entry)
    return (status == "completed" or phase in ("done", "monitor")) and not actions and not drafts


def record(run_dirs):
    changes = []
    with locked() as directory:
        state = read_state(directory)
        for run_dir in run_dirs:
            run_dir = str(Path(run_dir).resolve())
            data = ledger.read(run_dir)
            created = data.get("createdAt") or ""
            for url, entry in data["prs"].items():
                current = state["prs"].setdefault(url, {"handled": []})
                if created < (current.get("runCreatedAt") or ""):
                    # A newer run already recorded this PR; an older ledger must not roll it back.
                    changes.append({"url": url, "outcome": "superseded"})
                    continue
                items = {item_key(item) for item in entry.get("untouched", []) if item.get("url") and item.get("date")}
                handled = set(current.get("handled", []))
                outcome = "handled" if settled(entry) else entry.get("status") or "unfinished"
                if outcome == "handled":
                    handled |= items
                current.update({"runDir": run_dir, "runCreatedAt": created, "recordedAt": stamp(),
                                "head": entry.get("currentHeadSha") or entry.get("publishedHeadSha")
                                or entry.get("initialHeadSha"),
                                "outcome": outcome, "handled": sorted(handled)[-200:]})
                changes.append({"url": url, "outcome": outcome})
        state["updatedAt"] = stamp()
        save_json(directory / "state.json", state)
    return changes


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("filter")
    p.add_argument("--discovery", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    p = sub.add_parser("record")
    p.add_argument("run_dirs", nargs="+")
    sub.add_parser("status")
    args = parser.parse_args(argv)
    try:
        if args.command == "filter":
            with locked() as directory:
                state = read_state(directory)
            filtered, dropped = filter_discovery(json.loads(args.discovery.read_text()), state)
            save_json(args.out, filtered)
            print(json.dumps({"out": str(args.out), "matches": len(filtered["matches"]),
                              "alreadyHandled": [item["url"] for item in dropped]}))
            return 0
        if args.command == "record":
            counts = {}
            for change in record(args.run_dirs):
                counts[change["outcome"]] = counts.get(change["outcome"], 0) + 1
            print(json.dumps({"recorded": sum(counts.values()), "outcomes": counts}))
            return 0
        state = read_state(state_dir())
        print(json.dumps({"prs": len(state["prs"]), "updatedAt": state.get("updatedAt")}))
        return 0
    except (ledger.LedgerError, OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
