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
from feedback_snapshot import build_snapshot, read_pages, SnapshotError

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

    def plan(self, fake=None, **extra):
        fake = fake or FakeGitHub()
        data = {"url": URL, "expectedHeadSha": "abc", "analyzedAt": "2026-10-30T10:00:00Z",
                "replies": [{"key": "11", "kind": "inline", "inReplyTo": 11, "body": "Fixed in abc."},
                            {"key": "review-5", "kind": "conversation", "body": "Answered."}],
                "reviewers": ["alice", "bob"],
                "humanFeedbackSnapshot": build_snapshot(URL, "owner", fake.issue_comments,
                                                        fake.review_comments, fake.reviews), **extra}
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
        return json.loads(publish.publication_journal_path(self.dir / "pr-7-publish.json", URL).read_text())

    def seed_thread(self, fake):
        fake.review_comments.append({"id": 11, "body": "fix this", "user": fake.user("alice"),
                                     "created_at": "2026-10-29T00:00:00Z", "html_url": "h11"})

    def test_posts_replies_then_requests_reviewers_with_readback(self):
        fake = FakeGitHub()
        self.seed_thread(fake)
        fake.reviews = [{"id": 5, "body": "Reviewed", "user": fake.user("bob"), "commit_id": "abc", "state": "COMMENTED",
                         "submitted_at": "2026-10-29T00:00:00Z", "html_url": "r"}]
        code, lines = self.run_publish(fake, self.plan(fake))
        self.assertEqual(code, 0)
        self.assertEqual(len(fake.review_comments), 2)
        self.assertEqual(len(fake.issue_comments), 1)
        self.assertIn("<!-- respond-all 11 2026-10-30T10:00:00Z -->", fake.review_comments[1]["body"])
        self.assertEqual(fake.requested, ["alice"])
        journal = self.journal()
        self.assertEqual(journal["status"], "complete")
        self.assertEqual(journal["reviewers"]["alice"]["outcome"], "Requested")
        self.assertEqual(journal["reviewers"]["bob"]["outcome"], "Already reviewed final head")
        posts = [i for i, call in enumerate(fake.calls) if call[0] == "POST"]
        self.assertTrue(fake.calls[posts[-1]][1].endswith("requested_reviewers"))

    def test_rerun_is_idempotent(self):
        fake = FakeGitHub()
        self.seed_thread(fake)
        path = self.plan(fake)
        self.run_publish(fake, path)
        code, lines = self.run_publish(fake, path)
        self.assertEqual(code, 0)
        self.assertEqual(len(fake.review_comments) + len(fake.issue_comments), 3)
        self.assertTrue(any("existing" in line for line in lines))
        self.assertIn("Already requested", "\n".join(lines))

    def test_head_moved_or_closed_stops_before_writes(self):
        for fake in (FakeGitHub(head="def"), FakeGitHub(state="closed")):
            with self.subTest(head=fake.head, state=fake.state):
                code, lines = self.run_publish(fake, self.plan())
                self.assertEqual(code, 3)
                self.assertFalse([c for c in fake.calls if c[0] == "POST"])
                self.assertEqual(self.journal()["status"], "stale")

    def test_stale_retry_preserves_previously_verified_publication_evidence(self):
        fake = FakeGitHub()
        self.seed_thread(fake)
        path = self.plan(fake)
        self.assertEqual(self.run_publish(fake, path)[0], 0)
        previous = self.journal()
        posts = len([c for c in fake.calls if c[0] == "POST"])
        fake.review_comments[0]["body"] = "Edited feedback after publication"
        self.assertEqual(self.run_publish(fake, path)[0], 3)
        observed = self.journal()
        self.assertEqual(observed["status"], "stale")
        self.assertEqual(observed["replies"], previous["replies"])
        self.assertEqual(observed["reviewers"], previous["reviewers"])
        self.assertEqual(len([c for c in fake.calls if c[0] == "POST"]), posts)

    def test_dry_run_leaves_existing_journal_unchanged(self):
        fake = FakeGitHub()
        self.seed_thread(fake)
        path = self.plan(fake)
        self.assertEqual(self.run_publish(fake, path)[0], 0)
        journal = publish.publication_journal_path(path, URL)
        before = journal.read_bytes()
        self.assertEqual(self.run_publish(fake, path, "--dry-run")[0], 0)
        self.assertEqual(journal.read_bytes(), before)

    def test_revised_plan_archives_original_readback_evidence(self):
        fake = FakeGitHub()
        self.seed_thread(fake)
        path = self.plan(fake)
        self.assertEqual(self.run_publish(fake, path)[0], 0)
        previous = self.journal()
        data = json.loads(path.read_text())
        data["analyzedAt"] = "2026-10-30T11:00:00Z"
        path.write_text(json.dumps(data))
        self.assertEqual(self.run_publish(fake, path)[0], 0)
        history = self.journal()["history"]
        self.assertEqual(len(history), 1)
        self.assertEqual(json.loads(Path(history[0]).read_text()), previous)

    def test_new_human_feedback_after_analysis_is_stale_but_bots_are_not(self):
        fake = FakeGitHub()
        self.seed_thread(fake)
        fake.issue_comments = [{"id": 1, "body": "bot", "user": fake.user("coderabbitai[bot]", "Bot"),
                                "created_at": "2026-10-30T11:00:00Z", "html_url": "b"}]
        path = self.plan(fake)
        code, _ = self.run_publish(fake, path, "--dry-run")
        self.assertEqual(code, 0)
        fake.review_comments.append({"id": 2, "body": "one more", "user": fake.user("alice"),
                                     "created_at": "2026-10-30T11:00:00Z", "html_url": "h"})
        code, lines = self.run_publish(fake, path)
        self.assertEqual(code, 3)
        self.assertIn("re-analyze", lines[0])
        self.assertFalse([c for c in fake.calls if c[0] == "POST"])

    def test_dry_run_writes_nothing(self):
        fake = FakeGitHub()
        self.seed_thread(fake)
        code, lines = self.run_publish(fake, self.plan(fake), "--dry-run")
        self.assertEqual(code, 0)
        self.assertFalse([c for c in fake.calls if c[0] == "POST"])
        self.assertIn("would post inline", "\n".join(lines))
        self.assertIn("would request", "\n".join(lines))

    def test_failed_review_request_is_blocked_and_journaled(self):
        fake = FakeGitHub()
        self.seed_thread(fake)
        fake.unrequestable = {"bob"}
        code, lines = self.run_publish(fake, self.plan(fake))
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

    def test_unsafe_ids_and_keys_are_rejected_before_any_request(self):
        for reply in ({"key": "11", "kind": "inline", "inReplyTo": "../../../user/repos", "body": "x"},
                      {"key": "11", "kind": "inline", "inReplyTo": -4, "body": "x"},
                      {"key": "a --> b", "kind": "conversation", "body": "x"}):
            with self.subTest(reply=reply):
                fake = FakeGitHub()
                code, lines = self.run_publish(fake, self.plan(replies=[reply]))
                self.assertEqual(code, 1)
                self.assertEqual(fake.calls, [("GET", "user")])

    def test_inline_target_must_belong_to_this_pr(self):
        fake = FakeGitHub()
        fake.review_comments = [{"id": 11, "body": "fix this", "user": fake.user("alice"),
                                 "created_at": "2026-10-29T00:00:00Z", "html_url": "h11"}]
        plan = self.plan(fake, replies=[{"key": "12", "kind": "inline", "inReplyTo": 12, "body": "x"},
                                  {"key": "11", "kind": "inline", "inReplyTo": "11", "body": "y"}],
                         reviewers=[])
        code, lines = self.run_publish(fake, plan)
        self.assertEqual(code, 1)
        self.assertIn("not on this PR", lines[0])
        self.assertEqual([c for c in fake.calls if c[0] == "POST"],
                         [("POST", f"{BASE}/pulls/7/comments/11/replies")])

    def test_edits_to_existing_human_comments_or_review_bodies_are_stale(self):
        for kind in ("conversation", "inline", "review"):
            with self.subTest(kind=kind):
                fake = FakeGitHub()
                item = {"id": 11, "body": "Original request", "user": fake.user("alice"),
                        "created_at": "2026-10-29T00:00:00Z", "html_url": "human"}
                pool = {"conversation": fake.issue_comments, "inline": fake.review_comments,
                        "review": fake.reviews}[kind]
                if kind == "review":
                    item.update(submitted_at="2026-10-29T00:00:00Z", state="COMMENTED", commit_id="abc")
                pool.append(item)
                path = self.plan(fake)
                item["body"] = "Different request after analysis"
                if kind != "review":
                    item["updated_at"] = "2026-10-30T11:00:00Z"
                code, _ = self.run_publish(fake, path)
                self.assertEqual(code, 3)
                self.assertFalse([call for call in fake.calls if call[0] == "POST"])
                self.assertEqual(self.journal()["reason"], "human_feedback_changed")

    def test_legacy_plan_without_snapshot_stops_before_any_publication(self):
        fake = FakeGitHub()
        path = self.plan(humanFeedbackSnapshot=None)
        code, lines = self.run_publish(fake, path)
        self.assertEqual(code, 3)
        self.assertIn("original analysis evidence", lines[0])
        self.assertEqual(fake.calls, [("GET", "user")])

    def test_comment_edit_timestamp_change_is_stale_even_when_body_is_restored(self):
        fake = FakeGitHub()
        self.seed_thread(fake)
        fake.review_comments[0]["updated_at"] = "2026-10-29T00:00:00Z"
        path = self.plan(fake)
        fake.review_comments[0]["updated_at"] = "2026-10-30T11:00:00Z"
        self.assertEqual(self.run_publish(fake, path)[0], 3)
        self.assertFalse([call for call in fake.calls if call[0] == "POST"])

    def test_author_and_bot_changes_do_not_invalidate_snapshot_or_retry(self):
        fake = FakeGitHub()
        self.seed_thread(fake)
        path = self.plan(fake)
        fake.issue_comments.append({"id": 50, "body": "Bot update", "user": fake.user("bot[bot]", "Bot")})
        self.assertEqual(self.run_publish(fake, path)[0], 0)
        posts_before = len([call for call in fake.calls if call[0] == "POST"])
        self.assertEqual(self.run_publish(fake, path)[0], 0)
        self.assertEqual(len([call for call in fake.calls if call[0] == "POST"]), posts_before)

    def test_namespaced_journals_keep_same_numbers_in_different_repositories_separate(self):
        one = publish.publication_journal_path(self.dir / "pr-7-analysis.json", URL)
        two = publish.publication_journal_path(self.dir / "pr-7-analysis.json",
                                               "https://github.com/another/repo/pull/7")
        self.assertNotEqual(one, two)
        self.assertEqual(one, self.dir / "prs/example/repo/pr-7-publication.json")
        scoped = self.dir / "prs/example/repo/pr-7-analysis.json"
        self.assertEqual(publish.publication_journal_path(scoped, URL), one)
        with self.assertRaisesRegex(publish.PublishError, "different repository"):
            publish.publication_journal_path(scoped, "https://github.com/another/repo/pull/7")

    def test_legacy_journal_is_reused_only_for_its_exact_pr_identity(self):
        old = self.dir / "pr-7-publication.json"
        old.write_text(json.dumps({"url": "https://github.com/another/repo/pull/7"}))
        preferred = publish.publication_journal_path(self.dir / "plan.json", URL)
        self.assertNotEqual(preferred, old)
        old.write_text(json.dumps({"url": URL}))
        self.assertEqual(publish.publication_journal_path(self.dir / "plan.json", URL), old)

    def test_snapshot_is_order_independent_and_accepts_slurped_raw_pages(self):
        fake = FakeGitHub()
        self.seed_thread(fake)
        fake.review_comments.append({**fake.review_comments[0], "id": 12})
        first = build_snapshot(URL, "owner", [], fake.review_comments, [])
        second = build_snapshot(URL, "owner", [], list(reversed(fake.review_comments)), [])
        self.assertEqual(first, second)
        raw = self.dir / "raw.json"
        raw.write_text(json.dumps([[fake.review_comments[0]], [fake.review_comments[1]]]))
        self.assertEqual(read_pages(raw), fake.review_comments)
        with self.assertRaisesRegex(SnapshotError, "Duplicate"):
            build_snapshot(URL, "owner", [], [fake.review_comments[0]] * 2, [])

    def test_feedback_freshness_compares_instants_not_iso_string_order(self):
        item = {"user": {"login": "alice", "type": "User"}, "html_url": "human",
                "created_at": "2026-10-30T10:00:00Z", "updated_at": "2026-10-30T10:00:00Z"}
        self.assertEqual(publish.human_after([item], "owner", "2026-10-30T10:00:00.500+00:00", "created_at"), [])
        item["updated_at"] = "2026-10-30T08:30:00-02:00"
        self.assertEqual(publish.human_after([item], "owner", "2026-10-30T10:00:00Z", "created_at"), ["human"])

    def test_invalid_plan_is_reported_per_file(self):
        bad = self.dir / "bad.json"
        bad.write_text(json.dumps({"url": URL, "analyzedAt": "x"}))
        code, lines = self.run_publish(FakeGitHub(), bad)
        self.assertEqual(code, 1)
        self.assertIn("required", lines[0])


if __name__ == "__main__":
    unittest.main()
