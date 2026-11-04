#!/usr/bin/env python3
"""Offline tests for the scripted scenario runner; the browser tests use a loopback page only."""

import contextlib
import functools
import http.server
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import scenario_run

PAGE = """<!doctype html><title>Fixture</title>
<label>Search <input name="q"></label><button onclick="go()">Search</button><ul id="rows"></ul>
<script>
console.error("fixture console error");
function go() {
  const q = document.querySelector('[name=q]').value;
  setTimeout(() => { document.querySelector('#rows').innerHTML = q ? `<li>${q}-row</li>` : ""; }, 300);
}
</script>"""

PASSING = """export default async function (t) {
  await t.step("open", () => t.page.goto("/"));
  await t.step("search", async () => {
    await t.page.getByRole("textbox", { name: "Search" }).fill(t.params.term);
    await t.page.getByRole("button", { name: "Search" }).click();
  });
  await t.waitFor("one matching row", async () => (await t.page.locator("#rows li").count()) === 1);
  t.check("row text", (await t.page.locator("#rows li").textContent()) === `${t.params.term}-row`);
  t.note("rows", await t.page.locator("#rows li").allTextContents());
  await t.shot("result");
}
"""

FAILING = """export default async function (t) {
  await t.page.goto("/");
  t.check("heading present", await t.page.locator("h1").count() > 0, "no h1 on page");
}
"""

STOPPING = """export default async function (t) {
  await t.page.goto("/");
  await t.step("click missing control", () => t.page.getByRole("button", { name: "Export" }).click({ timeout: 500 }));
  t.check("never reached", true);
}
"""


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def browsers_installed():
    return scenario_run.installed(scenario_run.tool_dir())


class UnitTests(unittest.TestCase):
    def test_params_parse_and_reject_missing_value(self):
        self.assertEqual(scenario_run.parse_params(["term=abc", "url=a=b"]), {"term": "abc", "url": "a=b"})
        with self.assertRaises(ValueError):
            scenario_run.parse_params(["term"])

    def test_summary_counts_checks_and_names_failures(self):
        result = {"status": "fail", "checks": [{"name": "a", "ok": True}, {"name": "b", "ok": False}],
                  "steps": [{"name": "open", "ok": True}], "screenshots": ["x.png"], "consoleErrors": ["e"],
                  "failedRequests": [], "failureEvidence": [{"aria": "f.aria.yml"}], "durationMs": 5}
        summary = scenario_run.summarize(result, Path("/r/result.json"))
        self.assertEqual((summary["checks"], summary["failedChecks"], summary["consoleErrors"]), ("1/2", ["b"], 1))
        self.assertEqual(summary["failureEvidence"], ["f.aria.yml"])

    def test_unreachable_runtime_stops_before_launching_a_browser(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "s.mjs").write_text(PASSING)
            (root / "runtime.json").write_text(json.dumps({"frontendUrl": "http://127.0.0.1:9"}))
            with patch.object(scenario_run, "setup") as setup, contextlib.redirect_stdout(io.StringIO()) as out:
                code = scenario_run.main(["run", "--script", str(root / "s.mjs"), "--out", str(root / "out"),
                                          "--runtime", str(root / "runtime.json")])
            self.assertEqual(code, 2)
            self.assertIn("unreachable", json.loads(out.getvalue())["error"])
            setup.assert_not_called()


@unittest.skipUnless(browsers_installed(), "run `scenario_run.py setup` to enable browser tests")
class BrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        site = cls.root / "site"
        site.mkdir()
        (site / "index.html").write_text(PAGE)
        handler = functools.partial(Quiet, directory=str(site))
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.temporary.cleanup()

    def run_script(self, name, body, *extra):
        script = self.root / f"{name}.mjs"
        script.write_text(body)
        runtime = self.root / f"{name}-runtime.json"
        runtime.write_text(json.dumps({"frontendUrl": self.url, "params": {"term": "default"}}))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = scenario_run.main(["run", "--script", str(script), "--out", str(self.root / name),
                                      "--runtime", str(runtime), "--timeout", "60", *extra])
        return code, json.loads(out.getvalue())

    def test_passing_scenario_records_checks_screenshot_and_console(self):
        code, summary = self.run_script("pass", PASSING, "--param", "term=FR_MOD")
        self.assertEqual((code, summary["status"], summary["checks"]), (0, "pass", "2/2"), summary)
        result = json.loads(Path(summary["result"]).read_text())
        self.assertEqual(result["observations"]["rows"], ["FR_MOD-row"])
        self.assertTrue(Path(summary["screenshots"][0]).stat().st_size > 1000)
        self.assertIn("fixture console error", result["consoleErrors"][0])
        self.assertIsNone(result["trace"])

    def test_failed_check_keeps_running_and_saves_trace(self):
        code, summary = self.run_script("fail", FAILING)
        self.assertEqual((code, summary["status"], summary["failedChecks"]), (1, "fail", ["heading present"]))
        self.assertTrue(Path(summary["trace"]).is_file())

    def test_stopped_step_saves_screenshot_and_aria_tree(self):
        code, summary = self.run_script("stop", STOPPING)
        self.assertEqual((code, summary["status"], summary["failedStep"]), (2, "error", "click missing control"))
        aria = Path(summary["failureEvidence"][0]).read_text()
        self.assertIn('button "Search"', aria)

    def test_saved_session_is_private_and_reused_by_the_next_scenario(self):
        login = """export default async function (t) {
  await t.page.goto("/");
  await t.page.evaluate(() => localStorage.setItem("authToken", "fixture-token"));
  t.check("logged in", true);
}
"""
        session = self.root / "session.json"
        runtime = self.root / "login-runtime.json"
        runtime.write_text(json.dumps({"frontendUrl": self.url, "storageState": str(session)}))
        script = self.root / "login.mjs"
        script.write_text(login)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = scenario_run.main(["run", "--script", str(script), "--out", str(self.root / "login"),
                                      "--runtime", str(runtime), "--save-session", str(session)])
        summary = json.loads(out.getvalue())
        self.assertEqual((code, summary["savedSession"]), (0, str(session.resolve())))
        self.assertEqual(session.stat().st_mode & 0o777, 0o600)
        runtime = self.root / "shared-runtime.json"
        runtime.write_text(json.dumps({"frontendUrl": self.url, "storageState": str(session)}))
        reuse = """export default async function (t) {
  await t.page.goto("/");
  t.check("session restored", await t.page.evaluate(() => localStorage.getItem("authToken")) === "fixture-token");
}
"""
        script = self.root / "reuse.mjs"
        script.write_text(reuse)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = scenario_run.main(["run", "--script", str(script), "--out", str(self.root / "reuse"),
                                      "--runtime", str(runtime)])
        self.assertEqual(code, 0, out.getvalue())

    def test_hung_script_times_out_with_a_result(self):
        body = 'export default async function (t) { await t.page.goto("/"); await t.page.waitForTimeout(60000); }'
        code, summary = self.run_script("hang", body, "--timeout", "2")
        self.assertEqual(code, 2)
        self.assertIn("timed out after 2s", summary["error"])

    def test_script_without_checks_is_an_error(self):
        code, summary = self.run_script("empty", 'export default async function (t) { await t.page.goto("/"); }')
        self.assertEqual(code, 2)
        self.assertIn("no checks", summary["error"])


if __name__ == "__main__":
    unittest.main()
