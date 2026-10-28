#!/usr/bin/env python3
"""Read-only discovery of authored PRs with untouched human feedback."""

import argparse
from datetime import datetime, timezone
import json
import re
import subprocess
import sys


ACTOR = "author { login __typename }"
COMMENT = "id body url createdAt updatedAt " + ACTOR
REVIEW = "id body url submittedAt state " + ACTOR
PAGE = "pageInfo { hasNextPage endCursor hasPreviousPage startCursor }"
THREAD = "id isResolved isOutdated comments(last:100) { " + PAGE + " nodes { " + COMMENT + " } }"
COMMIT = "commit { oid committedDate pushedDate }"
EVENT_TYPES = (
    "[HEAD_REF_FORCE_PUSHED_EVENT,REVIEW_REQUESTED_EVENT,"
    "REVIEW_REQUEST_REMOVED_EVENT,REVIEW_DISMISSED_EVENT,READY_FOR_REVIEW_EVENT]"
)
EVENT = "__typename " + " ".join(
    "... on " + kind + " { createdAt actor { login __typename } }"
    for kind in (
        "HeadRefForcePushedEvent", "ReviewRequestedEvent",
        "ReviewRequestRemovedEvent", "ReviewDismissedEvent", "ReadyForReviewEvent",
    )
)
CONNECTIONS = {
    "labels": ("name", True, ""),
    "comments": (COMMENT, False, ""),
    "reviews": (REVIEW, False, ""),
    "reviewThreads": (THREAD, True, ""),
    "commits": (COMMIT, False, ""),
    "timelineItems": (EVENT, False, ",itemTypes:" + EVENT_TYPES),
}
META = (
    "id number title url state headRefOid reviewDecision "
    "author { login } repository { nameWithOwner }"
)
DETAIL = "id " + " ".join(
    field + "(" + ("first" if forward else "last") + ":100" + extra
    + ") { " + PAGE + " nodes { " + fields + " } }"
    for field, (fields, forward, extra) in CONNECTIONS.items()
)


def graphql(query, variables=None):
    result = subprocess.run(
        ["gh", "api", "graphql", "--input", "-"],
        input=json.dumps({"query": query, "variables": variables or {}}),
        text=True, capture_output=True, timeout=120,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "GitHub GraphQL request failed")
    payload = json.loads(result.stdout)
    if payload.get("errors"):
        raise RuntimeError("; ".join(e["message"] for e in payload["errors"]))
    return payload["data"]


def discover(login, repos):
    query_text = "is:pr is:open author:" + login
    query_text += "".join(" repo:" + repo for repo in repos)
    query = (
        "query($q:String!,$cursor:String) { search(query:$q,type:ISSUE,"
        "first:100,after:$cursor) { issueCount " + PAGE
        + " nodes { ... on PullRequest { " + META + " } } } }"
    )
    cursor = None
    found = []
    while True:
        page = graphql(query, {"q": query_text, "cursor": cursor})["search"]
        if page["issueCount"] > 1000:
            raise RuntimeError("GitHub search exceeds 1000 PRs; restrict with --repo")
        found.extend(page["nodes"])
        if not page["pageInfo"]["hasNextPage"]:
            return found
        next_cursor = page["pageInfo"]["endCursor"]
        if not next_cursor or next_cursor == cursor:
            raise RuntimeError("PR search pagination did not advance")
        cursor = next_cursor


def get_pr(url):
    match = re.fullmatch(r"https://github\.com/([^/]+)/([^/]+)/pull/(\d+)/?", url)
    if not match:
        raise ValueError("--pr requires a full https://github.com/OWNER/REPO/pull/NUMBER URL")
    owner, repo, number = match.groups()
    data = graphql(
        "query($owner:String!,$repo:String!,$number:Int!) { "
        "repository(owner:$owner,name:$repo) { pullRequest(number:$number) { "
        + META + " } } }",
        {"owner": owner, "repo": repo, "number": int(number)},
    )
    pr = (data.get("repository") or {}).get("pullRequest")
    if not pr:
        raise RuntimeError("PR is inaccessible: " + url)
    return pr


def complete_connection(node_id, typename, field, initial, fields, forward=False, extra=""):
    page = initial
    nodes = list(page["nodes"])
    seen = set()
    more = "hasNextPage" if forward else "hasPreviousPage"
    edge = "endCursor" if forward else "startCursor"
    args = ("first:100,after:$cursor" if forward else "last:100,before:$cursor") + extra
    while page["pageInfo"][more]:
        cursor = page["pageInfo"][edge]
        if not cursor or cursor in seen:
            raise RuntimeError("Pagination did not advance for " + field)
        seen.add(cursor)
        page = graphql(
            "query($id:ID!,$cursor:String!) { node(id:$id) { ... on "
            + typename + " { " + field + "(" + args + ") { " + PAGE
            + " nodes { " + fields + " } } } } }",
            {"id": node_id, "cursor": cursor},
        )["node"][field]
        if forward:
            nodes.extend(page["nodes"])
        else:
            nodes = page["nodes"] + nodes
    return nodes


def complete_detail(detail):
    completed = {"id": detail["id"]}
    for field, (fields, forward, extra) in CONNECTIONS.items():
        completed[field] = complete_connection(
            detail["id"], "PullRequest", field, detail[field], fields, forward, extra,
        )
    for thread in completed["reviewThreads"]:
        thread["comments"] = complete_connection(
            thread["id"], "PullRequestReviewThread", "comments", thread["comments"], COMMENT,
        )
    return completed


def is_other_human(author, login):
    return bool(author and author.get("__typename") == "User" and author["login"] != login)


def evaluate(pr, detail, login):
    """Return (match, exclusion reason); dates are GitHub UTC ISO timestamps."""
    if not pr.get("author") or pr["author"]["login"] != login:
        return None, "not_authored_by_viewer"
    if pr["state"] != "OPEN":
        return None, "closed"
    if pr.get("reviewDecision") == "APPROVED":
        return None, "approved"

    opinions = {}
    feedback = []
    actions = []

    def comment_event(comment, kind, resolved=False):
        author = comment.get("author")
        if author and author["login"] == login:
            actions.append({"kind": kind, "date": max(
                comment["createdAt"], comment.get("updatedAt") or comment["createdAt"],
            ), "url": comment["url"]})
        elif is_other_human(author, login) and comment["body"].strip() and not resolved:
            feedback.append({"kind": kind, "date": comment["createdAt"],
                             "url": comment["url"], "author": author["login"],
                             "body": comment["body"]})

    for comment in detail["comments"]:
        comment_event(comment, "comment")
    for thread in detail["reviewThreads"]:
        for comment in thread["comments"]:
            comment_event(comment, "inline", thread["isResolved"])
    for review in sorted(detail["reviews"], key=lambda r: r.get("submittedAt") or ""):
        date = review.get("submittedAt")
        if not date or review["state"] == "PENDING":
            continue
        author = review.get("author")
        if author and author["login"] == login:
            actions.append({"kind": "review", "date": date, "url": review["url"]})
        elif is_other_human(author, login):
            if review["state"] in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
                opinions[author["login"]] = review["state"]
            if review["state"] == "CHANGES_REQUESTED" or (
                review["state"] == "COMMENTED" and review["body"].strip()
            ):
                feedback.append({"kind": review["state"], "date": date,
                                 "url": review["url"], "author": author["login"],
                                 "body": review["body"]})
    if not pr.get("reviewDecision") and "APPROVED" in opinions.values() and (
        "CHANGES_REQUESTED" not in opinions.values()
    ):
        return None, "approved"
    if not feedback:
        return None, "no_human_feedback"

    latest = max(feedback, key=lambda event: event["date"])
    for item in detail["commits"]:
        commit = item["commit"]
        actions.append({"kind": "branch_commit", "date": max(
            commit["committedDate"], commit.get("pushedDate") or "",
        ), "sha": commit["oid"]})
    for event in detail["timelineItems"]:
        actor = event.get("actor")
        if event["__typename"] == "HeadRefForcePushedEvent" or (
            actor and actor["login"] == login
        ):
            actions.append({"kind": event["__typename"], "date": event["createdAt"]})
    later = [event for event in actions if event["date"] > latest["date"]]
    if later:
        return None, "author_acted_or_branch_updated_after_feedback"
    return {
        "repository": pr["repository"]["nameWithOwner"], "number": pr["number"],
        "title": pr["title"], "url": pr["url"], "headSha": pr["headRefOid"],
        "reviewDecision": pr.get("reviewDecision"), "feedback": latest,
        "labels": [label["name"] for label in detail["labels"]],
    }, None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--repo", action="append", default=[], help="Restrict to OWNER/REPO (repeatable)")
    scope.add_argument("--pr", action="append", default=[], help="Revalidate a full PR URL (repeatable)")
    args = parser.parse_args()
    login = graphql("query { viewer { login } }")["viewer"]["login"]
    prs = [get_pr(url) for url in args.pr] if args.pr else discover(login, args.repo)
    report = {"viewer": login, "collectedAt": datetime.now(timezone.utc).isoformat(),
              "scanned": len(prs), "matches": [], "excluded": [], "unavailable": []}
    pending = []
    for pr in prs:
        if pr.get("reviewDecision") == "APPROVED":
            report["excluded"].append({"url": pr["url"], "reason": "approved"})
        else:
            pending.append(pr)
    # Ten PRs keep the nested thread/comment query below GitHub's node limit.
    for offset in range(0, len(pending), 10):
        batch = pending[offset:offset + 10]
        try:
            nodes = graphql(
                "query($ids:[ID!]!) { nodes(ids:$ids) { ... on PullRequest { "
                + DETAIL + " } } }", {"ids": [pr["id"] for pr in batch]},
            )["nodes"]
        except (RuntimeError, subprocess.TimeoutExpired) as error:
            report["unavailable"].extend({"url": pr["url"], "error": str(error)} for pr in batch)
            continue
        for pr, node in zip(batch, nodes):
            try:
                if not node:
                    raise RuntimeError("PR became inaccessible during collection")
                match, reason = evaluate(pr, complete_detail(node), login)
                if match:
                    report["matches"].append(match)
                else:
                    report["excluded"].append({"url": pr["url"], "reason": reason})
            except (RuntimeError, subprocess.TimeoutExpired) as error:
                report["unavailable"].append({"url": pr["url"], "error": str(error)})
    report["matches"].sort(key=lambda pr: pr["feedback"]["date"], reverse=True)
    report["complete"] = not report["unavailable"]
    print(json.dumps(report, indent=2))
    return 0 if report["complete"] else 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
        print(json.dumps({"complete": False, "error": str(error)}), file=sys.stderr)
        sys.exit(2)
