#!/usr/bin/env python3
"""Offline tests for cross-run review history: selection of PRs that still need work, and recording."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import collect_reviews as collector
import history
from test_collect_reviews import FakeGitHub, hit, metadata

HEAD, BASE, NEW_HEAD = "b" * 40, "a" * 40, "c" * 40


def pull(number=10, repository="EnerZam/JouleMV", head=HEAD, requested=("reviewer",), **overrides):
    value = metadata(repository, number, **overrides)
    value["number"] = number
    value["head"]["sha"] = head
    value["requested_reviewers"] = [{"login": login} for login in requested]
    value["created_at"] = value["updated_at"] = f"2026-11-0{number % 9 + 1}T00:00:00Z"
    return value


def results_pr(number=10, review="clean", publication="not_required", live="complete", fresh=True, head=HEAD):
    return {"repository": "EnerZam/JouleMV", "number": number, "url": f"https://github.com/EnerZam/JouleMV/pull/{number}",
            "title": "Feature", "base_sha": BASE, "head_sha": head,
            "current_base_sha": BASE if fresh else None, "current_head_sha": head if fresh else None,
            "review": {"status": review}, "publication": {"status": publication}, "live": {"status": live},
            "scenarios": []}


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        patcher = patch.dict(os.environ, {"REVIEW_STATE_DIR": str(self.root / "state")})
        patcher.start()
        self.addCleanup(patcher.stop)

    def state(self, prs):
        history.save_json(history.state_dir() / "state.json", {"version": 1, "prs": prs})

    def select(self, api, force=False):
        scratch = self.root / "scratch"
        scratch.mkdir(exist_ok=True)
        with history.locked() as directory:
            state = history.read_state(directory)
        return history.select(api, state, [], force, scratch)

    def api(self, pulls):
        api = FakeGitHub([hit(number=value["number"]) for value in pulls])
        for value in pulls:
            api.responses[f"repos/EnerZam/JouleMV/pulls/{value['number']}"] = value
        return api

    def test_complete_pr_at_same_head_is_skipped_and_new_pr_selected(self):
        self.state({"EnerZam/JouleMV#10": {"reviewedHead": HEAD, "complete": True, "outcome": "clean"}})
        result = self.select(self.api([pull(10), pull(11)]))
        self.assertEqual([c["key"] for c in result["selected"]], ["EnerZam/JouleMV#11"])
        self.assertEqual(result["skipped"][0]["reason"], "unchanged")

    def test_moved_head_is_incremental_and_listed_first(self):
        self.state({"EnerZam/JouleMV#10": {"reviewedHead": HEAD, "reviewedBase": BASE, "complete": True,
                                           "outcome": "clean"}})
        result = self.select(self.api([pull(11), pull(10, head=NEW_HEAD)]))
        self.assertEqual([c["kind"] for c in result["selected"]], ["moved", "new"])
        self.assertEqual(result["selected"][0]["basis"], {"mode": "incremental", "fromHead": HEAD, "fromBase": BASE,
                                                          "previousOutcome": "clean", "previousRun": None})

    def test_rerequest_only_after_a_submitted_request_changes(self):
        self.state({"EnerZam/JouleMV#10": {"reviewedHead": HEAD, "complete": True, "outcome": "issues",
                                           "publication": "verified"},
                    "EnerZam/JouleMV#11": {"reviewedHead": HEAD, "complete": True, "outcome": "issues",
                                           "publication": "reused"}})
        result = self.select(self.api([pull(10), pull(11)]))
        self.assertEqual([(c["key"], c["kind"]) for c in result["selected"]], [("EnerZam/JouleMV#10", "rerequested")])
        self.assertEqual(result["skipped"][0]["reason"], "unchanged")

    def test_incomplete_attempt_is_retried(self):
        self.state({"EnerZam/JouleMV#10": {"reviewedHead": HEAD, "complete": False, "outcome": "clean"}})
        self.assertEqual(self.select(self.api([pull(10)]))["selected"][0]["kind"], "retry")

    def test_unfinished_live_test_resumes_after_request_cleared(self):
        self.state({"EnerZam/JouleMV#10": {"reviewedHead": HEAD, "complete": False, "outcome": "issues",
                                           "liveStatus": "pending"},
                    "EnerZam/JouleMV#12": {"reviewedHead": HEAD, "complete": False, "outcome": "issues",
                                           "liveStatus": "blocked"}})
        api = self.api([])
        api.responses["repos/EnerZam/JouleMV/pulls/10"] = pull(10, requested=())
        api.responses["repos/EnerZam/JouleMV/pulls/12"] = pull(12, requested=())
        result = self.select(api)
        self.assertEqual([(c["key"], c["basis"]["stage"]) for c in result["selected"]], [("EnerZam/JouleMV#10", "live")])

    def test_drafts_and_stale_requests_are_skipped(self):
        result = self.select(self.api([pull(10, draft=True), pull(11, requested=())]))
        self.assertEqual(result["selected"], [])
        self.assertEqual({s["reason"] for s in result["skipped"]}, {"draft", "request_no_longer_current"})

    def test_all_flag_ignores_history(self):
        self.state({"EnerZam/JouleMV#10": {"reviewedHead": HEAD, "complete": True, "outcome": "clean"}})
        result = self.select(self.api([pull(10)]), force=True)
        self.assertEqual(result["selected"][0]["basis"], {"mode": "full"})

    def test_select_cli_reports_nothing_new(self):
        self.state({"EnerZam/JouleMV#10": {"reviewedHead": HEAD, "complete": True, "outcome": "clean"}})
        with patch.object(history, "GitHub", return_value=self.api([pull(10)])), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(history.main(["select"]), history.NOTHING_NEW)
        printed = json.loads(out.getvalue())
        self.assertEqual((printed["selected"], printed["skipped"]), ([], {"EnerZam/JouleMV#10": "unchanged"}))

    def write_results(self, prs):
        run = self.root / "run"
        run.mkdir(exist_ok=True)
        path = run / "results.json"
        path.write_text(json.dumps({"version": 1, "login": "reviewer", "collected_at": "x", "prs": prs}))
        return path

    def test_record_marks_complete_fresh_outcomes(self):
        changes = history.record(self.write_results([results_pr(10), results_pr(11, review="issues", publication="verified")]))
        state = history.read_state(history.state_dir())["prs"]
        self.assertTrue(all(c["complete"] for c in changes))
        self.assertEqual(state["EnerZam/JouleMV#10"]["reviewedHead"], HEAD)
        self.assertEqual(state["EnerZam/JouleMV#11"]["outcome"], "issues")

    def test_record_blocked_live_keeps_review_but_stays_incomplete(self):
        history.record(self.write_results([results_pr(10, live="blocked")]))
        entry = history.read_state(history.state_dir())["prs"]["EnerZam/JouleMV#10"]
        self.assertEqual((entry["reviewedHead"], entry["complete"], entry["liveStatus"]), (HEAD, False, "blocked"))

    def test_record_unfinished_or_stale_review_is_not_reviewed(self):
        history.record(self.write_results([results_pr(10, review="pending", publication="pending", live="pending"),
                                           results_pr(11, fresh=False)]))
        state = history.read_state(history.state_dir())["prs"]
        self.assertNotIn("reviewedHead", state["EnerZam/JouleMV#10"])
        self.assertNotIn("reviewedHead", state["EnerZam/JouleMV#11"])


class SelectionCollectionTests(unittest.TestCase):
    def test_collector_uses_selection_without_search_and_keeps_basis(self):
        with tempfile.TemporaryDirectory() as temporary:
            api = FakeGitHub([])
            selection = {"selected": [{"key": "EnerZam/JouleMV#10", "repository": "EnerZam/JouleMV", "number": 10,
                                       "url": "https://github.com/EnerZam/JouleMV/pull/10", "kind": "moved",
                                       "basis": {"mode": "incremental", "fromHead": HEAD}}]}
            snapshot, _ = collector.collect(api, Path(temporary) / "run", selection=selection)
            self.assertFalse(any(endpoint == "search/issues" for endpoint, _ in api.calls))
            self.assertEqual(snapshot["prs"][0]["review_basis"], {"mode": "incremental", "fromHead": HEAD, "kind": "moved"})
            self.assertTrue(snapshot["collection"]["complete"])


if __name__ == "__main__":
    unittest.main()
