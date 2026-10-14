#!/usr/bin/env python3
"""Behavior tests for inventory coverage, revision pinning, and read-only transport."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from urllib.parse import parse_qs, urlparse

import collect_reviews as collector


def hit(repository="EnerZam/JouleMV", number=10):
    return {"html_url": f"https://github.com/{repository}/pull/{number}", "title": "Feature",
            "pull_request": {"url": f"https://api.github.com/repos/{repository}/pulls/{number}"}}


def metadata(repository="EnerZam/JouleMV", number=10, **overrides):
    result = {"html_url": f"https://github.com/{repository}/pull/{number}", "title": "Feature",
              "body": "Expected feature", "state": "open", "draft": False, "user": {"login": "author"},
              "base": {"ref": "main", "sha": "a" * 40, "repo": {"full_name": repository}},
              "head": {"ref": "feature", "sha": "b" * 40, "repo": {"full_name": repository}},
              "commits": 1, "changed_files": 1, "additions": 2, "deletions": 1}
    result.update(overrides)
    return result


class FakeGitHub:
    host = "github.com"

    def __init__(self, hits=None):
        self.hits = hits if hits is not None else [hit()]
        self.responses = {}
        self.calls = []
        self.graphql_responses = []
        self.total_count = len(self.hits)
        self.incomplete = False

    def get(self, endpoint, parameters=None):
        self.calls.append((endpoint, parameters))
        page = (parameters or {}).get("page", 1)
        key = (endpoint, page) if parameters else endpoint
        if key in self.responses:
            value = self.responses[key]
            if callable(value):
                value = value()
            if isinstance(value, Exception):
                raise value
            return copy.deepcopy(value)
        if endpoint == "user":
            return {"login": "reviewer"}
        if endpoint == "search/issues":
            return {"items": self.hits[(page - 1) * 100:page * 100], "total_count": self.total_count,
                    "incomplete_results": self.incomplete}
        if endpoint.endswith("/requested_reviewers"):
            return {"users": [{"login": "reviewer"}], "teams": []}
        if endpoint.endswith("/files"):
            return [{"filename": "feature.py", "patch": "@@ -1 +1 @@\n-old\n+new"}]
        if endpoint.endswith("/commits"):
            return [{"sha": "b" * 40}]
        if endpoint.endswith("/comments") or endpoint.endswith("/reviews"):
            return []
        parts = endpoint.split("/")
        return metadata("/".join(parts[1:3]), int(parts[-1]))

    def graphql(self, query):
        self.calls.append(("graphql", query))
        if self.graphql_responses:
            response = self.graphql_responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return copy.deepcopy(response)
        return {"repository": {"pullRequest": {"reviewThreads": {
            "nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}}}}


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.output = Path(self.temporary.name) / "run"

    def run_collection(self, api):
        snapshot, path = collector.collect(api, self.output)
        self.assertEqual(snapshot, json.loads(path.read_text()))
        return snapshot

    def test_account_wide_cross_repository_inventory_includes_drafts(self):
        api = FakeGitHub([hit(), hit("AnotherOwner/Alarm", 2)])
        api.responses["repos/AnotherOwner/Alarm/pulls/2"] = metadata("AnotherOwner/Alarm", 2, draft=True)
        snapshot = self.run_collection(api)
        self.assertTrue(snapshot["collection"]["complete"])
        self.assertEqual({pr["repository"] for pr in snapshot["prs"]}, {"EnerZam/JouleMV", "AnotherOwner/Alarm"})
        self.assertEqual(snapshot["prs"][1]["review_eligibility"], "blocked_draft")
        self.assertEqual(snapshot["prs"][0]["head_sha"], "b" * 40)
        search = next(parameters for endpoint, parameters in api.calls if endpoint == "search/issues")
        self.assertNotIn("repo:", search["q"])

    def test_completed_and_team_only_requests_are_excluded_after_live_check(self):
        api = FakeGitHub([hit(), hit(number=11)])
        api.responses[("repos/EnerZam/JouleMV/pulls/10/requested_reviewers", 1)] = {
            "users": [], "teams": [{"slug": "review-team"}]}
        api.responses["repos/EnerZam/JouleMV/pulls/11"] = metadata(number=11, state="closed")
        snapshot = self.run_collection(api)
        self.assertEqual(snapshot["prs"], [])
        self.assertEqual(len(snapshot["excluded"]), 2)
        self.assertTrue(snapshot["collection"]["complete"])
        self.assertFalse(any(endpoint.endswith("/files") for endpoint, _ in api.calls))

    def test_requested_reviewer_on_second_page_is_retained(self):
        api = FakeGitHub()
        endpoint = "repos/EnerZam/JouleMV/pulls/10/requested_reviewers"
        api.responses[(endpoint, 1)] = {"users": [{"login": f"someone{i}"} for i in range(100)], "teams": []}
        api.responses[(endpoint, 2)] = {"users": [{"login": "reviewer"}], "teams": []}
        snapshot = self.run_collection(api)
        self.assertEqual(len(snapshot["prs"]), 1)
        self.assertEqual(len(snapshot["prs"][0]["requested_reviewers"]), 101)

    def test_revision_movement_preserves_initial_pin_and_marks_context_partial(self):
        api = FakeGitHub()
        revisions = [metadata(), metadata(head={"ref": "feature", "sha": "c" * 40})]
        api.responses["repos/EnerZam/JouleMV/pulls/10"] = lambda: revisions.pop(0)
        snapshot = self.run_collection(api)
        pr = snapshot["prs"][0]
        self.assertEqual(pr["head_sha"], "b" * 40)
        self.assertEqual(pr["current_head_sha"], "c" * 40)
        self.assertFalse(pr["coverage"]["revision_stable"])
        self.assertFalse(snapshot["collection"]["complete"])

    def test_all_rest_pages_and_nested_thread_comments_are_saved(self):
        api = FakeGitHub()
        endpoint = "repos/EnerZam/JouleMV/pulls/10"
        api.responses[endpoint] = metadata(changed_files=101)
        api.responses[(endpoint + "/files", 1)] = [{"filename": f"f{i}", "patch": "p"} for i in range(100)]
        api.responses[(endpoint + "/files", 2)] = [{"filename": "f100", "patch": "p"}]
        api.responses[(endpoint + "/comments", 1)] = [{"id": i} for i in range(100)]
        api.responses[(endpoint + "/comments", 2)] = [{"id": 100}]
        thread = {"id": "thread-one", "isResolved": False, "isOutdated": True, "path": "f0", "line": 1,
                  "comments": {"totalCount": 101, "nodes": [{"id": str(i), "databaseId": i} for i in range(100)],
                               "pageInfo": {"hasNextPage": True, "endCursor": "comments-cursor"}}}
        api.graphql_responses = [
            {"repository": {"pullRequest": {"reviewThreads": {"nodes": [thread],
                "pageInfo": {"hasNextPage": True, "endCursor": "threads-cursor"}}}}},
            {"node": {"comments": {"nodes": [{"id": "100", "databaseId": 100}], "totalCount": 101,
                "pageInfo": {"hasNextPage": False, "endCursor": "end"}}}},
            {"repository": {"pullRequest": {"reviewThreads": {"nodes": [],
                "pageInfo": {"hasNextPage": False, "endCursor": "end"}}}}},
        ]
        snapshot = self.run_collection(api)
        self.assertTrue(snapshot["collection"]["complete"])
        directory = Path(snapshot["prs"][0]["artifact_dir"])
        self.assertEqual(len(json.loads((directory / "files.json").read_text())), 101)
        threads = json.loads((directory / "review-threads.json").read_text())
        self.assertEqual(len(threads[0]["comments"]["nodes"]), 101)
        self.assertTrue(threads[0]["isOutdated"])

    def test_search_paginates_and_deduplicates(self):
        api = FakeGitHub([hit()] * 100 + [hit("Other/Repo", 1)])
        snapshot = self.run_collection(api)
        self.assertEqual(len(snapshot["prs"]), 2)
        self.assertEqual([params["page"] for endpoint, params in api.calls if endpoint == "search/issues"], [1, 2])

    def test_search_caps_and_incomplete_results_never_claim_complete(self):
        api = FakeGitHub()
        api.total_count = 1001
        api.incomplete = True
        snapshot = self.run_collection(api)
        self.assertFalse(snapshot["collection"]["complete"])
        warnings = " ".join(snapshot["collection"]["warnings"])
        self.assertIn("1000", warnings)
        self.assertIn("incomplete_results", warnings)

    def test_missing_patch_and_file_count_trigger_git_fallback(self):
        api = FakeGitHub()
        api.responses["repos/EnerZam/JouleMV/pulls/10"] = metadata(changed_files=2)
        api.responses[("repos/EnerZam/JouleMV/pulls/10/files", 1)] = [{"filename": "logo.png"}]
        snapshot = self.run_collection(api)
        coverage = snapshot["prs"][0]["coverage"]
        self.assertFalse(coverage["complete"])
        self.assertTrue(coverage["local_git_fallback_required"])
        self.assertEqual(coverage["missing_patches"], ["logo.png"])
        self.assertFalse(coverage["artifacts"]["files"]["complete"])

    def test_context_api_failure_retains_pr_and_successful_artifacts(self):
        api = FakeGitHub()
        api.responses[("repos/EnerZam/JouleMV/pulls/10/reviews", 1)] = collector.CollectionError("Private access failed")
        snapshot = self.run_collection(api)
        pr = snapshot["prs"][0]
        self.assertEqual(pr["collection_status"], "partial")
        self.assertFalse(pr["coverage"]["artifacts"]["reviews"]["complete"])
        self.assertTrue((Path(pr["artifact_dir"]) / "files.json").exists())

    def test_auth_and_search_failures_are_saved_as_incomplete(self):
        for failed_endpoint in ["user", "search/issues"]:
            with self.subTest(endpoint=failed_endpoint):
                api = FakeGitHub()
                api.responses[failed_endpoint if failed_endpoint == "user" else (failed_endpoint, 1)] = collector.CollectionError("Access failed")
                snapshot, path = collector.collect(api, Path(self.temporary.name) / failed_endpoint.replace("/", "-"))
                self.assertFalse(snapshot["collection"]["complete"])
                self.assertTrue(path.exists())
                self.assertTrue(snapshot["collection"]["warnings"])

    def test_new_run_cannot_overwrite_prior_evidence(self):
        self.output.mkdir()
        marker = self.output / "snapshot.json"
        marker.write_text("existing evidence")
        with self.assertRaises(FileExistsError):
            collector.collect(FakeGitHub(), self.output)
        self.assertEqual(marker.read_text(), "existing evidence")

    def test_unsafe_repository_identity_retains_failure_without_path_traversal(self):
        bad = hit()
        bad["pull_request"]["url"] = "https://api.github.com/repos/../private/pulls/1"
        snapshot = self.run_collection(FakeGitHub([bad]))
        self.assertFalse(snapshot["collection"]["complete"])
        self.assertEqual(snapshot["prs"][0]["collection_status"], "failed")
        self.assertFalse((Path(self.temporary.name) / "private").exists())


class TransportTests(unittest.TestCase):
    def test_transport_uses_only_get_and_read_only_graphql_with_argv(self):
        calls = []
        backend = FakeGitHub()

        def runner(argv, **kwargs):
            calls.append((argv, kwargs))
            if "graphql" in argv:
                response = {"data": backend.graphql(argv[-1].removeprefix("query="))}
            else:
                parsed = urlparse(argv[-1])
                parameters = {key: values[0] for key, values in parse_qs(parsed.query).items()}
                for key in ["page", "per_page"]:
                    if key in parameters:
                        parameters[key] = int(parameters[key])
                response = backend.get(parsed.path, parameters or None)
            return CompletedProcess(argv, 0, json.dumps(response), "")

        api = collector.GitHub(runner=runner)
        with tempfile.TemporaryDirectory() as temporary:
            snapshot, _ = collector.collect(api, Path(temporary) / "run")
        self.assertTrue(snapshot["collection"]["complete"])
        self.assertEqual(len(snapshot["prs"]), 1)
        for argv, kwargs in calls:
            self.assertIsInstance(argv, list)
            self.assertNotIn("shell", kwargs)
            if "graphql" in argv:
                self.assertTrue(argv[-1].startswith("query=query"))
            else:
                self.assertEqual(argv[argv.index("--method") + 1], "GET")

    def test_transport_errors_do_not_expose_credential_bearing_stderr(self):
        runner = lambda argv, **kwargs: CompletedProcess(argv, 1, "", "Bearer very-private-token")
        with self.assertRaises(collector.CollectionError) as context:
            collector.GitHub(runner=runner).get("user")
        self.assertNotIn("very-private-token", str(context.exception))


if __name__ == "__main__":
    unittest.main()
