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
            "url": report()["url"],
            "status": "complete",
            "replies": {"11": {"status": "verified", "outcome": "posted", "url": "https://r/11"}},
            "reviewers": {"alice": {"status": "verified", "outcome": "Requested"}}}))
        stdout = self.run_render(path, "--outcomes")
        self.assertIn("alice: Requested", stdout)
        self.assertIn("complete; replies 1/1", stdout)
        full = (self.dir / "combined-outcomes.md").read_text()
        self.assertIn("[posted](https://r/11)", full)
        self.assertIn("| Draft", full)

    def test_outcomes_keep_unverified_implementation_as_a_plan(self):
        path = self.write(report())
        self.run_render(path, "--outcomes")
        full = (self.dir / "combined-outcomes.md").read_text()
        self.assertIn("Implementation outcome not recorded. Planned Fix", full)
        self.assertIn("Workflow status not recorded", full)

    def test_same_number_in_different_repositories_uses_own_journal(self):
        paths = []
        for repository in ("one/repo", "two/repo"):
            folder = self.dir / "prs" / repository
            folder.mkdir(parents=True)
            data = report(repository=repository, url=f"https://github.com/{repository}/pull/7")
            path = folder / "pr-7-analysis.json"
            path.write_text(json.dumps(data))
            (folder / "pr-7-publication.json").write_text(json.dumps({
                "url": data["url"], "status": "complete", "replies": {},
                "reviewers": {repository.split('/')[0]: {"outcome": "Requested"}}}))
            paths.append(path)
        output = self.run_render(*paths, "--outcomes", "--out", self.dir / "out.md")
        self.assertIn("one: Requested", output.splitlines()[2])
        self.assertNotIn("two: Requested", output.splitlines()[2])
        self.assertIn("two: Requested", output.splitlines()[3])

    def test_foreign_legacy_journal_is_never_joined(self):
        path = self.write(report())
        (self.dir / "pr-7-publication.json").write_text(json.dumps({
            "url": "https://github.com/other/repo/pull/7", "status": "complete"}))
        output = self.run_render(path, "--outcomes")
        self.assertIn("not published", output)
        self.assertNotIn("complete;", output)

    def test_run_dir_joins_recorded_outcomes_without_rebuilding_reports(self):
        path = self.write(report())
        (self.dir / "ledger.json").write_text(json.dumps({"prs": {report()["url"]: {
            "reports": {"analysis": path.name}, "phase": "blocked", "status": "blocked",
            "currentHeadSha": "pushed-sha", "ci": {"headSha": "pushed-sha", "status": "blocked"},
            "blockers": ["Actions capacity"], "comments": [
                {"id": 11, "outcome": "Fixed at pushed-sha; focused regression passed"}]}}}))
        stdout = self.run_render("--run-dir", self.dir, "--outcomes")
        full = (self.dir / "combined-outcomes.md").read_text()
        self.assertIn("blocked/blocked", stdout)
        self.assertIn("Fixed at pushed-sha; focused regression passed", full)
        self.assertIn("Actions capacity", full)
        self.assertIn("Current head: `pushed-sha`", full)
        self.assertIn("Publication does not establish passing CI", full)

    def test_run_dir_rejects_wrong_report_identity(self):
        path = self.write(report())
        (self.dir / "ledger.json").write_text(json.dumps({"prs": {
            "https://github.com/other/repo/pull/7": {"reports": {"analysis": path.name}}}}))
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            render_report.load_run_reports(self.dir)

    def test_run_dir_honors_recorded_publication_path(self):
        path = self.write(report())
        custom = self.dir / "readbacks" / "recorded.json"
        custom.parent.mkdir()
        custom.write_text(json.dumps({"url": report()["url"], "expectedHeadSha": "published-head",
                                      "status": "complete", "replies": {}, "reviewers": {}}))
        (self.dir / "ledger.json").write_text(json.dumps({"prs": {report()["url"]: {
            "reports": {"analysis": path.name, "publication": "readbacks/recorded.json"},
            "phase": "monitor", "status": "running"}}}))
        output = self.run_render("--run-dir", self.dir, "--outcomes")
        self.assertIn("complete; replies 0/0; head published-h", output)
        custom.write_text(json.dumps({"url": "https://github.com/foreign/repo/pull/7"}))
        with self.assertRaisesRegex(render_report.PublishError, "identity mismatch"):
            self.run_render("--run-dir", self.dir, "--outcomes")

    def test_outcomes_preserve_actions_and_drafts_under_a_terminal_label(self):
        path = self.write(report())
        (self.dir / "ledger.json").write_text(json.dumps({"prs": {report()["url"]: {
            "reports": {"analysis": path.name}, "phase": "done", "status": "completed",
            "actions": [{"key": "resolve-thread", "status": "planned", "condition": "needs authorization"}],
            "comments": [{"id": 11, "draftReply": "New draft requiring a decision", "replyStatus": "draft"}]}}}))
        output = self.run_render("--run-dir", self.dir, "--outcomes")
        self.assertIn("queued; recorded done/completed; outstanding actions=1; drafts=1", output)
        full = (self.dir / "combined-outcomes.md").read_text()
        self.assertIn("Action resolve-thread: planned; needs authorization", full)
        self.assertIn("New draft requiring a decision", full)
        self.assertIn("outstanding draft remains", full)

    def test_exhaustive_appendix_loads_each_repositories_own_inventory(self):
        paths = []
        for repository in ("one/repo", "two/repo"):
            folder = self.dir / "prs" / repository
            folder.mkdir(parents=True)
            data = report(repository=repository, url=f"https://github.com/{repository}/pull/7",
                          includeCoverageAppendix=True, coverageInventory="coverage.json")
            path = folder / "pr-7-analysis.json"
            path.write_text(json.dumps(data))
            (folder / "coverage.json").write_text(json.dumps([
                {"id": f"{repository}-historical", "url": "https://comment", "category": "resolved",
                 "reason": "Already answered"}]))
            paths.append(path)
        self.run_render(*paths, "--out", self.dir / "plan.md")
        full = (self.dir / "plan.md").read_text()
        self.assertEqual(full.count("one/repo-historical"), 1)
        self.assertEqual(full.count("two/repo-historical"), 1)


if __name__ == "__main__":
    unittest.main()
