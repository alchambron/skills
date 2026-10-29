#!/usr/bin/env python3
"""Collect immutable review context using authenticated, read-only GitHub API calls."""

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlencode, urlparse

from project_profiles import project_profile


COMPONENT = re.compile(r"[A-Za-z0-9_.-]+\Z")
HOST = re.compile(r"[A-Za-z0-9.-]+\Z")
SHA = re.compile(r"[0-9a-fA-F]{40}\Z")


class CollectionError(Exception):
    """API or response failure safe to include in public artifacts."""


def safe_component(value):
    if not isinstance(value, str) or not COMPONENT.fullmatch(value) or value in {".", ".."}:
        raise CollectionError("Invalid repository path component")
    return value


def repository_parts(value):
    parts = value.split("/") if isinstance(value, str) else []
    if len(parts) != 2:
        raise CollectionError("Repository must be owner/name")
    return tuple(safe_component(part) for part in parts)


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


class GitHub:
    def __init__(self, host="github.com", runner=subprocess.run):
        if not HOST.fullmatch(host) or host.startswith("-"):
            raise CollectionError("Invalid GitHub hostname")
        self.host = host
        self.runner = runner

    def _execute(self, arguments):
        try:
            result = self.runner(arguments, capture_output=True, text=True, timeout=90, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CollectionError(f"GitHub command failed ({type(exc).__name__})") from exc
        if result.returncode:
            # gh stderr may contain user or credential-bearing configuration. Keep it private.
            raise CollectionError(f"GitHub API failed (exit {result.returncode}); inspect authentication/access privately")
        try:
            return json.loads(result.stdout)
        except (TypeError, json.JSONDecodeError) as exc:
            raise CollectionError("GitHub returned invalid JSON") from exc

    def get(self, endpoint, parameters=None):
        if parameters:
            endpoint += "?" + urlencode(parameters)
        return self._execute(["gh", "api", "--hostname", self.host, "--method", "GET", endpoint])

    def graphql(self, query, variables=None):
        arguments = ["gh", "api", "--hostname", self.host, "graphql", "-f", "query=" + query]
        for key, value in (variables or {}).items():
            if value is None:
                # Omitted nullable variables default to null without a cursor field.
                continue
            if type(value) is int:
                arguments.extend(["-F", f"{key}={value}"])
            elif isinstance(value, str):
                # Raw fields preserve strings such as "null", "123", or "@path".
                arguments.extend(["-f", f"{key}={value}"])
            else:
                raise CollectionError("Unsupported GraphQL variable type")
        result = self._execute(arguments)
        if not isinstance(result, dict) or result.get("errors") or not result.get("data"):
            raise CollectionError("GraphQL context failed or returned partial data")
        return result["data"]


def get_list(api, endpoint, target):
    """Save each completed page even when a later page fails."""
    pages, items = [], []
    try:
        for page in range(1, 101):
            payload = api.get(endpoint, {"per_page": 100, "page": page})
            if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
                raise CollectionError("Expected paginated API array")
            pages.append(payload)
            items.extend(payload)
            save_json(target.with_name(target.stem + "-pages.json"), pages)
            if len(payload) < 100:
                save_json(target, items)
                return items, True, None
        raise CollectionError("Pagination exceeded 100 pages")
    except CollectionError as exc:
        save_json(target, items)
        return items, False, str(exc)


def get_reviewers(api, endpoint, target):
    requested, pages = {"users": [], "teams": []}, []
    for page in range(1, 101):
        payload = api.get(endpoint, {"per_page": 100, "page": page})
        if not isinstance(payload, dict) or any(not isinstance(payload.get(key), list) or any(not isinstance(item, dict) for item in payload[key]) for key in requested):
            raise CollectionError("Requested-reviewer metadata incomplete")
        pages.append(payload)
        save_json(target.with_name("requested-reviewers-pages.json"), pages)
        for key in requested:
            requested[key].extend(payload[key])
        save_json(target, requested)
        if all(len(payload[key]) < 100 for key in requested):
            return requested
    raise CollectionError("Requested-reviewer pagination exceeded 100 pages")


def search_requests(api, login, repositories, output):
    items, queries, complete, seen = [], [], True, set()
    for repository in repositories or [None]:
        query = f"is:pr is:open review-requested:{login}"
        if repository:
            query += f" repo:{repository}"
        status = {"query": query, "total_count": None, "received": 0, "complete": True, "warnings": []}
        pages = []
        try:
            for page in range(1, 11):
                payload = api.get("search/issues", {"q": query, "per_page": 100, "page": page})
                if not isinstance(payload, dict) or not isinstance(payload.get("items"), list) or any(not isinstance(item, dict) for item in payload["items"]):
                    raise CollectionError("Invalid search response")
                pages.append(payload)
                status["total_count"] = payload.get("total_count")
                if not isinstance(status["total_count"], int):
                    raise CollectionError("Search omitted total_count")
                if payload.get("incomplete_results"):
                    status["complete"] = False
                    status["warnings"].append("GitHub marked search incomplete_results")
                for item in payload["items"]:
                    key = item.get("html_url") or item.get("url")
                    if key not in seen:
                        seen.add(key)
                        items.append(item)
                status["received"] += len(payload["items"])
                save_json(output / f"search-{len(queries) + 1}-pages.json", pages)
                if len(payload["items"]) < 100 or status["received"] >= min(status["total_count"], 1000):
                    break
            if status["total_count"] > 1000:
                status["complete"] = False
                status["warnings"].append("GitHub search exposes at most 1000 results; narrow with --repo")
            if status["received"] < min(status["total_count"], 1000):
                status["complete"] = False
                status["warnings"].append("Search count exceeds received results")
        except CollectionError as exc:
            status["complete"] = False
            status["warnings"].append(str(exc))
        complete = complete and status["complete"]
        queries.append(status)
    return items, queries, complete


def collect_threads(api, owner, repository, number, target):
    threads, pages, cursor = [], [], None
    complete, error = True, None
    try:
        for _ in range(100):
            query = """query($owner: String!, $name: String!, $number: Int!, $after: String) {
              repository(owner: $owner, name: $name) { pullRequest(number: $number) {
                reviewThreads(first: 100, after: $after) {
                  pageInfo { hasNextPage endCursor }
                  nodes { id isResolved isOutdated path line originalLine startLine originalStartLine diffSide
                    comments(first: 100) { totalCount pageInfo { hasNextPage endCursor } nodes { id databaseId } }
                  }
                }
              } }
            }"""
            data = api.graphql(query, {"owner": owner, "name": repository, "number": number, "after": cursor})
            try:
                connection = data["repository"]["pullRequest"]["reviewThreads"]
                nodes = connection["nodes"]
                page_info = connection["pageInfo"]
                if not isinstance(nodes, list):
                    raise TypeError()
            except (KeyError, TypeError) as exc:
                raise CollectionError("Review-thread connection missing") from exc
            pages.append(data)
            save_json(target.with_name("review-threads-pages.json"), pages)
            for thread in nodes:
                threads.append(thread)
                comments = thread.get("comments", {})
                comment_nodes = list(comments.get("nodes", []))
                comment_page = comments.get("pageInfo", {})
                used = set()
                while comment_page.get("hasNextPage"):
                    comment_cursor = comment_page.get("endCursor")
                    if not comment_cursor or comment_cursor in used:
                        raise CollectionError("Review-thread comments cursor did not advance")
                    used.add(comment_cursor)
                    nested = "query($thread: ID!, $after: String) { node(id: $thread) { ... on PullRequestReviewThread { comments(first: 100, after: $after) { totalCount pageInfo { hasNextPage endCursor } nodes { id databaseId } } } } }"
                    result = api.graphql(nested, {"thread": thread["id"], "after": comment_cursor})
                    try:
                        comment_connection = result["node"]["comments"]
                        comment_nodes.extend(comment_connection["nodes"])
                        comment_page = comment_connection["pageInfo"]
                    except (KeyError, TypeError) as exc:
                        raise CollectionError("Nested thread comments missing") from exc
                comments["nodes"] = comment_nodes
                comments["pageInfo"] = comment_page
                if comments.get("totalCount") != len(comment_nodes):
                    raise CollectionError("Review-thread comment count mismatch")
            if not page_info.get("hasNextPage"):
                break
            next_cursor = page_info.get("endCursor")
            if not next_cursor or next_cursor == cursor:
                raise CollectionError("Review-thread cursor did not advance")
            cursor = next_cursor
        else:
            raise CollectionError("Review-thread pagination exceeded 100 pages")
    except (CollectionError, KeyError, TypeError, AttributeError) as exc:
        complete, error = False, str(exc) if isinstance(exc, CollectionError) else "Malformed review-thread metadata"
    save_json(target, threads)
    return threads, complete, error


def candidate_identity(hit, host):
    url = hit.get("pull_request", {}).get("url") or hit.get("url", "")
    parsed = urlparse(url)
    # Search REST URLs have /repos/OWNER/REPO/pulls/NUMBER or issues/NUMBER.
    components = parsed.path.strip("/").split("/")
    if components[:2] == ["api", "v3"]:
        components = components[2:]
    if len(components) != 5 or components[0] != "repos" or components[3] not in {"pulls", "issues"}:
        raise CollectionError("Search candidate has invalid API URL")
    owner, repository = (safe_component(part) for part in components[1:3])
    if not components[4].isdigit() or int(components[4]) < 1:
        raise CollectionError("Search candidate has invalid PR number")
    number = int(components[4])
    return owner, repository, number


def collect_pr(api, hit, login, output):
    owner, repository, number = candidate_identity(hit, api.host)
    endpoint = f"repos/{owner}/{repository}/pulls/{number}"
    directory = output / owner / repository / str(number)
    directory.mkdir(parents=True)
    record = {"repository": f"{owner}/{repository}", "number": number,
              "url": hit.get("html_url", f"https://{api.host}/{owner}/{repository}/pull/{number}"),
              "title": hit.get("title", ""), "base_sha": None, "head_sha": None,
              "draft": None, "artifact_dir": str(directory),
              "coverage": {"complete": False, "warnings": [], "artifacts": {}}}
    record.update(project_profile(record["repository"]))
    coverage = record["coverage"]
    warnings = coverage["warnings"]
    try:
        pr = api.get(endpoint)
        save_json(directory / "pull-request.json", pr)
        if not isinstance(pr, dict) or any(not isinstance(pr.get(side), dict) for side in ["base", "head"]):
            raise CollectionError("PR metadata missing base/head")
        for key in ["title", "body", "draft", "state", "created_at", "updated_at", "commits", "changed_files", "additions", "deletions"]:
            record[key] = pr.get(key)
        record["url"] = pr.get("html_url", record["url"])
        record["author"] = (pr.get("user") or {}).get("login")
        for side in ["base", "head"]:
            record[f"{side}_ref"] = pr[side].get("ref")
            record[f"{side}_sha"] = pr[side].get("sha")
            record[f"{side}_repository"] = (pr[side].get("repo") or {}).get("full_name")
            if not SHA.fullmatch(record[f"{side}_sha"] or ""):
                raise CollectionError("PR metadata has invalid commit SHA")
        requested = get_reviewers(api, endpoint + "/requested_reviewers", directory / "requested-reviewers.json")
        record["requested_reviewers"] = [user.get("login") for user in requested["users"]]
        record["requested_teams"] = [team.get("slug") for team in requested["teams"]]
        if pr.get("state") != "open" or login.casefold() not in {user.casefold() for user in record["requested_reviewers"] if isinstance(user, str)}:
            record["excluded"] = "PR is closed or authenticated user's direct review request is no longer current"
            return record, False
        coverage["artifacts"]["pull-request"] = {"complete": True}
        coverage["artifacts"]["requested-reviewers"] = {"complete": True}
        if record["draft"]:
            record["review_eligibility"] = "blocked_draft"
        else:
            record["review_eligibility"] = "ready"
        lists = {}
        for name, source in [("files", endpoint + "/files"), ("commits", endpoint + "/commits"),
                             ("issue-comments", f"repos/{owner}/{repository}/issues/{number}/comments"),
                             ("review-comments", endpoint + "/comments"), ("reviews", endpoint + "/reviews")]:
            values, complete, error = get_list(api, source, directory / (name + ".json"))
            lists[name] = values
            coverage["artifacts"][name] = {"complete": complete, "count": len(values), "error": error}
            if error:
                warnings.append(f"{name}: {error}")
        for artifact, count_name in [("files", "changed_files"), ("commits", "commits")]:
            expected = pr.get(count_name)
            if not isinstance(expected, int) or len(lists[artifact]) != expected:
                coverage["artifacts"][artifact]["complete"] = False
                warnings.append(f"{artifact} count mismatch: expected {expected}, collected {len(lists[artifact])}; local git fallback required")
        missing = [item.get("filename", "<unknown>") for item in lists["files"] if not item.get("patch")]
        coverage["missing_patches"] = missing
        coverage["local_git_fallback_required"] = bool(missing) or not coverage["artifacts"]["files"]["complete"] or not coverage["artifacts"]["commits"]["complete"]
        if missing:
            warnings.append(f"{len(missing)} files have no API patch; inspect full three-dot local git diff and binary changes")
        threads, complete, error = collect_threads(api, owner, repository, number, directory / "review-threads.json")
        coverage["artifacts"]["review-threads"] = {"complete": complete, "count": len(threads), "error": error}
        if error:
            warnings.append(f"review-threads: {error}")
        rest_ids = {comment.get("id") for comment in lists["review-comments"]}
        thread_ids = {comment.get("databaseId") for thread in threads for comment in thread.get("comments", {}).get("nodes", [])}
        if not thread_ids.issubset(rest_ids):
            coverage["artifacts"]["review-threads"]["complete"] = False
            warnings.append("Thread metadata references comments absent from REST artifacts; refresh context before duplicate checks")
        final_pr = api.get(endpoint)
        save_json(directory / "pull-request-final.json", final_pr)
        if not isinstance(final_pr, dict) or any(not isinstance(final_pr.get(side), dict) for side in ["base", "head"]):
            raise CollectionError("Final PR metadata missing base/head")
        record["current_head_sha"] = (final_pr.get("head") or {}).get("sha")
        record["current_base_sha"] = (final_pr.get("base") or {}).get("sha")
        coverage["revision_stable"] = record["head_sha"] == record["current_head_sha"] and record["base_sha"] == record["current_base_sha"]
        if not coverage["revision_stable"]:
            warnings.append("PR base/head changed during collection; recollect before reviewing/publishing")
        coverage["complete"] = all(part["complete"] for part in coverage["artifacts"].values()) and not missing and coverage["revision_stable"]
        record["collection_status"] = "complete" if coverage["complete"] else "partial"
    except (CollectionError, KeyError, TypeError, AttributeError) as exc:
        record["collection_status"] = "failed"
        warnings.append(str(exc) if isinstance(exc, CollectionError) else "Malformed PR context metadata")
    save_json(directory / "context.json", record)
    return record, True


def collect(api, output, repositories=()):
    output = Path(output).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=False)
    snapshot = {"version": 1, "login": None, "host": api.host,
                "collected_at": dt.datetime.now(dt.timezone.utc).isoformat(), "prs": [], "excluded": [],
                "collection": {"complete": False, "warnings": [], "queries": [],
                               "scope": "current direct review requests; team-only requests excluded", "repositories": list(repositories)}}
    target = output / "snapshot.json"
    try:
        identity = api.get("user")
        login = identity.get("login") if isinstance(identity, dict) else None
        if not isinstance(login, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", login):
            raise CollectionError("Authenticated GitHub login unavailable")
        snapshot["login"] = login
        hits, queries, search_complete = search_requests(api, login, repositories, output)
        snapshot["collection"]["queries"] = queries
        for query in queries:
            snapshot["collection"]["warnings"].extend(query["warnings"])
        save_json(target, snapshot)
        for hit in hits:
            try:
                record, included = collect_pr(api, hit, login, output)
                snapshot["prs" if included else "excluded"].append(record)
            except CollectionError as exc:
                snapshot["prs"].append({"url": hit.get("html_url"), "title": hit.get("title"),
                                        "collection_status": "failed", "coverage": {"complete": False, "warnings": [str(exc)]}})
            save_json(target, snapshot)
        snapshot["collection"]["complete"] = search_complete and all(pr["coverage"]["complete"] for pr in snapshot["prs"])
    except CollectionError as exc:
        snapshot["collection"]["warnings"].append(str(exc))
    finally:
        save_json(target, snapshot)
    return snapshot, target


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="New run directory (existing directories are refused)")
    parser.add_argument("--repo", action="append", default=[], help="Optional owner/name scope; repeat for several repositories")
    parser.add_argument("--host", default="github.com")
    args = parser.parse_args(argv)
    try:
        for repository in args.repo:
            repository_parts(repository)
        api = GitHub(args.host)
        snapshot, target = collect(api, args.output, list(dict.fromkeys(args.repo)))
    except (CollectionError, FileExistsError, OSError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps({"snapshot": str(target), "login": snapshot["login"], "prs": len(snapshot["prs"]),
                      "excluded": len(snapshot["excluded"]), "complete": snapshot["collection"]["complete"],
                      "partial_prs": sum(not pr["coverage"]["complete"] for pr in snapshot["prs"])}))
    return 0 if snapshot["collection"]["complete"] else 2


if __name__ == "__main__":
    sys.exit(main())
