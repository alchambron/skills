"""Offline tests for publish.py against an in-memory GitHub."""

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import publish

URL = "https://github.com/example/repo/pull/7"
BASE = "repos/example/repo"


class FakeGitHub:
    def __init__(self, head="abc", state="open"):
        self.head, self.state = head, state
        self.issue_comments, self.review_comments, self.reviews = [], [], []
        self.requested, self.calls, self.next_id = [], [], 100
        self.unrequestable = set()

    def user(self, login, kind="User"):
        return {"login": login, "type": kind}

    def __call__(self, method, path, body=None, paginate=False):
        self.calls.append((method, path))
        if path == "user":
            return {"login": "owner"}
        if path == f"{BASE}/pulls/7":
            return {"state": self.state, "head": {"sha": self.head},
                    "requested_reviewers": [{"login": l} for l in self.requested]}
        if path.startswith(f"{BASE}/issues/7/comments") and method == "GET":
            return self.issue_comments
        if path.startswith(f"{BASE}/pulls/7/comments") and method == "GET":
            return self.review_comments
        if path.startswith(f"{BASE}/pulls/7/reviews"):
            return self.reviews
        if method == "POST" and path == f"{BASE}/issues/7/comments":
            return self.add(self.issue_comments, body)
        if method == "POST" and path.startswith(f"{BASE}/pulls/7/comments/") and path.endswith("/replies"):
            return self.add(self.review_comments, body)
        if method == "GET" and path.startswith((f"{BASE}/issues/comments/", f"{BASE}/pulls/comments/")):
            wanted = int(path.rsplit("/", 1)[1])
            return next(c for c in self.issue_comments + self.review_comments if c["id"] == wanted)
        if method == "POST" and path == f"{BASE}/pulls/7/requested_reviewers":
            for login in body["reviewers"]:
                if login in self.unrequestable:
                    raise publish.PublishError("Reviews may only be requested from collaborators")
                self.requested.append(login)
            return {}
        raise AssertionError(f"unexpected {method} {path}")

    def add(self, pool, body):
        self.next_id += 1
        item = {"id": self.next_id, "body": body["body"], "user": self.user("owner"),
                "html_url": f"https://github.com/example/repo/pull/7#c{self.next_id}",
                "created_at": "2026-10-30T12:00:00Z"}
        pool.append(item)
        return item


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def plan(self, **extra):
        data = {"url": URL, "expectedHeadSha": "abc", "analyzedAt": "2026-10-30T10:00:00Z",
                "replies": [{"key": "11", "kind": "inline", "inReplyTo": 11, "body": "Fixed in abc."},
                            {"key": "review-5", "kind": "conversation", "body": "Answered."}],
                "reviewers": ["alice", "bob"], **extra}
        path = self.dir / "pr-7-publish.json"
        path.write_text(json.dumps(data))
        return path

    def run_publish(self, fake, *args):
        output = io.StringIO()
        with patch.object(publish, "gh", side_effect=fake), \
                patch.object(sys, "argv", ["publish.py", *map(str, args)]), contextlib.redirect_stdout(output):
            code = publish.main()
        return code, output.getvalue().splitlines()

    def journal(self):
        return json.loads((self.dir / "pr-7-publication.json").read_text())

    def test_posts_replies_then_requests_reviewers_with_readback(self):
        fake = FakeGitHub()
        fake.reviews = [{"user": fake.user("bob"), "commit_id": "abc", "state": "COMMENTED",
                         "submitted_at": "2026-10-29T00:00:00Z", "html_url": "r"}]
        code, lines = self.run_publish(fake, self.plan())
        self.assertEqual(code, 0)
        self.assertEqual(len(fake.review_comments), 1)
        self.assertEqual(len(fake.issue_comments), 1)
        self.assertIn("<!-- respond-all 11 2026-10-30T10:00:00Z -->", fake.review_comments[0]["body"])
        self.assertEqual(fake.requested, ["alice"])
        journal = self.journal()
        self.assertEqual(journal["status"], "complete")
        self.assertEqual(journal["reviewers"]["alice"]["outcome"], "Requested")
        self.assertEqual(journal["reviewers"]["bob"]["outcome"], "Already reviewed final head")
        posts = [i for i, call in enumerate(fake.calls) if call[0] == "POST"]
        self.assertTrue(fake.calls[posts[-1]][1].endswith("requested_reviewers"))

    def test_rerun_is_idempotent(self):
        fake = FakeGitHub()
        self.run_publish(fake, self.plan())
        code, lines = self.run_publish(fake, self.plan())
        self.assertEqual(code, 0)
        self.assertEqual(len(fake.review_comments) + len(fake.issue_comments), 2)
        self.assertTrue(any("existing" in line for line in lines))
        self.assertIn("Already requested", "\n".join(lines))

    def test_head_moved_or_closed_stops_before_writes(self):
        for fake in (FakeGitHub(head="def"), FakeGitHub(state="closed")):
            with self.subTest(head=fake.head, state=fake.state):
                code, lines = self.run_publish(fake, self.plan())
                self.assertEqual(code, 3)
                self.assertFalse([c for c in fake.calls if c[0] == "POST"])
                self.assertEqual(self.journal()["status"], "stale")

    def test_new_human_feedback_after_analysis_is_stale_but_bots_are_not(self):
        fake = FakeGitHub()
        fake.issue_comments = [{"id": 1, "body": "bot", "user": fake.user("coderabbitai[bot]", "Bot"),
                                "created_at": "2026-10-30T11:00:00Z", "html_url": "b"}]
        code, _ = self.run_publish(fake, self.plan(), "--dry-run")
        self.assertEqual(code, 0)
        fake.review_comments = [{"id": 2, "body": "one more", "user": fake.user("alice"),
                                 "created_at": "2026-10-30T11:00:00Z", "html_url": "h"}]
        code, lines = self.run_publish(fake, self.plan())
        self.assertEqual(code, 3)
        self.assertIn("re-analyze", lines[0])
        self.assertFalse([c for c in fake.calls if c[0] == "POST"])

    def test_dry_run_writes_nothing(self):
        fake = FakeGitHub()
        code, lines = self.run_publish(fake, self.plan(), "--dry-run")
        self.assertEqual(code, 0)
        self.assertFalse([c for c in fake.calls if c[0] == "POST"])
        self.assertIn("would post inline", "\n".join(lines))
        self.assertIn("would request", "\n".join(lines))

    def test_failed_review_request_is_blocked_and_journaled(self):
        fake = FakeGitHub()
        fake.unrequestable = {"bob"}
        code, lines = self.run_publish(fake, self.plan())
        self.assertEqual(code, 1)
        self.assertEqual(self.journal()["reviewers"]["bob"]["outcome"], "Blocked")
        self.assertEqual(self.journal()["status"], "blocked")

    def test_analyzer_report_is_accepted_as_plan(self):
        path = self.dir / "pr-7-analysis.json"
        path.write_text(json.dumps({
            "url": URL, "headSha": "abc", "analyzedAt": "2026-10-30T10:00:00Z",
            "comments": [
                {"id": 11, "replyKind": "inline", "draftReply": "Answer inline"},
                {"id": "review-5", "draftReply": "Answer in conversation"},
                {"id": 12, "draftReply": ""},
                {"id": 13, "draftReply": "Existing reply is adequate", "publish": False}],
            "reviewers": [{"login": "alice"}, {"login": "carol", "request": False}]}))
        plan = publish.load_plan(path)
        self.assertEqual([(r["key"], r["kind"], r["inReplyTo"]) for r in plan["replies"]],
                         [("11", "inline", 11), ("review-5", "conversation", None)])
        self.assertEqual(plan["reviewers"], ["alice"])

    def test_invalid_plan_is_reported_per_file(self):
        bad = self.dir / "bad.json"
        bad.write_text(json.dumps({"url": URL, "analyzedAt": "x"}))
        code, lines = self.run_publish(FakeGitHub(), bad)
        self.assertEqual(code, 1)
        self.assertIn("required", lines[0])


if __name__ == "__main__":
    unittest.main()
