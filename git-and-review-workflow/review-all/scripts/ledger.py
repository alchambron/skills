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


def review_request(pr, login, reviewers):
    """A live direct request remains actionable even after agent analysis is complete."""
    users = {user.casefold() for user in reviewers if isinstance(user, str)} if isinstance(reviewers, list) else None
    pr["review_request"] = {"status": "unknown" if users is None or not login else
                            "requested" if login.casefold() in users else "not_requested",
                            "checked_at": now(), "head_sha": pr.get("head_sha")}


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
        for key in ("project_type", "project_label", "runtime_provenance", "review_basis"):
            if key in candidate:
                pr[key] = copy.deepcopy(candidate[key])
        pr["coverage_gaps"] = list(candidate.get("coverage", {}).get("warnings", []))
        blank_stages(pr)
        review_request(pr, data.get("login"), candidate.get("requested_reviewers"))
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


def _upsert_scenario(pr, payload):
    if not isinstance(payload, dict) or not payload.get("id"):
        raise ValueError("Scenario requires a stable id")
    existing = next((i for i, item in enumerate(pr["scenarios"]) if item["id"] == payload["id"]), None)
    if existing is None:
        pr["scenarios"].append(copy.deepcopy(payload))
    else:
        previous = pr["scenarios"][existing]
        replacement = copy.deepcopy(payload)
        if {key: value for key, value in previous.items() if key != "attempts"} == {key: value for key, value in replacement.items() if key != "attempts"}:
            replacement = previous
        else:
            replacement["attempts"] = previous.get("attempts", []) + [
                {key: value for key, value in previous.items() if key != "attempts"}]
        pr["scenarios"][existing] = replacement


def _apply_section(pr, base_sha, head_sha, section, payload):
    if section == "cleanup":
        record_cleanup(pr, base_sha, head_sha, payload)
        return
    if (pr.get("base_sha"), pr.get("head_sha")) != (base_sha, head_sha):
        raise ValueError("PR/revision mismatch; refresh and review the intended pins before recording")
    if section == "scenario":
        _upsert_scenario(pr, payload)
    elif section == "scenarios":
        if not isinstance(payload, list):
            raise ValueError("scenarios section requires a list of scenario objects")
        for item in payload:
            _upsert_scenario(pr, item)
    elif section in SECTIONS:
        pr[section] = copy.deepcopy(payload)
    else:
        raise ValueError("Unknown ledger section")
    pr["updated_at"] = now()
    # New evidence requires a new final refresh; retain previously recorded observations.
    pr.pop("current_base_sha", None)
    pr.pop("current_head_sha", None)


def record(path, repository, number, base_sha, head_sha, section, payload):
    repository_parts(repository)
    if section not in SECTIONS and section != "scenarios":
        raise ValueError("Unknown ledger section")
    with locked(path) as target:
        data = read(target)
        pr = next((item for item in data["prs"] if (item["repository"], item["number"]) == (repository, number)), None)
        if pr is None:
            raise ValueError("PR/revision mismatch; refresh and review the intended pins before recording")
        _apply_section(pr, base_sha, head_sha, section, payload)
        save(target, data)
    return data


def apply_batch(path, operations, *, base_dir=None):
    """Apply a list of checkpoint operations in one locked, atomic ledger write."""
    if not isinstance(operations, list) or not operations:
        raise ValueError("apply requires a non-empty list of operations")
    applied = 0
    with locked(path) as target:
        data = read(target)
        for op in operations:
            if not isinstance(op, dict):
                raise ValueError("Each apply operation must be an object")
            repository = op.get("repository")
            number = op.get("number")
            repository_parts(repository)
            base_sha = None if op.get("base_sha") == "unavailable" else op.get("base_sha")
            head_sha = None if op.get("head_sha") == "unavailable" else op.get("head_sha")
            pr = next((item for item in data["prs"] if (item["repository"], item["number"]) == (repository, number)), None)
            if pr is None:
                raise ValueError("PR/revision mismatch; refresh and review the intended pins before recording")
            if "sections" in op:
                sections = op["sections"]
                if not isinstance(sections, dict) or not sections:
                    raise ValueError("sections must be a non-empty object")
                for section, payload in sections.items():
                    _apply_section(pr, base_sha, head_sha, section, payload)
                    applied += 1
            else:
                section = op.get("section")
                if "data_file" in op:
                    source = Path(op["data_file"])
                    if not source.is_absolute() and base_dir is not None:
                        source = Path(base_dir) / source
                    payload = json.loads(source.read_text(encoding="utf-8"))
                elif "data" in op:
                    payload = op["data"]
                else:
                    raise ValueError("Operation requires data, data_file, or sections")
                _apply_section(pr, base_sha, head_sha, section, payload)
                applied += 1
        save(target, data)
    return data, applied


def summarize_ledger(path):
    """Return a compact per-PR status projection without dumping full findings or scenario prose."""
    data = read(path)
    rows = []
    for pr in data["prs"]:
        counts = {}
        for item in pr.get("scenarios", []):
            counts[item["status"]] = counts.get(item["status"], 0) + 1
        rows.append({
            "repository": pr["repository"],
            "number": pr["number"],
            "base_sha": pr.get("base_sha"),
            "head_sha": pr.get("head_sha"),
            "fresh": (pr.get("current_base_sha") == pr.get("base_sha")
                      and pr.get("current_head_sha") == pr.get("head_sha")),
            "review": (pr.get("review") or {}).get("status"),
            "findings": len((pr.get("review") or {}).get("findings", [])),
            "publication": (pr.get("publication") or {}).get("status"),
            "live": (pr.get("live") or {}).get("status"),
            "scenarios": counts,
            "review_request": (pr.get("review_request") or {}).get("status"),
            "pending_cleanup": len(pr.get("pending_cleanup", [])),
        })
    return {"prs": rows, "pr_count": len(rows), "collected_at": data.get("collected_at"), "resumed_at": data.get("resumed_at")}


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
                review_request(pr, data.get("login"), [user.get("login") for user in final["requested_reviewers"]]
                               if isinstance(final.get("requested_reviewers"), list) else None)
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


REVISION_GAPS = ("Revision changed; prior outcomes are archived and require revalidation",)


def previous_outcome(previous, pr, from_head):
    """The earlier run's outcome for this PR at from_head, current or archived."""
    match = next((item for item in previous["prs"]
                  if (item["repository"], item["number"]) == (pr["repository"], pr["number"])), None)
    if match is None:
        return None
    if match.get("head_sha") == from_head:
        return match
    return next((item for item in reversed(match.get("revision_history", []))
                 if item.get("head_sha") == from_head), None)


def carry(path):
    """Reuse code-identical evidence, including unchanged PRs awaiting a user decision."""
    carried, skipped = [], []
    with locked(path) as target:
        data = read(target)
        for pr in data["prs"]:
            basis = pr.get("review_basis") or {}
            if basis.get("mode") not in ("carry", "decision"):
                continue
            label = f"{pr['repository']}#{pr['number']}"
            if pr["review"]["status"] != "pending" or pr["scenarios"]:
                skipped.append({"pr": label, "reason": "stages already recorded in this run"})
                continue
            source_path = Path(basis.get("previousRun") or "") / "results.json"
            try:
                source = previous_outcome(read(source_path), pr, basis.get("fromHead"))
            except (OSError, ValueError, KeyError, TypeError) as exc:
                skipped.append({"pr": label, "reason": f"{source_path}: {exc}"})
                continue
            if source is None or source.get("review", {}).get("status") not in ("clean", "issues"):
                skipped.append({"pr": label, "reason": f"no finished review at {basis.get('fromHead')} in {source_path}"})
                continue
            decision = basis.get("mode") == "decision"
            if decision and (source.get("base_sha") != pr["base_sha"] or source.get("head_sha") != pr["head_sha"]
                             or source.get("live", {}).get("status") not in ("complete", "not_required")
                             or source.get("publication", {}).get("status") not in ("verified", "reused", "not_required", "not_requested")):
                skipped.append({"pr": label, "reason": "decision reuse requires completed evidence at the exact base and head"})
                continue
            files = basis.get("changedFiles", [])
            origin = {"run": str(source_path.parent), "head_sha": basis["fromHead"], "base_sha": source.get("base_sha"),
                      "changed_files": files}
            note = (f"Prior execution from {origin['run']} at the same base/head; your review decision is still requested"
                    if decision else f"Carried from {origin['run']} at {basis['fromHead'][:12]}: the new commits change only "
                    f"documentation ({', '.join(files[:5])}{', ...' if len(files) > 5 else ''})")
            pr["review"] = copy.deepcopy(source["review"])
            pr["review"]["limits"] = pr["review"].get("limits", []) + [note]
            publication = copy.deepcopy(source.get("publication") or {"status": "pending"})
            if decision and publication.get("status") in ("verified", "reused"):
                pr["publication"] = {"status": "reused", "review_url": publication.get("review_url"),
                                     "reused_threads": publication.get("reused_threads", []),
                                     "carried_from": publication, "new_reviews": 0}
            elif publication.get("status") in ("not_required", "not_requested"):
                pr["publication"] = publication
            else:
                # Existing threads still apply to identical code; reuse them through the publishing skill's readback.
                pr["publication"] = {"status": "pending", "carried_from": publication}
            pr["live"] = {**copy.deepcopy(source.get("live") or {"status": "pending"}),
                          "carried_from": copy.deepcopy(source.get("live", {}).get("carried_from") or origin),
                          "reused_from": origin}
            pr["scenarios"] = [{**copy.deepcopy(item), "carried_from": copy.deepcopy(item.get("carried_from") or
                                {"run": origin["run"], "head_sha": origin["head_sha"]}), "reused_from": origin}
                               for item in source.get("scenarios", [])]
            if source.get("runtime_provenance"):
                pr["runtime_provenance"] = copy.deepcopy(source["runtime_provenance"])
            gaps = [gap for gap in source.get("coverage_gaps", []) if gap not in REVISION_GAPS and gap not in pr["coverage_gaps"]]
            pr["coverage_gaps"] = pr["coverage_gaps"] + gaps + [note]
            pr["updated_at"] = now()
            pr.pop("current_base_sha", None)
            pr.pop("current_head_sha", None)
            carried.append({"pr": label, "review": pr["review"]["status"], "publication": pr["publication"]["status"],
                            "live": pr["live"]["status"], "scenarios": len(pr["scenarios"])})
        if carried:
            save(target, data)
    return {"carried": carried, "skipped": skipped}


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
    update.add_argument("--section", required=True, choices=(*SECTIONS, "scenarios"))
    update.add_argument("--data", required=True, type=Path)
    batch = commands.add_parser("apply", help="Atomically apply a batch of section/scenario updates from a JSON file")
    batch.add_argument("--input", required=True, type=Path)
    batch.add_argument("--ops", required=True, type=Path)
    proj = commands.add_parser("summary", help="Print a compact per-PR ledger status summary without dumping full text")
    proj.add_argument("--input", required=True, type=Path)
    transfer = commands.add_parser("carry", help="Copy earlier outcomes to PRs selected in carry mode")
    transfer.add_argument("--input", required=True, type=Path)
    refresh = commands.add_parser("resume", help="Refresh saved PRs and reconcile publication without posting")
    refresh.add_argument("--input", required=True, type=Path)
    refresh.add_argument("--host")
    args = parser.parse_args(argv)
    try:
        if args.command == "carry":
            print(json.dumps(carry(args.input)))
            return 0
        if args.command == "summary":
            print(json.dumps(summarize_ledger(args.input)))
            return 0
        if args.command == "apply":
            ops = json.loads(args.ops.read_text(encoding="utf-8"))
            data, applied = apply_batch(args.input, ops, base_dir=args.ops.resolve().parent)
            print(json.dumps({"prs": len(data["prs"]), "applied": applied, "command": "apply"}))
            return 0
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
