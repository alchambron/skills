"""Offline tests for respond-all feedback history and the shared heavy-command lock."""

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import history
import ledger

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "respond" / "scripts"))
import heavy_lock  # noqa: E402

URL = "https://github.com/example/repo/pull/{}"


def item(number=1, day=20):
    return {"kind": "comment", "date": f"2026-10-{day:02d}T12:00:00Z",
            "url": URL.format(number) + f"#issuecomment-{day}", "author": "reviewer"}


def match(number=1, items=None):
    items = items or [item(number)]
    return {"repository": "example/repo", "number": number, "title": "Example", "url": URL.format(number),
            "headSha": "abc", "reviewDecision": None, "signal": "actionable",
            "feedback": {**items[-1], "body": "Please fix"}, "untouched": items, "labels": []}


def discovery(*matches):
    return {"viewer": "owner", "collectedAt": "2026-11-04T00:00:00Z", "scanned": len(matches),
            "matches": list(matches), "lowSignal": [], "excluded": [], "unavailable": [], "complete": True}


class HistoryBase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        patcher = patch.dict(os.environ, {"RESPOND_STATE_DIR": str(root / "state"), "CODEX_HOME": str(root / "codex"),
                                          "REVIEW_STATE_ROOT": str(root / "review-state")})
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_with(self, entries):
        run_dir = ledger.init(None)
        ops = [{"pr": url, "create": match(int(url.rsplit("/", 1)[1]))} for url in entries]
        ops += [{"pr": url, "set": fields} for url, fields in entries.items()]
        ledger.apply(run_dir, ops)
        return run_dir

    def state(self):
        return history.read_state(history.state_dir())["prs"]


class HistoryTests(HistoryBase):
    def test_settled_entries_mark_feedback_handled_and_filter_drops_it(self):
        run_dir = self.run_with({URL.format(1): {"status": "skipped", "phase": "done"},
                                 URL.format(2): {"status": "completed", "phase": "done"},
                                 URL.format(3): {"status": "blocked", "phase": "blocked"},
                                 URL.format(4): {"status": "awaiting_authorization"}})
        history.record([run_dir])
        filtered, dropped = history.filter_discovery(discovery(match(1), match(2), match(3), match(4)),
                                                     history.read_state(history.state_dir()))
        self.assertEqual([m["number"] for m in filtered["matches"]], [3, 4])
        self.assertEqual({d["url"] for d in dropped}, {URL.format(1), URL.format(2)})
        self.assertEqual(filtered["excluded"][-1]["reason"], "already_handled")

    def test_new_or_edited_feedback_survives_the_filter(self):
        run_dir = self.run_with({URL.format(1): {"status": "skipped", "phase": "done"}})
        history.record([run_dir])
        filtered, _ = history.filter_discovery(discovery(match(1, [item(1), item(1, 22)])),
                                               history.read_state(history.state_dir()))
        self.assertEqual([i["date"][:10] for i in filtered["matches"][0]["untouched"]], ["2026-10-22"])

    def test_outstanding_actions_keep_feedback_open(self):
        run_dir = self.run_with({URL.format(1): {"status": "completed", "phase": "done"}})
        ledger.apply(run_dir, [{"pr": URL.format(1), "action": {"key": "reply", "status": "planned"}}])
        history.record([run_dir])
        self.assertEqual(self.state()[URL.format(1)]["handled"], [])

    def test_older_ledger_does_not_roll_back_newer_record(self):
        older = self.run_with({URL.format(1): {"status": "blocked", "phase": "blocked"}})
        time.sleep(0.01)
        newer = self.run_with({URL.format(1): {"status": "skipped", "phase": "done"}})
        history.record([newer])
        self.assertEqual(history.record([older]), [{"url": URL.format(1), "outcome": "superseded"}])
        self.assertEqual(self.state()[URL.format(1)]["outcome"], "handled")

    def test_filter_cli_writes_queue_ready_discovery(self):
        source = Path(self.temporary.name) / "discovery.json"
        target = Path(self.temporary.name) / "filtered.json"
        source.write_text(json.dumps(discovery(match(1))))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(history.main(["filter", "--discovery", str(source), "--out", str(target)]), 0)
        run_dir = ledger.init(None)
        data = ledger.queue(run_dir, target, None)
        self.assertEqual(data["initialQueue"], [URL.format(1)])


class HeavyLockTests(HistoryBase):
    def test_single_slot_serialises_commands(self):
        script = Path(heavy_lock.__file__)
        command = [sys.executable, str(script), "--slots", "1", "--", sys.executable, "-c", "import time; time.sleep(0.6)"]
        started = time.monotonic()
        first = subprocess.Popen(command, stderr=subprocess.DEVNULL)
        time.sleep(0.15)
        second = subprocess.run(command, stderr=subprocess.DEVNULL)
        first.wait()
        self.assertEqual((first.returncode, second.returncode), (0, 0))
        self.assertGreaterEqual(time.monotonic() - started, 1.1)

    def test_exit_code_passes_through(self):
        self.assertEqual(heavy_lock.main(["--", sys.executable, "-c", "raise SystemExit(7)"]), 7)


if __name__ == "__main__":
    unittest.main()
