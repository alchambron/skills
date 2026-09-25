#!/usr/bin/env python3
"""Select one person's confirmed review actions from a JouleMV queue run."""

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import sys


PR_URL = re.compile(r"https://github\.com/EnerZam/JouleMV/pull/(\d+)/?")


def _object(value, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _nonempty(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")
    return value.strip()


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{label} must be a positive integer")
    return value


def build_pending_reviews(actions_data, snapshot_data, login):
    login = _nonempty(login, "login")
    actions = _object(actions_data, "actions.json").get("actions")
    snapshot = _object(snapshot_data, "snapshot.json")
    prs = snapshot.get("prs")
    if not isinstance(actions, list) or not isinstance(prs, list):
        raise ValueError("actions.json actions and snapshot.json prs must be arrays")

    collected_at = _nonempty(snapshot.get("collected_at"), "snapshot collected_at")
    try:
        datetime.fromisoformat(collected_at)
    except ValueError as error:
        raise ValueError("snapshot collected_at must be an ISO timestamp") from error
    open_count = snapshot.get("open_count")
    if isinstance(open_count, bool) or not isinstance(open_count, int) or open_count < 0:
        raise ValueError("snapshot open_count must be a nonnegative integer")
    errors = snapshot.get("errors")
    if not isinstance(errors, list) or any(not isinstance(error, str) for error in errors):
        raise ValueError("snapshot errors must be an array of strings")

    by_number = {}
    for index, raw_pr in enumerate(prs):
        pr = _object(raw_pr, f"snapshot PR {index}")
        number = _number(pr.get("number"), f"snapshot PR {index} number")
        if number in by_number:
            raise ValueError(f"snapshot contains duplicate PR #{number}")
        by_number[number] = pr

    selected = {}
    for index, raw_action in enumerate(actions):
        action = _object(raw_action, f"action {index}")
        number = _number(action.get("pr"), f"action {index} pr")
        owner = _nonempty(action.get("owner"), f"action {index} owner")
        kind = _nonempty(action.get("action"), f"action {index} action")
        rereview = action.get("rereview", False)
        if not isinstance(rereview, bool):
            raise ValueError(f"action {index} rereview must be a boolean")
        if owner.casefold() != login.casefold() or kind != "Review":
            continue
        pr = by_number.get(number)
        if pr is None:
            raise ValueError(f"review action for PR #{number} is absent from snapshot")
        title = _nonempty(pr.get("title"), f"PR #{number} title")
        url = _nonempty(pr.get("url"), f"PR #{number} url")
        match = PR_URL.fullmatch(url)
        if not match or int(match.group(1)) != number:
            raise ValueError(f"PR #{number} has an invalid URL")
        head_sha = _nonempty(pr.get("headRefOid"), f"PR #{number} headRefOid")
        if not re.fullmatch(r"[0-9a-fA-F]{40}", head_sha):
            raise ValueError(f"PR #{number} headRefOid must be a full commit SHA")
        if number not in selected:
            selected[number] = {"number": number, "title": title,
                                "url": url, "headSha": head_sha, "kind": "Review"}
        if rereview:
            selected[number]["kind"] = "Re-review"

    warnings = list(errors)
    if len(prs) != open_count:
        warnings.insert(0, f"Partial queue coverage: {len(prs)}/{open_count} open PRs collected.")
    uncertainties = actions_data.get("uncertainties", [])
    if not isinstance(uncertainties, list):
        raise ValueError("actions.json uncertainties must be an array")
    if uncertainties:
        warnings.append(f"{len(uncertainties)} uncertain queue items are excluded from this confirmed-action list.")

    reviews = sorted(selected.values(), key=lambda item: (item["kind"] != "Re-review", item["number"]))
    return {"login": login, "collectedAt": collected_at, "openPrs": open_count,
            "collectedPrs": len(prs), "coverageWarnings": warnings, "reviews": reviews}


def render_markdown(result):
    lines = [f"**Pending JouleMV reviews for @{result['login']}**",
             f"Collected: {result['collectedAt']}"]
    for warning in result["coverageWarnings"]:
        lines.append(f"Coverage warning: {warning}")
    if not result["reviews"]:
        lines.append("No confirmed reviews currently assigned to you.")
    for index, review in enumerate(result["reviews"], 1):
        title = " ".join(review["title"].split())
        lines.extend([f"{index}. **{review['kind']} #{review['number']}** — {title}",
                      f"   {review['url']}"])
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue-output", type=Path, required=True,
                        help="Directory containing review_queue.py actions.json and snapshot.json")
    parser.add_argument("--login", required=True, help="Authenticated GitHub login to select")
    parser.add_argument("--json-output", type=Path,
                        help="Machine-readable output path (default: QUEUE_OUTPUT/pending-reviews.json)")
    args = parser.parse_args()
    os.umask(0o077)
    actions_data = json.loads((args.queue_output / "actions.json").read_text())
    snapshot_data = json.loads((args.queue_output / "snapshot.json").read_text())
    result = build_pending_reviews(actions_data, snapshot_data, args.login)
    output_path = args.json_output or args.queue_output / "pending-reviews.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2) + "\n")
    print(render_markdown(result), end="")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"Pending reviews failed: {error}", file=sys.stderr)
        sys.exit(2)
