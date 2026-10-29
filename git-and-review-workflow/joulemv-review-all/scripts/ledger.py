#!/usr/bin/env python3
"""Checkpoint and resume a review run. GitHub access is read-only."""
import argparse
import copy
from contextlib import contextmanager
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import urlsplit

from collect_reviews import CollectionError, GitHub, repository_parts
from render_results import cleanup_status, publication_link_matches_pr, validate

SHA = re.compile(r"[0-9a-fA-F]{40}")
SECTIONS = ("review", "publication", "live", "scenario", "coverage_gaps", "cleanup")
CLEANUP_KEYS = ("database_dropped", "servers_stopped", "worktree_removed")


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


@contextmanager
def locked(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield path


def save(path, data):
    """Validate before replacing the last durable checkpoint."""
    validate(data)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            full_sync = getattr(fcntl, "F_FULLFSYNC", None)
            if full_sync is not None:
                fcntl.fcntl(handle.fileno(), full_sync)
            else:
                os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def read(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    validate(data)
    return data


def blank_stages(pr):
    pr["review"] = {"status": "pending" if pr.get("base_sha") and pr.get("head_sha") else "blocked",
                    "findings": [], "standards": [], "spec": [], "limits": []}
    pr["publication"] = {"status": "pending"}
    pr["live"] = {"status": "pending"}
    pr["scenarios"] = []


def initialize(snapshot_path, output):
    snapshot = json.loads(Path(snapshot_path).read_text(encoding="utf-8"))
    data = {key: copy.deepcopy(snapshot[key]) for key in ("version", "login", "collected_at")}
    data["host"] = snapshot.get("host", "github.com")
    data["collection"] = copy.deepcopy(snapshot.get("collection", {}))
    data["prs"] = []
    for candidate in snapshot["prs"]:
        pr = {key: copy.deepcopy(candidate.get(key)) for key in
              ("repository", "number", "url", "title", "base_sha", "head_sha")}
        if not pr["repository"] or not pr["number"]:
            match = re.fullmatch(r"/([^/]+/[^/]+)/pull/([1-9][0-9]*)/?", urlsplit(pr["url"] or "").path)
            if not match:
                raise ValueError("Candidate has no recoverable PR identity; retain snapshot and repair its identity before init")
            pr["repository"], pr["number"] = match[1], int(match[2])
        repository_parts(pr["repository"])
        pr["title"] = pr["title"] or "Unavailable PR title"
        for key in ("project_type", "project_label", "runtime_provenance"):
            if key in candidate:
                pr[key] = copy.deepcopy(candidate[key])
        pr["coverage_gaps"] = list(candidate.get("coverage", {}).get("warnings", []))
        blank_stages(pr)
        if candidate.get("draft") or candidate.get("collection_status") == "failed":
            pr["review"]["status"] = "blocked"
            pr["coverage_gaps"].append("Draft or unavailable collection; inspect snapshot before reviewing")
        # Collection-time current SHAs do not attest to a final refresh after review.
        data["prs"].append(pr)
    with locked(output) as path:
        if path.exists():
            raise FileExistsError("Ledger exists; use record or resume instead of overwriting it")
        save(path, data)
    return data


def record(path, repository, number, base_sha, head_sha, section, payload):
    repository_parts(repository)
    if section not in SECTIONS:
        raise ValueError("Unknown ledger section")
    with locked(path) as target:
        data = read(target)
        pr = next((item for item in data["prs"] if (item["repository"], item["number"]) == (repository, number)), None)
        if pr is None:
            raise ValueError("PR/revision mismatch; refresh and review the intended pins before recording")
        if section == "cleanup":
            record_cleanup(pr, base_sha, head_sha, payload)
            save(target, data)
            return data
        if (pr.get("base_sha"), pr.get("head_sha")) != (base_sha, head_sha):
            raise ValueError("PR/revision mismatch; refresh and review the intended pins before recording")
        if section == "scenario":
            if not isinstance(payload, dict) or not payload.get("id"):
                raise ValueError("Scenario requires a stable id")
            existing = next((i for i, item in enumerate(pr["scenarios"]) if item["id"] == payload["id"]), None)
            if existing is None:
                pr["scenarios"].append(payload)
            else:
                previous = pr["scenarios"][existing]
                replacement = copy.deepcopy(payload)
                if {key: value for key, value in previous.items() if key != "attempts"} == {key: value for key, value in replacement.items() if key != "attempts"}:
                    replacement = previous
                else:
                    replacement["attempts"] = previous.get("attempts", []) + [
                        {key: value for key, value in previous.items() if key != "attempts"}]
                pr["scenarios"][existing] = replacement
        else:
            pr[section] = copy.deepcopy(payload)
        pr["updated_at"] = now()
        # New evidence requires a new final refresh; retain previously recorded observations.
        pr.pop("current_base_sha", None)
        pr.pop("current_head_sha", None)
        save(target, data)
    return data


def record_cleanup(pr, base_sha, head_sha, payload):
    """Attach observed cleanup to an archived runtime without erasing its receipt."""
    if not isinstance(payload, dict) or any(key not in payload or
            (payload[key] is not None and not isinstance(payload[key], bool)) for key in CLEANUP_KEYS):
        raise ValueError("Cleanup requires database_dropped, servers_stopped, worktree_removed as booleans or null")
    matching = [item for item in pr.get("revision_history", [])
                if (item.get("base_sha"), item.get("head_sha")) == (base_sha, head_sha)]
    if not matching:
        raise ValueError("Cleanup pins do not identify an archived revision")
    for item in matching:
        item.setdefault("live", {})["cleanup"] = copy.deepcopy(payload)
        item["cleanup_recorded_at"] = now()
    pending = []
    for item in pr.get("pending_cleanup", []):
        if (item.get("base_sha"), item.get("head_sha")) == (base_sha, head_sha):
            item["live"]["cleanup"] = copy.deepcopy(payload)
            item["cleanup_recorded_at"] = now()
            if cleanup_status(item["live"]) == "complete":
                continue
        pending.append(item)
    pr["pending_cleanup"] = pending
    if not pending:
        pr["coverage_gaps"] = [gap for gap in pr.get("coverage_gaps", []) if gap !=
                               "Previous runtime cleanup remains outstanding; see pending_cleanup receipts"]


def require_pr(api, pr):
    repository_parts(pr["repository"])
    value = api.get(f"repos/{pr['repository']}/pulls/{pr['number']}")
    if not isinstance(value, dict):
        raise CollectionError("PR refresh returned malformed metadata")
    for side in ("base", "head"):
        details = value.get(side)
        pin = details.get("sha") if isinstance(details, dict) else None
        if not isinstance(pin, str) or not SHA.fullmatch(pin):
            raise CollectionError("PR refresh returned incomplete revision metadata")
    if value.get("state") not in ("open", "closed"):
        raise CollectionError("PR refresh returned no state")
    return value


def invalidate(pr, current):
    pins = (current["base"]["sha"], current["head"]["sha"])
    if pins == (pr.get("base_sha"), pr.get("head_sha")):
        return
    old = {key: copy.deepcopy(pr.get(key)) for key in
           ("base_sha", "head_sha", "review", "publication", "live", "scenarios", "coverage_gaps", "runtime_provenance")}
    old["archived_at"] = now()
    pr.setdefault("revision_history", []).append(old)
    live = old.get("live") or {}
    if (live.get("runtime_commit") or live.get("environment") or live.get("cleanup")) and cleanup_status(live) == "pending":
        pr.setdefault("pending_cleanup", []).append({"base_sha": old["base_sha"], "head_sha": old["head_sha"], "live": live})
    pr.pop("runtime_provenance", None)
    pr["base_sha"], pr["head_sha"] = pins
    blank_stages(pr)
    pr["coverage_gaps"] = ["Revision changed; prior outcomes are archived and require revalidation"]
    if pr.get("pending_cleanup"):
        pr["coverage_gaps"].append("Previous runtime cleanup remains outstanding; see pending_cleanup receipts")


def reconcile_publication(api, pr):
    publication = pr.get("publication", {})
    review_url = publication.get("review_url")
    references = ([review_url] if review_url else []) + publication.get("reused_threads", [])
    if not references:
        if publication.get("status") in ("verified", "reused"):
            raise CollectionError("Saved publication has no reference to reconcile; inspect GitHub before retrying")
        return
    for reference in references:
        if not publication_link_matches_pr(reference, pr):
            raise CollectionError("Saved publication URL does not match this PR")
        fragment = urlsplit(reference).fragment
        if fragment.startswith("pullrequestreview-"):
            identifier = fragment.removeprefix("pullrequestreview-")
            value = api.get(f"repos/{pr['repository']}/pulls/{pr['number']}/reviews/{identifier}")
            matches = isinstance(value, dict) and str(value.get("id")) == identifier and value.get("state") == "CHANGES_REQUESTED" and value.get("commit_id") == pr["head_sha"]
        else:
            identifier = re.sub(r"^(discussion_r|r)", "", fragment)
            value = api.get(f"repos/{pr['repository']}/pulls/comments/{identifier}")
            target = urlsplit(value.get("pull_request_url", "")).path if isinstance(value, dict) else ""
            expected = f"/repos/{pr['repository']}/pulls/{pr['number']}"
            # Reuse equivalence was decided by the coordinator. An older or outdated
            # thread remains reusable; this read only checks the saved identity.
            original = value.get("original_commit_id") if isinstance(value, dict) else None
            revision_matches = isinstance(original, str) and bool(SHA.fullmatch(original)) and (
                reference in publication.get("reused_threads", []) or original == pr["head_sha"])
            matches = isinstance(value, dict) and str(value.get("id")) == identifier and revision_matches and target in (expected, "/api/v3" + expected)
        if not matches:
            raise CollectionError("Saved publication no longer verifies for this revision; inspect GitHub before retrying")
    publication["reconciliation"] = {"status": "matched", "checked_at": now()}
    if publication.get("status") in ("verified", "reused"):
        publication.pop("error", None)
    elif publication.get("status") in ("pending", "failed"):
        publication["error"] = "Saved publication found; complete publisher readback before recording verified/reused"


def resume(path, api=None, host=None):
    with locked(path) as target:
        data = read(target)
        selected_host = host or data.get("host", "github.com")
        api = api or GitHub(selected_host)
        for pr in data["prs"]:
            pr.pop("current_base_sha", None)
            pr.pop("current_head_sha", None)
            pr.pop("resume_error", None)
            try:
                parsed = urlsplit(pr["url"])
                if parsed.scheme != "https" or parsed.hostname != selected_host or parsed.username or parsed.password or parsed.port not in (None, 443) or parsed.path.rstrip("/") != f"/{pr['repository']}/pull/{pr['number']}":
                    raise CollectionError("Ledger PR URL does not match its identity and selected GitHub host")
                current = require_pr(api, pr)
                invalidate(pr, current)
                try:
                    reconcile_publication(api, pr)
                except (CollectionError, ValueError, TypeError) as exc:
                    pr.setdefault("publication", {})["status"] = "failed"
                    pr["publication"]["error"] = str(exc)
                    pr["publication"]["reconciliation"] = {"status": "unverified", "checked_at": now()}
                final = require_pr(api, pr)
                invalidate(pr, final)
                pr["current_base_sha"] = final["base"]["sha"]
                pr["current_head_sha"] = final["head"]["sha"]
                pr["refreshed_at"] = now()
                pr["pr_state"] = final["state"]
                if final["state"] != "open":
                    pr["review"] = {**pr["review"], "status": "blocked"}
                    if "PR is closed; no further review publication" not in pr["coverage_gaps"]:
                        pr["coverage_gaps"].append("PR is closed; no further review publication")
            except (CollectionError, ValueError, TypeError) as exc:
                pr["resume_error"] = str(exc)
                if pr.get("publication", {}).get("status") in ("verified", "reused"):
                    pr["publication"]["status"] = "failed"
                    pr["publication"]["error"] = "Revision refresh failed; inspect saved references before retrying publication"
            save(target, data)
        data["resumed_at"] = now()
        save(target, data)
    return data


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Create a ledger from a collected snapshot")
    init.add_argument("--snapshot", required=True, type=Path)
    init.add_argument("--output", required=True, type=Path)
    update = commands.add_parser("record", help="Atomically record one section or upsert a scenario")
    update.add_argument("--input", required=True, type=Path)
    update.add_argument("--repository", required=True)
    update.add_argument("--number", required=True, type=int)
    update.add_argument("--base-sha", required=True)
    update.add_argument("--head-sha", required=True)
    update.add_argument("--section", required=True, choices=SECTIONS)
    update.add_argument("--data", required=True, type=Path)
    refresh = commands.add_parser("resume", help="Refresh saved PRs and reconcile publication without posting")
    refresh.add_argument("--input", required=True, type=Path)
    refresh.add_argument("--host")
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            data = initialize(args.snapshot, args.output)
        elif args.command == "record":
            data = record(args.input, args.repository, args.number,
                          None if args.base_sha == "unavailable" else args.base_sha,
                          None if args.head_sha == "unavailable" else args.head_sha,
                          args.section, json.loads(args.data.read_text(encoding="utf-8")))
        else:
            data = resume(args.input, host=args.host)
        failures = sum(bool(pr.get("resume_error")) or pr.get("publication", {}).get("status") == "failed" for pr in data["prs"])
        print(json.dumps({"prs": len(data["prs"]), "unresolved": failures, "command": args.command}))
        return 2 if args.command == "resume" and failures else 0
    except (OSError, ValueError, KeyError, TypeError, CollectionError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
