"""Offline tests for ledger operations, locking contract and summaries."""

import json
from pathlib import Path
import tempfile
import unittest

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
        self.assertIn("children=2/4 fixers=1/1", text.splitlines()[0])
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


if __name__ == "__main__":
    unittest.main()
