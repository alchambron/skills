#!/usr/bin/env python3
"""Cross-run review history for review-all: select only PRs that still need work.

Commands:
  select [--all] [--repo R] [--output FILE]   search current review requests and write the selection
  record --results RUN_DIR/results.json       save each PR's reviewed head, outcome and live status
  status                                      print a history summary

Exit codes: 0 PRs selected or command done, 10 nothing new to review, 2 error. GitHub access is read-only.
History lives in $REVIEW_STATE_DIR (default ~/.codex/review-state/review-all).
"""

import argparse
from contextlib import contextmanager
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import secrets
import sys

from collect_reviews import CollectionError, GitHub, candidate_identity, repository_parts, search_requests
from check_dependencies import require_dependencies

NOTHING_NEW = 10
DONE_REVIEW = {"clean", "issues"}
DONE_PUBLICATION = {"verified", "reused", "not_required", "not_requested"}
DONE_LIVE = {"complete", "not_required"}
PRIORITY = {"moved": 0, "rerequested": 0, "retry": 1, "new": 2}
# A moved head whose new commits touch only these paths runs the code already reviewed and tested.
DOC_SUFFIXES = (".md", ".mdx", ".rst", ".adoc")
RUNTIME_DIRS = {"src", "public", "static", "assets", "resources", "templates"}
COMPARE_FILE_LIMIT = 300


def stamp():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def state_dir():
    return Path(os.environ.get("REVIEW_STATE_DIR") or Path.home() / ".codex" / "review-state" / "review-all")


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    temporary.write_text(json.dumps(value, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
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


def key_of(repository, number):
    return f"{repository}#{number}"


def classify(entry, head, force):
    """Return (kind, basis); kind is new/moved/rerequested/retry/unchanged (no basis = skip)."""
    if force or not entry or not entry.get("reviewedHead"):
        return "new", {"mode": "full"}
    if entry["reviewedHead"] != head:
        return "moved", {"mode": "incremental", "fromHead": entry["reviewedHead"],
                         "fromBase": entry.get("reviewedBase"), "previousOutcome": entry.get("outcome"),
                         "previousRun": entry.get("runDir")}
    if entry.get("complete") and entry.get("outcome") == "issues" and entry.get("publication") == "verified":
        # Our submitted Request changes cleared the request; a current request means the author asked again.
        return "rerequested", {"mode": "discussion", "previousOutcome": "issues", "previousRun": entry.get("runDir")}
    if entry.get("complete"):
        return "unchanged", None
    return "retry", {"mode": "retry", "previousOutcome": entry.get("outcome"), "previousRun": entry.get("runDir")}


def documentation_only(paths):
    """True when every path is documentation that no build or runtime reads."""
    def documentation(path):
        parts = path.split("/")
        if parts[0] == "docs":
            return True
        return path.lower().endswith(DOC_SUFFIXES) and not RUNTIME_DIRS.intersection(parts[:-1])
    return bool(paths) and all(documentation(path) for path in paths)


def changed_paths(api, name, from_head, head):
    """Files changed from from_head to head, or None when head does not simply extend from_head."""
    value = api.get(f"repos/{name}/compare/{from_head}...{head}")
    if not isinstance(value, dict) or value.get("status") != "ahead":
        return None
    files = value.get("files")
    if not isinstance(files, list) or len(files) >= COMPARE_FILE_LIMIT:
        return None
    paths = []
    for item in files:
        paths.append(item["filename"])
        if item.get("previous_filename"):
            paths.append(item["previous_filename"])
    return paths


def carry_basis(api, name, entry, head, basis, warnings):
    """Turn an incremental basis into carry when the new commits change only documentation."""
    if not entry.get("complete"):
        return basis
    try:
        paths = changed_paths(api, name, basis["fromHead"], head)
    except (CollectionError, KeyError, TypeError, ValueError) as exc:
        warnings.append(f"{name}: compare {basis['fromHead'][:12]}...{head[:12]} unavailable ({exc}); reviewing incrementally")
        return basis
    if paths is None or not documentation_only(paths):
        return basis
    return {**basis, "mode": "carry", "changedFiles": paths}


def select(api, state, repositories, force, scratch):
    identity = api.get("user")
    login = identity.get("login") if isinstance(identity, dict) else None
    if not isinstance(login, str):
        raise CollectionError("Authenticated GitHub login unavailable")
    hits, queries, complete = search_requests(api, login, repositories, scratch)
    candidates, skipped, warnings = [], [], [w for q in queries for w in q["warnings"]]
    for hit in hits:
        try:
            owner, repository, number = candidate_identity(hit, api.host)
            pr = api.get(f"repos/{owner}/{repository}/pulls/{number}")
            head, base = pr["head"]["sha"], pr["base"]["sha"]
        except (CollectionError, KeyError, TypeError) as exc:
            warnings.append(f"{hit.get('html_url')}: {exc}")
            continue
        name = f"{owner}/{repository}"
        item = {"key": key_of(name, number), "url": pr.get("html_url") or hit.get("html_url"),
                "repository": name, "number": number, "head": head, "base": base,
                "title": pr.get("title"), "updatedAt": pr.get("updated_at"), "createdAt": pr.get("created_at")}
        requested = {user.get("login", "").casefold() for user in pr.get("requested_reviewers") or []}
        if pr.get("state") != "open" or login.casefold() not in requested:
            skipped.append({**item, "reason": "request_no_longer_current"})
            continue
        if pr.get("draft"):
            skipped.append({**item, "reason": "draft"})
            continue
        entry = state["prs"].get(item["key"])
        kind, basis = classify(entry, head, force)
        if basis is None:
            skipped.append({**item, "reason": kind})
            continue
        if kind == "moved":
            basis = carry_basis(api, name, entry, head, basis, warnings)
        candidates.append({**item, "kind": kind, "basis": basis})
    seen = {c["key"] for c in candidates} | {s["key"] for s in skipped}
    # Publishing Request changes clears the request, so an unfinished live test is resumed from history.
    for key, entry in state["prs"].items():
        if (force or key in seen or entry.get("closed") or entry.get("complete") or not entry.get("reviewedHead")
                or entry.get("liveStatus") not in (None, "pending")):
            continue
        name, _, number = key.rpartition("#")
        try:
            repository_parts(name)
            pr = api.get(f"repos/{name}/pulls/{int(number)}")
            head = pr["head"]["sha"]
        except (CollectionError, KeyError, TypeError, ValueError) as exc:
            warnings.append(f"{key}: {exc}")
            continue
        if pr.get("state") != "open" or pr.get("draft") or head != entry["reviewedHead"]:
            continue
        candidates.append({"key": key, "url": pr.get("html_url") or entry.get("url"), "repository": name,
                           "number": int(number), "head": head, "base": pr["base"]["sha"], "title": pr.get("title"),
                           "updatedAt": pr.get("updated_at"), "createdAt": pr.get("created_at"), "kind": "retry",
                           "basis": {"mode": "retry", "stage": "live", "previousOutcome": entry.get("outcome"),
                                     "previousRun": entry.get("runDir")}})
    candidates.sort(key=lambda c: (PRIORITY[c["kind"]], c.get("updatedAt") if c["kind"] != "new" else c.get("createdAt") or ""))
    return {"login": login, "searchComplete": complete, "warnings": warnings,
            "selected": candidates, "skipped": skipped}


def summarize(pr):
    review = (pr.get("review") or {}).get("status")
    publication = (pr.get("publication") or {}).get("status")
    live = (pr.get("live") or {}).get("status")
    fresh = pr.get("current_head_sha") == pr.get("head_sha") and pr.get("current_base_sha") == pr.get("base_sha")
    if review == "clean" and publication in (None, "pending"):
        publication = "not_required"
    published = review in DONE_REVIEW and publication in DONE_PUBLICATION
    return review, publication, live, fresh, published


def record(results_path):
    results_path = Path(results_path).resolve()
    data = json.loads(results_path.read_text(encoding="utf-8"))
    changes = []
    with locked() as directory:
        state = read_state(directory)
        for pr in data.get("prs", []):
            if not pr.get("repository") or not pr.get("number") or not pr.get("head_sha"):
                continue
            key = key_of(pr["repository"], pr["number"])
            entry = state["prs"].setdefault(key, {"url": pr.get("url")})
            review, publication, live, fresh, published = summarize(pr)
            entry["lastRunAt"] = stamp()
            entry["runDir"] = str(results_path.parent)
            if pr.get("pr_state") and pr["pr_state"] != "open":
                entry["closed"] = True
            if published and fresh:
                entry.update({"reviewedHead": pr["head_sha"], "reviewedBase": pr["base_sha"],
                              "outcome": review, "publication": publication, "recordedAt": stamp(),
                              "liveStatus": live, "complete": live in DONE_LIVE})
            else:
                entry["complete"] = False
                if entry.get("reviewedHead") != pr["head_sha"]:
                    entry["outcome"] = review
                elif published:
                    entry["liveStatus"] = live
            changes.append({"key": key, "complete": entry["complete"], "review": review,
                            "publication": publication, "live": live, "fresh": fresh})
        state["updatedAt"] = stamp()
        save_json(directory / "state.json", state)
    return changes


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("select")
    command.add_argument("--all", action="store_true", help="Ignore history and select every requested PR")
    command.add_argument("--repo", action="append", default=[])
    command.add_argument("--host", default="github.com")
    command.add_argument("--output", type=Path, help="Selection file (default: history directory/selection.json)")
    command = commands.add_parser("record")
    command.add_argument("--results", required=True, type=Path)
    commands.add_parser("status")
    args = parser.parse_args(argv)
    try:
        if args.command == "select":
            if not require_dependencies():
                return 2
            for repository in args.repo:
                repository_parts(repository)
            with locked() as directory:
                state = read_state(directory)
            scratch = directory / "search"
            scratch.mkdir(parents=True, exist_ok=True)
            result = select(GitHub(args.host), state, list(dict.fromkeys(args.repo)), args.all, scratch)
            target = args.output or directory / "selection.json"
            save_json(target, result)
            print(json.dumps({"selection": str(target), "searchComplete": result["searchComplete"],
                              "selected": [f"{c['key']} {c['kind']}" for c in result["selected"]],
                              "skipped": {s["key"]: s["reason"] for s in result["skipped"]},
                              "warnings": result["warnings"][:5]}))
            return 0 if result["selected"] else NOTHING_NEW
        if args.command == "record":
            changes = record(args.results)
            print(json.dumps({"recorded": len(changes), "complete": sum(c["complete"] for c in changes),
                              "incomplete": [c["key"] for c in changes if not c["complete"]]}))
            return 0
        state = read_state(state_dir())
        print(json.dumps({"prs": len(state["prs"]),
                          "complete": sum(bool(e.get("complete")) for e in state["prs"].values()),
                          "updatedAt": state.get("updatedAt")}))
        return 0
    except (CollectionError, OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
