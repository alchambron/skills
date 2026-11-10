"""Offline checks that incomplete skill installations cannot start batch work."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import agent_run
import check_dependencies as guard
import collect_reviews
import history
import scenario_run


class DependencyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.skills = self.root / "skills"
        self.skills.mkdir()
        environment = patch.dict(os.environ, {"REVIEW_SKILLS_DIRS": str(self.skills),
                                              "REVIEW_STATE_DIR": str(self.root / "state")})
        environment.start()
        self.addCleanup(environment.stop)

    def install(self, browser_folder="joulemv-browser-testing"):
        for name, folders, _, resources in guard.DEPENDENCIES:
            folder = self.skills / (browser_folder if name == "browser-testing" else folders[0])
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "SKILL.md").write_text(f"---\nname: {name}\ndescription: Test fixture\n---\nInstructions.\n")
            for resource in resources:
                target = folder / resource
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("Supporting instructions.\n")

    def test_empty_installation_is_blocked_with_install_links(self):
        result = guard.check()
        self.assertFalse(result["ok"])
        self.assertEqual(len(result["dependencies"]), 5)
        for item in result["dependencies"]:
            self.assertEqual(item["status"], "missing_or_invalid")
            self.assertIn(item["url"], guard.notice(result))
            self.assertTrue(item["url"].startswith("https://github.com/alchambron/skills/tree/main/"))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(guard.main(["--skills-root", str(self.skills)]), 2)
        self.assertEqual(json.loads(output.getvalue())["status"], "installation_required")

    def test_complete_installation_and_both_browser_folder_names(self):
        for folder in ("joulemv-browser-testing", "browser-testing"):
            with self.subTest(folder=folder):
                self.install(folder)
                result = guard.check()
                self.assertTrue(result["ok"])
                self.assertTrue(guard.require_dependencies())
                for item in result["dependencies"]:
                    self.assertTrue(Path(item["path"]).is_relative_to(self.skills.resolve()))
                (self.skills / folder / "SKILL.md").unlink()

    def test_wrong_skill_name_and_missing_reference_are_rejected(self):
        self.install()
        (self.skills / "code-review/SKILL.md").write_text("---\nname: unrelated\n---\nWrong skill.\n")
        (self.skills / "github-inline-review/references/github-api.md").unlink()
        missing = {item["name"]: item for item in guard.check()["dependencies"]
                   if item["status"] != "available"}
        self.assertEqual(set(missing), {"code-review", "github-inline-review"})
        self.assertIn("github-api.md", missing["github-inline-review"]["searched"][0]["reason"])

    def test_explicit_empty_root_does_not_use_author_installation(self):
        self.install()
        empty = self.root / "empty"
        empty.mkdir()
        self.assertFalse(guard.check([empty])["ok"])
        self.assertTrue(guard.check([empty, self.skills])["ok"])

    def test_batch_commands_stop_before_github_or_state_creation(self):
        with contextlib.redirect_stderr(io.StringIO()), \
                patch.object(history, "GitHub") as history_api, \
                patch.object(collect_reviews, "GitHub") as collector_api:
            self.assertEqual(history.main(["select"]), 2)
            self.assertEqual(collect_reviews.main(["--output", str(self.root / "run")]), 2)
        history_api.assert_not_called()
        collector_api.assert_not_called()
        self.assertFalse((self.root / "state").exists())
        self.assertFalse((self.root / "run").exists())

    def test_runtime_and_scenario_commands_stop_before_start_or_install(self):
        with contextlib.redirect_stderr(io.StringIO()), \
                patch("sys.argv", ["agent_run.py", "start"]), \
                patch.object(agent_run, "start") as start, \
                patch.object(scenario_run, "run") as run, \
                patch.object(scenario_run, "setup") as setup:
            self.assertEqual(agent_run.main(), 2)
            self.assertEqual(scenario_run.main(["run", "--script", "unused.mjs", "--out", "unused"]), 2)
            self.assertEqual(scenario_run.main(["setup"]), 2)
        start.assert_not_called()
        run.assert_not_called()
        setup.assert_not_called()

    def test_cleanup_remains_available_without_dependencies(self):
        with patch("sys.argv", ["agent_run.py", "stop"]), patch.object(agent_run, "stop") as stop:
            self.assertEqual(agent_run.main(), 0)
        stop.assert_called_once()

    def test_missing_checker_itself_blocks_start_but_not_cleanup_dispatch(self):
        script = self.root / "agent_run.py"
        script.write_text(Path(agent_run.__file__).read_text())
        program = """
import runpy, sys
namespace = runpy.run_path(sys.argv[1])
namespace['main'].__globals__['stop'] = lambda: print('cleanup dispatched')
namespace['main'].__globals__['watchdog'] = lambda run_id: print('cleanup dispatched')
sys.argv = sys.argv[1:]
raise SystemExit(namespace['main']())
"""
        for command in (["stop"], ["_watchdog", "owned-run"], ["start"]):
            with self.subTest(command=command):
                result = subprocess.run([sys.executable, "-I", "-c", program, str(script), *command],
                                        cwd=self.root, capture_output=True, text=True, timeout=10)
                if command == ["start"]:
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertEqual(json.loads(result.stdout)["status"], "installation_required")
                else:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("cleanup dispatched", result.stdout)

    def test_passing_check_allows_selection(self):
        self.install()
        result = {"searchComplete": True, "selected": [], "skipped": [], "warnings": []}
        with contextlib.redirect_stdout(io.StringIO()), \
                patch.object(history, "GitHub"), patch.object(history, "select", return_value=result) as select:
            self.assertEqual(history.main(["select"]), history.NOTHING_NEW)
        select.assert_called_once()
        self.assertTrue((self.root / "state/selection.json").is_file())


if __name__ == "__main__":
    unittest.main()
