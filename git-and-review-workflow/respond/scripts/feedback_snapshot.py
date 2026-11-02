#!/usr/bin/env python3
"""Fingerprint original, fully paginated human feedback for safe publication.

feedback_snapshot.py --url PR_URL --viewer AUTHOR --raw RAW_DIR [--out FILE]

RAW_DIR contains the original issue-comments.json, review-comments.json and
reviews.json collected with gh api --paginate --slurp. Embed the resulting JSON
as humanFeedbackSnapshot in the analysis and derived publication plans. Never
capture current feedback merely to authorize replies drafted against older data.
"""

import argparse
import hashlib
import json
from pathlib import Path
import sys


class SnapshotError(ValueError):
    pass


def build_snapshot(url, viewer, issue_comments, review_comments, reviews):
    if not isinstance(viewer, str) or not viewer:
        raise SnapshotError("A nonempty author login is required")
    rows = []
    counts = {}
    for kind, items in (("conversation", issue_comments), ("inline", review_comments),
                        ("review", reviews)):
        if not isinstance(items, list):
            raise SnapshotError(f"{kind} feedback must be a fully collected list")
        seen = set()
        count = 0
        for item in items:
            if not isinstance(item, dict):
                raise SnapshotError(f"Invalid {kind} feedback item")
            user = item.get("user")
            if not isinstance(user, dict) or not user.get("login") or not user.get("type"):
                raise SnapshotError(f"Cannot identify the author of {kind} feedback {item.get('id')}")
            if user["type"] == "Bot" or user["login"].casefold() == viewer.casefold():
                continue
            if user["type"] != "User":
                raise SnapshotError(f"Unknown actor type in {kind} feedback {item.get('id')}")
            if kind == "review" and item.get("state") == "PENDING":
                continue
            if not isinstance(item.get("id"), int) or not isinstance(item.get("body"), str):
                raise SnapshotError(f"Incomplete {kind} feedback item")
            if item["id"] in seen:
                raise SnapshotError(f"Duplicate {kind} feedback ID {item['id']}")
            seen.add(item["id"])
            row = {"kind": kind, "id": item["id"], "author": user["login"].casefold(),
                   "body": item["body"]}
            # Review bodies have no REST edit timestamp; their content hash still
            # detects edits. Comments additionally retain updated_at, catching a
            # change-and-revert between collection and publication.
            fields = ("submitted_at", "state", "commit_id") if kind == "review" else (
                "created_at", "updated_at", "in_reply_to_id")
            row.update({field: item.get(field) for field in fields})
            rows.append(row)
            count += 1
        counts[kind] = count
    rows.sort(key=lambda row: (row["kind"], row["id"]))
    canonical = json.dumps(rows, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return {"schemaVersion": 1, "url": url, "viewer": viewer.casefold(), "counts": counts,
            "sha256": hashlib.sha256(canonical.encode()).hexdigest()}


def read_pages(path):
    data = json.loads(Path(path).read_text())
    if not isinstance(data, list):
        raise SnapshotError(f"{path}: expected paginated REST arrays")
    if data and all(isinstance(page, list) for page in data):
        return [item for page in data for item in page]
    if all(isinstance(item, dict) for item in data):
        return data
    raise SnapshotError(f"{path}: invalid REST page structure")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--viewer", required=True)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    try:
        snapshot = build_snapshot(args.url, args.viewer,
                                  read_pages(args.raw / "issue-comments.json"),
                                  read_pages(args.raw / "review-comments.json"),
                                  read_pages(args.raw / "reviews.json"))
        text = json.dumps(snapshot, sort_keys=True) + "\n"
        if args.out:
            args.out.write_text(text)
        else:
            print(text, end="")
    except (SnapshotError, OSError, json.JSONDecodeError) as error:
        print(f"feedback snapshot error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
