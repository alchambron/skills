#!/usr/bin/env python3
"""Publish prepared PR replies and reviewer requests, then read both back.

Usage: publish.py PLAN.json [PLAN.json ...] [--dry-run] [--journal-dir DIR]

A plan is either an analyzer report (comments with draftReply) or an explicit plan:
  {"url": PR_URL, "expectedHeadSha": SHA, "analyzedAt": ISO,
   "replies": [{"key": "4426046010", "kind": "inline", "inReplyTo": 4426046010, "body": "..."},
               {"key": "review-5700340832", "kind": "conversation", "body": "..."}],
   "reviewers": ["login"]}

For each PR, in order: verify the PR is open at expectedHeadSha; stop as stale if a human
posted after analyzedAt; post each reply once (a hidden marker makes reruns idempotent)
and read it back; then request each reviewer unless already requested or already reviewed
the final head, and read the request back. Every step is journaled before and after the
write in pr-NUMBER-publication.json beside the plan. Exit 0 complete, 1 blocked, 3 stale.
"""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys

URL = re.compile(r"https://github\.com/([^/]+)/([^/]+)/pull/(\d+)/?")
KEY = re.compile(r"[A-Za-z0-9._:-]{1,100}")


class PublishError(Exception):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def gh(method, path, body=None, paginate=False):
    command = ["gh", "api", "-X", method, path]
    if paginate:
        command += ["--paginate", "--slurp"]
    if body is not None:
        command += ["--input", "-"]
    result = subprocess.run(command, input=None if body is None else json.dumps(body),
                            text=True, capture_output=True, timeout=120)
    if result.returncode:
        raise PublishError(result.stderr.strip() or f"gh api {method} {path} failed")
    data = json.loads(result.stdout or "null")
    if paginate:
        return [item for page in data for item in page]
    return data


def marker(key, analyzed_at):
    return f"<!-- respond-all {key} {analyzed_at} -->"


def load_plan(path):
    data = json.loads(Path(path).read_text())
    url = data.get("url")
    match = URL.fullmatch(url or "")
    if not match:
        raise PublishError(f"{path}: url must be a full GitHub PR URL")
    head = data.get("expectedHeadSha") or data.get("headSha")
    analyzed_at = data.get("analyzedAt")
    if not head or not analyzed_at:
        raise PublishError(f"{path}: expectedHeadSha/headSha and analyzedAt are required")
    if "replies" in data:
        replies = data["replies"]
    else:
        replies = []
        for comment in data.get("comments", []):
            if not comment.get("draftReply") or comment.get("publish") is False:
                continue
            kind = comment.get("replyKind") or ("inline" if comment.get("inReplyTo") else "conversation")
            replies.append({"key": str(comment["id"]), "kind": kind, "body": comment["draftReply"],
                            "inReplyTo": comment.get("inReplyTo") or (
                                comment["id"] if kind == "inline" else None)})
    if "reviewersToRequest" in data:
        reviewers = data["reviewersToRequest"]
    else:
        reviewers = [r if isinstance(r, str) else r["login"] for r in data.get("reviewers", [])
                     if isinstance(r, str) or r.get("request", True)]
    for reply in replies:
        reply["key"] = str(reply.get("key", ""))
        if not KEY.fullmatch(reply["key"]):
            raise PublishError(f"{path}: reply key {reply['key']!r} must match {KEY.pattern}")
        if reply.get("kind") not in ("inline", "conversation") or not reply.get("body"):
            raise PublishError(f"{path}: reply {reply['key']} needs kind inline|conversation and body")
        if reply["kind"] == "inline":
            # The ID becomes part of an API path: accept only a positive integer.
            target = str(reply.get("inReplyTo", ""))
            if not target.isdigit() or int(target) <= 0:
                raise PublishError(f"{path}: inline reply {reply['key']} needs a numeric inReplyTo")
            reply["inReplyTo"] = int(target)
    owner, repo, number = match.groups()
    return {"path": Path(path), "url": url, "repo": f"{owner}/{repo}", "number": int(number),
            "head": head, "analyzedAt": analyzed_at, "replies": replies,
            "reviewers": list(dict.fromkeys(reviewers))}


class Journal:
    def __init__(self, path, plan):
        self.path = path
        self.data = {"url": plan["url"], "expectedHeadSha": plan["head"],
                     "analyzedAt": plan["analyzedAt"], "plan": str(plan["path"]),
                     "startedAt": now(), "status": "running", "replies": {}, "reviewers": {}}

    def record(self, section, key, **fields):
        self.data[section].setdefault(key, {}).update(fields, at=now())
        self.save()

    def finish(self, status, **fields):
        self.data.update(status=status, finishedAt=now(), **fields)
        self.save()

    def save(self):
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, indent=1) + "\n")
        os.replace(temporary, self.path)


def human_after(items, viewer, since, date_key):
    return [item.get("html_url") for item in items
            if (item.get("user") or {}).get("type") == "User"
            and item["user"]["login"] != viewer and (item.get(date_key) or "") > since]


def publish(plan, viewer, dry_run, journal_dir):
    repo, number, head = plan["repo"], plan["number"], plan["head"]
    base = f"repos/{repo}"
    journal = Journal(Path(journal_dir or plan["path"].parent) / f"pr-{number}-publication.json", plan)
    lines = []
    tag = f"{repo}#{number}"

    pull = gh("GET", f"{base}/pulls/{number}")
    if pull["state"] != "open":
        journal.finish("stale", reason=f"PR is {pull['state']}")
        return 3, [f"{tag}\tstale\tPR is {pull['state']}"]
    if pull["head"]["sha"] != head:
        journal.finish("stale", reason="head_moved", observedHeadSha=pull["head"]["sha"])
        return 3, [f"{tag}\tstale\thead moved to {pull['head']['sha'][:8]}; re-analyze"]

    issue_comments = gh("GET", f"{base}/issues/{number}/comments?per_page=100", paginate=True)
    review_comments = gh("GET", f"{base}/pulls/{number}/comments?per_page=100", paginate=True)
    reviews = gh("GET", f"{base}/pulls/{number}/reviews?per_page=100", paginate=True)
    newer = (human_after(issue_comments, viewer, plan["analyzedAt"], "created_at")
             + human_after(review_comments, viewer, plan["analyzedAt"], "created_at")
             + human_after(reviews, viewer, plan["analyzedAt"], "submitted_at"))
    if newer:
        journal.finish("stale", reason="new_human_feedback", newFeedback=newer)
        return 3, [f"{tag}\tstale\t{len(newer)} human item(s) after analysis; re-analyze: {newer[0]}"]

    blocked = 0
    for reply in plan["replies"]:
        key = reply["key"]
        mark = marker(key, plan["analyzedAt"])
        pool = review_comments if reply["kind"] == "inline" else issue_comments
        existing = next((c for c in pool if c["user"]["login"] == viewer and mark in (c.get("body") or "")), None)
        if existing:
            journal.record("replies", key, status="verified", outcome="existing", url=existing["html_url"])
            lines.append(f"{tag}\treply {key}\texisting\t{existing['html_url']}")
            continue
        if reply["kind"] == "inline" and not any(c["id"] == reply["inReplyTo"] for c in review_comments):
            blocked += 1
            journal.record("replies", key, status="blocked", error="inReplyTo is not a review comment on this PR")
            lines.append(f"{tag}\treply {key}\tblocked\tinReplyTo {reply['inReplyTo']} is not on this PR")
            continue
        if dry_run:
            lines.append(f"{tag}\treply {key}\twould post {reply['kind']}")
            continue
        body = reply["body"].rstrip() + "\n\n" + mark
        journal.record("replies", key, status="in_flight", kind=reply["kind"])
        try:
            if reply["kind"] == "inline":
                created = gh("POST", f"{base}/pulls/{number}/comments/{reply['inReplyTo']}/replies",
                             {"body": body})
                readback = gh("GET", f"{base}/pulls/comments/{created['id']}")
            else:
                created = gh("POST", f"{base}/issues/{number}/comments", {"body": body})
                readback = gh("GET", f"{base}/issues/comments/{created['id']}")
            if mark not in (readback.get("body") or ""):
                raise PublishError("readback body is missing the marker")
            journal.record("replies", key, status="verified", outcome="posted", url=readback["html_url"])
            lines.append(f"{tag}\treply {key}\tposted\t{readback['html_url']}")
        except (PublishError, subprocess.TimeoutExpired) as error:
            blocked += 1
            journal.record("replies", key, status="blocked", error=str(error))
            lines.append(f"{tag}\treply {key}\tblocked\t{error}")

    for login in plan["reviewers"]:
        if login == viewer:
            continue
        requested = {u["login"] for u in gh("GET", f"{base}/pulls/{number}")["requested_reviewers"]}
        if login in requested:
            outcome = "Already requested"
        elif any(r["user"] and r["user"]["login"] == login and r.get("commit_id") == head
                 and r.get("state") != "PENDING" for r in reviews):
            outcome = "Already reviewed final head"
        elif dry_run:
            lines.append(f"{tag}\treviewer {login}\twould request")
            continue
        else:
            journal.record("reviewers", login, status="in_flight", headSha=head)
            try:
                gh("POST", f"{base}/pulls/{number}/requested_reviewers", {"reviewers": [login]})
                after = {u["login"] for u in gh("GET", f"{base}/pulls/{number}")["requested_reviewers"]}
                if login not in after:
                    raise PublishError("request not visible on readback")
                outcome = "Requested"
            except (PublishError, subprocess.TimeoutExpired) as error:
                blocked += 1
                journal.record("reviewers", login, status="blocked", outcome="Blocked", error=str(error))
                lines.append(f"{tag}\treviewer {login}\tBlocked\t{error}")
                continue
        journal.record("reviewers", login, status="verified", outcome=outcome, headSha=head)
        lines.append(f"{tag}\treviewer {login}\t{outcome}")

    status = "dry_run" if dry_run else ("blocked" if blocked else "complete")
    journal.finish(status)
    lines.append(f"{tag}\t{status}\thead={head[:8]}\tjournal={journal.path}")
    return (1 if blocked else 0), lines


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("plans", nargs="+")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--journal-dir")
    args = parser.parse_args()
    viewer = gh("GET", "user")["login"]
    worst = 0
    for path in args.plans:
        try:
            code, lines = publish(load_plan(path), viewer, args.dry_run, args.journal_dir)
        except (PublishError, subprocess.TimeoutExpired, KeyError, json.JSONDecodeError) as error:
            code, lines = 1, [f"{path}\terror\t{error}"]
        print("\n".join(lines), flush=True)
        worst = max(worst, code)
    return worst


if __name__ == "__main__":
    sys.exit(main())
