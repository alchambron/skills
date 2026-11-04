#!/usr/bin/env python3
"""Offline tests for run token accounting from synthetic Codex rollouts."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import token_usage

URL = "https://github.com/EnerZam/JouleMV/pull/{}"


def rollout(directory, name, identifier, start, end, total, run_dir=None, parent=None, agent=None, brief=None, extra=""):
    source = {"subagent": {"thread_spawn": {"parent_thread_id": parent, "agent_path": agent}}} if parent else "vscode"
    lines = [{"timestamp": start, "type": "session_meta", "payload": {"id": identifier, "source": source}}]
    if brief:
        lines.append({"timestamp": start, "type": "response_item",
                      "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": brief}]}})
    if run_dir or extra:
        lines.append({"timestamp": start, "type": "response_item",
                      "payload": {"type": "custom_tool_call", "input": f"cat {run_dir or ''}/results.json {extra}"}})
    for moment, used, percent in ((start, total // 2, 10.0), (end, total, 12.5)):
        lines.append({"timestamp": moment, "type": "event_msg", "payload": {
            "type": "token_count", "rate_limits": {"primary": {"used_percent": percent}},
            "info": {"total_token_usage": {"input_tokens": used - 100, "cached_input_tokens": used - 1000,
                                           "output_tokens": 100, "reasoning_output_tokens": 10, "total_tokens": used}}}})
    (directory / f"rollout-{name}.jsonl").write_text("\n".join(json.dumps(line) for line in lines) + "\n")


class TokenUsageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.run_dir = root / "runs" / "review-all-1"
        self.run_dir.mkdir(parents=True)
        (self.run_dir / "results.json").write_text(json.dumps({
            "collected_at": "2026-11-04T19:51:00Z",
            "prs": [{"repository": "EnerZam/JouleMV", "number": n, "url": URL.format(n)} for n in (10, 11)]}))
        self.sessions = root / "sessions"
        day = self.sessions / "2026" / "11" / "04"
        day.mkdir(parents=True)
        run = str(self.run_dir)
        rollout(day, "a", "coord", "2026-11-04T19:50:00Z", "2026-11-04T21:00:00Z", 1000, run_dir=run,
                brief="$review-all", extra=URL.format(10) + " " + URL.format(11))
        # Static review child: whole batch, split by references (3 for #10, 1 for #11).
        rollout(day, "b", "static", "2026-11-04T19:52:00Z", "2026-11-04T20:00:00Z", 400, parent="coord", agent="/root/standards",
                extra=" ".join([URL.format(10)] * 3 + ["EnerZam/JouleMV#11"]))
        # Delegated test child: owns #10 by its plaintext brief, though it lists #11's files too.
        rollout(day, "c", "delegated", "2026-11-04T19:55:00Z", "2026-11-04T20:40:00Z", 3000, run_dir=run,
                brief=f"Act as the test sub-agent. Own ONLY acceptance for {URL.format(10)}",
                extra="EnerZam/JouleMV/11/diff EnerZam/JouleMV/11/spec EnerZam/JouleMV/11/x")
        # Native scenario child below the delegated one: named after #11 and never prints RUN_DIR.
        rollout(day, "d", "scenario", "2026-11-04T20:05:00Z", "2026-11-04T20:20:00Z", 600, parent="delegated",
                agent="/root/scenario_JouleMV_11_a", extra=URL.format(10))
        # Unrelated sessions: a later analysis of the run, and another thread's sub-agent.
        rollout(day, "e", "later", "2026-11-04T22:00:00Z", "2026-11-04T22:10:00Z", 9000, run_dir=run)
        rollout(day, "f", "foreign", "2026-11-04T20:00:00Z", "2026-11-04T20:10:00Z", 7000, parent="elsewhere",
                agent="/root/scenario_JouleMV_10")

    def report(self):
        run_dir, prs, coordinator, sessions = token_usage.collect(self.run_dir, self.sessions)
        return token_usage.attribute(prs, coordinator, sessions)

    def test_run_sessions_are_coordinator_delegates_and_their_sub_agents_only(self):
        report = self.report()
        self.assertEqual(sorted(row["session"] for row in report["sessions"]), ["coord", "delegated", "scenario", "static"])
        self.assertEqual(report["total"]["total_tokens"], 1000 + 400 + 3000 + 600)

    def test_task_identity_beats_references_and_batch_children_split_by_references(self):
        report = self.report()
        rows = {row["session"]: row for row in report["sessions"]}
        self.assertEqual((rows["delegated"]["prs"], rows["delegated"]["attribution"]), ({"EnerZam/JouleMV#10": 1.0}, "task"))
        self.assertEqual(rows["scenario"]["prs"], {"EnerZam/JouleMV#11": 1.0})
        self.assertEqual(rows["static"]["prs"], {"EnerZam/JouleMV#10": 0.75, "EnerZam/JouleMV#11": 0.25})
        self.assertEqual(rows["coord"]["attribution"], "shared")
        prs = {row["pr"]: row for row in report["prs"]}
        self.assertEqual(prs["EnerZam/JouleMV#10"]["direct"]["total_tokens"], 3000 + 300)
        self.assertEqual(prs["EnerZam/JouleMV#11"]["direct"]["total_tokens"], 600 + 100)
        self.assertEqual(prs["EnerZam/JouleMV#11"]["withSharedSplit"]["total_tokens"], 700 + 500)
        self.assertEqual(report["shared"]["total_tokens"], 1000)
        self.assertEqual(report["weeklyLimitPercent"]["delta"], 2.5)

    def test_cli_writes_report_and_prints_markdown_table(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = token_usage.main(["--run-dir", str(self.run_dir), "--sessions-root", str(self.sessions), "--markdown"])
        self.assertEqual(code, 0)
        table = out.getvalue()
        self.assertIn("| EnerZam/JouleMV#10 | 0.0M |", table)
        self.assertIn("**Total** (4 sessions)", table)
        saved = json.loads((self.run_dir / "token-usage.json").read_text())
        self.assertEqual(saved["coordinatorSession"], "coord")

    def test_missing_coordinator_is_an_error(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(token_usage.main(["--run-dir", str(self.run_dir), "--sessions-root",
                                               str(Path(self.temporary.name) / "empty")]), 2)


if __name__ == "__main__":
    unittest.main()
