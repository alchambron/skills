"""Offline checkpoint/recovery tests; GitHub writes and live runtimes are absent."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from collect_reviews import CollectionError
from ledger import initialize, record, resume, save
from test_render_results import ledger, scenario


class FakeGitHub:
    def __init__(self, metadata=None, review=None, fail=None):
        self.metadata = metadata or {"state": "open", "base": {"sha": "a" * 40}, "head": {"sha": "b" * 40}}
        self.review = review or {"id": 123, "state": "CHANGES_REQUESTED", "commit_id": "b" * 40}
        self.fail = fail
        self.calls = []

    def get(self, endpoint):
        self.calls.append(endpoint)
        if self.fail and self.fail in endpoint:
            raise CollectionError("read unavailable")
        if "/reviews/" in endpoint or "/comments/" in endpoint:
            return copy.deepcopy(self.review)
        return copy.deepcopy(self.metadata)


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "results.json"
        self.data = ledger()
        save(self.path, self.data)

    def tearDown(self):
        self.temp.cleanup()

    def load(self):
        return json.loads(self.path.read_text())

    def update(self, section, payload, **kwargs):
        return record(self.path, "Example/Application", 7, kwargs.get("base", "a" * 40),
                      kwargs.get("head", "b" * 40), section, payload)

    def publication(self):
        self.data["prs"][0]["publication"] = {
            "status": "verified", "review_url": "https://github.com/Example/Application/pull/7#pullrequestreview-123"}
        save(self.path, self.data)

    def test_init_preserves_all_candidates_and_refuses_overwrite(self):
        snapshot = copy.deepcopy(self.data)
        candidate = snapshot["prs"][0]
        candidate["project_type"] = "frontend"
        candidate["runtime_provenance"] = {"backend": "verified separately"}
        blocked = copy.deepcopy(candidate)
        blocked.update(number=8, url="https://github.com/Example/Application/pull/8", head_sha=None,
                       collection_status="failed", coverage={"warnings": ["no access"]})
        snapshot["prs"].append(blocked)
        snapshot_path = self.root / "snapshot.json"
        snapshot_path.write_text(json.dumps(snapshot))
        target = self.root / "new.json"
        result = initialize(snapshot_path, target)
        self.assertEqual(len(result["prs"]), 2)
        self.assertEqual(result["prs"][0]["project_type"], "frontend")
        self.assertEqual(result["prs"][1]["review"]["status"], "blocked")
        self.assertIsNone(result["prs"][1]["head_sha"])
        self.assertNotIn("current_head_sha", result["prs"][0])
        with self.assertRaises(FileExistsError):
            initialize(snapshot_path, target)

    def test_record_checks_revision_and_validation_before_replacement(self):
        original = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "revision mismatch"):
            self.update("review", {"status": "clean"}, head="c" * 40)
        with self.assertRaisesRegex(ValueError, "invalid review"):
            self.update("review", {"status": "approved"})
        self.assertEqual(self.path.read_bytes(), original)

    def test_record_clears_final_refresh_and_upserts_without_duplicate_attempts(self):
        updated = scenario(status="fail")
        result = self.update("scenario", updated)
        self.assertEqual(len(result["prs"][0]["scenarios"]), 1)
        self.assertEqual(len(result["prs"][0]["scenarios"][0]["attempts"]), 1)
        result = self.update("scenario", updated)
        self.assertEqual(len(result["prs"][0]["scenarios"][0]["attempts"]), 1)
        self.assertNotIn("current_head_sha", result["prs"][0])

    def test_atomic_replace_failure_retains_checkpoint_and_removes_temporary(self):
        original = self.path.read_bytes()
        with patch("ledger.os.replace", side_effect=OSError("replace failed")):
            with self.assertRaises(OSError):
                self.update("review", {"status": "blocked"})
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_full_sync_failure_preserves_checkpoint_without_fallback(self):
        original = self.path.read_bytes()
        with patch("ledger.fcntl.F_FULLFSYNC", 51, create=True), \
                patch("ledger.fcntl.fcntl", side_effect=OSError("device flush failed")), \
                patch("ledger.os.fsync") as fallback, patch("ledger.os.replace") as replace:
            with self.assertRaisesRegex(OSError, "device flush failed"):
                self.update("review", {"status": "blocked"})
        fallback.assert_not_called()
        replace.assert_not_called()
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_full_sync_precedes_replace_and_directory_sync(self):
        events = []
        import os
        import stat
        real_replace = os.replace
        def replace(source, target):
            events.append("replace")
            real_replace(source, target)
        def sync_directory(fd):
            self.assertTrue(stat.S_ISDIR(os.fstat(fd).st_mode))
            events.append("directory sync")
        with patch("ledger.fcntl.F_FULLFSYNC", 51, create=True), \
                patch("ledger.fcntl.fcntl", side_effect=lambda fd, command: events.append("full sync")), \
                patch("ledger.os.replace", side_effect=replace), \
                patch("ledger.os.fsync", side_effect=sync_directory):
            self.update("review", {"status": "blocked"})
        self.assertEqual(events, ["full sync", "replace", "directory sync"])
        self.assertEqual(self.load()["prs"][0]["review"]["status"], "blocked")

    def test_platform_without_full_sync_flushes_file_and_directory(self):
        import os
        import stat
        flushed = []
        with patch("ledger.fcntl.F_FULLFSYNC", None, create=True), \
                patch("ledger.fcntl.fcntl") as full_sync, \
                patch("ledger.os.fsync", side_effect=lambda fd: flushed.append(stat.S_ISDIR(os.fstat(fd).st_mode))):
            self.update("review", {"status": "blocked"})
        full_sync.assert_not_called()
        self.assertEqual(flushed, [False, True])

    def test_resume_reads_saved_pr_directly_and_reuses_verified_publication(self):
        self.publication()
        api = FakeGitHub()
        first = resume(self.path, api)
        second = resume(self.path, api)
        self.assertEqual(second["prs"][0]["publication"]["status"], "verified")
        self.assertEqual(first["prs"][0]["scenarios"], second["prs"][0]["scenarios"])
        self.assertEqual(api.calls, ["repos/Example/Application/pulls/7",
                                   "repos/Example/Application/pulls/7/reviews/123",
                                   "repos/Example/Application/pulls/7"] * 2)
        self.assertNotIn("revision_history", second["prs"][0])

    def test_drift_archives_results_and_keeps_unknown_cleanup_receipts(self):
        self.publication()
        self.data["prs"][0]["live"] = {"status": "complete", "runtime_commit": "b" * 40,
                                       "environment": "owned db", "cleanup": {"servers_stopped": False}}
        save(self.path, self.data)
        api = FakeGitHub(metadata={"state": "open", "base": {"sha": "c" * 40}, "head": {"sha": "b" * 40}})
        result = resume(self.path, api)["prs"][0]
        self.assertEqual(result["base_sha"], "c" * 40)
        self.assertEqual(result["review"]["status"], "pending")
        self.assertEqual(result["scenarios"], [])
        self.assertEqual(result["revision_history"][0]["publication"]["status"], "verified")
        self.assertFalse(result["pending_cleanup"][0]["live"]["cleanup"]["servers_stopped"])
        repeated = resume(self.path, api)["prs"][0]
        self.assertEqual(len(repeated["revision_history"]), 1)
        self.assertEqual(len(repeated["pending_cleanup"]), 1)

    def test_partial_revision_response_clears_previous_freshness(self):
        self.publication()
        result = resume(self.path, FakeGitHub(metadata={"state": "open", "head": {"sha": "b" * 40}}))["prs"][0]
        self.assertNotIn("current_head_sha", result)
        self.assertNotIn("current_base_sha", result)
        self.assertIn("resume_error", result)
        self.assertEqual(result["publication"]["status"], "failed")
        self.assertIn("review_url", result["publication"])
        self.assertEqual(result["scenarios"], self.data["prs"][0]["scenarios"])

    def test_malformed_base_is_checkpointed_as_refresh_error(self):
        result = resume(self.path, FakeGitHub(metadata={"state": "open", "head": {"sha": "b" * 40}, "base": ["unexpected"]}))["prs"][0]
        self.assertIn("resume_error", result)
        self.assertNotIn("current_head_sha", self.load()["prs"][0])

    def test_technical_exemption_does_not_create_cleanup_obligation(self):
        self.data["prs"][0]["live"] = {"status": "complete", "scope": "technical", "runtime_commit": "b" * 40,
                                       "cleanup_required": False, "cleanup_reason": "Read-only source check"}
        save(self.path, self.data)
        result = resume(self.path, FakeGitHub(metadata={"state": "open", "base": {"sha": "c" * 40}, "head": {"sha": "b" * 40}}))["prs"][0]
        self.assertNotIn("pending_cleanup", result)

    def test_archived_cleanup_requires_receipt_and_preserves_history(self):
        self.data["prs"][0]["live"] = {"status": "complete", "runtime_commit": "b" * 40,
                                       "environment": "owned db", "cleanup": {"servers_stopped": False}}
        save(self.path, self.data)
        resume(self.path, FakeGitHub(metadata={"state": "open", "base": {"sha": "c" * 40}, "head": {"sha": "b" * 40}}))
        with self.assertRaisesRegex(ValueError, "Cleanup requires"):
            self.update("cleanup", {"servers_stopped": True})
        receipt = {"servers_stopped": True, "database_dropped": None, "worktree_removed": False}
        result = self.update("cleanup", receipt)["prs"][0]
        self.assertEqual(len(result["pending_cleanup"]), 1)
        receipt = dict.fromkeys(receipt, True)
        result = self.update("cleanup", receipt)["prs"][0]
        self.assertEqual(result["pending_cleanup"], [])
        self.assertEqual(result["revision_history"][0]["live"]["cleanup"], receipt)
        self.assertEqual(result["revision_history"][0]["live"]["environment"], "owned db")
        self.assertEqual(self.update("cleanup", receipt)["prs"][0]["pending_cleanup"], [])
        with self.assertRaisesRegex(ValueError, "archived revision"):
            self.update("cleanup", receipt, base="d" * 40)

    def test_publication_error_does_not_erase_link_or_claim_verified(self):
        self.publication()
        result = resume(self.path, FakeGitHub(fail="/reviews/"))["prs"][0]
        self.assertEqual(result["publication"]["status"], "failed")
        self.assertIn("review_url", result["publication"])
        self.assertEqual(result["current_head_sha"], "b" * 40)

    def test_publication_must_be_submitted_change_request_on_same_revision(self):
        for change in ({"state": "PENDING"}, {"state": "DISMISSED"}, {"commit_id": "c" * 40}):
            self.publication()
            review = {"id": 123, "state": "CHANGES_REQUESTED", "commit_id": "b" * 40, **change}
            result = resume(self.path, FakeGitHub(review=review))["prs"][0]
            self.assertEqual(result["publication"]["status"], "failed")

    def test_reused_inline_comment_checks_identity_without_rejecting_outdated_threads(self):
        self.data["prs"][0]["publication"] = {"status": "reused", "reused_threads": [
            "https://github.com/Example/Application/pull/7#discussion_r456"]}
        save(self.path, self.data)
        comment = {"id": 456, "original_commit_id": "b" * 40, "commit_id": "c" * 40,
                   "pull_request_url": "https://api.github.com/repos/Example/Application/pulls/7"}
        self.assertEqual(resume(self.path, FakeGitHub(review=comment))["prs"][0]["publication"]["status"], "reused")
        comment.update(original_commit_id="a" * 40, commit_id="b" * 40, line=9)
        self.assertEqual(resume(self.path, FakeGitHub(review=comment))["prs"][0]["publication"]["status"], "reused")
        comment["line"] = None
        self.assertEqual(resume(self.path, FakeGitHub(review=comment))["prs"][0]["publication"]["status"], "reused")
        comment["original_commit_id"] = "b" * 40
        comment["pull_request_url"] = "https://api.github.com/repos/Example/Application/pulls/8"
        self.assertEqual(resume(self.path, FakeGitHub(review=comment))["prs"][0]["publication"]["status"], "failed")

    def test_matching_permalink_does_not_elevate_ambiguous_submission(self):
        for status in ("pending", "failed"):
            self.publication()
            self.data["prs"][0]["publication"]["status"] = status
            save(self.path, self.data)
            result = resume(self.path, FakeGitHub())["prs"][0]["publication"]
            self.assertEqual(result["status"], status)
            self.assertEqual(result["reconciliation"]["status"], "matched")
            self.assertIn("publisher readback", result["error"])

    def test_closed_pr_retains_observations_but_blocks_work(self):
        result = resume(self.path, FakeGitHub(metadata={"state": "closed", "base": {"sha": "a" * 40}, "head": {"sha": "b" * 40}}))["prs"][0]
        self.assertEqual(result["pr_state"], "closed")
        self.assertEqual(result["review"]["status"], "blocked")
        self.assertEqual(result["scenarios"], self.data["prs"][0]["scenarios"])

    def test_host_mismatch_fails_without_reading_other_server(self):
        self.data["prs"][0]["url"] = "https://other.example/Example/Application/pull/7"
        save(self.path, self.data)
        api = FakeGitHub()
        result = resume(self.path, api)["prs"][0]
        self.assertEqual(api.calls, [])
        self.assertIn("host", result["resume_error"])


if __name__ == "__main__":
    unittest.main()
