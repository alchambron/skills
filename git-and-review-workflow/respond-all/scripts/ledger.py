#!/usr/bin/env python3
"""Small, locked, atomic updates to a respond-all run ledger.

Commands:
  init [--root DIR]                         create a run directory and empty ledger; print its path
  queue RUN_DIR --discovery FILE [--batch N] freeze the first N matches as the initial queue
  apply RUN_DIR [OPS_FILE|-]                apply a JSON list of operations in one atomic write
  summary RUN_DIR [--phase P] [--status S]  print slot usage and one line per PR
  progress [RUN_DIR | --latest] [--json]    show recorded progress without changing the ledger
  show RUN_DIR PR_URL                       print one PR entry as JSON

Operations (each names "run" or "pr"):
  {"run": {...}}                            shallow-set run fields
  {"pr": URL, "create": {...}}              add a PR entry from a collector match
  {"pr": URL, "set": {...}}                 shallow-set PR fields (phase/status validated)
  {"pr": URL, "action": {"key": ...}}       upsert an action by key
  {"pr": URL, "reviewer": {"login": ...}}   upsert a reviewer by login
  {"pr": URL, "comment": {"id": ...}}       upsert a comment outcome by id
  {"pr": URL, "report": {"name": "path"}}   record report paths
  {"pr": URL, "blocker": "text"}            add a blocker; {"pr": URL, "clearBlockers": true}

Large content belongs in files under the run directory; operations over 8 KB are rejected.
"""

import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import secrets
import sys

SCHEMA_VERSION = 2
PHASES = {"queued", "analysis", "fix", "publish", "respond", "monitor", "done", "blocked"}
STATUSES = {"queued", "running", "awaiting_authorization", "blocked", "completed", "skipped"}
ACTION_STATUSES = {"planned", "in_flight", "verified", "blocked"}
DEFAULT_LIMITS = {"maximumActiveChildren": 4, "maximumFixers": 3,
                  "maximumCpuHeavyLocalOperations": 1, "batchSize": 12}
OP_LIMIT = 8192


class LedgerError(Exception):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def ledger_path(run_dir):
    return Path(run_dir) / "ledger.json"


def read(run_dir):
    path = ledger_path(run_dir)
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        raise LedgerError(f"No ledger at {path}") from None
    except json.JSONDecodeError as error:
        raise LedgerError(f"Invalid ledger {path}: {error}") from None
    data.setdefault("resourceLimits", dict(DEFAULT_LIMITS))
    data.setdefault("prs", {})
    return data


def write(run_dir, data):
    """Replace ledger.json atomically; the previous file survives any failure."""
    data["updatedAt"] = now()
    path = ledger_path(run_dir)
    temporary = path.with_name(f".ledger.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    with open(temporary, "w") as handle:
        json.dump(data, handle, indent=1)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


class Locked:
    """Serialise read-modify-write cycles between the parent and children."""

    def __init__(self, run_dir):
        self.path = Path(run_dir) / "ledger.lock"

    def __enter__(self):
        self.handle = open(self.path, "a")
        fcntl.flock(self.handle, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        fcntl.flock(self.handle, fcntl.LOCK_UN)
        self.handle.close()


def new_pr(match):
    feedback = match.get("feedback") or {}
    return {
        "repository": match["repository"], "url": match["url"], "number": match.get("number"),
        "title": match.get("title"), "initialHeadSha": match.get("headSha"),
        "currentHeadSha": match.get("headSha"), "baseSha": None,
        "labels": match.get("labels", []), "labelsCollectedAt": match.get("collectedAt"),
        "triggeringFeedback": {key: feedback.get(key) for key in ("kind", "date", "url", "author")},
        "untouched": match.get("untouched", []), "signal": match.get("signal", "actionable"),
        "reviewers": [], "authorization": {}, "phase": "analysis", "status": "queued",
        "child": None, "reports": {}, "comments": [], "actions": [], "watcher": None,
        "blockers": [], "fixRounds": 0,
    }


def upsert(items, key, value):
    for item in items:
        if item.get(key) == value[key]:
            item.update(value)
            return item
    items.append(dict(value))
    return items[-1]


def validate_fields(fields):
    if "phase" in fields and fields["phase"] not in PHASES:
        raise LedgerError(f"Unknown phase {fields['phase']!r}; use one of {sorted(PHASES)}")
    if "status" in fields and fields["status"] not in STATUSES:
        raise LedgerError(f"Unknown status {fields['status']!r}; use one of {sorted(STATUSES)}")


def apply_op(data, op):
    size = len(json.dumps(op))
    if size > OP_LIMIT:
        raise LedgerError(f"Operation is {size} bytes; write the content to a file in the run "
                          "directory and record its path instead")
    if "run" in op:
        validate_fields(op["run"])
        data.update(op["run"])
        return
    url = op.get("pr")
    if not url:
        raise LedgerError(f"Operation needs 'run' or 'pr': {op}")
    if "create" in op:
        if url not in data["prs"]:
            data["prs"][url] = new_pr({**op["create"], "url": url})
        return
    entry = data["prs"].get(url)
    if entry is None:
        raise LedgerError(f"Unknown PR {url}; add it with a create operation")
    if "set" in op:
        validate_fields(op["set"])
        entry.update(op["set"])
    if "action" in op:
        action = dict(op["action"])
        if "key" not in action:
            raise LedgerError("Action needs a stable key")
        status = action.get("status", "planned")
        if status not in ACTION_STATUSES:
            raise LedgerError(f"Unknown action status {status!r}")
        stamp = now()
        current = next((a for a in entry["actions"] if a.get("key") == action["key"]), None)
        if status in ("planned", "in_flight") and not (current and current.get("intentAt")):
            action.setdefault("intentAt", stamp)
        if status in ("verified", "blocked"):
            action.setdefault("observedAt", stamp)
        upsert(entry["actions"], "key", action)
    if "reviewer" in op:
        upsert(entry["reviewers"], "login", op["reviewer"])
    if "comment" in op:
        upsert(entry["comments"], "id", op["comment"])
    if "report" in op:
        entry.setdefault("reports", {})
        if not isinstance(entry["reports"], dict):
            entry["reports"] = {"legacy": entry["reports"]}
        entry["reports"].update(op["report"])
    if "blocker" in op and op["blocker"] not in entry["blockers"]:
        entry["blockers"].append(op["blocker"])
    if op.get("clearBlockers"):
        entry["blockers"] = []


def apply(run_dir, ops):
    if isinstance(ops, dict):
        ops = [ops]
    with Locked(run_dir):
        data = read(run_dir)
        for op in ops:
            apply_op(data, op)
        write(run_dir, data)
    return data


def init(root):
    root = Path(root or Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "review-runs")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = root / f"respond-all-{stamp}-{secrets.token_hex(3)}"
    run_dir.mkdir(parents=True)
    write(run_dir, {
        "schemaVersion": SCHEMA_VERSION, "runId": run_dir.name, "createdAt": now(),
        "scope": None, "initialQueue": [], "lowSignal": [], "deferred": [],
        "coverageGaps": [], "resourceLimits": dict(DEFAULT_LIMITS),
        "localOperationOwner": None, "prs": {},
    })
    return run_dir


def queue(run_dir, discovery_path, batch):
    discovery = json.loads(Path(discovery_path).read_text())
    with Locked(run_dir):
        data = read(run_dir)
        if data.get("initialQueue"):
            raise LedgerError("Initial queue already frozen; add PRs with create operations")
        size = batch or data["resourceLimits"].get("batchSize", DEFAULT_LIMITS["batchSize"])
        data["resourceLimits"]["batchSize"] = size
        matches = discovery.get("matches", [])
        selected, deferred = matches[:size], matches[size:]
        for match in selected:
            data["prs"][match["url"]] = new_pr({**match, "collectedAt": discovery.get("collectedAt")})
        data["initialQueue"] = [match["url"] for match in selected]
        data["deferred"] = [match["url"] for match in deferred]
        data["lowSignal"] = [{"url": match["url"], "feedback": match["feedback"]["url"]}
                             for match in discovery.get("lowSignal", [])]
        data["coverageGaps"] = discovery.get("unavailable", [])
        data["scope"] = {"viewer": discovery.get("viewer"), "collectedAt": discovery.get("collectedAt"),
                         "discovery": str(Path(discovery_path).resolve())}
        write(run_dir, data)
    return data


def label(entry):
    number = entry.get("number") or entry["url"].rsplit("/", 1)[-1]
    return f"{entry.get('repository', '?')}#{number}"


def summary(data, phase=None, status=None):
    prs = list(data["prs"].values())
    running = [e for e in prs if e.get("status") == "running" and e.get("phase") != "monitor"]
    fixers = [e for e in running if e.get("phase") == "fix"]
    limits = data.get("resourceLimits", DEFAULT_LIMITS)
    counts = {}
    for entry in prs:
        key = f"{entry.get('phase')}/{entry.get('status')}"
        counts[key] = counts.get(key, 0) + 1
    lines = [
        f"run={data.get('runId')} prs={len(prs)} deferred={len(data.get('deferred', []))} "
        f"lowSignal={len(data.get('lowSignal', []))} "
        f"children={len(running)}/{limits.get('maximumActiveChildren')} "
        f"fixers={len(fixers)}/{limits.get('maximumFixers')} "
        f"heavy={data.get('localOperationOwner') or '-'}",
        "counts: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())),
    ]
    for entry in prs:
        if phase and entry.get("phase") != phase or status and entry.get("status") != status:
            continue
        child = entry.get("child") or {}
        task = child.get("taskId") if isinstance(child, dict) else child
        pending = sum(1 for a in entry.get("actions", []) if a.get("status") in ("planned", "in_flight"))
        lines.append("\t".join([
            label(entry), f"{entry.get('phase')}/{entry.get('status')}",
            (entry.get("currentHeadSha") or "-")[:8], f"child={task or '-'}",
            f"rounds={entry.get('fixRounds', 0)}", f"pendingActions={pending}",
            f"blockers={len(entry.get('blockers', []))}",
            ("watch" if entry.get("watcher") else "-"),
        ]))
    return "\n".join(lines)


PROGRESS_STATES = ("completed", "running", "waiting_authorization", "waiting_ci",
                   "queued", "blocked", "skipped")


def outstanding(entry):
    """Keep unfulfilled intent visible even when a parent marked the PR completed."""
    actions = [a for a in entry.get("actions", []) if a.get("status") in
               ("planned", "in_flight", "blocked", "draft")]
    drafts = [c for c in entry.get("comments", [])
              if c.get("publish") is not False and
              (c.get("replyStatus") == "draft" or c.get("status") == "draft" or
               c.get("replyOutcome") == "Draft" or
               ((c.get("draftReply") or c.get("replyDraft")) and
                not c.get("replyUrl") and not c.get("replyOutcome") and
                c.get("replyStatus") not in ("verified", "posted")))]
    return actions, drafts


def progress(data, run_dir=None):
    """Project only recorded ledger evidence; no API calls or inferred approvals."""
    entries = []
    counts = dict.fromkeys(PROGRESS_STATES, 0)
    for url, original in data["prs"].items():
        entry = {"url": url, **original}
        phase, status = entry.get("phase", "queued"), entry.get("status", "queued")
        actions, drafts = outstanding(entry)
        blocked_actions = [a for a in actions if a.get("status") == "blocked"]
        blockers = list(entry.get("blockers") or [])
        if entry.get("handoffReason") and entry["handoffReason"] not in blockers:
            blockers.append(entry["handoffReason"])
        blockers += [a.get("condition") or a.get("outcome") or a.get("key", "Blocked action")
                     for a in blocked_actions]
        if status == "blocked" or phase == "blocked" or blockers:
            state = "blocked"
        elif status == "awaiting_authorization":
            state = "waiting_authorization"
        elif status == "skipped" and not actions and not drafts:
            state = "skipped"
        elif phase == "monitor":
            state = "waiting_ci"
        elif status == "running":
            state = "running"
        elif (status == "completed" or phase == "done") and not actions and not drafts:
            state = "completed"
        else:
            state = "queued"
        if blockers:
            next_action = "Resolve blocker: " + str(blockers[0])
        elif state == "waiting_authorization":
            next_action = "Await approval of the response plan"
        elif actions:
            next_action = "Complete action: " + str(actions[0].get("key", "unnamed"))
        elif drafts:
            next_action = f"Publish or explicitly defer {len(drafts)} draft replies"
        else:
            next_action = {
                "completed": "No recorded workflow action remains; approval and merge are separate",
                "running": f"Await {phase} result",
                "waiting_ci": "Await CI notification or resume monitoring if stopped",
                "queued": f"Start {phase}",
                "blocked": "Record the blocker and required next action",
                "skipped": "No action in this batch",
            }.get(state, "Refresh recorded state")
        reports = entry.get("reports") or {}
        if not isinstance(reports, dict):
            reports = {"legacy": reports}
        verification = entry.get("verification", entry.get("validation"))
        if verification is None:
            verification = {"status": "not summarized in ledger", "reports": {
                k: v for k, v in reports.items()
                if k.startswith("fixRound") or k in ("verification", "validation", "outcome")}}
        publication = {
            "pushedHeadSha": entry.get("publishedHeadSha"),
            "journal": reports.get("publication"),
            "status": entry.get("publicationStatus", "readback status not summarized in ledger"),
        }
        approval = {"reviewDecision": entry.get("reviewDecision", "not recorded"),
                    "humanApproval": entry.get("humanApproval"),
                    "headSha": entry.get("approvalHeadSha")}
        ci = entry.get("ci", entry.get("checks", {"status": "not recorded"}))
        row = {"url": entry["url"], "label": label(entry), "state": state,
               "phase": phase, "status": status, "fixRounds": entry.get("fixRounds", 0),
               "headSha": entry.get("currentHeadSha") or entry.get("publishedHeadSha") or
                          entry.get("initialHeadSha"),
               "nextAction": next_action, "blockers": blockers,
               "outstandingActions": actions, "draftReplies": len(drafts),
               "verification": verification, "publication": publication, "ci": ci,
               "approval": approval}
        entries.append(row)
        counts[state] += 1
    active = [e for e in entries if e["status"] == "running" and e["phase"] != "monitor"]
    return {"runId": data.get("runId"), "runDir": str(run_dir) if run_dir else None,
            "updatedAt": data.get("updatedAt") or data.get("createdAt"),
            "source": "Recorded ledger snapshot; no live GitHub refresh",
            "batchTotal": len(entries), "counts": counts,
            "deferred": len(data.get("deferred") or []),
            "lowSignalSkipped": len(data.get("lowSignal") or []),
            "activeChildren": len(active),
            "activeFixers": sum(e["phase"] == "fix" for e in active),
            "coverageGaps": data.get("coverageGaps") or [], "entries": entries}


def progress_text(snapshot):
    counts = " ".join(f"{k}={v}" for k, v in snapshot["counts"].items())
    lines = [f"{snapshot['runId']} updated={snapshot['updatedAt'] or 'unknown'} (recorded only)",
             f"batch={snapshot['batchTotal']} {counts}",
             f"children={snapshot['activeChildren']} fixers={snapshot['activeFixers']} "
             f"deferred={snapshot['deferred']} no-findings-skipped={snapshot['lowSignalSkipped']} "
             f"coverage-gaps={len(snapshot['coverageGaps'])}"]
    for row in snapshot["entries"]:
        pub = row["publication"]
        verification = row["verification"]
        verification_status = (verification.get("status", "details recorded")
                               if isinstance(verification, dict) else "details recorded")
        if isinstance(verification, dict) and verification.get("reports"):
            verification_status = "see recorded reports"
        publication_status = "pushed" if pub["pushedHeadSha"] else "push not recorded"
        if pub["journal"]:
            publication_status += ", journal recorded"
        if pub["status"] != "readback status not summarized in ledger":
            publication_status += ", " + str(pub["status"])
        ci = row["ci"]
        ci_status = (ci.get("status", ci.get("state", "details recorded"))
                     if isinstance(ci, dict) else str(ci))
        approval = row["approval"]["reviewDecision"]
        if row["approval"]["humanApproval"] is not None:
            approval += f", humanApproval={row['approval']['humanApproval']}"
        lines.append(f"{row['label']} {row['state']} phase={row['phase']} rounds={row['fixRounds']} "
                     f"head={row['headSha'] or '-'}; next={row['nextAction']}" +
                     (f"; outstanding={len(row['outstandingActions'])} drafts={row['draftReplies']}"
                      if row['outstandingActions'] or row['draftReplies'] else "") +
                     f"; verification={verification_status}; CI={ci_status}; publication={publication_status}; "
                     f"approval={approval}")
        if len(row["blockers"]) > 1:
            lines.append("  additional blockers=" + "; ".join(str(b) for b in row["blockers"][1:]))
    return "\n".join(lines)


def latest_run(root=None):
    root = Path(root or Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "review-runs")
    candidates = []
    for path in root.glob("respond-all-*/ledger.json"):
        try:
            data = read(path.parent)
            if not isinstance(data.get("prs"), dict):
                continue
            stamp = data.get("updatedAt") or data.get("createdAt")
            updated = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            candidates.append((updated, str(path.parent), path.parent))
        except (LedgerError, OSError, ValueError, TypeError, AttributeError):
            continue
    if not candidates:
        raise LedgerError(f"No readable respond-all ledger with a timestamp under {root}")
    return max(candidates)[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("init")
    p.add_argument("--root")
    p = sub.add_parser("queue")
    p.add_argument("run_dir")
    p.add_argument("--discovery", required=True)
    p.add_argument("--batch", type=int)
    p = sub.add_parser("apply")
    p.add_argument("run_dir")
    p.add_argument("ops", nargs="?", default="-")
    p = sub.add_parser("summary")
    p.add_argument("run_dir")
    p.add_argument("--phase")
    p.add_argument("--status")
    p = sub.add_parser("show")
    p.add_argument("run_dir")
    p.add_argument("url")
    p = sub.add_parser("progress")
    p.add_argument("run_dir", nargs="?")
    p.add_argument("--latest", action="store_true")
    p.add_argument("--root", help="Run directory parent used with --latest")
    p.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.command == "init":
        print(init(args.root))
    elif args.command == "queue":
        print(summary(queue(args.run_dir, args.discovery, args.batch)))
    elif args.command == "apply":
        text = sys.stdin.read() if args.ops == "-" else Path(args.ops).read_text()
        data = apply(args.run_dir, json.loads(text))
        print(f"ok updatedAt={data['updatedAt']}")
    elif args.command == "summary":
        print(summary(read(args.run_dir), args.phase, args.status))
    elif args.command == "show":
        entry = read(args.run_dir)["prs"].get(args.url)
        if entry is None:
            raise LedgerError(f"Unknown PR {args.url}")
        print(json.dumps(entry, indent=1))
    elif args.command == "progress":
        if bool(args.run_dir) == args.latest or args.root and not args.latest:
            raise LedgerError("Use progress RUN_DIR or progress --latest [--root DIR]")
        run_dir = latest_run(args.root) if args.latest else Path(args.run_dir)
        snapshot = progress(read(run_dir), run_dir)
        print(json.dumps(snapshot, indent=1) if args.json else progress_text(snapshot))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (LedgerError, json.JSONDecodeError, OSError) as error:
        print(f"ledger error: {error}", file=sys.stderr)
        sys.exit(2)
