"""Offline tests for ledger operations, locking contract and summaries."""

import json
import io
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import ledger


def match(number, labels=()):
    return {"repository": "example/repo", "number": number, "title": f"PR {number}",
            "url": f"https://github.com/example/repo/pull/{number}", "headSha": f"sha{number}",
            "labels": list(labels), "signal": "actionable",
            "untouched": [{"kind": "inline", "date": "d", "url": "u", "author": "r"}],
            "feedback": {"kind": "inline", "date": "d", "url": f"f{number}", "author": "r",
                         "body": "Please fix"}}


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.run = ledger.init(self.temp.name)
        discovery = {"viewer": "owner", "collectedAt": "now", "complete": True,
                     "matches": [match(n) for n in range(1, 16)],
                     "lowSignal": [match(99)], "unavailable": []}
        self.discovery = Path(self.temp.name) / "discovery.json"
        self.discovery.write_text(json.dumps(discovery))

    def tearDown(self):
        self.temp.cleanup()

    def url(self, number):
        return f"https://github.com/example/repo/pull/{number}"

    def test_init_creates_unique_run_with_default_limits(self):
        data = ledger.read(self.run)
        self.assertEqual(data["schemaVersion"], ledger.SCHEMA_VERSION)
        self.assertEqual(data["resourceLimits"]["maximumActiveChildren"], 4)
        self.assertNotEqual(self.run, ledger.init(self.temp.name))

    def test_queue_freezes_one_batch_and_defers_the_rest(self):
        data = ledger.queue(self.run, self.discovery, 12)
        self.assertEqual(len(data["initialQueue"]), 12)
        self.assertEqual(len(data["deferred"]), 3)
        self.assertEqual(data["lowSignal"], [{"url": self.url(99), "feedback": "f99"}])
        entry = data["prs"][self.url(1)]
        self.assertEqual((entry["phase"], entry["status"], entry["fixRounds"]), ("analysis", "queued", 0))
        self.assertEqual(entry["triggeringFeedback"]["url"], "f1")
        with self.assertRaisesRegex(ledger.LedgerError, "already frozen"):
            ledger.queue(self.run, self.discovery, 12)

    def test_apply_batches_operations_in_one_write(self):
        ledger.queue(self.run, self.discovery, 2)
        url = self.url(1)
        data = ledger.apply(self.run, [
            {"pr": url, "set": {"phase": "fix", "status": "running", "child": {"taskId": "t1"}}},
            {"pr": url, "report": {"analysis": "pr-1-analysis.json"}},
            {"pr": url, "reviewer": {"login": "r", "reviewId": 5}},
            {"pr": url, "reviewer": {"login": "r", "addressed": True}},
            {"pr": url, "blocker": "needs CI"}, {"pr": url, "blocker": "needs CI"},
            {"run": {"executionAuthorizedAt": "now"}},
        ])
        entry = data["prs"][url]
        self.assertEqual(entry["reports"], {"analysis": "pr-1-analysis.json"})
        self.assertEqual(entry["reviewers"], [{"login": "r", "reviewId": 5, "addressed": True}])
        self.assertEqual(entry["blockers"], ["needs CI"])
        self.assertEqual(data["executionAuthorizedAt"], "now")

    def test_action_upsert_keeps_intent_and_stamps_observation(self):
        ledger.queue(self.run, self.discovery, 1)
        url = self.url(1)
        first = ledger.apply(self.run, {"pr": url, "action": {
            "key": "reply:1", "kind": "reply", "status": "in_flight"}})
        intent = first["prs"][url]["actions"][0]["intentAt"]
        data = ledger.apply(self.run, {"pr": url, "action": {
            "key": "reply:1", "status": "verified", "evidence": {"url": "x"}}})
        action = data["prs"][url]["actions"][0]
        self.assertEqual(action["intentAt"], intent)
        self.assertIn("observedAt", action)
        self.assertEqual(len(data["prs"][url]["actions"]), 1)

    def test_invalid_operations_leave_previous_ledger_intact(self):
        ledger.queue(self.run, self.discovery, 1)
        before = ledger.ledger_path(self.run).read_text()
        for op in ({"pr": self.url(1), "set": {"phase": "nonsense"}},
                   {"pr": self.url(1), "set": {"status": "done"}},
                   {"pr": self.url(50), "set": {"status": "queued"}},
                   {"pr": self.url(1), "action": {"status": "verified"}},
                   {"pr": self.url(1), "comment": {"id": 1, "body": "x" * 9000}}):
            with self.subTest(op=str(op)[:60]), self.assertRaises(ledger.LedgerError):
                ledger.apply(self.run, [{"pr": self.url(1), "set": {"status": "running"}}, op])
        self.assertEqual(ledger.ledger_path(self.run).read_text(), before)
        self.assertEqual(list(Path(self.run).glob(".ledger.*.tmp")), [])

    def test_create_adds_new_pr_once(self):
        ledger.queue(self.run, self.discovery, 1)
        data = ledger.apply(self.run, [{"pr": self.url(40), "create": match(40)},
                                       {"pr": self.url(40), "create": match(40)}])
        self.assertIn(self.url(40), data["prs"])
        self.assertEqual(len(data["prs"]), 2)

    def test_summary_reports_slots_and_filters(self):
        ledger.queue(self.run, self.discovery, 3)
        data = ledger.apply(self.run, [
            {"pr": self.url(1), "set": {"status": "running", "child": {"taskId": "a1"}}},
            {"pr": self.url(2), "set": {"phase": "fix", "status": "running"}},
        ])
        text = ledger.summary(data)
        self.assertIn("children=2/4 fixers=1/3", text.splitlines()[0])
        self.assertIn("example/repo#1\tanalysis/running\tsha1\tchild=a1", text)
        filtered = ledger.summary(data, phase="fix").splitlines()
        self.assertEqual(len(filtered), 3)

    def test_legacy_ledger_without_limits_or_report_dict_is_accepted(self):
        path = ledger.ledger_path(self.run)
        legacy = {"schemaVersion": 1, "runId": "old", "prs": {self.url(1): {
            **ledger.new_pr(match(1)), "reports": ["old.md"]}}}
        path.write_text(json.dumps(legacy))
        data = ledger.apply(self.run, {"pr": self.url(1), "report": {"analysis": "new.json"}})
        self.assertEqual(data["prs"][self.url(1)]["reports"],
                         {"legacy": ["old.md"], "analysis": "new.json"})
        self.assertEqual(data["resourceLimits"], ledger.DEFAULT_LIMITS)

    def test_progress_counts_mixed_states_once_and_monitor_does_not_use_child_slot(self):
        data = ledger.queue(self.run, self.discovery, 7)
        states = [("done", "completed"), ("fix", "running"),
                  ("analysis", "awaiting_authorization"), ("monitor", "running"),
                  ("analysis", "queued"), ("blocked", "blocked"), ("analysis", "skipped")]
        for number, (phase, status) in enumerate(states, 1):
            data["prs"][self.url(number)].update(phase=phase, status=status)
        snapshot = ledger.progress(data, self.run)
        self.assertEqual(snapshot["batchTotal"], 7)
        self.assertEqual(snapshot["counts"], dict.fromkeys(ledger.PROGRESS_STATES, 1))
        self.assertEqual((snapshot["activeChildren"], snapshot["activeFixers"]), (1, 1))
        self.assertIn("children=1/4 fixers=1/3", ledger.summary(data))
        self.assertEqual(snapshot["entries"][3]["state"], "waiting_ci")

    def test_terminal_label_cannot_hide_pending_actions_drafts_or_blockers(self):
        data = ledger.queue(self.run, self.discovery, 4)
        for entry in data["prs"].values():
            entry.update(phase="done", status="completed")
        data["prs"][self.url(1)]["actions"] = [{"key": "reply:1", "status": "planned"}]
        data["prs"][self.url(2)]["comments"] = [{"id": 5, "draftReply": "Please review"}]
        data["prs"][self.url(3)]["actions"] = [
            {"key": "request:reviewer", "status": "blocked", "condition": "Wait for final-head CI"}]
        data["prs"][self.url(4)]["comments"] = [
            {"id": 6, "draftReply": "Historical draft", "publish": False},
            {"id": 7, "draftReply": "Published", "replyUrl": "https://example/reply"}]
        rows = ledger.progress(data)["entries"]
        self.assertEqual([row["state"] for row in rows], ["queued", "queued", "blocked", "completed"])
        self.assertIn("reply:1", rows[0]["nextAction"])
        self.assertEqual(rows[1]["draftReplies"], 1)
        self.assertIn("Wait for final-head CI", rows[2]["nextAction"])

    def test_progress_keeps_push_verification_and_human_approval_separate(self):
        data = ledger.queue(self.run, self.discovery, 1)
        entry = data["prs"][self.url(1)]
        entry.update(currentHeadSha="a" * 40, publishedHeadSha="a" * 40,
                     phase="monitor", status="running", fixRounds=2)
        entry["reports"] = {"fixRound2": "proof.json", "publication": "journal.json"}
        row = ledger.progress(data)["entries"][0]
        self.assertEqual(row["headSha"], "a" * 40)
        self.assertEqual(row["verification"]["status"], "not summarized in ledger")
        self.assertEqual(row["publication"]["pushedHeadSha"], "a" * 40)
        self.assertEqual(row["publication"]["status"], "readback status not summarized in ledger")
        self.assertEqual(row["approval"], {"reviewDecision": "not recorded", "humanApproval": None,
                                          "headSha": None})
        text = ledger.progress_text(ledger.progress(data))
        self.assertIn("verification=see recorded reports", text)
        self.assertIn("approval=not recorded", text)
        self.assertEqual(len(text.splitlines()), 4)

    def test_progress_shows_publication_complete_while_ci_is_pending(self):
        data = ledger.queue(self.run, self.discovery, 1)
        entry = data["prs"][self.url(1)]
        entry.update(phase="monitor", status="running", publicationStatus="complete",
                     ci={"headSha": "abc", "status": "pending"})
        snapshot = ledger.progress(data)
        self.assertEqual(snapshot["counts"]["waiting_ci"], 1)
        self.assertEqual(snapshot["entries"][0]["ci"]["status"], "pending")
        self.assertEqual(snapshot["entries"][0]["publication"]["status"], "complete")
        self.assertIn("CI=pending", ledger.progress_text(snapshot))
        self.assertIn("publication=push not recorded, complete", ledger.progress_text(snapshot))
        self.assertIn("approval=not recorded", ledger.progress_text(snapshot))

    def test_progress_legacy_schema_is_read_only_and_does_not_create_lock(self):
        path = ledger.ledger_path(self.run)
        path.write_text(json.dumps({"schemaVersion": 1, "runId": "legacy", "prs": {
            self.url(1): {"url": self.url(1), "phase": "monitor", "status": "running",
                          "reports": ["old.md"]}}}))
        before = (path.read_bytes(), path.stat().st_mtime_ns)
        snapshot = ledger.progress(ledger.read(self.run), self.run)
        self.assertEqual(snapshot["counts"]["waiting_ci"], 1)
        self.assertEqual(snapshot["entries"][0]["approval"]["reviewDecision"], "not recorded")
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)
        self.assertFalse((self.run / "ledger.lock").exists())

    def test_latest_run_uses_recorded_timestamp_and_skips_unreadable_ledgers(self):
        ledger.queue(self.run, self.discovery, 1)
        older = ledger.read(self.run)
        older["updatedAt"] = "2026-01-01T00:00:00Z"
        ledger.ledger_path(self.run).write_text(json.dumps(older))
        latest = ledger.init(self.temp.name)
        data = ledger.read(latest)
        data["updatedAt"] = "2026-02-01T00:00:00+00:00"
        ledger.ledger_path(latest).write_text(json.dumps(data))
        for suffix, content in [("broken", "{"), ("invalid", "[]"),
                                ("bad-date", '{"updatedAt":"no date"}')]:
            candidate = Path(self.temp.name) / f"respond-all-{suffix}"
            candidate.mkdir()
            (candidate / "ledger.json").write_text(content)
        self.assertEqual(ledger.latest_run(self.temp.name), latest)
        with tempfile.TemporaryDirectory() as empty, self.assertRaisesRegex(ledger.LedgerError, "No readable"):
            ledger.latest_run(empty)

    def test_progress_cli_json_and_latest_are_read_only(self):
        ledger.queue(self.run, self.discovery, 1)
        path = ledger.ledger_path(self.run)
        before = (path.read_bytes(), path.stat().st_mtime_ns)
        output = io.StringIO()
        with patch("sys.argv", ["ledger.py", "progress", "--latest", "--root", self.temp.name, "--json"]), \
                redirect_stdout(output):
            self.assertEqual(ledger.main(), 0)
        snapshot = json.loads(output.getvalue())
        self.assertEqual(snapshot["runDir"], str(self.run))
        self.assertEqual(snapshot["counts"]["queued"], 1)
        self.assertIn("no live GitHub", snapshot["source"])
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)


if __name__ == "__main__":
    unittest.main()
