"""Published update notices never replace or weaken local compatibility checks."""
import copy
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock

from skill_versions import check_updates, parse_version


def result():
    return {"ok": True, "skill": {"name": "review-all", "installed_version": "1.0.0", "url": "https://github.com/alchambron/skills/tree/main/git-and-review-workflow/review-all"},
            "dependencies": [{"name": "manual-test-guide", "installed_version": "1.9.0", "url": "https://github.com/alchambron/skills/tree/main/testing-and-quality/manual-test-guide"}]}


def runner(guide="1.10.0", review="1.1.0"):
    data = {"schema_version": 1, "skills": {"review-all": {"version": review}, "manual-test-guide": {"version": guide}}}
    return Mock(return_value=Mock(returncode=0, stdout=json.dumps(data)))


class VersionTests(unittest.TestCase):
    def test_stable_version_ordering_is_numeric(self):
        self.assertGreater(parse_version("1.10.0"), parse_version("1.9.9"))
        for value in (None, 1, "1.0", "01.0.0", "1.0.0-beta", "v1.0.0", "1.0.0\n"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_version(value)

    def test_new_published_versions_show_installed_latest_and_links(self):
        data = result()
        before = copy.deepcopy(data)
        run = runner()
        check_updates(data, run)
        self.assertTrue(data["ok"])
        self.assertEqual(data["dependencies"], before["dependencies"])
        self.assertEqual(len(data["update_check"]["updates"]), 2)
        self.assertIn("installed 1.9.0; latest 1.10.0", data["update_message"])
        self.assertIn(data["skill"]["url"], data["update_message"])
        args, kwargs = run.call_args
        self.assertIn("repos/alchambron/skills/contents/skill-versions.json?ref=main", args[0])
        self.assertEqual(kwargs["timeout"], 10)

    def test_same_or_older_published_versions_do_not_request_downgrade(self):
        for run in (runner(guide="1.9.0", review="1.0.0"), runner(guide="1.8.0", review="0.9.0")):
            data = check_updates(result(), run)
            self.assertEqual(data["update_check"], {"status": "checked", "updates": []})
            self.assertNotIn("update_message", data)

    def test_unavailable_or_invalid_catalog_keeps_latest_unknown(self):
        failures = [Mock(side_effect=OSError()), Mock(side_effect=subprocess.TimeoutExpired("gh", 10)),
                    Mock(return_value=Mock(returncode=1, stdout="private error")),
                    Mock(return_value=Mock(returncode=0, stdout='{"schema_version":1,"skills":{}}')),
                    runner(guide="bogus"), Mock(return_value=Mock(returncode=0, stdout="bad JSON"))]
        for run in failures:
            with self.subTest(run=run):
                data = check_updates(result(), run)
                self.assertEqual(data["update_check"]["status"], "unavailable")
                self.assertTrue(data["ok"])
                self.assertIn("Latest versions are unknown", data["update_message"])
                self.assertNotIn("private error", data["update_message"])

    def test_update_notice_never_clears_local_failure(self):
        data = result()
        data["ok"] = False
        self.assertFalse(check_updates(data, runner())["ok"])

    def test_repository_catalog_matches_packaged_versions(self):
        root = Path(__file__).resolve().parents[3]
        catalog = root / "skill-versions.json"
        if not catalog.is_file():
            self.skipTest("Published catalog check applies in the source repository")
        data = json.loads(catalog.read_text())
        self.assertEqual(data["schema_version"], 1)
        for name, entry in data["skills"].items():
            with self.subTest(skill=name):
                self.assertEqual((root / entry["path"] / "VERSION").read_text().strip(), entry["version"])
                parse_version(entry["version"])


if __name__ == "__main__":
    unittest.main()
