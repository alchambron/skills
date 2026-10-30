"""Offline tests for plan and outcome rendering."""

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import render_report


def report(**extra):
    return {"repository": "example/repo", "number": 7, "title": "Fix | pipes",
            "url": "https://github.com/example/repo/pull/7", "headSha": "abc", "baseSha": "def",
            "labels": ["risk:limited", "priority:high", "backend"], "summary": "One fix, one answer",
            "plan": ["Guard null"], "verification": ["Focused test"], "blockers": [],
            "coverage": {"humanUntouched": 2},
            "comments": [
                {"id": 11, "url": "u11", "reviewer": "alice", "status": "open", "summary": "Null crash",
                 "disposition": "Fix", "importance": "Important", "value": "Valuable",
                 "solution": "Guard null", "draftReply": "Fixed.", "inReplyTo": 11},
                {"id": "review-5", "url": "u5", "reviewer": "bob", "status": "open",
                 "disposition": "No change — intended", "solution": "Explain", "draftReply": "Intended."}],
            **extra}


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, data, name="pr-7-analysis.json"):
        path = self.dir / name
        path.write_text(json.dumps(data))
        return path

    def run_render(self, *args):
        output = io.StringIO()
        with patch.object(sys, "argv", ["render_report.py", *map(str, args)]), contextlib.redirect_stdout(output):
            self.assertEqual(render_report.main(), 0)
        return output.getvalue()

    def test_labels_split_into_risk_priority_and_other(self):
        self.assertEqual(render_report.split_labels(report()), ("risk:limited", "priority:high", "backend"))
        self.assertEqual(render_report.split_labels(report(labels=[])), ("Not labeled", "Not labeled", "None"))
        risk, priority, _ = render_report.split_labels(report(labels=["priority:low", "Urgent Ticket"]))
        self.assertEqual(priority, "Conflicting labels: priority:low, Urgent Ticket (legacy)")

    def test_plan_prints_summary_and_writes_full_tables(self):
        stdout = self.run_render(self.write(report()))
        self.assertIn("| PR / other labels | Risk | Priority | Feedback and suggested plan |", stdout)
        self.assertIn("Fix 1, No change 1", stdout)
        self.assertNotIn("Draft replies", stdout)
        full = (self.dir / "combined-plan.md").read_text()
        self.assertIn("Fix \\| pipes", full)
        self.assertIn("| Comment | Important? | Valuable? | Solution for this comment |", full)
        self.assertIn("> Intended.", full)

    def test_outcomes_join_publication_journal(self):
        path = self.write(report())
        (self.dir / "pr-7-publication.json").write_text(json.dumps({
            "status": "complete",
            "replies": {"11": {"status": "verified", "outcome": "posted", "url": "https://r/11"}},
            "reviewers": {"alice": {"status": "verified", "outcome": "Requested"}}}))
        stdout = self.run_render(path, "--outcomes")
        self.assertIn("alice: Requested", stdout)
        self.assertIn("complete; replies 1/1", stdout)
        full = (self.dir / "combined-outcomes.md").read_text()
        self.assertIn("[posted](https://r/11)", full)
        self.assertIn("| Draft", full)


if __name__ == "__main__":
    unittest.main()
